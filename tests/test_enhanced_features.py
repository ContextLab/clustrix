"""Tests for enhanced features: dependency handling, uv support, and configuration enhancements."""

import pytest
import subprocess
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os

from clustrix.config import ClusterConfig, get_config, configure
from clustrix.utils import (
    get_environment_requirements,
    get_environment_info,
    is_uv_available,
    get_package_manager_command,
    setup_remote_environment,
)


class TestEnhancedDependencyHandling:
    """Test enhanced dependency handling with pip list --format=freeze."""

    @patch("subprocess.run")
    def test_get_environment_requirements_pip_list_format(self, mock_run):
        """Test using pip list --format=freeze for dependency capture."""
        # Mock successful pip list --format=freeze output
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = """numpy==1.21.0
pandas==1.3.0
scipy==1.7.0
matplotlib==3.4.2
requests==2.25.1
"""
        mock_run.return_value = mock_result

        requirements = get_environment_requirements()

        # Verify pip list --format=freeze was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "pip" in call_args
        assert "list" in call_args
        assert "--format=freeze" in call_args

        # Verify requirements were parsed correctly
        assert requirements["numpy"] == "1.21.0"
        assert requirements["pandas"] == "1.3.0"
        assert requirements["scipy"] == "1.7.0"
        assert requirements["matplotlib"] == "3.4.2"
        assert requirements["requests"] == "2.25.1"

    @patch("subprocess.run")
    def test_get_environment_requirements_conda_packages(self, mock_run):
        """Test capturing conda-installed packages."""
        # Mock output that includes conda-installed packages
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = """numpy==1.21.0
pandas==1.3.0
mkl==2021.3.0
intel-openmp==2021.3.0
conda==4.10.3
"""
        mock_run.return_value = mock_result

        requirements = get_environment_requirements()

        # Verify conda packages are captured
        assert requirements["mkl"] == "2021.3.0"
        assert requirements["intel-openmp"] == "2021.3.0"
        assert requirements["conda"] == "4.10.3"

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
    @patch("importlib.import_module")
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

        # Verify essential packages are included
        assert requirements["cloudpickle"] == "2.0.0"
        assert requirements["dill"] == "0.3.4"

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

    @patch("clustrix.utils.is_uv_available")
    def test_get_package_manager_command_auto_uv_unavailable(self, mock_uv_available):
        """Test auto-detection when uv is not available."""
        mock_uv_available.return_value = False
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
        mock_sftp.open.return_value.__enter__.return_value = mock_file

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
        mock_sftp.open.return_value.__enter__.return_value = mock_file

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
        mock_sftp.open.return_value.__enter__.return_value = mock_file

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

    def test_kubernetes_configuration_fields(self):
        """Test new Kubernetes configuration fields."""
        config = ClusterConfig(
            cluster_type="kubernetes",
            k8s_namespace="production",
            k8s_image="python:3.12-slim",
            k8s_service_account="clustrix-sa",
            k8s_pull_policy="Always",
            k8s_job_ttl_seconds=7200,
            k8s_backoff_limit=5,
        )

        assert config.k8s_namespace == "production"
        assert config.k8s_image == "python:3.12-slim"
        assert config.k8s_service_account == "clustrix-sa"
        assert config.k8s_pull_policy == "Always"
        assert config.k8s_job_ttl_seconds == 7200
        assert config.k8s_backoff_limit == 5

    def test_cloud_provider_configuration_fields(self):
        """Test cloud provider configuration fields."""
        config = ClusterConfig(
            cloud_provider="aws",
            cloud_region="us-east-1",
            cloud_auto_configure=True,
            eks_cluster_name="production-cluster",
            aws_profile="production",
        )

        assert config.cloud_provider == "aws"
        assert config.cloud_region == "us-east-1"
        assert config.cloud_auto_configure is True
        assert config.eks_cluster_name == "production-cluster"
        assert config.aws_profile == "production"

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
        """Test saving and loading configuration with new fields."""
        original_config = ClusterConfig(
            cluster_type="kubernetes",
            k8s_namespace="test",
            k8s_image="python:3.11",
            cloud_provider="aws",
            cloud_auto_configure=True,
            package_manager="uv",
            eks_cluster_name="test-cluster",
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_file = f.name

        try:
            # Save configuration
            original_config.save_to_file(config_file)

            # Load configuration
            loaded_config = ClusterConfig.load_from_file(config_file)

            # Verify all fields are preserved
            assert loaded_config.cluster_type == "kubernetes"
            assert loaded_config.k8s_namespace == "test"
            assert loaded_config.k8s_image == "python:3.11"
            assert loaded_config.cloud_provider == "aws"
            assert loaded_config.cloud_auto_configure is True
            assert loaded_config.package_manager == "uv"
            assert loaded_config.eks_cluster_name == "test-cluster"

        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)

    def test_configure_function_with_new_parameters(self):
        """Test configure function with new parameters."""
        # Test configuring new Kubernetes parameters
        configure(
            k8s_namespace="custom",
            k8s_image="python:3.12",
            cloud_provider="azure",
            package_manager="uv",
        )

        config = get_config()
        assert config.k8s_namespace == "custom"
        assert config.k8s_image == "python:3.12"
        assert config.cloud_provider == "azure"
        assert config.package_manager == "uv"

    def test_azure_specific_configuration(self):
        """Test Azure-specific configuration fields."""
        config = ClusterConfig(
            cloud_provider="azure",
            aks_cluster_name="my-cluster",
            azure_resource_group="my-rg",
            azure_subscription_id="subscription-123",
        )

        assert config.aks_cluster_name == "my-cluster"
        assert config.azure_resource_group == "my-rg"
        assert config.azure_subscription_id == "subscription-123"

    def test_gcp_specific_configuration(self):
        """Test GCP-specific configuration fields."""
        config = ClusterConfig(
            cloud_provider="gcp",
            gke_cluster_name="my-gke-cluster",
            gcp_project_id="my-project-123",
            gcp_zone="us-central1-a",
        )

        assert config.gke_cluster_name == "my-gke-cluster"
        assert config.gcp_project_id == "my-project-123"
        assert config.gcp_zone == "us-central1-a"


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

        # New fields should have defaults
        assert config.package_manager == "pip"
        assert config.cloud_provider == "manual"
        assert config.cloud_auto_configure is False

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

    @patch("clustrix.utils.is_uv_available")
    def test_graceful_degradation_no_uv(self, mock_uv_available):
        """Test graceful degradation when uv is not available."""
        mock_uv_available.return_value = False

        config = ClusterConfig(package_manager="auto")
        pkg_manager = get_package_manager_command(config)

        # Should fallback to pip
        assert pkg_manager == "pip"

    def test_kubernetes_with_cloud_provider_config(self):
        """Test Kubernetes configuration with cloud provider settings."""
        config = ClusterConfig(
            cluster_type="kubernetes",
            cloud_provider="aws",
            cloud_auto_configure=True,
            eks_cluster_name="prod-cluster",
            k8s_namespace="production",
            package_manager="uv",
        )

        # All settings should coexist
        assert config.cluster_type == "kubernetes"
        assert config.cloud_provider == "aws"
        assert config.cloud_auto_configure is True
        assert config.eks_cluster_name == "prod-cluster"
        assert config.k8s_namespace == "production"
        assert config.package_manager == "uv"
