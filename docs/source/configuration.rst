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

.. warning::

   **Items 4-6 are not trusted with your credentials.** Nobody chooses a
   working-directory configuration file by being in the directory --
   ``git clone`` followed by ``cd`` is the whole of what it takes for a
   repository to supply one, and it usually wins outright, because
   ``~/.clustrix/clustrix.yml`` is not in this list at all (``config.yml``
   is). Adopting one therefore emits a ``UserWarning`` naming the file, and
   a credential stored in ``~/.clustrix/.env`` that does not name a host of
   its own is **not** offered to a ``cluster_host`` that came from one.

   Nothing else changes: every non-credential setting in a project-local
   ``clustrix.yml`` takes effect as before. Two things make the credential
   available again, and they are the only two:

   - Set ``SSH_HOST`` in the credential file (``~/.clustrix/.env``) to the
     host that may receive the secret. That is you naming the recipient, in
     a file only you can write, and it is checked before provenance is --
     so it works whatever the hostname's provenance turns out to be.
   - Move the settings into ``~/.clustrix/config.yml``, delete the
     working-directory file, unset ``CLUSTRIX_CONFIG_DIR`` if it is set,
     and start a new process.

   Both remedies name ``~/.clustrix`` rather than ``<config dir>``. The
   placeholder is right for *where clustrix looks*, and wrong here: under a
   ``CLUSTRIX_CONFIG_DIR`` redirect it stands for a directory somebody else
   chose, so a sentence about authorising a host would be pointing at a
   file the redirector controls. Quickstart says ``~/.clustrix/.env`` for
   the same reason.

   ``configure(cluster_host=...)`` and ``load_config(path)`` are **not** on
   that list, however obvious they look. See the next paragraph.

   **Items 1-3 are trusted only while ``<config dir>`` is ``~/.clustrix``.**
   The reason to trust them is that putting a file in your own
   ``~/.clustrix`` is something you did; that reason does not survive the
   *directory* being named by ``CLUSTRIX_CONFIG_DIR``, because an
   environment variable is inherited from whatever started the process and a
   repository-shipped ``.envrc``, ``Makefile`` or devcontainer definition
   sets one for every command run inside the checkout. A redirected config
   directory therefore behaves like items 4-6: the settings apply, a
   ``UserWarning`` names the file, and a hostless stored credential is not
   offered to a ``cluster_host`` it named. The redirect itself is unchanged
   and still what you want in a container or on a shared machine; to
   authorise a host from one, use either of the two remedies above.

   **Provenance follows the hostname, not the object, and it is permanent
   within the process.** Once an untrusted file has named a ``cluster_host``,
   rebuilding a config around that same hostname does not make it your
   choice -- ``dataclasses.replace(config, ...)``,
   ``configure(**asdict(config))`` (which is what the notebook widget's
   *Apply* button does) and any other round trip all leave it untrusted.
   Handing a value back through a function is not evidence that anyone chose
   it.

   That is why typing ``configure(cluster_host="the-same-host")`` yourself
   does not lift the refusal either, even though you really did type it:
   the widget's *Apply* button makes that exact call, with that exact
   hostname, on a config it read out of the file. The two are the same call.
   Clearing the record for one would clear it for the other, which is the
   laundering route this rule exists to close, so the record is never
   cleared and there is no API to clear it. ``load_config(path)`` on the
   offending file is the same story: naming a path you did not write is not
   choosing a host.

   **In the** ``%%clusterfy`` **widget, only the host field lifts it.** The
   widget remembers which of the configurations in its dropdown it found on
   disk and which hostname each of those files named, so rearranging them
   changes nothing: renaming a configuration in the name box, copying it with
   the *+* button, saving it into your own configuration directory or pasting
   over it in the *Load* box all keep the refusal, because none of them is
   you choosing who receives your password. Typing your own hostname over the
   host field does lift it -- for that hostname -- because a host is only
   refused by a file that actually named it.

   Pasting is the one worth stating precisely, because it is the user
   typing: the refusal survives a paste that *keeps* the hostname the found
   file named, and a paste that changes the hostname is you naming a host,
   which lifts it for that host exactly as the host field does.

   **Saving is where that has to survive a restart.** *Save configuration*
   writes into ``~/.clustrix``, and that is a directory the widget infers
   trust from when it looks for configurations next time -- so without care,
   pressing Save would promote a configuration a repository shipped to one
   you chose, one session later, with nothing left on disk to say otherwise.
   It is also not only the configuration you selected: a save writes every
   configuration in the dropdown, verbatim, including ones you never opened.

   So the widget writes the source down beside the configurations, under a
   top-level ``config_sources`` key, and reads it back the next time. It
   behaves exactly like the profile store's record below: it can only ever
   *lower* trust -- a file claiming ``runtime`` for its own configurations is
   ignored -- so a project's configuration you deliberately keep is kept,
   along with the reason it is not handed your credential.

   **To adopt a project's configuration on purpose**, do what
   ``clustrix`` already tells you to do for a working-directory file: put
   the settings into ``~/.clustrix/config.yml`` *yourself* and start a new
   process. A file you wrote records nothing, and a file that records
   nothing is yours -- which is the whole difference between moving a
   configuration and pressing a button that moves it for you. If you would
   rather keep the file where it is, ``SSH_HOST=<host>`` in the credential
   file is authorisation for that one host, as always.

   The cost is a refusal when a ``./clustrix.yml`` names the host you were
   going to use anyway. Those two cases are genuinely indistinguishable, so
   the refusal is the safe half of the pair, and the two remedies above are
   the way out: ``SSH_HOST`` is authorisation no round trip can manufacture,
   and a new process starts with an empty record.

   **A saved profile remembers where it came from.** That record is
   per-process, but the notebook widget's profile store is not. Seven of its
   operations write ``<config dir>/profiles/profiles.yml`` as a side effect
   -- creating, cloning, renaming, removing or saving a profile, importing
   one, and merely *switching* which is active -- so a profile read out of a
   bundle a repository shipped ends up inside your own configuration
   directory, where re-deriving its provenance from the file's location
   would call it yours. Clustrix therefore writes the source down beside
   each profile and restores it with them: an untrusted profile stays
   untrusted across restarts, and carries the same refusal. A recorded
   source can only ever *lower* trust -- a bundle claiming ``runtime`` for
   its own profiles is ignored -- so a project-local profile you deliberately
   keep is kept, along with the reason it is not handed your credential.
   Deleting the profile and starting a new process is what clears it.

   **Upgrading from a version that did not record this.** A profile store
   written before clustrix recorded provenance says nothing about where its
   profiles came from, and clustrix does not guess. It used to: it worked out
   the source from where the store now sat, which is ``~/.clustrix``, which
   is trusted -- so the rule above protected nobody whose store had already
   been written into. Silence now fails closed.

   What you see the first time you open such a store is a warning naming the
   profiles concerned, and, if you go on to use one with a stored credential
   that names no host, a refusal explaining the same thing. Nothing is
   deleted, every profile still loads and every other way of connecting --
   SSH keys, a credential that names its host, ``configure()`` in your own
   Python -- is unaffected.

   Two things clear it. ``SSH_HOST=<host>`` in the credential file is
   authorisation for that one host, as always. Or, once you have looked at
   the list in the warning and recognise every profile on it:

   .. code-block:: python

      import clustrix
      clustrix.adopt_profile_store()   # then start a new process

   That records, for each profile the store had no answer for, that you named
   the store yourself -- the same thing passing a path to
   ``ProfileManager.load_from_file`` has always meant. It is not a way to
   grant trust: a profile the store *does* record as untrusted is left
   exactly as it is, however often you run it. Look at the list first; a
   profile you do not recognise is the thing this is protecting you from.

   **What ``load_config(path)`` does and does not mean.** It is trusted:
   it is a call in your own Python naming a file, it is not reachable by
   handing a config back through a function, and distrusting *relative*
   paths would be theatre, since
   ``load_config(os.path.abspath("clustrix.yml"))`` is the same act. What
   it is not is a check on the file's contents. Clustrix cannot tell a
   configuration file you wrote from one that arrived with a checkout, so
   ``load_config`` on a repository-shipped file trusts that repository's
   ``cluster_host`` -- and it does so whether or not the automatic search
   would also have found it, which it does not when the file is named
   anything but ``clustrix.{yml,yaml,json}`` or you are running from
   another directory. Point ``load_config`` at files you wrote.

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
3. The configuration file found at import.
4. Dataclass defaults.

There is **no** general environment-variable layer. Nothing reads a
``CLUSTRIX_<FIELD>`` variable, and no environment variable assigns to a field
on ``ClusterConfig``. Documentation elsewhere that lists "environment
variables" as a general precedence level is describing something the code does
not do.

Clustrix does read the environment for other purposes. Those uses group as
follows, and not one of them writes to a configuration field.

*Where configuration lives.* ``CLUSTRIX_CONFIG_DIR`` chooses the directory
searched at import and written by ``save_config``. It moves where clustrix
looks; it does not vouch for what it finds there (see the warning above).

*What happens on import.* ``CLUSTRIX_AUTO_WIDGET`` displays the notebook
widget when clustrix is imported.

*Credentials.* Whatever name you put in ``password_env_var`` supplies an SSH
password. ``FlexibleCredentialManager`` -- the fallback used when neither
``key_file`` nor ``password`` is set -- reads ``SSH_HOST``, ``SSH_USERNAME``,
``SSH_PASSWORD``, ``SSH_PRIVATE_KEY_PATH``, ``SSH_PORT``, ``HF_TOKEN`` (or
``HUGGINGFACE_TOKEN``), ``HUGGINGFACE_USERNAME`` and ``HF_USERNAME``, from a
``.env`` file or from the process environment, and switches to its CI source
when ``GITHUB_ACTIONS`` is ``"true"``. A stored SSH credential goes to one
host and no other: if it sets ``SSH_HOST``, that host must be the one being
connected to; if it does not, ``cluster_host`` must have come from a source
you chose (see the warning above). The HuggingFace backend reads
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
``from clustrix.config import load_config; load_config("my-cluster.yml")``.
Named ``clustrix.yml`` it is also picked up automatically when it sits in the
working directory -- along with the credential restriction described above:

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
