Decorator API
=============

The ``@cluster`` decorator is the main interface for Clustrix, allowing you to easily execute functions on remote clusters or locally with parallelization.

.. automodule:: clustrix.decorator
   :members:
   :undoc-members:
   :show-inheritance:

Examples
--------

Basic Usage
~~~~~~~~~~~

.. code-block:: python

   from clustrix import cluster, configure

   # Explicit and self-contained: with no cluster configured (or, as here,
   # cluster_type="local" and no host), @cluster runs locally -- see "How
   # Execution Mode Is Chosen" below.
   configure(cluster_type="local", cluster_host=None)

   @cluster(cores=4, memory='8GB')
   def my_function(x, y):
       return x + y

   result = my_function(10, 20)

Resource Specification
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(
       cores=16,
       memory='32GB', 
       time='04:00:00',
       partition='gpu'
   )
   def gpu_computation():
       # Your GPU code here
       pass

Parallel Loop Execution (Remote)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``parallel=True`` submits to a *remote* backend, loop detection uses
``clustrix.utils.detect_loops`` -- a much simpler AST scan than the local
path below. Two conditions must BOTH hold, and most functions fail at least
one of them.

**First, the loop's range must be a literal.** ``detect_loops`` only
recognises a ``for`` loop written as ``range(<int literal>)``. Anything whose
bound is known only at run time is declined outright. It used to guess
``range(0, 10)`` in that case, which meant a loop over ``range(len(data))``
was chunked as ten iterations and the caller silently received a tenth of the
work; that fabrication was removed, and the answer is now ``None``. Verified
directly:

``detect_loops`` reads the function's source with ``inspect.getsource``, so
these have to live in a real file to be analysed at all:

.. code-block:: python

   # loopdemo.py
   def a(data):
       for item in data:
           pass

   def b(data):
       for i in range(len(data)):
           pass

   def c():
       for i in range(100):
           pass

.. code-block:: python

   from clustrix.utils import detect_loops
   import loopdemo

   print(detect_loops(loopdemo.a, ([1, 2, 3],), {}))   # None -- a value, not a range
   print(detect_loops(loopdemo.b, ([1, 2, 3],), {}))   # None -- len(data) is not a literal
   print(detect_loops(loopdemo.c, (), {})["range"])    # range(0, 100)

**Second, the function must be able to receive a chunk.** Clustrix splits the
range and passes each piece as the keyword arguments ``_chunk_range_<var>``
and ``_chunk_index``. A function that does not declare them (or ``**kwargs``)
cannot be handed one, so ``_create_work_chunks`` produces no chunks and the
call runs whole. This used to inject the argument anyway and fail with
``TypeError: ... got an unexpected keyword argument '_chunk_range_i'``; it
now declines and logs instead.

.. code-block:: python

   from clustrix.decorator import _accepts_chunk_kwargs

   def no_chunk_params():
       results = []
       for i in range(100):
           results.append(i)
       return results

   def chunk_aware(_chunk_range_i=None, _chunk_index=None):
       total = 0
       for i in range(100):
           total += i
       return total

   names = ["_chunk_range_i", "_chunk_index"]
   print(_accepts_chunk_kwargs(no_chunk_params, names))   # False -- runs whole
   print(_accepts_chunk_kwargs(chunk_aware, names))       # True  -- can be chunked

So a remote-parallelizable function needs a literal range *and* the chunk
parameters:

.. code-block:: python

   # cluster-required: needs a remote backend to actually distribute the work
   from clustrix import cluster

   @cluster(cores=8, parallel=True)
   def parallel_processing(_chunk_range_i=None, _chunk_index=None):
       # When chunked, _chunk_range_i is this worker's slice of range(100).
       # When not chunked, it is None and the whole range runs here.
       span = _chunk_range_i if _chunk_range_i is not None else range(100)
       return [i * i for i in span]

.. note::

   Results come back as a list of per-chunk results, so a parallelized run
   and an unparallelized run of the same function can return different
   shapes. See :doc:`../limitations` before relying on this.

How Execution Mode Is Chosen
-----------------------------

``clustrix.decorator._choose_execution_mode`` decides, on every call,
whether to run locally or submit to a remote backend. It falls back to
*local* execution whenever ``config.cluster_host`` is unset (SLURM, PBS,
SGE, SSH) and the cluster type is not Kubernetes-with-auto-provisioning or
one of the HTTP-API backends (currently HuggingFace Jobs). Concretely: if
you never call ``configure()`` with a real host, ``@cluster``-decorated
functions still run -- in the calling process, with no cluster involved --
and the exact same code starts submitting real remote jobs the moment
``configure()`` points at one. See :doc:`../tutorials/usage_patterns` for
worked examples of both modes side by side.

Local Parallelization
~~~~~~~~~~~~~~~~~~~~~

When ``parallel=True`` (or ``config.auto_parallel``) triggers *local*
execution, ``@cluster`` first asks ``clustrix.loop_analysis.find_parallelizable_loops``
whether the function has a chunkable loop, using a stricter analysis than
the remote path above: a ``for <var> in range(...)`` loop only qualifies if
its body reads no name other than ``<var>`` itself -- no accumulator
variable, no function call, nothing from an outer scope. In practice this
means the common ``results = []; for x in data: results.append(f(x))``
shape is **never** flagged parallelizable (``results`` counts as a
dependency), verified directly:

.. code-block:: python

   from clustrix.loop_analysis import find_parallelizable_loops

   def accumulator_style(data):
       results = []
       for item in data:
           results.append(item * 2)
       return results

   def trivial_range_loop(n):
       for i in range(n):
           x = i * 2  # reads only the loop variable -- no dependencies

   find_parallelizable_loops(accumulator_style, ([1, 2, 3],), {})  # [] -- not flagged
   find_parallelizable_loops(trivial_range_loop, (20,), {})        # one parallelizable loop

When a loop *is* flagged, clustrix hands each chunk to the function as a
keyword argument named ``_parallel_<var>`` (for a loop variable ``i``, that
is ``_parallel_i``) and calls the function once per chunk -- the function's
own loop body is not rewritten or re-executed; only the keyword is added to
whatever ``args``/``kwargs`` the call already had. A function that wants to
actually make use of chunked local parallelization therefore has to be
written with two parts: a trivial, dependency-free loop for
``find_parallelizable_loops`` to detect, and a branch on ``_parallel_<var>``
that does the real work. Verified end-to-end (real ``@cluster`` call,
``cluster_type="local"``, no cluster involved):

.. code-block:: python

   from clustrix import cluster, configure

   configure(cluster_type="local")

   @cluster(cores=4, parallel=True)
   def chunked_doubler(n, _parallel_i=None, **kwargs):
       # Trivial loop purely so find_parallelizable_loops flags this
       # function -- its body is never actually used for the result.
       for i in range(n):
           pass
       if _parallel_i is not None:
           return [x * 2 for x in _parallel_i]  # this chunk's share of work
       return [x * 2 for x in range(n)]          # no chunk: do it all here

   result = chunked_doubler(20)  # -> [0, 2, 4, ..., 38], computed across chunks

If the callee's signature doesn't accept ``_parallel_<var>`` (and doesn't
accept ``**kwargs``), clustrix does not silently run it sequentially
without telling you: it logs ``"Not parallelizing <name> locally: it takes
no '_parallel_<var>' parameter."`` at ``INFO`` level (see
``clustrix/decorator.py``'s ``_create_local_work_chunks``) and then falls
back to calling the function once, normally -- still a correct result, just
without local parallelization. Given how narrow the detection criterion is,
in practice this decline path -- or simply "no loop detected at all" -- is
what most real functions will hit locally; :doc:`local_executor` and its
``LocalExecutor.execute_loop_parallel`` are the more direct way to get
guaranteed local parallel execution over an arbitrary loop.