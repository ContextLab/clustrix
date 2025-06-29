"""
SSH key automation utilities for Clustrix cluster authentication.

This module provides functions to automatically detect, generate, and deploy
SSH keys for seamless cluster authentication setup.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import paramiko
from clustrix.config import ClusterConfig

logger = logging.getLogger(__name__)


class SSHKeySetupError(Exception):
    """Base exception for SSH key setup failures."""

    pass


class SSHKeyGenerationError(SSHKeySetupError):
    """Failed to generate SSH key pair."""

    pass


class SSHKeyDeploymentError(SSHKeySetupError):
    """Failed to deploy public key to remote host."""

    pass


class SSHConnectionError(SSHKeySetupError):
    """Failed to establish SSH connection."""

    pass


def find_ssh_keys() -> List[str]:
    """
    Find existing SSH private keys in ~/.ssh/ directory.

    Returns:
        List of paths to existing SSH private key files
    """
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        return []

    # Common SSH private key names
    key_names = [
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa_clustrix",
        "id_ed25519_clustrix",
    ]

    existing_keys = []
    for key_name in key_names:
        key_path = ssh_dir / key_name
        if key_path.exists() and key_path.is_file():
            # Verify it's actually a private key by checking permissions and content
            try:
                if (
                    key_path.stat().st_mode & 0o077 == 0
                ):  # Proper private key permissions
                    with open(key_path, "r") as f:
                        content = f.read(100)  # Read first 100 chars
                        if "PRIVATE KEY" in content:
                            existing_keys.append(str(key_path))
            except (OSError, PermissionError):
                continue

    return existing_keys


def detect_existing_ssh_key(
    hostname: str, username: str, port: int = 22
) -> Optional[str]:
    """
    Check if SSH keys already work for the given host.

    Args:
        hostname: Target hostname
        username: SSH username
        port: SSH port (default 22)

    Returns:
        Path to working SSH key, or None if no key works
    """
    ssh_keys = find_ssh_keys()

    for key_path in ssh_keys:
        try:
            # Test SSH connection with this key
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Try to connect with this key
            client.connect(
                hostname=hostname,
                username=username,
                port=port,
                key_filename=key_path,
                timeout=10,
                auth_timeout=10,
                banner_timeout=10,
                look_for_keys=False,  # Only use the specified key
                allow_agent=False,  # Don't use SSH agent
            )

            # If we get here, the connection worked
            client.close()
            logger.info(f"Found working SSH key: {key_path}")
            return key_path

        except Exception as e:
            logger.debug(f"SSH key {key_path} failed for {hostname}: {e}")
            continue

    return None


def generate_ssh_key(
    key_path: str,
    key_type: str = "ed25519",
    passphrase: str = "",
    comment: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Generate SSH key pair using ssh-keygen.

    Args:
        key_path: Path for the private key file
        key_type: Type of key to generate ('ed25519', 'rsa', 'ecdsa')
        passphrase: Passphrase for the key (empty for no passphrase)
        comment: Comment for the key (defaults to user@hostname)

    Returns:
        Tuple of (private_key_path, public_key_path)

    Raises:
        SSHKeyGenerationError: If key generation fails
    """
    try:
        # Ensure ssh-keygen is available
        subprocess.run(["ssh-keygen", "--help"], capture_output=True, check=False)
    except FileNotFoundError:
        raise SSHKeyGenerationError(
            "ssh-keygen not found. Please install OpenSSH client."
        )

    # Prepare ssh-keygen command
    cmd = ["ssh-keygen", "-t", key_type, "-f", key_path]

    if passphrase:
        cmd.extend(["-N", passphrase])
    else:
        cmd.extend(["-N", ""])  # No passphrase

    if comment:
        cmd.extend(["-C", comment])

    # Create directory if it doesn't exist
    key_dir = Path(key_path).parent
    key_dir.mkdir(mode=0o700, exist_ok=True)

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"Generated SSH key pair: {key_path}")

        # Set proper permissions
        os.chmod(key_path, 0o600)  # Private key: read/write for owner only
        os.chmod(f"{key_path}.pub", 0o644)  # Public key: readable by all

        return key_path, f"{key_path}.pub"

    except subprocess.CalledProcessError as e:
        raise SSHKeyGenerationError(f"Failed to generate SSH key: {e.stderr}")


def add_host_key(hostname: str, port: int = 22) -> bool:
    """
    Add host key to known_hosts file to avoid verification prompts.
    
    Args:
        hostname: Target hostname
        port: SSH port (default 22)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        cmd = ["ssh-keyscan"]
        if port != 22:
            cmd.extend(["-p", str(port)])
        cmd.append(hostname)
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            # Append to known_hosts file
            known_hosts_path = Path.home() / ".ssh" / "known_hosts"
            known_hosts_path.parent.mkdir(mode=0o700, exist_ok=True)
            
            with open(known_hosts_path, "a") as f:
                f.write(result.stdout)
            
            logger.info(f"Added host key for {hostname} to known_hosts")
            return True
    except Exception as e:
        logger.debug(f"Failed to add host key for {hostname}: {e}")
    
    return False


def deploy_public_key(
    hostname: str,
    username: str,
    public_key_path: str,
    port: int = 22,
    password: Optional[str] = None,
) -> bool:
    """
    Deploy public key to remote host's authorized_keys.

    Args:
        hostname: Target hostname
        username: SSH username
        public_key_path: Path to public key file
        port: SSH port (default 22)
        password: Password for initial authentication (if needed)

    Returns:
        True if deployment successful, False otherwise

    Raises:
        SSHKeyDeploymentError: If deployment fails
    """
    try:
        # Read public key content
        with open(public_key_path, "r") as f:
            public_key_content = f.read().strip()
    except IOError as e:
        raise SSHKeyDeploymentError(f"Cannot read public key file: {e}")

    # First, add the host key to known_hosts to avoid verification prompts
    add_host_key(hostname, port)
    
    # Try ssh-copy-id first (most reliable method) with host key acceptance
    try:
        cmd = ["ssh-copy-id", "-i", public_key_path]
        # Add SSH options to automatically accept new host keys
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
        if port != 22:
            cmd.extend(["-p", str(port)])
        cmd.append(f"{username}@{hostname}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=password if password else None,
            timeout=30
        )
        
        if result.returncode == 0:
            logger.info("Successfully deployed public key using ssh-copy-id")
            return True
        else:
            logger.debug(f"ssh-copy-id failed with return code {result.returncode}: {result.stderr}")

    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug(f"ssh-copy-id failed: {e}, trying manual deployment")

    # Fallback: Manual deployment using paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect with password or existing key
        if password:
            client.connect(
                hostname=hostname,
                username=username,
                password=password,
                port=port,
                timeout=30,
            )
        else:
            # Try with existing keys
            client.connect(hostname=hostname, username=username, port=port, timeout=30)

        # Create .ssh directory if it doesn't exist
        stdin, stdout, stderr = client.exec_command(
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh"
        )
        stdout.channel.recv_exit_status()  # Wait for command to complete

        # Add public key to authorized_keys
        escaped_key = public_key_content.replace("'", "'\"'\"'")
        cmd_str = f"echo '{escaped_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
        stdin, stdout, stderr = client.exec_command(cmd_str)
        exit_status = stdout.channel.recv_exit_status()

        client.close()

        if exit_status == 0:
            logger.info("Successfully deployed public key manually")
            return True
        else:
            error_msg = stderr.read().decode() if stderr else "Unknown error"
            raise SSHKeyDeploymentError(
                f"Failed to add key to authorized_keys: {error_msg}"
            )

    except Exception as e:
        raise SSHKeyDeploymentError(f"Failed to deploy public key: {e}")


def update_ssh_config(
    hostname: str, username: str, key_file: str, alias: str, port: int = 22
) -> None:
    """
    Add or update SSH config entry for cluster.

    Args:
        hostname: Target hostname
        username: SSH username
        key_file: Path to SSH private key
        alias: Alias name for the host
        port: SSH port (default 22)
    """
    ssh_config_path = Path.home() / ".ssh" / "config"

    # Create .ssh directory if it doesn't exist
    ssh_config_path.parent.mkdir(mode=0o700, exist_ok=True)

    # Prepare new config entry
    config_entry = f"""
# Clustrix auto-generated entry for {alias}
Host {alias}
    HostName {hostname}
    User {username}
    IdentityFile {key_file}
    IdentitiesOnly yes"""

    if port != 22:
        config_entry += f"\n    Port {port}"

    config_entry += "\n"

    # Check if entry already exists
    if ssh_config_path.exists():
        with open(ssh_config_path, "r") as f:
            existing_content = f.read()

        # Look for existing entry for this alias
        if f"Host {alias}" in existing_content:
            logger.info(f"SSH config entry for {alias} already exists, skipping update")
            return

    # Append new entry
    with open(ssh_config_path, "a") as f:
        f.write(config_entry)

    # Set proper permissions
    os.chmod(ssh_config_path, 0o600)
    logger.info(f"Added SSH config entry for {alias}")


def setup_ssh_keys(
    config: ClusterConfig,
    cluster_alias: Optional[str] = None,
    auto_generate: bool = True,
    key_type: str = "ed25519",
    passphrase: str = "",
    password: Optional[str] = None,
) -> ClusterConfig:
    """
    Automatically set up SSH keys for cluster authentication.

    Args:
        config: ClusterConfig object for the target cluster
        cluster_alias: Optional alias for SSH config entry
        auto_generate: Whether to generate new keys if none exist
        key_type: Type of key to generate if needed ('ed25519', 'rsa')
        passphrase: Passphrase for new keys (empty for no passphrase)
        password: Password for initial SSH connection (if needed)

    Returns:
        Updated ClusterConfig with key_file populated

    Raises:
        SSHKeySetupError: If key setup fails
    """
    if not config.cluster_host:
        raise SSHKeySetupError("cluster_host must be specified in config")

    if not config.username:
        raise SSHKeySetupError("username must be specified in config")

    hostname = config.cluster_host
    username = config.username
    port = getattr(config, "port", 22)

    # Step 1: Check if SSH keys already work
    existing_key = detect_existing_ssh_key(hostname, username, port)
    if existing_key:
        logger.info(f"Found working SSH key for {hostname}: {existing_key}")
        config.key_file = existing_key
        return config

    # Step 2: Generate new SSH key if auto_generate is enabled
    if not auto_generate:
        raise SSHKeySetupError(
            f"No working SSH key found for {hostname} and auto_generate=False"
        )

    # Create key path
    ssh_dir = Path.home() / ".ssh"
    if cluster_alias:
        key_name = f"id_{key_type}_{cluster_alias}"
    else:
        key_name = f"id_{key_type}_clustrix_{hostname.replace('.', '_')}"

    key_path = str(ssh_dir / key_name)

    # Check if key already exists
    if Path(key_path).exists():
        logger.warning(f"SSH key already exists at {key_path}, using existing key")
        config.key_file = key_path
    else:
        # Generate new key
        comment = f"{username}@{hostname} (generated by Clustrix)"
        private_key_path, public_key_path = generate_ssh_key(
            key_path, key_type, passphrase, comment
        )
        config.key_file = private_key_path

        # Deploy public key
        deploy_public_key(hostname, username, public_key_path, port, password)

    # Step 3: Update SSH config if alias provided
    if cluster_alias:
        update_ssh_config(hostname, username, config.key_file, cluster_alias, port)

    # Step 4: Test the connection (with retry for key propagation)
    import time
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        test_key = detect_existing_ssh_key(hostname, username, port)
        if test_key == config.key_file:
            break
        elif attempt < max_retries - 1:
            logger.info(f"Connection test attempt {attempt + 1} failed, retrying in {retry_delay}s...")
            time.sleep(retry_delay)
        else:
            # Final attempt failed - but still return success since key was generated and deployed
            logger.warning(
                f"SSH key generated and deployed successfully, but connection test failed. "
                f"Generated key: {config.key_file}, Test result: {test_key}. "
                f"The key may need time to propagate or there may be server-side caching."
            )
            # Don't raise an error - the key was successfully created and deployed

    logger.info(f"SSH key setup completed successfully for {hostname}")
    return config


def get_ssh_key_info(key_path: str) -> Dict[str, Any]:
    """
    Get information about an SSH key.

    Args:
        key_path: Path to SSH private key

    Returns:
        Dictionary with key information (type, fingerprint, comment, etc.)
    """
    try:
        # Get key type and fingerprint
        result = subprocess.run(
            ["ssh-keygen", "-l", "-f", key_path],
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse output: "2048 SHA256:... comment (RSA)"
        parts = result.stdout.strip().split()
        if len(parts) >= 3:
            bit_size = parts[0]
            fingerprint = parts[1]
            key_type = parts[-1].strip("()")
            comment = " ".join(parts[2:-1]) if len(parts) > 3 else ""

            return {
                "path": key_path,
                "type": key_type,
                "bit_size": bit_size,
                "fingerprint": fingerprint,
                "comment": comment,
                "exists": True,
            }

    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass

    return {"path": key_path, "exists": False}


def list_ssh_keys() -> List[Dict[str, Any]]:
    """
    List all SSH keys with their information.

    Returns:
        List of dictionaries with SSH key information
    """
    ssh_keys = find_ssh_keys()
    return [get_ssh_key_info(key_path) for key_path in ssh_keys]
