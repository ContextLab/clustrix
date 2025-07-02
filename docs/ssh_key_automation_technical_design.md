# SSH Key Automation Technical Design Document

## Issue #57: Automate SSH key setup for cluster authentication

### Document Version
- **Version**: 1.0
- **Date**: 2025-01-02
- **Author**: Clustrix Development Team
- **Status**: Draft

## Executive Summary

This document outlines the technical design for automating SSH key setup in Clustrix. The goal is to enable users to establish passwordless SSH authentication with remote clusters through a single button click in the Jupyter widget or CLI command, eliminating manual SSH key configuration.

## Problem Statement

### Current State
- Users must manually generate SSH keys, copy them to remote clusters, and configure their SSH clients
- This creates significant friction for new users
- Manual setup is error-prone and time-consuming
- Different clusters may have different SSH requirements

### Desired State
- One-click SSH key setup from Jupyter widget
- Automatic key generation, deployment, and configuration
- Seamless passwordless authentication after initial setup
- Clear feedback and error handling

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

#### 5. Widget Integration (`clustrix/notebook_magic.py`)
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
clustrix ssh-setup --host cluster.edu --user jdoe [--alias mycluster]
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
   # Use paramiko with password authentication
   client = paramiko.SSHClient()
   client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
   client.connect(hostname, username=username, password=password)
   ```

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

1. **Dartmouth Clusters (ndoli, tensor01)**:
   - Home directories: `/dartfs-hpc/rc/home/b/{username}/`
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

## Testing Strategy

### Unit Tests
- Key generation with different algorithms
- SSH config file manipulation
- Error handling for various failure modes

### Integration Tests
- Mock SSH server for deployment testing
- Paramiko connection testing
- Full workflow simulation

### Manual Testing Checklist
1. Fresh setup (no existing keys)
2. Existing non-working keys
3. Existing working keys
4. Test on SLURM cluster (ndoli)
5. Test on SSH cluster (tensor01)
6. Permission and quota issues
7. Network failure scenarios

### Validation Script
```python
# scripts/validation/test_ssh_key_automation_real_clusters.py
def validate_ssh_automation(cluster_configs: List[Dict]):
    """
    Test SSH key automation on real clusters:
    1. Clean existing keys
    2. Get password from 1Password
    3. Run setup_ssh_keys()
    4. Verify passwordless access
    5. Test with clustrix job submission
    """
```

## Success Metrics

1. **Test System Success**: Works reliably on ndoli (SLURM) and tensor01 (SSH)
2. **Time to Complete**: <30 seconds for key setup
3. **User Satisfaction**: Eliminate manual SSH configuration
4. **Reliability**: Passwordless auth works consistently after setup
5. **Error Handling**: Clear, actionable error messages for common failures

## Implementation Phases

### Phase 1: Core Functionality with Real Cluster Testing (Week 1)
- Basic key generation and deployment
- Password-based authentication
- Simple success/failure detection
- **Immediate testing on ndoli (SLURM) and tensor01 (SSH)**
- Fix issues discovered during real cluster testing

### Phase 2: Robustness and Key Rotation (Week 2)
- Comprehensive error handling
- University cluster adaptations
- Progress feedback and logging
- Implement key rotation feature (force refresh option)
- Add age-based key refresh recommendations

### Phase 3: Integration and Polish (Week 3)
- Widget UI improvements (including rotation checkbox)
- CLI command implementation
- Documentation and examples
- Multi-user/multi-key support

### Phase 4: Edge Cases and Optimization (Week 4)
- Handle edge cases discovered during testing
- Performance optimization
- Fallback strategies for unusual cluster configurations

## Design Decisions (from Open Questions)

1. **Multiple Keys**: **Yes** - Support different keys for different users. Key naming will include username to differentiate.

2. **Key Rotation**: **Yes** - Implement "force refresh" option that:
   - Deletes existing keys and deploys new ones
   - Widget checkbox for "Force key refresh"
   - Option to auto-refresh keys older than X days (configurable in widget)

3. **Backup/Recovery**: **Keep it simple** - If keys are lost, users can force a refresh to set up new ones. No complex backup needed.

4. **Team Environments**: **Solved by design** - SSH keys are stored in user home directories (`~/.ssh/authorized_keys`), so each user has their own keys. This naturally handles multi-user clusters.

## Appendix: Current Implementation Analysis

### What Works
- Basic key generation using ssh-keygen
- Widget UI with password field and button
- SSH config file updates

### What's Broken
- Key deployment fails silently in some cases
- No proper error handling for university clusters
- Connection testing gives false positives
- Password authentication fallback issues

### Root Causes
1. Incomplete error detection in deployment process
2. Assumptions about server SSH configuration
3. Insufficient testing on real university clusters
4. Missing cluster-specific adaptations

## Next Steps

1. Review and approve this technical design
2. Update GitHub issue #57 with design document
3. Implement Phase 1 with focus on Dartmouth clusters
4. Create comprehensive validation suite
5. Iterate based on real-world testing

---

**Note**: This design prioritizes user experience and reliability over advanced features. The goal is seamless, one-click SSH key setup that "just works" for the majority of users.