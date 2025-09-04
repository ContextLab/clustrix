"""
Test suite for SSH-based environment setup operations in clustrix.utils.
Covers remote environment creation, package installation, and two-venv system testing.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
from clustrix.utils import (
    setup_environment,
    setup_two_venv_environment,
    setup_python_compatible_environment,
    setup_remote_environment,
    get_package_manager_command,
)
from clustrix.config import ClusterConfig


class TestSSHEnvironmentSetup:
    """Test SSH-based remote environment setup operations."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock SSH client
        self.mock_ssh = MagicMock()
        self.mock_ssh.exec_command.return_value = (
            MagicMock(),  # stdin
            MagicMock(
                channel=MagicMock(recv_exit_status=MagicMock(return_value=0))
            ),  # stdout
            MagicMock(),  # stderr
        )

        # Mock stdout.read() to return empty by default
        self.mock_ssh.exec_command.return_value[1].read.return_value = b""
        self.mock_ssh.exec_command.return_value[2].read.return_value = b""

        # Create test config
        self.config = ClusterConfig(
            cluster_type="ssh",
            cluster_host="test-cluster.edu",
            username="testuser",
            python_executable="python3.9",
        )
        self.work_dir = "/scratch/test_job_123"
        self.requirements = {"numpy": "1.21.0", "dill": "0.3.4", "cloudpickle": "2.2.1"}

    def test_setup_environment_conda_existing(self):
        """Test setup_environment with existing conda environment."""
        config = ClusterConfig(conda_env_name="my_env", cluster_type="slurm")

        result = setup_environment(self.work_dir, self.requirements, config)

        assert result == "conda run -n my_env python"

    def test_setup_environment_conda_create_new(self):
        """Test setup_environment creating new conda environment."""
        with patch("clustrix.utils.get_package_manager_command", return_value="conda"):
            config = ClusterConfig(python_executable="python3.11")

            result = setup_environment(self.work_dir, self.requirements, config)

            # Should return conda run command
            assert "conda run -p" in result
            assert "python" in result

    def test_setup_environment_venv_creation(self):
        """Test setup_environment creating virtual environment."""
        with patch("clustrix.utils.get_package_manager_command", return_value="pip"):
            config = ClusterConfig(python_executable="python3.9")

            result = setup_environment(self.work_dir, self.requirements, config)

            assert result == f"{self.work_dir}/venv/bin/python"

    def test_setup_environment_venv_with_requirements(self):
        """Test setup_environment with package requirements."""
        with patch("clustrix.utils.get_package_manager_command", return_value="uv"):
            config = ClusterConfig(python_executable="python")

            result = setup_environment(self.work_dir, self.requirements, config)

            assert result == f"{self.work_dir}/venv/bin/python"


class TestTwoVenvEnvironmentSetup:
    """Test the two-venv environment setup system for cross-version compatibility."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_ssh = MagicMock()
        self.mock_config = ClusterConfig(
            cluster_type="ssh", cluster_host="cluster.edu", username="user"
        )
        self.work_dir = "/scratch/job_456"
        self.requirements = {"numpy": "1.20.0", "dill": "0.3.4"}

        # Mock successful command execution
        mock_stdout = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stdout.read.return_value = b""

        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            mock_stdout,
            mock_stderr,
        )

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_conda_available(self, mock_get_config):
        """Test two-venv setup when conda is available on remote system."""
        mock_get_config.return_value = self.mock_config

        # Mock conda availability check
        stdout_conda = MagicMock()
        stdout_conda.read.return_value = b"conda 4.10.1"
        stdout_conda.channel.recv_exit_status.return_value = 0

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0
        stdout_success.read.return_value = b""

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_conda, MagicMock()),  # conda check
            (MagicMock(), stdout_success, MagicMock()),  # setup commands
        ]

        result = setup_two_venv_environment(
            self.mock_ssh, self.work_dir, self.requirements, self.mock_config
        )

        # Should return conda-based result
        assert result["uses_conda"] is True
        assert "conda run -n" in result["venv1_python"]
        assert "conda run -n" in result["venv2_python"]
        assert result["conda_env1_name"] is not None
        assert result["conda_env2_name"] is not None

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_fallback_to_venv(self, mock_get_config):
        """Test two-venv setup falling back to virtualenv when conda unavailable."""
        mock_get_config.return_value = self.mock_config

        # Mock conda unavailability and Python version check
        stdout_no_conda = MagicMock()
        stdout_no_conda.read.return_value = b""  # No conda

        stdout_python_version = MagicMock()
        stdout_python_version.read.return_value = b"(3, 9)"

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_no_conda, MagicMock()),  # conda check
            (MagicMock(), stdout_python_version, MagicMock()),  # python3.9 check
            (MagicMock(), stdout_success, MagicMock()),  # setup commands
        ]

        result = setup_two_venv_environment(
            self.mock_ssh, self.work_dir, self.requirements, self.mock_config
        )

        # Should return venv-based result
        assert result["uses_conda"] is False
        assert result["venv1_python"].endswith("/bin/python")
        assert result["venv2_python"].endswith("/bin/python")
        assert result["conda_env1_name"] is None

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_no_compatible_python(self, mock_get_config):
        """Test two-venv setup when no compatible Python found."""
        mock_get_config.return_value = self.mock_config

        # Mock no conda and no compatible Python
        stdout_empty = MagicMock()
        stdout_empty.read.return_value = b""

        # First call: conda check (empty)
        # Subsequent calls: Python version checks (all empty)
        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_empty,
            MagicMock(),
        )

        with pytest.raises(RuntimeError, match="No compatible Python version found"):
            setup_two_venv_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.mock_config
            )

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_installation_failure(self, mock_get_config):
        """Test two-venv setup when installation commands fail."""
        mock_get_config.return_value = self.mock_config

        # Mock conda availability but installation failure
        stdout_conda = MagicMock()
        stdout_conda.read.return_value = b"conda 4.10.1"

        stdout_failure = MagicMock()
        stdout_failure.channel.recv_exit_status.return_value = 1
        stderr_failure = MagicMock()
        stderr_failure.read.return_value = b"Installation failed"

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_conda, MagicMock()),  # conda check
            (MagicMock(), stdout_failure, stderr_failure),  # setup commands fail
        ]

        with pytest.raises(RuntimeError, match="Failed to setup two-venv environment"):
            setup_two_venv_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.mock_config
            )

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_with_cluster_packages(self, mock_get_config):
        """Test two-venv setup with cluster-specific package configurations."""
        # Create config with cluster packages
        config_with_packages = ClusterConfig(
            cluster_type="ssh",
            cluster_host="cluster.edu",
            username="user",
            cluster_packages=[
                "matplotlib==3.5.0",
                {"package": "scipy", "timeout": 180},
            ],
        )
        mock_get_config.return_value = config_with_packages

        # Mock conda availability
        stdout_conda = MagicMock()
        stdout_conda.read.return_value = b"conda 4.10.1"
        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_conda, MagicMock()),
            (MagicMock(), stdout_success, MagicMock()),
        ]

        result = setup_two_venv_environment(
            self.mock_ssh, self.work_dir, self.requirements, config_with_packages
        )

        # Verify cluster packages were handled
        assert result["uses_conda"] is True

        # Check that exec_command was called with cluster package installation
        setup_commands = self.mock_ssh.exec_command.call_args_list[1][0][0]
        assert "matplotlib==3.5.0" in setup_commands
        assert "scipy" in setup_commands

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_with_post_install_commands(self, mock_get_config):
        """Test two-venv setup with post-installation commands."""
        config_with_post_install = ClusterConfig(
            cluster_type="ssh",
            cluster_host="cluster.edu",
            username="user",
            venv_post_install_commands=[
                "echo 'Setup complete'",
                "python -c 'import numpy'",
            ],
        )
        mock_get_config.return_value = config_with_post_install

        stdout_conda = MagicMock()
        stdout_conda.read.return_value = b"conda 4.10.1"
        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_conda, MagicMock()),
            (MagicMock(), stdout_success, MagicMock()),
        ]

        result = setup_two_venv_environment(
            self.mock_ssh, self.work_dir, self.requirements, config_with_post_install
        )

        assert result["uses_conda"] is True

        # Check that post-install commands were included
        setup_commands = self.mock_ssh.exec_command.call_args_list[1][0][0]
        assert "echo 'Setup complete'" in setup_commands
        assert "python -c 'import numpy'" in setup_commands


class TestPythonCompatibleEnvironment:
    """Test Python-compatible environment setup for cross-version compatibility."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_ssh = MagicMock()
        self.config = ClusterConfig(
            cluster_type="ssh", cluster_host="cluster.edu", username="user"
        )
        self.work_dir = "/scratch/job_789"
        self.requirements = {"dill": "0.3.4", "cloudpickle": "2.2.1"}

    @patch("clustrix.config.get_config")
    def test_setup_python_compatible_successful(self, mock_get_config):
        """Test successful Python-compatible environment setup."""
        mock_get_config.return_value = self.config

        # Mock version detection
        stdout_version_detect = MagicMock()
        stdout_version_detect.read.return_value = b"Python 3.9.5"

        # Mock Python version compatibility check
        stdout_version_check = MagicMock()
        stdout_version_check.read.return_value = b"(3, 9)"

        # Mock successful setup
        stdout_setup = MagicMock()
        stdout_setup.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_version_detect, MagicMock()),  # version detection
            (MagicMock(), stdout_version_check, MagicMock()),  # compatibility check
            (MagicMock(), stdout_setup, MagicMock()),  # setup commands
        ]

        result = setup_python_compatible_environment(
            self.mock_ssh, self.work_dir, self.requirements, self.config
        )

        assert result == f"{self.work_dir}/compat_venv/bin/python"

    @patch("clustrix.config.get_config")
    def test_setup_python_compatible_no_compatible_version(self, mock_get_config):
        """Test fallback when no compatible Python version found."""
        mock_get_config.return_value = self.config

        # Mock version detection
        stdout_version_detect = MagicMock()
        stdout_version_detect.read.return_value = b"Python 2.7.18"

        # Mock failed compatibility checks (empty output)
        stdout_empty = MagicMock()
        stdout_empty.read.return_value = b""

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_version_detect, MagicMock()),  # version detection
            (MagicMock(), stdout_empty, MagicMock()),  # python3.9 check
            (MagicMock(), stdout_empty, MagicMock()),  # python3.8 check
            (MagicMock(), stdout_empty, MagicMock()),  # python3.7 check
            (MagicMock(), stdout_empty, MagicMock()),  # python3.6 check
            (MagicMock(), stdout_empty, MagicMock()),  # python3 check
            (MagicMock(), stdout_empty, MagicMock()),  # python check
        ]

        # Mock the fallback call to setup_remote_environment
        with patch(
            "clustrix.utils.setup_remote_environment", return_value="fallback_result"
        ) as mock_fallback:
            result = setup_python_compatible_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

            assert result == "fallback_result"
            mock_fallback.assert_called_once_with(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

    @patch("clustrix.config.get_config")
    def test_setup_python_compatible_setup_failure(self, mock_get_config):
        """Test fallback when environment setup fails."""
        mock_get_config.return_value = self.config

        # Mock successful version checks but setup failure
        stdout_version_detect = MagicMock()
        stdout_version_detect.read.return_value = b"Python 3.9.5"

        stdout_version_check = MagicMock()
        stdout_version_check.read.return_value = b"(3, 9)"

        stdout_setup_fail = MagicMock()
        stdout_setup_fail.channel.recv_exit_status.return_value = 1

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_version_detect, MagicMock()),
            (MagicMock(), stdout_version_check, MagicMock()),
            (MagicMock(), stdout_setup_fail, MagicMock()),
        ]

        with patch(
            "clustrix.utils.setup_remote_environment", return_value="fallback_result"
        ) as mock_fallback:
            result = setup_python_compatible_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

            assert result == "fallback_result"
            mock_fallback.assert_called_once()


class TestRemoteEnvironmentSetup:
    """Test the main remote environment setup function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_ssh = MagicMock()
        self.config = ClusterConfig(
            cluster_type="ssh",
            cluster_host="cluster.edu",
            username="user",
            python_executable="python3.9",
        )
        self.work_dir = "/scratch/job_999"
        self.requirements = {"numpy": "1.21.0", "pandas": "1.3.0"}

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_conda(self, mock_get_pkg_mgr, mock_get_config):
        """Test remote environment setup with conda."""
        mock_get_config.return_value = self.config
        mock_get_pkg_mgr.return_value = "conda"

        # Mock successful command execution
        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        # Mock SFTP operations
        mock_sftp = MagicMock()
        mock_file = MagicMock()
        mock_sftp.open.return_value.__enter__.return_value = mock_file
        self.mock_ssh.open_sftp.return_value = mock_sftp

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_success,
            MagicMock(),
        )

        result = setup_remote_environment(
            self.mock_ssh, self.work_dir, self.requirements, self.config
        )

        assert result == "Environment setup completed successfully"

        # Verify SFTP file creation for conda environment
        mock_sftp.open.assert_called_once_with(f"{self.work_dir}/environment.yml", "w")
        mock_file.write.assert_called_once()

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_venv(self, mock_get_pkg_mgr, mock_get_config):
        """Test remote environment setup with virtual environment."""
        mock_get_config.return_value = self.config
        mock_get_pkg_mgr.return_value = "pip"

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_success,
            MagicMock(),
        )

        result = setup_remote_environment(
            self.mock_ssh, self.work_dir, self.requirements, self.config
        )

        assert result == "Environment setup completed successfully"

        # Verify exec_command was called with venv setup commands
        exec_args = self.mock_ssh.exec_command.call_args[0][0]
        assert "python3.9 -m venv venv" in exec_args
        assert "source venv/bin/activate" in exec_args

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_with_modules(
        self, mock_get_pkg_mgr, mock_get_config
    ):
        """Test remote environment setup with module loads and environment variables."""
        config_with_modules = ClusterConfig(
            cluster_type="ssh",
            cluster_host="cluster.edu",
            username="user",
            python_executable="python3.9",
            module_loads=["gcc/9.3.0", "python/3.9.5"],
            environment_variables={"OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"},
            pre_execution_commands=[
                "ulimit -s unlimited",
                "export PYTHONPATH=/custom/path",
            ],
        )

        mock_get_config.return_value = config_with_modules
        mock_get_pkg_mgr.return_value = "pip"

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_success,
            MagicMock(),
        )

        result = setup_remote_environment(
            self.mock_ssh, self.work_dir, self.requirements, config_with_modules
        )

        assert result == "Environment setup completed successfully"

        # Verify module loads, environment variables, and pre-execution commands
        exec_args = self.mock_ssh.exec_command.call_args[0][0]
        assert "module load gcc/9.3.0" in exec_args
        assert "module load python/3.9.5" in exec_args
        assert "export OMP_NUM_THREADS=4" in exec_args
        assert "export MKL_NUM_THREADS=4" in exec_args
        assert "ulimit -s unlimited" in exec_args
        assert "export PYTHONPATH=/custom/path" in exec_args

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_essential_packages_only(
        self, mock_get_pkg_mgr, mock_get_config
    ):
        """Test that only essential packages are installed from requirements."""
        mock_get_config.return_value = self.config
        mock_get_pkg_mgr.return_value = "pip"

        # Requirements with essential and non-essential packages
        mixed_requirements = {
            "numpy": "1.21.0",  # non-essential
            "dill": "0.3.4",  # essential
            "cloudpickle": "2.2.1",  # essential
            "tensorflow": "2.6.0",  # non-essential
        }

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_success,
            MagicMock(),
        )

        result = setup_remote_environment(
            self.mock_ssh, self.work_dir, mixed_requirements, self.config
        )

        assert result == "Environment setup completed successfully"

        # Verify only essential packages are installed
        exec_args = self.mock_ssh.exec_command.call_args[0][0]
        assert "dill==0.3.4" in exec_args
        assert "cloudpickle==2.2.1" in exec_args
        # Non-essential packages should not be installed
        assert "numpy==1.21.0" not in exec_args
        assert "tensorflow==2.6.0" not in exec_args

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_failure(self, mock_get_pkg_mgr, mock_get_config):
        """Test remote environment setup failure handling."""
        mock_get_config.return_value = self.config
        mock_get_pkg_mgr.return_value = "pip"

        # Mock failed command execution
        stdout_failure = MagicMock()
        stdout_failure.channel.recv_exit_status.return_value = 1

        stderr_failure = MagicMock()
        stderr_failure.read.return_value = b"Package installation failed"

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_failure,
            stderr_failure,
        )

        with pytest.raises(RuntimeError, match="Environment setup failed"):
            setup_remote_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_no_requirements(
        self, mock_get_pkg_mgr, mock_get_config
    ):
        """Test remote environment setup with no package requirements."""
        mock_get_config.return_value = self.config
        mock_get_pkg_mgr.return_value = "pip"

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.return_value = (
            MagicMock(),
            stdout_success,
            MagicMock(),
        )

        result = setup_remote_environment(self.mock_ssh, self.work_dir, {}, self.config)

        assert result == "Environment setup completed successfully"

        # With no requirements, no packages should be installed
        exec_args = self.mock_ssh.exec_command.call_args[0][0]
        assert "pip install" not in exec_args
        # But the venv should be created
        assert "python3.9 -m venv venv" in exec_args


class TestSFTPOperationsAndEdgeCases:
    """Test SFTP operations and edge cases in SSH environment setup."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_ssh = MagicMock()
        self.config = ClusterConfig(
            cluster_type="ssh", cluster_host="cluster.edu", username="user"
        )
        self.work_dir = "/scratch/test_sftp"
        self.requirements = {"dill": "0.3.4", "cloudpickle": "2.2.1"}

    @patch("clustrix.config.get_config")
    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_sftp_write_failure(
        self, mock_get_pkg_mgr, mock_get_config
    ):
        """Test handling of SFTP file write failures."""
        mock_get_config.return_value = self.config
        mock_get_pkg_mgr.return_value = "conda"

        # Mock SFTP operation failure
        mock_sftp = MagicMock()
        mock_sftp.open.side_effect = IOError("Permission denied")
        self.mock_ssh.open_sftp.return_value = mock_sftp

        with pytest.raises(IOError, match="Permission denied"):
            setup_remote_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_ssh_command_timeout(self, mock_get_config):
        """Test handling of SSH command timeouts during setup."""
        mock_get_config.return_value = self.config

        # Mock command execution that times out
        self.mock_ssh.exec_command.side_effect = Exception("Connection timeout")

        with pytest.raises(Exception, match="Connection timeout"):
            setup_two_venv_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_partial_python_version_parsing(self, mock_get_config):
        """Test handling of malformed Python version output."""
        mock_get_config.return_value = self.config

        # Mock malformed version output for all Python version checks
        stdout_empty = MagicMock()
        stdout_empty.read.return_value = b""

        # Need to provide enough responses for all Python version candidates
        responses = [(MagicMock(), stdout_empty, MagicMock()) for _ in range(12)]
        self.mock_ssh.exec_command.side_effect = responses

        with pytest.raises(RuntimeError, match="No compatible Python version found"):
            setup_two_venv_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )

    @patch("clustrix.config.get_config")
    def test_setup_python_compatible_version_parsing_edge_cases(self, mock_get_config):
        """Test Python version parsing edge cases."""
        mock_get_config.return_value = self.config

        # Mock version detection and all Python candidate checks to return empty
        stdout_version = MagicMock()
        stdout_version.read.return_value = b"Python 3.9.5"

        stdout_empty = MagicMock()
        stdout_empty.read.return_value = b""

        # Provide enough responses for version detection + all Python candidates
        responses = [
            (MagicMock(), stdout_version, MagicMock())
        ]  # Initial version check
        responses.extend(
            [(MagicMock(), stdout_empty, MagicMock()) for _ in range(8)]
        )  # All Python candidates

        self.mock_ssh.exec_command.side_effect = responses

        with patch("clustrix.utils.setup_remote_environment", return_value="fallback"):
            result = setup_python_compatible_environment(
                self.mock_ssh, self.work_dir, self.requirements, self.config
            )
            assert result == "fallback"

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_with_complex_cluster_packages(self, mock_get_config):
        """Test two-venv setup with complex cluster package configurations."""
        complex_config = ClusterConfig(
            cluster_type="ssh",
            cluster_host="cluster.edu",
            username="user",
            cluster_packages=[
                "simple-package==1.0.0",
                {
                    "package": "complex-package==2.0.0",
                    "pip_args": "--no-deps --force-reinstall",
                    "timeout": 600,
                },
                {"package": "gpu-package", "timeout": 900},
                "another-simple==0.5.0",
            ],
        )
        mock_get_config.return_value = complex_config

        # Mock conda availability
        stdout_conda = MagicMock()
        stdout_conda.read.return_value = b"conda 4.10.1"
        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_conda, MagicMock()),
            (MagicMock(), stdout_success, MagicMock()),
        ]

        result = setup_two_venv_environment(
            self.mock_ssh, self.work_dir, self.requirements, complex_config
        )

        assert result["uses_conda"] is True

        # Verify complex package installation commands
        setup_commands = self.mock_ssh.exec_command.call_args_list[1][0][0]
        assert "simple-package==1.0.0" in setup_commands
        assert "complex-package==2.0.0" in setup_commands
        assert "--no-deps --force-reinstall" in setup_commands
        assert "--timeout=600" in setup_commands
        assert "gpu-package" in setup_commands
        assert "--timeout=900" in setup_commands

    @patch("clustrix.config.get_config")
    def test_setup_two_venv_environment_variable_handling(self, mock_get_config):
        """Test two-venv setup with environment variables and module loads."""
        config_with_env = ClusterConfig(
            cluster_type="ssh",
            cluster_host="cluster.edu",
            username="user",
            module_loads=["python/3.9.0", "gcc/11.2.0"],
            environment_variables={
                "CUDA_VISIBLE_DEVICES": "0,1",
                "OMP_NUM_THREADS": "16",
            },
            pre_execution_commands=["export PATH=/custom/bin:$PATH"],
        )
        mock_get_config.return_value = config_with_env

        # Mock successful execution
        stdout_no_conda = MagicMock()
        stdout_no_conda.read.return_value = b""

        stdout_python_check = MagicMock()
        stdout_python_check.read.return_value = b"(3, 9)"

        stdout_success = MagicMock()
        stdout_success.channel.recv_exit_status.return_value = 0

        self.mock_ssh.exec_command.side_effect = [
            (MagicMock(), stdout_no_conda, MagicMock()),  # conda check
            (MagicMock(), stdout_python_check, MagicMock()),  # python version check
            (MagicMock(), stdout_success, MagicMock()),  # setup commands
        ]

        # Note: The setup_two_venv_environment function doesn't actually use these configs,
        # but we can test that it completes without errors
        result = setup_two_venv_environment(
            self.mock_ssh, self.work_dir, self.requirements, config_with_env
        )

        assert result["uses_conda"] is False
        assert "/bin/python" in result["venv1_python"]


class TestPackageManagerIntegration:
    """Test integration with different package managers in SSH environment."""

    @patch("clustrix.utils.is_uv_available")
    @patch("clustrix.utils.is_conda_available")
    def test_get_package_manager_with_uv_available(
        self, mock_conda_available, mock_uv_available
    ):
        """Test package manager detection when uv is available."""
        mock_uv_available.return_value = True
        mock_conda_available.return_value = False

        config = ClusterConfig(cluster_type="ssh", package_manager="auto")
        result = get_package_manager_command(config)

        assert result == "uv pip"

    @patch("clustrix.utils.is_uv_available")
    @patch("clustrix.utils.is_conda_available")
    def test_get_package_manager_fallback_to_pip(
        self, mock_conda_available, mock_uv_available
    ):
        """Test package manager fallback to pip when uv and conda unavailable."""
        mock_uv_available.return_value = False
        mock_conda_available.return_value = False

        config = ClusterConfig(cluster_type="ssh", package_manager="auto")
        result = get_package_manager_command(config)

        assert result == "pip"

    @patch("clustrix.utils.is_uv_available")
    @patch("clustrix.utils.is_conda_available")
    def test_get_package_manager_with_conda(
        self, mock_conda_available, mock_uv_available
    ):
        """Test package manager detection when conda is available."""
        mock_uv_available.return_value = False
        mock_conda_available.return_value = True

        config = ClusterConfig(cluster_type="ssh", package_manager="auto")
        result = get_package_manager_command(config)

        assert result == "conda"
