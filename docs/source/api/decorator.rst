The ``@cluster`` decorator
==========================

.. autofunction:: clustrix.cluster

Parameters
----------

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Parameter
     - Meaning
   * - ``cores``
     - CPUs per job. Locally, the number of worker processes a loop is
       divided among; ``-1`` means every CPU available to the process.
       ``-1`` is refused for a SLURM job. Default: ``default_cores``.
   * - ``memory``
     - Memory per job, for example ``"8GB"``. Recorded but not enforced by
       the local backend. Default: ``default_memory``.
   * - ``time``
     - Wall-time limit per job, for example ``"01:00:00"``. Default:
       ``default_time``.
   * - ``partition``
     - SLURM partition. Default: ``default_partition``.
   * - ``parallel``
     - ``False`` runs the function as one unit; ``True`` or unset divides a
       loop when one qualifies. Default: ``auto_parallel`` (on). See
       :doc:`../parallel_loops`.
   * - ``environment``
     - Name of a conda environment that already exists on the cluster; the
       function runs in it. Default: ``conda_env_name``.
   * - ``async_submit``
     - ``True`` returns an ``AsyncJobResult`` at once; call ``.get_result()``
       on it. Loop division and automatic input staging do not apply to an
       asynchronous call.
   * - ``data``
     - A list of local paths to stage for calls of this function, in addition
       to the ones found automatically. See :doc:`../input_files`.
   * - ``no_data``
     - A list of local paths that must not be staged.
   * - ``hf_flavor``, ``hf_timeout``
     - Per-function hardware flavor and time limit for HuggingFace Jobs.
   * - ``hf_namespace``, ``hf_token``, ``hf_username``, ``key_file``
     - Accepted without a warning, but **no backend reads them from the
       decorator**. Set them with :func:`clustrix.configure`.
   * - ``auto_gpu_parallel``
     - No effect. Accepted so that old call sites keep working; passing it
       logs a warning.

Any other keyword is ignored, with a warning that names it. In particular
``cluster_type`` is **not** a decorator keyword: the backend is chosen with
:func:`clustrix.configure`.

.. code-block:: python

   import logging

   from clustrix import cluster, configure

   logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
   configure(cluster_type="local")


   @cluster(cores=2, cluster_type="local")
   def double(x):
       return 2 * x


   print(double(21))

.. code-block:: text

   WARNING clustrix.decorator: @cluster received unrecognised option(s) cluster_type; they have no effect. Recognised extras: data, hf_flavor, hf_namespace, hf_timeout, hf_token, hf_username, key_file, no_data
   WARNING clustrix.decorator: @cluster(cores=2) has no effect here: double has no loop that can be split. Locally, cores is the number of worker processes a loop in the function is divided among; with no loop that can be divided, the function runs once, in this process. clustrix.explain(function) says why a loop was not divided.
   42

Where a call runs
-----------------

Decided on every call from the live configuration:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Configuration
     - The call runs
   * - ``cluster_type="huggingface"``
     - As a HuggingFace job.
   * - No ``cluster_host`` (whatever ``cluster_type`` says)
     - On this machine. A loop that qualifies is divided among ``cores``
       worker processes.
   * - ``cluster_host`` set, ``prefer_local_parallel=True``
     - On this machine, as above.
   * - ``cluster_host`` set, ``cluster_type="ssh"`` or ``"slurm"``
     - On the cluster.
   * - ``cluster_host`` set, ``cluster_type="local"``
     - On this machine through the same submit-and-collect path a remote
       job takes, as one unit of work.

Both spellings of the decorator work:

.. code-block:: python

   from clustrix import cluster, configure

   configure(cluster_type="local")


   @cluster
   def bare(x):
       return x + 1


   @cluster(memory="2GB")
   def with_options(x):
       return x + 2


   print(bare(1), with_options(1))

``clustrix.explain``
--------------------

.. autofunction:: clustrix.explain

See :doc:`../parallel_loops` for how to read its output.
