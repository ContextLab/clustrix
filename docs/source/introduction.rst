.. _introduction:

Introduction
============

Clustrix runs an ordinary Python function somewhere else.

You write the function you would have written anyway, add ``@cluster`` above
it, and call it normally. Clustrix serializes the function together with its
arguments, ships them to a compute resource you configured, runs them there,
and returns the function's return value to the caller. The call site looks
exactly like a local call:

.. code-block:: python

    from clustrix import cluster

    @cluster(cores=8, memory="16GB", time="02:00:00")
    def fit_model(n_samples: int, seed: int = 0):
        import random

        random.seed(seed)
        return sum(random.random() for _ in range(n_samples)) / n_samples

    print(fit_model(100_000))

That block runs *right now*, on your laptop, because no cluster is configured.
Point :func:`clustrix.configure` at a SLURM login node and the same three
lines submit a batch job, poll it, and hand you the same float back. That
substitutability is the entire point of the library.

.. _the-problem:

The problem it solves
---------------------

Running one Python function on a cluster is, in practice, not one step. It is
a dozen:

1. Write a shell script with the right scheduler directives.
2. Get your code onto the cluster (``scp``, ``rsync``, a git push-and-pull).
3. Reproduce your Python environment there, at the versions you actually
   tested against.
4. Write a wrapper that imports your module, calls your function with the
   right arguments, and saves the result somewhere.
5. Submit it. Get an opaque job ID.
6. Poll ``squeue`` until it disappears.
7. Work out whether it finished or died, by reading a log file whose name you
   have to guess.
8. Copy the result back, unpickle it, and hope the pickle protocol matched.
9. Repeat all of the above every time you change one line.

Steps 1--9 have nothing to do with your science or your product. They are the
same every time, they are easy to get subtly wrong, and getting them wrong
usually shows up as a job that silently produced the wrong number rather than
as an error.

Clustrix automates that loop. It captures your local environment's
requirements, creates the remote working directory, uploads the serialized
call, generates the scheduler script, submits it, polls it, retrieves the
result or the traceback, and cleans up. What you get back is either the
function's value or an exception raised in your own process.

.. _how-it-works:

How it works, in one paragraph
------------------------------

``@cluster`` wraps your function. When you call the wrapper, Clustrix decides
between local and remote execution (no ``cluster_host`` configured means it
just calls your function in-process). For remote execution it pickles the
function *by value* using ``dill(recurse=True)`` with a ``cloudpickle``
fallback, so closures, nested functions and module-level globals the body
references all travel with it; the arguments are pickled the same way.
That payload plus a captured requirements list is uploaded over SFTP, a
scheduler script is generated for your ``cluster_type``, the job is submitted,
and Clustrix polls it. On success it downloads ``result.pkl``; on failure it
downloads ``error.pkl`` and re-raises. :doc:`execution_model` describes each
of those stages in detail.

One consequence is worth stating up front: **serialization does not need your
function's source code.** In other words, a function you typed into a REPL, a
notebook cell, or built with ``exec`` serializes and runs correctly, because
dill and cloudpickle work from the compiled code object rather than from text.
Only the *source-based* features need ``inspect.getsource()``. Automatic loop
parallelization (``@cluster(parallel=True)``) is the one that matters here: it
parses the function body with ``ast``, and quietly does nothing when there is
no source to parse.

.. _what-clustrix-is-not:

What Clustrix is not
--------------------

Being clear about the boundaries saves more time than any feature list.

**It is not a distributed dataframe or array library.** There is no
Clustrix equivalent of a partitioned DataFrame, no lazy graph, no shuffle. The
unit of work is one Python function call.

**It is not a long-lived cluster runtime.** There is no scheduler daemon, no
worker pool that stays warm between calls, no actor model, no shared object
store. Each decorated call is an independent job.

**It is not a workflow engine.** There are no task dependencies, no DAG, no
retries-with-backoff policy, no provenance database. If you need "run A, then
B and C in parallel, then D, and resume from step C after a crash", use a
workflow tool and let it call your Clustrix-decorated functions.

**It is not a low-latency dispatcher.** Every remote call pays for
serialization, an SFTP upload, environment setup on the worker, scheduler
queue time, and a download. That is seconds at best, and on a busy HPC queue
it is however long the queue is. Sending a millisecond of work through it is
pure loss.

**It does not stage your data for you.** What travels to the worker is the
pickled function and its pickled arguments, and nothing else. A dataset your
function opens by path has to already be reachable from the worker -- on a
shared filesystem, in object storage it can authenticate to, or somewhere you
put it yourself with ``scp`` or ``rsync`` beforehand. The
:doc:`filesystem utilities <tutorials/filesystem_tutorial>` are read-only:
``cluster_ls``, ``cluster_glob``, ``cluster_stat`` and their siblings let you
inspect and locate remote data, and they are not a transfer service for bulk
inputs. Passing a 200 GB array as an *argument* is worse still, since it would
be pickled into the payload.

Explicit input and output staging -- declaring the files a call needs and the
files it produces, and having Clustrix move them -- is planned rather than
present. `Issue #151
<https://github.com/ContextLab/clustrix/issues/151>`_ carries the design.
Until it lands, treat the paragraph above as the working rule.

.. _alternatives:

How it compares to what you are probably already doing
------------------------------------------------------

Writing the sbatch script yourself
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the honest baseline for anyone on an HPC system, and it is not a bad
one. It is completely transparent, it has no dependencies, and when something
breaks you can see exactly where.

Clustrix wins when you iterate: the edit-submit-inspect loop collapses to
editing a Python function and calling it. It also removes the two failure
modes that cost the most time -- an environment on the cluster that has
drifted from the one you tested against, and a wrapper script that loads the
wrong version of your code.

Hand-written sbatch wins when the job is not shaped like "call this Python
function": array jobs over an existing file list, MPI programs, non-Python
executables, anything that needs specific scheduler features Clustrix does not
expose. Clustrix passes ``cores``, ``memory``, ``time``, ``partition`` and
``queue`` through to the generated script; anything more exotic than that is
easier to write yourself.

Dask
~~~~

Dask is the right answer when your problem *is* a big array or dataframe, or
when it decomposes into thousands of small interdependent tasks whose graph
Dask can optimize. It keeps a live scheduler and workers, moves intermediate
results between them, and spills to disk. ``dask-jobqueue`` will even bring up
those workers as SLURM/PBS/SGE jobs.

Clustrix is a much smaller thing. It has no scheduler, no cluster state, no
task graph, and no way to pass an intermediate result from one remote call to
another without it coming back through your process. In exchange, there is
nothing to stand up: no cluster object, no adaptive scaling, no worker
lifetime to reason about, and no requirement that your code be expressed as a
graph. If your workload is "run this one expensive function on a big node",
Dask's machinery is overhead and Clustrix is a decorator.

Ray
~~~

Ray is a distributed runtime: actors, a shared object store, task
dependencies expressed between remote calls, and its own libraries built on
top (Tune, Serve, RLlib). If you want stateful workers, or task A's output fed
into task B without a round trip through the driver, Ray does that and
Clustrix does not.

Ray also expects to own the cluster. Getting it onto a shared HPC system means
launching a head node and workers as scheduler jobs and managing their
lifetime. Clustrix instead speaks the scheduler's own language: it submits an
ordinary batch job and exits. On a machine where you cannot run a persistent
daemon, that difference is decisive.

joblib
~~~~~~

``joblib.Parallel`` is the closest thing in spirit -- parallelize a loop with
minimal ceremony -- and for multi-core work on one machine it is the better
tool by a wide margin. Reach for joblib there. Clustrix's own local path does
not compete with it: ``@cluster(cores=N)`` with no cluster configured runs your
function in the calling process, one core, and ``cores`` is ignored
(`issue #152 <https://github.com/ContextLab/clustrix/issues/152>`_). The
in-process pools that :class:`clustrix.local_executor.LocalExecutor` builds are
real and do give a speedup, but you have to drive them yourself; see
:doc:`the local-parallelism notebook <notebooks/local_parallel_comparison>`.

The difference that does favour Clustrix is reach. joblib's backends are
processes and threads on the current machine, and its distributed backends
require Dask or Ray underneath. Clustrix's target is a machine you do not have
a shell on right now.

Plain SSH + rsync
~~~~~~~~~~~~~~~~~

For a one-off, this is fine, and it is what Clustrix does underneath for the
``ssh`` backend. It stops being fine when it becomes a habit: the script
accumulates, the environment on the far end drifts, and eventually you cannot
tell whether the number you got came from the code currently in your editor.

Clustrix's version of this is the ``ssh`` cluster type, which is verified
end to end. It adds serialization of the exact function object you called,
environment capture, result retrieval, and -- worth calling out -- host key
verification against your ``known_hosts`` by default, with an unknown key
rejected rather than silently trusted.

.. _when-not-to-use:

When Clustrix is the wrong tool
-------------------------------

Do not use it if:

- **Your function is fast.** Anything under a few seconds of compute is
  dominated by submission overhead.
- **You need results streamed back as they are produced.** Clustrix returns
  the function's return value when the job finishes. There is no partial
  result channel.
- **Your workload is a data pipeline over data that already lives on the
  cluster.** Then the cluster's own tooling, or Dask/Spark, is closer to the
  shape of the problem.
- **You need stateful workers.** Every call starts a fresh process.
- **You need guaranteed-correct dependency resolution on the remote side.**
  Clustrix reconstructs an environment from your local requirements. That
  works well for pure-Python and common scientific stacks, and it can fail for
  packages with heavy system-level or GPU-driver-specific builds. Pin what
  matters and check the first job's output.
- **You need PBS, SGE, Kubernetes or a cloud VM provider.** None of those is
  currently supported; see :ref:`removed-backends`.

.. _maturity:

Backend maturity
----------------

Clustrix ships exactly four backends, and each one has been exercised against
the real thing. ``slurm``, ``ssh`` and ``huggingface`` have each submitted a
real job to real infrastructure and returned its result; ``local`` runs
in-process. :ref:`supported-cluster-types` on the front page is the
authoritative list, and ``cluster_type`` accepts nothing outside it.

PBS, SGE, Kubernetes and the AWS / GCP / Azure / Lambda Cloud VM providers are
**not supported**. Setting ``cluster_type`` to any of them raises a
``ValueError`` that names the backend and its tracking issue rather than
failing somewhere deeper. Each is planned for a future release -- see
:ref:`removed-backends`.

Where to go next
----------------

- :doc:`installation` -- install it, including the optional extras.
- :doc:`quickstart` -- a working result in five minutes, starting with a
  backend that needs no cluster at all.
- :doc:`execution_model` -- what actually happens between your call and your
  result.
- :doc:`configuration` -- every setting, where it can be set, and how
  credentials are handled.
- :doc:`limitations` -- the known sharp edges, in full.
