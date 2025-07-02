# Technical Design: Enhanced Authentication Methods for Cluster Access (Issue #66)

## Overview

This document outlines the technical design for implementing enhanced authentication methods in Clustrix, addressing issue #66. The design focuses on providing seamless authentication fallbacks, 1Password integration, and Kerberos/GSSAPI support for enterprise clusters.

## Goals

1. **Seamless Authentication**: Users should be able to connect to clusters without manual intervention when possible
2. **Security**: Credentials should be stored and handled securely, leveraging 1Password when available
3. **Flexibility**: Support multiple authentication methods with intelligent fallback mechanisms
4. **User Experience**: Clear feedback and guidance when authentication requires user action
5. **Continuous Validation**: Validate on real clusters (tensor01.dartmouth.edu and ndoli.dartmouth.edu) from the first implementation step

## Current State

### Existing Components

1. **SSH Key Management** (`clustrix/ssh_utils.py`)
   - Automatic SSH key generation and deployment
   - Key rotation capabilities
   - SSH config file management

2. **Authentication Fallbacks** (`clustrix/auth_fallbacks.py`)
   - Environment-aware password retrieval
   - Support for Colab, notebooks, CLI, and scripts

3. **Secure Credentials** (`clustrix/secure_credentials.py`)
   - 1Password CLI integration
   - Secure storage patterns
   - Currently used only in validation scripts

4. **Widget Interface** (`clustrix/notebook_magic.py`)
   - Cluster configuration widget with SSH setup
   - Password field for initial setup

## Proposed Architecture

### Authentication Flow

```
┌─────────────────────┐
│   User Connection   │
│      Request        │
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Auth Requirements  │
│     Detection       │
└──────────┬──────────┘
           │
           v
┌─────────────────────┐     ┌─────────────────────┐
│   SSH Key Auth      │────>│     Success?        │
└─────────────────────┘     └──────┬──────────────┘
                                   │ No
                                   v
┌─────────────────────┐     ┌─────────────────────┐
│  Kerberos/GSSAPI    │────>│     Success?        │
│   (if required)     │     └──────┬──────────────┘
└─────────────────────┘            │ No
                                   v
┌─────────────────────┐     ┌─────────────────────┐
│  1Password Lookup   │────>│     Success?        │
│  (if configured)    │     └──────┬──────────────┘
└─────────────────────┘            │ No
                                   v
┌─────────────────────┐     ┌─────────────────────┐
│ Environment Variable │────>│     Success?        │
│  (if configured)    │     └──────┬──────────────┘
└─────────────────────┘            │ No
                                   v
┌─────────────────────┐     ┌─────────────────────┐
│   Widget Password   │────>│     Success?        │
│      Check          │     └──────┬──────────────┘
└─────────────────────┘            │ No
                                   v
┌─────────────────────┐     ┌─────────────────────┐
│  Interactive Prompt │────>│  Offer 1Password    │
│  (CLI/GUI based)   │     │      Storage        │
└─────────────────────┘     └─────────────────────┘
```

### Component Design

#### 1. Enhanced Configuration with Environment Variable Support

Update `clustrix/config.py`:

```python
@dataclass
class ClusterConfig:
    """Enhanced cluster configuration with auth options"""
    # Existing fields...
    
    # Authentication options
    use_1password: bool = False
    onepassword_note: str = ""
    
    use_env_password: bool = False
    password_env_var: str = ""  # Name of environment variable containing password
    
    # Other auth settings
    cache_credentials: bool = True
    credential_cache_ttl: int = 300  # 5 minutes
    
    def get_env_password(self) -> Optional[str]:
        """Get password from specified environment variable"""
        if self.use_env_password and self.password_env_var:
            return os.environ.get(self.password_env_var)
        return None
```

#### 2. Enhanced Widget with Dynamic Fields

Update `clustrix/notebook_magic.py`:

```python
def create_cluster_widget(config: ClusterConfig = None) -> widgets.VBox:
    """Create enhanced cluster configuration widget with dynamic auth options"""
    
    # Basic cluster configuration fields
    cluster_type = widgets.Dropdown(
        options=['slurm', 'pbs', 'sge', 'kubernetes', 'ssh', 'local'],
        value=config.cluster_type if config else 'ssh',
        description='Cluster Type:'
    )
    
    hostname = widgets.Text(
        value=config.cluster_host if config else '',
        placeholder='cluster.university.edu',
        description='Hostname:'
    )
    
    username = widgets.Text(
        value=config.username if config else os.getenv('USER', ''),
        placeholder='username',
        description='Username:'
    )
    
    # Password field for immediate use/SSH setup
    password_input = widgets.Password(
        value='',
        placeholder='Password for SSH setup',
        description='Password:',
    )
    
    # Authentication Options Section
    auth_header = widgets.HTML(
        value='<h4>Authentication Options</h4>'
    )
    
    # 1Password Option
    use_1password = widgets.Checkbox(
        value=config.use_1password if config else False,
        description='Use 1Password',
        style={'description_width': 'initial'}
    )
    
    onepassword_note = widgets.Text(
        value=config.onepassword_note if config else '',
        placeholder='e.g., clustrix-tensor01 (optional)',
        description='Note name:',
        layout=widgets.Layout(display='none')  # Hidden by default
    )
    
    # Environment Variable Option
    use_env_password = widgets.Checkbox(
        value=config.use_env_password if config else False,
        description='Use Environment Variable',
        style={'description_width': 'initial'}
    )
    
    password_env_var = widgets.Text(
        value=config.password_env_var if config else '',
        placeholder='e.g., CLUSTER_PASSWORD',
        description='Variable name:',
        layout=widgets.Layout(display='none')  # Hidden by default
    )
    
    # SSH Key Setup
    setup_button = widgets.Button(
        description='Setup SSH Keys',
        button_style='primary',
        tooltip='Generate and deploy SSH keys to the cluster'
    )
    
    force_refresh = widgets.Checkbox(
        value=False,
        description='Force key refresh',
        tooltip='Replace existing SSH keys'
    )
    
    # Status displays
    auth_status = widgets.HTML(
        value='<i>Authentication not configured</i>'
    )
    
    status_output = widgets.Output()
    
    # Dynamic field visibility handlers
    def on_1password_toggle(change):
        """Show/hide 1Password note field"""
        if change['new']:
            onepassword_note.layout.display = 'flex'
            # Check 1Password availability
            if not SecureCredentialManager.is_available():
                auth_status.value = '<span style="color: orange">⚠️ 1Password CLI not found - install with: brew install 1password-cli</span>'
                use_1password.value = False
                onepassword_note.layout.display = 'none'
            else:
                auth_status.value = '<span style="color: green">✅ 1Password enabled</span>'
        else:
            onepassword_note.layout.display = 'none'
            update_auth_status()
    
    def on_env_toggle(change):
        """Show/hide environment variable field"""
        if change['new']:
            password_env_var.layout.display = 'flex'
        else:
            password_env_var.layout.display = 'none'
        update_auth_status()
    
    def update_auth_status():
        """Update authentication status display"""
        methods = []
        if use_1password.value:
            methods.append("1Password")
        if use_env_password.value:
            methods.append("Environment Variable")
        
        if methods:
            auth_status.value = f'<span style="color: green">Auth methods: {", ".join(methods)}</span>'
        else:
            auth_status.value = '<i>Using standard authentication</i>'
    
    # Attach observers
    use_1password.observe(on_1password_toggle, names='value')
    use_env_password.observe(on_env_toggle, names='value')
    
    def on_setup_ssh_keys(b):
        """Enhanced SSH setup with authentication fallback"""
        with status_output:
            status_output.clear_output()
            
            # Update config with current widget values
            config = ClusterConfig(
                cluster_type=cluster_type.value,
                cluster_host=hostname.value,
                username=username.value,
                use_1password=use_1password.value,
                onepassword_note=onepassword_note.value,
                use_env_password=use_env_password.value,
                password_env_var=password_env_var.value
            )
            
            # Initialize auth manager
            auth_manager = AuthenticationManager(config)
            
            # Set widget password if provided
            if password_input.value:
                auth_manager.set_widget_password(password_input.value)
            
            print(f"🔐 Setting up SSH keys for {username.value}@{hostname.value}")
            
            # Get password through auth chain
            password = auth_manager.get_password_for_setup()
            
            if not password:
                print("❌ Could not obtain password for SSH setup")
                return
            
            # Validate on real cluster immediately
            print("\n🧪 Validating authentication on cluster...")
            if validate_cluster_auth(config, password):
                print("✅ Authentication validated successfully!")
            else:
                print("⚠️  Authentication validation failed - continuing with setup anyway")
            
            # Continue with SSH setup
            try:
                result = setup_ssh_keys(
                    hostname=hostname.value,
                    username=username.value,
                    password=password,
                    key_type='ed25519',  # Default to secure key type
                    force_refresh=force_refresh.value
                )
                
                # Clear password field for security
                password_input.value = ''
                
                # Update status
                auth_status.value = '<span style="color: green">✅ SSH keys configured successfully</span>'
                
                # Validate SSH key auth works
                print("\n🧪 Validating SSH key authentication...")
                if validate_ssh_key_auth(config):
                    print("✅ SSH key authentication working!")
                else:
                    print("⚠️  SSH key authentication not working yet")
                
            except Exception as e:
                print(f"❌ SSH setup failed: {e}")
                auth_status.value = f'<span style="color: red">❌ Setup failed: {e}</span>'
    
    setup_button.on_click(on_setup_ssh_keys)
    
    # Layout with sections
    basic_section = widgets.VBox([
        widgets.HTML('<h4>Cluster Configuration</h4>'),
        cluster_type,
        hostname,
        username,
        password_input
    ])
    
    auth_section = widgets.VBox([
        auth_header,
        use_1password,
        onepassword_note,
        use_env_password,
        password_env_var,
        auth_status
    ])
    
    ssh_section = widgets.VBox([
        widgets.HTML('<h4>SSH Key Setup</h4>'),
        widgets.HBox([setup_button, force_refresh]),
        status_output
    ])
    
    # Return complete widget
    return widgets.VBox([
        basic_section,
        auth_section,
        ssh_section
    ])
```

#### 3. Enhanced Authentication Manager with Environment Variable Support

Create `clustrix/auth_manager.py`:

```python
class AuthenticationManager:
    """Unified authentication management with configurable methods"""
    
    def __init__(self, config: ClusterConfig):
        self.config = config
        self.credential_manager = SecureCredentialManager() if config.use_1password else None
        self.widget_password = None
        self.password_cache = {}  # Cache with TTL
    
    def get_password_for_setup(self) -> Optional[str]:
        """Get password for SSH key setup using configured methods"""
        # Try methods in order based on configuration
        
        # 1. Check widget password first (immediate use)
        if self.widget_password:
            password = self.widget_password
            self.widget_password = None  # Clear after use
            return password
        
        # 2. Try 1Password if configured
        if self.config.use_1password:
            password = self._try_1password()
            if password:
                return password
        
        # 3. Try environment variable if configured
        if self.config.use_env_password:
            password = self._try_env_var()
            if password:
                return password
        
        # 4. Fall back to interactive prompt
        return self._interactive_prompt()
    
    def _try_1password(self) -> Optional[str]:
        """Try to get password from 1Password"""
        if not self.credential_manager or not self.credential_manager.is_available():
            print("⚠️  1Password CLI not available")
            return None
        
        try:
            note_name = self.config.onepassword_note
            if not note_name:
                # Try default patterns
                note_name = f"clustrix-{self.config.cluster_host}"
            
            print(f"🔐 Checking 1Password for '{note_name}'...")
            
            credentials = self.credential_manager.get_credentials(note_name)
            password = self._extract_password(credentials)
            
            if password:
                print("✅ Found password in 1Password")
                return password
            else:
                print(f"⚠️  No password found in 1Password note '{note_name}'")
                
        except Exception as e:
            print(f"⚠️  1Password lookup failed: {e}")
        
        return None
    
    def _try_env_var(self) -> Optional[str]:
        """Try to get password from environment variable"""
        if not self.config.password_env_var:
            return None
        
        print(f"🔐 Checking environment variable '{self.config.password_env_var}'...")
        
        password = os.environ.get(self.config.password_env_var)
        if password:
            print(f"✅ Found password in ${self.config.password_env_var}")
            return password
        else:
            print(f"⚠️  Environment variable ${self.config.password_env_var} not set")
        
        return None
    
    def _interactive_prompt(self) -> Optional[str]:
        """Prompt user for password interactively"""
        env_type = detect_environment()
        hostname = self.config.cluster_host
        username = self.config.username
        
        prompt = f"Password for {username}@{hostname}: "
        
        print(f"\n🔐 Please enter password for {username}@{hostname}")
        
        if env_type == 'notebook' and not is_colab():
            # Use GUI popup for notebooks
            password = self._gui_password_prompt(prompt)
        elif env_type in ['cli', 'script', 'terminal']:
            # Use getpass for CLI
            password = getpass.getpass(prompt)
        elif env_type == 'colab':
            # Google Colab special handling
            from google.colab import auth
            password = auth.getpass(prompt)
        else:
            # Fallback to basic input
            password = getpass.getpass(prompt)
        
        if password and self.config.use_1password:
            # Offer to store in 1Password
            self._offer_1password_storage(password)
        
        return password
    
    def _offer_1password_storage(self, password: str):
        """Offer to store password in 1Password"""
        if not self.credential_manager or not self.credential_manager.is_available():
            return
        
        # Check if already stored
        note_name = f"clustrix-{self.config.cluster_host}"
        try:
            existing = self.credential_manager.get_credentials(note_name)
            if existing:
                return  # Already stored
        except:
            pass  # Not found, can proceed
        
        # Prompt user
        env_type = detect_environment()
        
        if env_type == 'notebook':
            # GUI prompt
            import tkinter as tk
            from tkinter import messagebox
            
            root = tk.Tk()
            root.withdraw()
            result = messagebox.askyesno(
                "Store in 1Password?",
                f"Would you like to store the password for {self.config.username}@{self.config.cluster_host} in 1Password?"
            )
            root.destroy()
            
            should_store = result
        else:
            # Terminal prompt
            response = input("\nStore password in 1Password for future use? [y/N]: ")
            should_store = response.lower() in ['y', 'yes']
        
        if should_store:
            self._store_in_1password(password, note_name)
    
    def _store_in_1password(self, password: str, note_name: str):
        """Store password in 1Password"""
        note_content = f"""Clustrix SSH Cluster Credentials
- hostname: {self.config.cluster_host}
- username: {self.config.username}
- password: {password}
- created: {datetime.now().isoformat()}

This note was automatically created by Clustrix for secure cluster access.
"""
        
        try:
            self.credential_manager.create_secure_note(note_name, note_content)
            print(f"✅ Password stored in 1Password as '{note_name}'")
            
            # Update config
            self.config.onepassword_note = note_name
            self.config.save()
            
        except Exception as e:
            print(f"❌ Failed to store in 1Password: {e}")
```

#### 4. Real Cluster Validation Framework

Create `clustrix/validation.py`:

```python
"""Real cluster validation utilities"""

def validate_cluster_auth(config: ClusterConfig, password: str = None) -> bool:
    """Validate authentication works on real cluster"""
    try:
        # Try to establish SSH connection
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Try password auth if provided
        if password:
            client.connect(
                hostname=config.cluster_host,
                username=config.username,
                password=password,
                timeout=10,
                look_for_keys=False  # Don't try keys yet
            )
            print(f"✅ Password authentication successful to {config.cluster_host}")
            client.close()
            return True
            
    except paramiko.AuthenticationException:
        print(f"❌ Password authentication failed to {config.cluster_host}")
        return False
    except Exception as e:
        print(f"⚠️  Connection error: {e}")
        return False

def validate_ssh_key_auth(config: ClusterConfig) -> bool:
    """Validate SSH key authentication works"""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Try SSH key auth
        client.connect(
            hostname=config.cluster_host,
            username=config.username,
            timeout=10,
            look_for_keys=True,
            allow_agent=True
        )
        
        # Run simple command to verify
        stdin, stdout, stderr = client.exec_command("echo 'SSH key auth working'")
        result = stdout.read().decode().strip()
        
        client.close()
        
        if result == "SSH key auth working":
            return True
            
    except Exception as e:
        print(f"SSH key auth error: {e}")
        
    return False

def validate_kerberos_auth(config: ClusterConfig) -> bool:
    """Validate Kerberos authentication if applicable"""
    # Check if this is a Kerberos-enabled cluster
    kerberos_clusters = ['ndoli.dartmouth.edu', 'discovery.dartmouth.edu']
    
    if not any(config.cluster_host.endswith(cluster) for cluster in kerberos_clusters):
        return True  # Not a Kerberos cluster
    
    try:
        # Check for valid ticket
        result = subprocess.run(['klist', '-s'], capture_output=True)
        if result.returncode != 0:
            print("⚠️  No valid Kerberos ticket - run 'kinit' first")
            return False
        
        # Try GSSAPI connection
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        client.connect(
            hostname=config.cluster_host,
            username=config.username,
            gss_auth=True,
            gss_kex=True,
            timeout=10
        )
        
        print(f"✅ Kerberos authentication successful to {config.cluster_host}")
        client.close()
        return True
        
    except Exception as e:
        print(f"❌ Kerberos authentication failed: {e}")
        return False
```

#### 5. CLI Interface Enhancement

Update CLI argument parsing:

```python
def add_auth_arguments(parser):
    """Add authentication-related arguments to parser"""
    auth_group = parser.add_argument_group('authentication options')
    
    auth_group.add_argument(
        '--use-1password',
        action='store_true',
        help='Use 1Password for credential storage'
    )
    
    auth_group.add_argument(
        '--1password-note',
        type=str,
        help='1Password secure note name containing credentials'
    )
    
    auth_group.add_argument(
        '--use-env-password',
        action='store_true',
        help='Use environment variable for password'
    )
    
    auth_group.add_argument(
        '--password-env-var',
        type=str,
        default='CLUSTRIX_PASSWORD',
        help='Environment variable containing password (default: CLUSTRIX_PASSWORD)'
    )
    
    auth_group.add_argument(
        '--no-interactive',
        action='store_true',
        help='Disable interactive password prompts (for CI/CD)'
    )
```

## Implementation Plan with Continuous Validation

### Phase 1: Core Infrastructure with Validation (Week 1)

1. **Day 1-2: Basic Framework**
   - Create `AuthenticationManager` class
   - Implement environment variable support
   - Create validation framework
   - **Validate**: Test password auth on tensor01.dartmouth.edu

2. **Day 3-4: Widget Enhancement**
   - Add dynamic checkbox/field UI
   - Implement widget password handling
   - **Validate**: Test widget flow on tensor01

3. **Day 5: Integration**
   - Connect auth manager to executor
   - Test complete flow
   - **Validate**: End-to-end test on both tensor01 and ndoli

### Phase 2: 1Password Integration (Week 2)

1. **Day 1-2: Core 1Password**
   - Extend secure credentials for auth
   - Implement note parsing
   - **Validate**: Test 1Password retrieval on tensor01

2. **Day 3-4: Storage Flow**
   - Implement storage offering
   - Add config persistence
   - **Validate**: Test storage and retrieval cycle

3. **Day 5: Polish**
   - Error handling
   - User feedback
   - **Validate**: Complete 1Password flow on both clusters

### Phase 3: Advanced Features (Week 3)

1. **Day 1-2: Kerberos Support**
   - Detect Kerberos requirements
   - Implement GSSAPI auth
   - **Validate**: Test on ndoli.dartmouth.edu

2. **Day 3-4: Fallback Chain**
   - Complete auth chain implementation
   - Add proper logging
   - **Validate**: Test all fallback scenarios

3. **Day 5: CLI Integration**
   - Add CLI arguments
   - Test non-interactive mode
   - **Validate**: Test CLI on both clusters

### Phase 4: Testing & Documentation (Week 4)

1. **Day 1-2: Unit Tests**
   - Mock-based unit tests
   - Coverage requirements
   - **Validate**: Run test suite

2. **Day 3-4: Integration Tests**
   - Real cluster validation suite
   - Performance testing
   - **Validate**: Full test suite on both clusters

3. **Day 5: Documentation**
   - User guides
   - API documentation
   - **Validate**: Follow docs to set up from scratch

## Validation Scripts

### Continuous Validation Script

```python
#!/usr/bin/env python3
# scripts/validate_auth_continuous.py

"""Continuous validation for authentication implementation"""

import os
import sys
from clustrix import ClusterConfig
from clustrix.auth_manager import AuthenticationManager
from clustrix.validation import (
    validate_cluster_auth,
    validate_ssh_key_auth,
    validate_kerberos_auth
)

# Test clusters
TEST_CLUSTERS = [
    {
        'name': 'tensor01',
        'host': 'tensor01.dartmouth.edu',
        'type': 'ssh',
        'simple_auth': True,
        'kerberos': False
    },
    {
        'name': 'ndoli',
        'host': 'ndoli.dartmouth.edu', 
        'type': 'slurm',
        'simple_auth': False,
        'kerberos': True
    }
]

def validate_implementation(feature_name: str):
    """Validate a feature implementation on all test clusters"""
    print(f"\n{'='*60}")
    print(f"Validating: {feature_name}")
    print('='*60)
    
    for cluster in TEST_CLUSTERS:
        print(f"\n🧪 Testing on {cluster['name']} ({cluster['host']})")
        
        config = ClusterConfig(
            cluster_type=cluster['type'],
            cluster_host=cluster['host'],
            username=os.environ.get('USER'),
            use_1password=True,
            use_env_password=True,
            password_env_var='CLUSTER_PASSWORD'
        )
        
        # Test authentication methods
        auth_manager = AuthenticationManager(config)
        
        # Test password auth if applicable
        if cluster['simple_auth']:
            password = auth_manager.get_password_for_setup()
            if password:
                if validate_cluster_auth(config, password):
                    print("✅ Password authentication working")
                else:
                    print("❌ Password authentication failed")
        
        # Test SSH key auth
        if validate_ssh_key_auth(config):
            print("✅ SSH key authentication working")
        else:
            print("⚠️  SSH key authentication not set up")
        
        # Test Kerberos if applicable
        if cluster['kerberos']:
            if validate_kerberos_auth(config):
                print("✅ Kerberos authentication working")
            else:
                print("⚠️  Kerberos authentication not available")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        feature_name = ' '.join(sys.argv[1:])
    else:
        feature_name = "Current Implementation"
    
    validate_implementation(feature_name)
```

## Testing Strategy

### Widget Manual Testing Requirements

**CRITICAL**: All widget behaviors must be manually tested in a Jupyter notebook environment by a human user. This includes:

1. **Visual Appearance Testing**
   - Verify checkboxes render correctly
   - Confirm text fields appear/disappear as expected
   - Check layout and spacing is appropriate
   - Validate all labels and descriptions are clear

2. **Interactive Behavior Testing**
   - Test checkbox toggling shows/hides text fields
   - Verify password masking works properly
   - Check status messages update correctly
   - Test button interactions work as expected

3. **Authentication Flow Testing**
   - Test SSH setup with different auth method combinations
   - Verify error messages are user-friendly
   - Check success feedback is clear and informative
   - Test widget state persistence during operations

**Human Validation Process**: After each widget implementation phase, a human user must:
- Load the widget in a Jupyter notebook
- Test all interactive elements
- Report back on visual appearance and behavior
- Confirm user experience meets expectations
- Identify any issues with layout, functionality, or clarity

This manual testing is essential because widget rendering and behavior can vary significantly across different Jupyter environments and cannot be fully validated through automated testing alone.

### Unit Tests with Validation

```python
class TestAuthenticationManager:
    """Test authentication manager with real validation"""
    
    def test_env_var_password(self):
        """Test environment variable password retrieval"""
        os.environ['TEST_CLUSTER_PASS'] = 'testpass123'
        
        config = ClusterConfig(
            cluster_host='tensor01.dartmouth.edu',
            username='testuser',
            use_env_password=True,
            password_env_var='TEST_CLUSTER_PASS'
        )
        
        auth_manager = AuthenticationManager(config)
        password = auth_manager._try_env_var()
        
        assert password == 'testpass123'
        
        # Clean up
        del os.environ['TEST_CLUSTER_PASS']
    
    @pytest.mark.integration
    def test_real_cluster_auth(self):
        """Test authentication on real cluster (requires credentials)"""
        if not os.environ.get('CLUSTRIX_TEST_REAL_CLUSTERS'):
            pytest.skip("Real cluster tests not enabled")
        
        config = ClusterConfig(
            cluster_host='tensor01.dartmouth.edu',
            username=os.environ.get('USER'),
            use_env_password=True,
            password_env_var='TENSOR01_PASSWORD'
        )
        
        # Should work if env var is set correctly
        assert validate_cluster_auth(config)
```

## Security Considerations

1. **Password Handling**
   - Never log passwords in any form
   - Clear passwords from memory after use
   - Use secure input methods (getpass, masked widgets)
   - Validate on real clusters using secure connections

2. **Environment Variables**
   - Only read from explicitly specified variables
   - No scanning of all environment variables
   - Clear documentation of which variable is used

3. **1Password Integration**
   - Require explicit user consent
   - Validate 1Password CLI authenticity
   - Use secure note format with clear structure

## Success Metrics

1. **Functionality**
   - All auth methods work on tensor01.dartmouth.edu
   - Kerberos auth works on ndoli.dartmouth.edu
   - Seamless fallback between methods

2. **User Experience**
   - Clear widget UI with dynamic fields
   - Helpful error messages at each step
   - Successful authentication on first attempt

3. **Security**
   - No password leaks in logs or errors
   - Secure storage in 1Password
   - Proper environment variable handling

## Conclusion

This enhanced design provides a complete authentication solution with:
- User-specified environment variable support
- Dynamic widget UI with checkboxes and conditional fields
- Continuous validation on real clusters from day one
- Comprehensive fallback chain with clear user feedback
- Secure password handling throughout the system

The implementation plan ensures that every feature is validated on both tensor01.dartmouth.edu (simple SSH) and ndoli.dartmouth.edu (Kerberos/GSSAPI) before moving to the next phase.