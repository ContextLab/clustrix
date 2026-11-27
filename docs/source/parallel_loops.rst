.. _parallel-loops:

Loop parallelisation
====================

If a function decorated with ``@cluster`` contains a loop whose iterations do
not depend on each other, Clustrix runs the iterations in parallel and returns
what the function would have returned. The function is not rewritten by you
and takes no special arguments.

This page says which loops qualify, where the pieces run, what is checked
while they run, and what cannot be checked at all. The implementation is
``clustrix/parallelize.py``.

.. contents:: On this page
   :local:
   :depth: 1

An example
----------

.. code-block:: python

   import math

   import clustrix
   from clustrix import cluster, configure

   configure(cluster_type="local")


   @cluster(cores=4)
   def simulate(seeds):
       totals = []
       for seed in seeds:
           total = 0.0
           for i in range(1, 200_000):
               total += math.sin(seed + i) / i
           totals.append(total)
       return max(totals)


   print(clustrix.explain(simulate))
   print(simulate(range(8)))

Saved as a script and run on a 4-CPU machine, this prints:

.. code-block:: text

   simulate:
     line 12: `for seed in seeds` -- SPLIT
         collected in order: totals
         private to each iteration: i, seed, total
     code before the loop runs in the caller
     code after the loop runs in the caller
   1.0707919409038549

The return value is the same object a plain call returns: here a float, not a
list of per-worker results.

``clustrix.explain``
--------------------

.. autofunction:: clustrix.explain
   :no-index:

``explain`` takes a function (decorated or not), runs nothing, and returns a
string. Print it before relying on a loop being divided. For a loop that
cannot be divided it gives the line and the reason:

.. code-block:: python

   import clustrix


   def running_total(values):
       total = 0
       history = []
       for value in values:
           total = total + value
           history.append(total)
       return history


   print(clustrix.explain(running_total))

.. code-block:: text

   running_total:
     line 7: `for value in values` -- not split
         line 8: total is read before the iteration assigns it, so it carries a value from the previous iteration

Which loops are divided
-----------------------

Only a statement at the top level of the decorated function is considered:

* a ``for`` loop, or
* a statement that is one comprehension with a single ``for``
  (``squares = [f(x) for x in xs]``).

A ``while`` loop, a loop inside a helper function that the decorated function
calls, and a loop nested under ``if``, ``with`` or ``try`` are not considered;
``explain`` reports ``no loop at the top level of the function to split``.
At most one loop per function is divided: the first that qualifies.

Every name the loop body uses must be one of three kinds.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Kind
     - Meaning
   * - private to each iteration
     - Assigned in the body before it is read there, and not used after the
       loop.
   * - read-only
     - Defined before the loop and only read in it.
   * - collected in order
     - Defined before the loop and only written in it, by ``acc.append(x)``
       (or any method call whose result is discarded), ``acc[k] = v``,
       ``acc.attr = v``, ``acc += v`` or ``acc = acc + v``.

Writes to a collected name are not performed by the workers. Each write is
recorded, and the records are replayed on the real object in the original
iteration order after all pieces have finished. The result is therefore the
one a sequential run produces, including the order of a list, the insertion
order of a dict and the rounding of a floating-point sum.

What prevents a split
~~~~~~~~~~~~~~~~~~~~~

Each line below is the reason ``explain`` printed for a function written to
trigger it.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - In the loop
     - Reported as
   * - A value carried between iterations
     - ``total is read before the iteration assigns it, so it carries a value
       from the previous iteration``
   * - ``break``
     - ``break stops the loop early, so later iterations depend on earlier
       ones``
   * - ``return``
     - ``the loop body returns``
   * - A variable assigned in the loop and used after it (this includes the
       loop variable)
     - ``last is assigned in the loop and used after it, which depends on
       which iteration ran last``
   * - A change to something the function did not create (a module-level
       list, array or object; a variable of an enclosing function)
     - ``RESULTS is not created in this function and the loop changes it; a
       change made in a worker would be lost. Create it inside the function
       and return it``
   * - The global ``random`` or ``numpy.random`` generator, **including
       seeding it** in each iteration
     - ``random.seed() uses a random generator shared by the whole process, so
       what it returns and what it leaves behind depend on the order of the
       iterations; create one per iteration instead, e.g. rng =
       np.random.default_rng(seed + i)``
   * - A function defined before the loop that reads a variable the loop, or
       the code after it, assigns
     - ``a function defined before the loop reads total, which is assigned
       again in or after the loop; it would go on seeing the old value``
   * - A write the loop's own ``try`` or ``with`` depends on:
       ``counts[k] += 1`` under ``except KeyError``, ``seen.remove(x)`` under
       ``contextlib.suppress``. (``out.append(x)`` and ``out[k] = v`` inside
       ``try`` or ``with`` are fine.)
     - ``counts is changed inside try or with by something that can raise,
       and whether it raises decides what the loop does next; a split would
       make the change after the loop``
   * - Assigning to an attribute of a function, class or module
       (``f.count = ...``)
     - ``f is not created in this function and the loop changes it; ...``
   * - ``for box.last in xs`` -- a loop target that is not a plain variable
     - ``the loop assigns each item to box.last, which is not a plain
       variable, so every iteration writes to the same place``

Some functions are not analysed at all, and ``explain`` says ``not analysed``
with the reason:

* a function containing a ``global`` or ``nonlocal`` statement, anywhere;
* a function wrapped by another decorator (``@cluster`` on top of
  ``@functools.wraps``-style wrapper): the split would be made from the inner
  function's source and skip the wrapper. Put ``@cluster`` directly on the
  function that has the loop;
* a method that uses ``super()`` or a class-private name (``self.__x``);
* a function whose file was edited after it was imported, so the source on
  disk is no longer the code that runs;
* a generator, a coroutine, a ``functools.partial``, a callable object.

A function whose loop is not divided still runs, as one unit. Locally, a
warning says that ``cores`` had no effect for that function.

.. note::

   Reusing a loop variable name later in the function counts as "used after
   the loop". Two consecutive loops that both use ``for v in ...`` leave the
   first one undivided for that reason. Give them different variable names.

Where the pieces run
--------------------

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Backend
     - What happens
   * - No ``cluster_host`` (local)
     - The loop is divided among ``cores`` worker processes on this machine.
       ``cores=-1`` means every CPU available to the process
       (``SLURM_CPUS_PER_TASK`` is honoured, then the CPU affinity mask). The
       code before and after the loop runs in the calling process.
   * - ``ssh``, ``slurm``
     - The items are cut into at most ``max_parallel_jobs`` contiguous pieces
       and each piece is submitted as one job with the resources the
       decorator names: ``@cluster(cores=4, memory="8GB")`` describes each
       job, not the total. The code before and after the loop runs as its own
       job, unless it only unpacks arguments or returns what was collected,
       in which case it runs in the caller. If one job fails, the jobs after
       it are cancelled and the error is raised.
   * - ``huggingface``
     - Same as ``ssh``/``slurm``: one HuggingFace job per piece. Not executed
       in this documentation build.

``cores=-1`` with ``cluster_type="slurm"`` raises ``ValueError``: a scheduler
has to be asked for a number.

Local behaviour in more detail
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* The workers are ordinary Python subprocesses that are kept between calls.
  They are not started with ``multiprocessing``, so a script without an
  ``if __name__ == "__main__":`` guard is not re-run inside each worker.
* The first call of a function divides its loop and measures, in the workers,
  what an iteration costs. A later call is divided only if that measurement
  says the whole loop would take at least 0.2 seconds; otherwise it runs in
  the calling process, and is timed again. With logging at ``INFO`` the
  message is, for example, ``tiny: its loop is too short to be worth dividing
  (about 0.001s in all); it runs in this process.``
* What the iterations print is shown after the loop has finished, in
  iteration order.
* A decorated function called from inside an iteration that is already
  running in a worker is not divided again.
* The workers do not outlive the process that started them. If it is killed
  -- ``SIGTERM``, ``SIGKILL``, a restarted notebook kernel -- each worker
  exits within about a second, in the middle of whatever iteration it was
  running: a file that iteration was writing is left as it is, and a
  process the iteration itself started is not stopped.
* The loop's iterable is turned into a list before it is divided, so a
  generator is consumed completely first.

On SLURM
~~~~~~~~

The function below was run against a single-node SLURM installation with
``max_parallel_jobs=4``. It returns the job ID each iteration saw.

.. code-block:: python

   # cluster-required: needs a SLURM login node
   from clustrix import cluster, configure

   configure(
       cluster_type="slurm",
       cluster_host="login.cluster.example.edu",
       username="researcher",
       remote_work_dir="/scratch/researcher/clustrix",
       max_parallel_jobs=4,
   )


   @cluster(cores=1, memory="500MB", time="00:05:00")
   def job_ids(n):
       import os

       seen = []
       for i in range(n):
           seen.append((i, os.environ.get("SLURM_JOB_ID")))
       return seen


   print(job_ids(8))

.. code-block:: text

   INFO clustrix.decorator: job_ids: running its loop as 4 job(s) of up to 2 iteration(s).
   [(0, '196'), (1, '196'), (2, '197'), (3, '197'), (4, '198'), (5, '198'), (6, '199'), (7, '199')]

There are no SLURM job arrays yet. Each piece is a separate ``sbatch``
submission, and Clustrix does not throttle beyond ``max_parallel_jobs``
(default 100); set it to what your site allows.

Stopping a call that is waiting for jobs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

On ``ssh`` and ``slurm``, interrupting the call cancels the jobs it started
and has not collected -- the pieces of a divided loop, or the one job of a
function that was not divided:

* **Ctrl-C** (``KeyboardInterrupt``), in a terminal or a notebook.
* **SIGTERM** -- ``kill``, a batch system's time limit, a notebook server
  shutting down. While a call waits, clustrix turns the signal into an
  exception in the main thread, the jobs are cancelled, and the process
  then ends by SIGTERM as it would have (exit status 143 in a shell). If
  your program has installed its own SIGTERM handler, that handler is left
  in charge: the jobs are cancelled if it raises (``sys.exit`` does) and
  keep running if it returns. A call made from a thread other than the main
  one is not covered, and neither is ``async_submit=True``, which does not
  wait.

Not covered: **SIGKILL** (``kill -9``), a crash of the interpreter, and
loss of power or network cannot be caught. The jobs stay on the cluster;
cancel them with ``scancel``, or ``kill`` on an ssh host. A job that fails,
and one that outlasts ``job_wait_timeout``, is not cancelled either.

What is checked while the loop runs
-----------------------------------

Reading source code can show that a loop is unsafe to divide. It cannot show
that one is safe, because it cannot see what a called function does. These
checks on real values run behind the source analysis:

Aliasing
   Before any piece is sent, everything the body reads -- its values, the
   items, and a generator it loops over -- is searched for any object that
   can also be reached from what the loop collects into (``seen = out``
   followed by ``out.append(f(seen, x))``). The search follows containers,
   attributes, closures, classes and NumPy object arrays. It visits at most
   five million objects; beyond that the loop runs in order, because an
   unfinished search has shown nothing.

Items produced as the loop goes
   A list, a dict, an array, a ``range`` and iterators that only walk those
   (``zip``, ``enumerate``, ``reversed``, ``itertools.product``, ...) are
   listed before the loop is divided. A generator, ``map``, ``filter``, an
   open file or any other iterator is **not**: it may read what the loop
   changes, raise half way, print, or never end, and listing it first would
   move all of that to before the first iteration. Such a loop runs in order
   and the message says ``it runs over a generator, which produces its items
   as the loop goes; wrap it in list(...) if they can all be produced before
   the first iteration``. So ``for row in df.iterrows()`` and ``for p in
   Path(d).glob("*.csv")`` are divided only as ``list(df.iterrows())`` and
   ``sorted(Path(d).glob("*.csv"))``. The same happens when the body can
   reach the iterator it runs over (``next(it)`` inside ``for x in it``).

Mutation
   Each piece fingerprints the values it reads and the items it was given,
   before and after it runs. A difference means an iteration changed something
   the others share. Module-level data and enclosing-function variables that
   the loop body *names* are included; what a called function reads from its
   own module is not.

Hidden randomness
   The state of the global ``random`` and NumPy generators is compared around
   each piece, which catches a helper that draws from them.

Identity
   An object that existed before the loop is not copied into what the loop
   collects: ``out.append(row)`` collects ``row`` itself, so a later
   ``row.append(1)`` shows in ``out`` exactly as in a sequential run. This
   holds for objects found through lists, tuples, dict values and attributes;
   it does not hold for the elements of a set or the keys of a dict.
   Module-level objects the body names (a ``MISSING = object()`` sentinel)
   are kept as themselves too. A value
   that cannot be copied between processes (a generator, a lock), and a
   function created in the loop that reads the loop's variables and is kept
   afterwards, both abandon the split.

When a check fails, the split is abandoned and the original loop runs once,
in order. The code before the loop is not run a second time. A warning says
why, and says so when the failed attempt had already run the loop's side
effects once:

.. code-block:: python

   from clustrix import cluster, configure

   configure(cluster_type="local")


   @cluster(cores=4)
   def mutates_shared(rows):
       out = []
       for row in rows:
           row.append(1)
           out.append(len(row))
       return out


   print(mutates_shared([[0], [1], [2], [3]]))

.. code-block:: text

   WARNING clustrix.decorator: mutates_shared: not split -- the loop changed the items being looped over, which its iterations share. That was found after the loop had been run in pieces, so anything it does besides computing (writing a file, calling a service) happened in that attempt as well. The loop ran in order instead, so the result is the one a direct call gives. It will not be divided again in this session; pass parallel=False to say so in the code.
   [2, 2, 2, 2]

.. _parallel-loops-unseen:

What no check can see
---------------------

The checks cover the values the loop was handed. They do not cover state that
lives anywhere else:

* a module-level cache or counter that a called function updates,
* a library's internal state,
* files and databases,
* a GPU's random state,
* process-wide settings made before the loop -- ``decimal.getcontext().prec``,
  the working directory, an environment variable set inside the function --
  which a worker process does not have,
* a module attribute assigned at run time (``settings.SCALE = 10`` after
  ``import settings``): a worker imports the module afresh and sees the value
  in the file. Pass such values as arguments. Local workers are kept between
  calls, so they also keep the version of an imported module they first
  loaded; a module edited and reloaded in the notebook is not reloaded there,
* the iteration order of a set of strings (or of anything else hashed by
  content or address): it depends on the process, as it does between two
  runs of Python. ``list(vocabulary)`` inside the loop and after it can
  disagree. Sort such a set. Setting ``PYTHONHASHSEED`` is not enough: the
  workers inherit it, but a set rebuilt from a pickle can still iterate in
  a different order from the one it was copied from,
* ``sys.argv`` and other state of the interpreter that started the program,
* an object whose pickle leaves its state out (a custom ``__getstate__``):
  pickling is how a change is noticed.

Such side effects still happen once per iteration, but in an unspecified
order across pieces, and in a different process from the caller. In a local
run, a helper that stored each item in a module-level dict left that dict
empty in the calling process, because the writes happened in the workers.

If the iterations of your loop communicate through anything other than the
names the table above describes, turn the feature off for that function.

When an iteration raises
~~~~~~~~~~~~~~~~~~~~~~~~

The exception that reaches you is the one from the earliest failing
iteration, as in a sequential run. Locally, the loop is then run again in
order in the calling process in two cases: when it collects into something
the caller passed in, so that object holds what was collected before the
error, as it would after a direct call; and when a write the split postpones
could itself have raised (``remaining.remove(x)``), so that whichever error
comes first in order is the one raised. On a cluster the caller's objects
are never touched, with or without a split.

Two things differ from a direct call and are not corrected. Iterations after
the failing one may already have run, so what they printed or wrote appears
although a direct call would have stopped. And an iteration that can only
succeed after an earlier one changed some hidden state raises in a worker
where a direct call would have returned; the exception carries a note saying
the loop was divided, and ``parallel=False`` is the fix.

GPUs
----

``@cluster(gpus=N)`` says that each job needs ``N`` GPUs. For a divided loop
that means each piece.

.. code-block:: python

   from clustrix import cluster, configure

   configure(cluster_type="slurm", cluster_host="login.example.edu",
             username="researcher", default_partition="gpu",
             max_parallel_jobs=8)


   @cluster(gpus=1, cores=4, memory="16GB", time="04:00:00")
   def train_all(settings):
       scores = {}
       for name, setting in settings.items():
           scores[name] = train_and_score(setting)   # one job, one GPU, each
       return scores

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Backend
     - What ``gpus=N`` does
   * - ``slurm``
     - Adds ``#SBATCH --gres=gpu:N`` to every job. The scheduler chooses the
       devices, sets ``CUDA_VISIBLE_DEVICES`` in the job and holds a job
       back while the devices are busy. A request the partition cannot meet
       is refused by ``sbatch``, and that message is what you see.
   * - ``ssh``
     - There is no scheduler, so every call first asks the host which GPUs
       it has (once per Python session) and raises if there are fewer than
       ``N``. When a loop is divided, each piece is given its own devices
       through ``CUDA_VISIBLE_DEVICES``, and there are no more pieces than
       ``GPUs // N``. A function whose loop is not divided is checked and
       then left alone: it sees every GPU the host shows it.
   * - local
     - The same, with worker processes in place of jobs: at most
       ``min(cores, GPUs // N)`` workers, each started seeing only its own
       devices.
   * - ``huggingface``
     - No effect, with a warning. The hardware is the flavor: pass
       ``hf_flavor``.

If the host or this machine has fewer GPUs than one job needs, the call
raises ``ValueError`` (``gpus=1 was requested and 0 GPU(s) were found on
...``) instead of running without one. That holds on every local and
``ssh`` route -- a function with no loop, ``cores=1``, ``parallel=False``
and ``async_submit=True`` included -- and nothing is submitted.

The GPUs counted are the ones the calling process may use. If
``CUDA_VISIBLE_DEVICES`` is set -- by a scheduler, a container or you --
the devices handed to the pieces are taken from *that* list, in its order
and its spelling (indices or ``GPU-...`` UUIDs): a process restricted to
device ``3`` gives its workers device ``3``, never ``0``. An entry is used
only if ``nvidia-smi`` lists such a device, so a variable left in a shell
profile on a machine without a GPU does not count as one. On an ``ssh``
host the variable is read from the environment a job would start in.

.. warning::

   How far this has been run. The SLURM path was run against a real scheduler
   configured with two resources of type ``gpu``: the request is accepted,
   two pieces run at a time, and pieces that overlap are given different
   devices (``tests/real_world/test_gpu_requests_real.py``). Those resources
   were placeholders, not graphics cards. **No computation has been run on a
   real GPU through any of these paths**, and the counting of GPUs on an
   ``ssh`` host and on a local machine has not been run on a machine that has
   one. What was run there is the refusal when there is none, and the rules
   for ``CUDA_VISIBLE_DEVICES`` against the text ``nvidia-smi`` prints.
   Check ``CUDA_VISIBLE_DEVICES`` in your first job.

Random state on a GPU is outside what the run-time checks can see; seed it
per iteration, as with any generator.

Turning it off
--------------

.. code-block:: python

   from clustrix import cluster, configure

   configure(cluster_type="local")


   @cluster(parallel=False)
   def one_unit(values):
       out = []
       for value in values:
           out.append(value * 2)
       return out


   print(one_unit([1, 2, 3]))

``@cluster(parallel=False)`` applies to one function.
``configure(auto_parallel=False)`` applies to every function that does not
say otherwise. The default is on.

Not available
-------------

* SLURM job arrays.
* Dividing work across GPUs. ``auto_gpu_parallel`` is accepted, warns, and
  does nothing, and no GPU path has been verified on real hardware.
* The keyword protocol of earlier releases (a function accepting
  ``_chunk_range_<var>`` or ``_parallel_<var>`` and returning a list of
  per-chunk results) and the module ``clustrix.loop_analysis``. Both were
  removed; a function written for that protocol now receives no such
  keywords.

See also
--------

* :doc:`api/decorator` for the decorator's parameters.
* :doc:`limitations` for limits unrelated to loops.
