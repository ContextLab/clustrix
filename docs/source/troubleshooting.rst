.. _troubleshooting:

Troubleshooting
===============

This page is about what to do when a job fails: where to look, what the
messages mean, and which failures are yours versus the cluster's.

:doc:`execution_model` has a table of *what* can fail at each stage. This page
is about *finding out which one happened*.

Turn on logging first
---------------------

Clustrix logs through the standard :mod:`logging` module and says nothing at
default levels. Almost every question below is answered faster with logging on:

.. code-block:: python

   import logging

   logging.basicConfig(
       level=logging.DEBUG,
       format="%(levelname)s %(name)s: %(message)s",
   )

``INFO`` is usually enough to see the job id, the remote directory, which
execution mode was chosen and why, and whether an environment was built or
reused. ``DEBUG`` adds the generated job script and the SSH commands.

Where the files are
-------------------

Every job gets its own directory on the remote host, under
``remote_work_dir`` (default ``~/.clustrix/jobs``), named
``job_<epoch>_<random>``. The random suffix exists so two jobs submitted in
the same second cannot collide.

The directory is created mode ``700`` and contains:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - File
     - What it is
   * - ``function_data.pkl``
     - the serialized function, arguments and requirements you sent
   * - ``job.sh``
     - the generated job script, exactly as submitted
   * - ``result.pkl`` / ``result.pkl.hmac``
     - the result and its signature, written by the worker on success
   * - ``error.pkl`` / ``error.pkl.hmac``
     - the exception and traceback, signed the same way, written on failure
   * - ``error_venv1_deserialize.pkl``, ``error_venv2_execute.pkl``, ``error_venv1_serialize.pkl``
     - per-stage errors. The **first** stage to fail claims ``error.pkl``; these say which stage it was
   * - ``.clustrix_result_key``
     - the per-job signing key, mode ``600``

Scheduler output lands in the same directory:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Backend
     - Files
   * - SLURM
     - ``slurm-<jobid>.out``, ``slurm-<jobid>.err``
   * - SSH
     - ``job.out``, ``job.err``
   * - HuggingFace Jobs
     - no files; the job log is fetched through the API

.. important::

   ``cleanup_on_success`` defaults to ``True``, so a **successful** job's
   directory is removed. A **failed** job's directory is kept -- that is the
   whole point. If you want to inspect a successful run, set
   ``cleanup_on_success=False`` before submitting.

Reading a failure
-----------------

When a job fails, clustrix retrieves the signed ``error.pkl`` and re-raises the
original exception, with its original type, in your process. Most of the time
you do not need to log in at all -- the traceback you get is the one from the
compute node.

You do need to log in when the failure happened *before* Python started: a
missing module, a scheduler rejection, an exit 127. Those appear in the
scheduler's ``.err`` file, not in ``error.pkl``.

.. code-block:: bash

   ssh you@cluster
   cd ~/.clustrix/jobs
   ls -t | head            # most recent job directories first
   cd job_1787099351_a1b2c3d4
   cat slurm-*.err         # or job.err on the ssh backend
   cat job.sh              # exactly what ran

Messages you are likely to see
------------------------------

**"... produced a payload with no signature. Refusing to deserialize it."**

The job produced a result or error file that carries no HMAC. Loading a pickle
executes code, so clustrix refuses rather than trusting a file from a remote
host. Treat it as a warning sign: a result that should have been signed at
submission was not. See :doc:`execution_model`.

**"No result-signing key is recorded for Job ... "**

This process does not hold the key. Keys live in memory for the life of the
submitting process only, so a *different* process cannot collect a job's result
-- and a fresh interpreter after a notebook restart is a different process.
Job results are not portable across processes.

**"Host key verification failed for '<host>' ..."**

The host is not in your ``known_hosts``. This is the default and it is
deliberate. The message contains the fingerprint the host offered and the
``ssh-keyscan`` command that adds it; the notebook widget offers a "Trust this
host" button instead. See :ref:`host-key-verification`.
The alternative, ``ssh_host_key_policy="auto_add"``, trusts any key and is what
makes machine-in-the-middle attacks possible; choose it knowingly or not at
all. It also writes: on that policy clustrix creates ``~/.ssh/known_hosts`` if
it is absent -- directory ``0700``, file ``0600`` -- so that the key it accepts
is actually recorded and the *second* connection to that host is verified
rather than re-accepted. ``reject`` never creates anything.

**"This function uses package(s) that cannot be installed on the cluster: ..."**

Your function reaches into a package that exists only on your machine -- an
editable install, a git checkout, a bare source tree. The message names the
package and how it was installed. :doc:`limitations` lists the workarounds.

**"The remote system has Python 3.x, but this session runs Python 3.y."**

Refused at submit time on purpose. dill embeds CPython bytecode, so a
minor-version mismatch produces ``unknown opcode`` at run time -- a far more
confusing failure than this one.

**Exit 127, no other output**

The job died before it could write a diagnostic, almost always because
``remote_work_dir`` is not visible from the compute node. On SLURM each node
has its own ``/tmp``, so an environment built on the login node
simply is not there at run time. Use a home directory or shared scratch. The
default (``~/.clustrix/jobs``) is already safe; this bites people who set
``/tmp/...`` deliberately.

**The call has not returned and the job is still queued**

A scheduler backend blocks in a poll loop, and that loop has a deadline:
``job_wait_timeout``, 24 hours by default. On expiry you get a
``TimeoutError`` naming the job's last known status and the remote directory
its files are in. The job is deliberately **not** cancelled -- it may still be
queued, and cancelling someone's allocation because the client got bored is
not that function's decision -- so the result can still be collected by hand
from the directory the message names. Raise ``job_wait_timeout`` for a queue
that legitimately runs longer, or set it to ``None`` to wait indefinitely.
Polling starts one second apart and backs off to ``job_poll_interval``
(30 seconds), which is the longest gap between checks.

**"@cluster(cores=N) has no effect here: ... has no loop that can be split"**

Locally, ``cores`` is the number of worker processes a loop is divided among.
The function has no loop that qualifies, so it ran once in the calling
process. ``print(clustrix.explain(function))`` gives the line and the reason;
:doc:`parallel_loops` lists what qualifies.

**"<function>: not split -- ..."**

A loop passed the source analysis and then failed a run-time check (a shared
value was modified, or the global random state moved). The loop ran once, in
order, and the result is the sequential one. Nothing needs fixing unless you
wanted the parallel run.

**FileNotFoundError on the cluster for a file that exists locally**

The path was not found by automatic staging. Usually it is computed at run
time (``os.path.join(...)`` with a variable, a glob, a path read from a
configuration file). Pass it as an argument or list it in
``@cluster(data=[...])``. Staging does not apply to HuggingFace Jobs or to
``async_submit=True``. See :doc:`input_files`.

**"This call names ... of local input, more than clustrix copies to a cluster automatically"**

The call would upload more than ``auto_stage_max_bytes``. Put the data on the
cluster and pass that path, raise the limit, or exclude a path with
``@cluster(no_data=[...])``.

**A file the job wrote is not on my machine**

Only the return value comes back. See :doc:`input_files`.

**The first job on a host takes much longer than later ones**

It builds the Python environment there. Later jobs with the same requirements
reuse it. ``configure(replicate_local_environment=False)`` builds a smaller
environment from only the packages the job was found to need.

When the answer looks wrong rather than missing
-----------------------------------------------

- A divided loop returns what the sequential loop returns, with one
  exception that no check can see: side effects outside the values the loop
  was given (a module-level cache, files, a database, GPU random state)
  happen in an unspecified order and, locally, in worker processes. If a
  result depends on such state, use ``@cluster(parallel=False)``. See
  :ref:`parallel-loops-unseen`.
- Passing a keyword to ``@cluster`` that it does not recognise is accepted and
  **ignored**, with a warning. ``hf_namespace``, ``hf_token``,
  ``hf_username`` and ``key_file`` are accepted without a warning and are
  also not read from the decorator. If a setting seems not to apply, check
  :doc:`configuration` for whether it is read at all.

Getting help
------------

If you open an issue, the useful things to include are: the clustrix version
(``python -c "import clustrix; print(clustrix.__version__)"``), the
``cluster_type``, the ``INFO``-level log of the submission, and the contents of
the failed job directory's ``.err`` file. The job script itself (``job.sh``) is
usually more informative than any description of it.
