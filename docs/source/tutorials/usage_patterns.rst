Realistic Usage Patterns
========================

This page shows how to structure code that uses the ``@cluster`` decorator,
based on patterns that came out of Clustrix's own development scripts. Every
runnable example on this page has been executed against the real package (no
mocks) as part of this documentation's own test suite -- see
``scripts/check_docs_examples.py``.

A key fact that shapes every pattern here: **if you don't configure a
remote cluster, ``@cluster`` still runs your function -- just locally, in the
calling process.** ``clustrix.decorator._choose_execution_mode`` falls back to
local execution whenever ``config.cluster_host`` is unset (SLURM/PBS/SGE/SSH)
and the cluster type isn't Kubernetes-with-auto-provisioning or one of the
HTTP-API backends (currently HuggingFace Jobs). That means every example
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

Pattern 4: Kubernetes with Auto-Provisioning
-----------------------------------------------

A common mistake is to pass ``provider=`` to ``@cluster(...)`` expecting it
to select the Kubernetes provisioner -- it doesn't; that keyword is for the
hostful cloud VM backends (Lambda Cloud, AWS, Azure, GCP). The Kubernetes
provider is a separate setting, ``k8s_provider``, and it has to be set via
``configure()`` (default: ``"aws"``):

.. danger::

   Calling a function under ``auto_provision_k8s=True`` **creates real cloud
   infrastructure and bills you for it**. ``k8s_provider`` defaults to
   ``"aws"``, so omitting it -- or calling this function before
   ``configure()`` has run -- goes straight to AWS EKS and starts creating a
   VPC. A reviewer copy-pasting this example with only
   ``configure(cluster_type="local")`` in effect got as far as
   ``CreateVpc`` -> ``VpcLimitExceeded`` against a real account.

   Set ``k8s_provider="local"`` (kind/minikube, no cloud account involved)
   unless you have deliberately decided to spend money. The cloud
   provisioning paths are **unverified**: no clustrix job has been shown to
   run end to end on any of them.

.. code-block:: python

    # cluster-required: PROVISIONS REAL INFRASTRUCTURE. Do not run casually.
    from clustrix import configure, cluster

    configure(
        cluster_type="kubernetes",
        auto_provision_k8s=True,
        k8s_provider="local",   # NOT set via @cluster(provider=...)
        k8s_node_count=2,
    )

    # `platform` and `auto_provision` are NOT recognised @cluster keywords --
    # they are accepted and ignored. Only `cores` and `memory` take effect
    # per call here. They are shown because they appear in older examples.
    @cluster(cores=1, memory="512Mi")
    def analyze_data(size, multiplier=1):
        import math
        import socket

        total = sum(math.sqrt(i * multiplier) for i in range(min(size, 1000)))
        return {
            "analysis_result": total,
            "execution_environment": {"hostname": socket.gethostname()},
        }

    result = analyze_data(1000, 2)
    print(f"Result: {result['analysis_result']}")
    print(f"Executed on: {result['execution_environment']['hostname']}")

See :doc:`kubernetes_tutorial` (the "Auto-Provisioning a Cluster" section)
for the full picture, including which of the five supported cloud providers
are unverified and which environment variables each one needs.

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
6. **Kubernetes specifically**: ``k8s_provider`` (via ``configure()``) picks
   the auto-provisioning backend; ``@cluster(provider=...)`` does not.
