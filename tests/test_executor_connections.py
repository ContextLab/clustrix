"""Tests for clustrix.executor_connections module.

This module tests SSH and Kubernetes connection management functionality,
focusing on connection establishment, file transfers, and lifecycle management.
"""

import os
import tempfile
import time
import pytest
from unittest.mock import Mock, patch, MagicMock, call, mock_open
from clustrix.executor_connections import ConnectionManager
from clustrix.config import ClusterConfig


class TestConnectionManager:
    """Test ConnectionManager class for SSH and Kubernetes connections."""

    @pytest.fixture
    def ssh_config(self):
        """Create config for SSH-based clusters."""
        return ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.cluster.com",
            cluster_port=22,
            username="testuser",
            key_file="~/.ssh/test_key",
            remote_work_dir="/tmp/test_clustrix",
        )

    @pytest.fixture
    def k8s_config(self):
        """Create config for Kubernetes clusters."""
        return ClusterConfig(
            cluster_type="kubernetes",
            k8s_cluster_name="test-cluster",
            k8s_namespace="default",
        )

    @pytest.fixture
    def k8s_auto_provision_config(self):
        """Create config for auto-provisioned Kubernetes clusters."""
        return ClusterConfig(
            cluster_type="kubernetes",
            auto_provision_k8s=True,
            k8s_provider="aws",
            k8s_cluster_name="test-auto-cluster",
            k8s_region="us-west-2",
            k8s_node_count=2,
            k8s_node_type="t3.medium",
        )

    @pytest.fixture
    def connection_manager_ssh(self, ssh_config):
        """Create ConnectionManager for SSH clusters."""
        return ConnectionManager(ssh_config)

    @pytest.fixture
    def connection_manager_k8s(self, k8s_config):
        """Create ConnectionManager for Kubernetes clusters."""
        return ConnectionManager(k8s_config)

    def test_init(self, ssh_config):
        """Test ConnectionManager initialization."""
        manager = ConnectionManager(ssh_config)

        assert manager.config == ssh_config
        assert manager.ssh_client is None
        assert manager.sftp_client is None
        assert manager.k8s_client is None

    @patch("paramiko.SSHClient")
    def test_setup_ssh_connection_with_key(
        self, mock_ssh_class, connection_manager_ssh
    ):
        """Test SSH connection setup with key file authentication."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        connection_manager_ssh.setup_ssh_connection()

        # Verify SSH client creation and configuration
        mock_ssh_class.assert_called_once()
        mock_ssh.set_missing_host_key_policy.assert_called_once()

        # Verify connection call
        expected_kwargs = {
            "hostname": "test.cluster.com",
            "port": 22,
            "username": "testuser",
            "key_filename": "~/.ssh/test_key",
        }
        mock_ssh.connect.assert_called_once_with(**expected_kwargs)

        # Verify SFTP setup
        mock_ssh.open_sftp.assert_called_once()
        assert connection_manager_ssh.ssh_client == mock_ssh
        assert connection_manager_ssh.sftp_client == mock_sftp

    @patch.dict(os.environ, {"USER": "envuser"})
    @patch("paramiko.SSHClient")
    def test_setup_ssh_connection_with_password(self, mock_ssh_class):
        """Test SSH connection setup with password authentication."""
        config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.cluster.com", password="testpass"
        )
        manager = ConnectionManager(config)

        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_ssh_class.return_value = mock_ssh

        manager.setup_ssh_connection()

        expected_kwargs = {
            "hostname": "test.cluster.com",
            "port": 22,
            "username": "envuser",
            "password": "testpass",
        }
        mock_ssh.connect.assert_called_once_with(**expected_kwargs)

    @pytest.mark.skip(reason="Requires credential manager module")
    def test_setup_ssh_connection_with_credential_manager(self):
        """Test SSH connection setup using credential manager (skipped due to dynamic import)."""
        pass

    @pytest.mark.skip(reason="Requires credential manager module")
    def test_setup_ssh_connection_with_credential_manager_key(self):
        """Test SSH connection setup using credential manager with key file (skipped due to dynamic import)."""
        pass

    def test_setup_ssh_connection_no_host(self):
        """Test SSH connection setup fails without cluster_host."""
        config = ClusterConfig(cluster_type="slurm")
        manager = ConnectionManager(config)

        with pytest.raises(ValueError, match="cluster_host must be specified"):
            manager.setup_ssh_connection()

    @patch("paramiko.SSHClient")
    def test_setup_ssh_connection_failure(self, mock_ssh_class, connection_manager_ssh):
        """Test SSH connection setup failure handling."""
        mock_ssh = Mock()
        mock_ssh.connect.side_effect = Exception("Connection failed")
        mock_ssh_class.return_value = mock_ssh

        with pytest.raises(Exception, match="Connection failed"):
            connection_manager_ssh.setup_ssh_connection()

    def test_setup_kubernetes_basic(self, connection_manager_k8s):
        """Test basic Kubernetes client setup."""
        with patch("kubernetes.config.load_kube_config") as mock_k8s_config, patch(
            "kubernetes.client.ApiClient"
        ) as mock_api_client_class:

            mock_api_client = Mock()
            mock_api_client_class.return_value = mock_api_client

            connection_manager_k8s.setup_kubernetes()

            mock_k8s_config.assert_called_once()
            mock_api_client_class.assert_called_once()
            assert connection_manager_k8s.k8s_client == mock_api_client

    def test_setup_kubernetes_import_error(self, connection_manager_k8s):
        """Test Kubernetes setup with missing kubernetes package."""
        with patch.dict("sys.modules", {"kubernetes": None}):
            with pytest.raises(ImportError, match="kubernetes package required"):
                connection_manager_k8s.setup_kubernetes()

    @pytest.mark.skip(reason="Requires Kubernetes cluster provisioner module")
    def test_setup_kubernetes_auto_provision(self):
        """Test Kubernetes setup with auto-provisioning (skipped due to dynamic import)."""
        pass

    @pytest.mark.skip(reason="Requires cloud provider manager module")
    def test_setup_kubernetes_cloud_auto_configure(self):
        """Test Kubernetes setup with cloud provider auto-configuration (skipped due to dynamic import)."""
        pass

    def test_execute_remote_command_no_client(self, connection_manager_ssh):
        """Test remote command execution without SSH client."""
        with pytest.raises(RuntimeError, match="SSH client not connected"):
            connection_manager_ssh.execute_remote_command("ls")

    @patch("paramiko.SSHClient")
    def test_execute_remote_command_success(
        self, mock_ssh_class, connection_manager_ssh
    ):
        """Test successful remote command execution."""
        mock_ssh = Mock()
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"file1\nfile2\n"
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)
        connection_manager_ssh.ssh_client = mock_ssh

        stdout, stderr = connection_manager_ssh.execute_remote_command("ls")

        assert stdout == "file1\nfile2\n"
        assert stderr == ""
        mock_ssh.exec_command.assert_called_once_with("ls")

    def test_upload_file_no_client(self, connection_manager_ssh):
        """Test file upload without SSH client."""
        with pytest.raises(RuntimeError, match="SSH client not connected"):
            connection_manager_ssh.upload_file("/local/path", "/remote/path")

    @patch("paramiko.SSHClient")
    def test_upload_file_success(self, mock_ssh_class, connection_manager_ssh):
        """Test successful file upload."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp
        connection_manager_ssh.ssh_client = mock_ssh

        connection_manager_ssh.upload_file("/local/path", "/remote/path")

        mock_ssh.open_sftp.assert_called_once()
        mock_sftp.put.assert_called_once_with("/local/path", "/remote/path")
        mock_sftp.close.assert_called_once()

    def test_download_file_no_client(self, connection_manager_ssh):
        """Test file download without SSH client."""
        with pytest.raises(RuntimeError, match="SSH client not connected"):
            connection_manager_ssh.download_file("/remote/path", "/local/path")

    @patch("paramiko.SSHClient")
    def test_download_file_success(self, mock_ssh_class, connection_manager_ssh):
        """Test successful file download."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp
        connection_manager_ssh.ssh_client = mock_ssh

        connection_manager_ssh.download_file("/remote/path", "/local/path")

        mock_ssh.open_sftp.assert_called_once()
        mock_sftp.get.assert_called_once_with("/remote/path", "/local/path")
        mock_sftp.close.assert_called_once()

    def test_create_remote_file_no_client(self, connection_manager_ssh):
        """Test remote file creation without SSH client."""
        with pytest.raises(RuntimeError, match="SSH client not connected"):
            connection_manager_ssh.create_remote_file("/remote/path", "content")

    @patch("paramiko.SSHClient")
    def test_create_remote_file_success(self, mock_ssh_class, connection_manager_ssh):
        """Test successful remote file creation."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_file = Mock()
        # Mock the context manager properly
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_file)
        mock_context.__exit__ = Mock(return_value=False)
        mock_sftp.open.return_value = mock_context
        mock_ssh.open_sftp.return_value = mock_sftp
        connection_manager_ssh.ssh_client = mock_ssh

        connection_manager_ssh.create_remote_file("/remote/path", "test content")

        mock_ssh.open_sftp.assert_called_once()
        mock_sftp.open.assert_called_once_with("/remote/path", "w")
        mock_file.write.assert_called_once_with("test content")
        mock_sftp.close.assert_called_once()

    def test_remote_file_exists_no_client(self, connection_manager_ssh):
        """Test remote file existence check without SSH client."""
        result = connection_manager_ssh.remote_file_exists("/remote/path")
        assert result is False

    @patch("paramiko.SSHClient")
    def test_remote_file_exists_true(self, mock_ssh_class, connection_manager_ssh):
        """Test remote file exists returns True when file exists."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_sftp.stat.return_value = Mock()  # File exists
        mock_ssh.open_sftp.return_value = mock_sftp
        connection_manager_ssh.ssh_client = mock_ssh

        result = connection_manager_ssh.remote_file_exists("/remote/path")

        assert result is True
        mock_ssh.open_sftp.assert_called_once()
        mock_sftp.stat.assert_called_once_with("/remote/path")
        mock_sftp.close.assert_called_once()

    @patch("paramiko.SSHClient")
    def test_remote_file_exists_false(self, mock_ssh_class, connection_manager_ssh):
        """Test remote file exists returns False when file doesn't exist."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        mock_sftp.stat.side_effect = Exception("File not found")
        mock_ssh.open_sftp.return_value = mock_sftp
        connection_manager_ssh.ssh_client = mock_ssh

        result = connection_manager_ssh.remote_file_exists("/remote/path")

        assert result is False

    @patch("clustrix.executor_connections.ConnectionManager.setup_ssh_connection")
    def test_connect_ssh_clusters(self, mock_setup_ssh, connection_manager_ssh):
        """Test connect method for SSH-based clusters."""
        connection_manager_ssh.connect()
        mock_setup_ssh.assert_called_once()

    @patch("clustrix.executor_connections.ConnectionManager.setup_kubernetes")
    def test_connect_kubernetes(self, mock_setup_k8s, connection_manager_k8s):
        """Test connect method for Kubernetes clusters."""
        connection_manager_k8s.connect()
        mock_setup_k8s.assert_called_once()

    @patch("clustrix.executor_connections.ConnectionManager.setup_kubernetes")
    def test_connect_kubernetes_already_connected(
        self, mock_setup_k8s, connection_manager_k8s
    ):
        """Test connect method when Kubernetes client already exists."""
        connection_manager_k8s.k8s_client = Mock()  # Already connected
        connection_manager_k8s.connect()
        mock_setup_k8s.assert_not_called()

    def test_disconnect(self, connection_manager_ssh):
        """Test disconnect method."""
        mock_sftp = Mock()
        mock_ssh = Mock()

        connection_manager_ssh.sftp_client = mock_sftp
        connection_manager_ssh.ssh_client = mock_ssh

        connection_manager_ssh.disconnect()

        mock_sftp.close.assert_called_once()
        mock_ssh.close.assert_called_once()
        assert connection_manager_ssh.sftp_client is None
        assert connection_manager_ssh.ssh_client is None

    def test_disconnect_no_clients(self, connection_manager_ssh):
        """Test disconnect method with no active clients."""
        # Should not raise any errors
        connection_manager_ssh.disconnect()

    @patch("clustrix.executor_connections.os.path.exists")
    @patch("clustrix.executor_connections.os.unlink")
    def test_cleanup_auto_provisioned_cluster_with_temp_config(
        self, mock_unlink, mock_exists, connection_manager_k8s
    ):
        """Test cleanup of auto-provisioned cluster with temp config."""
        mock_exists.return_value = True
        connection_manager_k8s._k8s_temp_config_path = "/tmp/test_config.yaml"

        connection_manager_k8s.cleanup_auto_provisioned_cluster()

        mock_exists.assert_called_once_with("/tmp/test_config.yaml")
        mock_unlink.assert_called_once_with("/tmp/test_config.yaml")

    def test_cleanup_auto_provisioned_cluster_with_provisioner(
        self, connection_manager_k8s
    ):
        """Test cleanup of auto-provisioned cluster with provisioner."""
        # Mock provisioner and cluster info
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info
        connection_manager_k8s.config.k8s_cleanup_on_exit = True

        mock_provisioner.destroy_cluster_infrastructure.return_value = True

        connection_manager_k8s.cleanup_auto_provisioned_cluster()

        mock_provisioner.destroy_cluster_infrastructure.assert_called_once_with(
            "test-cluster"
        )

    def test_cleanup_auto_provisioned_cluster_preserve(self, connection_manager_k8s):
        """Test cleanup preserves cluster when k8s_cleanup_on_exit is False."""
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info
        connection_manager_k8s.config.k8s_cleanup_on_exit = False

        connection_manager_k8s.cleanup_auto_provisioned_cluster()

        # Should not destroy cluster
        mock_provisioner.destroy_cluster_infrastructure.assert_not_called()

    def test_get_cluster_status_no_managed_cluster(self, connection_manager_k8s):
        """Test get_cluster_status when no managed cluster exists."""
        status = connection_manager_k8s.get_cluster_status()

        expected = {"status": "NO_MANAGED_CLUSTER", "ready": False}
        assert status == expected

    def test_get_cluster_status_success(self, connection_manager_k8s):
        """Test successful get_cluster_status."""
        mock_provisioner = Mock()
        mock_cluster_info = {
            "cluster_id": "test-cluster",
            "provider": "aws",
            "endpoint": "https://test.eks.amazonaws.com",
        }

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        mock_status = {"status": "ACTIVE", "ready_for_jobs": True}
        mock_provisioner.get_cluster_status.return_value = mock_status

        result = connection_manager_k8s.get_cluster_status()

        expected = {
            "status": "ACTIVE",
            "ready": True,
            "cluster_name": "test-cluster",
            "provider": "aws",
            "endpoint": "https://test.eks.amazonaws.com",
        }
        assert result == expected

    def test_get_cluster_status_error(self, connection_manager_k8s):
        """Test get_cluster_status with error."""
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        mock_provisioner.get_cluster_status.side_effect = Exception("API Error")

        result = connection_manager_k8s.get_cluster_status()

        expected = {"status": "ERROR", "ready": False, "error": "API Error"}
        assert result == expected

    def test_ensure_cluster_ready_no_managed_cluster(self, connection_manager_k8s):
        """Test ensure_cluster_ready when no managed cluster exists."""
        result = connection_manager_k8s.ensure_cluster_ready()
        assert result is True  # Assume external cluster is ready

    @patch("clustrix.executor_connections.time")
    def test_ensure_cluster_ready_success(
        self, mock_time_module, connection_manager_k8s
    ):
        """Test successful ensure_cluster_ready."""
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        # Mock time progression
        mock_time_module.time.side_effect = [0, 30]  # Start time, check time
        mock_time_module.sleep.return_value = None

        # Mock cluster status check
        with patch.object(connection_manager_k8s, "get_cluster_status") as mock_status:
            mock_status.return_value = {"ready": True}

            result = connection_manager_k8s.ensure_cluster_ready(timeout=60)

        assert result is True
        mock_status.assert_called_once()

    @patch("clustrix.executor_connections.time")
    def test_ensure_cluster_ready_timeout(
        self, mock_time_module, connection_manager_k8s
    ):
        """Test ensure_cluster_ready timeout."""
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        # Mock time progression to exceed timeout
        mock_time_module.time.side_effect = [0, 60, 120]  # Exceed timeout
        mock_time_module.sleep.return_value = None

        # Mock cluster status check
        with patch.object(connection_manager_k8s, "get_cluster_status") as mock_status:
            mock_status.return_value = {"ready": False, "status": "CREATING"}

            result = connection_manager_k8s.ensure_cluster_ready(timeout=60)

        assert result is False

    def test_connect_ssh_already_connected(self, connection_manager_ssh):
        """Test connect method when SSH client already exists."""
        connection_manager_ssh.ssh_client = Mock()  # Already connected

        # Should not try to setup again
        with patch(
            "clustrix.executor_connections.ConnectionManager.setup_ssh_connection"
        ) as mock_setup:
            connection_manager_ssh.connect()
            mock_setup.assert_not_called()

    def test_connect_unknown_cluster_type(self):
        """Test connect method with unknown cluster type."""
        config = ClusterConfig(cluster_type="unknown")
        manager = ConnectionManager(config)

        # Should not raise an error, just do nothing
        manager.connect()
        assert manager.ssh_client is None
        assert manager.k8s_client is None

    def test_cleanup_auto_provisioned_cluster_no_attributes(
        self, connection_manager_k8s
    ):
        """Test cleanup when no auto-provisioned attributes exist."""
        # Should not raise errors when attributes don't exist
        connection_manager_k8s.cleanup_auto_provisioned_cluster()

    def test_cleanup_auto_provisioned_cluster_exception(self, connection_manager_k8s):
        """Test cleanup handles exceptions gracefully."""
        # Mock provisioner that raises exception during cleanup
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info
        connection_manager_k8s.config.k8s_cleanup_on_exit = True

        mock_provisioner.destroy_cluster_infrastructure.side_effect = Exception(
            "Cleanup failed"
        )

        # Should not raise exception, just log error
        connection_manager_k8s.cleanup_auto_provisioned_cluster()

    def test_get_cluster_status_no_cluster_name(self, connection_manager_k8s):
        """Test get_cluster_status when cluster info exists but no cluster_id."""
        mock_provisioner = Mock()
        mock_cluster_info = {}  # No cluster_id

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        status = connection_manager_k8s.get_cluster_status()

        expected = {"status": "UNKNOWN", "ready": False}
        assert status == expected

    def test_ensure_cluster_ready_no_cluster_name(self, connection_manager_k8s):
        """Test ensure_cluster_ready when cluster info exists but no cluster_id."""
        mock_provisioner = Mock()
        mock_cluster_info = {}  # No cluster_id

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        result = connection_manager_k8s.ensure_cluster_ready()

        assert result is False

    @patch("clustrix.executor_connections.time")
    def test_ensure_cluster_ready_exception_handling(
        self, mock_time_module, connection_manager_k8s
    ):
        """Test ensure_cluster_ready handles exceptions during status checks."""
        mock_provisioner = Mock()
        mock_cluster_info = {"cluster_id": "test-cluster"}

        connection_manager_k8s._k8s_provisioner = mock_provisioner
        connection_manager_k8s._k8s_cluster_info = mock_cluster_info

        # Mock time progression
        mock_time_module.time.side_effect = [
            0,
            10,
            60,
            120,
        ]  # Exceed timeout after exception
        mock_time_module.sleep.return_value = None

        # Mock cluster status check to raise exception first time, then timeout
        with patch.object(connection_manager_k8s, "get_cluster_status") as mock_status:
            mock_status.side_effect = [
                Exception("API Error"),
                {"ready": False, "status": "CREATING"},
            ]

            result = connection_manager_k8s.ensure_cluster_ready(timeout=60)

        assert result is False
        # Should have been called at least once (exception handling tested)
        assert mock_status.call_count >= 1
