"""
Comprehensive executor tests to improve coverage from 53% to 85%+.

These tests focus on covering the missing functionality in ClusterExecutor,
including job submission, status checking, result retrieval, and error handling
across different cluster types (SLURM, PBS, SGE, Kubernetes, SSH).
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


class TestClusterExecutorComprehensive:
    """Comprehensive tests for ClusterExecutor functionality."""

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
    def ssh_config(self):
        """SSH cluster configuration."""
        return ClusterConfig(
            cluster_host="ssh.cluster.com",
            username="testuser",
            key_file="~/.ssh/test_key",
            remote_work_dir="/home/testuser/work",
            cluster_type="ssh",
        )

    @pytest.fixture
    def k8s_config(self):
        """Kubernetes cluster configuration."""
        return ClusterConfig(
            cluster_type="kubernetes",
            k8s_namespace="default",
            k8s_image="python:3.11-slim",
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

    @pytest.fixture
    def sample_job_config(self):
        """Sample job configuration."""
        return {"cores": 4, "memory": "8GB", "time": "02:00:00", "partition": "compute"}

    def test_submit_job_routing(
        self, base_config, sample_func_data, sample_job_config, mock_ssh_setup
    ):
        """Test job submission routing to correct cluster type methods."""
        executor = ClusterExecutor(base_config)

        # Mock the specific job submission methods
        executor._submit_slurm_job = Mock(return_value="job_123")
        executor._submit_pbs_job = Mock(return_value="job_456")
        executor._submit_sge_job = Mock(return_value="job_789")
        executor._submit_k8s_job = Mock(return_value="job_k8s")
        executor._submit_ssh_job = Mock(return_value="job_ssh")
        executor.connect = Mock()

        # Test SLURM routing
        base_config.cluster_type = "slurm"
        result = executor.submit_job(sample_func_data, sample_job_config)
        assert result == "job_123"
        executor._submit_slurm_job.assert_called_once()

        # Test PBS routing
        base_config.cluster_type = "pbs"
        result = executor.submit_job(sample_func_data, sample_job_config)
        assert result == "job_456"
        executor._submit_pbs_job.assert_called_once()

        # Test SGE routing
        base_config.cluster_type = "sge"
        result = executor.submit_job(sample_func_data, sample_job_config)
        assert result == "job_789"
        executor._submit_sge_job.assert_called_once()

        # Test Kubernetes routing
        base_config.cluster_type = "kubernetes"
        result = executor.submit_job(sample_func_data, sample_job_config)
        assert result == "job_k8s"
        executor._submit_k8s_job.assert_called_once()

        # Test SSH routing
        base_config.cluster_type = "ssh"
        result = executor.submit_job(sample_func_data, sample_job_config)
        assert result == "job_ssh"
        executor._submit_ssh_job.assert_called_once()

    def test_submit_job_unsupported_type(
        self, base_config, sample_func_data, sample_job_config
    ):
        """Test error handling for unsupported cluster types."""
        executor = ClusterExecutor(base_config)
        executor.connect = Mock()
        base_config.cluster_type = "unsupported"

        with pytest.raises(ValueError, match="Unsupported cluster type"):
            executor.submit_job(sample_func_data, sample_job_config)

    @patch("time.time", return_value=1234567890)
    @patch("tempfile.NamedTemporaryFile")
    @patch("os.unlink")
    def test_submit_slurm_job_complete(
        self,
        mock_unlink,
        mock_tempfile,
        mock_time,
        base_config,
        sample_func_data,
        sample_job_config,
        mock_ssh_setup,
    ):
        """Test complete SLURM job submission workflow."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_pickle"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock remote command execution
        executor._execute_remote_command = Mock(
            return_value=("Submitted batch job 123456", "")
        )
        executor._upload_file = Mock()
        executor._create_remote_file = Mock()

        # Mock setup_remote_environment and create_job_script
        with patch("clustrix.executor.setup_remote_environment") as mock_setup, patch(
            "clustrix.executor.create_job_script", return_value="#!/bin/bash\necho test"
        ) as mock_script:
            result = executor._submit_slurm_job(sample_func_data, sample_job_config)

        # Verify job ID extraction
        assert result == "123456"

        # Verify remote directory creation
        executor._execute_remote_command.assert_any_call(
            "mkdir -p /home/testuser/work/job_1234567890"
        )

        # Verify file upload
        executor._upload_file.assert_called_once()

        # Verify script creation and submission
        executor._create_remote_file.assert_called_once()
        executor._execute_remote_command.assert_any_call(
            "cd /home/testuser/work/job_1234567890 && sbatch job.sh"
        )

        # Verify job tracking
        assert "123456" in executor.active_jobs
        assert (
            executor.active_jobs["123456"]["remote_dir"]
            == "/home/testuser/work/job_1234567890"
        )
        assert executor.active_jobs["123456"]["status"] == "submitted"

    @patch("time.time", return_value=1234567890)
    @patch("tempfile.NamedTemporaryFile")
    @patch("os.unlink")
    def test_submit_pbs_job_complete(
        self,
        mock_unlink,
        mock_tempfile,
        mock_time,
        base_config,
        sample_func_data,
        sample_job_config,
        mock_ssh_setup,
    ):
        """Test complete PBS job submission workflow."""
        base_config.cluster_type = "pbs"
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_pickle"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock remote command execution
        executor._execute_remote_command = Mock(return_value=("789.server", ""))
        executor._upload_file = Mock()
        executor._create_remote_file = Mock()

        with patch(
            "clustrix.executor.create_job_script", return_value="#!/bin/bash\necho test"
        ):
            result = executor._submit_pbs_job(sample_func_data, sample_job_config)

        assert result == "789.server"
        executor._execute_remote_command.assert_any_call(
            "cd /home/testuser/work/job_1234567890 && qsub job.pbs"
        )

    @patch("time.time", return_value=1234567890)
    @patch("tempfile.NamedTemporaryFile")
    @patch("os.unlink")
    def test_submit_sge_job_complete(
        self,
        mock_unlink,
        mock_tempfile,
        mock_time,
        base_config,
        sample_func_data,
        sample_job_config,
        mock_ssh_setup,
    ):
        """Test complete SGE job submission workflow."""
        base_config.cluster_type = "sge"
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_pickle"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock remote command execution with SGE-style output
        executor._execute_remote_command = Mock(
            return_value=('Your job 456789 ("test") has been submitted', "")
        )
        executor._upload_file = Mock()
        executor._create_remote_file = Mock()

        with patch("clustrix.executor.setup_remote_environment"), patch(
            "clustrix.executor.create_job_script", return_value="#!/bin/bash\necho test"
        ):
            result = executor._submit_sge_job(sample_func_data, sample_job_config)

        assert result == "456789"
        executor._execute_remote_command.assert_any_call(
            "cd /home/testuser/work/job_1234567890 && qsub job.sge"
        )

    @patch("time.time", return_value=1234567890)
    @patch("base64.b64encode")
    @patch("clustrix.executor.cloudpickle.dumps")
    def test_submit_k8s_job_complete(
        self,
        mock_cloudpickle,
        mock_b64encode,
        mock_time,
        k8s_config,
        sample_func_data,
        sample_job_config,
    ):
        """Test complete Kubernetes job submission workflow."""
        executor = ClusterExecutor(k8s_config)

        # Mock Kubernetes setup
        mock_k8s_client = Mock()
        executor.k8s_client = mock_k8s_client

        # Mock serialization
        mock_cloudpickle.return_value = b"serialized_data"
        mock_b64encode.return_value = b"encoded_data"

        # Mock Kubernetes API inside the _submit_k8s_job method
        with patch("kubernetes.client") as mock_client:
            mock_batch_api = Mock()
            mock_client.BatchV1Api.return_value = mock_batch_api

            mock_job_response = Mock()
            mock_job_response.metadata.name = "clustrix-job-1234567890"
            mock_batch_api.create_namespaced_job.return_value = mock_job_response

            result = executor._submit_k8s_job(sample_func_data, sample_job_config)

        assert result == "clustrix-job-1234567890"
        mock_batch_api.create_namespaced_job.assert_called_once()

    @patch("time.time", return_value=1234567890)
    @patch("tempfile.NamedTemporaryFile")
    @patch("os.unlink")
    def test_submit_ssh_job_complete(
        self,
        mock_unlink,
        mock_tempfile,
        mock_time,
        ssh_config,
        sample_func_data,
        sample_job_config,
        mock_ssh_setup,
    ):
        """Test complete SSH job submission workflow."""
        executor = ClusterExecutor(ssh_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_pickle"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock remote command execution
        executor._execute_remote_command = Mock(return_value=("", ""))
        executor._upload_file = Mock()
        executor._create_remote_file = Mock()

        with patch("clustrix.executor.setup_remote_environment"), patch(
            "clustrix.executor.create_job_script", return_value="#!/bin/bash\necho test"
        ):
            result = executor._submit_ssh_job(sample_func_data, sample_job_config)

        # SSH jobs use timestamp as job ID
        assert result == "ssh_1234567890"
        executor._execute_remote_command.assert_any_call(
            "cd /home/testuser/work/job_1234567890 && nohup bash job.sh > job.out 2> job.err < /dev/null &"
        )

    def test_get_job_status_slurm(self, base_config, mock_ssh_setup):
        """Test SLURM job status checking."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]

        # Test completed job
        executor._execute_remote_command = Mock(return_value=("COMPLETED", ""))
        status = executor._check_job_status("123456")
        assert status == "completed"

        # Test running job
        executor._execute_remote_command = Mock(return_value=("RUNNING", ""))
        status = executor._check_job_status("123456")
        assert status == "running"

        # Test failed job
        executor._execute_remote_command = Mock(return_value=("FAILED", ""))
        status = executor._check_job_status("123456")
        assert status == "failed"

        # Test pending job
        executor._execute_remote_command = Mock(return_value=("PENDING", ""))
        status = executor._check_job_status("123456")
        assert status == "running"

    def test_get_job_status_pbs(self, base_config, mock_ssh_setup):
        """Test PBS job status checking."""
        base_config.cluster_type = "pbs"
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]

        # Test completed job
        executor._execute_remote_command = Mock(return_value=("job_state = C", ""))
        status = executor._check_job_status("789.server")
        assert status == "completed"

        # Test running job
        executor._execute_remote_command = Mock(return_value=("job_state = R", ""))
        status = executor._check_job_status("789.server")
        assert status == "running"

        # Test queued job
        executor._execute_remote_command = Mock(return_value=("job_state = Q", ""))
        status = executor._check_job_status("789.server")
        assert status == "failed"

    def test_get_job_status_sge(self, base_config, mock_ssh_setup):
        """Test SGE job status checking."""
        base_config.cluster_type = "sge"
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]

        # Mock the _check_sge_status method that is actually called
        executor._check_sge_status = Mock()

        # Test various SGE states
        test_cases = [
            ("running", "running"),
            ("pending", "pending"),
            ("completed", "completed"),
        ]

        for sge_state, expected_status in test_cases:
            executor._check_sge_status.return_value = sge_state
            status = executor._check_job_status("456789")
            assert status == expected_status

    def test_get_job_status_kubernetes(self, k8s_config):
        """Test Kubernetes job status checking."""
        executor = ClusterExecutor(k8s_config)

        with patch("kubernetes.client") as mock_client:
            mock_batch_api = Mock()
            mock_client.BatchV1Api.return_value = mock_batch_api

            # Test completed job
            mock_job = Mock()
            mock_job.status.succeeded = 1
            mock_job.status.failed = None
            mock_job.status.active = None
            mock_batch_api.read_namespaced_job.return_value = mock_job

            status = executor._check_job_status("test-job")
            assert status == "completed"

            # Test failed job
            mock_job.status.succeeded = None
            mock_job.status.failed = 1
            mock_job.status.active = None
            status = executor._check_job_status("test-job")
            assert status == "failed"

            # Test running job
            mock_job.status.succeeded = None
            mock_job.status.failed = None
            mock_job.status.active = 1
            status = executor._check_job_status("test-job")
            assert status == "running"

    def test_get_job_status_ssh(self, ssh_config, mock_ssh_setup):
        """Test SSH job status checking."""
        executor = ClusterExecutor(ssh_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "ssh_job_123": {"remote_dir": "/home/testuser/work/job_123"}
        }

        # Mock file checking
        executor._remote_file_exists = Mock()

        # Test completed job
        executor._remote_file_exists.side_effect = lambda path: "result.pkl" in path
        status = executor._check_job_status("ssh_job_123")
        assert status == "completed"

        # Test failed job - with error file content
        executor._remote_file_exists.side_effect = lambda path: "job.err" in path
        executor._execute_remote_command = Mock(return_value=("5 /path/to/job.err", ""))
        status = executor._check_job_status("ssh_job_123")
        assert status == "failed"

        # Test running job
        executor._remote_file_exists.side_effect = lambda path: False
        status = executor._check_job_status("ssh_job_123")
        assert status == "running"

    def test_get_result_success(self, base_config, mock_ssh_setup):
        """Test successful result retrieval."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "123456": {"remote_dir": "/home/testuser/work/job_123456"}
        }

        # Mock successful result download
        result_data = {"result": 42}

        with patch("tempfile.NamedTemporaryFile") as mock_tempfile, patch(
            "pickle.load", return_value=result_data
        ) as mock_pickle_load, patch("os.unlink"):
            mock_file = Mock()
            mock_file.name = "/tmp/result.pkl"
            mock_tempfile.return_value.__enter__.return_value = mock_file

            executor._download_file = Mock()

            result = executor.get_result("123456")
            assert result == 42

    def test_get_result_with_error(self, base_config, mock_ssh_setup):
        """Test result retrieval when job failed."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "123456": {"remote_dir": "/home/testuser/work/job_123456"}
        }

        # Mock error result download
        error_data = {"error": "ValueError: Test error"}

        with patch("tempfile.NamedTemporaryFile") as mock_tempfile, patch(
            "pickle.load", return_value=error_data
        ), patch("os.unlink"):
            mock_file = Mock()
            mock_file.name = "/tmp/error.pkl"
            mock_tempfile.return_value.__enter__.return_value = mock_file

            executor._download_file = Mock()
            executor._remote_file_exists = Mock(
                side_effect=lambda path: "error.pkl" in path
            )

            with pytest.raises(Exception, match="ValueError: Test error"):
                executor.get_result("123456")

    def test_cancel_job_slurm(self, base_config, mock_ssh_setup):
        """Test SLURM job cancellation."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor._execute_remote_command = Mock(return_value=("", ""))

        result = executor.cancel_job("123456")
        assert result is True
        executor._execute_remote_command.assert_called_with("scancel 123456")

    def test_cancel_job_pbs(self, base_config, mock_ssh_setup):
        """Test PBS job cancellation."""
        base_config.cluster_type = "pbs"
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor._execute_remote_command = Mock(return_value=("", ""))

        result = executor.cancel_job("789.server")
        assert result is True
        executor._execute_remote_command.assert_called_with("qdel 789.server")

    def test_cancel_job_sge(self, base_config, mock_ssh_setup):
        """Test SGE job cancellation."""
        base_config.cluster_type = "sge"
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor._execute_remote_command = Mock(return_value=("", ""))

        result = executor.cancel_job("456789")
        assert result is True
        executor._execute_remote_command.assert_called_with("qdel 456789")

    def test_cancel_job_kubernetes(self, k8s_config):
        """Test Kubernetes job cancellation."""
        executor = ClusterExecutor(k8s_config)

        with patch("clustrix.executor.client") as mock_client:
            mock_batch_api = Mock()
            mock_client.BatchV1Api.return_value = mock_batch_api

            result = executor.cancel_job("test-job")
            assert result is True
            mock_batch_api.delete_namespaced_job.assert_called_once()

    def test_remote_file_operations(self, base_config, mock_ssh_setup):
        """Test remote file operations."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        # Test file existence check
        mock_stat = Mock()
        executor.sftp_client.stat.return_value = mock_stat
        assert executor._remote_file_exists("/remote/path/file.txt") is True

        # Test file not exists
        executor.sftp_client.stat.side_effect = FileNotFoundError()
        assert executor._remote_file_exists("/remote/path/nonexistent.txt") is False

        # Test file upload
        executor._upload_file("/local/file.txt", "/remote/file.txt")
        executor.sftp_client.put.assert_called_with(
            "/local/file.txt", "/remote/file.txt"
        )

        # Test file download
        executor._download_file("/remote/file.txt", "/local/file.txt")
        executor.sftp_client.get.assert_called_with(
            "/remote/file.txt", "/local/file.txt"
        )

        # Test remote file creation
        mock_file = Mock()
        executor.sftp_client.open.return_value.__enter__.return_value = mock_file
        executor._create_remote_file("/remote/script.sh", "#!/bin/bash\necho test")
        mock_file.write.assert_called_with("#!/bin/bash\necho test")

    def test_execute_remote_command(self, base_config, mock_ssh_setup):
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

    def test_connect_method(self, base_config, mock_ssh_setup):
        """Test the connect method for different cluster types."""
        # Test SSH-based connection
        executor = ClusterExecutor(base_config)
        executor.connect()

        assert executor.ssh_client is not None
        assert executor.sftp_client is not None

        # Test Kubernetes connection
        k8s_config = ClusterConfig(cluster_type="kubernetes")
        executor = ClusterExecutor(k8s_config)

        with patch("clustrix.executor.config") as mock_k8s_config, patch(
            "clustrix.executor.client"
        ) as mock_k8s_client:
            executor.connect()
            mock_k8s_config.load_kube_config.assert_called_once()

    def test_disconnect_method(self, base_config, mock_ssh_setup):
        """Test disconnection cleanup."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.sftp_client = mock_ssh_setup["sftp_client"]

        executor.disconnect()

        executor.ssh_client.close.assert_called_once()
        executor.sftp_client.close.assert_called_once()
        assert executor.ssh_client is None
        assert executor.sftp_client is None

    def test_error_log_retrieval(self, base_config, mock_ssh_setup):
        """Test error log retrieval for failed jobs."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "123456": {"remote_dir": "/home/testuser/work/job_123456"}
        }

        # Mock log file reading
        mock_file = Mock()
        mock_file.read.return_value = "Error: Test failure\nStacktrace: ..."
        executor.sftp_client.open.return_value.__enter__.return_value = mock_file

        error_log = executor.get_error_log("123456")
        assert "Error: Test failure" in error_log

    def test_job_config_validation(self, base_config, sample_func_data):
        """Test job configuration validation and defaults."""
        executor = ClusterExecutor(base_config)
        executor.connect = Mock()
        executor._submit_slurm_job = Mock(return_value="job_123")

        # Test with minimal config
        minimal_config = {}
        executor.submit_job(sample_func_data, minimal_config)

        # Test with full config
        full_config = {
            "cores": 8,
            "memory": "16GB",
            "time": "04:00:00",
            "partition": "gpu",
            "nodes": 2,
        }
        executor.submit_job(sample_func_data, full_config)

        assert executor._submit_slurm_job.call_count == 2

    def test_cleanup_after_job_completion(self, base_config, mock_ssh_setup):
        """Test cleanup of remote files after job completion."""
        base_config.cleanup_remote_files = True
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]
        executor.active_jobs = {
            "123456": {"remote_dir": "/home/testuser/work/job_123456"}
        }

        # Mock cleanup operations
        executor._execute_remote_command = Mock(return_value=("", ""))

        executor.cleanup_job("123456")

        # Verify cleanup command was executed
        executor._execute_remote_command.assert_called_with(
            "rm -rf /home/testuser/work/job_123456"
        )
        assert "123456" not in executor.active_jobs

    def test_prepare_function_data_edge_cases(self, base_config):
        """Test function data preparation with edge cases."""
        executor = ClusterExecutor(base_config)

        # Test with complex function
        def complex_func(data_dict, *args, **kwargs):
            """Function with docstring and complex signature."""
            result = sum(data_dict.values())
            return result + sum(args) + sum(kwargs.values())

        test_data = {"a": 1, "b": 2}
        func_data = executor.prepare_function_data(
            complex_func, (test_data, 5), {"extra": 10}
        )

        assert "func" in func_data
        assert "args" in func_data
        assert "kwargs" in func_data
        assert "requirements" in func_data

        # Test function serialization
        assert func_data["args"] == (test_data, 5)
        assert func_data["kwargs"] == {"extra": 10}

    def test_job_status_unknown_job(self, base_config, mock_ssh_setup):
        """Test job status checking for unknown job IDs."""
        executor = ClusterExecutor(base_config)
        executor.ssh_client = mock_ssh_setup["ssh_client"]

        # Mock command that raises exception (job not found)
        executor._execute_remote_command = Mock(side_effect=Exception("Job not found"))

        status = executor._check_job_status("nonexistent_job")
        assert status == "unknown"
