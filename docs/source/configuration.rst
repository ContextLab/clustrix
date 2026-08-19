.. _configuration:

Configuration
=============

Every setting Clustrix has lives on one dataclass, ``clustrix.config.ClusterConfig``,
and there is exactly one instance of it per process. This page lists every
field that changes behaviour, says what it actually does and what its real
default is, and -- just as importantly -- says which fields currently do
nothing.

Defaults quoted here were read out of the dataclass, not out of an older
version of this document.

.. contents:: On this page
   :local:
   :depth: 2


Where configuration comes from
------------------------------

**At import.** ``import clustrix`` calls ``_load_default_config()``, which
tries these paths in order and stops at the first one that loads:

1. ``<config dir>/config.yml``
2. ``<config dir>/config.yaml``
3. ``<config dir>/config.json``
4. ``./clustrix.yml``
5. ``./clustrix.yaml``
6. ``./clustrix.json``

``<config dir>`` is ``~/.clustrix``, unless ``CLUSTRIX_CONFIG_DIR`` is set, in
which case it is that (expanded). A file that raises while loading is skipped
silently and the search continues.

Note item 4: a ``clustrix.yml`` in the current working directory is picked up
automatically. Changing directory does not reload it.

**At runtime.** ``clustrix.configure(**kwargs)`` sets fields on the existing
instance. ``load_config(path)`` -- imported from ``clustrix.config``, not
re-exported at the package top level -- replaces the instance wholesale from a
file. Both reject unknown names rather than accepting them silently:

.. code-block:: python

   import clustrix

   try:
       clustrix.configure(cleanup_remote_files=True)
   except ValueError as exc:
       print(exc)

.. code-block:: text

   Unknown configuration parameter: cleanup_remote_files

``load_config`` goes further and suggests the field you probably meant:

.. code-block:: text

   ValueError: bad.yml contains unknown setting(s): cleanup_remote_files
   (did you mean cleanup_on_success?)

**Per call.** Six settings can be overridden on the decorator: ``cores``,
``memory``, ``time``, ``partition``, ``queue`` and ``environment``. Everything
else is configuration-only, with the exception of the pass-through extras
listed under :ref:`decorator-extras`.

**Effective precedence**

1. ``@cluster(...)`` arguments (the six above, plus the extras).
2. ``clustrix.configure()`` / direct attribute assignment.
3. The configuration file found at import.
4. Dataclass defaults.

There is **no** general environment-variable layer. Only three environment
variables are read at all: ``CLUSTRIX_CONFIG_DIR`` (where to look for config),
``CLUSTRIX_AUTO_WIDGET`` (display the notebook widget on import), and whatever
name you put in ``password_env_var``. Documentation elsewhere that lists
"environment variables" as a general precedence level is describing something
the code does not do.

Reading and saving
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.config import ClusterConfig

   cfg = ClusterConfig(cluster_type="ssh", cluster_host="host.example.edu",
                       username="me", password="hunter2")
   print("password is masked in repr:", "hunter2" not in repr(cfg))

``__repr__`` masks secret-bearing fields as ``***`` so a token cannot land in a
traceback, a log line or a notebook cell.

``ClusterConfig.save_to_file`` and ``clustrix.config.save_config`` write with
mode 0600 -- applied via
``os.open``'s mode argument and re-applied with ``fchmod`` before any content
is written, so a newly created file never exists at wider permissions even
momentarily, and overwriting a pre-existing looser file also tightens it.
Secret-bearing fields are **omitted by default**; pass ``include_secrets=True``
to write them anyway. Which fields count as secret is derived from field
*names* (``secret``, ``token``, ``password``, ``api_key``, ``access_key``,
``*_key``, ``client_id``, ``tenant_id``, ``subscription_id``), so a newly added
credential field is covered automatically. ``environment_variables`` is
filtered entry by entry with the same test.


Choosing a backend
------------------

.. list-table::
   :header-rows: 1
   :widths: 22 12 66

   * - Field
     - Default
     - Effect
   * - ``cluster_type``
     - ``"slurm"``
     - One of ``local``, ``ssh``, ``slurm``, ``pbs``, ``sge``, ``kubernetes``,
       ``huggingface`` (``SUPPORTED_CLUSTER_TYPES``). Anything else raises
       ``ValueError: Unsupported cluster type: ...`` at submit time. Note the
       default is ``slurm``, but with no ``cluster_host`` set the decorator
       still runs locally -- see :ref:`execution-model`.
   * - ``cluster_host``
     - ``None``
     - The SSH host. **Its absence is what makes execution local** for every
       backend except ``huggingface`` and auto-provisioned Kubernetes.
   * - ``cluster_port``
     - ``22``
     - Port passed to paramiko.
   * - ``prefer_local_parallel``
     - ``False``
     - Forces local execution even when a ``cluster_host`` is configured.

Connection and authentication
-----------------------------

.. list-table::
   :header-rows: 1
   :widths: 22 12 66

   * - Field
     - Default
     - Effect
   * - ``username``
     - ``None``
     - SSH username. Falls back to ``$USER``.
   * - ``key_file``
     - ``None``
     - Path to a private key. **Tried first**, before ``password``.
   * - ``password``
     - ``None``
     - Used only if ``key_file`` is unset. If both are unset, clustrix asks
       ``FlexibleCredentialManager`` (``.env``, environment, GitHub Actions),
       and failing that falls back to the SSH agent and default keys.
   * - ``use_env_password``
     - ``False``
     - Enables reading the password from ``password_env_var``.
   * - ``password_env_var``
     - ``""``
     - Name of the environment variable holding the password.
   * - ``ssh_host_key_policy``
     - ``"reject"``
     - ``"reject"`` refuses an unknown host key and prints the ``ssh-keyscan``
       command to add it. ``"auto_add"`` trusts unknown keys -- insecure, and
       never the default. Any other value raises at construction time.
   * - ``ssh_connect_timeout``
     - ``30``
     - Seconds paramiko waits to connect. The OS default is minutes, which
       turns an unreachable host into a hang rather than an error.
   * - ``ssh_port``
     - ``22``
     - Read by ``auth_manager`` only. The executor uses ``cluster_port``.
   * - ``api_key``
     - ``None``
     - Generic API key used by the credential/auth helpers.

.. code-block:: python

   from clustrix.config import ClusterConfig

   try:
       ClusterConfig(ssh_host_key_policy="yolo")
   except ValueError as exc:
       print(exc)

.. code-block:: text

   Invalid ssh_host_key_policy='yolo'. Valid values are 'reject' (default,
   secure) or 'auto_add' (insecure, trusts unknown host keys automatically).


Resources
---------

.. list-table::
   :header-rows: 1
   :widths: 22 14 64

   * - Field
     - Default
     - Effect
   * - ``default_cores``
     - ``4``
     - ``--cpus-per-task`` / ``ppn`` / ``-pe``, and the local process-pool size.
   * - ``default_memory``
     - ``"8GB"``
     - Rewritten per scheduler by ``normalize_memory``: ``8G`` for SLURM,
       ``8gb`` for PBS/SGE, ``8GB`` for Kubernetes.
   * - ``default_time``
     - ``"01:00:00"``
     - Wall-clock limit directive.
   * - ``default_partition``
     - ``None``
     - ``#SBATCH --partition``. Omitted when unset.
   * - ``default_queue``
     - ``None``
     - ``#PBS -q`` / ``#$ -q``. Omitted when unset.
   * - ``max_parallel_jobs``
     - ``100``
     - Upper bound on the number of chunks ``_execute_parallel`` splits a
       remote loop into.


Paths and the remote environment
--------------------------------

.. list-table::
   :header-rows: 1
   :widths: 26 20 54

   * - Field
     - Default
     - Effect
   * - ``remote_work_dir``
     - ``"~/.clustrix/jobs"``
     - Parent of every job directory. A leading ``~/`` is expanded against the
       remote ``$HOME`` before SFTP touches it. Home-relative rather than
       ``/tmp`` on purpose: on SLURM/PBS/SGE a compute node has its own
       ``/tmp``, so an environment built on the login node is simply absent at
       run time and the job dies with exit 127 before writing diagnostics.
   * - ``local_work_dir``
     - ``None``
     - Base directory for the *filesystem utilities* when operating locally.
       Defaults to the current working directory. Does not affect job
       execution.
   * - ``python_executable``
     - ``"python"``
     - Command used to create the single-venv fallback and to run the job
       script. Note that many systems have no ``python``, only ``python3``;
       ``resolve_remote_python`` probes for a working interpreter rather than
       trusting this blindly.
   * - ``package_manager``
     - ``"pip"``
     - ``"pip"``, ``"uv"`` (``uv pip``), ``"conda"``, or ``"auto"`` (uv, then
       conda, then pip). Applies to the single-venv fallback path.
   * - ``conda_env_name``
     - ``None``
     - Passed through as the job's ``environment``.
   * - ``use_two_venv``
     - ``True``
     - Build the two-environment layout described in :ref:`two-venv`. Turning
       it off gives you one venv containing **only** dill and cloudpickle --
       your packages are not mirrored.
   * - ``venv_setup_timeout``
     - ``300``
     - Seconds allowed for the two-venv setup thread. Exceeding it logs
       ``Two-venv setup timed out`` and falls back to the single venv.
   * - ``replicate_local_environment``
     - ``True``
     - Mirror every installed ``name==version`` into VENV2 (and into the
       HuggingFace container). Turning it off makes the remote environment
       standard-library-only plus ``cluster_packages``.
   * - ``excluded_packages``
     - ``[]``
     - Names to leave out of that mirror. The documented escape hatch for a
       platform-specific wheel that cannot install on the cluster.
   * - ``cluster_packages``
     - ``[]``
     - Extra installs for VENV2. Either a string (``"torch"``,
       ``"torch==2.1.0"``) or a dict ``{"package": ..., "pip_args": ...,
       "timeout": ...}``. Also honoured by the HuggingFace backend (string form
       and the ``package`` key).
   * - ``venv_post_install_commands``
     - ``[]``
     - Commands run inside VENV2 after installs finish.
   * - ``module_loads``
     - ``[]``
     - ``module load <name>`` lines. Pasted unquoted, therefore validated.
   * - ``environment_variables``
     - ``{}``
     - ``export NAME=<quoted value>`` lines. Names are validated as shell
       identifiers; values are quoted.
   * - ``pre_execution_commands``
     - ``[]``
     - Raw shell lines emitted before execution. Not validated, not quoted.

All three of the last group also affect the *single-venv* setup path, which
shares ``environment_setup_lines``.

.. code-block:: python

   import clustrix

   clustrix.configure(
       cluster_type="local",
       replicate_local_environment=True,
       excluded_packages=["torch"],          # never mirror this one
       cluster_packages=["polars==0.20.31"], # but do install this one
   )
   cfg = clustrix.get_config()
   print(cfg.excluded_packages, cfg.cluster_packages)


Execution behaviour
-------------------

.. list-table::
   :header-rows: 1
   :widths: 26 12 62

   * - Field
     - Default
     - Effect
   * - ``auto_parallel``
     - ``True``
     - Attempt loop parallelization. Locally this routes through
       ``_execute_local_parallel``; remotely through ``detect_loops`` and
       ``_execute_parallel``. Read :doc:`limitations` before trusting it: the
       preconditions are narrow and the *return shape can change*.
   * - ``async_submit``
     - ``False``
     - Return an ``AsyncJobResult`` immediately instead of blocking.
   * - ``job_poll_interval``
     - ``30``
     - Seconds between status checks while waiting for a scheduler job.
   * - ``cleanup_on_success``
     - ``True``
     - ``rm -rf`` the remote job directory after a successful collection. A
       **failed** job's directory is always kept.

.. _decorator-extras:

Backend-specific settings
-------------------------

Kubernetes
~~~~~~~~~~

``k8s_namespace`` (``"default"``), ``k8s_image`` (``"python:3.11-slim"``),
``k8s_service_account`` (``None``), ``k8s_pull_policy`` (``"IfNotPresent"``),
``k8s_job_ttl_seconds`` (``3600``), ``k8s_backoff_limit`` (``3``).

``@cluster`` accepts ``k8s_namespace``, ``k8s_image``,
``k8s_service_account`` and ``k8s_pull_policy`` as keyword arguments and puts
them in ``job_config`` -- but ``KubernetesJobManager.submit_k8s_job`` reads
only ``self.config.k8s_*``, so **those per-call values have no effect**. The
only ``job_config`` keys this backend reads are ``cores`` and ``memory``. Set
the ``k8s_*`` fields through configuration instead.

The container installs only ``cloudpickle`` and ``dill``:
**``replicate_local_environment`` and ``cluster_packages`` are not honoured by
this backend.** Pick an image that already contains what your function
imports.

Auto-provisioning fields -- ``auto_provision_k8s`` (``False``),
``k8s_provider`` (``"aws"``), ``k8s_from_scratch`` (``True``),
``k8s_auto_cleanup`` (``True``), ``k8s_cluster_name``, ``k8s_node_count``
(``2``), ``k8s_node_type``, ``k8s_version`` (``"1.28"``), ``k8s_region`` --
drive cluster creation. ``k8s_remote`` (``False``) is read by the notebook
widget only.

HuggingFace Jobs (``cluster_type="huggingface"``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 26 20 54

   * - Field
     - Default
     - Effect
   * - ``hf_namespace``
     - ``None``
     - Org or user the job runs under. Falls back to ``hf_username``. Usually
       needs to be an org: a personal account is often not on a plan that can
       run jobs.
   * - ``hf_token``
     - ``None``
     - Falls back to the token ``hf auth login`` wrote (honouring ``HF_HOME``).
   * - ``hf_flavor``
     - ``None`` -> ``"cpu-basic"``
     - Hardware tier. Falls back to ``hf_hardware``.
   * - ``hf_allow_gpu_flavors``
     - ``False``
     - Any flavor not starting with ``cpu-`` is refused unless this is true.
   * - ``hf_image``
     - ``None`` -> ``python:<your minor>-slim``
     - Must match your local Python minor version, because dill payloads carry
       CPython bytecode.
   * - ``hf_job_timeout``
     - ``None`` -> ``"30m"``
     - Job timeout. Per-call override: ``@cluster(hf_timeout="2h")``.

Per-call overrides that are actually read: ``hf_flavor`` and
``hf_timeout``. ``@cluster`` also accepts ``hf_namespace``, ``hf_token`` and
``hf_username``, but ``HFJobsManager`` resolves all three from the
configuration (and, for the token, from ``HF_TOKEN`` or the ``hf auth login``
cache), so passing them per call has no effect on this backend.

.. code-block:: python

   from clustrix.config import ClusterConfig
   from clustrix.hf_jobs import HFJobsManager

   manager = HFJobsManager(ClusterConfig(cluster_type="huggingface"))
   try:
       manager._flavor({"hf_flavor": "a10g-small"})
   except ValueError as exc:
       print(exc)

.. code-block:: text

   Flavor 'a10g-small' is a GPU flavor and bills by the second. Set
   hf_allow_gpu_flavors=True to confirm you intend to pay for it; otherwise use
   a CPU flavor (default: cpu-basic).

Cloud VM providers
~~~~~~~~~~~~~~~~~~

``aws_*``, ``azure_*``, ``gcp_*``, ``lambda_*``, ``cloud_provider``
(``"manual"``), ``cloud_region``, ``cloud_auto_configure`` (``False``) feed the
``provider=`` routing and the pricing clients. The pricing and cost-estimation
clients work. The VM *execution* backends have never been shown to run a job
end to end; see :doc:`limitations`.

Both boto3-style and widget-style AWS names are accepted
(``aws_access_key_id``/``aws_access_key``, ``aws_secret_access_key``/
``aws_secret_key``) and reconciled by ``clustrix.field_mappings``.

The following can also be passed per call to ``@cluster``: ``lambda_api_key``,
``aws_access_key_id``, ``aws_secret_access_key``, ``aws_region``,
``azure_subscription_id``, ``azure_tenant_id``, ``azure_client_id``,
``azure_client_secret``, ``gcp_project_id``, ``gcp_service_account_key``,
``key_file``, ``terminate_on_completion``, ``instance_startup_timeout``.
Anything else is warned about and ignored.


Settings that currently have no effect
--------------------------------------

These fields exist on ``ClusterConfig``, are accepted by ``configure()``, are
saved and loaded, and are shown by the notebook widget -- but no execution code
path reads them. They are listed here so you do not tune something that cannot
change anything.

============================  ===========================================
Field                         Status
============================  ===========================================
``gpu_detection_enabled``     Not read. GPU detection runs unconditionally
                              inside ``enhanced_setup_two_venv_environment``.
``auto_gpu_packages``         Not read.
``cuda_version_preference``   Not read.
``gpu_memory_fraction``       Not read.
``prefer_gpu_execution``      Not read.
``gpu_requirements``          Not read.
``rapids_ecosystem``          Not read.
``max_gpu_parallel_jobs``     Not read.
``auto_gpu_parallel``         Not read. It used to select a client-side GPU
                              path that never called your function -- it ran a
                              fixed torch program per GPU and returned the
                              traces of random matrices as your result. That
                              path was deleted; the field is kept so existing
                              config files keep loading, and passing it to
                              ``@cluster`` now warns.
``local_parallel_threshold``  Not read. Local chunking uses
                              ``os.cpu_count() * 2`` instead.
``cache_credentials``         Not read.
``credential_cache_ttl``      Not read.
``local_cache_dir``           Not read.
``k8s_service_account``       Not read by the executor.
``k8s_pull_policy``           Not read by the executor.
``k8s_auto_cleanup``          Not read by the executor.
``cost_monitoring``           Not read.
``k8s_remote``                Notebook widget only.
``hf_sdk`` / ``hf_hardware``  Spaces-era fields. ``hf_hardware`` survives only
                              as a fallback for ``hf_flavor``.
``venv_info``                 Runtime scratch space, written by clustrix
                              during a submission. Do not set it yourself.
============================  ===========================================

One field is read by code but is **not** a dataclass field:
``hf_payload_repo``, which ``HFJobsManager._payload_repo`` looks up with
``getattr``. Because ``configure()`` rejects unknown names, it cannot be set
through the normal path; it defaults to ``<namespace>/clustrix-payloads``.


A worked configuration
----------------------

.. code-block:: python

   # cluster-required: needs a real SLURM cluster and credentials
   import clustrix

   clustrix.configure(
       cluster_type="slurm",
       cluster_host="hpc.example.edu",
       username="researcher",
       key_file="~/.ssh/id_ed25519",
       remote_work_dir="/scratch/researcher/clustrix",
       default_cores=8,
       default_memory="32GB",
       default_time="04:00:00",
       default_partition="compute",
       module_loads=["cuda/12.1"],
       environment_variables={"OMP_NUM_THREADS": "8"},
       excluded_packages=["tensorflow-macos", "tensorflow-metal"],
       cluster_packages=["torch==2.1.0"],
       job_poll_interval=15,
       cleanup_on_success=False,   # keep job dirs while you are debugging
   )

   @clustrix.cluster(cores=16, memory="64GB", time="08:00:00")
   def train(dataset_path):
       import torch
       return torch.load(dataset_path).mean().item()

The same thing as a file, loadable with
``from clustrix.config import load_config; load_config("clustrix.yml")``, or
picked up automatically if it sits in the working directory:

.. code-block:: yaml

   cluster_type: slurm
   cluster_host: hpc.example.edu
   username: researcher
   key_file: ~/.ssh/id_ed25519
   remote_work_dir: /scratch/researcher/clustrix
   default_cores: 8
   default_memory: 32GB
   default_time: "04:00:00"
   default_partition: compute
   module_loads:
     - cuda/12.1
   environment_variables:
     OMP_NUM_THREADS: "8"
   excluded_packages:
     - tensorflow-macos
     - tensorflow-metal
   cluster_packages:
     - torch==2.1.0
   job_poll_interval: 15
   cleanup_on_success: false


See also
--------

* :doc:`execution_model` -- what each of these settings changes, and when.
* :doc:`limitations` -- the cases no setting can fix.
