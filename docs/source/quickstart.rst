.. _quickstart:

Quickstart
==========

Five minutes, from an installed package to a function running on a cluster.
Blocks whose first line is ``# cluster-required`` need a machine you supply;
the others run anywhere and are executed by
``scripts/check_docs_examples.py``. The :doc:`tutorials <tutorials/index>`
cover each step in depth, with real output.

Install
-------

.. code-block:: bash

   pip install clustrix              # Python 3.10 or newer
   pip install "clustrix[widget]"    # plus the Jupyter configuration widget

.. _quickstart-first-result:

Step 1: decorate a function
---------------------------

.. code-block:: python

    from clustrix import cluster, configure

    configure(cluster_type="local", cluster_host=None)

    @cluster
    def mean_root(n):
        import math

        total = 0.0
        for i in range(n):
            total += math.sqrt(i)
        return total / n

    print(mean_root(100_000))

With no ``cluster_host`` configured, the function runs on this machine. It is
called, and returns, exactly as before.

.. _quickstart-parallel:

Step 2: let a loop use your CPUs
--------------------------------

If the function has a loop whose iterations do not depend on one another,
``cores`` is the number of worker processes the loop is divided among.

.. code-block:: python

    import os

    import clustrix
    from clustrix import cluster

    @cluster(cores=4)
    def simulate(seeds, steps):
        import os
        import random

        finals, workers = {}, set()
        for seed in seeds:
            rng = random.Random(seed)
            position = 0.0
            for _ in range(steps):
                position += rng.gauss(0.0, 1.0)
            finals[seed] = position
            workers.add(os.getpid())
        return finals, workers

    finals, workers = simulate(list(range(8)), 20_000)
    print(len(finals), "results from", len(workers), "process(es)")
    print(clustrix.explain(simulate))

``clustrix.explain`` says whether a loop will be divided and, if not, which
line prevents it. The result is the same as an undivided run, in the same
order. Clustrix checks what it can, before and while the loop runs, and falls
back to running the loop in order when a check fails; it cannot see side
effects outside the values the loop was handed (files, module-level caches).
:doc:`tutorials/02_parallel_loops` goes through all of this.

.. _quickstart-ssh:

Step 3: run it on a remote machine
----------------------------------

Only the configuration changes.

.. code-block:: python

    # cluster-required: needs a host you can ssh to
    import os

    from clustrix import configure

    configure(
        cluster_type="ssh",
        cluster_host="gpu-box.example.edu",
        username="your-username",
        key_file=os.path.expanduser("~/.ssh/id_ed25519"),
        remote_work_dir="~/clustrix",
    )

    print(mean_root(100_000))      # now runs over there

The host must be in your ``~/.ssh/known_hosts`` (it is, if you have logged in
to it with ``ssh`` from this computer); otherwise the first call is refused
with the host's fingerprint and instructions. The first job also builds a
Python environment on the remote machine, which is reused afterwards.
:doc:`tutorials/03_ssh_cluster` shows each of these.

.. _quickstart-slurm:

Step 4: or submit to SLURM
--------------------------

.. code-block:: python

    # cluster-required: needs a SLURM login node
    import os

    from clustrix import cluster, configure

    configure(
        cluster_type="slurm",
        cluster_host="login.cluster.example.edu",
        username="your-username",
        key_file=os.path.expanduser("~/.ssh/id_ed25519"),
        remote_work_dir="/scratch/your-username/clustrix",
        default_partition="short",
        max_parallel_jobs=20,
    )

    @cluster(cores=1, memory="2GB", time="00:30:00")
    def simulate(seeds, steps):
        ...

``cores``, ``memory`` and ``time`` describe one job. A loop that can be
divided becomes one job per chunk, at most ``max_parallel_jobs`` of them.
See :doc:`tutorials/04_slurm`.

.. _quickstart-filesystem:

Step 5: input files
-------------------

On ``ssh`` and ``slurm``, local files that a call names -- in its arguments,
or as string constants in the function -- are uploaded once and made readable
by the job at the same path. Paths computed at run time are not found; list
them with ``@cluster(data=[...])``. **Output files are not brought back**:
return the data, or write to a directory on the cluster and fetch it
yourself. See :doc:`tutorials/05_data`.

.. _quickstart-hf:

No cluster? Hugging Face Jobs
-----------------------------

``configure(cluster_type="huggingface")`` runs the function in a rented
container. It needs a Hugging Face token and is billed per second; see
:doc:`tutorials/07_huggingface_jobs`.

.. _quickstart-config-file:

Keeping the configuration
-------------------------

.. code-block:: python

    from clustrix.config import configure, get_config, load_config, save_config

    configure(cluster_type="local", cluster_host=None, default_cores=2)
    save_config("my-cluster.yml")       # passwords and tokens are left out

    # ... in another session ...
    load_config("my-cluster.yml")
    print(get_config().default_cores)

A ``config.yml`` in ``~/.clustrix/`` is loaded automatically at import. In a
notebook, ``%load_ext clustrix`` followed by a ``%%remote`` cell opens a
widget that edits, tests and saves named profiles
(:doc:`tutorials/06_notebook_widget`). :doc:`configuration` lists every
setting.

Which backend?
--------------

+----------------------------------------+---------------------------+
| Situation                              | ``cluster_type``          |
+========================================+===========================+
| Writing and debugging; one machine     | ``local``                 |
+----------------------------------------+---------------------------+
| A cluster with a SLURM scheduler       | ``slurm``                 |
+----------------------------------------+---------------------------+
| One lab machine or rented server       | ``ssh``                   |
+----------------------------------------+---------------------------+
| No machine of your own                 | ``huggingface``           |
+----------------------------------------+---------------------------+

Those four are the only values accepted. ``pbs``, ``sge``, ``kubernetes`` and
the cloud VM providers are not supported, and naming one raises a
``ValueError`` that points at its tracking issue; see :ref:`removed-backends`.

Where to go next
----------------

- :doc:`tutorials/index` -- the seven tutorials, in order.
- :doc:`introduction` -- what Clustrix is for, and when to use something else.
- :doc:`configuration` -- every setting and where it can be set.
- :doc:`troubleshooting` -- when something goes wrong.
