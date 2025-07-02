# SSH Key Automation Tutorial

This tutorial demonstrates how to use Clustrix's automated SSH key setup feature to quickly and securely connect to remote clusters.

## Overview

SSH key automation in Clustrix eliminates the manual process of generating SSH keys, deploying them to remote clusters, and configuring SSH settings. Instead of spending 15-30 minutes on manual setup, you can achieve secure cluster access in under 15 seconds with a single command or button click.

## Benefits

- **⚡ Speed**: Setup in <15 seconds vs 15-30 minutes manually
- **🔒 Security**: Ed25519 keys with proper permissions (600/644)
- **🎯 Simplicity**: One-click setup in Jupyter, single CLI command
- **🧹 Cleanup**: Automatic removal of conflicting old keys
- **🔄 Rotation**: Force refresh to generate new keys
- **🌐 Cross-platform**: Works on Windows, macOS, Linux

## Quick Start

### Method 1: Jupyter Widget (Recommended)

If you're using Clustrix in a Jupyter notebook, the simplest way is using the interactive widget:

```python
import clustrix

# The widget will automatically appear when you import clustrix
# Look for the "SSH Key Setup" section in the widget interface
```

1. Enter your cluster hostname (e.g., `cluster.university.edu`)
2. Enter your username
3. Enter your password
4. Optionally check "Force refresh SSH keys" to generate new keys
5. Click "Setup SSH Keys"

The widget will show real-time progress and success/error messages.

### Method 2: CLI Command

For command-line usage, use the `clustrix ssh-setup` command:

```bash
# Basic setup
clustrix ssh-setup --host cluster.university.edu --user your_username

# With custom port and alias
clustrix ssh-setup --host cluster.university.edu --user your_username --port 2222 --alias my_cluster

# Force generation of new keys
clustrix ssh-setup --host cluster.university.edu --user your_username --force-refresh

# Use RSA keys instead of Ed25519 (not recommended)
clustrix ssh-setup --host cluster.university.edu --user your_username --key-type rsa
```

You'll be prompted for your password securely.

### Method 3: Python API

For programmatic access or custom integrations:

```python
from clustrix import setup_ssh_keys_with_fallback
from clustrix.config import ClusterConfig

# Create cluster configuration
config = ClusterConfig(
    cluster_type="slurm",
    cluster_host="cluster.university.edu", 
    username="your_username"
)

# Setup SSH keys with automatic fallback
result = setup_ssh_keys_with_fallback(
    config=config,
    password="your_password",  # Optional - will prompt if not provided
    cluster_alias="my_cluster",  # Optional - creates SSH config alias
    key_type="ed25519",  # Optional - default is ed25519
    force_refresh=False,  # Optional - set True to generate new keys
)

# Check results
if result["success"]:
    print(f"✅ SSH keys setup successfully!")
    print(f"Key path: {result['key_path']}")
    print(f"Connection tested: {result['connection_tested']}")
else:
    print(f"❌ Setup failed: {result['error']}")
```

## Advanced Features

### Password Fallback System

Clustrix includes an intelligent password fallback system that works across different environments:

#### Google Colab
Store your cluster password in Colab secrets:
1. Click the key icon (🔑) in the Colab sidebar
2. Add a secret with key `CLUSTER_PASSWORD_HOSTNAME` or similar
3. Clustrix will automatically retrieve it

```python
# Colab will automatically use stored secrets
result = setup_ssh_keys_with_fallback(config)
```

#### Local Environment Variables
Set environment variables for automatic password retrieval:

```bash
# Set cluster-specific password
export CLUSTRIX_PASSWORD_CLUSTER_UNIVERSITY_EDU="your_password"

# Or generic fallback
export CLUSTER_PASSWORD="your_password"
```

#### Interactive Environments
- **Jupyter Notebooks**: GUI popup dialogs or widget input
- **CLI**: Secure terminal password prompts via `getpass`
- **Python Scripts**: Standard input prompts

### SSH Key Management

#### Key Naming Convention
Generated keys follow a consistent naming pattern:
```
~/.ssh/id_ed25519_clustrix_{username}_{cluster_alias}
```

Examples:
- `id_ed25519_clustrix_researcher_my_cluster`
- `id_ed25519_clustrix_john_hpc_cluster`

#### Key Rotation and Cleanup
```python
# Force generation of new keys (removes old ones)
result = setup_ssh_keys_with_fallback(
    config, 
    force_refresh=True
)

# Automatic cleanup of conflicting Clustrix keys
# Old keys are backed up and removed before deploying new ones
```

#### SSH Config Management
When using aliases, SSH config entries are automatically created:

```ssh-config
# ~/.ssh/config (auto-generated)
Host my_cluster
    HostName cluster.university.edu
    User researcher
    IdentityFile ~/.ssh/id_ed25519_clustrix_researcher_my_cluster
    IdentitiesOnly yes
```

You can then connect simply with:
```bash
ssh my_cluster
```

## Troubleshooting

### Common Issues

#### 1. Kerberos/GSSAPI Authentication
Some enterprise clusters (like university HPC systems) use Kerberos authentication:

```
❌ SSH key setup successful but connection test failed
```

**Solution**: This is expected behavior. Use password authentication for such clusters:
```bash
# For Kerberos clusters, use kinit first
kinit your_netid@UNIVERSITY.EDU
ssh your_netid@cluster.university.edu
```

#### 2. Multiple Key Conflicts
If you have existing SSH keys causing conflicts:

```
❌ Permission denied (publickey)
```

**Solution**: Use force refresh to clean up conflicting keys:
```python
setup_ssh_keys_with_fallback(config, force_refresh=True)
```

#### 3. Connection Timeouts
```
❌ SSH key deployed successfully but connection test failed
```

**Solution**: The key may need time to propagate. Try connecting manually:
```bash
ssh -i ~/.ssh/id_ed25519_clustrix_user_cluster user@cluster.host
```

#### 4. Home Directory Issues
```
❌ Failed to deploy public key
```

**Solution**: Check if your home directory path is correct:
```python
config = ClusterConfig(
    cluster_host="cluster.host",
    username="user",
    remote_work_dir="/correct/path/to/home"  # Specify correct path
)
```

### Debugging

Enable verbose logging to see detailed information:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Now run SSH key setup with detailed logs
result = setup_ssh_keys_with_fallback(config)
```

## Security Best Practices

### Key Types
- **Ed25519** (default): Modern, secure, fast
- **RSA**: Traditional, widely supported (use 4096-bit minimum)

```python
# Use Ed25519 (recommended)
setup_ssh_keys_with_fallback(config, key_type="ed25519")

# Use RSA if Ed25519 not supported
setup_ssh_keys_with_fallback(config, key_type="rsa")
```

### Permissions
Clustrix automatically sets secure permissions:
- Private keys: `600` (read/write for owner only)
- Public keys: `644` (readable by all)
- SSH directory: `700` (accessible by owner only)

### Key Comments
Generated keys include informative comments:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... researcher@cluster.edu (generated by Clustrix on 2025-07-02 10:30:45)
```

## Integration Examples

### Automated Workflows
```python
from clustrix import setup_ssh_keys_with_fallback, cluster
from clustrix.config import ClusterConfig

def setup_and_run_job():
    # Setup SSH keys automatically
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="hpc.university.edu",
        username="researcher"
    )
    
    ssh_result = setup_ssh_keys_with_fallback(config)
    if not ssh_result["success"]:
        raise Exception(f"SSH setup failed: {ssh_result['error']}")
    
    # Now run your cluster job
    @cluster(cores=16, memory="32GB")
    def my_computation(data):
        # Your computation here
        return process_data(data)
    
    result = my_computation(my_data)
    return result
```

### Batch Cluster Setup
```python
clusters = [
    {"host": "cluster1.edu", "user": "researcher"},
    {"host": "cluster2.edu", "user": "researcher"}, 
    {"host": "cluster3.edu", "user": "researcher"},
]

for cluster_info in clusters:
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=cluster_info["host"],
        username=cluster_info["user"]
    )
    
    result = setup_ssh_keys_with_fallback(config, cluster_alias=f"cluster_{cluster_info['host'].split('.')[0]}")
    print(f"✅ {cluster_info['host']}: {'Success' if result['success'] else 'Failed'}")
```

## Enterprise Cluster Support

### University HPC Systems
Many university clusters use Kerberos authentication. SSH key automation will deploy keys successfully, but you'll need Kerberos tickets for authentication:

```bash
# Setup Kerberos ticket
kinit your_netid@UNIVERSITY.EDU

# Now you can connect (keys help with file transfers)
ssh your_netid@cluster.university.edu
```

### Multi-Factor Authentication
For clusters requiring MFA, SSH keys reduce authentication steps:
1. SSH keys handle the cryptographic authentication
2. MFA only needed for initial login or sensitive operations

### Shared Clusters
On shared/multi-user clusters, each user gets their own key:
- Keys are user-specific and isolated
- No interference with other users' SSH configurations
- Automatic cleanup prevents key conflicts

## API Reference

### setup_ssh_keys_with_fallback()

Complete SSH key setup with automatic password fallback.

```python
setup_ssh_keys_with_fallback(
    config: ClusterConfig,
    password: Optional[str] = None,
    cluster_alias: Optional[str] = None,
    key_type: str = "ed25519", 
    force_refresh: bool = False,
    auto_refresh_days: Optional[int] = None
) -> Dict[str, Any]
```

**Parameters:**
- `config`: ClusterConfig object with cluster details
- `password`: Password for initial SSH connection (optional)
- `cluster_alias`: Alias name for SSH config entry (optional)
- `key_type`: Type of key to generate (`"ed25519"` or `"rsa"`)
- `force_refresh`: Force generation of new keys (default: False)
- `auto_refresh_days`: Auto-refresh keys older than X days (future feature)

**Returns:**
```python
{
    "success": bool,
    "key_path": str,
    "key_already_existed": bool,
    "key_deployed": bool, 
    "connection_tested": bool,
    "error": Optional[str],
    "details": Dict[str, Any]
}
```

### CLI Reference

```bash
clustrix ssh-setup --help
```

**Options:**
- `--host`: Cluster hostname (required)
- `--user`: Username for cluster access (required)
- `--port`: SSH port (default: 22)
- `--alias`: Alias name for SSH config entry
- `--key-type`: SSH key type (`ed25519` or `rsa`)
- `--force-refresh`: Force generation of new keys

## Next Steps

After setting up SSH keys:

1. **Configure Clustrix** for your cluster:
   ```python
   clustrix.configure(
       cluster_type='slurm',
       cluster_host='cluster.university.edu',
       username='your_username'
   )
   ```

2. **Start using the @cluster decorator**:
   ```python
   @cluster(cores=4, memory="8GB")
   def my_function(data):
       return process(data)
   ```

3. **Explore advanced features**:
   - Cloud provider integration
   - Kubernetes deployment
   - Cost monitoring
   - Filesystem utilities

## Support

For issues or questions:
- Check the [troubleshooting section](#troubleshooting) above
- Review logs with debug logging enabled
- Open an issue on [GitHub](https://github.com/ContextLab/clustrix/issues)
- Check existing [SSH key automation discussions](https://github.com/ContextLab/clustrix/issues/57)

---

**✨ That's it! You now have secure, automated SSH access to your clusters in under 15 seconds. Happy computing! 🚀**