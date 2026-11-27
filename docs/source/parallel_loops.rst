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
   * - A write to a global or nonlocal
     - ``COUNT is a global or nonlocal the loop assigns``
   * - The global ``random`` or ``numpy.random`` generator, unseeded
     - ``random.random() draws from a random generator shared by all
       iterations, so each draw depends on the ones before it; create one per
       iteration instead, e.g. rng = np.random.default_rng(seed + i)``

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

What is checked while the loop runs
-----------------------------------

Reading source code can show that a loop is unsafe to divide. It cannot show
that one is safe, because it cannot see what a called function does. Three
checks on real values run behind the source analysis:

Aliasing
   Before any piece is sent, the values the body reads are searched for a
   reference to an object the loop collects into (``seen = out`` followed by
   ``out.append(f(seen, x))``).

Mutation
   Each piece fingerprints the values it reads and the items it was given,
   before and after it runs. A difference means an iteration changed something
   the others share.

Hidden randomness
   The state of the global ``random`` and NumPy generators is compared around
   each piece, which catches a helper that draws from them.

When a check fails, the split is abandoned and the original loop runs once,
in order. The code before the loop is not run a second time. A warning says
why:

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

   WARNING clustrix.decorator: mutates_shared: not split -- the loop changed the items being looped over, which its iterations share
   [2, 2, 2, 2]

.. _parallel-loops-unseen:

What no check can see
---------------------

The checks cover the values the loop was handed. They do not cover state that
lives anywhere else:

* a module-level cache or counter that a called function updates,
* a library's internal state,
* files and databases,
* a GPU's random state.

Such side effects still happen once per iteration, but in an unspecified
order across pieces, and in a different process from the caller. In a local
run, a helper that stored each item in a module-level dict left that dict
empty in the calling process, because the writes happened in the workers.

If the iterations of your loop communicate through anything other than the
names the table above describes, turn the feature off for that function.

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
