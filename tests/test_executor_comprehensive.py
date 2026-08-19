"""
Comprehensive executor tests focusing only on real functionality.

Tests the actual methods that exist in ClusterExecutor without assumptions.
"""

import pytest
from unittest.mock import Mock, patch
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig
from clustrix.utils import serialize_function


def global_test_function(x):
    """Global test function that can be pickled."""
    return x * 2


def failing_test_function():
    """A real failure, so the failure paths have something real to carry."""
    raise ValueError("Test error")


def local_config():
    """`local` is a real backend: it runs the function on this machine."""
    return ClusterConfig(cluster_type="local")


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

    # ------------------------------------------------------------------
    # Everything from here to `test_execute_function_wrapper` used to mock the
    # executor's own backward-compatibility aliases -- `_submit_slurm_job`,
    # `_execute_remote_command`, `_check_job_status`, `_download_file` -- and
    # assert those Mocks were called. The refactor routes through
    # `scheduler_manager` / `connection_manager` / `local_manager` instead, so
    # the Mocks were never reached: each test asserted something about an
    # object clustrix had stopped consulting.
    #
    # Rewritten against real code. `cluster_type="local"` really executes, and
    # where a real cluster would be needed the assertion is on the error path,
    # which is real and observable here.
    # ------------------------------------------------------------------

    def test_job_submission_routing(self, base_config, sample_func_data):
        """submit_job records which manager owns the job, and routes by it."""
        local = ClusterExecutor(local_config())
        job_id = local.submit_job(
            serialize_function(global_test_function, (5,), {}), {"cores": 1}
        )
        assert local.active_jobs[job_id] == {"manager": "local", "job_id": job_id}
        assert job_id in local.local_manager.active_jobs

        # A scheduler job takes the SSH path instead -- proving it is not
        # being run locally -- and records nothing when that path fails.
        # cluster_host is cleared so the failure is a configuration error
        # rather than a DNS lookup for a host that does not exist.
        base_config.cluster_host = None
        scheduler = ClusterExecutor(base_config)
        with pytest.raises(ValueError, match="cluster_host must be specified"):
            scheduler.submit_job(sample_func_data, {"cores": 1})
        assert scheduler.active_jobs == {}

        # An unroutable cluster type is refused rather than guessed at.
        base_config.cluster_type = "not-a-cluster"
        with pytest.raises(ValueError, match="Unsupported cluster type"):
            ClusterExecutor(base_config).submit_job(sample_func_data, {"cores": 1})

    def test_result_retrieval_success(self):
        """A real function runs and its real return value comes back."""
        executor = ClusterExecutor(local_config())
        job_id = executor.submit_job(
            serialize_function(global_test_function, (21,), {}), {"cores": 1}
        )

        assert executor.wait_for_result(job_id) == 42
        # Delivered once: the executor stops tracking a collected job.
        assert job_id not in executor.active_jobs

    def test_result_retrieval_error(self):
        """A job that raised re-raises the original exception, not a wrapper."""
        executor = ClusterExecutor(local_config())
        job_id = executor.submit_job(
            serialize_function(failing_test_function, (), {}), {"cores": 1}
        )

        with pytest.raises(ValueError, match="Test error"):
            executor.wait_for_result(job_id)

    def test_job_cancellation_slurm(self, base_config):
        """An untracked ID still reaches the scheduler; it is not ignored.

        `cancel_job` falls back to prefix-based routing for IDs it has no
        record of. Silently returning for those would mean a job clustrix
        lost track of could never be cancelled at all.
        """
        executor = ClusterExecutor(base_config)

        assert "123456" not in executor.active_jobs
        with pytest.raises(RuntimeError, match="SSH client not connected"):
            executor.cancel_job("123456")

    def test_connection_management(self, base_config):
        """Real connect/disconnect behaviour, with nothing mocked."""
        # A local executor has nothing to connect to, and connect() must not
        # invent an SSH session for it.
        local = ClusterExecutor(local_config())
        local.connect()
        assert local.ssh_client is None
        assert local.sftp_client is None

        # disconnect() with nothing open is a no-op, and repeating it is safe.
        local.disconnect()
        local.disconnect()
        assert local.ssh_client is None

        # A scheduler config with no host cannot connect, and says which
        # setting is missing rather than failing later inside paramiko.
        base_config.cluster_host = None
        scheduler = ClusterExecutor(base_config)
        with pytest.raises(ValueError, match="cluster_host must be specified"):
            scheduler.connect()
        assert scheduler.ssh_client is None

    def test_job_status_checking(self):
        """Real statuses for real jobs, through the compatibility alias.

        `_check_job_status` is documented as an alias for `get_job_status`;
        this checks it still delegates rather than having drifted.
        """
        executor = ClusterExecutor(local_config())

        succeeded = executor.submit_job(
            serialize_function(global_test_function, (5,), {}), {"cores": 1}
        )
        failed = executor.submit_job(
            serialize_function(failing_test_function, (), {}), {"cores": 1}
        )

        assert executor.get_job_status(succeeded) == "completed"
        assert executor._check_job_status(succeeded) == "completed"
        assert executor.get_job_status(failed) == "failed"
        assert executor._check_job_status(failed) == "failed"

    def test_job_status_public_method(self, base_config):
        """A status that cannot be determined is reported as unknown.

        With no connection there is no way to ask SLURM anything, and
        `check_job_status` deliberately answers "unknown" instead of raising
        -- `wait_for_result` polls this, and a poll that raised on a dropped
        connection would abandon a job that is still running.
        """
        executor = ClusterExecutor(base_config)

        assert executor.get_job_status("job_123") == "unknown"

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
