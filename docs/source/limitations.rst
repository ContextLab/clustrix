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

Older versions of this documentation said such functions "cannot be
serialized". That was wrong, and it mattered: the claim was paired with
machinery that substituted a rewritten or hardcoded function whenever
``inspect.getsource`` failed, which at one point returned the literal string
``"Function execution completed"`` as your result. That machinery has been
deleted (issues #89, #90). The function you wrote is the function that gets
serialized. Nothing is substituted for it, ever.

**What is actually lost** is everything that reads source text:

* loop parallelization (``detect_loops``, ``find_parallelizable_loops`` -- both
  start with ``inspect.getsource``),
* GPU-parallel operation detection,
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


Local auto-parallelization needs a ``_parallel_<var>`` parameter
----------------------------------------------------------------

When ``_create_local_work_chunks`` splits a loop, it hands each chunk to your
function as a keyword argument named ``_parallel_<loop variable>``. A function
that neither declares that parameter nor collects ``**kwargs`` cannot receive
it, so clustrix declines to parallelize and runs the function sequentially --
and, since this was previously silent, it now says so:

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
parallelized, and the length of that list depends on ``os.cpu_count()`` on the
machine that ran it.

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

On a 12-core machine that prints:

.. code-block:: text

   parallel   -> list [999, 999, 999, 999, 999, 999, 999, 999, 999, 999, 999, 999,
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


Unverified backends
-------------------

Only ``slurm``, ``ssh`` and ``huggingface`` have been demonstrated running a
real job end to end (``scripts/collect_execution_evidence.py``). ``local``
works and is exercised by the test suite. The rest are implemented but
unverified:

==================  ===========================================================
Backend             Caveat
==================  ===========================================================
``pbs``             Never run against real hardware. It now shares the staging
                    and environment setup the other schedulers use; previously
                    it ran ``python execute_function.py``, a file nothing in
                    clustrix has ever created.
``sge``             Never run against real hardware.
``kubernetes``      Never verified against a real cluster. Additionally it does
                    **not** replicate your environment: the container installs
                    only ``cloudpickle`` and ``dill``, so everything else your
                    function imports must already be in ``k8s_image``. Results
                    come back through the pod log, which means a very large
                    result is at the mercy of log retention.
Cloud VM providers  Every ``provider=`` backend (``aws``, ``gcp``, ``azure``,
                    ``lambda``, and ``provider="huggingface"``, which is the
                    Spaces provider, not HuggingFace Jobs) is unverified end to
                    end. Until recently the path could not have worked at all:
                    the serializer writes the function under a ``"function"``
                    key while the remote bootstrap read ``"func"``. That was
                    fixed (issue #119), but nothing has since demonstrated a
                    completed cloud job.
==================  ===========================================================

Use ``cluster_type="huggingface"`` (HuggingFace Jobs), not
``provider="huggingface"`` (Spaces).


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
* **``cores=0`` falls back to the default.** The merge is written as
  ``cores or config.default_cores``, so any falsy value takes the default.
* **``@cluster`` mutates global configuration.** Passing ``platform=``,
  ``auto_provision=``, ``cluster_name=``, ``node_count=``, ``node_type=``,
  ``kubernetes_version=`` or ``from_scratch=`` writes the corresponding field
  onto the shared ``ClusterConfig``, where it stays for every later call.
* **Unknown ``@cluster`` keywords are warned about, not rejected**, and only on
  the first call -- so a typo in a keyword name is easy to miss if you are not
  watching the log.
* **Some recognised ``@cluster`` keywords are still ignored by their backend.**
  ``k8s_namespace``, ``k8s_image``, ``k8s_service_account`` and
  ``k8s_pull_policy`` are accepted and placed in ``job_config``, but
  ``KubernetesJobManager`` reads only ``self.config.k8s_*``. Likewise
  ``hf_namespace``, ``hf_token`` and ``hf_username`` are accepted but
  ``HFJobsManager`` resolves them from configuration. These produce no warning,
  because the keywords *are* on the recognised list. Set them through
  ``clustrix.configure()``.


See also
--------

* :doc:`execution_model` -- the mechanism behind each of these limits.
* :doc:`configuration` -- including the list of settings that have no effect.
