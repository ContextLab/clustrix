.. _limitations:

Limitations and Edge Cases
==========================

What Clustrix cannot do, what it does differently from what you might expect,
and what to do instead. Everything on this page was checked against the code
and, where an error message is quoted, produced by running it.

.. contents:: On this page
   :local:
   :depth: 2


.. _limitation-loops:

Loop parallelisation
--------------------

:doc:`parallel_loops` is the full account. In short:

* Only a ``for`` loop, or a statement that is a single-``for`` comprehension,
  at the top level of the decorated function is considered. Not a ``while``
  loop, not a loop in a helper function, not a loop under ``if``, ``with`` or
  ``try``. At most one loop per function is divided.
* A loop is not divided when an iteration uses a value from the previous
  one, contains ``break`` or ``return``, assigns a name that is used after
  the loop, writes a global, or draws from the unseeded global ``random`` or
  NumPy generator. :func:`clustrix.explain` names the line and the reason.
* Run-time checks catch a shared value being modified and the global random
  state moving, and fall back to the sequential loop.
* **No check sees state outside the values the loop was handed**: a
  module-level cache, a file, a database, a GPU's random state. Side effects
  of that kind happen once per iteration, in an unspecified order, and
  locally in a worker process rather than in the caller.
* There are no SLURM job arrays. Each piece of a divided loop is a separate
  ``sbatch`` submission, at most ``max_parallel_jobs`` at once.
* No GPU path has been verified on real hardware in this release, and
  ``auto_gpu_parallel`` does nothing.
* Locally, a function with no loop that can be divided runs once in the
  calling process whatever ``cores`` says, and a warning says so.

.. _limitation-input-files:

Input files
-----------

:doc:`input_files` is the full account. In short:

* Automatic staging applies to ``ssh`` and ``slurm`` only, and not to
  ``async_submit=True``.
* A path computed at run time is not found. Pass it as an argument or list
  it in ``@cluster(data=[...])``.
* A path that appears in code is staged only if it is inside the project.
  Credential files are refused. A call naming more than
  ``auto_stage_max_bytes`` stops.
* **Files a job writes are not brought back.** Only the return value is.
* Staged copies are never deleted automatically.
* A :doc:`data package <data_packages>` too large to travel inline cannot be
  read by an ``ssh`` or ``slurm`` worker.

.. _limitation-sourceless:

Functions whose source cannot be read
-------------------------------------

A function defined in the REPL, in a notebook cell, or by ``exec()``
serializes and runs correctly. Dill and cloudpickle serialize by value, so
the worker never needs the source text.

What needs the source is loop division. When the source is not available the
function runs as one unit, and :func:`clustrix.explain` says so:

.. code-block:: python

   import clustrix
   from clustrix.utils import deserialize_function, serialize_function

   namespace = {}
   exec(
       "def sourceless(n):\n"
       "    out = []\n"
       "    for i in range(n):\n"
       "        out.append(i)\n"
       "    return out\n",
       namespace,
   )
   sourceless = namespace["sourceless"]

   print(clustrix.explain(sourceless))

   data = serialize_function(sourceless, (3,), {})
   func, args, kwargs = deserialize_function(data)
   print("still runs:", func(*args, **kwargs))

.. code-block:: text

   sourceless:
     not analysed: its source is not available (could not get source code)
   still runs: [0, 1, 2]

A function defined in a Jupyter cell or an IPython prompt does have source
available, and its loop is divided.


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
  than ``%USERPROFILE%\.clustrix``. Note that a config directory named by that
  variable is not trusted to choose which host receives a *stored* credential,
  and neither is a ``profiles.yml`` found under it -- see :ref:`the search
  order <configuration>`. When you move it, authorise the host by setting
  ``SSH_HOST`` in the credential file; handing the same hostname back through
  ``configure`` or ``load_config`` does not lift the refusal.
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
* ``pre_execution_commands`` **is not validated or quoted.** It is a raw shell
  injection point by design. ``module_loads`` and ``environment_variables``
  keys *are* validated and will refuse metacharacters.
* ``cores`` **must be a positive integer, or -1.** ``@cluster(cores=0)``,
  ``@cluster(cores=-2)`` and ``@cluster(cores=True)`` raise ``ValueError``
  when the decorator is applied. ``-1`` means every CPU available to the
  calling process and is refused for a SLURM job.
* **Unknown** ``@cluster`` **keywords are warned about, not rejected.** The
  warning goes to the ``clustrix.decorator`` logger on every call, so a typo in
  a keyword name is easy to miss if nothing is watching that logger. This is
  how ``@cluster(cluster_type="local")`` fails: ``cluster_type`` is a
  *configuration* setting, not a decorator keyword, so the decorator warns and
  ignores it. Use ``configure(cluster_type="local")``.
* **Some recognised** ``@cluster`` **keywords are still ignored by their
  backend.**
  ``hf_namespace``, ``hf_token``, ``hf_username`` and ``key_file`` are
  accepted, but no backend reads them from the decorator. This produces no
  warning, because the keywords are on the recognised list. Set them through
  ``clustrix.configure()``.
* **The first job on a new host is slow.** It builds a Python environment
  there. In a run against a local test host the first job took 27 seconds and
  the next one 3.
* **Printed output of a remote job is not shown.** It is in the job's log
  files on the cluster.


See also
--------

* :doc:`execution_model` -- the mechanism behind each of these limits.
* :doc:`configuration` -- including the list of settings that have no effect.
