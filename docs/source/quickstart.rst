.. _quickstart:

Quickstart
==========

Five minutes, from nothing to a real result. Every Python block on this page
is complete and self-contained -- copy it into a file and run it.

Blocks whose first line is ``# cluster-required`` are the only ones that need
infrastructure you have to supply. Everything else runs on the machine you are
reading this on.

Install
-------

.. code-block:: bash

   pip install clustrix

Clustrix requires Python 3.10 or newer. See :doc:`installation` for the
optional extras (Jupyter widget, docs).

.. _quickstart-first-result:

Step 1: your first result, with no cluster at all
-------------------------------------------------

``cluster_type="local"`` executes the decorated function in the calling
process. It exists so that you can write and debug the code *before* you have
credentials for anything, and so that the same script keeps working when you
do not.

.. code-block:: python

    from clustrix import cluster, configure

    configure(cluster_type="local")

    @cluster(cores=4, memory="8GB", time="00:30:00")
    def estimate_pi(n_points: int, seed: int = 0) -> float:
        import random

        rng = random.Random(seed)
        inside = 0
        for _ in range(n_points):
            x, y = rng.random(), rng.random()
            if x * x + y * y <= 1.0:
                inside += 1
        return 4.0 * inside / n_points

    print(f"pi ~= {estimate_pi(200_000):.4f}")

Two things to notice:

- The ``cores``, ``memory`` and ``time`` arguments are accepted and ignored by
  the local backend. They are there so the *same* decorated function works
  unchanged against a scheduler.
- The ``import random`` is **inside** the function body. Do that
  consistently. The remote worker starts a fresh interpreter that has not run
  your module's top-level imports, so anything the body names must either be
  imported inside it or be something Clustrix can pickle by value along with
  the function.

.. _quickstart-sweep:

Step 2: a parameter sweep
-------------------------

The most common real use: the same function, many inputs, each call
independent. Call it in a loop. Against a scheduler each call becomes its own
job; locally each call just runs.

.. code-block:: python

    from clustrix import cluster, configure

    configure(cluster_type="local")

    @cluster(cores=2, memory="4GB", time="00:30:00")
    def score_threshold(threshold: float, n: int = 20_000) -> dict:
        import random

        rng = random.Random(int(threshold * 1000))
        hits = sum(1 for _ in range(n) if rng.random() < threshold)
        return {"threshold": threshold, "hit_rate": hits / n}

    results = [score_threshold(t) for t in (0.1, 0.25, 0.5, 0.75)]
    for r in results:
        print(f"threshold={r['threshold']:<5} hit_rate={r['hit_rate']:.3f}")

Keep the return value small. It comes back through a pickle file, so return
the summary you need, not the intermediate arrays you needed to compute it.

.. _quickstart-parallel:

Step 3: use all your cores on one machine
------------------------------------------

``parallel=True`` asks Clustrix to split a ``for`` loop across worker
processes. It is picky about which loops it will take, and the requirements
are easy to miss, so they are worth stating before the example:

1. **The loop's range must be a literal** -- ``range(50_000)``, not
   ``range(n)``. A bound only known at run time is declined, because guessing
   it would change the answer.
2. **The loop body must not carry a dependency between iterations.** An
   accumulator like ``total += math.sqrt(i)`` disqualifies the loop: every
   iteration depends on the last.
3. **Your function must accept the chunk.** Locally that means a keyword
   argument named ``_parallel_<loop variable>``. A function without it is run
   whole -- correctly, just not in parallel -- and Clustrix says so at
   ``INFO``.

Fail any of the three and the function still returns the right answer; it
simply is not distributed. :doc:`limitations` has the full contract.

.. code-block:: python

    # roots.py
    import math

    from clustrix import cluster, configure

    configure(cluster_type="local")

    @cluster(cores=4, parallel=True)
    def roots_of_slice(_parallel_i=None):
        """Square-root every index this worker was handed.

        ``_parallel_i`` is this worker's slice of the range. When it is None,
        this process is the only worker and does the whole thing.
        """
        import math

        indices = range(50_000) if _parallel_i is None else _parallel_i

        # This loop is what the analyser splits. It looks pointless and is
        # load-bearing: the analyser needs a literal `range()` whose body has
        # no dependency between iterations, and it is that loop -- not the
        # comprehension below -- that defines the range being divided up.
        # Delete it and parallelization silently stops while the printed
        # answer stays the same.
        for i in range(50_000):
            pass

        return [math.sqrt(j) for j in indices]

    if __name__ == "__main__":
        values = roots_of_slice()
        print(len(values), f"{sum(values):.2f}")

Run it as a file rather than pasting it into a REPL -- both because loop
detection reads the source, and because the worker processes re-import the
module, which is what the ``__main__`` guard is for::

    $ python roots.py
    50000 7453447.91

The work is divided into chunks, executed across worker processes, and
concatenated back into one list in the original order -- identical to what the
undecorated function returns. The chunk count depends on your machine
(``os.cpu_count() * 2``); on a 12-core machine it is 25.

.. important::

   Whether this runs locally at all depends on ``cluster_host``, **not** on
   ``cluster_type``. ``_choose_execution_mode`` returns ``"local"`` only when
   no host is configured. If a configuration file in ``~/.clustrix`` or the
   working directory sets ``cluster_host`` -- and Step 8 writes one -- this
   same code takes the *remote* path, which wants ``_chunk_range_i`` and
   ``_chunk_index`` instead of ``_parallel_i``, and will decline to
   parallelize while printing exactly the output above. :doc:`execution_model`
   has the full rule.

.. _quickstart-filesystem:

Step 4: find your data before you compute on it
------------------------------------------------

The filesystem helpers take a :class:`~clustrix.config.ClusterConfig` and run
against whichever machine that config points at. With
``cluster_type="local"`` they operate on the local disk, so the same code you
debug here works unchanged once the config names a remote host.

.. code-block:: python

    from pathlib import Path

    from clustrix import (
        ClusterConfig,
        cluster_count_files,
        cluster_du,
        cluster_exists,
        cluster_glob,
        cluster_ls,
        cluster_stat,
    )

    # Make a small dataset to look at.
    Path("data").mkdir(exist_ok=True)
    for i in range(3):
        Path(f"data/sample_{i}.csv").write_text("a,b\n1,2\n")

    cfg = ClusterConfig(cluster_type="local")

    print("contents:", sorted(cluster_ls("data", cfg)))
    print("csv files:", sorted(cluster_glob("*.csv", "data", cfg)))
    print("exists:", cluster_exists("data/sample_0.csv", cfg))
    print("count:", cluster_count_files("data", "*.csv", cfg))

    info = cluster_stat("data/sample_0.csv", cfg)
    print(f"sample_0.csv: {info.size} bytes, is_file={info.is_file}")

    usage = cluster_du("data", cfg)
    print(f"{usage.file_count} files, {usage.total_bytes} bytes")

Full reference: :doc:`tutorials/filesystem_tutorial`.

.. _quickstart-slurm:

Step 5: a real SLURM cluster
----------------------------

This is one of the three backends verified end to end against real
infrastructure. Nothing about the decorated function changes -- only the
configuration.

.. code-block:: python

    # cluster-required: needs an account on a real SLURM login node
    from clustrix import cluster, configure

    configure(
        cluster_type="slurm",
        cluster_host="login.hpc.example.edu",
        username="your-username",
        # Read the password from an environment variable instead of writing it
        # into a file. Both settings are required for this to take effect.
        use_env_password=True,
        password_env_var="CLUSTRIX_SLURM_PASSWORD",
        remote_work_dir="/scratch/your-username/clustrix",
        default_cores=4,
        default_memory="8GB",
        default_time="00:30:00",
        job_poll_interval=10,
    )

    @cluster(cores=8, memory="16GB", time="01:00:00", partition="standard")
    def where_did_this_run(n: int) -> dict:
        import socket

        return {"host": socket.gethostname(), "answer": sum(range(n))}

    print(where_did_this_run(1_000_000))

What happens when you call it: Clustrix opens an SSH connection, creates
``remote_work_dir``, uploads the pickled function and arguments, builds a
Python environment there from your local requirements, writes an ``sbatch``
script carrying the ``cores`` / ``memory`` / ``time`` / ``partition`` you
asked for, submits it, polls every ``job_poll_interval`` seconds, and then
downloads the result -- or the remote traceback, which it re-raises in your
process.

.. note::

   **The first call is slow.** Building the remote environment takes minutes.
   Later calls reuse it.

.. warning::

   Clustrix verifies the host's SSH key against your ``known_hosts`` files.
   An unrecognized key is **rejected** with a message telling you the exact
   ``ssh-keyscan`` command to run. If you understand the risk and want unknown
   keys trusted automatically, set ``ssh_host_key_policy="auto_add"`` --
   that opt-in exposes you to machine-in-the-middle attacks. See
   :doc:`ssh_setup`.

.. _quickstart-ssh:

Step 6: one big machine over SSH
---------------------------------

``cluster_type="ssh"`` runs the job directly on the host, with no scheduler
in between. This is the right backend for a lab GPU box or a rented instance
you already have a login on. It is verified end to end against a real GPU
host.

.. code-block:: python

    # cluster-required: needs SSH access to a real host
    from clustrix import cluster, configure

    configure(
        cluster_type="ssh",
        cluster_host="gpu-box.example.edu",
        username="your-username",
        key_file="~/.ssh/id_ed25519",
        remote_work_dir="~/.clustrix/work",
        job_poll_interval=5,
    )

    @cluster(cores=8, memory="32GB")
    def gpu_inventory() -> dict:
        import shutil
        import socket
        import subprocess

        report = {"host": socket.gethostname(), "gpus": []}
        if shutil.which("nvidia-smi"):
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                check=False,
            )
            report["gpus"] = [line for line in out.stdout.splitlines() if line]
        return report

    print(gpu_inventory())

.. _quickstart-hf:

Step 7: no cluster of your own
-------------------------------

``cluster_type="huggingface"`` submits to Hugging Face Jobs, which runs your
function in a container. There is no host to SSH into and no ``cluster_host``
to set. This backend is verified end to end against real Hugging Face Jobs
containers.

.. code-block:: python

    # cluster-required: needs a Hugging Face token with job-write scope
    import os

    from clustrix import cluster, configure

    configure(
        cluster_type="huggingface",
        hf_token=os.environ["HF_TOKEN"],
        hf_namespace="your-username-or-org",
        hf_flavor="cpu-basic",
        hf_job_timeout="15m",
    )

    @cluster(cores=2, memory="8GB")
    def container_report(n: int) -> dict:
        import platform

        return {"python": platform.python_version(), "answer": sum(range(n))}

    print(container_report(1_000_000))

.. warning::

   GPU flavors bill by the second. Clustrix refuses a GPU ``hf_flavor``
   unless you also set ``hf_allow_gpu_flavors=True``, so that paying for one
   is always a deliberate act.

.. _quickstart-config-file:

Step 8: stop repeating your configuration
------------------------------------------

Write the settings once and load them, instead of calling
:func:`~clustrix.config.configure` at the top of every script.

.. code-block:: python

    from clustrix.config import configure, get_config, load_config, save_config

    configure(
        cluster_type="ssh",
        cluster_host="gpu-box.example.edu",
        username="your-username",
        use_env_password=True,
        password_env_var="CLUSTRIX_GPU_PASSWORD",
        default_cores=8,
    )

    save_config("clustrix.yml")

    # ... in another session ...
    load_config("clustrix.yml")
    print(get_config().cluster_type, get_config().cluster_host)

Two things the saved file does for you:

- It is created with ``0600`` permissions (owner read/write only), set before
  any content is written.
- Secret-bearing fields such as ``password`` are **omitted** by default. What
  is written is ``password_env_var`` -- the *name* of the variable to read the
  credential from at run time. That environment-variable indirection is
  currently the only supported way to supply a credential without putting it
  on disk; there is no general "override any config field from the
  environment" mechanism.

Clustrix also loads a configuration automatically at import time if it finds
one, checking ``~/.clustrix/config.{yml,yaml,json}`` and then
``./clustrix.{yml,yaml,json}``. Set ``CLUSTRIX_CONFIG_DIR`` to move the first
of those. Full details in :doc:`configuration`.

The command line does the same thing:

.. code-block:: bash

   clustrix config --cluster-type slurm \
                   --cluster-host login.hpc.example.edu \
                   --username your-username \
                   --cores 8 --memory 16GB \
                   --config-file clustrix.yml

   clustrix config   # print the current settings

Which backend should I use?
---------------------------

+----------------------------------------+---------------------------+
| Situation                              | ``cluster_type``          |
+========================================+===========================+
| Writing and debugging the function     | ``local``                 |
+----------------------------------------+---------------------------+
| University / national HPC allocation   | ``slurm``                 |
+----------------------------------------+---------------------------+
| One lab machine or rented GPU box      | ``ssh``                   |
+----------------------------------------+---------------------------+
| No machine of your own                 | ``huggingface``           |
+----------------------------------------+---------------------------+

Those four are the only ``cluster_type`` values Clustrix accepts, and each one
has been proven to work against real infrastructure. ``pbs``, ``sge``,
``kubernetes`` and the cloud VM providers (AWS / GCP / Azure / Lambda Cloud)
are **not supported**, and naming one raises a ``ValueError`` that points at
its tracking issue. Each is planned for a future release; see
:ref:`removed-backends`.

Where to go next
----------------

- :doc:`introduction` -- what Clustrix is for, and when to use something else.
- :doc:`execution_model` -- what happens between your call and your result.
- :doc:`configuration` -- every setting and where it can be set.
- :doc:`ssh_setup` -- keys, host keys and passwordless access.
- :doc:`tutorials/usage_patterns` -- how to structure real code around
  ``@cluster``.
