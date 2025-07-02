"""Enhanced Kerberos/GSSAPI authentication for Dartmouth discovery clusters."""

import os
import subprocess
import tempfile
from typing import Optional, Dict, Any
from pathlib import Path

from .config import ClusterConfig
from .auth_methods import AuthMethod, AuthResult


class KerberosAuthMethod(AuthMethod):
    """Enhanced Kerberos/GSSAPI authentication with Dartmouth-specific support."""
    
    # Kerberos-enabled clusters with their specific requirements
    KERBEROS_CLUSTERS = {
        'ndoli.dartmouth.edu': {
            'realm': 'KIEWIT.DARTMOUTH.EDU',
            'hostname': 'ndoli.dartmouth.edu',  # Can use alias
            'gssapi_auth': True,
            'gssapi_delegate': True,
            'description': 'SLURM cluster with Kerberos/GSSAPI'
        },
        'discovery.dartmouth.edu': {
            'realm': 'KIEWIT.DARTMOUTH.EDU', 
            'hostname': 'slurm-fe01-prd.dartmouth.edu',  # Real hostname required
            'gssapi_auth': True,
            'gssapi_delegate': True,
            'description': 'Discovery cluster (requires real hostname)'
        },
        'andes.dartmouth.edu': {
            'realm': 'KIEWIT.DARTMOUTH.EDU',
            'hostname': 'andes8.dartmouth.edu',
            'gssapi_auth': True,
            'gssapi_delegate': True,
            'description': 'Andes cluster'
        },
        'polaris.dartmouth.edu': {
            'realm': 'KIEWIT.DARTMOUTH.EDU',
            'hostname': 'polaris8.dartmouth.edu', 
            'gssapi_auth': True,
            'gssapi_delegate': True,
            'description': 'Polaris cluster'
        }
    }
    
    def is_applicable(self, connection_params: Dict[str, Any]) -> bool:
        """Check if Kerberos authentication is required for this cluster."""
        hostname = connection_params.get('hostname', '')
        return any(hostname.endswith(cluster) or hostname == cluster 
                  for cluster in self.KERBEROS_CLUSTERS.keys())
    
    def attempt_auth(self, connection_params: Dict[str, Any]) -> AuthResult:
        """Attempt Kerberos authentication with full GSSAPI support."""
        hostname = connection_params.get('hostname', '')
        username = connection_params.get('username', '')
        
        # Find cluster configuration
        cluster_config = None
        for cluster_pattern, config in self.KERBEROS_CLUSTERS.items():
            if hostname.endswith(cluster_pattern) or hostname == cluster_pattern:
                cluster_config = config
                break
        
        if not cluster_config:
            return AuthResult(
                success=False,
                error=f"Hostname {hostname} not recognized as Kerberos cluster"
            )
        
        print(f"🎫 Kerberos authentication required for {hostname}")
        print(f"   Realm: {cluster_config['realm']}")
        print(f"   Description: {cluster_config['description']}")
        
        # Check for valid Kerberos ticket
        if not self._has_valid_ticket():
            return AuthResult(
                success=False,
                error="No valid Kerberos ticket found",
                guidance=self._get_kinit_guidance(cluster_config['realm'], username)
            )
        
        # Ensure SSH config is set up for GSSAPI
        self._ensure_ssh_config(hostname, cluster_config)
        
        # Ensure krb5.conf is properly configured (macOS specific)
        if self._is_macos():
            self._ensure_krb5_config()
        
        try:
            # Test GSSAPI connection
            import paramiko
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Use real hostname for connection
            real_hostname = cluster_config['hostname']
            
            client.connect(
                hostname=real_hostname,
                username=username,
                port=connection_params.get('port', 22),
                gss_auth=True,
                gss_kex=True,
                timeout=10
            )
            
            # Test command execution to verify filesystem access
            stdin, stdout, stderr = client.exec_command("echo 'Kerberos auth working'")
            result = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            
            client.close()
            
            if result == "Kerberos auth working":
                print("✅ Kerberos authentication successful with filesystem access")
                return AuthResult(
                    success=True,
                    method='kerberos',
                    key_path=None  # No key needed for Kerberos
                )
            else:
                return AuthResult(
                    success=False,
                    error=f"Kerberos login succeeded but filesystem access failed: {error}",
                    guidance="This may indicate credential delegation issues. Check SSH config."
                )
                
        except Exception as e:
            return AuthResult(
                success=False,
                error=f"Kerberos authentication failed: {e}",
                guidance=self._get_troubleshooting_guidance(str(e))
            )
    
    def _has_valid_ticket(self) -> bool:
        """Check if user has a valid Kerberos ticket."""
        try:
            result = subprocess.run(['klist', '-s'], capture_output=True, timeout=5)
            return result.returncode == 0
        except:
            return False
    
    def _get_kinit_guidance(self, realm: str, username: str) -> str:
        """Get guidance for obtaining Kerberos ticket."""
        netid = username if '@' not in username else username.split('@')[0]
        
        guidance = f"""
To authenticate with Kerberos:

1. Command line:
   kinit {netid}@{realm}
   
2. Or use Ticket Viewer app on macOS
   
3. Then verify with:
   klist
   
Your ticket will be valid for several hours and can be renewed.
"""
        return guidance.strip()
    
    def _ensure_ssh_config(self, hostname: str, cluster_config: Dict[str, str]):
        """Ensure SSH config includes necessary GSSAPI settings."""
        ssh_config_path = Path.home() / '.ssh' / 'config'
        
        # Create .ssh directory if it doesn't exist
        ssh_config_path.parent.mkdir(mode=0o700, exist_ok=True)
        
        # Read existing config
        existing_config = ""
        if ssh_config_path.exists():
            existing_config = ssh_config_path.read_text()
        
        # Check if GSSAPI settings are already present
        if 'GSSAPIAuthentication yes' in existing_config and 'GSSAPIDelegateCredentials yes' in existing_config:
            print("✅ SSH config already includes GSSAPI settings")
            return
        
        # Add GSSAPI configuration
        gssapi_config = f"""
# Clustrix: GSSAPI configuration for Dartmouth clusters
Host {hostname}
    HostName {cluster_config['hostname']}
    GSSAPIAuthentication yes
    GSSAPIDelegateCredentials yes
    
# Global GSSAPI settings for all Dartmouth clusters  
Host *.dartmouth.edu
    GSSAPIAuthentication yes
    GSSAPIDelegateCredentials yes

"""
        
        # Append to SSH config
        with open(ssh_config_path, 'a') as f:
            f.write(gssapi_config)
        
        # Set proper permissions
        ssh_config_path.chmod(0o600)
        
        print(f"✅ Updated SSH config with GSSAPI settings for {hostname}")
    
    def _ensure_krb5_config(self):
        """Ensure krb5.conf is properly configured for macOS."""
        krb5_config_path = Path('/etc/krb5.conf')
        
        # Check if krb5.conf exists and has proper configuration
        if krb5_config_path.exists():
            try:
                config_content = krb5_config_path.read_text()
                if 'KIEWIT.DARTMOUTH.EDU' in config_content and 'aes256-cts-hmac-sha1-96' in config_content:
                    print("✅ krb5.conf properly configured")
                    return
            except:
                pass
        
        # Create recommended krb5.conf content
        krb5_content = """[libdefaults]
    default_realm = KIEWIT.DARTMOUTH.EDU
    permitted_enctypes = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96
    default_tgs_enctypes = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96
    default_tkt_enctypes = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96

[realms]
    KIEWIT.DARTMOUTH.EDU = {
        kdc = kerberos.dartmouth.edu
        admin_server = kerberos.dartmouth.edu
    }

[domain_realm]
    .dartmouth.edu = KIEWIT.DARTMOUTH.EDU
    dartmouth.edu = KIEWIT.DARTMOUTH.EDU
"""
        
        # Write to temporary file first
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as tmp:
            tmp.write(krb5_content)
            tmp_path = tmp.name
        
        guidance = f"""
⚠️  krb5.conf needs to be configured for proper encryption support.

A recommended configuration has been created at: {tmp_path}

To install it, run:
    sudo cp {tmp_path} /etc/krb5.conf

This fixes macOS compatibility issues with newer Kerberos servers.
"""
        
        print(guidance)
        
        return guidance
    
    def _is_macos(self) -> bool:
        """Check if running on macOS."""
        return os.uname().sysname == 'Darwin'
    
    def _get_troubleshooting_guidance(self, error_msg: str) -> str:
        """Get troubleshooting guidance based on error message."""
        guidance = []
        
        if 'GSS' in error_msg or 'GSSAPI' in error_msg:
            guidance.append("• Check that you have a valid Kerberos ticket: klist")
            guidance.append("• Verify SSH config includes GSSAPIAuthentication and GSSAPIDelegateCredentials")
            
        if 'hostname' in error_msg.lower() or 'connection' in error_msg.lower():
            guidance.append("• Ensure you're using the correct hostname (some clusters require real hostname)")
            guidance.append("• Check network connectivity to the cluster")
            
        if 'encryption' in error_msg.lower() or 'deprecated' in error_msg.lower():
            guidance.append("• Update krb5.conf with modern encryption types (see Kerberos setup)")
            
        if not guidance:
            guidance.append("• Check Kerberos ticket: klist")
            guidance.append("• Verify SSH config includes GSSAPI settings")
            guidance.append("• Contact cluster administrator if issues persist")
        
        return "\\n".join(guidance)


def setup_kerberos_for_cluster(hostname: str, username: str) -> bool:
    """
    Set up Kerberos authentication for a specific cluster.
    
    Returns True if setup was successful, False otherwise.
    """
    config = ClusterConfig(cluster_host=hostname, username=username)
    kerberos_method = KerberosAuthMethod(config)
    
    connection_params = {
        'hostname': hostname,
        'username': username,
        'port': 22
    }
    
    if not kerberos_method.is_applicable(connection_params):
        print(f"ℹ️  {hostname} does not require Kerberos authentication")
        return True
    
    print(f"🎫 Setting up Kerberos authentication for {hostname}")
    
    # Check if already working
    result = kerberos_method.attempt_auth(connection_params)
    
    if result.success:
        print("✅ Kerberos authentication already working!")
        return True
    
    print(f"⚠️  Setup needed: {result.error}")
    if result.guidance:
        print(f"\\nNext steps:")
        print(result.guidance)
    
    return False


if __name__ == '__main__':
    # Test Kerberos setup for common clusters
    clusters = ['ndoli.dartmouth.edu', 'discovery.dartmouth.edu']
    username = os.environ.get('USER', 'testuser')
    
    for cluster in clusters:
        print(f"\\n{'='*60}")
        setup_kerberos_for_cluster(cluster, username)
        print('='*60)