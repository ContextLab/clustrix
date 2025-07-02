# Enhanced Authentication Methods for Cluster Access

## Problem Statement

While SSH key automation (Issue #57) works perfectly for SSH-based clusters, many enterprise and university clusters use alternative authentication methods like Kerberos/GSSAPI. Additionally, users need fallback options when SSH key setup fails.

### Current Limitations:
- Kerberos/GSSAPI clusters (like Dartmouth Discovery) require manual authentication
- No password fallback when SSH key setup fails
- Limited authentication options for different environments

## Proposed Solution

Implement a comprehensive authentication system with multiple fallback methods and support for enterprise authentication protocols.

### 1. Password Fallback System

Add automatic password fallback when SSH key authentication fails, with environment-specific handling:

```python
def get_cluster_password(config: ClusterConfig) -> Optional[str]:
    """Get cluster password with environment-specific fallbacks."""
    
    # 1. Colab environment - use secrets
    if in_colab():
        from google.colab import userdata
        try:
            return userdata.get(f'CLUSTER_PASSWORD_{config.cluster_host}')
        except:
            pass
    
    # 2. Local environment - check environment variables
    password = os.getenv(f'CLUSTRIX_PASSWORD_{config.cluster_host.upper().replace(".", "_")}')
    if password:
        return password
    
    # 3. Interactive fallbacks
    if in_notebook():
        # GUI popup for notebook environments
        return get_password_gui(f"Password for {config.cluster_host}")
    elif in_cli():
        # Terminal input for CLI
        return getpass.getpass(f"Password for {config.cluster_host}: ")
    else:
        # Python script fallback
        return input(f"Password for {config.cluster_host}: ")
```

### 2. Kerberos/GSSAPI Authentication Support

Add detection and setup for Kerberos-based clusters:

```python
def setup_kerberos_auth(config: ClusterConfig) -> Dict[str, Any]:
    """Set up Kerberos authentication for enterprise clusters."""
    
    # Detect if cluster requires Kerberos
    if requires_kerberos(config.cluster_host):
        return {
            "auth_method": "kerberos",
            "instructions": [
                f"Run: kinit {config.username}@{get_kerberos_realm(config.cluster_host)}",
                "Then use: ssh -K {config.cluster_host}"
            ],
            "ssh_config_updates": {
                "GSSAPIAuthentication": "yes",
                "GSSAPIDelegateCredentials": "yes"
            }
        }
```

### 3. Enhanced Authentication Detection

Automatically detect cluster authentication requirements:

```python
def detect_auth_methods(hostname: str) -> List[str]:
    """Detect supported authentication methods for cluster."""
    methods = []
    
    # Test SSH capabilities
    ssh_banner = get_ssh_banner(hostname)
    if "gssapi" in ssh_banner.lower():
        methods.append("kerberos")
    if "publickey" in ssh_banner.lower():
        methods.append("ssh_key")
    if "password" in ssh_banner.lower():
        methods.append("password")
    
    return methods
```

## Implementation Plan

### Phase 1: Password Fallback System
- [ ] Implement environment-specific password retrieval
- [ ] Add GUI password prompt for notebook environments
- [ ] Add CLI password prompts
- [ ] Update cluster connection logic to use password fallbacks

### Phase 2: Kerberos/GSSAPI Support
- [ ] Add Kerberos detection for known university clusters
- [ ] Implement SSH config updates for GSSAPI
- [ ] Add user guidance for `kinit` authentication
- [ ] Create Kerberos authentication workflow

### Phase 3: Advanced Authentication Methods
- [ ] **1Password Integration**: Automated credential retrieval
- [ ] **Encrypted File Storage**: Secure local credential storage
- [ ] **Passkey Support**: Modern passwordless authentication
- [ ] **Multi-factor Authentication**: Support for 2FA requirements

### Phase 4: Unified Authentication System
- [ ] Create authentication method priority system
- [ ] Add automatic fallback between methods
- [ ] Implement authentication caching (where secure)
- [ ] Add authentication status dashboard

## Technical Details

### Environment Detection:
```python
def detect_environment():
    """Detect current execution environment."""
    if 'google.colab' in sys.modules:
        return 'colab'
    elif 'ipykernel' in sys.modules:
        return 'notebook'
    elif sys.stdin.isatty():
        return 'cli'
    else:
        return 'script'
```

### Secure Password Handling:
```python
def get_password_gui(prompt: str) -> Optional[str]:
    """Get password via GUI popup in notebook environment."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
        
        root = tk.Tk()
        root.withdraw()  # Hide main window
        password = simpledialog.askstring("Cluster Password", prompt, show='*')
        root.destroy()
        return password
    except ImportError:
        # Fallback to ipywidgets
        return get_password_widget(prompt)
```

### Authentication Priority:
1. SSH keys (if available and working)
2. Kerberos/GSSAPI (if detected)
3. 1Password integration (if configured)
4. Environment variables
5. Interactive password prompt

## Success Criteria

1. **Seamless Fallbacks**: Users can always authenticate, even if SSH keys fail
2. **Enterprise Compatibility**: Support for Kerberos/GSSAPI clusters
3. **Environment Awareness**: Appropriate authentication method for each environment
4. **Security**: No credential storage in plain text, proper cleanup
5. **User Experience**: Clear guidance and minimal friction

## Benefits

- **Universal Cluster Support**: Works with SSH, Kerberos, and hybrid clusters
- **Enhanced User Experience**: Automatic fallbacks reduce friction
- **Enterprise Ready**: Supports university and corporate authentication systems
- **Security**: Multiple secure credential management options
- **Flexibility**: Adapts to different execution environments

This enhancement would make Clustrix compatible with virtually any cluster authentication system while maintaining security and ease of use.