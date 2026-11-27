.. _cli:

Command line
============

Installing the package installs a ``clustrix`` command. Every transcript on
this page was produced by running the command shown, in a fresh home
directory, against a real ``sshd`` on ``127.0.0.1:2222``. The home directory
is shown as ``/home/researcher``.

.. code-block:: console

   $ clustrix --help
   Usage: clustrix [OPTIONS] COMMAND [ARGS]...

     Clustrix CLI - Distributed computing for Python functions.

   Options:
     --help  Show this message and exit.

   Commands:
     config       Show Clustrix settings, or change them and save the change.
     credentials  Manage Clustrix credentials.
     load         Load configuration from a file and make it the default.
     ssh-setup    Set up SSH keys for cluster authentication.
     status       Show cluster status; exits non-zero when the cluster is...

``clustrix config``
-------------------

Without options it prints the settings in effect:

.. code-block:: console

   $ clustrix config
   Current Clustrix Configuration:
     cluster_type: slurm
     cluster_host: None
     username: None
     default_cores: 4
     default_memory: 8GB
     default_time: 01:00:00

With options it changes those settings **and saves them** to the per-user
configuration file, ``<config dir>/config.yml`` (``~/.clustrix/config.yml``
unless ``CLUSTRIX_CONFIG_DIR`` is set). The next process, a Python session
included, starts from that file.

.. code-block:: console

   $ clustrix config --cluster-type slurm --cluster-host 127.0.0.1 --username clx --cores 2 --memory 4GB --time 00:30:00
   Configuration updated successfully!
   Configuration saved to /home/researcher/.clustrix/config.yml
   $ clustrix config
   Current Clustrix Configuration:
     cluster_type: slurm
     cluster_host: 127.0.0.1
     username: clx
     default_cores: 2
     default_memory: 4GB
     default_time: 00:30:00

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Option
     - Sets
   * - ``--cluster-type``
     - ``cluster_type``: one of ``local``, ``ssh``, ``slurm``,
       ``huggingface``
   * - ``--cluster-host``
     - ``cluster_host``
   * - ``--username``
     - ``username``
   * - ``--cores``
     - ``default_cores``; ``-1`` means every core available
   * - ``--memory``
     - ``default_memory`` (for example ``8GB``)
   * - ``--time``
     - ``default_time`` (for example ``04:00:00``)
   * - ``--config-file PATH``
     - Save to this file instead of the per-user file. A file saved this way
       is not read automatically; load it with ``clustrix load``.

Other settings (the port, the key file, the remote work directory and the
rest of :doc:`configuration`) have no option. Put them in a YAML file and use
``clustrix load``, or use :func:`clustrix.configure` in Python.

Values are validated, and nothing is saved when one is refused:

.. code-block:: console

   $ clustrix config --cores 0
   Error: default_cores=0 is not valid: use a positive number of cores, or -1 for every core available.
   $ echo $?
   1

There is no ``--api-key`` option; it was removed. Secrets are never written
to the configuration file: passwords, tokens and environment-variable
mappings are left out of what is saved. The file is created with mode
``0600`` in a directory with mode ``0700``.

If the per-user file holds named profiles saved by the notebook widget,
``clustrix config`` refuses to overwrite it and says so.

``clustrix load``
-----------------

Reads a YAML or JSON configuration file, makes it the configuration, and
saves the result to the per-user file. The loaded file replaces the previous
configuration; it is not merged into it.

.. code-block:: console

   $ cat site.yml
   cluster_type: slurm
   cluster_host: 127.0.0.1
   cluster_port: 2222
   username: clx
   key_file: /tmp/clustrix-local-cluster/id_ed25519
   remote_work_dir: /home/clx/clustrix-docs
   default_cores: 1
   $ clustrix load site.yml
   Configuration loaded from site.yml
   Configuration saved to /home/researcher/.clustrix/config.yml
   $ clustrix load nope.yml
   Error: File not found
   $ echo $?
   1

``clustrix status``
-------------------

Connects to the configured ``ssh`` or ``slurm`` host and reports the outcome.
The exit status is ``0`` when the connection succeeds and ``1`` when it
fails, so it can be used in a script.

With no such host there is nothing to connect to, and that is not a failure:
it says where ``@cluster`` runs a function instead, in the same words as
``%clustrix status``, and exits ``0``.

.. code-block:: console

   $ clustrix status
   backend      : local (no cluster host set; cluster_type is 'slurm')
   resources    : 4 cores, 8GB, 01:00:00
   connection   : not checked (no ssh or slurm host is configured)
   $ echo $?
   0

That is a fresh installation: ``cluster_type`` defaults to ``slurm``, and
with no host the function runs on this machine, so that is what the first
line says. After ``clustrix config --cluster-type local`` it reads
``backend      : local``. For ``huggingface`` the backend is named and no
connection is attempted: this command does not check a HuggingFace token.

A host that has never been connected to is refused (see
:ref:`host-key-verification`):

.. code-block:: console

   $ clustrix status
   Cluster Status:
     Type: slurm
     Host: 127.0.0.1
     User: clx
     Connection: ✗ Failed
     Error: Host key verification failed for '[127.0.0.1]:2222': this host is not in your known_hosts file(s), so clustrix refused the connection rather than risk a machine-in-the-middle attack.
     Offered key: ssh-ed25519 SHA256:t71ZfprzQXrLFRY47Bhv+RehzAB+NGmKLeKfi1cK//c

   To fix this:
     1. If you recognize and trust this host, add its key with:
          ssh-keyscan -p 2222 127.0.0.1 >> ~/.ssh/known_hosts
        then retry.
     2. If you understand the risk and want clustrix to trust unknown host keys automatically (NOT recommended -- this is exactly the behavior that enables MITM attacks), set on ClusterConfig:
          ssh_host_key_policy="auto_add"

   $ echo $?
   1

After checking the fingerprint and adding the key:

.. code-block:: console

   $ ssh-keyscan -p 2222 127.0.0.1 >> ~/.ssh/known_hosts
   $ clustrix status
   Cluster Status:
     Type: slurm
     Host: 127.0.0.1
     User: clx
     Connection: ✓ Connected
   $ echo $?
   0

``clustrix ssh-setup``
----------------------

Generates an SSH key, installs the public key on the host using your
password once, and optionally writes a ``~/.ssh/config`` entry. See
:doc:`ssh_setup`.

.. code-block:: console

   $ clustrix ssh-setup --help
   Usage: clustrix ssh-setup [OPTIONS]

     Set up SSH keys for cluster authentication.

   Options:
     --host TEXT               Cluster hostname  [required]
     --user TEXT               Username for cluster access  [required]
     --port INTEGER            SSH port (default: 22)
     --alias TEXT              Alias name for SSH config entry
     --key-type [ed25519|rsa]  SSH key type
     --force-refresh           Force generation of new SSH keys
     --help                    Show this message and exit.

It first looks for a key that already works and stops if it finds one. It
then prompts for the password; the password is not accepted as an option. In
this documentation build the command was run up to the password prompt and
not beyond it.

``clustrix credentials``
------------------------

Manages the credential file ``<config dir>/.env``.

.. code-block:: console

   $ clustrix credentials --help
   Usage: clustrix credentials [OPTIONS] COMMAND [ARGS]...

     Manage Clustrix credentials.

   Options:
     --help  Show this message and exit.

   Commands:
     edit     Open credential file in editor.
     list     List available credentials (values masked).
     migrate  Legacy migration command - 1Password support removed.
     reset    Reset credential file to template.
     setup    Interactive credential setup wizard.
     test     Test all configured credentials.

.. code-block:: console

   $ clustrix credentials list
   🔑 Clustrix Credential Status
   ==================================================
   📁 Config Directory: /home/researcher/.clustrix
   📄 Credential File: /home/researcher/.clustrix/.env
   📋 .env File Exists: ✅

   📊 Credential Sources:
     ✅ DotEnvCredentialSource
     ✅ EnvironmentCredentialSource
     ❌ GitHubActionsCredentialSource

   🔐 Available Credentials:
     ❌ SSH: Not configured
     ❌ HUGGINGFACE: Not configured

   💡 No credentials configured. Run 'clustrix credentials setup' to get started.

Two credential kinds exist, ``SSH`` and ``HUGGINGFACE``. An environment
variable overrides the same name in ``.env``.

Values in ``.env`` are written in single quotes and read literally. A value
such as ``pa$$word`` or ``${HOME}`` is used exactly as written; there is no
``${VAR}`` expansion.

A stored SSH credential is offered only to the host it names (``SSH_HOST``),
or to a host that came from a configuration source you chose. A host named
by a configuration file that was merely found in the working directory, or
in a directory selected by ``CLUSTRIX_CONFIG_DIR``, does not receive stored
credentials; see :doc:`configuration`.

``credentials setup``, ``edit`` and ``reset`` are interactive and were not
run for this page.
