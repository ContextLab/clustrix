SSH Key Setup for Remote Clusters
====================================

Clustrix provides **automated SSH key setup** that transforms the traditional 15-30 minute manual process into a **15-second automated experience**. This feature eliminates the complexity of SSH configuration while maintaining security best practices.

.. note::
   **🚀 New in Clustrix**: Automated SSH key setup makes cluster access effortless! 
   Try the interactive tutorial: `SSH Key Automation Tutorial <https://colab.research.google.com/github/ContextLab/clustrix/blob/master/docs/ssh_key_automation_tutorial.ipynb>`_

Quick Start: Automated Setup
-----------------------------

The easiest way to set up SSH access is using Clustrix's automated system:

Method 1: Interactive Widget (Recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import clustrix
   
   # The widget appears automatically with SSH Key Setup section
   # 1. Enter your cluster hostname (e.g., cluster.university.edu)
   # 2. Enter your username  
   # 3. Enter your password
   # 4. Click "Setup SSH Keys"
   # ✅ Done in 15 seconds!

Method 2: Command Line Interface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Basic automated setup
   clustrix ssh-setup --host cluster.university.edu --user your_username
   
   # With custom alias for easy access
   clustrix ssh-setup --host cluster.university.edu --user your_username --alias my_hpc
   
   # Now you can connect with: ssh my_hpc

Method 3: Python API
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import setup_ssh_keys_with_fallback
   from clustrix.config import ClusterConfig
   
   config = ClusterConfig(
       cluster_type="slurm",
       cluster_host="cluster.university.edu", 
       username="your_username"
   )
   
   result = setup_ssh_keys_with_fallback(config)
   if result["success"]:
       print("✅ SSH keys setup successfully!")

What the Automation Does
-----------------------

The automated SSH setup handles everything for you:

🔑 **Key Generation**
  - Creates Ed25519 keys (quantum-resistant, modern encryption)
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
   kinit your_netid@UNIVERSITY.EDU
   ssh your_netid@cluster.university.edu

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
       cluster_host="cluster.university.edu",
       username="your_username"
       # No need to specify key_file - automatically detected!
   )

Configuration File
~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   # ~/.clustrix/config.yml
   cluster_type: "slurm"
   cluster_host: "cluster.university.edu"
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

   import clustrix
   from clustrix import setup_ssh_keys_with_fallback, cluster
   from clustrix.config import ClusterConfig
   
   # Step 1: Automated SSH setup
   config = ClusterConfig(
       cluster_type="slurm",
       cluster_host="hpc.university.edu",
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

Manual Setup (Legacy)
---------------------

.. warning::
   **Manual setup is no longer recommended**. Use the automated SSH setup above for better security and convenience.

If you need manual setup for special configurations:

1. Generate SSH Key Pair
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Generate Ed25519 key (recommended)
   ssh-keygen -t ed25519 -f ~/.ssh/clustrix_key
   
   # Or RSA key for older systems
   ssh-keygen -t rsa -b 4096 -f ~/.ssh/clustrix_key

2. Deploy Public Key
~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Copy public key to cluster
   ssh-copy-id -i ~/.ssh/clustrix_key.pub username@cluster.hostname.edu

3. Configure SSH Client
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   # ~/.ssh/config
   Host my-cluster
       HostName cluster.hostname.edu
       User username
       IdentityFile ~/.ssh/clustrix_key
       IdentitiesOnly yes

4. Configure Clustrix
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   configure(
       cluster_type="slurm",
       cluster_host="my-cluster",
       key_file="~/.ssh/clustrix_key"
   )

Troubleshooting
--------------

Common Issues and Solutions
~~~~~~~~~~~~~~~~~~~~~~~~~~

**SSH Key Setup Failed**

.. code-block:: python

   # Enable debug logging
   import logging
   logging.basicConfig(level=logging.DEBUG)
   
   # Try setup with detailed output
   result = setup_ssh_keys_with_fallback(config)
   print(f"Detailed result: {result}")

**Kerberos Authentication Required**

.. code-block:: bash

   # This is expected for university clusters
   kinit your_netid@UNIVERSITY.EDU
   ssh your_netid@cluster.university.edu

**Connection Test Failed**

.. code-block:: python

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
~~~~~~~~~~~~

1. **Use Ed25519 Keys**: Default in automated setup, quantum-resistant
2. **Regular Rotation**: Use ``force_refresh=True`` periodically  
3. **Unique Keys**: Different keys for different clusters
4. **Secure Storage**: Keys stored with proper permissions automatically

Network Security
~~~~~~~~~~~~~~~

1. **SSH Config Aliases**: Hide hostnames, centralize settings
2. **Connection Timeouts**: Prevent hanging connections
3. **Agent Forwarding**: Only when necessary
4. **Jump Hosts**: Supported through SSH config

Monitoring
~~~~~~~~~

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
-----------

- **Interactive Tutorial**: `SSH Automation Notebook <https://colab.research.google.com/github/ContextLab/clustrix/blob/master/docs/ssh_key_automation_tutorial.ipynb>`_
- **GitHub Issues**: `Report problems <https://github.com/ContextLab/clustrix/issues>`_
- **Documentation**: `Read the Docs <https://clustrix.readthedocs.io>`_
- **SSH Key Automation**: `Issue #57 <https://github.com/ContextLab/clustrix/issues/57>`_

.. note::
   **Remember**: 15 seconds of automation beats 15-30 minutes of manual setup! 🚀