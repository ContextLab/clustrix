.. _execution-model:

Execution Model
===============

This page describes what Clustrix actually does, in order, from the moment
Python reads your ``@cluster`` decorator to the moment your result comes back.
Everything here was traced in the source (``clustrix/decorator.py``,
``clustrix/utils.py``, ``clustrix/executor_core.py``,
``clustrix/executor_connections.py``, ``clustrix/executor_schedulers.py``,
``clustrix/local_executor.py``, ``clustrix/hf_jobs.py``,
``clustrix/executor_kubernetes.py``), and every message quoted below is the
real message the code prints.

If you only remember one thing: **the decorator does almost nothing at import
time.** All of the interesting work happens on the call.

.. contents:: On this page
   :local:
   :depth: 2


Decoration time versus call time
--------------------------------

At import time, ``@cluster(...)`` builds a wrapper with ``functools.wraps`` and
attaches the raw (un-defaulted) arguments to it as ``_cluster_config``. It does
**not** read the configuration, does not contact a cluster, does not serialize
anything, and does not inspect your function's source.

.. code-block:: python

   import clustrix

   @clustrix.cluster(cores=8, memory="16GB")
   def analyze(x):
       return x * 2

   print(analyze._cluster_config)

That prints the arguments exactly as you passed them, with ``None`` for
everything you left out:

.. code-block:: text

   {'cores': 8, 'memory': '16GB', 'time': None, 'partition': None,
    'queue': None, 'parallel': None, 'auto_gpu_parallel': None,
    'environment': None, 'async_submit': None}

The consequence is that **configuration order does not matter**. Decorating
before ``clustrix.configure()`` is fine; the wrapper calls ``get_config()`` on
every invocation, so the configuration in force is the one present when you
*call* the function, not when you defined it.

Unrecognised keyword arguments are accepted by the decorator (they are stored
in ``_cluster_config``) but warned about on the first call, because a silently
ignored option is worse than a rejected one:

.. code-block:: text

   WARNING clustrix.decorator: @cluster received unrecognised option(s) gpu_type;
   they have no effect. Recognised extras: aws_access_key_id, aws_region,
   aws_secret_access_key, azure_client_id, azure_client_secret,
   azure_subscription_id, azure_tenant_id, gcp_project_id,
   gcp_service_account_key, hf_flavor, hf_namespace, hf_timeout, hf_token,
   hf_username, instance_startup_timeout, k8s_image, k8s_namespace,
   k8s_pull_policy, k8s_service_account, key_file, lambda_api_key,
   terminate_on_completion


The order of operations on a call
---------------------------------

1. Read the global configuration (``get_config()``).
2. Build ``job_config`` by merging decorator arguments over configuration
   defaults.
3. Decide local or remote (``_choose_execution_mode``).
4. Decide sync or async (``async_submit``).
5. Optionally attempt GPU parallelization, then loop parallelization.
6. Serialize the function, its arguments and the environment description.
7. Submit: create the remote job directory, upload the payload, build the
   remote environment, generate and submit a job script.
8. Poll for completion.
9. Download ``result.pkl``, **verify its HMAC**, then deserialize it with dill.
10. Clean up the remote job directory if the job succeeded and
    ``cleanup_on_success`` is set.

Steps 7--10 differ per backend; see :ref:`per-backend-divergence`.


Step 2: resource resolution
---------------------------

Each of ``cores``, ``memory``, ``time``, ``partition``, ``queue`` and
``environment`` falls back to a configuration default when the decorator left
it as ``None``:

===============  =============================
Decorator arg    Config fallback
===============  =============================
``cores``        ``default_cores`` (4)
``memory``       ``default_memory`` (``"8GB"``)
``time``         ``default_time`` (``"01:00:00"``)
``partition``    ``default_partition`` (None)
``queue``        ``default_queue`` (None)
``environment``  ``conda_env_name`` (None)
===============  =============================

The fallback is written as ``cores or config.default_cores``, so ``cores=0``
also falls back. Any resource key still missing when a job script is generated
is filled in again by ``resolve_job_resources``.

Memory strings are rewritten per scheduler by ``normalize_memory``:

.. code-block:: python

   from clustrix.utils import normalize_memory

   print(normalize_memory("8GB", "slurm"))  # SLURM wants 8G
   print(normalize_memory("8GB", "pbs"))    # PBS wants 8gb
   print(normalize_memory("8GB", "k8s"))    # Kubernetes wants 8GB


Step 3: local or remote
-----------------------

``_choose_execution_mode`` answers this, in this order:

1. ``cluster_type == "kubernetes"`` **and** ``auto_provision_k8s`` -> remote.
2. ``cluster_type == "huggingface"`` -> remote. This backend reaches its
   compute over an HTTP API, so it legitimately has no ``cluster_host``;
   without this rule it would fall into rule 3 and silently run on your laptop
   while reporting success.
3. No ``cluster_host`` -> **local**.
4. ``prefer_local_parallel`` is true -> local.
5. Otherwise -> remote.

Note rule 3: with the default configuration and no ``cluster_host``, a
``@cluster`` function runs on your own machine. That is the intended
development behaviour, not a failure. ``cluster_type="local"`` is different --
it is a real backend that goes through serialization (see
:ref:`per-backend-divergence`).

Local execution then splits again:

* ``auto_parallel`` off (or ``parallel=False``) -> ``func(*args, **kwargs)``,
  directly, with no serialization at all.
* ``auto_parallel`` on -> ``_execute_local_parallel``, which looks for a
  parallelizable loop and, if it finds one **and** your function can accept a
  chunk, runs chunks in a process pool. See :doc:`limitations` before relying
  on this: the conditions are narrow and the return shape changes.


Step 6: serialization
---------------------

``serialize_function(func, args, kwargs)`` produces a plain dict. This is the
payload every backend receives:

.. code-block:: text

   function:        <198 bytes>          # the function, by value
   function_source: None                 # inspect.getsource(func), or None
   args:            <16 bytes>
   kwargs:          <21 bytes>
   requirements:    <559 packages>       # name -> version of your environment
   func_info:       {'name': 'demo', 'module': '__main__', 'file': None,
                     'source': None}
   python_version:  '3.9.13 (main, Aug 25 2022, 18:24:45) \n[Clang 12.0.0 ]'
   working_directory: '/path/where/you/called/it'

``function``, ``args`` and ``kwargs`` all go through the same private helper,
``_dumps_by_value``. Arguments get the same treatment as the function on
purpose: stdlib pickle stores a class by qualified name, so passing an instance
of a class defined in your ``__main__`` would fail on the worker with
``Can't get attribute 'Point'``.

How ``_dumps_by_value`` chooses a serializer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Walk the object graph (``_walk_referenced_modules``) and split every module
   it reaches into *project-local* and *installed*.

   ``_is_local_module`` calls a module local when its file (or, for a PEP 420
   namespace package, its ``__path__``) is not under the standard library, a
   ``site-packages``/``purelib``/``platlib`` directory, or the user site
   directory. ``__main__`` and ``clustrix`` itself are always excluded:
   ``__main__`` is serialized by value anyway, and shipping a copy of clustrix
   would bloat every payload for nothing.

   The walk is bounded at 2,000,000 nodes. Exceeding that raises
   ``WalkTooLargeError`` and the submission is refused -- not knowing what a
   payload needs is not the same as knowing it needs nothing.

2. Refuse the job if any *installed* module it reaches belongs to a
   distribution the cluster cannot install (see
   :ref:`environment-replication`).

3. If there are project-local modules, register them with
   ``cloudpickle.register_pickle_by_value`` (under a lock and a refcount,
   because that registry is process-global and ``AsyncClusterExecutor``
   serializes on a thread pool) and use ``cloudpickle.dumps(obj, protocol=4)``.
   **There is no fallback in this branch.** Falling back to a by-reference
   payload would produce exactly the ``ModuleNotFoundError`` the branch exists
   to prevent, minutes later, on the cluster. Instead the failure is reported
   here, with the offending object located by ``_unpicklable_location``:

   .. code-block:: text

      RuntimeError: Cannot serialize this job: something it reaches cannot be
      pickled (cannot pickle 'socket' object). Locks, open files, sockets and
      database handles cannot cross to a worker. Create it where it is used
      instead of capturing it, or install the package on the cluster so the
      worker imports it rather than receiving a copy.

4. If there are no project-local modules, try in order:
   ``dill.dumps(obj, protocol=4, recurse=True)``, then plain ``dill.dumps``,
   then ``cloudpickle.dumps``, then stdlib ``pickle.dumps``.

   ``recurse=True`` is the important one: plain ``dill.dumps(func)`` captures
   closure cells but **not** ``func.__globals__``, so a function that calls a
   module-level helper serializes fine and then dies on the worker with
   ``NameError: name '_helper' is not defined``. ``recurse=True`` walks the
   globals the body actually names and bundles them.

   The silent-degradation risk of this fallback chain is real and is documented
   in :ref:`limitation-unsendable`.

Source code is *metadata*, not the mechanism
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``function_source`` and ``func_info["source"]`` come from
``inspect.getsource(func)`` and are ``None`` when it fails (REPL, notebook
cell, ``exec``). They are only a last-ditch fallback for the worker if binary
deserialization fails. A function with no readable source still serializes and
still runs correctly -- what you lose is the source-based *analysis* features.
See :ref:`limitation-sourceless`.

.. _environment-replication:

Step 6b: environment replication
--------------------------------

``get_environment_requirements()`` reads ``importlib.metadata`` directly -- not
``pip freeze``, not ``uv pip freeze``. Those two disagree about the same
environment (uv renders conda-built distributions as ``name @ file://...``,
pip renders them as ``name==version``), so whichever happened to be on
``PATH`` changed both the requirement set and the environment cache key for a
machine whose environment had not changed at all. Metadata gives the same
answer every time.

The scan is cached in-process, keyed on ``tuple(sys.path)``.

Distributions that cannot be recreated elsewhere are **excluded** from the
requirement map and reported separately by
``get_unreproducible_requirements()``. Three cases qualify:

* installed in editable mode (``direct_url.json`` with ``dir_info.editable``);
* installed from a VCS checkout (``direct_url.json`` with ``vcs_info``);
* present only as a source checkout -- a bare ``.egg-info`` outside every
  installed root, which is what ``setup.py develop`` leaves behind.

A ``name @ file:///.../work`` line from conda is **not** one of these: conda
records the build directory it compiled from, but the artifact landed in
site-packages like any other wheel and ``name==version`` reinstalls it.
Dropping those used to remove about a third of a conda environment.

If your function reaches into one of those packages, submission is refused
immediately, naming the package:

.. code-block:: text

   RuntimeError: This function uses package(s) that cannot be installed on the
   cluster: mylib (installed in editable mode from file:///home/me/src/mylib).
   clustrix mirrors your environment with `pip install name==version`, which for
   these would install something other than what you are running. Publish the
   package, vendor the code into your project directory so clustrix can send it
   by value, or list it in `excluded_packages` if the remote job genuinely does
   not need it.

You can see the same information before you submit:

.. code-block:: python

   from clustrix.utils import (
       get_environment_requirements,
       get_unreproducible_requirements,
   )

   reqs = get_environment_requirements()
   print(type(reqs), "packages will be mirrored:", len(reqs) > 0)
   for name, reason in get_unreproducible_requirements().items():
       print("cannot be reinstalled remotely:", name, "--", reason)

The content-addressed environment cache
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Remote environments are named after *what is in them*, not after the job.
``_environment_key`` hashes:

* ``ENVIRONMENT_RECIPE_VERSION`` (currently ``"3"``),
* the remote Python version,
* every ``name==version`` in the requirement map,
* ``replicate_local_environment``,
* ``excluded_packages``,
* ``cluster_packages``,

and renders the first 12 hex digits as ``py<version><digest>``, producing
conda environments named ``clustrix_venv1_<key>`` and ``clustrix_venv2_<key>``.

.. code-block:: python

   from clustrix.config import ClusterConfig
   from clustrix.utils import ENVIRONMENT_RECIPE_VERSION, _environment_key

   cfg = ClusterConfig()
   key_a = _environment_key("3.11", {"numpy": "1.26.4"}, cfg)
   key_b = _environment_key("3.11", {"numpy": "1.26.3"}, cfg)
   print("recipe version:", ENVIRONMENT_RECIPE_VERSION)
   print("same requirements reuse one environment:",
         key_a == _environment_key("3.11", {"numpy": "1.26.4"}, cfg))
   print("different requirements never share one:", key_a != key_b)

Two consequences:

* The second job with the same requirements reuses the first job's
  environments. Building them per job cost roughly ten minutes each on a shared
  filesystem, every time, and left them behind.
* ``ENVIRONMENT_RECIPE_VERSION`` must be bumped whenever the *recipe* changes,
  because the key hashes the inputs. Without it a policy change leaves every
  existing environment matching its old key, so the cache serves a stale
  environment forever.

A conda environment is only reused if it contains ``.clustrix_ready``, which is
written as the last link in the ``&&`` chain. The name alone proves nothing: a
run whose installs failed left a named but half-built environment behind.

.. _two-venv:

Step 7: the two-venv model
--------------------------

This is the part of Clustrix that is most often misunderstood, so it is worth
being precise.

``setup_two_venv_environment`` builds **two** environments on the cluster:

* **VENV1 -- the serialization environment.** Contains ``dill`` and
  ``cloudpickle`` and nothing else. Its job is to turn bytes into objects and
  objects back into bytes.
* **VENV2 -- the execution environment.** A mirror of your local environment:
  every ``name==version`` from ``get_environment_requirements()`` (minus
  ``excluded_packages``), plus ``cluster_packages``, plus any
  ``venv_post_install_commands``. Your function runs here.

Both are pinned to your **local** Python minor version. Conda is preferred and
is located by probing for ``etc/profile.d/conda.sh``, because paramiko's
``exec_command`` starts a non-interactive, non-login shell that never sources
the profile scripts that put ``conda`` on ``PATH``. If conda is absent,
clustrix probes for a system interpreter and ``_select_remote_python`` requires
an exact minor-version match:

.. code-block:: text

   RuntimeError: The remote system has Python 3.9, but this session runs Python
   3.12. Serialized functions carry CPython bytecode, which cannot be loaded by
   a different minor version, so the cluster needs a Python 3.12 interpreter --
   or conda, which clustrix will use to create one.

Why two environments
~~~~~~~~~~~~~~~~~~~~~~~~

Because the environment that can *read the payload* and the environment that
can *run the function* have different requirements, and forcing them to be the
same environment means either the deserializer is missing or the user's
packages are.

VENV1 needs exactly dill and cloudpickle, at versions that match the sender.
VENV2 needs whatever your function imports -- possibly hundreds of packages,
possibly a GPU build of PyTorch -- and must not have its dependency resolution
disturbed by clustrix's own needs.

The three programs
~~~~~~~~~~~~~~~~~~~~~~

``generate_two_venv_execution_commands`` emits three separate ``python -c``
programs into the job script, communicating through files in the job
directory:

.. code-block:: text

   VENV1  read function_data.pkl        -> write function_deserialized.pkl
   VENV2  read function_deserialized.pkl -> run it -> write result_raw.pkl
   VENV1  read result_raw.pkl           -> write result.pkl + result.pkl.hmac

You can print the real commands without a cluster:

.. code-block:: python

   from clustrix.utils import generate_two_venv_execution_commands

   lines = generate_two_venv_execution_commands(
       "/scratch/me/jobs/job_1", "clustrix_venv1_py311_abc", "clustrix_venv2_py311_abc"
   )
   print("\n".join(lines[:8]))

which prints:

.. code-block:: text

   # Two-venv approach for cross-version compatibility
   # VENV1: Serialization/deserialization with compatible Python
   # VENV2: Function execution with proper environment

   # Step 1: Use VENV1 to deserialize function data
   # Using conda environment clustrix_venv1_py311_abc
   conda run -n clustrix_venv1_py311_abc python -c "
   import os as _os

Never stdlib pickle
~~~~~~~~~~~~~~~~~~~~~~~

Every handoff in that chain binds ``_ser`` to ``dill``, falling back to
``cloudpickle``, and **raises if neither is importable**:

.. code-block:: text

   RuntimeError: clustrix needs dill (or at least cloudpickle) in this
   environment: the function, its arguments and its result are exchanged as dill
   bytes, which stdlib pickle cannot read. Install it on the cluster (pip install
   dill) and re-submit.

There is a deliberate asymmetry here that has broken this path before. Stdlib
pickle serializes a function *by qualified name*: it writes down
``__main__.analyze`` and expects the loading interpreter to be able to import
it. A fresh remote interpreter cannot -- there is no ``__main__.analyze``
there -- so the payload fails with ``AttributeError: Can't get attribute
'analyze' on <module '__main__'>``. Since almost every function you decorate is
defined in your ``__main__``, this affected essentially every job. Dill and
cloudpickle serialize by *value* instead, embedding the code object, so a fresh
interpreter can rebuild the function without importing anything.

Falling back to stdlib pickle in these stages was therefore never a graceful
degradation -- pickle cannot even read the dill bytes the previous stage wrote,
and the job died somewhere inside the unpickler naming neither the missing
package nor the real cause. The same rule applies on the receiving end:
``result.pkl`` is loaded with ``dill.loads``, because replaying dill's
reconstruction opcodes through stdlib pickle builds a *fresh* class for
anything defined in your ``__main__``, so a returned instance failed
``isinstance()`` against the very class that defined it.

The single-venv fallback
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If ``use_two_venv=False``, or if the two-venv setup raises or exceeds
``venv_setup_timeout`` (300s), ``_setup_job_environment`` logs a warning and
falls back to ``setup_remote_environment``, which builds **one** venv
containing only ``dill`` and ``cloudpickle`` (version-pinned to yours when
known). It does **not** mirror your environment. A job that lands here will
fail on the first ``import`` of anything outside the standard library, so treat
the warning ``Two-venv setup failed, falling back to basic setup:`` as an
error in practice.


Step 7b: the job directory and the signing key
----------------------------------------------

For every SSH-reachable backend, ``_stage_job_directory`` does this:

1. Resolve ``remote_work_dir`` (expanding a leading ``~/`` via ``echo $HOME``,
   because SFTP does not expand ``~`` and would create a directory literally
   named ``~``).
2. ``mkdir -p`` the parent, then ``mkdir -m 700`` the job directory itself.
   The exclusive create is deliberate: ``mkdir -p`` succeeds on a directory
   somebody else already owns, and job directory names used to be fully
   predictable, so on a world-writable work directory an attacker could
   pre-create the directory and receive the signing key into it.
   Names are now ``job_<unix-time>_<8 hex chars>``.
3. Write a fresh 64-hex-character key to ``.clustrix_result_key`` with mode
   0600, **over SFTP** -- writing it with ``printf ... > file`` would put the
   secret in a remote command line, readable from ``ps`` by any user on the
   login node.
4. ``pickle.dump`` the ``func_data`` dict (the outer dict only; its inner
   values are already dill/cloudpickle bytes) and upload it as
   ``function_data.pkl``.


Step 7c: the job script
-----------------------

``create_job_script`` dispatches on cluster type to
``_create_slurm_script`` / ``_create_pbs_script`` / ``_create_sge_script`` /
``_create_ssh_script``. Anything else raises
``ValueError: Unsupported cluster type: ...``. All four share
``environment_setup_lines`` and ``job_execution_lines``.

.. code-block:: python

   from clustrix.config import ClusterConfig
   from clustrix.utils import create_job_script

   cfg = ClusterConfig(
       cluster_type="slurm",
       cluster_host="hpc.example.edu",
       username="me",
       module_loads=["python/3.9"],
       environment_variables={"OMP_NUM_THREADS": "4"},
   )
   script = create_job_script(
       "slurm",
       {"cores": 8, "memory": "16GB", "time": "02:00:00"},
       "/home/me/.clustrix/jobs/job_1",
       cfg,
   )
   print("\n".join(script.splitlines()[:11]))

Real output:

.. code-block:: text

   #!/bin/bash
   #SBATCH --job-name=clustrix
   #SBATCH --output=/home/me/.clustrix/jobs/job_1/slurm-%j.out
   #SBATCH --error=/home/me/.clustrix/jobs/job_1/slurm-%j.err
   #SBATCH --cpus-per-task=8
   #SBATCH --mem=16G
   #SBATCH --time=02:00:00
   module load python/3.9
   export OMP_NUM_THREADS=4
   export CLUSTRIX_RESULT_KEY=$(cat /home/me/.clustrix/jobs/job_1/.clustrix_result_key 2>/dev/null || true)
   cd /home/me/.clustrix/jobs/job_1

Three settings are pasted into that script unquoted, because quoting would
break what they mean, and are therefore validated instead:

* ``module_loads`` -- ``module`` is a shell function and the module name is its
  bare argument. Entries must match ``[A-Za-z0-9._:/=+,@%-]+``.
* ``environment_variables`` keys -- ``export NAME=`` needs the bare name.
  Values beside them *are* quoted with ``shlex.quote``.
* ``pre_execution_commands`` -- shell commands by definition, passed through
  unchanged. A user who writes a command there is asking for it to run.

.. code-block:: python

   from clustrix.utils import validate_shell_fragment

   try:
       validate_shell_fragment("module_loads", "python; rm -rf /")
   except ValueError as exc:
       print(type(exc).__name__, "raised as expected")

The refusal reads:

.. code-block:: text

   ValueError: clustrix config module_loads='python; rm -rf /' cannot be used: it
   is written into a generated job script at a place that must stay unquoted (a
   scheduler directive or a module-load line), so a shell metacharacter there
   would run as a command. Allowed characters are letters, digits and
   . _ : / = + , @ % -

The job script also exports ``CLUSTRIX_RESULT_KEY`` from the 0600 key file
rather than baking it into ``job.sh``, which is world-readable on some shared
filesystems. The first thing each Python stage does is ``os.environ.pop`` that
variable, so your function -- and everything it imports -- never sees the
secret it would need to forge a result.


Steps 8--9: polling, verification, and the result
-------------------------------------------------

``wait_for_result`` polls ``check_job_status`` every ``job_poll_interval``
seconds (default 30) until the status is ``completed`` or ``failed``.

* **SLURM** -- ``squeue -j <id> -h -o %T``, with a file-based fallback because
  completed jobs leave the queue.
* **PBS** -- ``qstat -f <id>``, reading ``job_state``.
* **SGE** -- same shape as PBS.
* **SSH** -- purely file-based: does ``result.pkl`` exist, or an error file.

On ``completed``, the result path is downloaded, and then -- **before** any
deserialization -- ``_verify_result_signature`` reads ``result.pkl.hmac`` from
the job directory and checks it against the key recorded at submission.

This matters because ``dill.loads`` executes code. The result file arrives from
a remote host over a shared filesystem, so it is not something to open on
trust. Verification is done by one function, ``verify_signed_payload``, and it
has exactly three refusals:

.. code-block:: python

   from clustrix.utils import verify_signed_payload, PayloadAuthenticationError

   for tag, key in [("abc", None), ("", "k" * 8), ("deadbeef", "k" * 8)]:
       try:
           verify_signed_payload(b"payload", tag, key, "Job slurm_1234")
       except PayloadAuthenticationError as exc:
           print(type(exc).__name__, "->", str(exc).split(".")[0])

The three real messages:

.. code-block:: text

   PayloadAuthenticationError: No result-signing key is recorded for Job
   slurm_1234, so what it produced cannot be authenticated. Refusing to
   deserialize it: loading a pickle executes code. Re-run the job from this
   process, which records a key at submission.

   PayloadAuthenticationError: Job slurm_1234 produced a payload with no
   signature. Refusing to deserialize it: loading a pickle executes code, and an
   unsigned payload cannot be told apart from a file someone else wrote into the
   job directory.

   PayloadAuthenticationError: Job slurm_1234 payload failed its integrity
   check. Refusing to deserialize it.

Read the first one carefully: **a missing key is a refusal, not a warning.**
"No key recorded" and "forged" are indistinguishable from the caller's side, so
both are refused. The practical consequence is that a job can only be collected
by the process that submitted it -- the key lives in
``SchedulerManager.active_jobs``, in memory. Restart your interpreter and the
result is unreachable through clustrix.

``error.pkl`` is signed and verified exactly the same way, for the same reason:
it is also passed to ``dill.loads`` on your machine, so a failing job must not
be a cheaper way onto the submitting host than a succeeding one.

What verification is and is not
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It bounds trust to whoever can already read the job directory. It stops an
unrelated user on a shared filesystem, a stale file from an earlier run, and a
truncated transfer. It is **not** a defence against a wholly compromised remote
host -- that host runs your function anyway.

Errors
~~~~~~~~~~

On ``failed``, ``extract_original_exception`` downloads and verifies
``error.pkl`` and deserializes it with dill. The remote stages ship the
exception *object* under an ``exception`` key, so the original type survives
and ``except ValueError:`` fires as you would expect. If that fails, clustrix
falls back to ``RuntimeError(f"Job {job_id} failed. Error log:\n{error_log}")``.

Each of the three stages writes its own ``error_<stage>.pkl`` unconditionally
but only claims the shared ``error.pkl`` if no earlier stage already did, so a
stage-1 failure is not overwritten by the cascade it causes.


.. _per-backend-divergence:

Per-backend divergence
----------------------

===================  ==================================================================
Backend              How the flow differs
===================  ==================================================================
``local``            No SSH, no job script, no venvs, no HMAC. ``LocalJobManager``
                     deserializes the payload and runs it **synchronously inside**
                     ``submit_job``; ``wait_for_result`` just returns the recorded
                     outcome or re-raises the recorded exception. ``cancel_job``
                     always raises, because the work already happened.
``ssh``              Full flow. ``nohup bash job.sh > job.out 2> job.err &``. Job id
                     is ``ssh_<unix-time>``; completion is detected by the presence
                     of ``result.pkl``.
``slurm``            Full flow. ``sbatch job.sh``; job id is the last whitespace
                     token of sbatch's output.
``pbs``              Same staging and environment setup as SLURM (this used to be
                     missing entirely), ``qsub job.pbs``, job id is the whole
                     trimmed stdout. Not verified against real hardware.
``sge``              ``qsub job.sge``; job id is the third token of
                     ``Your job 123456 ...``. Not verified against real hardware.
``kubernetes``       No SSH and **no environment replication**. A Job manifest runs
                     ``pip install cloudpickle dill --quiet`` in ``k8s_image``
                     (default ``python:3.11-slim``) and then a single embedded
                     worker program. Result and signature come back as two prefixed
                     lines in the **pod log**, HMAC-verified with
                     ``CLUSTRIX_RESULT_KEY`` passed as a container env var. The
                     worker program is refused if it contains ``"``, ``$`` or
                     backtick, which the shell would reinterpret. Not verified
                     against a real cluster.
``huggingface``      No SSH. One ``python -c`` bootstrap runs in a container whose
                     image defaults to ``python:<your minor version>-slim``. It pops
                     ``CLUSTRIX_HMAC_KEY`` from the environment *before* pip runs,
                     installs ``dill``, ``cloudpickle`` and ``CLUSTRIX_PACKAGES``
                     (your mirrored environment plus ``cluster_packages``), then
                     runs the function and prints an HMAC-tagged base64 dill blob
                     between marker lines. Payloads over 256 KB are staged through
                     a **private dataset repo** instead of the environment. A
                     function that raises exits 0 -- an exception is an ordinary
                     outcome, not a failed job.
``provider=...``     ``@cluster(provider="aws"|"gcp"|"azure"|"lambda"|"huggingface")``
                     routes to ``CloudJobManager`` instead of the cluster path.
                     None of these has been shown to run a job end to end; see
                     :doc:`limitations`.
===================  ==================================================================

Two things every backend does share: the payload produced by
``serialize_function``, and the rule that results are dill-serialized and
HMAC-signed before they are trusted.


Failure modes, stage by stage
-----------------------------

=========================  =======================================================
Stage                      What you see when it goes wrong
=========================  =======================================================
Decoration                 Nothing. Errors cannot occur here.
Resource resolution        ``WARNING ... unrecognised option(s) X``; the option is
                           ignored.
Serialization              ``RuntimeError: Cannot serialize this job: ...`` naming
                           the offending object, **or** (no project-local module
                           involved) a silent fallback to a payload missing that
                           global, which fails remotely with ``NameError``.
Environment scan           ``RuntimeError: This function uses package(s) that
                           cannot be installed on the cluster: ...``
SSH connection             ``ValueError: cluster_host must be specified for
                           SSH-based clusters``; paramiko errors; an unknown host
                           key is rejected unless
                           ``ssh_host_key_policy="auto_add"``.
Job directory              ``mkdir -m 700`` failing is fatal and is not retried.
Environment build          ``RuntimeError: Failed to setup two-venv environment:
                           <stderr>``, or a version-skew ``RuntimeError``. Both are
                           caught one level up and downgraded to the single-venv
                           fallback with a ``WARNING``.
Job submission             Scheduler stderr; a job id that does not parse.
Execution                  The remote exception, re-raised locally with its
                           original type.
Result collection          ``PayloadAuthenticationError`` (missing key, missing
                           signature, or mismatch). Never downgraded to a warning.
Cleanup                    ``rm -rf <job dir>`` only runs on success and only when
                           ``cleanup_on_success`` is true, so a failed job's
                           directory is left for inspection.
=========================  =======================================================


See also
--------

* :doc:`configuration` -- every setting that changes the behaviour above.
* :doc:`limitations` -- what this model cannot do, and the workarounds.
