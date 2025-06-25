import pytest
import tempfile
import pickle
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from clustrix import cluster, configure, get_config
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig


class TestIntegration:
    """Integration tests for end-to-end functionality."""

    @pytest.fixture
    def mock_ssh_setup(self):
        """Setup mock SSH environment for integration tests."""
        with patch("paramiko.SSHClient") as mock_ssh_class:
            mock_ssh = Mock()
            mock_ssh_class.return_value = mock_ssh

            # Mock SFTP
            mock_sftp = MagicMock()
            mock_ssh.open_sftp.return_value = mock_sftp

            # Mock SFTP file operations for context manager support
            mock_file = MagicMock()
            mock_sftp.open.return_value.__enter__.return_value = mock_file
            mock_sftp.open.return_value.__exit__.return_value = None

            # Mock successful command execution
            mock_stdout = Mock()
            mock_stdout.read.return_value = b"Success"
            mock_stdout.channel.recv_exit_status.return_value = 0

            mock_stderr = Mock()
            mock_stderr.read.return_value = b""

            mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)

            yield mock_ssh, mock_sftp

    def test_end_to_end_simple_function(self, mock_ssh_setup, temp_dir):
        """Test complete execution of a simple function."""
        mock_ssh, mock_sftp = mock_ssh_setup

        # Configure for remote execution
        configure(
            cluster_type="slurm",
            cluster_host="test.cluster.com",
            username="testuser",
            remote_work_dir=temp_dir,
        )

        # Define and decorate function
        @cluster(cores=4, memory="8GB")
        def add_numbers(x, y):
            return x + y

        # Mock job submission
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"12345"
        mock_ssh.exec_command.return_value = (None, mock_stdout, Mock())

        # Mock job status checking
        def exec_side_effect(cmd):
            if "squeue" in cmd:
                status_mock = Mock()
                status_mock.read.return_value = b"COMPLETED"
                status_mock.channel.recv_exit_status.return_value = 0
                return (None, status_mock, Mock())
            else:
                # For other commands (environment setup, etc.), return successful execution
                cmd_stdout = Mock()
                cmd_stdout.read.return_value = b"Success"
                cmd_stdout.channel.recv_exit_status.return_value = 0

                cmd_stderr = Mock()
                cmd_stderr.read.return_value = b""

                return (None, cmd_stdout, cmd_stderr)

        mock_ssh.exec_command.side_effect = exec_side_effect

        # Mock result retrieval
        result_data = 42
        result_file = Path(temp_dir) / "result.pkl"
        with open(result_file, "wb") as f:
            pickle.dump(result_data, f)

        def get_side_effect(remote_path, local_path):
            # Copy our test result file to the requested location
            import shutil

            shutil.copy(result_file, local_path)

        mock_sftp.get.side_effect = get_side_effect
        mock_sftp.stat.return_value = Mock()  # File exists

        # Execute function
        result = add_numbers(10, 32)

        assert result == 42

    def test_parallel_loop_execution(self, mock_ssh_setup, temp_dir):
        """Test parallel execution of loops."""
        mock_ssh, mock_sftp = mock_ssh_setup

        configure(
            cluster_type="slurm",
            cluster_host="test.cluster.com",
            username="testuser",
            auto_parallel=True,
            max_parallel_jobs=10,
        )

        @cluster(cores=2, parallel=True)
        def process_items(items):
            results = []
            for item in items:
                results.append(item * 2)
            return results

        # This would normally trigger parallel execution
        # For testing, we'll verify the function is properly decorated
        assert hasattr(process_items, "_cluster_config")
        assert process_items._cluster_config["parallel"] is True

    def test_error_handling_integration(self, mock_ssh_setup, temp_dir):
        """Test error handling in remote execution."""
        mock_ssh, mock_sftp = mock_ssh_setup

        configure(
            cluster_type="pbs", cluster_host="test.cluster.com", username="testuser"
        )

        @cluster(cores=4)
        def failing_function():
            raise ValueError("This function always fails")

        # Mock PBS-specific command responses
        def exec_side_effect(cmd):
            if "qsub" in cmd:
                # Job submission returns job ID
                submit_mock = Mock()
                submit_mock.read.return_value = b"67890"
                submit_mock.channel.recv_exit_status.return_value = 0
                return (None, submit_mock, Mock())
            elif "qstat" in cmd:
                # Job status check - job doesn't exist in queue (completed/failed)
                status_mock = Mock()
                status_mock.read.return_value = (
                    b""  # Empty response means job not in queue
                )
                status_mock.channel.recv_exit_status.return_value = (
                    1  # qstat returns error
                )
                return (None, status_mock, Mock())
            else:
                # For other commands (environment setup, etc.)
                cmd_stdout = Mock()
                cmd_stdout.read.return_value = b"Success"
                cmd_stdout.channel.recv_exit_status.return_value = 0

                cmd_stderr = Mock()
                cmd_stderr.read.return_value = b""

                return (None, cmd_stdout, cmd_stderr)

        mock_ssh.exec_command.side_effect = exec_side_effect

        # Mock error file existence
        def stat_side_effect(path):
            if "error.pkl" in path:
                return Mock()  # Error file exists
            elif "result.pkl" in path:
                raise IOError()  # Result file doesn't exist
            raise IOError()  # Other files don't exist

        mock_sftp.stat.side_effect = stat_side_effect

        # Mock error retrieval
        error_data = ValueError("This function always fails")
        error_file = Path(temp_dir) / "error.pkl"
        with open(error_file, "wb") as f:
            pickle.dump(error_data, f)

        def get_side_effect(remote_path, local_path):
            if "error.pkl" in remote_path:
                import shutil

                shutil.copy(error_file, local_path)

        mock_sftp.get.side_effect = get_side_effect

        # Execute function and expect error
        with pytest.raises(ValueError, match="This function always fails"):
            failing_function()

    def test_configuration_persistence(self, temp_dir):
        """Test configuration loading and persistence."""
        config_file = Path(temp_dir) / "clustrix.yml"

        # Create configuration
        configure(
            cluster_type="sge",
            cluster_host="sge.cluster.com",
            username="sgeuser",
            default_cores=16,
            default_memory="32GB",
            module_loads=["python/3.9", "gcc/11.2"],
            environment_variables={"OMP_NUM_THREADS": "16"},
        )

        # Save configuration
        from clustrix.config import save_config

        save_config(str(config_file))

        # Reset configuration
        configure(cluster_type="ssh")  # Change to verify load works

        # Load configuration
        from clustrix.config import load_config

        load_config(str(config_file))

        # Verify loaded configuration
        config = get_config()
        assert config.cluster_type == "sge"
        assert config.cluster_host == "sge.cluster.com"
        assert config.username == "sgeuser"
        assert config.default_cores == 16
        assert config.default_memory == "32GB"
        assert config.module_loads == ["python/3.9", "gcc/11.2"]
        assert config.environment_variables == {"OMP_NUM_THREADS": "16"}

    def test_local_execution_fallback(self):
        """Test that functions execute locally when no cluster is configured."""
        # Ensure no cluster is configured
        configure(cluster_host=None)

        @cluster(cores=8)
        def compute_locally(x, y):
            return x**y

        result = compute_locally(2, 10)
        assert result == 1024

    @patch("clustrix.utils.get_environment_info")
    def test_environment_replication(self, mock_env_info, mock_ssh_setup, temp_dir):
        """Test environment replication on remote cluster."""
        mock_ssh, mock_sftp = mock_ssh_setup
        mock_env_info.return_value = "numpy==1.21.0\npandas==1.3.0\n"

        configure(
            cluster_type="slurm", cluster_host="test.cluster.com", username="testuser"
        )

        @cluster(cores=4)
        def data_processing():
            import numpy as np

            return np.array([1, 2, 3]).sum()

        # Mock SLURM-specific command responses
        def exec_side_effect(cmd):
            if "sbatch" in cmd:
                # Job submission returns job ID
                submit_mock = Mock()
                submit_mock.read.return_value = b"12345"
                submit_mock.channel.recv_exit_status.return_value = 0
                return (None, submit_mock, Mock())
            elif "squeue" in cmd:
                # Job status check - job completed
                status_mock = Mock()
                status_mock.read.return_value = b"COMPLETED"
                status_mock.channel.recv_exit_status.return_value = 0
                return (None, status_mock, Mock())
            else:
                # For other commands (environment setup, etc.)
                cmd_stdout = Mock()
                cmd_stdout.read.return_value = b"Success"
                cmd_stdout.channel.recv_exit_status.return_value = 0

                cmd_stderr = Mock()
                cmd_stderr.read.return_value = b""

                return (None, cmd_stdout, cmd_stderr)

        mock_ssh.exec_command.side_effect = exec_side_effect

        # Mock result file existence and retrieval
        def stat_side_effect(path):
            if "result.pkl" in path:
                return Mock()  # Result file exists
            raise IOError()  # Other files don't exist

        mock_sftp.stat.side_effect = stat_side_effect

        # Mock result retrieval
        result_data = 6  # sum([1, 2, 3])
        result_file = Path(temp_dir) / "result.pkl"
        with open(result_file, "wb") as f:
            pickle.dump(result_data, f)

        def get_side_effect(remote_path, local_path):
            if "result.pkl" in remote_path:
                import shutil

                shutil.copy(result_file, local_path)

        mock_sftp.get.side_effect = get_side_effect

        # Call the function to trigger environment replication
        result = data_processing()

        # Verify the result and environment info capture
        assert result == 6
        mock_env_info.assert_called()

    def test_resource_specification_inheritance(self):
        """Test that decorator resources override defaults."""
        configure(
            cluster_type="pbs",
            default_cores=4,
            default_memory="8GB",
            default_time="01:00:00",
        )

        @cluster(cores=16, memory="64GB", time="12:00:00")
        def heavy_computation():
            return "done"

        # Verify the decorated function has the specified resources
        assert heavy_computation._cluster_config["cores"] == 16
        assert heavy_computation._cluster_config["memory"] == "64GB"
        assert heavy_computation._cluster_config["time"] == "12:00:00"
