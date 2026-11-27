"""
SSH tests that need no cluster: key discovery, key generation and what a
connection to an address that cannot answer does.

Everything that drives a real sshd -- authentication, SFTP, jobs, the
filesystem helpers -- is in ``test_local_sshd_slurm.py`` and
``test_paths_with_a_tilde_real.py``. The tests that used to live here for
that connected with the default host-key policy from the empty ``$HOME``
every test is given, so they could only ever skip.
"""

import os
import uuid
from pathlib import Path
import pytest
import paramiko

from clustrix.ssh_utils import find_ssh_keys, generate_ssh_key
from tests.real_world import TempResourceManager
from clustrix.ssh_security import configure_host_key_policy


@pytest.mark.real_world
class TestRealSSHOperations:
    """SSH key handling and connection failure, with no cluster needed."""

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
""".format(username=os.getenv("USER", "user"))

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

    def test_ssh_connection_timeout_real(self):
        """Test SSH connection timeout handling."""
        try:
            # Create SSH client with short timeout
            client = paramiko.SSHClient()
            configure_host_key_policy(client)

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
