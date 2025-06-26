"""Tests for cloud provider integration."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import subprocess
import os

from clustrix.cloud_providers import (
    CloudProviderDetector,
    AWSEKSConfigurator,
    AzureAKSConfigurator,
    GoogleGKEConfigurator,
    CloudProviderManager,
    CloudProviderError,
)
from clustrix.config import ClusterConfig


class TestCloudProviderDetector:
    """Test cloud provider detection."""

    def test_detect_provider_aws(self):
        """Test AWS provider detection."""
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "test"}):
            provider = CloudProviderDetector.detect_provider()
            assert provider == "aws"

    def test_detect_provider_azure(self):
        """Test Azure provider detection."""
        with patch.dict(os.environ, {"AZURE_SUBSCRIPTION_ID": "test"}):
            provider = CloudProviderDetector.detect_provider()
            assert provider == "azure"

    def test_detect_provider_gcp(self):
        """Test GCP provider detection."""
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "test"}):
            provider = CloudProviderDetector.detect_provider()
            assert provider == "gcp"

    def test_detect_provider_manual(self):
        """Test fallback to manual when no provider detected."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                provider = CloudProviderDetector.detect_provider()
                assert provider == "manual"

    def test_check_aws_context_with_cli(self):
        """Test AWS context check with CLI."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            result = CloudProviderDetector._check_aws_context()
            assert result is True

    def test_check_aws_context_cli_failure(self):
        """Test AWS context check with CLI failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1)
            result = CloudProviderDetector._check_aws_context()
            assert result is False

    def test_check_azure_context_with_cli(self):
        """Test Azure context check with CLI."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            result = CloudProviderDetector._check_azure_context()
            assert result is True

    def test_check_gcp_context_with_cli(self):
        """Test GCP context check with CLI."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0, stdout=b"ACTIVE test@example.com"
            )
            result = CloudProviderDetector._check_gcp_context()
            assert result is True


class TestAWSEKSConfigurator:
    """Test AWS EKS configuration."""

    def test_configure_cluster_success(self):
        """Test successful EKS cluster configuration."""
        config = ClusterConfig()
        configurator = AWSEKSConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")

            result = configurator.configure_cluster("test-cluster", "us-west-2")

            assert result["provider"] == "aws"
            assert result["cluster_name"] == "test-cluster"
            assert result["region"] == "us-west-2"
            assert result["configured"] is True

    def test_configure_cluster_with_profile(self):
        """Test EKS configuration with AWS profile."""
        config = ClusterConfig(aws_profile="test-profile")
        configurator = AWSEKSConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")

            configurator.configure_cluster("test-cluster", "us-west-2")

            # Verify profile was included in command (first call is AWS EKS, second is kubectl verify)
            aws_call_args = mock_run.call_args_list[0][0][0]
            assert "--profile" in aws_call_args
            assert "test-profile" in aws_call_args

    def test_configure_cluster_failure(self):
        """Test EKS configuration failure."""
        config = ClusterConfig()
        configurator = AWSEKSConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stderr="Error message")

            with pytest.raises(CloudProviderError):
                configurator.configure_cluster("test-cluster", "us-west-2")

    def test_configure_cluster_timeout(self):
        """Test EKS configuration timeout."""
        config = ClusterConfig()
        configurator = AWSEKSConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=60)

            with pytest.raises(CloudProviderError):
                configurator.configure_cluster("test-cluster", "us-west-2")


class TestAzureAKSConfigurator:
    """Test Azure AKS configuration."""

    def test_configure_cluster_success(self):
        """Test successful AKS cluster configuration."""
        config = ClusterConfig()
        configurator = AzureAKSConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")

            result = configurator.configure_cluster("test-cluster", "test-rg")

            assert result["provider"] == "azure"
            assert result["cluster_name"] == "test-cluster"
            assert result["resource_group"] == "test-rg"
            assert result["configured"] is True

    def test_configure_cluster_with_subscription(self):
        """Test AKS configuration with subscription ID."""
        config = ClusterConfig(azure_subscription_id="test-sub")
        configurator = AzureAKSConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")

            configurator.configure_cluster("test-cluster", "test-rg")

            # Verify subscription was included in command (first call is AZ CLI, second is kubectl verify)
            az_call_args = mock_run.call_args_list[0][0][0]
            assert "--subscription" in az_call_args
            assert "test-sub" in az_call_args


class TestGoogleGKEConfigurator:
    """Test Google GKE configuration."""

    def test_configure_cluster_success(self):
        """Test successful GKE cluster configuration."""
        config = ClusterConfig()
        configurator = GoogleGKEConfigurator(config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")

            result = configurator.configure_cluster(
                "test-cluster", "us-central1-a", "test-project"
            )

            assert result["provider"] == "gcp"
            assert result["cluster_name"] == "test-cluster"
            assert result["zone"] == "us-central1-a"
            assert result["project_id"] == "test-project"
            assert result["configured"] is True


class TestCloudProviderManager:
    """Test cloud provider manager."""

    def test_auto_configure_disabled(self):
        """Test auto-configuration when disabled."""
        config = ClusterConfig(cloud_auto_configure=False)
        manager = CloudProviderManager(config)

        result = manager.auto_configure()

        assert result["auto_configured"] is False
        assert "disabled" in result["reason"]

    def test_auto_configure_manual_provider(self):
        """Test auto-configuration with manual provider."""
        config = ClusterConfig(cloud_auto_configure=True, cloud_provider="manual")

        with patch.object(
            CloudProviderDetector, "detect_provider", return_value="manual"
        ):
            manager = CloudProviderManager(config)
            result = manager.auto_configure()

            assert result["auto_configured"] is False
            assert "No cloud provider detected" in result["reason"]

    def test_auto_configure_aws_success(self):
        """Test successful AWS auto-configuration."""
        config = ClusterConfig(
            cloud_auto_configure=True,
            cloud_provider="aws",
            eks_cluster_name="test-cluster",
            cloud_region="us-west-2",
        )

        manager = CloudProviderManager(config)

        with patch.object(AWSEKSConfigurator, "configure_cluster") as mock_configure:
            mock_configure.return_value = {
                "provider": "aws",
                "cluster_name": "test-cluster",
                "region": "us-west-2",
                "configured": True,
            }

            result = manager.auto_configure()

            assert result["auto_configured"] is True
            assert result["provider"] == "aws"

    def test_auto_configure_aws_missing_config(self):
        """Test AWS auto-configuration with missing configuration."""
        config = ClusterConfig(
            cloud_auto_configure=True,
            cloud_provider="aws",
            # Missing cluster_name and region
        )

        manager = CloudProviderManager(config)
        result = manager.auto_configure()

        assert result["auto_configured"] is False
        assert "Missing EKS cluster name or region" in result["reason"]

    def test_auto_configure_azure_success(self):
        """Test successful Azure auto-configuration."""
        config = ClusterConfig(
            cloud_auto_configure=True,
            cloud_provider="azure",
            aks_cluster_name="test-cluster",
            azure_resource_group="test-rg",
        )

        manager = CloudProviderManager(config)

        with patch.object(AzureAKSConfigurator, "configure_cluster") as mock_configure:
            mock_configure.return_value = {
                "provider": "azure",
                "cluster_name": "test-cluster",
                "resource_group": "test-rg",
                "configured": True,
            }

            result = manager.auto_configure()

            assert result["auto_configured"] is True
            assert result["provider"] == "azure"

    def test_auto_configure_gcp_success(self):
        """Test successful GCP auto-configuration."""
        config = ClusterConfig(
            cloud_auto_configure=True,
            cloud_provider="gcp",
            gke_cluster_name="test-cluster",
            gcp_zone="us-central1-a",
            gcp_project_id="test-project",
        )

        manager = CloudProviderManager(config)

        with patch.object(GoogleGKEConfigurator, "configure_cluster") as mock_configure:
            mock_configure.return_value = {
                "provider": "gcp",
                "cluster_name": "test-cluster",
                "zone": "us-central1-a",
                "project_id": "test-project",
                "configured": True,
            }

            result = manager.auto_configure()

            assert result["auto_configured"] is True
            assert result["provider"] == "gcp"

    def test_auto_configure_exception_handling(self):
        """Test exception handling in auto-configuration."""
        config = ClusterConfig(
            cloud_auto_configure=True,
            cloud_provider="aws",
            eks_cluster_name="test-cluster",
            cloud_region="us-west-2",
        )

        manager = CloudProviderManager(config)

        with patch.object(AWSEKSConfigurator, "configure_cluster") as mock_configure:
            mock_configure.side_effect = Exception("Test error")

            result = manager.auto_configure()

            assert result["auto_configured"] is False
            assert "Test error" in result["error"]
