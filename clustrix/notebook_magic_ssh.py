"""SSH key management and configuration for notebook magic commands."""

import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import tempfile
import shutil

try:
    import paramiko

    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from .config import ClusterConfig


@dataclass
class SSHKeyInfo:
    """Information about SSH keys."""

    public_key_path: str
    private_key_path: str
    key_type: str
    fingerprint: Optional[str] = None
    comment: Optional[str] = None
    created_at: Optional[str] = None
    exists: bool = False


class SSHKeyManager:
    """Manages SSH key generation, deployment, and validation."""

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.ssh_dir = Path.home() / ".ssh"
        self.ssh_dir.mkdir(exist_ok=True, mode=0o700)

    def get_default_key_path(self, key_type: str = "ed25519") -> str:
        """Get the default path for SSH keys."""
        if key_type == "ed25519":
            return str(self.ssh_dir / "id_ed25519")
        elif key_type == "rsa":
            return str(self.ssh_dir / "id_rsa")
        elif key_type == "ecdsa":
            return str(self.ssh_dir / "id_ecdsa")
        else:
            return str(self.ssh_dir / f"id_{key_type}")

    def get_key_info(self, private_key_path: str) -> SSHKeyInfo:
        """Get information about an SSH key."""
        private_path = Path(private_key_path)
        public_path = private_path.with_suffix(private_path.suffix + ".pub")

        # Determine key type from filename
        key_type = "rsa"  # default
        if "ed25519" in private_key_path:
            key_type = "ed25519"
        elif "ecdsa" in private_key_path:
            key_type = "ecdsa"
        elif "dsa" in private_key_path:
            key_type = "dsa"

        key_info = SSHKeyInfo(
            public_key_path=str(public_path),
            private_key_path=str(private_path),
            key_type=key_type,
            exists=private_path.exists() and public_path.exists(),
        )

        # Get additional info if key exists
        if key_info.exists:
            try:
                key_info.fingerprint = self._get_key_fingerprint(str(public_path))
                key_info.comment = self._get_key_comment(str(public_path))
                stat_result = private_path.stat()
                key_info.created_at = str(stat_result.st_mtime)
            except Exception:
                pass  # Best effort

        return key_info

    def generate_ssh_keys(
        self,
        key_type: str = "ed25519",
        key_path: Optional[str] = None,
        passphrase: Optional[str] = None,
        comment: Optional[str] = None,
        force_overwrite: bool = False,
    ) -> SSHKeyInfo:
        """Generate SSH key pairs."""
        if not key_path:
            key_path = self.get_default_key_path(key_type)

        private_path = Path(key_path)
        public_path = private_path.with_suffix(private_path.suffix + ".pub")

        # Check if keys already exist
        if not force_overwrite and private_path.exists():
            raise FileExistsError(f"SSH key already exists: {private_path}")

        # Generate comment if not provided
        if not comment:
            username = os.getenv("USER", "user")
            hostname = os.getenv("HOSTNAME", "localhost")
            comment = f"{username}@{hostname}"

        # Generate SSH key using ssh-keygen
        cmd = [
            "ssh-keygen",
            "-t",
            key_type,
            "-f",
            str(private_path),
            "-C",
            comment,
            "-N",
            passphrase or "",  # Empty passphrase if none provided
        ]

        if force_overwrite:
            cmd.append("-y")  # Overwrite without asking

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            # Set proper permissions
            private_path.chmod(0o600)
            public_path.chmod(0o644)

            return self.get_key_info(str(private_path))

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"SSH key generation failed: {e.stderr}")

    def deploy_public_key(
        self,
        hostname: str,
        username: str,
        public_key_path: str,
        password: Optional[str] = None,
        port: int = 22,
    ) -> bool:
        """Deploy public key to remote server."""
        if not PARAMIKO_AVAILABLE:
            raise ImportError("paramiko is required for SSH key deployment")

        try:
            with open(public_key_path, "r") as f:
                public_key = f.read().strip()
        except FileNotFoundError:
            raise FileNotFoundError(f"Public key not found: {public_key_path}")

        # Connect via SSH and deploy key
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            # Connect using password authentication
            client.connect(
                hostname=hostname,
                username=username,
                password=password,
                port=port,
                timeout=30,
            )

            # Create .ssh directory if it doesn't exist
            stdin, stdout, stderr = client.exec_command("mkdir -p ~/.ssh")
            stdout.channel.recv_exit_status()

            # Add public key to authorized_keys
            command = f'echo "{public_key}" >> ~/.ssh/authorized_keys'
            stdin, stdout, stderr = client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                # Set proper permissions
                client.exec_command("chmod 700 ~/.ssh")
                client.exec_command("chmod 600 ~/.ssh/authorized_keys")
                return True
            else:
                error_msg = stderr.read().decode()
                raise RuntimeError(f"Failed to deploy key: {error_msg}")

        finally:
            client.close()

    def test_ssh_connectivity(
        self, hostname: str, username: str, private_key_path: str, port: int = 22
    ) -> bool:
        """Test SSH connectivity using key authentication."""
        if not PARAMIKO_AVAILABLE:
            raise ImportError("paramiko is required for SSH connectivity testing")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            # Load private key
            private_key = None
            try:
                private_key = paramiko.Ed25519Key.from_private_key_file(
                    private_key_path
                )
            except Exception:
                try:
                    private_key = paramiko.RSAKey.from_private_key_file(
                        private_key_path
                    )
                except Exception:
                    private_key = paramiko.ECDSAKey.from_private_key_file(
                        private_key_path
                    )

            # Test connection
            client.connect(
                hostname=hostname,
                username=username,
                pkey=private_key,
                port=port,
                timeout=30,
            )

            # Test command execution
            stdin, stdout, stderr = client.exec_command('echo "SSH test successful"')
            exit_status = stdout.channel.recv_exit_status()

            return exit_status == 0

        except Exception:
            return False
        finally:
            client.close()

    def _get_key_fingerprint(self, public_key_path: str) -> str:
        """Get SSH key fingerprint."""
        try:
            result = subprocess.run(
                ["ssh-keygen", "-lf", public_key_path],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip().split()[1]
        except subprocess.CalledProcessError:
            return "Unknown"

    def _get_key_comment(self, public_key_path: str) -> str:
        """Get SSH key comment."""
        try:
            with open(public_key_path, "r") as f:
                content = f.read().strip()
                parts = content.split()
                if len(parts) >= 3:
                    return parts[2]
                return ""
        except FileNotFoundError:
            return ""


class SSHConfigManager:
    """Manages SSH client configuration."""

    def __init__(self):
        self.config_path = Path.home() / ".ssh" / "config"
        self.config_path.parent.mkdir(exist_ok=True, mode=0o700)

    def read_config(self) -> Dict[str, Dict[str, str]]:
        """Read SSH config file."""
        config: Dict[str, Dict[str, str]] = {}
        current_host = None

        if not self.config_path.exists():
            return config

        try:
            with open(self.config_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    if line.lower().startswith("host "):
                        current_host = line[5:].strip()
                        config[current_host] = {}
                    elif current_host and " " in line:
                        key, value = line.split(None, 1)
                        config[current_host][key.lower()] = value

        except Exception:
            pass  # Best effort

        return config

    def add_host_config(
        self, hostname: str, config_options: Dict[str, str], overwrite: bool = False
    ) -> bool:
        """Add host configuration to SSH config."""
        current_config = self.read_config()

        if hostname in current_config and not overwrite:
            return False

        current_config[hostname] = config_options

        try:
            self._write_config(current_config)
            return True
        except Exception:
            return False

    def remove_host_config(self, hostname: str) -> bool:
        """Remove host configuration from SSH config."""
        current_config = self.read_config()

        if hostname not in current_config:
            return False

        del current_config[hostname]

        try:
            self._write_config(current_config)
            return True
        except Exception:
            return False

    def _write_config(self, config: Dict[str, Dict[str, str]]):
        """Write SSH config file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
            for host, options in config.items():
                tmp_file.write(f"Host {host}\n")
                for key, value in options.items():
                    tmp_file.write(f"    {key.title()} {value}\n")
                tmp_file.write("\n")

        # Atomically replace the config file
        shutil.move(tmp_file.name, self.config_path)
        self.config_path.chmod(0o600)


def generate_ssh_keys(
    key_type: str = "ed25519",
    key_path: Optional[str] = None,
    comment: Optional[str] = None,
    force_overwrite: bool = False,
) -> SSHKeyInfo:
    """Generate SSH key pairs with secure defaults."""
    # Use a temporary config for key generation
    temp_config = ClusterConfig(cluster_type="ssh")
    key_manager = SSHKeyManager(temp_config)

    return key_manager.generate_ssh_keys(
        key_type=key_type,
        key_path=key_path,
        comment=comment,
        force_overwrite=force_overwrite,
    )


def manage_ssh_keychain(
    action: str, hostname: str, config: Optional[ClusterConfig] = None, **kwargs
) -> Dict[str, Any]:
    """Manage SSH keychain operations."""
    if not config:
        config = ClusterConfig(cluster_type="ssh")

    key_manager = SSHKeyManager(config)
    config_manager = SSHConfigManager()

    results = {"success": False, "message": "", "data": {}}

    try:
        if action == "generate":
            key_info = key_manager.generate_ssh_keys(**kwargs)
            results["success"] = True
            results["message"] = f"Generated {key_info.key_type} key pair"
            results["data"]["key_info"] = key_info

        elif action == "deploy":
            success = key_manager.deploy_public_key(hostname, **kwargs)
            results["success"] = success
            results["message"] = (
                "Public key deployed successfully" if success else "Deployment failed"
            )

        elif action == "test":
            success = key_manager.test_ssh_connectivity(hostname, **kwargs)
            results["success"] = success
            results["message"] = (
                "SSH connectivity test passed"
                if success
                else "Connectivity test failed"
            )

        elif action == "configure":
            success = config_manager.add_host_config(
                hostname, kwargs.get("config_options", {})
            )
            results["success"] = success
            results["message"] = (
                "Host configuration added" if success else "Configuration failed"
            )

        else:
            results["message"] = f"Unknown action: {action}"

    except Exception as e:
        results["message"] = f"Error: {str(e)}"

    return results


def validate_ssh_connectivity(
    hostname: str, username: str, private_key_path: Optional[str] = None, port: int = 22
) -> Dict[str, Any]:
    """Validate SSH connectivity with comprehensive testing."""
    results = {
        "connection_test": False,
        "key_authentication": False,
        "command_execution": False,
        "error_messages": [],
    }

    if not PARAMIKO_AVAILABLE:
        results["error_messages"].append("paramiko not available")
        return results

    # Use default key path if not provided
    if not private_key_path:
        ssh_dir = Path.home() / ".ssh"
        for key_type in ["id_ed25519", "id_rsa", "id_ecdsa"]:
            key_path = ssh_dir / key_type
            if key_path.exists():
                private_key_path = str(key_path)
                break

    if not private_key_path:
        results["error_messages"].append("No SSH private key found")
        return results

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Test 1: Basic connection
        try:
            private_key = None
            for key_class in [paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey]:
                try:
                    private_key = key_class.from_private_key_file(private_key_path)
                    break
                except Exception:
                    continue

            if not private_key:
                results["error_messages"].append("Could not load private key")
                return results

            client.connect(
                hostname=hostname,
                username=username,
                pkey=private_key,
                port=port,
                timeout=30,
            )
            results["connection_test"] = True
            results["key_authentication"] = True

        except Exception as e:
            results["error_messages"].append(f"Connection failed: {str(e)}")
            return results

        # Test 2: Command execution
        try:
            stdin, stdout, stderr = client.exec_command('echo "test" && pwd')
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                results["command_execution"] = True
            else:
                error_output = stderr.read().decode()
                results["error_messages"].append(
                    f"Command execution failed: {error_output}"
                )

        except Exception as e:
            results["error_messages"].append(f"Command execution error: {str(e)}")

    finally:
        client.close()

    return results


def setup_ssh_config(hostname: str, config_options: Dict[str, str]) -> bool:
    """Setup SSH configuration for a host."""
    config_manager = SSHConfigManager()
    return config_manager.add_host_config(hostname, config_options, overwrite=True)


def list_ssh_keys(ssh_dir: Optional[str] = None) -> List[SSHKeyInfo]:
    """List all SSH keys in the SSH directory."""
    if not ssh_dir:
        ssh_dir = str(Path.home() / ".ssh")

    ssh_path = Path(ssh_dir)
    if not ssh_path.exists():
        return []

    keys: List[SSHKeyInfo] = []
    key_files: List[Path] = []

    # Find private key files
    for pattern in ["id_*", "*_key"]:
        key_files.extend(ssh_path.glob(pattern))

    # Filter out public keys and known_hosts, etc.
    private_key_files = [
        f
        for f in key_files
        if not f.name.endswith(".pub")
        and f.name not in ["known_hosts", "authorized_keys", "config"]
        and f.is_file()
    ]

    # Create a temporary config for key info
    temp_config = ClusterConfig(cluster_type="ssh")
    key_manager = SSHKeyManager(temp_config)

    for key_file in private_key_files:
        try:
            key_info = key_manager.get_key_info(str(key_file))
            keys.append(key_info)
        except Exception:
            pass  # Skip invalid keys

    return keys


# Export key functions for testing
__all__ = [
    "SSHKeyInfo",
    "SSHKeyManager",
    "SSHConfigManager",
    "generate_ssh_keys",
    "manage_ssh_keychain",
    "validate_ssh_connectivity",
    "setup_ssh_config",
    "list_ssh_keys",
]
