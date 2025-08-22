"""Tests for cloud provider integrations."""

import pytest
from unittest.mock import MagicMock, patch

from clustrix.cloud_providers.base import CloudProvider
from clustrix.cloud_providers import PROVIDERS


class TestCloudProviderBase:
    """Test the base cloud provider class."""

    def test_abstract_methods(self):
        """Test that base class can't be instantiated."""
        with pytest.raises(TypeError):
            CloudProvider()

    def test_is_authenticated(self):
        """Test authentication status check."""

        # Create a concrete implementation for testing
        class TestProvider(CloudProvider):
            def authenticate(self, **credentials):
                self.authenticated = True
                return True

            def validate_credentials(self):
                return True

            def create_cluster(self, cluster_name, **kwargs):
                return {}

            def delete_cluster(self, cluster_identifier):
                return True

            def get_cluster_status(self, cluster_identifier):
                return {}

            def list_clusters(self):
                return []

            def get_cluster_config(self, cluster_identifier):
                return {}

            def estimate_cost(self, **kwargs):
                return {}

            def get_available_instance_types(self, region=None):
                return ["test-instance"]

            def get_available_regions(self):
                return ["test-region"]

        provider = TestProvider()
        assert not provider.is_authenticated()

        provider.authenticate()
        assert provider.is_authenticated()


@pytest.mark.skipif("aws" not in PROVIDERS, reason="boto3 not installed")
class TestAWSProvider:
    """Test AWS provider implementation."""

    @pytest.fixture
    def mock_boto3(self):
        """Mock boto3 for testing."""
        with patch("clustrix.cloud_providers.aws.boto3") as mock_boto3:
            # Mock session
            mock_session = MagicMock()
            mock_boto3.Session.return_value = mock_session

            # Mock clients
            mock_ec2 = MagicMock()
            mock_eks = MagicMock()
            mock_iam = MagicMock()
            mock_sts = MagicMock()

            mock_session.client.side_effect = lambda service: {
                "ec2": mock_ec2,
                "eks": mock_eks,
                "iam": mock_iam,
                "sts": mock_sts,
            }[service]

            yield {
                "boto3": mock_boto3,
                "session": mock_session,
                "ec2": mock_ec2,
                "eks": mock_eks,
                "iam": mock_iam,
                "sts": mock_sts,
            }

    def test_authenticate_success(self, mock_boto3):
        """Test successful authentication."""
        from clustrix.cloud_providers.aws import AWSProvider

        provider = AWSProvider()
        result = provider.authenticate(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-west-2",
        )

        assert result is True
        assert provider.is_authenticated()
        assert provider.region == "us-west-2"

        # Check that clients were initialized
        assert provider.ec2_client is not None
        assert provider.eks_client is not None
        assert provider.iam_client is not None

    def test_authenticate_failure(self, mock_boto3):
        """Test authentication failure."""
        from clustrix.cloud_providers.aws import AWSProvider
        from botocore.exceptions import ClientError

        # Make get_user fail
        mock_boto3["iam"].get_user.side_effect = ClientError(
            {"Error": {"Code": "InvalidUserID.NotFound"}}, "GetUser"
        )

        provider = AWSProvider()
        result = provider.authenticate(
            access_key_id="bad_key", secret_access_key="bad_secret"
        )

        assert result is False
        assert not provider.is_authenticated()

    def test_create_ec2_instance(self, mock_boto3):
        """Test EC2 instance creation."""
        from clustrix.cloud_providers.aws import AWSProvider

        # Mock responses
        mock_boto3["ec2"].describe_images.return_value = {
            "Images": [
                {"ImageId": "ami-12345", "CreationDate": "2024-01-01T00:00:00.000Z"}
            ]
        }

        mock_boto3["ec2"].run_instances.return_value = {
            "Instances": [{"InstanceId": "i-1234567890", "State": {"Name": "pending"}}]
        }

        mock_boto3["ec2"].describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1234567890",
                            "PublicIpAddress": "1.2.3.4",
                            "PrivateIpAddress": "10.0.0.1",
                            "State": {"Name": "running"},
                        }
                    ]
                }
            ]
        }

        provider = AWSProvider()
        provider.authenticated = True
        provider.ec2_client = mock_boto3["ec2"]

        result = provider.create_ec2_instance(
            instance_name="test-instance", instance_type="t3.micro"
        )

        assert result["instance_id"] == "i-1234567890"
        assert result["public_ip"] == "1.2.3.4"
        assert result["instance_type"] == "t3.micro"

    def test_get_cluster_config_ec2(self, mock_boto3):
        """Test getting Clustrix config for EC2 instance."""
        from clustrix.cloud_providers.aws import AWSProvider

        mock_boto3["ec2"].describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1234567890",
                            "PublicIpAddress": "1.2.3.4",
                            "State": {"Name": "running"},
                        }
                    ]
                }
            ]
        }

        provider = AWSProvider()
        provider.authenticated = True
        provider.ec2_client = mock_boto3["ec2"]
        provider.region = "us-east-1"

        config = provider.get_cluster_config("i-1234567890", cluster_type="ec2")

        assert config["cluster_type"] == "ssh"
        assert config["cluster_host"] == "1.2.3.4"
        assert config["username"] == "ec2-user"
        assert config["cost_monitoring"] is True
        assert config["provider"] == "aws"

    def test_get_cluster_config_eks(self, mock_boto3):
        """Test getting Clustrix config for EKS cluster."""
        from clustrix.cloud_providers.aws import AWSProvider

        provider = AWSProvider()
        provider.authenticated = True
        provider.region = "us-west-2"

        config = provider.get_cluster_config("my-cluster", cluster_type="eks")

        assert config["cluster_type"] == "kubernetes"
        assert config["cluster_host"] == "my-cluster.eks.us-west-2.amazonaws.com"
        assert config["cluster_port"] == 443
        assert config["cost_monitoring"] is True
        assert config["provider"] == "aws"

    def test_estimate_cost_eks(self, mock_boto3):
        """Test cost estimation for EKS."""
        from clustrix.cloud_providers.aws import AWSProvider

        provider = AWSProvider()
        costs = provider.estimate_cost(
            cluster_type="eks", instance_type="t3.medium", node_count=3, hours=24
        )

        assert "control_plane" in costs
        assert "nodes" in costs
        assert "total" in costs
        assert costs["control_plane"] == 0.10 * 24  # $0.10/hour * 24 hours
        assert costs["nodes"] == 0.0416 * 3 * 24  # t3.medium price * 3 nodes * 24 hours

    def test_estimate_cost_ec2(self, mock_boto3):
        """Test cost estimation for EC2."""
        from clustrix.cloud_providers.aws import AWSProvider

        provider = AWSProvider()
        costs = provider.estimate_cost(
            cluster_type="ec2", instance_type="t3.large", hours=8
        )

        assert "instance" in costs
        assert "total" in costs
        assert costs["total"] == 0.0832 * 8  # t3.large price * 8 hours
