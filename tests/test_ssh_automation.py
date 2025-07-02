"""
Tests for SSH key automation functionality.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from clustrix.ssh_utils import (
    setup_ssh_keys,
    detect_working_ssh_key,
    validate_ssh_key,
    generate_ssh_key_pair,
    deploy_ssh_key,
    find_ssh_keys,
)
from clustrix.config import ClusterConfig


class TestSSHKeyAutomation:
    """Test SSH key automation functionality."""

    def test_setup_ssh_keys_missing_config(self):
        """Test setup_ssh_keys with missing config parameters."""
        config = ClusterConfig()

        result = setup_ssh_keys(config, password="test")

        assert not result["success"]
        assert "cluster_host must be specified" in result["error"]

    def test_setup_ssh_keys_missing_username(self):
        """Test setup_ssh_keys with missing username."""
        config = ClusterConfig(cluster_host="test.example.com")

        result = setup_ssh_keys(config, password="test")

        assert not result["success"]
        assert "username must be specified" in result["error"]

    @patch("clustrix.ssh_utils.detect_existing_ssh_key")
    def test_setup_ssh_keys_existing_key(self, mock_detect):
        """Test setup_ssh_keys when existing key is found."""
        mock_detect.return_value = "/home/user/.ssh/id_ed25519"

        config = ClusterConfig(
            cluster_host="test.example.com",
            username="testuser",
        )

        result = setup_ssh_keys(config, password="test")

        assert result["success"]
        assert result["key_already_existed"]
        assert result["key_path"] == "/home/user/.ssh/id_ed25519"
        assert not result["key_deployed"]
        assert result["connection_tested"]

    @patch("clustrix.ssh_utils.detect_existing_ssh_key")
    @patch("clustrix.ssh_utils.generate_ssh_key")
    @patch("clustrix.ssh_utils.deploy_public_key")
    @patch("clustrix.ssh_utils.update_ssh_config")
    @patch("pathlib.Path.exists")
    def test_setup_ssh_keys_new_key_generation(
        self, mock_exists, mock_update_ssh, mock_deploy, mock_generate, mock_detect
    ):
        """Test setup_ssh_keys with new key generation."""
        # Mock no existing key found
        mock_detect.return_value = None
        # Mock key doesn't exist initially
        mock_exists.return_value = False
        # Mock successful key generation
        mock_generate.return_value = ("/path/to/key", "/path/to/key.pub")
        # Mock successful deployment
        mock_deploy.return_value = True

        config = ClusterConfig(
            cluster_host="test.example.com",
            username="testuser",
        )

        result = setup_ssh_keys(config, password="test", cluster_alias="test_alias")

        assert result["success"]
        assert not result["key_already_existed"]
        assert result["key_deployed"]
        assert "key_generated" in result["details"]
        mock_generate.assert_called_once()
        mock_deploy.assert_called_once()

    @patch("clustrix.ssh_utils.detect_existing_ssh_key")
    @patch("clustrix.ssh_utils.generate_ssh_key")
    @patch("clustrix.ssh_utils.deploy_public_key")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    def test_setup_ssh_keys_force_refresh(
        self, mock_unlink, mock_exists, mock_deploy, mock_generate, mock_detect
    ):
        """Test setup_ssh_keys with force refresh."""
        # Mock no existing key detected (force refresh bypasses detection)
        mock_detect.return_value = None

        # Create a side effect that returns True first (key exists), then False (after deletion)
        mock_exists.side_effect = [True, False]

        # Mock successful key generation
        mock_generate.return_value = ("/path/to/key", "/path/to/key.pub")
        # Mock successful deployment
        mock_deploy.return_value = True

        config = ClusterConfig(
            cluster_host="test.example.com",
            username="testuser",
        )

        result = setup_ssh_keys(config, password="test", force_refresh=True)

        assert result["success"]
        # Should generate new key because force refresh is enabled
        mock_unlink.assert_called()  # Old key removed
        mock_generate.assert_called_once()

    @patch("paramiko.SSHClient")
    def test_validate_ssh_key_success(self, mock_ssh_client):
        """Test successful SSH key validation."""
        mock_client = Mock()
        mock_ssh_client.return_value = mock_client

        result = validate_ssh_key("test.example.com", "testuser", "/path/to/key")

        assert result is True
        mock_client.connect.assert_called_once_with(
            hostname="test.example.com",
            username="testuser",
            port=22,
            key_filename="/path/to/key",
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        mock_client.close.assert_called_once()

    @patch("paramiko.SSHClient")
    def test_validate_ssh_key_failure(self, mock_ssh_client):
        """Test SSH key validation failure."""
        mock_client = Mock()
        mock_client.connect.side_effect = Exception("Connection failed")
        mock_ssh_client.return_value = mock_client

        result = validate_ssh_key("test.example.com", "testuser", "/path/to/key")

        assert result is False

    def test_detect_working_ssh_key_alias(self):
        """Test that detect_working_ssh_key is an alias for detect_existing_ssh_key."""
        # This is a simple alias function, just test it exists and returns same result
        with patch("clustrix.ssh_utils.detect_existing_ssh_key") as mock_detect:
            mock_detect.return_value = "/test/key"

            result = detect_working_ssh_key("test.com", "user", 22)

            assert result == "/test/key"
            mock_detect.assert_called_once_with("test.com", "user", 22)

    @patch("subprocess.run")
    def test_generate_ssh_key_pair(self, mock_run):
        """Test SSH key pair generation."""
        with patch("clustrix.ssh_utils.generate_ssh_key") as mock_generate:
            mock_generate.return_value = ("/path/key", "/path/key.pub")

            result = generate_ssh_key_pair("test_key")

            assert result == ("/path/key", "/path/key.pub")
            mock_generate.assert_called_once()

    def test_deploy_ssh_key_alias(self):
        """Test that deploy_ssh_key is an alias for deploy_public_key."""
        with patch("clustrix.ssh_utils.deploy_public_key") as mock_deploy:
            mock_deploy.return_value = True

            result = deploy_ssh_key("host", "user", "pass", "/key.pub", 22)

            assert result is True
            mock_deploy.assert_called_once_with("host", "user", "/key.pub", 22, "pass")

    @patch("pathlib.Path.home")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_file")
    @patch("pathlib.Path.stat")
    @patch("builtins.open")
    def test_find_ssh_keys(
        self, mock_open, mock_stat, mock_is_file, mock_exists, mock_home
    ):
        """Test finding existing SSH keys."""
        # Mock home directory
        mock_home.return_value = Path("/home/user")

        # Mock .ssh directory exists
        mock_exists.return_value = True
        mock_is_file.return_value = True

        # Mock file permissions (600)
        mock_stat_obj = Mock()
        mock_stat_obj.st_mode = 0o100600  # Regular file with 600 permissions
        mock_stat.return_value = mock_stat_obj

        # Mock file content
        mock_file = Mock()
        mock_file.read.return_value = "-----BEGIN PRIVATE KEY-----"
        mock_open.return_value.__enter__.return_value = mock_file

        with patch("platform.system", return_value="Linux"):
            result = find_ssh_keys()

        # Should find the standard key files
        assert isinstance(result, list)
        # Length depends on which keys exist in the mock


class TestSSHKeyNaming:
    """Test SSH key naming conventions."""

    def test_key_naming_with_alias(self):
        """Test SSH key naming with cluster alias."""
        config = ClusterConfig(
            cluster_host="cluster.example.com",
            username="testuser",
        )

        with patch(
            "clustrix.ssh_utils.detect_existing_ssh_key", return_value=None
        ), patch("pathlib.Path.exists", return_value=False), patch(
            "clustrix.ssh_utils.generate_ssh_key"
        ) as mock_generate, patch(
            "clustrix.ssh_utils.deploy_public_key", return_value=True
        ):

            mock_generate.return_value = ("/path/to/key", "/path/to/key.pub")

            result = setup_ssh_keys(
                config, password="test", cluster_alias="my_cluster", key_type="ed25519"
            )

            # Key path should include alias in name
            expected_name = "id_ed25519_clustrix_testuser_my_cluster"
            assert expected_name in result["key_path"]

    def test_key_naming_without_alias(self):
        """Test SSH key naming without cluster alias."""
        config = ClusterConfig(
            cluster_host="cluster.example.com",
            username="testuser",
        )

        with patch(
            "clustrix.ssh_utils.detect_existing_ssh_key", return_value=None
        ), patch("pathlib.Path.exists", return_value=False), patch(
            "clustrix.ssh_utils.generate_ssh_key"
        ) as mock_generate, patch(
            "clustrix.ssh_utils.deploy_public_key", return_value=True
        ):

            mock_generate.return_value = ("/path/to/key", "/path/to/key.pub")

            result = setup_ssh_keys(config, password="test", key_type="ed25519")

            # Key path should include cleaned hostname
            expected_name = "id_ed25519_clustrix_testuser_cluster_example_com"
            assert expected_name in result["key_path"]


class TestSSHKeyErrorHandling:
    """Test error handling in SSH key operations."""

    def test_deployment_failure(self):
        """Test handling of deployment failure."""
        config = ClusterConfig(
            cluster_host="test.example.com",
            username="testuser",
        )

        with patch(
            "clustrix.ssh_utils.detect_existing_ssh_key", return_value=None
        ), patch("pathlib.Path.exists", return_value=False), patch(
            "clustrix.ssh_utils.generate_ssh_key"
        ) as mock_generate, patch(
            "clustrix.ssh_utils.deploy_public_key", return_value=False
        ):

            mock_generate.return_value = ("/path/to/key", "/path/to/key.pub")

            result = setup_ssh_keys(config, password="test")

            assert not result["success"]
            assert "Failed to deploy public key" in result["error"]

    def test_key_generation_failure(self):
        """Test handling of key generation failure."""
        from clustrix.ssh_utils import SSHKeyGenerationError

        config = ClusterConfig(
            cluster_host="test.example.com",
            username="testuser",
        )

        with patch(
            "clustrix.ssh_utils.detect_existing_ssh_key", return_value=None
        ), patch("pathlib.Path.exists", return_value=False), patch(
            "clustrix.ssh_utils.generate_ssh_key"
        ) as mock_generate:

            mock_generate.side_effect = SSHKeyGenerationError("Key generation failed")

            result = setup_ssh_keys(config, password="test")

            assert not result["success"]
            assert "Failed to generate SSH key" in result["error"]
