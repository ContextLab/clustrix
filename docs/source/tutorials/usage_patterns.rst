Laying Out Code That Uses ``@cluster``
======================================

Four habits that make decorated functions easy to move between your computer
and a cluster. The examples without a ``# cluster-required`` first line run
on any machine and are executed by ``scripts/check_docs_examples.py``.

A fact that shapes all of them: with no ``cluster_host`` configured,
``@cluster`` runs the function on this machine, so everything can be developed
and debugged before a cluster is involved.

Pattern 1: one function, one unit of work
-----------------------------------------

Write the function as you would for a laptop, with the expensive part as a
loop at the top level of the function. Take inputs as arguments and return
results; do not rely on globals changing or on files appearing next to the
script.

.. code-block:: python

    from clustrix import cluster, configure

    configure(cluster_type="local", cluster_host=None)

    @cluster(cores=4)
    def simulate(seeds, steps):
        import random

        finals = {}
        for seed in seeds:
            rng = random.Random(seed)       # one generator per iteration
            position = 0.0
            for _ in range(steps):
                position += rng.gauss(0.0, 1.0)
            finals[seed] = position
        return finals

    result = simulate([1, 2, 3, 4], 1000)
    assert sorted(result) == [1, 2, 3, 4]

The loop over ``seeds`` is divided among workers because its iterations share
nothing: each has its own generator, and ``finals`` is only written to.
``clustrix.explain(simulate)`` reports that decision.
:doc:`02_parallel_loops` covers which loops qualify.

Imports may be at the top of the file or inside the function; both work. The
function travels as code, together with the project modules it uses.

.. _repl-limitation:

Pattern 2: functions in a file, a notebook or the REPL
------------------------------------------------------

A decorated function can be defined in a module, in a notebook cell, or at
the interactive prompt. All three are serialized and run correctly, because
serialization works from the function's code object, not its source text.

What does need the source is loop division, which reads it to analyse the
loop. A function whose source cannot be found (one created with ``exec``, for
example) runs as one piece:

.. code-block:: python

    import clustrix

    namespace = {}
    exec("def add(a, b):\n    return a + b\n", namespace)
    add = clustrix.cluster(namespace["add"])
    assert add(2, 3) == 5

Pattern 3: configuration apart from functions
---------------------------------------------

Where a function runs is not part of the function. Call ``configure()`` once,
in the script or notebook that drives the analysis, and leave the decorated
functions free of host names. The same module then runs locally in a test and
on the cluster in production.

.. code-block:: python

    # cluster-required: needs a real SLURM login node
    from clustrix import cluster, configure

    configure(
        cluster_type="slurm",
        cluster_host="login.cluster.example.edu",
        username="researcher",
        remote_work_dir="/scratch/researcher/clustrix",
        default_partition="short",
    )

    @cluster(cores=1, memory="2GB", time="00:30:00")
    def simulate(seeds, steps):
        ...

``cluster_type`` is not accepted by the decorator; ``@cluster(cluster_type=...)``
warns and has no effect.

Pattern 4: return something that proves where it ran
----------------------------------------------------

While setting up a new backend, have the function return the host name and
process ID alongside its result. It removes all doubt about whether a call was
local or remote.

.. code-block:: python

    import os
    import socket

    from clustrix import cluster

    @cluster
    def where():
        import os
        import socket

        return socket.gethostname(), os.getpid()

    host, pid = where()
    assert (host, pid) == (socket.gethostname(), os.getpid())  # local: same process

If you were looking for Kubernetes, PBS, SGE or a cloud VM
----------------------------------------------------------

Clustrix has no backend for them; ``cluster_type`` accepts ``local``, ``ssh``,
``slurm`` and ``huggingface``, and anything else raises ``ValueError`` when it
is configured. :ref:`removed-backends` lists the tracking issues. In the
meantime: a VM you start yourself is reachable with ``cluster_type="ssh"``
(:doc:`03_ssh_cluster`), and rented GPUs are available through Hugging Face
Jobs (:doc:`07_huggingface_jobs`).
