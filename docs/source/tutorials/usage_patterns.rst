Realistic Usage Patterns
========================

This page shows how to structure code that uses the ``@cluster`` decorator,
based on patterns that came out of Clustrix's own development scripts. Every
runnable example on this page has been executed against the real package (no
mocks) as part of this documentation's own test suite -- see
``scripts/check_docs_examples.py``.

A key fact that shapes every pattern here: **if you don't configure a
remote cluster,** ``@cluster`` **still runs your function -- just locally, in
the calling process.** ``clustrix.decorator._choose_execution_mode`` falls back to
local execution whenever ``config.cluster_host`` is unset (SLURM/SSH) and the
cluster type isn't one of the HTTP-API backends (currently HuggingFace Jobs). That means every example
below runs as shown, without touching a real cluster, and the *same code*
starts submitting real remote jobs once you point ``configure()`` at one.

Pattern 1: A Structured Analysis Function
------------------------------------------

Write the decorated function like a normal Python function. Put all of its
imports *inside* the function body -- Clustrix serializes the function by
capturing its source, and the remote worker process doesn't share your local
interpreter's already-imported modules.

.. code-block:: python

    from clustrix import cluster

    @cluster(cores=1, memory="512Mi")
    def analyze_data(dataset_size: int, complexity: str = "medium"):
        """Analyze a dataset, sized for demonstration rather than realism."""
        # All imports inside the function body for serialization
        import platform
        import socket
        import math

        if complexity == "simple":
            result = dataset_size * 2
        elif complexity == "medium":
            result = sum(math.sqrt(i) for i in range(min(dataset_size, 1000)))
        else:
            result = sum(
                math.sin(i) * math.cos(i) for i in range(min(dataset_size, 5000))
            )

        return {
            "computation_result": result,
            "execution_info": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
            },
        }

    # Users call their functions completely normally.
    result = analyze_data(1000, complexity="medium")
    print(f"Result: {result['computation_result']}")
    print(f"Ran on: {result['execution_info']['hostname']}")

With no cluster configured, this prints a real number and your local
hostname -- there is no remote infrastructure involved yet. Point
``configure()`` at a verified backend (SLURM or SSH; see
:ref:`supported-cluster-types`) and the *hostname* in the result changes to
the remote worker's, with no change to ``analyze_data`` itself.

Pattern 2: A Standalone, Importable Module
--------------------------------------------

For anything beyond a single script, define functions in an importable
``.py`` module rather than inline. ``@cluster`` needs ``inspect.getsource()``
to see the function body, which only works reliably for functions defined in
a real file -- see :ref:`repl-limitation` below.

.. code-block:: python

    # analysis_module.py
    from clustrix import cluster

    @cluster(cores=1)
    def analyze_dataset_simple(size, complexity="medium"):
        """A function other modules can import and call like any other."""
        import math
        import platform
        import socket

        if complexity == "simple":
            result = size * 2
        else:
            result = sum(math.sqrt(i) for i in range(min(size, 1000)))

        return {
            "computation": {"result": result},
            "environment": {"hostname": socket.gethostname()},
            "success": True,
        }

.. code-block:: python

    # main.py
    from analysis_module import analyze_dataset_simple

    result = analyze_dataset_simple(500)
    assert result["success"]
    print(result["computation"]["result"])

.. _repl-limitation:

Functions Defined in the REPL
-----------------------------

Functions defined at the interactive ``python`` prompt **do** work with
``@cluster``: they serialize, run remotely, and return the right answer.
``inspect.getsource()`` cannot retrieve their source, but serialization does
not use source -- dill and cloudpickle work from the code object. Verified
directly:

.. code-block:: python

    from clustrix.utils import serialize_function, deserialize_function

    ns = {}
    exec("def add(a, b):\n    return a + b\n", ns)
    data = serialize_function(ns["add"], (2, 3), {})
    fn, args, kwargs = deserialize_function(data)
    print(fn(*args, **kwargs))  # 5

What is lost is only the *source-based* features layered on top of
serialization -- automatic loop-parallelization analysis, GPU-parallel
detection, and complexity analysis -- which parse the function's source text
with ``ast`` and so need a real file behind it. Those are skipped; execution
is unaffected.

Define functions in ``.py`` files or notebooks to get the full feature set.
Do not repeat the older claim that REPL functions "cannot be serialized": it
is false, and believing it is what justified a code path that returned a
fabricated string instead of the user's result. See :doc:`../limitations`.

Pattern 3: Configuring a Real Backend
----------------------------------------

Once a function works locally, switch it to a real cluster by calling
``configure()`` before the function runs -- no change to the decorated
function itself. This example needs a real SLURM cluster to execute (marked
accordingly in ``scripts/check_docs_examples.py``, which checks its syntax
and that every attribute it references actually exists, but does not run
it):

.. code-block:: python

    # cluster-required: needs a real SLURM login node
    from clustrix import cluster, configure

    configure(
        cluster_type="slurm",
        cluster_host="cluster.example.edu",
        username="researcher",
        remote_work_dir="/scratch/researcher/clustrix",
        default_cores=4,
        default_memory="8GB",
    )

    from analysis_module import analyze_dataset_simple

    result = analyze_dataset_simple(5000, complexity="heavy")

See :doc:`slurm_tutorial` and :doc:`../ssh_setup` for the two backends this
project has verified end to end, and :ref:`supported-cluster-types` for what
"verified" means for each backend.

Pattern 4: what to do when you wanted Kubernetes or a cloud VM
---------------------------------------------------------------

Clustrix supports neither. There is no ``cluster_type="kubernetes"``, no
``@cluster(provider="aws"|"gcp"|"azure"|"lambda")``, and no cost monitoring or
cloud pricing API to go with them. PBS and SGE are absent for the same reason.

Each is planned for a future release, and each has a tracking issue --
Kubernetes `#142`_, AWS `#143`_, GCP `#144`_, Azure `#145`_, Lambda Cloud
`#146`_, PBS `#140`_, SGE `#141`_. :ref:`removed-backends` has the full
table.

What to reach for instead:

* **A cloud GPU without owning hardware**: ``cluster_type="huggingface"``
  submits to HuggingFace Jobs, which runs your function in a container on
  rented GPUs. It is verified end to end. Mind the name: this is HuggingFace
  *Jobs*, and there is no HuggingFace *Spaces* backend.
* **A machine you brought up yourself**: bring up the VM through your
  provider's own console or CLI, then point ``cluster_type="ssh"`` at it.
  That path is verified end to end.
* **A batch allocation**: ``cluster_type="slurm"``, also verified.

.. _#140: https://github.com/ContextLab/clustrix/issues/140
.. _#141: https://github.com/ContextLab/clustrix/issues/141
.. _#142: https://github.com/ContextLab/clustrix/issues/142
.. _#143: https://github.com/ContextLab/clustrix/issues/143
.. _#144: https://github.com/ContextLab/clustrix/issues/144
.. _#145: https://github.com/ContextLab/clustrix/issues/145
.. _#146: https://github.com/ContextLab/clustrix/issues/146

Key Takeaways
-------------

1. **Structure**: define ``@cluster``-decorated functions in ``.py`` modules
   or notebooks. They still execute correctly from the interactive
   interpreter -- only the source-based analyses are skipped there.
2. **Imports**: put every import your function needs *inside* the function
   body.
3. **Configuration**: call ``configure()`` (or set up a config file) once,
   separately from the functions it affects.
4. **Calls**: decorated functions are called exactly like plain functions --
   with no cluster configured, they simply run locally.
5. **Results**: prefer returning a small dictionary with both the computed
   value and execution context (hostname, etc.) -- it makes it obvious
   whether a job actually ran remotely.
6. **Backends**: ``local``, ``ssh``, ``slurm`` and ``huggingface`` are the
   only ``cluster_type`` values Clustrix accepts. Anything else raises
   ``ValueError`` when you configure it -- from ``configure()``,
   ``load_config()`` or the ``ClusterConfig`` constructor -- rather than when
   you submit. In other words, the failure lands on the line where you named
   the backend, not after an SSH round trip to a host that was never going to
   be used. See :ref:`removed-backends`.
