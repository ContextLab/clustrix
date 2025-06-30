"""
Simplified comprehensive executor tests focusing only on real functionality.

Tests the actual methods that exist in ClusterExecutor without assumptions.
"""

import os
import time
import tempfile
import pickle
import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig


def global_test_function(x):
    """Global test function that can be pickled."""
    return x * 2


class TestClusterExecutorReal:
    """Test real ClusterExecutor functionality without assumptions."""

    @pytest.fixture
    def base_config(self):
        """Base cluster configuration."""
        return ClusterConfig(
            cluster_host="test.cluster.com",
            username="testuser",
            key_file="~/.ssh/test_key",
            remote_work_dir="/home/testuser/work",
            cluster_type="slurm",
        )

    @pytest.fixture
    def mock_ssh_setup(self):
        """Mock SSH connection setup."""
        with patch("paramiko.SSHClient") as mock_ssh_class:
            mock_ssh = Mock()
            mock_ssh_class.return_value = mock_ssh
            mock_sftp = Mock()
            mock_ssh.open_sftp.return_value = mock_sftp

            yield {
                "ssh_class": mock_ssh_class,
                "ssh_client": mock_ssh,
                "sftp_client": mock_sftp,
            }

    @pytest.fixture
    def sample_func_data(self):
        """Sample function data for testing."""
        return {
            "func": global_test_function,
            "args": (5,),
            "kwargs": {},
            "requirements": {"numpy": "1.21.0"},
        }

    def test_job_submission_routing(
        self, base_config, sample_func_data, mock_ssh_setup
    ):
        """Test that submit_job routes to correct cluster type methods."""
        executor = ClusterExecutor(base_config)

        # Mock the private methods that actually exist
        executor._submit_slurm_job = Mock(return_value="job_123")
        executor.connect = Mock()

        result = executor.submit_job(sample_func_data, {})
        assert result == "job_123"
        executor._submit_slurm_job.assert_called_once()

    def test_result_retrieval_success(self, base_config, mock_ssh_setup):
        """Test successful result retrieval."""
        base_config.cleanup_on_success = False
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "123456": {"remote_dir": "/home/testuser/work/job_123456"}
        }

        result_data = 42

        with patch("tempfile.NamedTemporaryFile") as mock_tempfile, patch(
            "builtins.open", mock_open(read_data=pickle.dumps(result_data))
        ), patch("os.unlink"), patch("os.path.exists", return_value=True):
            mock_file = Mock()
            mock_file.name = "/tmp/result.pkl"
            mock_tempfile.return_value.__enter__.return_value = mock_file

            executor._download_file = Mock()
            executor._check_job_status = Mock(return_value="completed")

            result = executor.get_result("123456")
            assert result == 42

    def test_result_retrieval_error(self, base_config, mock_ssh_setup):
        """Test result retrieval when job failed."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "123456": {"remote_dir": "/home/testuser/work/job_123456"}
        }

        with patch("tempfile.NamedTemporaryFile"), patch("os.unlink"):
            executor._check_job_status = Mock(return_value="failed")
            executor._extract_original_exception = Mock(
                return_value=ValueError("Test error")
            )

            with pytest.raises(ValueError, match="Test error"):
                executor.get_result("123456")

    def test_job_cancellation_slurm(self, base_config, mock_ssh_setup):
        """Test SLURM job cancellation."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor._execute_remote_command = Mock(return_value=("", ""))
        executor.active_jobs = {"123456": {"status": "running"}}

        executor.cancel_job("123456")
        executor._execute_remote_command.assert_called_with("scancel 123456")
        assert "123456" not in executor.active_jobs

    def test_connection_management(self, base_config):
        """Test connection and disconnection."""
        executor = ClusterExecutor(base_config)

        # Mock the SSH setup
        executor._setup_ssh_connection = Mock()
        # ssh_client should be None initially for connect to call _setup_ssh_connection
        executor.ssh_client = None
        executor.sftp_client = None

        # Test connect
        executor.connect()
        executor._setup_ssh_connection.assert_called_once()

        # Now set up mocks for disconnect test
        mock_ssh = Mock()
        mock_sftp = Mock()
        executor.ssh_client = mock_ssh
        executor.sftp_client = mock_sftp

        # Test disconnect
        executor.disconnect()
        mock_ssh.close.assert_called_once()
        mock_sftp.close.assert_called_once()
        # After disconnect, clients should be None
        assert executor.ssh_client is None
        assert executor.sftp_client is None

    def test_job_status_checking(self, base_config, mock_ssh_setup):
        """Test job status checking for different cluster types."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]

        # Test SLURM status
        executor._execute_remote_command = Mock(return_value=("COMPLETED", ""))
        status = executor._check_job_status("123456")
        assert status == "completed"

        # Test running status
        executor._execute_remote_command = Mock(return_value=("RUNNING", ""))
        status = executor._check_job_status("123456")
        assert status == "running"

    def test_execute_function_wrapper(self, base_config):
        """Test the execute method wrapper."""
        executor = ClusterExecutor(base_config)

        # Mock the submit_job and wait_for_result methods
        executor.submit_job = Mock(return_value="job_123")
        executor.wait_for_result = Mock(return_value=10)

        result = executor.execute(global_test_function, (5,), {})

        assert result == 10
        executor.submit_job.assert_called_once()
        executor.wait_for_result.assert_called_with("job_123")

    def test_job_status_public_method(self, base_config):
        """Test the public get_job_status method."""
        executor = ClusterExecutor(base_config)
        executor._check_job_status = Mock(return_value="running")

        status = executor.get_job_status("job_123")
        assert status == "running"
        executor._check_job_status.assert_called_with("job_123")

    def test_remote_command_execution(self, base_config, mock_ssh_setup):
        """Test remote command execution."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]

        # Mock command execution
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()
        mock_stdout.read.return_value = b"command output"
        mock_stderr.read.return_value = b"error output"

        executor.ssh_client.exec_command.return_value = (
            mock_stdin,
            mock_stdout,
            mock_stderr,
        )

        stdout, stderr = executor._execute_remote_command("ls -la")

        assert stdout == "command output"
        assert stderr == "error output"
        executor.ssh_client.exec_command.assert_called_with("ls -la")

    def test_file_operations(self, base_config, mock_ssh_setup):
        """Test basic file operations."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        # Test file existence check - this creates its own SFTP connection
        mock_sftp = Mock()
        executor.ssh_client.open_sftp.return_value = mock_sftp
        mock_sftp.stat.return_value = Mock()  # Any object indicates file exists
        mock_sftp.close.return_value = None

        assert executor._remote_file_exists("/remote/path/file.txt") is True

        # Test file not exists
        mock_sftp.stat.side_effect = FileNotFoundError()
        assert executor._remote_file_exists("/remote/path/nonexistent.txt") is False

        # Test file upload (creates its own SFTP connection)
        mock_upload_sftp = Mock()
        executor.ssh_client.open_sftp.return_value = mock_upload_sftp
        mock_upload_sftp.close.return_value = None

        executor._upload_file("/local/file.txt", "/remote/file.txt")
        mock_upload_sftp.put.assert_called_with("/local/file.txt", "/remote/file.txt")

        # Test file download (creates its own SFTP connection)
        mock_download_sftp = Mock()
        executor.ssh_client.open_sftp.return_value = mock_download_sftp
        mock_download_sftp.close.return_value = None

        executor._download_file("/remote/file.txt", "/local/file.txt")
        mock_download_sftp.get.assert_called_with("/remote/file.txt", "/local/file.txt")
