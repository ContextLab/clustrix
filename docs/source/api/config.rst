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

Set configuration via environment variables:

.. code-block:: bash

   export CLUSTRIX_CLUSTER_TYPE=slurm
   export CLUSTRIX_CLUSTER_HOST=cluster.example.com
   export CLUSTRIX_USERNAME=myuser

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

Authentication
~~~~~~~~~~~~~~

- ``username``: SSH username
- ``password``: SSH password (not recommended)
- ``key_file``: Path to SSH private key file

Cluster Settings
~~~~~~~~~~~~~~~~

- ``cluster_type``: Type of cluster (slurm, pbs, sge, kubernetes, ssh)
- ``cluster_host``: Hostname of cluster head node
- ``cluster_port``: SSH port (default: 22)

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