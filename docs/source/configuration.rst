.. _configuration:

Configuration
=============

Every setting Clustrix has lives on one dataclass, ``clustrix.config.ClusterConfig``,
and there is exactly one instance of it per process. This page covers all of
the fields on that dataclass: what each one actually does, what its real
default is, and -- for the ones that read like settings but change nothing --
that it is inert.

Defaults quoted here were read out of the dataclass rather than out of an
older version of this document, and the field list was checked the same way.
Concretely, every name returned by ``dataclasses.fields(ClusterConfig)``
appears somewhere below, either with an effect or in the table of fields that
have none.

.. contents:: On this page
   :local:
   :depth: 2


Where configuration comes from
------------------------------

**On first use.** ``import clustrix`` reads no files. The first call that
actually needs the configuration -- ``get_config()``, ``configure()``,
``save_config()``, or anything inside clustrix that reaches them -- triggers a
one-time search of these paths, in order, stopping at the first that exists:

1. ``<config dir>/config.yml``
2. ``<config dir>/config.yaml``
3. ``<config dir>/config.json``
4. ``./clustrix.yml``
5. ``./clustrix.yaml``
6. ``./clustrix.json``

``<config dir>`` is ``~/.clustrix``, unless ``CLUSTRIX_CONFIG_DIR`` is set, in
which case it is that (expanded).

The search used to run at import, which meant importing the library read your
home directory and your working directory before you had asked it for
anything, and an unreadable ``~/.clustrix`` made ``import clustrix`` raise
``PermissionError``. It is deferred so that neither happens. The singleton
itself is still built at import; only the file read moved.

A candidate that **cannot be examined at all** -- an unreadable directory, a
dead automount -- is logged at ``WARNING`` and the search moves on to the next
one.

A candidate that **is found and then fails to load** -- malformed YAML, a
misspelled setting -- raises ``clustrix.config.ConfigFileError``. It used to be
skipped in silence, which left the process running on built-in defaults while
you believed your file was in force; a ``cluster_host`` that never took effect
means the job runs somewhere other than where you said. Fix the file, move it
aside, or call ``clustrix.config.load_config(path)`` with a different one --
an explicit load replaces the configuration and supersedes the search.

Note item 4: a ``clustrix.yml`` in the current working directory is picked up
automatically, at first use. Changing directory afterwards does not reload it.

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
``memory``, ``time``, ``partition``, ``queue`` and ``environment``. Five of
the six reach a backend. ``queue`` does not: the decorator resolves it against
``default_queue`` and writes it into the job configuration, and nothing reads
it back out, because none of ``local``, ``ssh``, ``slurm`` or ``huggingface``
has a queue to submit to. Everything else is configuration-only, with the
exception of the pass-through extras listed under :ref:`decorator-extras`.

**Effective precedence**

1. ``@cluster(...)`` arguments (the six above, plus the extras).
2. ``clustrix.configure()`` / direct attribute assignment.
3. The configuration file found by the first-use search.
4. Dataclass defaults.

There is **no** general environment-variable layer. Nothing reads a
``CLUSTRIX_<FIELD>`` variable, and no environment variable assigns to a field
on ``ClusterConfig``. Documentation elsewhere that lists "environment
variables" as a general precedence level is describing something the code does
not do.

Clustrix does read the environment for other purposes. Those uses group as
follows, and not one of them writes to a configuration field.

*Where configuration lives.* ``CLUSTRIX_CONFIG_DIR`` chooses the directory
searched on first use and written by ``save_config``. It is read at the moment
of the search, not at import, so setting it after ``import clustrix`` but
before the first ``get_config()`` still takes effect.

*What happens on import.* ``CLUSTRIX_AUTO_WIDGET`` displays the notebook
widget when clustrix is imported.

*Credentials.* Whatever name you put in ``password_env_var`` supplies an SSH
password. ``FlexibleCredentialManager`` -- the fallback used when neither
``key_file`` nor ``password`` is set -- reads ``SSH_HOST``, ``SSH_USERNAME``,
``SSH_PASSWORD``, ``SSH_PRIVATE_KEY_PATH``, ``SSH_PORT``, ``HF_TOKEN`` (or
``HUGGINGFACE_TOKEN``), ``HUGGINGFACE_USERNAME`` and ``HF_USERNAME``, from a
``.env`` file or from the process environment, and switches to its CI source
when ``GITHUB_ACTIONS`` is ``"true"``. The HuggingFace backend reads
``HF_TOKEN`` directly as well, and honours ``HF_HOME`` when locating the token
that ``hf auth login`` cached. The key-setup helper in ``auth_fallbacks`` has
its own list: ``CLUSTRIX_PASSWORD_<HOST>``, ``CLUSTER_PASSWORD_<HOST>``,
``<HOST>_PASSWORD``, ``CLUSTRIX_DEFAULT_PASSWORD`` and ``CLUSTER_PASSWORD``,
with the host name upper-cased and its dots turned into underscores. All of
these hand a credential to the authentication path; none of them writes to
``ClusterConfig``.

*Variables clustrix sets for its own remote code.* ``CLUSTRIX_PACKAGES``,
``CLUSTRIX_PAYLOAD``, ``CLUSTRIX_PAYLOAD_REPO``, ``CLUSTRIX_PAYLOAD_FILE`` and
``CLUSTRIX_HMAC_KEY`` are written into the HuggingFace container by the
submitter and read back by the program running inside it;
``CLUSTRIX_ORIGINAL_CWD`` plays the same role for a packaged remote job. In
other words, these are an internal channel between the two halves of one
submission, and you do not set them yourself.

Separately, ``clustrix.validation`` -- a diagnostic helper, not part of
execution -- takes its target hosts from ``CLUSTRIX_VALIDATION_SSH_HOST``,
``CLUSTRIX_VALIDATION_SSH_NAME``, ``CLUSTRIX_VALIDATION_SLURM_HOST`` and
``CLUSTRIX_VALIDATION_SLURM_NAME``. With none of them set it reports that it
has nothing to check.

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
     - One of ``local``, ``ssh``, ``slurm``, ``huggingface``
       (``SUPPORTED_CLUSTER_TYPES``). Anything else raises a ``ValueError``
       that names the supported set. Note the default is ``slurm``, but with
       no ``cluster_host`` set the decorator still runs locally -- see
       :ref:`execution-model`. PBS, SGE, Kubernetes and the cloud VM providers
       are not supported; see :ref:`removed-backends`. This is a
       *configuration* setting and not a ``@cluster`` keyword: passing
       ``@cluster(cluster_type=...)`` warns and has no effect.
   * - ``cluster_host``
     - ``None``
     - The SSH host. **Its absence is what makes execution local** for every
       backend except ``huggingface``.
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
     - Read by ``auth_manager``, and by ``validate_cluster_auth`` in
       ``clustrix.validation`` -- the connection test behind the notebook
       widget's password check. The executor uses ``cluster_port``.
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
     - Rewritten per scheduler by ``normalize_memory``: ``8G`` for SLURM.
   * - ``default_time``
     - ``"01:00:00"``
     - Wall-clock limit directive.
   * - ``default_partition``
     - ``None``
     - ``#SBATCH --partition``. Omitted when unset.
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
       ``/tmp`` on purpose: on SLURM a compute node has its own
       ``/tmp``, so an environment built on the login node is simply absent at
       run time and the job dies with exit 127 before writing diagnostics.
   * - ``local_work_dir``
     - ``None``
     - Base directory for the *filesystem utilities* when operating locally.
       Defaults to the current working directory. Does not affect job
       execution.
   * - ``local_cache_dir``
     - ``"~/.clustrix/cache"``
     - Where a staged data package lands when it is materialized without an
       explicit destination: the files go under
       ``<local_cache_dir>/data-packages/<package id>``. That same
       subdirectory is the only thing ``DataPackage.delete`` clears out
       locally -- never the cache directory above it, and never the originals
       you packaged.
   * - ``python_executable``
     - ``"python"``
     - Command used to create the single-venv fallback and to run the job
       script. Note that many systems have no ``python``, only ``python3``;
       ``resolve_remote_python`` probes for a working interpreter rather than
       trusting this blindly. If the probe itself cannot be run -- a dropped
       SSH transport, a closed session -- it raises saying so, rather than
       reporting that the remote host has no matching interpreter: that would
       be a claim about a machine clustrix never managed to ask.
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
   * - ``job_wait_timeout``
     - ``86400``
     - Seconds to keep polling before giving up on a scheduler job and raising
       ``TimeoutError``. The job is deliberately **not** cancelled, and the
       message names the remote directory so the result can still be collected
       by hand. Set it to ``None`` to wait indefinitely. The default of 24
       hours is generous because a real queue wait legitimately runs into
       hours; a job that is held or stuck behind a queue that never clears
       would otherwise hang the caller with no way out but Ctrl-C.
   * - ``cleanup_on_success``
     - ``True``
     - ``rm -rf`` the remote job directory after a successful collection. A
       **failed** job's directory is always kept.

.. _decorator-extras:

Backend-specific settings
-------------------------

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

Data staging
~~~~~~~~~~~~

These four control ``clustrix.staging``, which moves a directory of input files
to wherever the function will run. Small packages ride inside the pickled
payload; larger ones go to a private HuggingFace dataset repo. The size bands
below decide which, and where the second one stops.

.. list-table::
   :header-rows: 1
   :widths: 28 18 54

   * - Field
     - Default
     - Effect
   * - ``hf_data_repo``
     - ``None``
     - Repo that oversized packages are uploaded to. Unset, the repo is
       ``<namespace>/clustrix-data``, with the namespace taken from
       ``hf_namespace``, then ``hf_username``, then whatever the token's
       ``whoami()`` reports. Applies whichever backend you run on: a ``slurm``
       job with a package too big to inline still stages through HuggingFace.
   * - ``stage_inline_max_bytes``
     - ``1048576`` (1 MB)
     - Packages smaller than this carry their file contents inside the package
       object, so no remote store is involved and there is nothing to clean up
       afterwards.
   * - ``stage_warn_bytes``
     - ``104857600`` (100 MB)
     - At or above this, staging logs a warning before starting. A transfer
       that takes minutes with no output is indistinguishable from a hang.
   * - ``stage_max_bytes``
     - ``5368709120`` (5 GB)
     - At or above this, staging refuses outright and names the largest file.
       Raise it if you genuinely mean to move that much over the network.

Nothing staged is reclaimed automatically. There is no TTL and no reaper --
deleting a package is always something you do, through
``DataPackage.delete``.

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
``auto_gpu_parallel``         Not read. There is no automatic
                              cross-GPU parallelization; parallelize across
                              GPUs inside your own function. The field is
                              accepted so that existing config files keep
                              loading, and passing it to ``@cluster`` warns.
``local_parallel_threshold``  Not read. Local chunking uses
                              ``os.cpu_count() * 2`` instead.
``cache_credentials``         Not read.
``credential_cache_ttl``      Not read.
``default_queue``             Resolved and placed in the job configuration
                              by the decorator, then never read: none of the
                              four supported backends submits to a queue.
                              ``@cluster(queue=...)`` is inert for the same
                              reason. Use ``default_partition`` on SLURM.
``hf_hardware``               Read only as a fallback for ``hf_flavor``.
                              Set ``hf_flavor``.
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
