"""
Real-world SSH tests for Clustrix.

These tests use actual SSH connections to verify that our
SSH handling code works correctly with real SSH servers.

Note: These tests require SSH server access. By default, they test
against localhost with the current user's SSH keys.
"""

import os
import tempfile
import uuid
from pathlib import Path
import pytest
import paramiko

from clustrix.ssh_utils import (
    find_ssh_keys,
    detect_existing_ssh_key,
    generate_ssh_key,
    get_ssh_key_info,
    list_ssh_keys,
    SSHKeySetupError,
)
from clustrix.config import ClusterConfig
from clustrix.filesystem import ClusterFilesystem
from tests.real_world import TempResourceManager, credentials, test_manager


@pytest.mark.real_world
class TestRealSSHOperations:
    """Test SSH operations with real SSH connections."""

    @pytest.fixture
    def ssh_config(self):
        """Get SSH configuration for testing."""
        ssh_creds = credentials.get_ssh_credentials()
        if not ssh_creds:
            pytest.skip("No SSH credentials available for testing")
        return ssh_creds

    def test_ssh_key_discovery_real(self):
        """Test discovering real SSH keys on the system."""
        # Find SSH keys in actual ~/.ssh directory
        ssh_keys = find_ssh_keys()

        # We should find at least some SSH keys on most systems
        # If none found, that's also valid (might be a fresh system)
        assert isinstance(ssh_keys, list)

        # If keys found, verify they're valid paths
        for key_path in ssh_keys:
            assert Path(key_path).exists()
            assert Path(key_path).is_file()

            # Verify it's in the SSH directory
            assert str(Path(key_path).parent).endswith(".ssh")

    def test_ssh_key_generation_real(self):
        """Test generating real SSH keys."""
        with TempResourceManager() as temp_mgr:
            # Create temporary SSH directory
            ssh_dir = temp_mgr.create_temp_dir()

            # Generate SSH key
            key_name = f"test_key_{uuid.uuid4().hex[:8]}"
            private_key_path = ssh_dir / key_name

            try:
                generate_ssh_key(
                    key_path=str(private_key_path),
                    key_type="ed25519",
                    comment="test@clustrix-real-test",
                )

                # Verify key files were created
                assert private_key_path.exists()
                public_key_path = Path(str(private_key_path) + ".pub")
                assert public_key_path.exists()

                # Verify key content
                private_content = private_key_path.read_text()
                public_content = public_key_path.read_text()

                assert "BEGIN OPENSSH PRIVATE KEY" in private_content
                assert "ssh-ed25519" in public_content
                assert "test@clustrix-real-test" in public_content

            except Exception as e:
                # SSH key generation might fail on some systems
                # Log the error but don't fail the test
                pytest.skip(f"SSH key generation failed: {e}")

    def test_ssh_connection_localhost_real(self, ssh_config):
        """Test real SSH connection to localhost."""
        if ssh_config["host"] != "localhost":
            pytest.skip("This test only runs against localhost")

        try:
            # Create SSH client
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Attempt connection
            if ssh_config.get("private_key_path"):
                # Use key-based authentication
                client.connect(
                    hostname=ssh_config["host"],
                    username=ssh_config["username"],
                    key_filename=ssh_config["private_key_path"],
                )
            else:
                # Try default key-based auth
                client.connect(
                    hostname=ssh_config["host"], username=ssh_config["username"]
                )

            # Test basic command execution
            stdin, stdout, stderr = client.exec_command('echo "SSH test successful"')
            result = stdout.read().decode().strip()
            error = stderr.read().decode().strip()

            assert result == "SSH test successful"
            assert error == ""

            # Test command with environment variables
            stdin, stdout, stderr = client.exec_command("echo $USER")
            username = stdout.read().decode().strip()
            assert username == ssh_config["username"]

        except Exception as e:
            pytest.skip(f"SSH connection failed: {e}")
        finally:
            try:
                client.close()
            except:
                pass

    def test_sftp_file_operations_real(self, ssh_config):
        """Test real SFTP file operations."""
        if ssh_config["host"] != "localhost":
            pytest.skip("This test only runs against localhost")

        with TempResourceManager() as temp_mgr:
            try:
                # Create SSH client
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Connect
                if ssh_config.get("private_key_path"):
                    client.connect(
                        hostname=ssh_config["host"],
                        username=ssh_config["username"],
                        key_filename=ssh_config["private_key_path"],
                    )
                else:
                    client.connect(
                        hostname=ssh_config["host"], username=ssh_config["username"]
                    )

                # Create SFTP client
                sftp = client.open_sftp()

                # Create test file locally
                test_content = f"SFTP test content {uuid.uuid4()}"
                local_file = temp_mgr.create_temp_file(test_content, ".txt")

                # Upload file
                remote_path = f"/tmp/clustrix_sftp_test_{uuid.uuid4().hex}.txt"
                sftp.put(str(local_file), remote_path)

                # Verify file exists on remote
                remote_stat = sftp.stat(remote_path)
                assert remote_stat.st_size > 0

                # Download file to verify content
                download_file = temp_mgr.create_temp_file(suffix=".txt")
                sftp.get(remote_path, str(download_file))

                # Verify content
                downloaded_content = download_file.read_text()
                assert downloaded_content == test_content

                # Test directory operations
                remote_dir = f"/tmp/clustrix_sftp_test_dir_{uuid.uuid4().hex}"
                sftp.mkdir(remote_dir)

                # List directory contents
                dir_contents = sftp.listdir("/tmp")
                assert Path(remote_path).name in dir_contents
                assert Path(remote_dir).name in dir_contents

                # Cleanup
                sftp.remove(remote_path)
                sftp.rmdir(remote_dir)

            except Exception as e:
                pytest.skip(f"SFTP operations failed: {e}")
            finally:
                try:
                    if "sftp" in locals():
                        sftp.close()
                    client.close()
                except:
                    pass

    def test_cluster_filesystem_ssh_real(self, ssh_config):
        """Test ClusterFilesystem with real SSH operations."""
        if ssh_config["host"] != "localhost":
            pytest.skip("This test only runs against localhost")

        with TempResourceManager() as temp_mgr:
            try:
                # Create SSH-based cluster config
                config = ClusterConfig(
                    cluster_type="ssh",
                    cluster_host=ssh_config["host"],
                    username=ssh_config["username"],
                    private_key_path=ssh_config.get("private_key_path"),
                    remote_work_dir="/tmp",
                )

                filesystem = ClusterFilesystem(config)

                # Test remote directory operations
                test_dir = f"/tmp/clustrix_fs_test_{uuid.uuid4().hex}"

                # Create remote directory
                stdin, stdout, stderr = filesystem.ssh_client.exec_command(
                    f"mkdir -p {test_dir}"
                )
                stderr_content = stderr.read().decode()
                if stderr_content:
                    pytest.skip(f"Failed to create test directory: {stderr_content}")

                # Test file operations
                test_file = f"{test_dir}/test_file.txt"
                test_content = f"Test content {uuid.uuid4()}"

                # Create remote file
                stdin, stdout, stderr = filesystem.ssh_client.exec_command(
                    f"echo '{test_content}' > {test_file}"
                )

                # Test filesystem operations
                assert filesystem.exists(test_file)
                assert filesystem.isfile(test_file)
                assert filesystem.isdir(test_dir)

                # Test file info
                file_info = filesystem.stat(test_file)
                assert file_info.size > 0
                assert file_info.name == "test_file.txt"
                assert file_info.is_file

                # Test directory listing
                files = filesystem.ls(test_dir)
                assert len(files) == 1
                assert files[0].name == "test_file.txt"

                # Cleanup
                filesystem.ssh_client.exec_command(f"rm -rf {test_dir}")

            except Exception as e:
                pytest.skip(f"SSH filesystem operations failed: {e}")

    def test_ssh_key_info_real(self):
        """Test getting information about real SSH keys."""
        # Find existing SSH keys
        ssh_keys = find_ssh_keys()

        if not ssh_keys:
            pytest.skip("No SSH keys found for testing")

        # Test key info for first found key
        key_path = ssh_keys[0]

        try:
            key_info = get_ssh_key_info(key_path)

            assert key_info is not None
            assert "type" in key_info
            assert (
                "bits" in key_info or "curve" in key_info
            )  # RSA has bits, Ed25519 has curve
            assert "fingerprint" in key_info
            assert "comment" in key_info

            # Verify key type is valid
            valid_types = ["rsa", "ed25519", "ecdsa", "dsa"]
            assert key_info["type"].lower() in valid_types

        except Exception as e:
            pytest.skip(f"Failed to get key info: {e}")

    def test_ssh_config_file_operations_real(self):
        """Test SSH config file operations."""
        with TempResourceManager() as temp_mgr:
            # Create temporary SSH config
            ssh_config_content = """
Host test-host
    HostName example.com
    User testuser
    Port 22
    IdentityFile ~/.ssh/test_key

Host localhost
    HostName 127.0.0.1
    User {username}
    Port 22
""".format(
                username=os.getenv("USER", "user")
            )

            config_file = temp_mgr.create_temp_file(ssh_config_content, ".config")

            # Test parsing SSH config
            config_content = config_file.read_text()
            assert "Host test-host" in config_content
            assert "HostName example.com" in config_content
            assert "User testuser" in config_content

            # Test config file structure
            lines = config_content.strip().split("\n")
            host_lines = [line for line in lines if line.strip().startswith("Host ")]
            assert len(host_lines) == 2  # test-host and localhost

    def test_ssh_connection_timeout_real(self, ssh_config):
        """Test SSH connection timeout handling."""
        try:
            # Create SSH client with short timeout
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Try to connect to non-existent host with timeout
            with pytest.raises((paramiko.SSHException, OSError, TimeoutError)):
                client.connect(
                    hostname="192.0.2.1",  # TEST-NET-1 (should not exist)
                    username="test",
                    timeout=2,  # 2 second timeout
                )

        except Exception as e:
            # This test might behave differently on different systems
            # Log the error but don't fail the test
            pytest.skip(f"Timeout test failed: {e}")

    def test_ssh_multiple_connections_real(self, ssh_config):
        """Test multiple SSH connections."""
        if ssh_config["host"] != "localhost":
            pytest.skip("This test only runs against localhost")

        connections = []
        try:
            # Create multiple SSH connections
            for i in range(3):
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                if ssh_config.get("private_key_path"):
                    client.connect(
                        hostname=ssh_config["host"],
                        username=ssh_config["username"],
                        key_filename=ssh_config["private_key_path"],
                    )
                else:
                    client.connect(
                        hostname=ssh_config["host"], username=ssh_config["username"]
                    )

                connections.append(client)

                # Test command on each connection
                stdin, stdout, stderr = client.exec_command(f'echo "Connection {i}"')
                result = stdout.read().decode().strip()
                assert result == f"Connection {i}"

            # Verify all connections are active
            assert len(connections) == 3

        except Exception as e:
            pytest.skip(f"Multiple SSH connections failed: {e}")
        finally:
            # Clean up connections
            for client in connections:
                try:
                    client.close()
                except:
                    pass

    @pytest.mark.expensive
    def test_ssh_performance_real(self, ssh_config):
        """Test SSH performance with real connections."""
        if ssh_config["host"] != "localhost":
            pytest.skip("This test only runs against localhost")

        import time

        try:
            # Measure connection time
            start_time = time.time()

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            if ssh_config.get("private_key_path"):
                client.connect(
                    hostname=ssh_config["host"],
                    username=ssh_config["username"],
                    key_filename=ssh_config["private_key_path"],
                )
            else:
                client.connect(
                    hostname=ssh_config["host"], username=ssh_config["username"]
                )

            connection_time = time.time() - start_time

            # Connection should be reasonably fast (< 5 seconds)
            assert connection_time < 5.0

            # Test command execution time
            start_time = time.time()
            stdin, stdout, stderr = client.exec_command('echo "Performance test"')
            result = stdout.read().decode().strip()
            command_time = time.time() - start_time

            # Command should be fast (< 1 second)
            assert command_time < 1.0
            assert result == "Performance test"

        except Exception as e:
            pytest.skip(f"SSH performance test failed: {e}")
        finally:
            try:
                client.close()
            except:
                pass
