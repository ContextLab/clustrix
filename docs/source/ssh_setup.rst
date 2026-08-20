SSH Key Setup for Remote Clusters
====================================

Clustrix generates an SSH key, deploys it to the cluster, and writes the
matching ``~/.ssh/config`` entry, in one call. Those are the same three steps
you would otherwise run by hand, in the same order.

.. note::
   A runnable walkthrough is available as a notebook:
   `SSH Key Automation Tutorial <https://colab.research.google.com/github/ContextLab/clustrix/blob/master/docs/ssh_key_automation_tutorial.ipynb>`_

Quick Start: Automated Setup
-----------------------------

The easiest way to set up SSH access is using Clustrix's automated system:

Method 1: Interactive Widget (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open the widget with the ``%%remote`` magic, in a cell of its own. Importing
``clustrix`` registers the magic but does not display the widget.

.. code-block:: ipython3

   %%remote

Then:

1. Choose a remote cluster type (``ssh`` or ``slurm``) so the connection
   section appears
2. Enter your cluster hostname (e.g. ``cluster.example.edu``)
3. Enter your username
4. Enter your password
5. Click "Auto setup SSH keys"

Method 2: Command Line Interface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Basic automated setup
   clustrix ssh-setup --host cluster.example.edu --user your_username
   
   # With custom alias for easy access
   clustrix ssh-setup --host cluster.example.edu --user your_username --alias my_hpc
   
   # Now you can connect with: ssh my_hpc

Method 3: Python API
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # cluster-required: connects to and deploys a key on a live host
   from clustrix import setup_ssh_keys_with_fallback
   from clustrix.config import ClusterConfig

   config = ClusterConfig(
       cluster_type="slurm",
       cluster_host="cluster.example.edu", 
       username="your_username"
   )
   
   result = setup_ssh_keys_with_fallback(config)
   if result["success"]:
       print("✅ SSH keys setup successfully!")

What the Automation Does
------------------------

The automated SSH setup handles everything for you:

🔑 **Key Generation**
  - Creates Ed25519 keys (modern elliptic-curve signatures; fast,
    compact, and widely supported)
  - Proper file permissions (600 for private, 644 for public)
  - Informative comments with timestamps

🚀 **Key Deployment** 
  - Securely copies public key to remote cluster
  - Automatically cleans up conflicting old keys
  - Tests connection to verify success

⚙️ **SSH Configuration**
  - Updates ~/.ssh/config with cluster alias
  - Configures optimal connection settings
  - Enables easy future connections

🔒 **Security Features**
  - No plain-text credential storage
  - Automatic password clearing from memory
  - Cross-platform compatibility (Windows, macOS, Linux)

Host Key Verification (Read This Before Connecting to a New Cluster)
----------------------------------------------------------------------

This is the first thing you will hit the first time you point clustrix at a
cluster it hasn't talked to before, so it's worth understanding before it
happens to you.

Every SSH connection clustrix makes -- for key setup, for job submission, for
file transfer -- checks the remote host's SSH key against your local
``known_hosts`` files (``/etc/ssh/ssh_known_hosts`` and
``~/.ssh/known_hosts``) before doing anything else. **By default
(``ssh_host_key_policy="reject"``), a host key that isn't already recorded
there causes clustrix to refuse the connection outright.** This is not a
prompt you can click through; it is a hard failure with an actionable
message:

.. code-block:: text

   HostKeyVerificationError: Host key verification failed for 'cluster.example.edu':
   this host is not in your known_hosts file(s), so clustrix refused the
   connection rather than risk a machine-in-the-middle attack.
     Offered key: ssh-ed25519 SHA256:AbCdEf...

   To fix this:
     1. If you recognize and trust this host, add its key with:
          ssh-keyscan cluster.example.edu >> ~/.ssh/known_hosts
        then retry.
     2. If you understand the risk and want clustrix to trust unknown host
        keys automatically (NOT recommended -- this is exactly the behavior
        that enables MITM attacks), set on ClusterConfig:
          ssh_host_key_policy="auto_add"

A secure default means the first connection to any cluster needs one of:

1. Run the ``ssh-keyscan`` command the error message gives you (this is the
   same thing ``ssh`` itself would ask you to confirm interactively the
   first time you connect by hand), or
2. Already have a plain ``ssh`` connection to that host under your belt --
   if you can already ``ssh cluster.example.edu`` from this machine, its
   key is already in ``known_hosts`` and clustrix will never hit this error
   for that host, or
3. Explicitly opt out with ``ssh_host_key_policy="auto_add"`` in your
   ``ClusterConfig`` or ``configure(...)`` call -- but understand that this
   accepts whatever key a host offers, which is genuinely insecure. Only do
   this for a host you already trust through some other channel (e.g. you set
   it up yourself and typed the hostname).

   The opt-out has to come from **you**. Setting it in a ``./clustrix.yml``
   that arrived with a ``git clone``, or in a directory
   ``$CLUSTRIX_CONFIG_DIR`` happens to point at, is ignored and warned
   about: turning verification off is a security decision, and it is a
   persistent one, so it is subject to the same provenance rule as a stored
   credential. See :ref:`untrusted-security-settings`.

``auto_add`` writes what it accepts, and writes it by **appending one line**.
Clustrix creates ``~/.ssh/known_hosts`` if it does not exist yet -- the
directory at mode ``0700`` and the file at ``0600``, which is what OpenSSH
itself does before first contact -- and then appends the accepted key to it,
exactly as ``ssh-keyscan host >> ~/.ssh/known_hosts`` would. Nothing already
in the file is read back and re-emitted.

That distinction matters more than it sounds. Clustrix does *not* use
paramiko's own ``AutoAddPolicy``, which persists a key by rewriting the entire
file: it drops comments, splits a line naming several hosts, silently discards
any key type it cannot parse (``sk-ssh-ed25519@openssh.com``, which OpenSSH
reads fine), and -- if two processes do it at once, or one is interrupted --
leaves entries cut mid-key. One corrupt line is enough to make *every*
subsequent SSH connection fail, clustrix's and your own, to hosts that had
nothing to do with clustrix. Appending cannot do any of that.

What appending does not do: it is not a lock, it makes no promise on NFS, and
it cannot stop some other tool from rewriting the file. It also never removes
anything, so a host whose key genuinely changed keeps its old line -- which
changes nothing in practice, because a known host offering a changed key
raises ``BadHostKeyException`` without consulting the policy at all.

The ``reject`` policy never writes to your filesystem, since verifying is not
a reason to create anything.

The automated key setup described above obeys the same policy, and the
``ssh-copy-id`` it shells out to obeys it too: the subprocess is handed
``-o StrictHostKeyChecking=yes`` under ``reject`` and ``accept-new`` under
``auto_add``, so the one place clustrix reaches for OpenSSH cannot be more
permissive than the paramiko connections beside it. Under ``auto_add`` -- and
only then -- key setup also runs ``ssh-keyscan`` and appends the result to
your ``known_hosts``. Under the default ``reject`` it does not: it fails with
the message above, which names the exact ``ssh-keyscan`` command to run, and
trusting a new host stays your decision rather than a side effect of
deploying a key. Both the scan and ``ssh-copy-id`` are pointed at the
``known_hosts`` clustrix itself reads, with ``-o UserKnownHostsFile=``:
OpenSSH resolves ``~`` from the passwd database rather than from the
environment, so without that flag the Python half of clustrix would verify
against one file while ``ssh-copy-id`` appended to another -- which differ in
a container, under ``sudo -u``, and on a login node with a relocated home.

Key deployment is also held to the credential gate. If the ``cluster_host``
came from somewhere you did not choose -- a ``./clustrix.yml`` in a cloned
repository, say -- ``ssh-copy-id`` is additionally given
``-o IdentitiesOnly=yes``, ``-o IdentityFile=<the key being deployed>`` and
``-o IdentityAgent=none``, so OpenSSH offers that one key and neither your
default identities nor anything in your ssh-agent. For a host you chose,
nothing changes.

.. code-block:: python

   from clustrix import configure

   # Secure default: unknown keys are rejected.
   configure(cluster_type="slurm", cluster_host="cluster.example.edu")

   # Explicit opt-out -- only for hosts you already trust out-of-band.
   configure(
       cluster_type="slurm",
       cluster_host="cluster.example.edu",
       ssh_host_key_policy="auto_add",
   )

Advanced Features
-----------------

Password Fallback System
~~~~~~~~~~~~~~~~~~~~~~~~

Clustrix automatically retrieves passwords from secure sources:

**Google Colab Integration**

.. code-block:: python

   # Store password in Colab secrets (key icon 🔑 in sidebar)
   # Use key: CLUSTER_PASSWORD_HOSTNAME or CLUSTER_PASSWORD
   # Clustrix automatically retrieves it!

**Environment Variables**

.. code-block:: bash

   # Set cluster-specific password
   export CLUSTRIX_PASSWORD_CLUSTER_UNIVERSITY_EDU="your_password"
   
   # Or generic fallback
   export CLUSTER_PASSWORD="your_password"

**Interactive Prompts**
  - **Jupyter Notebooks**: GUI popup dialogs
  - **Command Line**: Secure terminal prompts
  - **Python Scripts**: Standard input prompts

Key Rotation and Management
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # cluster-required: connects to a live host (also assumes `config` from
   # the Method 3 example above)
   # Force generation of new keys (for security rotation)
   result = setup_ssh_keys_with_fallback(
       config, 
       force_refresh=True  # Removes old keys, generates fresh ones
   )
   
   # Check existing SSH keys
   from clustrix import find_ssh_keys, list_ssh_keys
   
   keys = find_ssh_keys()
   print(f"Found {len(keys)} SSH keys")
   
   # Get detailed key information
   key_info = list_ssh_keys()
   for info in key_info:
       if info["exists"]:
           print(f"Key: {info['type']} {info['bit_size']} bits")

Enterprise Cluster Support
--------------------------

University and Enterprise Clusters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Many university clusters use **Kerberos authentication**. Clustrix handles this gracefully:

.. code-block:: bash

   # Clustrix deploys SSH keys successfully, then use Kerberos for auth
   kinit your_netid@EXAMPLE.EDU
   ssh your_netid@cluster.example.edu

The SSH key deployment still succeeds and helps with file transfers and other operations.

Multi-Factor Authentication
~~~~~~~~~~~~~~~~~~~~~~~~~~~

For clusters requiring MFA:
  - SSH keys handle the cryptographic authentication
  - MFA only needed for initial login or sensitive operations
  - Reduces overall authentication friction

Configuration Integration
-------------------------

After SSH setup, configure Clustrix normally:

Python Configuration
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import configure
   
   # After automated SSH setup, just configure normally
   configure(
       cluster_type="slurm",
       cluster_host="cluster.example.edu",
       username="your_username"
       # No need to specify key_file - automatically detected!
   )

Configuration File
~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   # ~/.clustrix/config.yml
   cluster_type: "slurm"
   cluster_host: "cluster.example.edu"
   username: "your_username"
   # key_file automatically set by SSH automation
   
   default_cores: 4
   default_memory: "8GB"
   default_time: "02:00:00"
   
   module_loads:
     - "python/3.11"
     - "gcc/11.2"

Complete Workflow Example
-------------------------

Here's a complete end-to-end example:

.. code-block:: python

   # cluster-required: connects to a live host and submits a real job
   import clustrix
   from clustrix import setup_ssh_keys_with_fallback, cluster
   from clustrix.config import ClusterConfig

   # Step 1: Automated SSH setup
   config = ClusterConfig(
       cluster_type="slurm",
       cluster_host="hpc.example.edu",
       username="researcher"
   )
   
   ssh_result = setup_ssh_keys_with_fallback(config)
   if not ssh_result["success"]:
       raise Exception(f"SSH setup failed: {ssh_result['error']}")
   
   print("✅ SSH keys configured automatically!")
   
   # Step 2: Configure Clustrix
   clustrix.configure(
       cluster_type=config.cluster_type,
       cluster_host=config.cluster_host,
       username=config.username,
       default_cores=4,
       default_memory="8GB"
   )
   
   # Step 3: Use cluster computing
   @cluster(cores=8, memory="16GB", time="01:00:00")
   def scientific_computation(n_samples=1000):
       import numpy as np
       data = np.random.randn(n_samples, n_samples)
       eigenvalues = np.linalg.eigvals(data)
       return float(np.mean(eigenvalues.real))
   
   # This executes on the cluster automatically
   result = scientific_computation(n_samples=500)
   print(f"Computation result: {result}")

Manual Setup
------------

.. note::
   Prefer the automated setup above. Do the steps by hand when your site needs
   something the automation does not cover -- a non-default key type, a jump
   host, a key held on a smartcard.

The manual equivalent, step by step:

1. Generate SSH Key Pair
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Generate Ed25519 key (recommended)
   ssh-keygen -t ed25519 -f ~/.ssh/clustrix_key
   
   # Or RSA key for older systems
   ssh-keygen -t rsa -b 4096 -f ~/.ssh/clustrix_key

2. Deploy Public Key
~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Copy public key to cluster
   ssh-copy-id -i ~/.ssh/clustrix_key.pub username@cluster.example.edu

3. Configure SSH Client
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   # ~/.ssh/config
   Host my-cluster
       HostName cluster.example.edu
       User username
       IdentityFile ~/.ssh/clustrix_key
       IdentitiesOnly yes

4. Configure Clustrix
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   configure(
       cluster_type="slurm",
       cluster_host="my-cluster",
       key_file="~/.ssh/clustrix_key"
   )

Troubleshooting
---------------

Common Issues and Solutions
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**SSH Key Setup Failed**

.. code-block:: python

   # cluster-required: connects to a live host (also assumes `config` from
   # the Method 3 example above)
   # Enable debug logging
   import logging
   logging.basicConfig(level=logging.DEBUG)

   # Try setup with detailed output
   result = setup_ssh_keys_with_fallback(config)
   print(f"Detailed result: {result}")

**Kerberos Authentication Required**

.. code-block:: bash

   # This is expected for university clusters
   kinit your_netid@EXAMPLE.EDU
   ssh your_netid@cluster.example.edu

**Connection Test Failed**

.. code-block:: python

   # cluster-required: connects to a live host (also assumes `config` from
   # the Method 3 example above)
   # Try force refresh to clean up old keys
   result = setup_ssh_keys_with_fallback(
       config, 
       force_refresh=True
   )

**Permission Denied**

.. code-block:: bash

   # Check key permissions
   ls -la ~/.ssh/
   
   # Should be:
   # drwx------  ~/.ssh/
   # -rw-------  ~/.ssh/id_ed25519*
   # -rw-r--r--  ~/.ssh/id_ed25519*.pub

Security Best Practices
-----------------------

Key Management
~~~~~~~~~~~~~~

1. **Use Ed25519 Keys**: Default in automated setup; modern
   elliptic-curve signatures, preferred over RSA
2. **Regular Rotation**: Use ``force_refresh=True`` periodically  
3. **Unique Keys**: Different keys for different clusters
4. **Secure Storage**: Keys stored with proper permissions automatically

Network Security
~~~~~~~~~~~~~~~~

1. **SSH Config Aliases**: Hide hostnames, centralize settings
2. **Connection Timeouts**: Prevent hanging connections
3. **Agent Forwarding**: Only when necessary
4. **Jump Hosts**: Supported through SSH config

Monitoring
~~~~~~~~~~

.. code-block:: python

   # Monitor SSH key usage
   from clustrix import list_ssh_keys
   
   keys = list_ssh_keys()
   for key_info in keys:
       if key_info["exists"]:
           print(f"Key: {key_info['path']}")
           print(f"Type: {key_info['type']}")
           print(f"Fingerprint: {key_info['fingerprint']}")

Getting Help
------------

- **Interactive Tutorial**: `SSH Automation Notebook <https://colab.research.google.com/github/ContextLab/clustrix/blob/master/docs/ssh_key_automation_tutorial.ipynb>`_
- **GitHub Issues**: `Report problems <https://github.com/ContextLab/clustrix/issues>`_
- **Documentation**: `Read the Docs <https://clustrix.readthedocs.io>`_
- **SSH Key Automation**: `Issue #57 <https://github.com/ContextLab/clustrix/issues/57>`_

.. note::
   **Remember**: one call replaces generating, deploying and configuring a
   key by hand.