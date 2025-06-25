import pytest
import pickle
import json
from unittest.mock import Mock, patch, MagicMock, call
from pathlib import Path
import paramiko
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig
import clustrix.executor


class TestClusterExecutor:
    """Test ClusterExecutor class."""

    @pytest.fixture
    def executor(self, mock_config):
        """Create a ClusterExecutor instance with mock config."""
        return ClusterExecutor(mock_config)

    def test_initialization(self, executor, mock_config):
        """Test executor initialization."""
        assert executor.config == mock_config
        assert executor.ssh_client is None
        assert executor.sftp_client is None

    @patch("paramiko.SSHClient")
    def test_connect(self, mock_ssh_class, executor):
        """Test SSH connection establishment."""
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        mock_sftp = Mock()
        mock_ssh.open_sftp.return_value = mock_sftp

        executor.connect()

        mock_ssh.set_missing_host_key_policy.assert_called_once()
        mock_ssh.connect.assert_called_once_with(
            hostname="test.cluster.com",
            port=22,
            username="testuser",
            key_filename="~/.ssh/test_key",
        )
        assert executor.ssh_client == mock_ssh
        assert executor.sftp_client == mock_sftp

    @patch("paramiko.SSHClient")
    def test_connect_with_password(self, mock_ssh_class):
        """Test SSH connection with password."""
        config = ClusterConfig(
            cluster_host="test.cluster.com", username="testuser", password="testpass"
        )
        executor = ClusterExecutor(config)

        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh

        executor.connect()

        mock_ssh.connect.assert_called_once_with(
            hostname="test.cluster.com",
            port=22,
            username="testuser",
            password="testpass",
        )

    def test_disconnect(self, executor):
        """Test SSH disconnection."""
        mock_ssh = Mock()
        mock_sftp = Mock()
        executor.ssh_client = mock_ssh
        executor.sftp_client = mock_sftp

        executor.disconnect()

        mock_sftp.close.assert_called_once()
        mock_ssh.close.assert_called_once()
        assert executor.ssh_client is None
        assert executor.sftp_client is None

    @patch("paramiko.SSHClient")
    def test_execute_command(self, mock_ssh_class, executor):
        """Test command execution."""
        mock_ssh = Mock()
        mock_ssh_class.return_value = mock_ssh
        executor.ssh_client = mock_ssh

        # Setup mock response
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"command output"
        mock_stdout.channel.recv_exit_status.return_value = 0

        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)

        stdout, stderr = executor._execute_command("echo test")

        assert stdout == "command output"
        assert stderr == ""
        mock_ssh.exec_command.assert_called_once_with("echo test")

    def test_execute_command_not_connected(self, executor):
        """Test command execution without connection."""
        with pytest.raises(RuntimeError, match="Not connected"):
            executor._execute_command("echo test")

    @patch("cloudpickle.dumps")
    def test_prepare_function_data(self, mock_pickle, executor):
        """Test function data preparation."""

        def test_func(x):
            return x * 2

        mock_pickle.return_value = b"pickled_data"

        result = executor._prepare_function_data(test_func, (5,), {}, {"cores": 4})

        assert result == b"pickled_data"
        mock_pickle.assert_called_once()

        # Check the structure of pickled data
        call_args = mock_pickle.call_args[0][0]
        assert call_args["func"].__name__ == "test_func"
        assert call_args["args"] == (5,)
        assert call_args["kwargs"] == {}
        assert call_args["config"] == {"cores": 4}

    @patch("os.unlink")
    @patch("pickle.dump")
    @patch("tempfile.NamedTemporaryFile")
    @patch.object(clustrix.executor, "setup_remote_environment")
    @patch("clustrix.executor.ClusterExecutor._upload_file")
    @patch("clustrix.executor.ClusterExecutor._create_remote_file")
    def test_submit_slurm_job(
        self,
        mock_create_file,
        mock_upload,
        mock_setup_env,
        mock_tempfile,
        mock_pickle,
        mock_unlink,
        executor,
    ):
        """Test SLURM job submission (simplified)."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock command execution responses
        command_responses = {
            "mkdir -p": ("", ""),  # mkdir command
            "sbatch": ("Submitted batch job 12345", ""),  # sbatch command
        }

        def mock_execute_command(cmd):
            for key, response in command_responses.items():
                if key in cmd:
                    return response
            return ("", "")

        executor._execute_remote_command = Mock(side_effect=mock_execute_command)

        func_data = {
            "func": "dummy_func",  # Simplified - not actually pickled
            "args": (),
            "kwargs": {},
            "requirements": [],
        }
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}

        job_id = executor._submit_slurm_job(func_data, job_config)

        assert job_id == "12345"

        # Verify key methods were called
        mock_upload.assert_called()  # Function data upload
        mock_create_file.assert_called()  # Job script creation

        # Verify sbatch command was executed
        execute_calls = executor._execute_remote_command.call_args_list
        sbatch_calls = [call for call in execute_calls if "sbatch" in str(call)]
        assert len(sbatch_calls) > 0

    @patch("os.unlink")
    @patch("pickle.dump")
    @patch("tempfile.NamedTemporaryFile")
    @patch.object(clustrix.executor, "setup_remote_environment")
    @patch("clustrix.executor.ClusterExecutor._upload_file")
    @patch("clustrix.executor.ClusterExecutor._create_remote_file")
    def test_submit_pbs_job(
        self,
        mock_create_file,
        mock_upload,
        mock_setup_env,
        mock_tempfile,
        mock_pickle,
        mock_unlink,
        executor,
    ):
        """Test PBS job submission."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock command execution responses
        command_responses = {"mkdir -p": ("", ""), "qsub": ("67890.pbs", "")}

        def mock_execute_command(cmd):
            for key, response in command_responses.items():
                if key in cmd:
                    return response
            return ("", "")

        executor._execute_remote_command = Mock(side_effect=mock_execute_command)

        func_data = {"func": "dummy_func", "args": (), "kwargs": {}, "requirements": []}
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}
        job_id = executor._submit_pbs_job(func_data, job_config)

        assert job_id == "67890.pbs"

        # Verify key methods were called
        mock_upload.assert_called()
        mock_create_file.assert_called()

        # Verify qsub command was executed
        execute_calls = executor._execute_remote_command.call_args_list
        qsub_calls = [call for call in execute_calls if "qsub" in str(call)]
        assert len(qsub_calls) > 0

    @patch("os.unlink")
    @patch("pickle.dump")
    @patch("tempfile.NamedTemporaryFile")
    @patch.object(clustrix.executor, "setup_remote_environment")
    @patch("clustrix.executor.ClusterExecutor._upload_file")
    @patch("clustrix.executor.ClusterExecutor._create_remote_file")
    def test_submit_sge_job(
        self,
        mock_create_file,
        mock_upload,
        mock_setup_env,
        mock_tempfile,
        mock_pickle,
        mock_unlink,
        executor,
    ):
        """Test SGE job submission."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()

        # Mock tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test_file"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        # Mock command execution responses
        command_responses = {
            "mkdir -p": ("", ""),
            "qsub": ("Your job 98765 has been submitted", ""),  # SGE format
        }

        def mock_execute_command(cmd):
            for key, response in command_responses.items():
                if key in cmd:
                    return response
            return ("", "")

        executor._execute_remote_command = Mock(side_effect=mock_execute_command)

        func_data = {"func": "dummy_func", "args": (), "kwargs": {}, "requirements": []}
        job_config = {"cores": 4, "memory": "8GB", "time": "01:00:00"}

        job_id = executor._submit_sge_job(func_data, job_config)

        assert job_id == "98765"

        # Verify key methods were called
        mock_upload.assert_called()
        mock_create_file.assert_called()

        # Verify qsub command was executed
        execute_calls = executor._execute_remote_command.call_args_list
        qsub_calls = [call for call in execute_calls if "qsub" in str(call)]
        assert len(qsub_calls) > 0

    @patch("kubernetes.client")
    @patch("clustrix.executor.cloudpickle")
    def test_submit_k8s_job(self, mock_cloudpickle, mock_client, executor):
        """Test Kubernetes job submission."""
        # Mock cloudpickle serialization
        mock_cloudpickle.dumps.return_value = b"serialized_data"

        # Mock Kubernetes API response
        mock_response = Mock()
        mock_response.metadata.name = "clustrix-job-12345"

        mock_batch_api = Mock()
        mock_batch_api.create_namespaced_job.return_value = mock_response
        mock_client.BatchV1Api.return_value = mock_batch_api

        # Mock k8s_client setup
        executor.k8s_client = Mock()

        func_data = {"func": "dummy_func", "args": (), "kwargs": {}, "requirements": []}
        job_config = {"cores": 4, "memory": "8Gi"}

        job_id = executor._submit_k8s_job(func_data, job_config)

        assert job_id == "clustrix-job-12345"

        # Verify Kubernetes API was called
        mock_batch_api.create_namespaced_job.assert_called_once()
        call_args = mock_batch_api.create_namespaced_job.call_args
        assert call_args[1]["namespace"] == "default"
        assert "body" in call_args[1]

        # Verify job manifest structure
        job_manifest = call_args[1]["body"]
        assert job_manifest["kind"] == "Job"
        assert (
            job_manifest["spec"]["template"]["spec"]["containers"][0]["name"]
            == "clustrix-worker"
        )

    def test_check_slurm_status(self, executor):
        """Test SLURM job status checking."""
        executor.ssh_client = Mock()

        # Mock squeue output
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"RUNNING"
        mock_stdout.channel.recv_exit_status.return_value = 0

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())

        status = executor._check_slurm_status("12345")

        assert status == "running"

        # Verify squeue command
        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "squeue" in call_args
        assert "12345" in call_args

    def test_check_pbs_status(self, executor):
        """Test PBS job status checking."""
        executor.ssh_client = Mock()

        # Mock qstat output
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"12345.pbs  user  R  queue"
        mock_stdout.channel.recv_exit_status.return_value = 0

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())

        status = executor._check_pbs_status("12345")

        assert status == "running"

    def test_get_job_status_completed(self, executor):
        """Test job status when result file exists."""
        executor.ssh_client = Mock()
        mock_sftp = Mock()
        executor.ssh_client.open_sftp.return_value = mock_sftp

        # Mock squeue command to return empty (job not in queue)
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""  # Empty output - job not in queue
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        # Add job to active jobs for tracking
        executor.active_jobs["job_12345"] = {"remote_dir": "/tmp/test_job"}

        # Mock file existence check - result.pkl exists
        mock_sftp.stat.return_value = Mock()  # File exists

        status = executor.get_job_status("job_12345")

        assert status == "completed"

    def test_get_job_status_failed(self, executor):
        """Test job status when error file exists."""
        executor.ssh_client = Mock()
        mock_sftp = Mock()
        executor.ssh_client.open_sftp.return_value = mock_sftp

        # Mock squeue command to return empty (job not in queue)
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""  # Empty output - job not in queue
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        # Add job to active jobs for tracking
        executor.active_jobs["job_12345"] = {"remote_dir": "/tmp/test_job"}

        # Mock file existence check - result.pkl doesn't exist, error.pkl does exist
        def stat_side_effect(path):
            if "result.pkl" in path:
                raise IOError()  # Result file doesn't exist
            else:
                return Mock()  # Other files exist

        mock_sftp.stat.side_effect = stat_side_effect

        status = executor.get_job_status("job_12345")

        assert status == "failed"

    def test_get_result_success(self, executor):
        """Test retrieving successful result."""
        executor.ssh_client = Mock()
        executor.sftp_client = Mock()

        # Mock SFTP for file download
        mock_sftp = Mock()
        executor.ssh_client.open_sftp.return_value = mock_sftp

        # Mock SSH command execution for cleanup
        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_stderr = Mock()
        mock_stderr.read.return_value = b""
        executor.ssh_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        # Add job to active jobs for tracking
        executor.active_jobs["job_12345"] = {"remote_dir": "/tmp/test_job"}

        # Mock the status check to return completed immediately
        executor._check_job_status = Mock(return_value="completed")

        # Mock result data
        test_result = {"value": 42}

        # Mock SFTP get to write test result when called
        import tempfile
        import os

        def mock_get(remote_path, local_path):
            # Create the directory if it doesn't exist (Windows compatibility)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            # Write test result to the local path when SFTP.get is called
            with open(local_path, "wb") as f:
                pickle.dump(test_result, f)

        mock_sftp.get.side_effect = mock_get

        result = executor.get_result("job_12345")

        assert result == test_result
        # Verify SFTP get was called with correct remote path (local path is a temp file)
        mock_sftp.get.assert_called_once()
        call_args = mock_sftp.get.call_args[0]
        assert call_args[0] == "/tmp/test_job/result.pkl"  # remote path
        assert call_args[1].endswith("tmp") or "/tmp" in call_args[1]  # local temp path

    def test_cancel_job_slurm(self, executor):
        """Test canceling SLURM job."""
        executor.ssh_client = Mock()
        executor.config.cluster_type = "slurm"

        mock_stdout = Mock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0

        executor.ssh_client.exec_command.return_value = (None, mock_stdout, Mock())

        executor.cancel_job("12345")

        call_args = executor.ssh_client.exec_command.call_args[0][0]
        assert "scancel 12345" in call_args

    def test_get_error_log(self, executor):
        """Test error log retrieval."""
        executor.active_jobs["failed_job"] = {"remote_dir": "/tmp/failed_job"}

        error_content = "Traceback (most recent call last):\n  File test.py, line 1\n    syntax error"

        with patch.object(executor, "_execute_remote_command") as mock_exec:
            mock_exec.return_value = (error_content, "")

            error_log = executor._get_error_log("failed_job")
            assert error_log == error_content

        # Test when no error log found
        with patch.object(executor, "_execute_remote_command") as mock_exec:
            mock_exec.return_value = ("", "")

            error_log = executor._get_error_log("failed_job")
            assert "No error log found" in error_log

        # Test unknown job
        error_log = executor._get_error_log("unknown_job")
        assert "No job info available" in error_log
