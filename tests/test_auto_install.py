"""Tests for automatic dependency installation functionality."""

import subprocess
import sys
from unittest.mock import patch, Mock, MagicMock
import pytest

from clustrix.auto_install import (
    check_dependencies_installed,
    install_provider_dependencies,
    ensure_cloud_provider_dependencies,
    get_installation_command,
    CLOUD_PROVIDER_DEPS,
    CLUSTER_TYPE_TO_PROVIDER,
)


class TestCheckDependenciesInstalled:
    """Test dependency checking functionality."""

    def test_unknown_provider_returns_true(self):
        """Test that unknown providers return True (no special deps needed)."""
        result = check_dependencies_installed("unknown_provider")
        assert result is True

    def test_aws_dependencies_available(self):
        """Test AWS dependency checking when boto3 is available."""
        with patch("builtins.__import__") as mock_import:
            # Mock successful import
            mock_import.return_value = MagicMock()
            result = check_dependencies_installed("aws")
            assert result is True

    def test_aws_dependencies_missing(self):
        """Test AWS dependency checking when boto3 is missing."""
        with patch("builtins.__import__") as mock_import:
            # Mock ImportError
            mock_import.side_effect = ImportError("No module named 'boto3'")
            result = check_dependencies_installed("aws")
            assert result is False

    def test_azure_dependencies_available(self):
        """Test Azure dependency checking when modules are available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()
            result = check_dependencies_installed("azure")
            assert result is True

    def test_azure_dependencies_missing(self):
        """Test Azure dependency checking when modules are missing."""
        with patch("builtins.__import__") as mock_import:
            mock_import.side_effect = ImportError("No module named 'azure'")
            result = check_dependencies_installed("azure")
            assert result is False

    def test_gcp_dependencies_available(self):
        """Test GCP dependency checking when modules are available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()
            result = check_dependencies_installed("gcp")
            assert result is True

    def test_gcp_dependencies_missing(self):
        """Test GCP dependency checking when modules are missing."""
        with patch("builtins.__import__") as mock_import:
            mock_import.side_effect = ImportError("No module named 'google'")
            result = check_dependencies_installed("gcp")
            assert result is False

    def test_kubernetes_dependencies_available(self):
        """Test Kubernetes dependency checking when available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()
            result = check_dependencies_installed("kubernetes")
            assert result is True

    def test_kubernetes_dependencies_missing(self):
        """Test Kubernetes dependency checking when missing."""
        with patch("builtins.__import__") as mock_import:
            mock_import.side_effect = ImportError("No module named 'kubernetes'")
            result = check_dependencies_installed("kubernetes")
            assert result is False


class TestInstallProviderDependencies:
    """Test dependency installation functionality."""

    def test_unknown_provider_returns_true(self):
        """Test that unknown providers return True."""
        result = install_provider_dependencies("unknown_provider")
        assert result is True

    @patch("clustrix.auto_install.check_dependencies_installed")
    def test_already_installed_returns_true(self, mock_check):
        """Test that already installed dependencies return True."""
        mock_check.return_value = True
        result = install_provider_dependencies("aws")
        assert result is True
        mock_check.assert_called_once_with("aws")

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.logger")
    def test_auto_install_false_with_warning(self, mock_logger, mock_check):
        """Test that auto_install=False shows warning and returns False."""
        mock_check.return_value = False

        result = install_provider_dependencies("aws", auto_install=False, quiet=False)

        assert result is False
        mock_logger.warning.assert_called_once()
        warning_call = mock_logger.warning.call_args[0][0]
        assert "Missing dependencies for aws provider" in warning_call
        assert "pip install" in warning_call

    @patch("clustrix.auto_install.check_dependencies_installed")
    def test_auto_install_false_quiet_no_warning(self, mock_check):
        """Test that auto_install=False with quiet=True doesn't show warning."""
        mock_check.return_value = False

        with patch("clustrix.auto_install.logger") as mock_logger:
            result = install_provider_dependencies(
                "aws", auto_install=False, quiet=True
            )

            assert result is False
            mock_logger.warning.assert_not_called()

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.subprocess.run")
    @patch("clustrix.auto_install.logger")
    def test_successful_installation(self, mock_logger, mock_subprocess, mock_check):
        """Test successful dependency installation."""
        mock_check.return_value = False
        mock_subprocess.return_value = Mock()

        result = install_provider_dependencies("aws", auto_install=True, quiet=False)

        assert result is True
        mock_subprocess.assert_called_once()

        # Check that the subprocess was called with correct arguments
        call_args = mock_subprocess.call_args[0][0]
        assert call_args[0] == sys.executable
        assert call_args[1:3] == ["-m", "pip"]
        assert call_args[3] == "install"
        assert "boto3>=1.26.0" in call_args
        assert "kubernetes>=20.13.0" in call_args

        # Check logging
        mock_logger.info.assert_called()
        info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
        assert any("Installing aws dependencies" in msg for msg in info_calls)
        assert any(
            "Successfully installed aws dependencies" in msg for msg in info_calls
        )

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.subprocess.run")
    @patch("clustrix.auto_install.logger")
    def test_successful_installation_quiet(
        self, mock_logger, mock_subprocess, mock_check
    ):
        """Test successful dependency installation in quiet mode."""
        mock_check.return_value = False
        mock_subprocess.return_value = Mock()

        result = install_provider_dependencies("aws", auto_install=True, quiet=True)

        assert result is True

        # Check that --quiet was added to command
        call_args = mock_subprocess.call_args[0][0]
        assert "--quiet" in call_args

        # Check no logging occurred
        mock_logger.info.assert_not_called()

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.subprocess.run")
    @patch("clustrix.auto_install.logger")
    def test_subprocess_called_process_error(
        self, mock_logger, mock_subprocess, mock_check
    ):
        """Test handling of subprocess.CalledProcessError."""
        mock_check.return_value = False
        error = subprocess.CalledProcessError(1, "pip", stderr="Installation failed")
        mock_subprocess.side_effect = error

        result = install_provider_dependencies("aws", auto_install=True, quiet=False)

        assert result is False
        mock_logger.error.assert_called()
        error_calls = [call[0][0] for call in mock_logger.error.call_args_list]
        assert any("Failed to install aws dependencies" in msg for msg in error_calls)
        assert any("Error output: Installation failed" in msg for msg in error_calls)

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.subprocess.run")
    @patch("clustrix.auto_install.logger")
    def test_subprocess_called_process_error_quiet(
        self, mock_logger, mock_subprocess, mock_check
    ):
        """Test handling of subprocess.CalledProcessError in quiet mode."""
        mock_check.return_value = False
        error = subprocess.CalledProcessError(1, "pip")
        mock_subprocess.side_effect = error

        result = install_provider_dependencies("aws", auto_install=True, quiet=True)

        assert result is False
        mock_logger.error.assert_not_called()

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.subprocess.run")
    @patch("clustrix.auto_install.logger")
    def test_generic_exception_handling(self, mock_logger, mock_subprocess, mock_check):
        """Test handling of generic exceptions."""
        mock_check.return_value = False
        mock_subprocess.side_effect = Exception("Unexpected error")

        result = install_provider_dependencies("aws", auto_install=True, quiet=False)

        assert result is False
        mock_logger.error.assert_called_once()
        error_call = mock_logger.error.call_args[0][0]
        assert "Unexpected error installing aws dependencies" in error_call

    @patch("clustrix.auto_install.check_dependencies_installed")
    @patch("clustrix.auto_install.subprocess.run")
    def test_generic_exception_handling_quiet(self, mock_subprocess, mock_check):
        """Test handling of generic exceptions in quiet mode."""
        mock_check.return_value = False
        mock_subprocess.side_effect = Exception("Unexpected error")

        with patch("clustrix.auto_install.logger") as mock_logger:
            result = install_provider_dependencies("aws", auto_install=True, quiet=True)

            assert result is False
            mock_logger.error.assert_not_called()


class TestEnsureCloudProviderDependencies:
    """Test the ensure cloud provider dependencies function."""

    @patch("clustrix.auto_install.install_provider_dependencies")
    def test_cloud_provider_specified(self, mock_install):
        """Test with cloud_provider specified."""
        mock_install.return_value = True

        result = ensure_cloud_provider_dependencies(
            cloud_provider="aws", auto_install=True, quiet=False
        )

        assert result is True
        mock_install.assert_called_once_with("aws", auto_install=True, quiet=False)

    @patch("clustrix.auto_install.install_provider_dependencies")
    def test_cloud_provider_manual_ignored(self, mock_install):
        """Test that manual cloud provider is ignored."""
        result = ensure_cloud_provider_dependencies(cloud_provider="manual")

        assert result is True
        mock_install.assert_not_called()

    @patch("clustrix.auto_install.install_provider_dependencies")
    def test_cluster_type_mapping(self, mock_install):
        """Test cluster type to provider mapping."""
        mock_install.return_value = True

        result = ensure_cloud_provider_dependencies(cluster_type="aws_ec2")

        assert result is True
        mock_install.assert_called_once_with("aws", auto_install=True, quiet=False)

    @patch("clustrix.auto_install.install_provider_dependencies")
    def test_unknown_cluster_type(self, mock_install):
        """Test with unknown cluster type."""
        result = ensure_cloud_provider_dependencies(cluster_type="unknown")

        assert result is True
        mock_install.assert_not_called()

    @patch("clustrix.auto_install.install_provider_dependencies")
    def test_no_provider_needed(self, mock_install):
        """Test when no provider is needed."""
        result = ensure_cloud_provider_dependencies()

        assert result is True
        mock_install.assert_not_called()

    @patch("clustrix.auto_install.install_provider_dependencies")
    def test_cloud_provider_overrides_cluster_type(self, mock_install):
        """Test that cloud_provider takes precedence over cluster_type."""
        mock_install.return_value = True

        result = ensure_cloud_provider_dependencies(
            cluster_type="aws_ec2", cloud_provider="gcp"
        )

        assert result is True
        mock_install.assert_called_once_with("gcp", auto_install=True, quiet=False)


class TestGetInstallationCommand:
    """Test the get installation command function."""

    def test_cloud_provider_specified(self):
        """Test with cloud_provider specified."""
        result = get_installation_command(cloud_provider="aws")

        expected_deps = CLOUD_PROVIDER_DEPS["aws"]
        expected = f"pip install {' '.join(expected_deps)}"
        assert result == expected

    def test_cloud_provider_manual_returns_none(self):
        """Test that manual cloud provider returns None."""
        result = get_installation_command(cloud_provider="manual")
        assert result is None

    def test_cluster_type_mapping(self):
        """Test cluster type to provider mapping."""
        result = get_installation_command(cluster_type="azure_aks")

        expected_deps = CLOUD_PROVIDER_DEPS["azure"]
        expected = f"pip install {' '.join(expected_deps)}"
        assert result == expected

    def test_unknown_cluster_type_returns_none(self):
        """Test with unknown cluster type."""
        result = get_installation_command(cluster_type="unknown")
        assert result is None

    def test_no_provider_returns_none(self):
        """Test when no provider is specified."""
        result = get_installation_command()
        assert result is None

    def test_cloud_provider_overrides_cluster_type(self):
        """Test that cloud_provider takes precedence over cluster_type."""
        result = get_installation_command(cluster_type="aws_ec2", cloud_provider="gcp")

        expected_deps = CLOUD_PROVIDER_DEPS["gcp"]
        expected = f"pip install {' '.join(expected_deps)}"
        assert result == expected

    def test_unknown_provider_returns_none(self):
        """Test with unknown provider returns None."""
        result = get_installation_command(cloud_provider="unknown_provider")
        assert result is None


class TestConstants:
    """Test the module constants and mappings."""

    def test_cloud_provider_deps_structure(self):
        """Test that CLOUD_PROVIDER_DEPS has expected structure."""
        expected_providers = {
            "aws",
            "azure",
            "gcp",
            "kubernetes",
            "lambda_cloud",
            "huggingface_spaces",
        }
        assert set(CLOUD_PROVIDER_DEPS.keys()) == expected_providers

        # Ensure all values are lists of strings
        for provider, deps in CLOUD_PROVIDER_DEPS.items():
            assert isinstance(deps, list)
            assert all(isinstance(dep, str) for dep in deps)
            assert all(">=" in dep or "==" in dep for dep in deps)  # Version specs

    def test_cluster_type_to_provider_mapping(self):
        """Test that cluster type mappings are valid."""
        expected_mappings = {
            "kubernetes": "kubernetes",
            "aws_ec2": "aws",
            "aws_eks": "aws",
            "azure_vm": "azure",
            "azure_aks": "azure",
            "gcp_vm": "gcp",
            "gcp_gke": "gcp",
            "lambda_cloud": "lambda_cloud",
            "huggingface_spaces": "huggingface_spaces",
        }

        assert CLUSTER_TYPE_TO_PROVIDER == expected_mappings

        # Ensure all mapped providers exist in CLOUD_PROVIDER_DEPS
        for provider in CLUSTER_TYPE_TO_PROVIDER.values():
            assert provider in CLOUD_PROVIDER_DEPS


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple functions."""

    @patch("clustrix.auto_install.subprocess.run")
    def test_full_workflow_aws_missing_deps(self, mock_subprocess):
        """Test full workflow when AWS dependencies are missing."""
        mock_subprocess.return_value = Mock()

        with patch("builtins.__import__") as mock_import:
            # First call (check) fails, second call (after install) succeeds
            mock_import.side_effect = [
                ImportError("No module named 'boto3'"),  # First check
                MagicMock(),  # After installation
            ]

            # Test the workflow
            result = ensure_cloud_provider_dependencies(cluster_type="aws_ec2")

            assert result is True
            mock_subprocess.assert_called_once()

    def test_installation_command_matches_dependencies(self):
        """Test that installation commands match actual dependencies."""
        for provider in CLOUD_PROVIDER_DEPS:
            command = get_installation_command(cloud_provider=provider)
            expected_deps = CLOUD_PROVIDER_DEPS[provider]

            for dep in expected_deps:
                assert dep in command
