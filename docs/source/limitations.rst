.. _limitations:

Limitations and Edge Cases
==========================

What Clustrix cannot do, what it does differently from what you might expect,
and what to do instead. Everything on this page was checked against the code
and, where an error message is quoted, produced by running it.

.. contents:: On this page
   :local:
   :depth: 2


.. _limitation-sourceless:

Functions whose source cannot be read
-------------------------------------

**What is true:** a function defined in the REPL, in a notebook cell, or by
``exec()`` **serializes and runs correctly**. Dill and cloudpickle serialize by
value -- they embed the code object -- so the worker never needs the source
text.

The function you wrote is the function that gets serialized. Nothing is
substituted for it, ever, and no code path rewrites it.

**What is actually lost** is everything that reads source text:

* loop parallelization (``detect_loops``, ``find_parallelizable_loops`` -- both
  start with ``inspect.getsource``),
* the source-text fallback the worker would use if binary deserialization
  failed.

.. code-block:: python

   from clustrix.utils import serialize_function, deserialize_function
   from clustrix.loop_analysis import find_parallelizable_loops

   namespace = {}
   exec(
       "def sourceless(n):\n"
       "    out = 0\n"
       "    for i in range(n):\n"
       "        out = i\n"
       "    return out\n",
       namespace,
   )
   sourceless = namespace["sourceless"]

   data = serialize_function(sourceless, (10,), {})
   print("function_source:", data["function_source"])
   print("parallelizable loops:", find_parallelizable_loops(sourceless, (10,), {}))

   func, args, kwargs = deserialize_function(data)
   print("still runs:", func(*args, **kwargs))

Output:

.. code-block:: text

   function_source: None
   parallelizable loops: []
   still runs: 9

(The round trip above happens in one process for brevity; the same payload was
also loaded in a *fresh* interpreter, where it returned ``9`` as well.)

**Workaround:** none needed for correctness. If you want loop parallelization,
move the function into a ``.py`` file.


Editable installs, git checkouts and local source trees
-------------------------------------------------------

An editable install, a ``pip install git+https://...``, or a bare ``.egg-info``
in a source tree cannot be recreated on the cluster. Their *version* exists,
but ``pip install name==version`` would install an unrelated package of the
same name off an index, or nothing at all.

Such distributions are excluded from the mirrored requirement set. If your
function actually reaches into one of them -- and clustrix knows, because it
walks the object graph and maps import names back to distributions -- the
submission is **refused at submit time**, naming the package:

.. code-block:: text

   RuntimeError: This function uses package(s) that cannot be installed on the
   cluster: mylib (installed in editable mode from file:///home/me/src/mylib).
   clustrix mirrors your environment with `pip install name==version`, which for
   these would install something other than what you are running. Publish the
   package, vendor the code into your project directory so clustrix can send it
   by value, or list it in `excluded_packages` if the remote job genuinely does
   not need it.

You can see the list before submitting:

.. code-block:: python

   from clustrix.utils import get_unreproducible_requirements

   offenders = get_unreproducible_requirements()
   print("unreproducible distributions found:", isinstance(offenders, dict))
   for name, reason in sorted(offenders.items()):
       print(" -", name, "--", reason)

**Workarounds**, in order of preference:

1. **Publish the package** (PyPI or a private index the cluster can reach) so
   ``name==version`` resolves.
2. **Vendor it into your project directory.** A module that is not under
   site-packages is classified project-local by ``_is_local_module`` and is
   shipped *by value* inside the payload -- no install required. This is why
   your own project modules already work.
3. **Add it to** ``cluster_packages`` with a spec the cluster can install
   (e.g. ``"mylib @ git+https://github.com/me/mylib@v1.2"``), which is passed
   to ``pip install`` verbatim.
4. **Add it to** ``excluded_packages`` if the remote job genuinely does not
   need it.

A caveat worth knowing: whether you hit the refusal depends on *where the code
lives*, not on what the metadata says. An editable install whose source tree is
outside site-packages is classified project-local and gets shipped by value
instead -- which usually works. The refusal fires for the cases where the code
really is in site-packages but the metadata is unreproducible, notably VCS
installs.


.. _limitation-local-cores:

Local cores require splittable work
-----------------------------------

With no cluster configured, ``@cluster(cores=8)`` runs an ordinary function in
the process that called it, on one core, and logs that the request has no
effect. One call is one unit of work, so there is nothing to give seven other
workers. Nothing forks, and the call returns when the function returns.

.. code-block:: python

   import os
   import clustrix

   clustrix.configure(cluster_type="local")

   @clustrix.cluster(cores=8)
   def where_did_it_run(n):
       return os.getpid(), sum(i * i for i in range(n))

   pid, _ = where_did_it_run(200_000)
   print("this interpreter:", os.getpid())
   print("the job ran in:  ", pid)
   print("same process?    ", pid == os.getpid())

.. code-block:: text

   this interpreter: 44824
   the job ran in:   44824
   same process?     True

There is exactly one local path where ``cores`` does size a pool: the
parallelizing path (on by default through ``auto_parallel``, and forced with
``parallel=True``) must find a supported loop, split it into chunks, and pass
each chunk through a ``_parallel_<variable>`` keyword the function accepts.
Most Python loops do not meet those rules. Even when they do, the pool size is
an upper bound, not a promise that many workers will be busy. On that path the
work is cut into roughly two chunks per worker, so that a worker which draws a
slow chunk can be relieved by an idle sibling taking the next one; the count
follows the pool you asked for, not the machine's CPU count.

Three details of the "has no effect" message itself:

* It is logged **once per decorated function per distinct request**, not on
  every call, because the local path is exactly where a decorated function
  gets called in a tight loop. Change the request -- a different
  ``default_cores``, a different reason for declining -- and it speaks again.
* A request of one worker is not reported. Every one of these routes already
  provides one.
* ``configure(default_cores=4)`` -- the shipped default -- is not reported
  either, deliberately. Clustrix cannot distinguish an explicit request that
  happens to equal the default from no request at all, and warning on the
  shipped value would fire on every local call anyone ever makes. Any *other*
  ``default_cores`` you set is treated as an instruction and is reported.

The parallel machinery underneath is real.
:class:`clustrix.local_executor.LocalExecutor` builds a
``ProcessPoolExecutor`` or a ``ThreadPoolExecutor`` and gives the speedups you
would expect.
:func:`clustrix.local_executor.choose_executor_type` decides which pool you
get, in two steps. First it calls ``pickle.dumps`` on your function and on
every argument, and any failure selects threads, because a process pool has no
way to send an unpicklable object to a worker. Then it reads
``inspect.getsource`` and scans the text for ``open(``, ``requests.``,
``urllib.``, ``http.``, ``ftp.``, ``sql``, ``database``, ``time.sleep`` and
``threading.``; a hit selects threads on the theory that the work releases the
GIL. Otherwise you get processes. That second step is a substring scan over
source text, so it is fooled by a variable called ``sqlite_path`` and blind to
I/O reached through a helper. Pass ``use_threads=True`` or ``use_threads=False``
to say what you meant.

For general local parallelism, drive
:class:`~clustrix.local_executor.LocalExecutor` yourself, or use ``joblib`` or
``concurrent.futures``. The
:doc:`local-parallelism notebook <notebooks/local_parallel_comparison>`
measures both the gap and what the pools are worth.


Loop detection is much narrower than it looks
---------------------------------------------

``auto_parallel`` defaults to ``True``, which suggests loops are routinely
parallelized. In practice ``find_parallelizable_loops`` rejects most real
loops. Three rules do the rejecting.

**1. The loop target must be a bare name.** ``_analyze_for_loop`` returns
``None`` unless ``node.target`` is an ``ast.Name``. So
``for i, x in enumerate(items)`` and ``for k, v in d.items()`` are not merely
non-parallelizable -- they are not detected as loops at all.

**2. Reading any external name in the body disqualifies the loop.**
``final_dependencies = dep_analyzer.reads - {variable}``, and
``find_parallelizable_loops`` requires ``not loop.dependencies``. Every
``ast.Name`` in ``Load`` context counts, including the receiver of a method
call. So the single most common loop shape in Python --
``results.append(f(x))`` -- is rejected, because ``results`` is read.

**3. An augmented assignment counts as a read.** ``total += i`` adds ``total``
to ``reads`` even though the AST target's context is ``Store``. This one is
correct and deliberate: without it, an accumulator loop looked dependency-free
and was falsely classified as safe to parallelize (issues #106, #131).

Demonstrated:

.. code-block:: python

   from clustrix.loop_analysis import detect_loops_in_function, find_parallelizable_loops

   def tuple_target(items):
       total = 0
       for i, x in enumerate(items):
           total += i * x
       return total

   def appending(items):
       results = []
       for x in items:
           results.append(x * 2)
       return results

   def independent():
       out = 0
       for i in range(1000):
           out = i
       return out

   for fn, argv in [(tuple_target, ([1, 2, 3],)), (appending, ([1, 2, 3],)),
                    (independent, ())]:
       loops = detect_loops_in_function(fn, argv, {})
       print(
           fn.__name__,
           "| detected:",
           [(lp.variable, sorted(lp.dependencies), lp.is_parallelizable) for lp in loops],
           "| parallelizable:",
           [lp.variable for lp in find_parallelizable_loops(fn, argv, {})],
       )

Real output:

.. code-block:: text

   tuple_target | detected: [] | parallelizable: []
   appending | detected: [('x', ['results'], False)] | parallelizable: []
   independent | detected: [('i', [], True)] | parallelizable: ['i']

**Workarounds:**

* Do not rely on automatic parallelization. Split the work yourself and submit
  one ``@cluster`` call per chunk; that is explicit, backend-independent, and
  produces a shape you chose.
* Rewrite ``for i, x in enumerate(items)`` as ``for i in range(len(items))``
  if you want the loop to be *seen* at all.
* Set ``auto_parallel=False`` to remove the guesswork entirely.


Auto-parallelization needs chunk parameters, and they differ local vs remote
----------------------------------------------------------------------------

The two paths use **different keyword names**, which is easy to trip over:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Path
     - Keywords your function must accept
   * - Local (``_create_local_work_chunks``)
     - ``_parallel_<loop variable>``
   * - Remote (``_create_work_chunks``)
     - ``_chunk_range_<loop variable>`` **and** ``_chunk_index``

Either path declines, and logs at ``INFO``, when the function cannot accept
its chunk. Neither injects the keyword regardless; a function that declares
neither the parameter nor ``**kwargs`` simply runs whole.

Both paths also require the loop's range to be a **literal** ``range(<int>)``.
A range whose bound is only known at run time -- ``range(n)``,
``range(len(data))`` -- is declined, because there is no way to split a bound
the analysis cannot read.

When ``_create_local_work_chunks`` splits a loop, it hands each chunk to your
function as a keyword argument named ``_parallel_<loop variable>``. A function
that cannot receive it is run sequentially, and clustrix says so:

.. code-block:: text

   INFO clustrix.decorator: Not parallelizing no_chunk_param locally:
   it takes no '_parallel_i' parameter.

That is an ``INFO`` on the ``clustrix.decorator`` logger, so you will not see
it unless logging is configured at that level.


Parallel and sequential runs can return different shapes
--------------------------------------------------------

This is the trap most likely to produce a wrong answer rather than an error.

``_combine_local_results`` does this:

* no results -> ``None``
* exactly one chunk -> that chunk's result, unwrapped
* all chunks returned lists -> the lists concatenated
* otherwise -> **the list of per-chunk results**

So a function that returns a scalar returns a *list of scalars* when it is
parallelized, and the length of that list is the number of chunks the work was
cut into: two per worker, and the worker count is the ``cores`` you asked for.

The "exactly one chunk" line is the helper's contract rather than something
you can provoke today: work is only split when the loop runs at least three
times, and ``chunk_size = max(1, len(loop_range) // (workers * 2))`` cuts any
such loop into at least two pieces. Nothing on the decorator's path currently
reaches that branch.

Two consequences catch people out, and neither is a difference between a
parallel run and a sequential one -- they are differences *between parallel
runs*.

**Changing ``cores`` alone changes the answer.** The chunk count follows the
pool size, so the same call cut a different number of ways returns a different
list:

.. code-block:: python

   import clustrix

   clustrix.configure(cluster_type="local", cluster_host=None)

   def partial_sum(n, _parallel_i=None):
       indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
       marker = 0
       for i in range(n):
           marker = i * i
       del marker
       return sum(indices)

.. code-block:: text

   partial_sum(8)                                     -> 28
   @cluster(parallel=True, cores=1) partial_sum(8)    -> [6, 22]
   @cluster(parallel=True, cores=2) partial_sum(8)    -> [1, 5, 9, 13]
   @cluster(parallel=True, cores=4) partial_sum(8)    -> [0, 1, 2, 3, 4, 5, 6, 7]

Three pool sizes, three answers, none of them 28. For a callee that returns a
**list** the concatenation makes the parallel answer match the sequential one,
so this only bites scalar-returning callees -- but there it bites hard, because
nothing raises.

**A short loop changes the return type.** A loop of fewer than three
iterations is not considered worth splitting, so the same decorated function
returns the scalar its body returns:

.. code-block:: text

   @cluster(parallel=True, cores=2) partial_sum(2)    -> 1     (an int)
   @cluster(parallel=True, cores=2) partial_sum(8)    -> [1, 5, 9, 13]

A caller who tested with a short input and shipped with a long one gets a list
where they tested an int. Whether any of this is the right behaviour is an open
design question -- see `issue #170
<https://github.com/ContextLab/clustrix/issues/170>`_ -- but it is the current
behaviour, and it is pinned by
``tests/unit/test_local_cores.py::test_the_answers_shape_depends_on_cores_and_on_how_long_the_loop_is``.

.. code-block:: python

   # shape_demo.py
   import clustrix

   clustrix.configure(cluster_type="local", auto_parallel=True)

   def body(_parallel_i=None):
       total = 0
       for i in range(1000):
           total = i
       return total

   @clustrix.cluster(cores=4)
   def counted(_parallel_i=None):
       total = 0
       for i in range(1000):
           total = i
       return total

Called from another module, so that ``inspect.getsource`` can see it:

.. code-block:: python

   import shape_demo

   parallel = shape_demo.counted()
   sequential = shape_demo.body()
   print("parallel   ->", type(parallel).__name__, repr(parallel)[:60])
   print("sequential ->", type(sequential).__name__, repr(sequential))
   assert isinstance(sequential, int)

With ``cores=4`` that prints a list of eight -- two chunks per worker -- and
the scalar:

.. code-block:: text

   parallel   -> list [999, 999, 999, 999, 999, 999, 999, 999]
   sequential -> int 999

Note also that the function above *accepts* ``_parallel_i`` and then ignores
it, which is why every chunk computed the same thing. Accepting the parameter
is what makes clustrix willing to parallelize; **using** it is your
responsibility.

The remote path has the same shape problem: ``_combine_results`` in
``decorator.py`` sorts by chunk index and returns
``[result[1] for result in results]`` unconditionally.

**Workaround:** either set ``parallel=False`` on the decorator (or
``auto_parallel=False`` globally) so a function always returns what its body
returns, or write the function to take ``_parallel_<var>`` and return a list,
so both shapes agree.


Python version skew is refused, not worked around
-------------------------------------------------

Dill and cloudpickle embed CPython bytecode, which does not load across minor
versions. When conda is unavailable and clustrix has to use a system
interpreter, ``_select_remote_python`` requires an exact ``major.minor`` match:

.. code-block:: python

   from clustrix.utils import _select_remote_python

   try:
       _select_remote_python([("python3.9", "3.9")], "3.12")
   except RuntimeError as exc:
       print(exc)

.. code-block:: text

   The remote system has Python 3.9, but this session runs Python 3.12.
   Serialized functions carry CPython bytecode, which cannot be loaded by a
   different minor version, so the cluster needs a Python 3.12 interpreter -- or
   conda, which clustrix will use to create one.

With no Python 3 at all:

.. code-block:: text

   No Python 3 interpreter found on the remote system. Consider installing conda.

**Workarounds:** install conda on the cluster (clustrix will create a matching
environment itself), install a matching interpreter, or match the cluster's
version locally. The same constraint applies to the HuggingFace backend, whose
image defaults to ``python:<your minor version>-slim`` for exactly this reason;
if you override ``hf_image``, keep the minor version identical.


.. _limitation-unsendable:

Things that genuinely cannot be sent
------------------------------------

Sockets, live database connections, open file handles and locks cannot cross
to a worker in any useful sense. What actually happens depends on which
serialization branch you land in, and the difference matters.

**If your payload reaches a project-local module**, cloudpickle is used with no
fallback, and you get a clear refusal that names the object:

.. code-block:: text

   RuntimeError: Cannot serialize this job: something it reaches cannot be
   pickled (cannot pickle 'socket' object). Locks, open files, sockets and
   database handles cannot cross to a worker. Create it where it is used instead
   of capturing it, or install the package on the cluster so the worker imports
   it rather than receiving a copy.

**If it does not**, ``_dumps_by_value`` falls back through
``dill(recurse=True)`` -> ``dill`` -> ``cloudpickle`` -> ``pickle``. The
``recurse=True`` attempt fails on the socket; the plain ``dill.dumps`` attempt
succeeds *by not capturing the offending global at all*. Submission looks fine
and the failure surfaces on the worker:

.. code-block:: text

   NameError: name 's' is not defined

That was produced by serializing a module-level function that closes over
``s = socket.socket()``, writing the payload to disk, and loading it in a
separate interpreter. It is a real gap: the local submission gives no warning.

A related surprise: some objects you might expect to be rejected are pickled
happily. ``dill.dumps(threading.Lock())`` succeeds in 47 bytes and round-trips
-- but what arrives is a *different, unlocked* lock in a different process, so
any coordination you were relying on is silently gone.

**Workarounds:**

* Create the resource inside the function, not outside it. Open the file, dial
  the socket, connect to the database in the body, so it exists on the worker
  and nothing needs to travel.
* Pass a *description* (a path, a DSN, a URL) rather than a live handle.
* For files, pass paths and use the filesystem utilities
  (``cluster_ls``, ``cluster_glob``, ``cluster_stat``) which work locally and
  remotely from the same code.

Payload size limits
~~~~~~~~~~~~~~~~~~~

The object graph walk is capped at 2,000,000 nodes; beyond that
``WalkTooLargeError`` is raised and the job is refused rather than shipped with
an unknown payload. On the HuggingFace backend, an encoded payload over 256 KB
is automatically staged through a private dataset repo instead of an
environment variable -- this is handled for you, but it does mean the payload
briefly exists in a Hub repo under your namespace.


Results can only be collected by the process that submitted them
-----------------------------------------------------------------

The per-job HMAC key lives in ``SchedulerManager.active_jobs``, in memory. If
your interpreter exits while a job is queued, the job still runs, but its
result can no longer be authenticated:

.. code-block:: text

   PayloadAuthenticationError: No result-signing key is recorded for Job
   slurm_1234, so what it produced cannot be authenticated. Refusing to
   deserialize it: loading a pickle executes code. Re-run the job from this
   process, which records a key at submission.

This is a refusal, not a warning, and there is no override. "No key recorded"
and "forged" are indistinguishable from the caller's side.

**Workaround:** keep the submitting process alive (use ``async_submit=True``
if you need it to do other things meanwhile), or have the remote function
write its own output to a durable location -- a file on the cluster, a
database -- and return only a path or a summary.


.. _removed-backends:

Backends Clustrix does not support
----------------------------------

Seven schedulers and cloud providers you might expect to find are absent. If
you came here looking for one of them, this is the list, and each row links to
the issue tracking its arrival.

The gate for admitting any of them is the same gate the four supported backends
have already passed: a real job, on real hardware, whose result comes back and
is checked in as evidence under ``docs/evidence/``. Clustrix will not publish a
backend on the strength of code that compiles. A backend that has never
completed a job is not a feature with rough edges; it is an untested code path
with a plausible-looking API in front of it, and the failure mode is that you
write against it, it appears to submit, and you learn much later that no result
was ever produced.

Setting ``cluster_type`` to any of these names raises a ``ValueError`` that
names the backend and its issue, so you find out at configuration time rather
than three stages into a submission.

=================  =============  ====================================================
Backend            Issue          What the name would select
=================  =============  ====================================================
PBS                `#140`_        ``cluster_type="pbs"`` -- the PBS/Torque scheduler.
SGE                `#141`_        ``cluster_type="sge"`` -- Sun/Son of Grid Engine.
Kubernetes         `#142`_        ``cluster_type="kubernetes"``, the ``k8s_*``
                                  settings, and cluster auto-provisioning.
AWS                `#143`_        ``provider="aws"`` -- EC2 and EKS.
GCP                `#144`_        ``provider="gcp"`` -- Google Compute Engine.
Azure              `#145`_        ``provider="azure"`` -- Azure VMs.
Lambda Cloud       `#146`_        ``provider="lambda"`` -- Lambda Labs GPU cloud.
=================  =============  ====================================================

.. _#140: https://github.com/ContextLab/clustrix/issues/140
.. _#141: https://github.com/ContextLab/clustrix/issues/141
.. _#142: https://github.com/ContextLab/clustrix/issues/142
.. _#143: https://github.com/ContextLab/clustrix/issues/143
.. _#144: https://github.com/ContextLab/clustrix/issues/144
.. _#145: https://github.com/ContextLab/clustrix/issues/145
.. _#146: https://github.com/ContextLab/clustrix/issues/146

Two adjacent things are absent for the same reason:

* **A HuggingFace Spaces provider.** Take care with the name: HuggingFace
  *Jobs* is ``cluster_type="huggingface"``, and that one is supported and
  verified. Spaces is a different product and Clustrix has no backend for it.
* **A cost monitoring and cloud pricing API** -- no
  ``cost_tracking_decorator``, ``get_cost_monitor``, ``start_cost_monitoring``,
  ``generate_cost_report`` or ``get_pricing_info``. Those priced the cloud VM
  backends, which are not here to be priced.

What to do instead
~~~~~~~~~~~~~~~~~~

* **PBS or SGE**: no direct substitute in Clustrix today. Follow `#140`_ /
  `#141`_. If your site also runs SLURM, ``cluster_type="slurm"`` is verified.
* **Kubernetes**: no substitute. Follow `#142`_.
* **A cloud GPU**: ``cluster_type="huggingface"`` submits to HuggingFace Jobs,
  which runs your function in a container on rented GPUs and is verified end to
  end. Otherwise, bring up a VM yourself and use ``cluster_type="ssh"``, which
  is also verified.
* **Cost estimates**: use your provider's own pricing calculator. Clustrix
  does not ship one.


Windows clients: config and credential files are not permission-restricted
--------------------------------------------------------------------------

Clustrix runs on Windows as a *client*: it submits to a Linux cluster, and the
job scripts it generates are quoted for the cluster's POSIX shell regardless of
what your own machine runs. One security property does not carry across,
however, and Windows users should know about it.

On Linux and macOS, files that can hold credentials are created with mode
``0600`` -- readable and writable by the owner only:

* the config file written by :meth:`ClusterConfig.save_to_file` and
  ``clustrix.config.save_config`` (with ``include_secrets=True``, or with a
  secret in ``environment_variables``, this file contains plaintext
  credentials);
* ``~/.clustrix/.env``, the credential template and store;
* the SSH keys and ``~/.ssh/config`` entries clustrix generates.

**On Windows none of these files are restricted.** POSIX permission bits are a
POSIX concept: NTFS controls access with ACLs instead, Python's ``os.chmod``
there only toggles the read-only attribute, and ``os.fchmod`` does not exist at
all before Python 3.13. Clustrix does not depend on ``pywin32``, so it has no
way to set an ACL. The files are therefore created with whatever permissions
they inherit from their parent directory -- typically readable by every account
on the machine, and by anything that can reach the directory over a network
share.

What to do about it on Windows:

* Prefer keeping secrets out of files entirely: set them in environment
  variables, or let clustrix prompt for them, rather than saving them with
  ``include_secrets=True``.
* If you must save them, put the config directory somewhere already restricted
  and restrict it explicitly, e.g.::

      icacls "%USERPROFILE%\.clustrix" /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"

  Set ``CLUSTRIX_CONFIG_DIR`` if you want that directory to be somewhere other
  than ``%USERPROFILE%\.clustrix``.
* Treat a saved clustrix config on Windows as you would any other unprotected
  file: do not put it on a shared drive, and do not commit it.

The corresponding tests assert the ``0600`` mode only on POSIX, because there
is no mode on Windows for them to assert -- the property genuinely does not
exist there, rather than merely being untested.


Smaller sharp edges
-------------------

* **The single-venv fallback does not install your packages.** If
  ``use_two_venv=False``, or the two-venv setup raises or exceeds
  ``venv_setup_timeout``, the job gets one venv containing only ``dill`` and
  ``cloudpickle``. The warning ``Two-venv setup failed, falling back to basic
  setup:`` should be treated as an error.
* **Local jobs cannot be cancelled.** ``cluster_type="local"`` runs the
  function *during* ``submit_job``, so by the time you could cancel it, it has
  finished and its side effects have happened. ``cancel_job`` raises rather
  than pretending otherwise.
* **A conda environment name proves nothing.** Reuse requires the
  ``.clustrix_ready`` marker, written only after every install succeeded.
* **``pre_execution_commands`` is not validated or quoted.** It is a raw shell
  injection point by design. ``module_loads`` and ``environment_variables``
  keys *are* validated and will refuse metacharacters.
* **``cores`` must be a positive integer.** ``@cluster(cores=0)`` and
  ``@cluster(cores=-2)`` raise ``ValueError`` at decoration time rather than
  falling through the ``cores or config.default_cores`` merge. Booleans are
  refused as well, at the decorator and at
  :class:`~clustrix.local_executor.LocalExecutor`: ``bool`` subclasses
  ``int``, so ``cores=True`` would otherwise pass the type check and be read
  as a request for one worker.
* **Unknown ``@cluster`` keywords are warned about, not rejected.** The
  warning goes to the ``clustrix.decorator`` logger on every call, so a typo in
  a keyword name is easy to miss if nothing is watching that logger. This is
  how ``@cluster(cluster_type="local")`` fails: ``cluster_type`` is a
  *configuration* setting, not a decorator keyword, so the decorator warns and
  ignores it. Use ``configure(cluster_type="local")``.
* **Some recognised ``@cluster`` keywords are still ignored by their backend.**
  ``hf_namespace``, ``hf_token`` and ``hf_username`` are accepted and placed in
  ``job_config``, but ``HFJobsManager`` resolves them from configuration
  instead. This produces no warning, because the keywords *are* on the
  recognised list. Set them through ``clustrix.configure()``.


See also
--------

* :doc:`execution_model` -- the mechanism behind each of these limits.
* :doc:`configuration` -- including the list of settings that have no effect.
