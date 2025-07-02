"""
Tests for SSH key automation utilities.
"""

import os
import tempfile
import pytest
import platform
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import subprocess

from clustrix.ssh_utils import (
    find_ssh_keys,
    detect_existing_ssh_key,
    generate_ssh_key,
    deploy_public_key,
    update_ssh_config,
    setup_ssh_keys,
    get_ssh_key_info,
    list_ssh_keys,
    SSHKeySetupError,
    SSHKeyGenerationError,
    SSHKeyDeploymentError,
)
from clustrix.config import ClusterConfig


class TestFindSSHKeys:
    """Test SSH key discovery functionality."""

    def test_find_ssh_keys_empty_directory(self):
        """Test finding SSH keys in empty directory."""
        with patch("clustrix.ssh_utils.Path.home") as mock_home:
            mock_ssh_dir = MagicMock()
            mock_ssh_dir.exists.return_value = False
            mock_home.return_value = MagicMock()
            mock_home.return_value.__truediv__.return_value = mock_ssh_dir

            result = find_ssh_keys()
            assert result == []

    def test_find_ssh_keys_with_keys(self):
        """Test finding existing SSH keys."""
        with tempfile.TemporaryDirectory() as temp_dir:
            ssh_dir = Path(temp_dir) / ".ssh"
            ssh_dir.mkdir(mode=0o700)

            # Create mock SSH keys
            key_files = ["id_rsa", "id_ed25519", "id_ecdsa"]
            created_keys = []

            for key_name in key_files:
                key_path = ssh_dir / key_name
                with open(key_path, "w") as f:
                    f.write(
                        "-----BEGIN OPENSSH PRIVATE KEY-----\ntest key content\n-----END OPENSSH PRIVATE KEY-----\n"
                    )
                # Set proper permissions only on Unix-like systems
                if platform.system() != "Windows":
                    os.chmod(key_path, 0o600)
                created_keys.append(str(key_path))

            with patch("clustrix.ssh_utils.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                result = find_ssh_keys()

                # Should find all created keys
                assert len(result) == 3
                for key_path in created_keys:
                    assert key_path in result

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="File permissions not applicable on Windows",
    )
    def test_find_ssh_keys_wrong_permissions(self):
        """Test that keys with wrong permissions are ignored."""
        with tempfile.TemporaryDirectory() as temp_dir:
            ssh_dir = Path(temp_dir) / ".ssh"
            ssh_dir.mkdir(mode=0o700)

            # Create key with wrong permissions
            key_path = ssh_dir / "id_rsa"
            with open(key_path, "w") as f:
                f.write(
                    "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n"
                )
            os.chmod(key_path, 0o644)  # Wrong permissions

            with patch("clustrix.ssh_utils.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                result = find_ssh_keys()

                # Should not find the key due to wrong permissions
                assert len(result) == 0


class TestDetectExistingSSHKey:
    """Test SSH key detection functionality."""

    @patch("clustrix.ssh_utils.find_ssh_keys")
    @patch("paramiko.SSHClient")
    def test_detect_existing_ssh_key_success(self, mock_ssh_client, mock_find_keys):
        """Test successful SSH key detection."""
        mock_find_keys.return_value = [
            "/home/user/.ssh/id_rsa",
            "/home/user/.ssh/id_ed25519",
        ]

        # Mock successful connection for first key
        mock_client = MagicMock()
        mock_ssh_client.return_value = mock_client
        mock_client.connect.side_effect = [None, Exception("Connection failed")]

        result = detect_existing_ssh_key("test.host.com", "testuser")

        assert result == "/home/user/.ssh/id_rsa"
        mock_client.connect.assert_called()
        mock_client.close.assert_called()

    @patch("clustrix.ssh_utils.find_ssh_keys")
    @patch("paramiko.SSHClient")
    def test_detect_existing_ssh_key_none_work(self, mock_ssh_client, mock_find_keys):
        """Test when no SSH keys work."""
        mock_find_keys.return_value = ["/home/user/.ssh/id_rsa"]

        # Mock failed connection
        mock_client = MagicMock()
        mock_ssh_client.return_value = mock_client
        mock_client.connect.side_effect = Exception("Connection failed")

        result = detect_existing_ssh_key("test.host.com", "testuser")

        assert result is None


class TestGenerateSSHKey:
    """Test SSH key generation functionality."""

    @patch("subprocess.run")
    @patch("os.chmod")
    def test_generate_ssh_key_success(self, mock_chmod, mock_run):
        """Test successful SSH key generation."""
        # Mock successful ssh-keygen commands
        mock_run.side_effect = [
            MagicMock(returncode=0),  # ssh-keygen --help
            MagicMock(returncode=0, stdout="", stderr=""),  # actual key generation
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = str(Path(temp_dir) / "test_key")

            private_key, public_key = generate_ssh_key(key_path, "ed25519")

            assert private_key == key_path
            assert public_key == f"{key_path}.pub"

            # Verify ssh-keygen was called correctly
            expected_cmd = ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", ""]
            mock_run.assert_any_call(
                expected_cmd, capture_output=True, text=True, check=True
            )

            # Verify permissions were set
            mock_chmod.assert_any_call(key_path, 0o600)
            mock_chmod.assert_any_call(f"{key_path}.pub", 0o644)

    @patch("subprocess.run")
    def test_generate_ssh_key_no_ssh_keygen(self, mock_run):
        """Test error when ssh-keygen is not available."""
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(SSHKeyGenerationError, match="ssh-keygen not found"):
            generate_ssh_key("/tmp/test_key")

    @patch("subprocess.run")
    def test_generate_ssh_key_generation_fails(self, mock_run):
        """Test error when key generation fails."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # ssh-keygen --help succeeds
            subprocess.CalledProcessError(1, "ssh-keygen", stderr="Generation failed"),
        ]

        with pytest.raises(SSHKeyGenerationError, match="Failed to generate SSH key"):
            generate_ssh_key("/tmp/test_key")


class TestDeployPublicKey:
    """Test SSH public key deployment functionality."""

    def test_deploy_public_key_file_not_found(self):
        """Test error when public key file doesn't exist."""
        with pytest.raises(SSHKeyDeploymentError, match="Cannot read public key file"):
            deploy_public_key("test.host.com", "testuser", "/nonexistent/key.pub")

    @patch("subprocess.run")
    def test_deploy_public_key_ssh_copy_id_success(self, mock_run):
        """Test successful deployment using ssh-copy-id."""
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".pub", delete=False) as f:
            f.write("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAATEST test@example.com\n")
            pub_key_path = f.name

        try:
            result = deploy_public_key("test.host.com", "testuser", pub_key_path)
            assert result is True

            # Verify ssh-copy-id was called with StrictHostKeyChecking option
            expected_cmd = [
                "ssh-copy-id",
                "-i",
                pub_key_path,
                "-o",
                "StrictHostKeyChecking=accept-new",
                "testuser@test.host.com",
            ]
            mock_run.assert_called_with(
                expected_cmd, capture_output=True, text=True, input=None, timeout=30
            )
        finally:
            os.unlink(pub_key_path)

    @patch("subprocess.run")
    @patch("paramiko.SSHClient")
    def test_deploy_public_key_manual_fallback(self, mock_ssh_client, mock_run):
        """Test manual deployment fallback when ssh-copy-id fails."""
        # ssh-copy-id fails
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh-copy-id")

        # Manual deployment succeeds
        mock_client = MagicMock()
        mock_ssh_client.return_value = mock_client

        # Mock exec_command returns
        mock_stdout = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_client.exec_command.return_value = (None, mock_stdout, None)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".pub", delete=False) as f:
            f.write("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAATEST test@example.com\n")
            pub_key_path = f.name

        try:
            result = deploy_public_key(
                "test.host.com", "testuser", pub_key_path, password="testpass"
            )
            assert result is True

            # Verify SSH connection was attempted
            mock_client.connect.assert_called()
            mock_client.exec_command.assert_called()
            mock_client.close.assert_called()
        finally:
            os.unlink(pub_key_path)


class TestUpdateSSHConfig:
    """Test SSH config file management."""

    def test_update_ssh_config_new_entry(self):
        """Test adding new SSH config entry."""
        with tempfile.TemporaryDirectory() as temp_dir:
            ssh_dir = Path(temp_dir) / ".ssh"
            ssh_config_path = ssh_dir / "config"

            with patch("clustrix.ssh_utils.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)

                update_ssh_config(
                    "test.host.com", "testuser", "/path/to/key", "testalias"
                )

                # Verify config file was created
                assert ssh_config_path.exists()

                # Verify content
                with open(ssh_config_path, "r") as f:
                    content = f.read()

                assert "Host testalias" in content
                assert "HostName test.host.com" in content
                assert "User testuser" in content
                assert "IdentityFile /path/to/key" in content

    def test_update_ssh_config_existing_entry(self):
        """Test skipping existing SSH config entry."""
        with tempfile.TemporaryDirectory() as temp_dir:
            ssh_dir = Path(temp_dir) / ".ssh"
            ssh_dir.mkdir(mode=0o700)
            ssh_config_path = ssh_dir / "config"

            # Create existing config
            with open(ssh_config_path, "w") as f:
                f.write("Host testalias\n    HostName existing.host.com\n")

            with patch("clustrix.ssh_utils.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)

                update_ssh_config(
                    "test.host.com", "testuser", "/path/to/key", "testalias"
                )

                # Verify original content is preserved
                with open(ssh_config_path, "r") as f:
                    content = f.read()

                assert "existing.host.com" in content
                assert "test.host.com" not in content


class TestSetupSSHKeys:
    """Test end-to-end SSH key setup."""

    def test_setup_ssh_keys_missing_host(self):
        """Test error when host is missing."""
        config = ClusterConfig(cluster_type="slurm", username="testuser")

        result = setup_ssh_keys(config, "testpass")
        assert not result["success"]
        assert "cluster_host must be specified" in result["error"]

    def test_setup_ssh_keys_missing_username(self):
        """Test error when username is missing."""
        config = ClusterConfig(cluster_type="slurm", cluster_host="test.host.com")

        result = setup_ssh_keys(config, "testpass")
        assert not result["success"]
        assert "username must be specified" in result["error"]

    @patch("clustrix.ssh_utils.detect_existing_ssh_key")
    def test_setup_ssh_keys_existing_key_found(self, mock_detect):
        """Test when existing SSH key is found."""
        mock_detect.return_value = "/home/user/.ssh/id_rsa"

        config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.host.com", username="testuser"
        )

        result = setup_ssh_keys(config, "testpass")

        assert result["success"]
        assert result["key_path"] == "/home/user/.ssh/id_rsa"
        assert result["key_already_existed"]
        assert config.key_file == "/home/user/.ssh/id_rsa"
        mock_detect.assert_called_once_with("test.host.com", "testuser", 22)

    @patch("clustrix.ssh_utils.detect_existing_ssh_key")
    @patch("clustrix.ssh_utils.generate_ssh_key")
    @patch("clustrix.ssh_utils.deploy_public_key")
    @patch("clustrix.ssh_utils.update_ssh_config")
    def test_setup_ssh_keys_full_workflow(
        self, mock_update_config, mock_deploy, mock_generate, mock_detect
    ):
        """Test complete SSH key setup workflow."""
        # No existing key found
        mock_detect.side_effect = [
            None,
            "/home/user/.ssh/id_ed25519_test",
        ]  # Before and after generation

        # Key generation succeeds
        mock_generate.return_value = (
            "/home/user/.ssh/id_ed25519_test",
            "/home/user/.ssh/id_ed25519_test.pub",
        )

        # Deployment succeeds
        mock_deploy.return_value = True

        config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.host.com", username="testuser"
        )

        result = setup_ssh_keys(config, cluster_alias="testcluster")

        assert result.key_file == "/home/user/.ssh/id_ed25519_test"
        mock_generate.assert_called_once()
        mock_deploy.assert_called_once()
        mock_update_config.assert_called_once()


class TestGetSSHKeyInfo:
    """Test SSH key information retrieval."""

    @patch("subprocess.run")
    def test_get_ssh_key_info_success(self, mock_run):
        """Test successful SSH key info retrieval."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="256 SHA256:AAAA test@example.com (ED25519)\n"
        )

        result = get_ssh_key_info("/path/to/key")

        expected = {
            "path": "/path/to/key",
            "type": "ED25519",
            "bit_size": "256",
            "fingerprint": "SHA256:AAAA",
            "comment": "test@example.com",
            "exists": True,
        }

        assert result == expected

    @patch("subprocess.run")
    def test_get_ssh_key_info_failure(self, mock_run):
        """Test SSH key info retrieval failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh-keygen")

        result = get_ssh_key_info("/path/to/key")

        expected = {"path": "/path/to/key", "exists": False}

        assert result == expected


class TestListSSHKeys:
    """Test SSH key listing functionality."""

    @patch("clustrix.ssh_utils.find_ssh_keys")
    @patch("clustrix.ssh_utils.get_ssh_key_info")
    def test_list_ssh_keys(self, mock_get_info, mock_find_keys):
        """Test SSH key listing."""
        mock_find_keys.return_value = ["/path/to/key1", "/path/to/key2"]
        mock_get_info.side_effect = [
            {"path": "/path/to/key1", "type": "RSA", "exists": True},
            {"path": "/path/to/key2", "type": "ED25519", "exists": True},
        ]

        result = list_ssh_keys()

        assert len(result) == 2
        assert result[0]["path"] == "/path/to/key1"
        assert result[1]["path"] == "/path/to/key2"
        assert mock_get_info.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__])
