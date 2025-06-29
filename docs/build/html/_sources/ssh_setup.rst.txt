SSH Key Setup for Remote Clusters
====================================

Clustrix requires SSH access to remote clusters for job submission and management. This guide provides detailed instructions for setting up SSH keys with different cluster environments and authentication methods.

Overview
--------

Clustrix supports multiple authentication methods:

- **SSH Key Authentication** (Recommended): Most secure and convenient
- **Password Authentication**: Simple but less secure
- **SSH Agent**: For multiple key management
- **Custom Key Files**: For specific cluster configurations

SSH Key Authentication Setup
---------------------------

1. Generate SSH Key Pair
~~~~~~~~~~~~~~~~~~~~~~~~

Generate a new SSH key pair specifically for cluster access:

.. code-block:: bash

   # Generate RSA key (recommended for compatibility)
   ssh-keygen -t rsa -b 4096 -f ~/.ssh/clustrix_key
   
   # Or generate Ed25519 key (more secure, newer systems)
   ssh-keygen -t ed25519 -f ~/.ssh/clustrix_ed25519

**Important**: Use a strong passphrase for additional security.

2. Copy Public Key to Cluster
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upload your public key to the cluster:

.. code-block:: bash

   # Using ssh-copy-id (easiest method)
   ssh-copy-id -i ~/.ssh/clustrix_key.pub username@cluster.hostname.edu
   
   # Manual method if ssh-copy-id is not available
   cat ~/.ssh/clustrix_key.pub | ssh username@cluster.hostname.edu "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

3. Configure SSH Client
~~~~~~~~~~~~~~~~~~~~~~

Create or edit ``~/.ssh/config`` to define cluster-specific settings:

.. code-block:: text

   # Example SSH config for a SLURM cluster
   Host my-slurm-cluster
       HostName slurm.university.edu
       User myusername
       IdentityFile ~/.ssh/clustrix_key
       ForwardAgent yes
       ServerAliveInterval 60
       ServerAliveCountMax 3
   
   # Example for a PBS cluster with specific port
   Host my-pbs-cluster
       HostName pbs.cluster.org
       Port 2222
       User researcher
       IdentityFile ~/.ssh/clustrix_key
       ProxyJump gateway.cluster.org
   
   # Example for SGE cluster with compression
   Host my-sge-cluster
       HostName sge.hpc.gov
       User scientist
       IdentityFile ~/.ssh/clustrix_key
       Compression yes
       TCPKeepAlive yes

4. Test SSH Connection
~~~~~~~~~~~~~~~~~~~~

Verify your SSH setup works correctly:

.. code-block:: bash

   # Test basic connection
   ssh my-slurm-cluster "hostname && whoami"
   
   # Test specific commands that Clustrix will use
   ssh my-slurm-cluster "which sbatch && squeue --version"

Clustrix Configuration
---------------------

Configure Clustrix to use your SSH setup:

Python Configuration
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import configure
   
   # Using SSH config host alias (recommended)
   configure(
       cluster_type="slurm",
       cluster_host="my-slurm-cluster",  # matches SSH config
       username="myusername",           # optional if in SSH config
   )
   
   # Using specific key file
   configure(
       cluster_type="pbs", 
       cluster_host="pbs.cluster.org",
       username="researcher",
       key_file="~/.ssh/clustrix_key"
   )
   
   # Using password authentication (not recommended)
   configure(
       cluster_type="sge",
       cluster_host="sge.hpc.gov", 
       username="scientist",
       password="your_password"  # Use environment variable instead
   )

Configuration File
~~~~~~~~~~~~~~~~~

Create ``~/.clustrix/config.yml``:

.. code-block:: yaml

   # SLURM cluster with SSH key
   cluster_type: "slurm"
   cluster_host: "my-slurm-cluster"
   username: "myusername"
   key_file: "~/.ssh/clustrix_key"
   remote_work_dir: "/scratch/myusername/clustrix"
   
   # Default resource settings
   default_cores: 4
   default_memory: "8GB"
   default_time: "02:00:00"
   
   # Environment setup
   module_loads:
     - "python/3.11"
     - "gcc/11.2"
   
   environment_variables:
     OMP_NUM_THREADS: "4"

Environment Variables
~~~~~~~~~~~~~~~~~~~

Set environment variables for sensitive information:

.. code-block:: bash

   # In your shell profile (~/.bashrc, ~/.zshrc)
   export CLUSTRIX_HOST="slurm.university.edu"
   export CLUSTRIX_USERNAME="myusername"
   export CLUSTRIX_KEY_FILE="~/.ssh/clustrix_key"
   
   # For password auth (not recommended in scripts)
   export CLUSTRIX_PASSWORD="your_password"

Advanced SSH Configurations
---------------------------

SSH Agent Integration
~~~~~~~~~~~~~~~~~~~

For managing multiple keys and passphrases:

.. code-block:: bash

   # Start SSH agent
   eval "$(ssh-agent -s)"
   
   # Add your cluster key
   ssh-add ~/.ssh/clustrix_key
   
   # Verify keys are loaded
   ssh-add -l

Configure Clustrix to use SSH agent:

.. code-block:: python

   configure(
       cluster_type="slurm",
       cluster_host="my-cluster",
       username="myuser"
       # No key_file specified - will use SSH agent
   )

Jump Hosts and Bastion Servers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For clusters behind firewalls:

.. code-block:: text

   # SSH config with jump host
   Host cluster-gateway
       HostName gateway.cluster.org
       User myuser
       IdentityFile ~/.ssh/gateway_key
   
   Host cluster-internal
       HostName internal.cluster.local
       User myuser  
       IdentityFile ~/.ssh/cluster_key
       ProxyJump cluster-gateway

.. code-block:: python

   # Clustrix configuration for jump host setup
   configure(
       cluster_type="slurm",
       cluster_host="cluster-internal",  # Uses SSH config
       username="myuser"
   )

Multiple Cluster Management
~~~~~~~~~~~~~~~~~~~~~~~~~

Manage multiple clusters with different keys:

.. code-block:: python

   # Define cluster configurations
   clusters = {
       "slurm_cluster": {
           "cluster_type": "slurm",
           "cluster_host": "slurm.university.edu",
           "key_file": "~/.ssh/slurm_key"
       },
       "pbs_cluster": {
           "cluster_type": "pbs", 
           "cluster_host": "pbs.research.org",
           "key_file": "~/.ssh/pbs_key"
       }
   }
   
   # Switch between clusters
   from clustrix import configure
   
   # Use SLURM cluster
   configure(**clusters["slurm_cluster"])
   
   @cluster(cores=8)
   def slurm_task():
       return "Running on SLURM"
   
   # Switch to PBS cluster
   configure(**clusters["pbs_cluster"])
   
   @cluster(cores=4)
   def pbs_task():
       return "Running on PBS"

Security Best Practices
-----------------------

Key Management
~~~~~~~~~~~~

1. **Use Strong Passphrases**: Always protect private keys with passphrases
2. **Separate Keys per Cluster**: Use different keys for different environments
3. **Regular Key Rotation**: Replace keys periodically (every 6-12 months)
4. **Backup Keys Securely**: Store encrypted backups of important keys

File Permissions
~~~~~~~~~~~~~~

Ensure correct SSH file permissions:

.. code-block:: bash

   # Set correct permissions
   chmod 700 ~/.ssh
   chmod 600 ~/.ssh/config
   chmod 600 ~/.ssh/clustrix_key
   chmod 644 ~/.ssh/clustrix_key.pub
   chmod 600 ~/.ssh/authorized_keys  # on remote cluster

Network Security
~~~~~~~~~~~~~~

1. **Use SSH Config**: Centralize connection settings
2. **Enable Compression**: For large data transfers
3. **Configure Timeouts**: Prevent hanging connections
4. **Use Port Forwarding**: For additional services when needed

Troubleshooting
--------------

Common Issues
~~~~~~~~~~~

**Connection Refused**

.. code-block:: bash

   # Check if SSH service is running
   ssh -v username@cluster.hostname.edu
   
   # Test different ports
   ssh -p 2222 username@cluster.hostname.edu

**Permission Denied**

.. code-block:: bash

   # Verify key permissions
   ls -la ~/.ssh/
   
   # Check authorized_keys on remote
   ssh username@cluster.hostname.edu "ls -la ~/.ssh/authorized_keys"
   
   # Debug SSH connection
   ssh -vvv username@cluster.hostname.edu

**Clustrix Connection Errors**

.. code-block:: python

   # Test Clustrix SSH connection
   from clustrix.executor import ClusterExecutor
   from clustrix.config import get_config
   
   config = get_config()
   executor = ClusterExecutor(config)
   
   try:
       executor.connect()
       print("SSH connection successful!")
       executor.disconnect()
   except Exception as e:
       print(f"Connection failed: {e}")

**Debug Mode**

Enable verbose logging for troubleshooting:

.. code-block:: python

   import logging
   logging.basicConfig(level=logging.DEBUG)
   
   from clustrix import configure, cluster
   
   configure(
       cluster_type="slurm",
       cluster_host="my-cluster",
       username="myuser"
   )

Cluster-Specific Setup
--------------------

SLURM Clusters
~~~~~~~~~~~~

Typical SLURM cluster requirements:

.. code-block:: python

   configure(
       cluster_type="slurm",
       cluster_host="slurm.hpc.edu",
       username="researcher", 
       key_file="~/.ssh/slurm_key",
       remote_work_dir="/scratch/researcher/jobs",
       module_loads=["python/3.11", "gcc/11"],
       default_partition="compute"
   )

PBS/Torque Clusters
~~~~~~~~~~~~~~~~

PBS cluster configuration:

.. code-block:: python

   configure(
       cluster_type="pbs",
       cluster_host="pbs.cluster.org",
       username="scientist",
       key_file="~/.ssh/pbs_key", 
       remote_work_dir="/home/scientist/clustrix",
       default_queue="normal"
   )

SGE Clusters
~~~~~~~~~~

Sun Grid Engine setup:

.. code-block:: python

   configure(
       cluster_type="sge",
       cluster_host="sge.grid.edu",
       username="user",
       key_file="~/.ssh/sge_key",
       remote_work_dir="/tmp/user/clustrix"
   )

Complete Example
---------------

Here's a complete working example:

.. code-block:: bash

   # 1. Generate SSH key
   ssh-keygen -t rsa -b 4096 -f ~/.ssh/my_cluster_key
   
   # 2. Copy to cluster
   ssh-copy-id -i ~/.ssh/my_cluster_key.pub myuser@cluster.university.edu
   
   # 3. Create SSH config
   echo "Host my-cluster
       HostName cluster.university.edu
       User myuser
       IdentityFile ~/.ssh/my_cluster_key" >> ~/.ssh/config
   
   # 4. Test connection
   ssh my-cluster "hostname"

.. code-block:: python

   # 5. Configure Clustrix
   from clustrix import configure, cluster
   
   configure(
       cluster_type="slurm",
       cluster_host="my-cluster",
       remote_work_dir="/scratch/myuser/clustrix"
   )
   
   # 6. Use Clustrix
   @cluster(cores=4, memory="8GB")
   def compute_task(n):
       return sum(i**2 for i in range(n))
   
   # This will execute on the remote cluster
   result = compute_task(1000)
   print(f"Result: {result}")

This setup provides secure, reliable SSH access for Clustrix to manage your cluster computing jobs.