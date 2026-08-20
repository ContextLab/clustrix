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

``ClusterConfig`` itself is also importable directly from the package root
(``from clustrix import ClusterConfig``), not just from ``clustrix.config``,
for building a config object explicitly instead of mutating the global one
through ``configure()``:

.. code-block:: python

   from clustrix import ClusterConfig

   config = ClusterConfig(cluster_type='local', default_cores=4)

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

There is no general ``CLUSTRIX_<FIELD>`` layer: no ``CLUSTRIX_CLUSTER_TYPE``
or ``CLUSTRIX_CLUSTER_HOST`` is read anywhere, so ordinary settings come from
a configuration file or ``configure()``. Two ``CLUSTRIX_`` variables are read
unconditionally, described below. Beyond those, three separate mechanisms
read further variables, and each is narrower than it looks:

**Connecting over SSH without a password or key file configured.** When an
SSH-family cluster (``ssh``, ``slurm``) has neither
``password`` nor ``key_file`` set, ``ClusterExecutor.setup_ssh_connection``
calls ``FlexibleCredentialManager.ensure_credential("ssh")``
(``clustrix/executor_connections.py``). That call first loads
``~/.clustrix/.env`` (directory overridable via ``CLUSTRIX_CONFIG_DIR``) with
``python-dotenv``, which -- as a side effect of loading the *whole file* --
puts every variable defined there into the process environment, not only the
SSH-related ones. It then reads, via ``clustrix/credential_manager.py``:

- ``SSH_HOST``, ``SSH_USERNAME``, ``SSH_PASSWORD``, ``SSH_PRIVATE_KEY_PATH``,
  ``SSH_PORT``
- ``HF_TOKEN``, ``HF_USERNAME``

Only the ``SSH_*`` variables can affect *this* connection. Everything else
your ``.env`` happens to define is put into the process environment as a side
effect of loading the whole file, and matters only if something else later
reads it. Cloud-provider and Kubernetes credentials in particular select no
execution backend, because Clustrix has none for them; see
:ref:`removed-backends`.

**The optional SSH-key-setup helper.** ``setup_ssh_keys_with_fallback()``
(exported from ``clustrix``; not called automatically by ``@cluster`` or
``ClusterExecutor``) reads, via ``clustrix/auth_fallbacks.py``:

- ``CLUSTRIX_PASSWORD_<HOST>``, ``CLUSTER_PASSWORD_<HOST>``,
  ``<HOST>_PASSWORD`` (``<HOST>`` is the cluster hostname, upper-cased with
  ``.`` replaced by ``_``)
- ``CLUSTRIX_DEFAULT_PASSWORD``
- ``CLUSTER_PASSWORD``

**Feature-specific variables**, read independently of the two mechanisms
above:

- ``HF_TOKEN`` -- the HuggingFace backend falls back to it when ``hf_token``
  is unset (``clustrix/hf_jobs.py``).
- ``HF_HOME`` -- if the token is still unset, the token written by
  ``hf auth login`` is read from ``$HF_HOME/token``, falling back to
  ``~/.cache/huggingface/token``.
- the variable *named by* ``password_env_var`` -- read for the SSH password
  when ``use_env_password`` is ``True``. The name is configurable, so there is
  no fixed variable to document here; see ``ClusterConfig.get_env_password``.

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

- ``cluster_type``: Type of cluster. The full, authoritative set is
  ``clustrix.config.SUPPORTED_CLUSTER_TYPES`` -- ``local``, ``ssh``,
  ``slurm``, ``huggingface``. Both the CLI and the notebook widget read this
  same tuple for their cluster-type choices, so it is never possible for one
  of them to offer a backend the other (or ``ClusterExecutor``) cannot
  actually run. ``pbs``, ``sge``, ``kubernetes`` and the cloud VM providers
  are not in the set, and naming one raises a ``ValueError`` that says so and
  points at its tracking issue. See :ref:`removed-backends`.
- ``cluster_type="local"`` runs the function on the submitting machine via
  ``LocalJobManager`` (see :doc:`local_executor`) instead of talking to a
  scheduler at all -- there is no host, no SSH connection, and
  ``submit_job``/``wait_for_result`` execute synchronously.
- ``cluster_host``: Hostname of cluster head node. Not used by ``local``
  (nothing to connect to) or ``huggingface``, which submits over an HTTP
  API and has no host.
- ``cluster_port``: SSH port (default: 22)
- ``ssh_connect_timeout``: Seconds paramiko waits to establish a connection
  (default: 30). The OS default is minutes, which turns an unreachable host
  into a hang rather than an error.
- ``ssh_host_key_policy``: What to do when a remote host's SSH key is not
  already in your known_hosts files. ``"reject"`` (default) refuses the
  connection and reports the exact ``ssh-keyscan`` command to add it.
  ``"auto_add"`` trusts unknown host keys automatically -- insecure
  (vulnerable to machine-in-the-middle attacks) and never the default; it
  has to be chosen deliberately. See ``clustrix.ssh_security``.

Paths
~~~~~

- ``remote_work_dir``: Working directory on the cluster. Defaults to
  ``~/.clustrix/jobs``. It must be on a filesystem the compute node can see:
  on SLURM each node has its own ``/tmp``, so an environment built
  on the login node is simply absent at run time and the job dies with exit 127
  before writing any diagnostics. A home directory or a shared scratch path
  both work; ``/tmp`` does not.
- ``local_work_dir``: Local working directory (default: current directory)
- ``local_cache_dir``: Accepted and stored (default: ``~/.clustrix/cache``),
  but nothing in clustrix reads it -- setting it has no effect. It is listed
  here only so that a configuration file containing it is not mistaken for a
  file that does something.
- ``conda_env_name``: An existing conda environment on the cluster to run
  jobs in, by **name** -- a path (a ``conda run -p`` prefix environment) is
  refused. It replaces the replicated execution environment and takes
  precedence over it, and with ``use_two_venv=False`` that replication is
  skipped entirely; see :doc:`../configuration` for how conda is located
  inside the job, for what counts as a name, and for the Python
  minor-version check the generated script makes before it runs anything.
  The name is validated where you set it -- ``configure()``, the constructor
  and a configuration file all refuse a path -- rather than at submission,
  when the job directory and the pickle are already on the cluster.
- ``venv_setup_timeout``: Seconds allowed for remote virtualenv creation
  (default: 300)

HuggingFace Jobs
~~~~~~~~~~~~~~~~

Used when ``cluster_type='huggingface'``:

- ``hf_token``: HuggingFace token. A token is required, but this field is not
  the only place it can come from: if it is unset, clustrix falls back to the
  ``HF_TOKEN`` environment variable, and then to the token ``hf auth login``
  writes (``$HF_HOME/token``, or ``~/.cache/huggingface/token``). Only when
  all three are absent does job submission fail, with a message naming all
  three options.
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

Credential Handling
~~~~~~~~~~~~~~~~~~~

``ClusterConfig`` treats a fixed set of fields -- ``clustrix.config.SECRET_FIELDS``
-- as credentials: anything whose name matches ``password``, ``token``,
``api_key``, ``*_key``, ``client_id``, ``tenant_id``, ``subscription_id``, or
similar (a handful of innocuous look-alikes, like ``use_env_password`` and
``password_env_var``, are explicitly excluded). This is computed once from
the dataclass's own field names rather than hand-maintained, so a newly
added credential field (a new cloud provider's API key, say) is covered
automatically instead of silently leaking in plaintext until someone
remembers to add it to a list.

Two things read that set:

- ``repr(config)`` masks every secret field as ``'***'`` rather than
  printing it verbatim, so a config object landing in a traceback, log
  line, or notebook cell display does not leak a password or token.
  ``environment_variables`` is masked entry-by-entry, since it commonly
  carries both ordinary settings (``OMP_NUM_THREADS``) and real secrets
  (``AWS_SECRET_ACCESS_KEY``).
- ``config.save_to_file(path)`` omits secret-bearing fields entirely by
  default, since a saved config file is easy to accidentally commit, back
  up, or share; pass ``include_secrets=True`` to write them anyway (for a
  config file you deliberately keep out of version control). The file is
  created with ``0600`` permissions from the moment it exists -- before any
  content is written, and re-applied even when overwriting a file that
  already had looser permissions -- so there is never a window where a
  config file containing credentials is world- or group-readable.