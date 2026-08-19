"""Tests for enhanced features: dependency handling, uv support, and configuration enhancements."""

import pytest
import subprocess
from unittest.mock import Mock, patch
import tempfile
import os

from clustrix.config import ClusterConfig, get_config, configure
from clustrix.utils import (
    get_environment_requirements,
    get_unreproducible_requirements,
    get_environment_info,
    is_uv_available,
    get_package_manager_command,
    setup_remote_environment,
)


class TestEnhancedDependencyHandling:
    """Test enhanced dependency handling with pip list --format=freeze."""

    def test_get_environment_requirements_covers_the_whole_environment(self):
        """Dependency capture must account for every installed distribution.

        Real environment, real metadata. The previous version of this test fed
        a hand-written `pip list` transcript through a mocked subprocess, so it
        could not see that the command actually run on a machine with uv
        installed produces a different -- and much shorter -- answer.
        """
        from clustrix.utils import _distribution_records

        records = _distribution_records()
        assert records, "no distributions found in this interpreter"

        requirements = get_environment_requirements()
        unreproducible = get_unreproducible_requirements()

        for canonical, record in records.items():
            if canonical == "clustrix":
                continue
            name = record["name"]
            if record["reason"]:
                assert name in unreproducible
                assert name not in requirements
            else:
                assert requirements.get(name) == record["version"]

    def test_get_environment_requirements_conda_packages(self):
        """conda-installed distributions must be captured like any other."""
        requirements = get_environment_requirements()
        conda_installed = [
            name
            for name in ("mkl", "conda", "intel-openmp", "numpy")
            if name in requirements
        ]
        if not conda_installed:
            pytest.skip("no conda-managed packages installed in this environment")
        for name in conda_installed:
            assert requirements[name]

    @patch("subprocess.run")
    def test_get_environment_requirements_editable_packages_excluded(self, mock_run):
        """Test that editable packages (-e) are excluded."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = """numpy==1.21.0
-e git+https://github.com/user/repo.git@main#egg=mypackage
pandas==1.3.0
-e /path/to/local/package
requests==2.25.1
"""
        mock_run.return_value = mock_result

        requirements = get_environment_requirements()

        # Verify editable packages are excluded
        assert "numpy" in requirements
        assert "pandas" in requirements
        assert "requests" in requirements
        # Editable packages should not be included
        editable_keys = [key for key in requirements.keys() if key.startswith("-e")]
        assert len(editable_keys) == 0

    @patch("subprocess.run")
    @patch("clustrix.utils.importlib.import_module")
    def test_get_environment_requirements_essential_fallback(
        self, mock_import, mock_run
    ):
        """Test fallback to essential packages when pip fails."""
        # Mock pip command failure
        mock_result = Mock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        # Mock successful import of essential packages
        mock_cloudpickle = Mock()
        mock_cloudpickle.__version__ = "2.0.0"
        mock_dill = Mock()
        mock_dill.__version__ = "0.3.4"

        def mock_import_side_effect(module_name):
            if module_name == "cloudpickle":
                return mock_cloudpickle
            elif module_name == "dill":
                return mock_dill
            else:
                raise ImportError(f"No module named '{module_name}'")

        mock_import.side_effect = mock_import_side_effect

        requirements = get_environment_requirements()

        # Verify essential packages are included (version will be actual installed version)
        assert "cloudpickle" in requirements
        assert "dill" in requirements

    @patch("subprocess.run")
    def test_get_environment_info_compatibility(self, mock_run):
        """Test get_environment_info for backward compatibility."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "numpy==1.21.0\npandas==1.3.0"
        mock_run.return_value = mock_result

        env_info = get_environment_info()

        # Should return string format for compatibility
        assert isinstance(env_info, str)
        assert "numpy==1.21.0" in env_info
        assert "pandas==1.3.0" in env_info


class TestUvPackageManagerSupport:
    """Test uv package manager support and integration."""

    @patch("subprocess.run")
    def test_is_uv_available_true(self, mock_run):
        """Test detecting uv when available."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = is_uv_available()

        assert result is True
        mock_run.assert_called_once_with(
            ["uv", "--version"], capture_output=True, text=True, timeout=10
        )

    @patch("subprocess.run")
    def test_is_uv_available_false_not_found(self, mock_run):
        """Test detecting uv when not installed."""
        mock_run.side_effect = FileNotFoundError("uv not found")

        result = is_uv_available()

        assert result is False

    @patch("subprocess.run")
    def test_is_uv_available_false_timeout(self, mock_run):
        """Test detecting uv when command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("uv", 10)

        result = is_uv_available()

        assert result is False

    @patch("clustrix.utils.is_uv_available")
    def test_get_package_manager_command_uv_explicit(self, mock_uv_available):
        """Test explicit uv configuration."""
        config = ClusterConfig(package_manager="uv")

        result = get_package_manager_command(config)

        assert result == "uv pip"
        # Should not check availability when explicitly configured
        mock_uv_available.assert_not_called()

    @patch("clustrix.utils.is_uv_available")
    def test_get_package_manager_command_auto_uv_available(self, mock_uv_available):
        """Test auto-detection when uv is available."""
        mock_uv_available.return_value = True
        config = ClusterConfig(package_manager="auto")

        result = get_package_manager_command(config)

        assert result == "uv pip"
        mock_uv_available.assert_called_once()

    @patch("clustrix.utils.is_conda_available")
    @patch("clustrix.utils.is_uv_available")
    def test_get_package_manager_command_auto_uv_unavailable(
        self, mock_uv_available, mock_conda_available
    ):
        """Test auto-detection when uv is not available."""
        mock_uv_available.return_value = False
        mock_conda_available.return_value = False
        config = ClusterConfig(package_manager="auto")

        result = get_package_manager_command(config)

        assert result == "pip"
        mock_uv_available.assert_called_once()

    def test_get_package_manager_command_pip_default(self):
        """Test default pip configuration."""
        config = ClusterConfig(package_manager="pip")

        result = get_package_manager_command(config)

        assert result == "pip"

    def test_get_package_manager_command_unknown_fallback(self):
        """Test fallback to pip for unknown package manager."""
        config = ClusterConfig(package_manager="unknown")

        result = get_package_manager_command(config)

        assert result == "pip"


class TestRemoteEnvironmentSetup:
    """Test remote environment setup with uv support."""

    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_with_uv(self, mock_get_pkg_manager):
        """Test remote environment setup using uv."""
        mock_get_pkg_manager.return_value = "uv pip"

        # Mock SSH client
        mock_ssh_client = Mock()
        mock_sftp = Mock()
        mock_ssh_client.open_sftp.return_value = mock_sftp

        # Mock file operations
        mock_file = Mock()
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_file)
        mock_context.__exit__ = Mock(return_value=None)
        mock_sftp.open.return_value = mock_context

        # Mock command execution
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_ssh_client.exec_command.return_value = (
            mock_stdin,
            mock_stdout,
            mock_stderr,
        )

        requirements = {"numpy": "1.21.0", "pandas": "1.3.0"}
        config = ClusterConfig(package_manager="uv")

        setup_remote_environment(mock_ssh_client, "/tmp/work", requirements, config)

        # Verify uv pip was used in requirements installation
        exec_calls = mock_ssh_client.exec_command.call_args_list
        install_command = None
        for call in exec_calls:
            command = call[0][0]
            if "install" in command:
                install_command = command
                break

        assert install_command is not None
        assert "uv pip install" in install_command

    @patch("clustrix.utils.get_package_manager_command")
    def test_setup_remote_environment_with_pip(self, mock_get_pkg_manager):
        """Test remote environment setup using traditional pip."""
        mock_get_pkg_manager.return_value = "pip"

        # Mock SSH client setup
        mock_ssh_client = Mock()
        mock_sftp = Mock()
        mock_ssh_client.open_sftp.return_value = mock_sftp
        mock_file = Mock()
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_file)
        mock_context.__exit__ = Mock(return_value=None)
        mock_sftp.open.return_value = mock_context

        # Mock successful command execution
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_ssh_client.exec_command.return_value = (
            mock_stdin,
            mock_stdout,
            mock_stderr,
        )

        requirements = {"requests": "2.25.1"}
        config = ClusterConfig(package_manager="pip")

        setup_remote_environment(mock_ssh_client, "/tmp/work", requirements, config)

        # Verify pip was used
        exec_calls = mock_ssh_client.exec_command.call_args_list
        install_command = None
        for call in exec_calls:
            command = call[0][0]
            if "install" in command:
                install_command = command
                break

        assert install_command is not None
        assert "pip install" in install_command
        assert "uv" not in install_command

    def test_setup_remote_environment_failure_handling(self):
        """Test handling of remote environment setup failures."""
        # Mock SSH client with command failure
        mock_ssh_client = Mock()
        mock_sftp = Mock()
        mock_ssh_client.open_sftp.return_value = mock_sftp
        mock_file = Mock()
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_file)
        mock_context.__exit__ = Mock(return_value=None)
        mock_sftp.open.return_value = mock_context

        # Mock failed command execution
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()
        mock_stderr.read.return_value.decode.return_value = (
            "Package installation failed"
        )
        mock_stdout.channel.recv_exit_status.return_value = 1  # Failure
        mock_ssh_client.exec_command.return_value = (
            mock_stdin,
            mock_stdout,
            mock_stderr,
        )

        requirements = {"nonexistent-package": "1.0.0"}
        config = ClusterConfig()

        with pytest.raises(RuntimeError, match="Environment setup failed"):
            setup_remote_environment(mock_ssh_client, "/tmp/work", requirements, config)


class TestConfigurationEnhancements:
    """Test enhanced configuration features."""

    def test_package_manager_configuration(self):
        """Test package manager configuration."""
        config = ClusterConfig(package_manager="uv")
        assert config.package_manager == "uv"

        config = ClusterConfig(package_manager="auto")
        assert config.package_manager == "auto"

        # Test default
        config = ClusterConfig()
        assert config.package_manager == "pip"

    def test_configuration_persistence_with_new_fields(self):
        """Test saving and loading configuration with new fields.

        Rewritten: this used to round-trip k8s_/cloud_/eks_ fields, which no
        longer exist on ClusterConfig. The property under test -- that a saved
        config reloads field for field -- is unchanged; only the fields it is
        stated over had to move to backends clustrix still has.
        """
        original_config = ClusterConfig(
            cluster_type="huggingface",
            hf_namespace="contextlab",
            hf_flavor="cpu-basic",
            package_manager="uv",
            venv_setup_timeout=600,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_file = f.name

        try:
            # Save configuration
            original_config.save_to_file(config_file)

            # Load configuration
            loaded_config = ClusterConfig.load_from_file(config_file)

            # Verify all fields are preserved
            assert loaded_config.cluster_type == "huggingface"
            assert loaded_config.hf_namespace == "contextlab"
            assert loaded_config.hf_flavor == "cpu-basic"
            assert loaded_config.package_manager == "uv"
            assert loaded_config.venv_setup_timeout == 600

        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_configure_function_with_new_parameters(self):
        """Test configure function with new parameters.

        Rewritten: the parameters it named (k8s_namespace, k8s_image,
        cloud_provider) belonged to removed backends. Restated over the
        HuggingFace Jobs settings, which are the ones a user configures today.
        """
        configure(
            hf_namespace="contextlab",
            hf_flavor="cpu-basic",
            package_manager="uv",
        )

        config = get_config()
        assert config.hf_namespace == "contextlab"
        assert config.hf_flavor == "cpu-basic"
        assert config.package_manager == "uv"

    def test_configure_rejects_a_setting_from_a_removed_backend(self):
        """configure() must not silently accept a field that no longer exists."""
        with pytest.raises(ValueError, match="Unknown configuration parameter"):
            configure(k8s_namespace="production")


class TestBackwardCompatibility:
    """Test backward compatibility of enhanced features."""

    def test_existing_configuration_still_works(self):
        """Test that existing configurations continue to work."""
        # Old-style configuration should still work
        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="login.cluster.edu",
            username="user",
            remote_work_dir="/scratch/user",
        )

        assert config.cluster_type == "slurm"
        assert config.cluster_host == "login.cluster.edu"
        assert config.username == "user"
        assert config.remote_work_dir == "/scratch/user"

        # New fields should have defaults. The two cloud_* assertions that
        # were here are gone with the fields themselves.
        assert config.package_manager == "pip"
        assert config.replicate_local_environment is True

    @patch("subprocess.run")
    def test_environment_capture_fallback(self, mock_run):
        """Test fallback behavior when new dependency methods fail."""
        # Mock pip list --format=freeze failure
        mock_run.side_effect = Exception("Command failed")

        # Should not raise exception, should return empty dict
        requirements = get_environment_requirements()
        assert isinstance(requirements, dict)

    def test_package_manager_command_unknown_input(self):
        """Test graceful handling of unknown package manager configuration."""
        config = ClusterConfig(package_manager="nonexistent")

        # Should fallback to pip
        result = get_package_manager_command(config)
        assert result == "pip"


class TestIntegrationScenarios:
    """Test integration scenarios with enhanced features."""

    @patch("subprocess.run")
    @patch("clustrix.utils.is_uv_available")
    def test_full_dependency_and_uv_workflow(self, mock_uv_available, mock_run):
        """Test complete workflow with enhanced dependency handling and uv."""
        # Mock uv availability
        mock_uv_available.return_value = True

        # Mock pip list --format=freeze output
        mock_pip_result = Mock()
        mock_pip_result.returncode = 0
        mock_pip_result.stdout = "numpy==1.21.0\nrequests==2.25.1"
        mock_run.return_value = mock_pip_result

        # Test auto package manager selection
        config = ClusterConfig(package_manager="auto")
        pkg_manager = get_package_manager_command(config)
        assert pkg_manager == "uv pip"

        # Test dependency capture
        requirements = get_environment_requirements()
        assert "numpy" in requirements
        assert "requests" in requirements

    @patch("clustrix.utils.is_conda_available")
    @patch("clustrix.utils.is_uv_available")
    def test_graceful_degradation_no_uv(self, mock_uv_available, mock_conda_available):
        """Test graceful degradation when uv is not available."""
        mock_uv_available.return_value = False
        mock_conda_available.return_value = False

        config = ClusterConfig(package_manager="auto")
        pkg_manager = get_package_manager_command(config)

        # Should fallback to pip
        assert pkg_manager == "pip"
