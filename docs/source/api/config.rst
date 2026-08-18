Configuration API
=================

Clustrix provides flexible configuration management for cluster settings, authentication, and execution preferences.

.. automodule:: clustrix.config
   :members:
   :undoc-members:
   :show-inheritance:

Configuration Methods
---------------------

Programmatic Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import clustrix
   
   clustrix.configure(
       cluster_type='slurm',
       cluster_host='cluster.example.com',
       username='myuser',
       key_file='~/.ssh/id_rsa',
       default_cores=8,
       default_memory='16GB'
   )

Configuration File
~~~~~~~~~~~~~~~~~~

Create a ``clustrix.yml`` file:

.. code-block:: yaml

   cluster_type: slurm
   cluster_host: cluster.example.com
   username: myuser
   key_file: ~/.ssh/id_rsa
   
   default_cores: 8
   default_memory: 16GB
   default_time: "02:00:00"
   default_partition: gpu
   
   remote_work_dir: /scratch/myuser/clustrix
   conda_env_name: myproject
   
   auto_parallel: true
   max_parallel_jobs: 50
   cleanup_on_success: true
   
   module_loads:
     - python/3.9
     - cuda/11.2
   
   environment_variables:
     CUDA_VISIBLE_DEVICES: "0,1"

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

Clustrix reads two environment variables. Individual settings are *not*
configurable this way -- there is no ``CLUSTRIX_CLUSTER_TYPE`` or
``CLUSTRIX_CLUSTER_HOST``; use a configuration file or ``configure()``.

``CLUSTRIX_CONFIG_DIR``
   Overrides the directory clustrix reads and writes user configuration in,
   which defaults to ``~/.clustrix``. This matters in containers and CI images
   where ``$HOME`` is not writable or not persistent, on machines shared by
   several projects, and in tests -- without it, the notebook widget's "Save"
   button writes into the developer's own ``~/.clustrix`` during a test run.

``CLUSTRIX_AUTO_WIDGET``
   Set to ``1`` to display the configuration widget on ``import clustrix``.
   Off by default; see :doc:`notebook_magic`.

.. code-block:: bash

   export CLUSTRIX_CONFIG_DIR=/workspace/.clustrix

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

Authentication
~~~~~~~~~~~~~~

- ``username``: SSH username
- ``password``: SSH password (not recommended)
- ``key_file``: Path to SSH private key file

Cluster Settings
~~~~~~~~~~~~~~~~

- ``cluster_type``: Type of cluster (``local``, ``ssh``, ``slurm``, ``pbs``, ``sge``, ``kubernetes``, ``huggingface``)
- ``cluster_host``: Hostname of cluster head node. Not used by ``huggingface``,
  which submits over an HTTP API and has no host.
- ``cluster_port``: SSH port (default: 22)
- ``ssh_connect_timeout``: Seconds paramiko waits to establish a connection
  (default: 30). The OS default is minutes, which turns an unreachable host
  into a hang rather than an error.

Paths
~~~~~

- ``remote_work_dir``: Working directory on the cluster. Defaults to
  ``~/.clustrix/jobs``. It must be on a filesystem the compute node can see:
  on SLURM, PBS and SGE each node has its own ``/tmp``, so an environment built
  on the login node is simply absent at run time and the job dies with exit 127
  before writing any diagnostics. A home directory or a shared scratch path
  both work; ``/tmp`` does not.
- ``local_work_dir``: Local working directory (default: current directory)
- ``local_cache_dir``: Local cache directory (default: ``~/.clustrix/cache``)
- ``conda_env_name``: Conda environment to activate on the cluster
- ``venv_setup_timeout``: Seconds allowed for remote virtualenv creation
  (default: 300)

HuggingFace Jobs
~~~~~~~~~~~~~~~~

Used when ``cluster_type='huggingface'``:

- ``hf_token``: HuggingFace token. Required, and must be set explicitly --
- ``hf_namespace``: Account the job is billed to. Personal accounts are often
  not on a plan that can run jobs, so this is usually an organization. Falls
  back to ``hf_username``.
- ``hf_flavor``: Hardware tier (default: ``cpu-basic``)
- ``hf_image``: Container image. Defaults to ``python:<your minor version>-slim``,
  because dill payloads carry CPython bytecode and are not portable across
  minor versions. Overriding this with a mismatched Python is the most likely
  way to get an "unknown opcode" failure.
- ``hf_job_timeout``: Job timeout passed to the HF API (default: ``30m``)
- ``hf_allow_gpu_flavors``: Must be ``True`` before any flavor whose name does
  not begin with ``cpu-`` is accepted. GPU flavors bill by the second.

Resource Defaults
~~~~~~~~~~~~~~~~~

- ``default_cores``: Default number of CPU cores
- ``default_memory``: Default memory allocation
- ``default_time``: Default time limit
- ``default_partition``: Default partition/queue

Execution Preferences
~~~~~~~~~~~~~~~~~~~~~

- ``auto_parallel``: Enable automatic loop parallelization
- ``max_parallel_jobs``: Maximum number of parallel jobs
- ``prefer_local_parallel``: Prefer local over remote parallel execution
- ``cleanup_on_success``: Clean up remote files after successful execution