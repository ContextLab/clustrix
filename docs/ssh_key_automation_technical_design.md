# SSH Key Automation Technical Design Document

## Issue #57: Automate SSH key setup for cluster authentication

**Status: implemented.** The architecture described here is in the codebase;
see "What is built" at the end for the module-by-module mapping. The one
proposed piece that was not built is `detect_cluster_requirements`, noted at
the point it appears. `tests/test_ssh_automation.py` holds 14 unit tests for
this path, and
`tests/real_world/cluster_validation/test_ssh_key_automation_real_clusters.py`
exercises it against real hosts.

**📖 Try the interactive [SSH Key Automation Tutorial](ssh_key_automation_tutorial.ipynb)** [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/ContextLab/clustrix/blob/master/docs/ssh_key_automation_tutorial.ipynb)

## Summary

Clustrix sets up passwordless SSH to a cluster from a single button in the
Jupyter widget, or a single command line. The user supplies a password once;
after that, key-based authentication carries every connection.

## What this replaces

Without it, a user generates a key pair by hand, copies the public half to the
cluster, fixes permissions on both ends, and edits `~/.ssh/config`. Each of
those steps has its own way of failing quietly — a key in the wrong file, a
directory mode of 755, a config entry that names the wrong key — and the
failure shows up much later as an unexplained password prompt. Clusters differ
in what they will accept, so the correct sequence is not the same everywhere.

The automated path does the same work, checks that it worked by opening a
passwordless connection, and says what went wrong when it did not.

## User Workflow

### Initial Setup Flow
1. User creates ClusterConfig with hostname and username
2. User enters password in widget (one-time only)
3. User clicks "Setup SSH Keys" button
4. Clustrix:
   - Generates SSH key pair (if needed)
   - Connects to cluster using password
   - Deploys public key to `~/.ssh/authorized_keys`
   - Updates local SSH config
   - Tests passwordless connection
5. User receives confirmation of successful setup

### Subsequent Usage Flow
1. User creates ClusterConfig with hostname, username, and key_file path
2. Clustrix uses SSH key for all connections
3. No password prompts or user interaction required

## Technical Architecture

### Components

#### 1. SSH Key Management (`clustrix/ssh_utils.py`)
```python
def setup_ssh_keys(
    config: ClusterConfig,
    password: str,
    cluster_alias: Optional[str] = None,
    key_type: str = "ed25519",
    force_refresh: bool = False,
    auto_refresh_days: Optional[int] = None
) -> Dict[str, Any]:
    """
    Main entry point for SSH key automation.
    
    Returns:
        {
            "success": bool,
            "key_path": str,           # Path to private key
            "key_already_existed": bool,
            "key_deployed": bool,
            "connection_tested": bool,
            "error": Optional[str],
            "details": Dict[str, Any]
        }
    """
```

#### 2. Key Detection and Validation
```python
def detect_working_ssh_key(hostname: str, username: str, port: int = 22) -> Optional[str]:
    """Check if any existing SSH key already works for this host."""
    
def validate_ssh_key(hostname: str, username: str, key_path: str, port: int = 22) -> bool:
    """Verify that a specific SSH key enables passwordless authentication."""
```

#### 3. Key Generation
```python
def generate_ssh_key_pair(
    key_name: str,
    key_type: str = "ed25519",
    key_dir: Path = Path.home() / ".ssh"
) -> Tuple[str, str]:
    """Generate new SSH key pair with proper permissions."""
```

#### 4. Key Deployment
```python
def deploy_ssh_key(
    hostname: str,
    username: str,
    password: str,
    public_key_path: str,
    port: int = 22
) -> bool:
    """Deploy public key to remote authorized_keys using password auth."""
```

#### 5. Widget Integration (`clustrix/notebook_magic_widget.py`, `clustrix/modern_notebook_widget.py`)
- Password input field (secure, masked)
- "Setup SSH Keys" button
- Progress indicator during setup
- Success/failure feedback
- Automatic config update with key_file path
- **Key Rotation Options**:
  - Checkbox: "Force refresh SSH keys"
  - Number input: "Auto-refresh keys older than [30] days"
  - Display: Current key age (if exists)

#### 6. CLI Integration (`clustrix/cli.py`)
```bash
clustrix ssh-setup --host cluster.example.edu --user user \
    [--port 22] [--alias mycluster] [--key-type ed25519|rsa] [--force-refresh]
```

## Implementation Details

### SSH Key Generation Strategy

1. **Key Naming Convention**:
   ```
   ~/.ssh/id_ed25519_clustrix_{username}_{hostname}
   ~/.ssh/id_ed25519_clustrix_{username}_{hostname}.pub
   ```
   - Includes username to support multiple users per cluster
   - Timestamp stored in key comment for age tracking

2. **Key Type Selection**:
   - Default: Ed25519 (modern, secure, fast)
   - Fallback: RSA 4096-bit (for older systems)
   - Auto-detect based on server capabilities

### Secure Key Deployment Process

1. **Initial Connection**:
   ```python
   # paramiko with password authentication, host keys verified
   client = paramiko.SSHClient()
   configure_host_key_policy(client, config)
   client.connect(hostname, username=username, password=password)
   ```

   `clustrix.ssh_security.configure_host_key_policy` is the single
   implementation every call site uses, and its default policy is to reject an
   unknown host key. `client.set_missing_host_key_policy(paramiko.AutoAddPolicy())`
   trusts whatever key is offered on first contact and must not appear
   anywhere in the codebase; the opt-out, for someone who has decided they
   want it, is `ClusterConfig(ssh_host_key_policy="auto_add")`.

2. **Remote Setup Commands**:
   ```bash
   # Ensure .ssh directory exists with correct permissions
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   
   # Backup existing authorized_keys if present
   [ -f ~/.ssh/authorized_keys ] && cp ~/.ssh/authorized_keys ~/.ssh/authorized_keys.backup
   
   # Append new public key
   echo "ssh-ed25519 AAAA... user@clustrix" >> ~/.ssh/authorized_keys
   
   # Fix permissions
   chmod 600 ~/.ssh/authorized_keys
   ```

3. **Verification**:
   - Immediately test passwordless connection
   - Rollback on failure (restore backup)

### Error Handling and Edge Cases

#### Common Failure Scenarios

1. **SSH Key Already Works**
   - Detection: Try connecting with existing keys first
   - Action: Skip generation, update config, inform user

2. **Permission Denied During Deployment**
   - Cause: Incorrect password, account locked, 2FA required
   - Action: Clear error message, retry option

3. **Server Restrictions**
   - Cause: SSH keys disabled, specific key types required
   - Action: Detect via server banner/errors, provide guidance

4. **Network Issues**
   - Cause: Firewall, VPN required, connection timeout
   - Action: Appropriate timeouts, retry logic, clear errors

5. **Existing authorized_keys Issues**
   - Cause: File permissions, disk quota, corrupted file
   - Action: Backup, fix permissions, handle quota errors

### Security Considerations

1. **Password Handling**:
   - Never store passwords
   - Clear from memory after use
   - Use secure input methods (getpass, widget masking)

2. **Key Storage**:
   - Enforce 600 permissions on private keys
   - Store in standard ~/.ssh directory
   - Never transmit private keys

3. **Connection Security**:
   - Verify host keys (with user prompt on first connection)
   - Use secure ciphers and key exchange algorithms
   - Implement connection timeouts

### University Cluster Considerations

#### Known Requirements

1. **Test Clusters (hpc2, gpu)**:
   - Home directories: `/remote/home/{username}/`
   - May require module loads before Python
   - Shared filesystem across compute nodes

2. **Common Restrictions**:
   - Some clusters require keys registered via web portal
   - IP-based access restrictions
   - Mandatory 2FA for certain operations
   - Specific SSH key algorithms required

#### Detection and Adaptation

```python
def detect_cluster_requirements(hostname: str) -> Dict[str, Any]:
    """Auto-detect cluster-specific SSH requirements."""
    return {
        "requires_rsa": False,  # Some old clusters don't support Ed25519
        "home_directory": None,  # Custom home directory path
        "requires_web_registration": False,  # Keys must be registered externally
        "max_key_length": None,  # Some clusters limit key size
    }
```

This piece was proposed and not built — there is no
`detect_cluster_requirements` in `clustrix/`. Key type is chosen by the
`key_type` argument rather than probed.

## Testing Strategy

### Unit Tests
- Key generation with different algorithms
- SSH config file manipulation
- Error handling for various failure modes

### Integration Tests
- Deployment against the real SSH server container in
  `tests/infrastructure/docker-compose.yml`
- Paramiko connection testing against that container
- The full workflow, end to end, with no step stubbed out

### Manual Testing Checklist
1. Fresh setup (no existing keys)
2. Existing non-working keys
3. Existing working keys
4. Test on SLURM cluster (hpc2)
5. Test on SSH cluster (gpu)
6. Permission and quota issues
7. Network failure scenarios

### Validation Script
```python
# tests/real_world/cluster_validation/test_ssh_key_automation_real_clusters.py
def validate_ssh_automation(cluster_configs: List[Dict]):
    """
    Test SSH key automation on real clusters:
    1. Clean existing keys
    2. Get password from environment variables
    3. Run setup_ssh_keys()
    4. Verify passwordless access
    5. Test with clustrix job submission
    """
```

## Success Metrics

1. **Test System Success**: Works reliably on hpc2 (SLURM) and gpu (SSH)
2. **Time to Complete**: <30 seconds for key setup
3. **User Satisfaction**: Eliminate manual SSH configuration
4. **Reliability**: Passwordless auth works consistently after setup
5. **Error Handling**: Clear, actionable error messages for common failures

## What is built

Everything in "Technical Architecture" above, apart from
`detect_cluster_requirements`, exists in `clustrix/`:

| Piece | Where |
|-|-|
| `setup_ssh_keys(config, password, cluster_alias, key_type, force_refresh, auto_refresh_days)` | `clustrix/ssh_utils.py` |
| `detect_working_ssh_key`, `validate_ssh_key`, `detect_existing_ssh_key` | `clustrix/ssh_utils.py` |
| `generate_ssh_key_pair`, `generate_ssh_key` | `clustrix/ssh_utils.py` |
| `deploy_ssh_key`, `deploy_public_key`, `update_ssh_config` | `clustrix/ssh_utils.py` |
| `setup_ssh_keys_with_fallback` | `clustrix/ssh_utils.py` |
| "Setup SSH Keys" button | `clustrix/notebook_magic_widget.py`, `clustrix/modern_notebook_widget.py` |
| `clustrix ssh-setup` | `clustrix/cli.py` |

The public helpers each take an optional `config: ClusterConfig`, which is how
the host key policy reaches paramiko.

## Design decisions

**Multiple keys.** Supported. Key names include the username, so one machine
can hold distinct keys for distinct accounts on the same cluster.

**Key rotation.** `force_refresh=True` discards the existing key and deploys a
new one; `auto_refresh_days` sets an age past which the key is replaced
without being asked. The widget exposes both.

**Backup and recovery.** Deliberately absent. A lost key is replaced by
forcing a refresh, which is cheaper than any backup scheme worth maintaining.

**Team environments.** Handled by the filesystem: keys live in each user's own
`~/.ssh/authorized_keys` on the cluster, so nothing is shared and nothing
collides.
