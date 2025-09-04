"""
Comprehensive tests for cloud provider notebook magic integrations.

This module tests AWS, Azure, and GCP configuration widgets and API connectivity
for notebook magic functionality in Clustrix.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock, call
from typing import Dict, Any, List

# Import modules under test
from clustrix.notebook_magic_aws import (
    configure_aws_credentials,
    validate_aws_connection,
    list_aws_resources,
    get_aws_regions,
    get_aws_instance_types,
    create_aws_config_widget,
)

from clustrix.notebook_magic_azure import (
    configure_azure_auth,
    validate_azure_connection,
    list_azure_resources,
    get_azure_regions,
    get_azure_vm_sizes,
    create_azure_config_widget,
)

from clustrix.notebook_magic_gcp import (
    configure_gcp_credentials,
    validate_gcp_connection,
    list_gcp_resources,
    get_gcp_regions,
    get_gcp_machine_types,
    create_gcp_config_widget,
)


class TestAWSNotebookMagic:
    """Test AWS notebook magic integration."""

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.notebook_magic_aws.boto3")
    def test_configure_aws_credentials_success(self, mock_boto3):
        """Test successful AWS credential configuration."""
        # Mock AWS provider
        mock_provider = Mock()
        mock_provider.authenticate.return_value = True

        with patch(
            "clustrix.notebook_magic_aws.AWSProvider", return_value=mock_provider
        ):
            result = configure_aws_credentials(
                access_key_id="test_key",
                secret_access_key="test_secret",
                region="us-west-2",
            )

        assert result["success"] is True
        assert result["credentials_valid"] is True
        assert result["region"] == "us-west-2"
        assert result["provider"] == "aws"
        mock_provider.authenticate.assert_called_once_with(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-west-2",
        )

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", False)
    def test_configure_aws_credentials_boto3_not_available(self):
        """Test AWS credential configuration when boto3 is not available."""
        result = configure_aws_credentials(
            access_key_id="test_key", secret_access_key="test_secret"
        )

        assert result["success"] is False
        assert "boto3 is not installed" in result["error"]
        assert result["credentials_valid"] is False

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", True)
    def test_configure_aws_credentials_auth_failure(self):
        """Test AWS credential configuration authentication failure."""
        mock_provider = Mock()
        mock_provider.authenticate.return_value = False

        with patch(
            "clustrix.notebook_magic_aws.AWSProvider", return_value=mock_provider
        ):
            result = configure_aws_credentials(
                access_key_id="invalid_key", secret_access_key="invalid_secret"
            )

        assert result["success"] is False
        assert result["credentials_valid"] is False
        assert "Failed to authenticate" in result["error"]

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.notebook_magic_aws.boto3")
    def test_validate_aws_connection_success(self, mock_boto3):
        """Test successful AWS connection validation."""
        # Mock session and clients
        mock_session = Mock()
        mock_sts_client = Mock()
        mock_ec2_client = Mock()

        mock_boto3.Session.return_value = mock_session
        mock_session.client.side_effect = lambda service: {
            "sts": mock_sts_client,
            "ec2": mock_ec2_client,
        }[service]

        # Mock STS response
        mock_sts_client.get_caller_identity.return_value = {
            "Account": "123456789012",
            "UserId": "AIDACKCEVSQ6C2EXAMPLE",
            "Arn": "arn:aws:iam::123456789012:user/test",
        }

        # Mock EC2 response
        mock_ec2_client.describe_instance_type_offerings.return_value = {
            "InstanceTypeOfferings": [
                {"InstanceType": "t3.micro"},
                {"InstanceType": "t3.small"},
            ]
        }

        result = validate_aws_connection(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-east-1",
        )

        assert result["valid"] is True
        assert result["details"]["account_id"] == "123456789012"
        assert result["details"]["region"] == "us-east-1"
        assert result["details"]["available_instance_types"] == 2

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.notebook_magic_aws.boto3")
    def test_validate_aws_connection_credentials_error(self, mock_boto3):
        """Test AWS connection validation with invalid credentials."""
        from clustrix.notebook_magic_aws import NoCredentialsError

        mock_session = Mock()
        mock_sts_client = Mock()

        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = mock_sts_client
        mock_sts_client.get_caller_identity.side_effect = NoCredentialsError()

        result = validate_aws_connection(
            access_key_id="invalid_key", secret_access_key="invalid_secret"
        )

        assert result["valid"] is False
        assert "Invalid or missing AWS credentials" in result["error"]

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.notebook_magic_aws.boto3")
    def test_list_aws_resources_success(self, mock_boto3):
        """Test successful AWS resource listing."""
        mock_session = Mock()
        mock_ec2_client = Mock()
        mock_eks_client = Mock()

        mock_boto3.Session.return_value = mock_session
        mock_session.client.side_effect = lambda service: {
            "ec2": mock_ec2_client,
            "eks": mock_eks_client,
        }[service]

        # Mock EC2 instances response
        mock_ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1234567890abcdef0",
                            "State": {"Name": "running"},
                            "InstanceType": "t3.medium",
                            "PublicIpAddress": "203.0.113.12",
                            "PrivateIpAddress": "10.0.0.4",
                        }
                    ]
                }
            ]
        }

        # Mock VPCs response
        mock_ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-12345678",
                    "CidrBlock": "10.0.0.0/16",
                    "State": "available",
                    "IsDefault": True,
                }
            ]
        }

        # Mock EKS clusters response
        mock_eks_client.list_clusters.return_value = {"clusters": ["test-cluster"]}
        mock_eks_client.describe_cluster.return_value = {
            "cluster": {
                "status": "ACTIVE",
                "version": "1.21",
                "endpoint": "https://test.yl4.us-west-2.eks.amazonaws.com",
            }
        }

        result = list_aws_resources(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-west-2",
        )

        assert result["success"] is True
        assert result["region"] == "us-west-2"
        assert len(result["resources"]["ec2_instances"]) == 1
        assert (
            result["resources"]["ec2_instances"][0]["instance_id"]
            == "i-1234567890abcdef0"
        )
        assert len(result["resources"]["eks_clusters"]) == 1
        assert result["resources"]["eks_clusters"][0]["name"] == "test-cluster"

    def test_get_aws_regions(self):
        """Test getting AWS regions list."""
        regions = get_aws_regions()
        assert isinstance(regions, list)
        assert "us-east-1" in regions
        assert "us-west-2" in regions
        assert "eu-west-1" in regions

    def test_get_aws_instance_types(self):
        """Test getting AWS instance types."""
        instance_types = get_aws_instance_types()
        assert isinstance(instance_types, list)
        assert "t3.micro" in instance_types
        assert "t3.medium" in instance_types

    @patch("clustrix.notebook_magic_aws.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_aws.widgets")
    def test_create_aws_config_widget(self, mock_widgets):
        """Test AWS configuration widget creation."""
        mock_widgets.VBox = Mock()
        mock_widgets.Text = Mock()
        mock_widgets.Password = Mock()
        mock_widgets.Dropdown = Mock()
        mock_widgets.Button = Mock()
        mock_widgets.Output = Mock()
        mock_widgets.HTML = Mock()
        mock_widgets.Layout = Mock()

        widget = create_aws_config_widget()

        mock_widgets.VBox.assert_called_once()
        assert mock_widgets.Text.call_count >= 1  # Access key widget
        assert mock_widgets.Password.call_count >= 1  # Secret key widget
        assert mock_widgets.Dropdown.call_count >= 1  # Region widget


class TestAzureNotebookMagic:
    """Test Azure notebook magic integration."""

    @patch("clustrix.notebook_magic_azure.AZURE_AVAILABLE", True)
    def test_configure_azure_auth_success(self):
        """Test successful Azure authentication configuration."""
        mock_provider = Mock()
        mock_provider.authenticate.return_value = True

        with patch(
            "clustrix.notebook_magic_azure.AzureProvider", return_value=mock_provider
        ):
            result = configure_azure_auth(
                subscription_id="test-subscription",
                client_id="test-client",
                client_secret="test-secret",
                tenant_id="test-tenant",
                region="eastus",
            )

        assert result["success"] is True
        assert result["authenticated"] is True
        assert result["region"] == "eastus"
        assert result["provider"] == "azure"

    @patch("clustrix.notebook_magic_azure.AZURE_AVAILABLE", False)
    def test_configure_azure_auth_sdk_not_available(self):
        """Test Azure authentication configuration when SDK is not available."""
        result = configure_azure_auth(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
        )

        assert result["success"] is False
        assert "Azure SDK is not installed" in result["error"]
        assert result["authenticated"] is False

    @patch("clustrix.notebook_magic_azure.AZURE_AVAILABLE", True)
    @patch("clustrix.notebook_magic_azure.ClientSecretCredential")
    @patch("clustrix.notebook_magic_azure.ResourceManagementClient")
    @patch("clustrix.notebook_magic_azure.ComputeManagementClient")
    def test_validate_azure_connection_success(
        self,
        mock_compute_client_class,
        mock_resource_client_class,
        mock_credential_class,
    ):
        """Test successful Azure connection validation."""
        # Mock credential
        mock_credential = Mock()
        mock_credential_class.return_value = mock_credential

        # Mock resource client
        mock_resource_client = Mock()
        mock_resource_client_class.return_value = mock_resource_client
        mock_resource_client.resource_groups.list.return_value = [
            Mock(),
            Mock(),
        ]  # 2 resource groups

        # Mock compute client
        mock_compute_client = Mock()
        mock_compute_client_class.return_value = mock_compute_client
        mock_compute_client.virtual_machine_sizes.list.return_value = [
            Mock() for _ in range(5)
        ]  # 5 VM sizes

        result = validate_azure_connection(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
            region="eastus",
        )

        assert result["valid"] is True
        assert result["details"]["subscription_id"] == "test-subscription"
        assert result["details"]["region"] == "eastus"
        assert result["details"]["resource_groups_count"] == 2
        assert result["details"]["available_vm_sizes"] == 5

    @patch("clustrix.notebook_magic_azure.AZURE_AVAILABLE", True)
    @patch("clustrix.notebook_magic_azure.ClientSecretCredential")
    @patch("clustrix.notebook_magic_azure.ResourceManagementClient")
    def test_validate_azure_connection_auth_error(
        self, mock_resource_client_class, mock_credential_class
    ):
        """Test Azure connection validation with authentication error."""
        from clustrix.notebook_magic_azure import ClientAuthenticationError

        mock_credential = Mock()
        mock_credential_class.return_value = mock_credential

        mock_resource_client = Mock()
        mock_resource_client_class.return_value = mock_resource_client
        mock_resource_client.resource_groups.list.side_effect = (
            ClientAuthenticationError("Invalid credentials")
        )

        result = validate_azure_connection(
            subscription_id="test-subscription",
            client_id="invalid-client",
            client_secret="invalid-secret",
            tenant_id="test-tenant",
        )

        assert result["valid"] is False
        assert "Azure authentication failed" in result["error"]

    @patch("clustrix.notebook_magic_azure.AZURE_AVAILABLE", True)
    @patch("clustrix.notebook_magic_azure.ClientSecretCredential")
    @patch("clustrix.notebook_magic_azure.ComputeManagementClient")
    @patch("clustrix.notebook_magic_azure.ContainerServiceClient")
    @patch("clustrix.notebook_magic_azure.ResourceManagementClient")
    def test_list_azure_resources_success(
        self,
        mock_resource_client_class,
        mock_container_client_class,
        mock_compute_client_class,
        mock_credential_class,
    ):
        """Test successful Azure resource listing."""
        # Mock credential
        mock_credential = Mock()
        mock_credential_class.return_value = mock_credential

        # Mock compute client and VMs
        mock_compute_client = Mock()
        mock_compute_client_class.return_value = mock_compute_client

        mock_vm = Mock()
        mock_vm.name = "test-vm"
        mock_vm.location = "eastus"
        mock_vm.hardware_profile.vm_size = "Standard_D2s_v3"
        mock_vm.provisioning_state = "Succeeded"
        mock_vm.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/test-vm"

        mock_compute_client.virtual_machines.list_all.return_value = [mock_vm]

        # Mock container client and AKS clusters
        mock_container_client = Mock()
        mock_container_client_class.return_value = mock_container_client

        mock_cluster = Mock()
        mock_cluster.name = "test-cluster"
        mock_cluster.location = "eastus"
        mock_cluster.kubernetes_version = "1.21.2"
        mock_cluster.provisioning_state = "Succeeded"
        mock_cluster.agent_pool_profiles = [Mock(), Mock()]  # 2 agent pools
        mock_cluster.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ContainerService/managedClusters/test-cluster"

        mock_container_client.managed_clusters.list.return_value = [mock_cluster]

        # Mock resource client
        mock_resource_client = Mock()
        mock_resource_client_class.return_value = mock_resource_client

        mock_rg = Mock()
        mock_rg.name = "test-rg"
        mock_rg.location = "eastus"
        mock_rg.properties.provisioning_state = "Succeeded"

        mock_resource_client.resource_groups.list.return_value = [mock_rg]

        result = list_azure_resources(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
            region="eastus",
        )

        assert result["success"] is True
        assert result["subscription_id"] == "test-subscription"
        assert result["region"] == "eastus"
        assert len(result["resources"]["virtual_machines"]) == 1
        assert result["resources"]["virtual_machines"][0]["name"] == "test-vm"
        assert len(result["resources"]["aks_clusters"]) == 1
        assert result["resources"]["aks_clusters"][0]["name"] == "test-cluster"
        assert len(result["resources"]["resource_groups"]) == 1
        assert result["resources"]["resource_groups"][0]["name"] == "test-rg"

    def test_get_azure_regions(self):
        """Test getting Azure regions list."""
        regions = get_azure_regions()
        assert isinstance(regions, list)
        assert "eastus" in regions
        assert "westus" in regions
        assert "westeurope" in regions

    def test_get_azure_vm_sizes(self):
        """Test getting Azure VM sizes."""
        vm_sizes = get_azure_vm_sizes()
        assert isinstance(vm_sizes, list)
        assert "Standard_B1s" in vm_sizes
        assert "Standard_D2s_v3" in vm_sizes

    @patch("clustrix.notebook_magic_azure.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_azure.widgets")
    def test_create_azure_config_widget(self, mock_widgets):
        """Test Azure configuration widget creation."""
        mock_widgets.VBox = Mock()
        mock_widgets.Text = Mock()
        mock_widgets.Password = Mock()
        mock_widgets.Dropdown = Mock()
        mock_widgets.Button = Mock()
        mock_widgets.Output = Mock()
        mock_widgets.HTML = Mock()
        mock_widgets.Layout = Mock()

        widget = create_azure_config_widget()

        mock_widgets.VBox.assert_called_once()
        assert mock_widgets.Text.call_count >= 3  # Multiple text widgets
        assert mock_widgets.Password.call_count >= 1  # Client secret widget


class TestGCPNotebookMagic:
    """Test GCP notebook magic integration."""

    @patch("clustrix.notebook_magic_gcp.GCP_AVAILABLE", True)
    def test_configure_gcp_credentials_success(self):
        """Test successful GCP credentials configuration."""
        mock_provider = Mock()
        mock_provider.authenticate.return_value = True

        with patch(
            "clustrix.notebook_magic_gcp.GCPProvider", return_value=mock_provider
        ):
            result = configure_gcp_credentials(
                project_id="test-project",
                service_account_key='{"type": "service_account", "client_email": "test@test.com"}',
                region="us-central1",
            )

        assert result["success"] is True
        assert result["credentials_valid"] is True
        assert result["region"] == "us-central1"
        assert result["project_id"] == "test-project"
        assert result["provider"] == "gcp"

    @patch("clustrix.notebook_magic_gcp.GCP_AVAILABLE", False)
    def test_configure_gcp_credentials_sdk_not_available(self):
        """Test GCP credentials configuration when SDK is not available."""
        result = configure_gcp_credentials(
            project_id="test-project", service_account_key='{"type": "service_account"}'
        )

        assert result["success"] is False
        assert "Google Cloud SDK is not installed" in result["error"]
        assert result["credentials_valid"] is False

    @patch("clustrix.notebook_magic_gcp.GCP_AVAILABLE", True)
    @patch("clustrix.notebook_magic_gcp.service_account")
    @patch("clustrix.notebook_magic_gcp.compute_v1")
    @patch("clustrix.notebook_magic_gcp.container_v1")
    def test_validate_gcp_connection_success(
        self, mock_container_v1, mock_compute_v1, mock_service_account
    ):
        """Test successful GCP connection validation."""
        # Mock service account credentials
        mock_creds = Mock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_creds
        )

        # Mock compute client
        mock_compute_client = Mock()
        mock_compute_v1.InstancesClient.return_value = mock_compute_client
        mock_compute_client.list.return_value = [Mock(), Mock()]  # 2 instances

        # Mock container client
        mock_container_client = Mock()
        mock_container_v1.ClusterManagerClient.return_value = mock_container_client
        mock_clusters_response = Mock()
        mock_clusters_response.clusters = [Mock()]  # 1 cluster
        mock_container_client.list_clusters.return_value = mock_clusters_response

        service_account_key = json.dumps(
            {
                "type": "service_account",
                "client_email": "test@test-project.iam.gserviceaccount.com",
            }
        )

        result = validate_gcp_connection(
            project_id="test-project",
            service_account_key=service_account_key,
            region="us-central1",
        )

        assert result["valid"] is True
        assert result["details"]["project_id"] == "test-project"
        assert result["details"]["region"] == "us-central1"
        assert result["details"]["compute_instances"] == 2
        assert result["details"]["gke_clusters"] == 1

    @patch("clustrix.notebook_magic_gcp.GCP_AVAILABLE", True)
    def test_validate_gcp_connection_invalid_json(self):
        """Test GCP connection validation with invalid JSON."""
        result = validate_gcp_connection(
            project_id="test-project",
            service_account_key="invalid json",
            region="us-central1",
        )

        assert result["valid"] is False
        assert "Invalid service account key JSON format" in result["error"]

    @patch("clustrix.notebook_magic_gcp.GCP_AVAILABLE", True)
    @patch("clustrix.notebook_magic_gcp.service_account")
    @patch("clustrix.notebook_magic_gcp.compute_v1")
    @patch("clustrix.notebook_magic_gcp.container_v1")
    def test_list_gcp_resources_success(
        self, mock_container_v1, mock_compute_v1, mock_service_account
    ):
        """Test successful GCP resource listing."""
        # Mock service account credentials
        mock_creds = Mock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_creds
        )

        # Mock compute instances
        mock_instances_client = Mock()
        mock_zones_client = Mock()

        mock_compute_v1.InstancesClient.return_value = mock_instances_client
        mock_compute_v1.ZonesClient.return_value = mock_zones_client

        # Mock zone response
        mock_zone = Mock()
        mock_zone.name = "us-central1-a"
        mock_zone.region = "projects/test/regions/us-central1"
        mock_zones_client.list.return_value = [mock_zone]

        # Mock instance response
        mock_instance = Mock()
        mock_instance.name = "test-instance"
        mock_instance.machine_type = (
            "projects/test/zones/us-central1-a/machineTypes/e2-medium"
        )
        mock_instance.status = "RUNNING"
        mock_instance.network_interfaces = [Mock()]
        mock_instance.network_interfaces[0].network_i_p = "10.0.0.1"
        mock_instance.network_interfaces[0].access_configs = [Mock()]
        mock_instance.network_interfaces[0].access_configs[0].nat_i_p = "203.0.113.1"

        mock_instances_client.list.return_value = [mock_instance]

        # Mock GKE clusters
        mock_container_client = Mock()
        mock_container_v1.ClusterManagerClient.return_value = mock_container_client

        mock_cluster = Mock()
        mock_cluster.name = "test-cluster"
        mock_cluster.location = "us-central1"
        mock_cluster.status.name = "RUNNING"
        mock_cluster.current_master_version = "1.21.3"
        mock_cluster.node_pools = [Mock(), Mock()]  # 2 node pools
        mock_cluster.node_pools[0].initial_node_count = 2
        mock_cluster.node_pools[1].initial_node_count = 3
        mock_cluster.endpoint = "203.0.113.100"

        mock_clusters_response = Mock()
        mock_clusters_response.clusters = [mock_cluster]
        mock_container_client.list_clusters.return_value = mock_clusters_response

        service_account_key = json.dumps(
            {
                "type": "service_account",
                "client_email": "test@test-project.iam.gserviceaccount.com",
            }
        )

        result = list_gcp_resources(
            project_id="test-project",
            service_account_key=service_account_key,
            region="us-central1",
        )

        assert result["success"] is True
        assert result["project_id"] == "test-project"
        assert result["region"] == "us-central1"
        assert len(result["resources"]["compute_instances"]) == 1
        assert result["resources"]["compute_instances"][0]["name"] == "test-instance"
        assert len(result["resources"]["gke_clusters"]) == 1
        assert result["resources"]["gke_clusters"][0]["name"] == "test-cluster"
        assert result["resources"]["gke_clusters"][0]["node_count"] == 5  # 2 + 3

    def test_get_gcp_regions(self):
        """Test getting GCP regions list."""
        regions = get_gcp_regions()
        assert isinstance(regions, list)
        assert "us-central1" in regions
        assert "us-east1" in regions
        assert "europe-west1" in regions

    def test_get_gcp_machine_types(self):
        """Test getting GCP machine types."""
        machine_types = get_gcp_machine_types()
        assert isinstance(machine_types, list)
        assert "e2-micro" in machine_types
        assert "n1-standard-1" in machine_types

    @patch("clustrix.notebook_magic_gcp.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_gcp.widgets")
    def test_create_gcp_config_widget(self, mock_widgets):
        """Test GCP configuration widget creation."""
        mock_widgets.VBox = Mock()
        mock_widgets.Text = Mock()
        mock_widgets.Textarea = Mock()
        mock_widgets.Dropdown = Mock()
        mock_widgets.Button = Mock()
        mock_widgets.Output = Mock()
        mock_widgets.HTML = Mock()
        mock_widgets.Layout = Mock()
        mock_widgets.FileUpload = Mock()

        widget = create_gcp_config_widget()

        mock_widgets.VBox.assert_called_once()
        assert mock_widgets.Text.call_count >= 1  # Project ID widget
        assert mock_widgets.Textarea.call_count >= 1  # Service account widget
        assert mock_widgets.FileUpload.call_count >= 1  # File upload widget


class TestCloudProviderIntegration:
    """Integration tests for cloud provider functionality."""

    @patch("clustrix.notebook_magic_aws.BOTO3_AVAILABLE", False)
    @patch("clustrix.notebook_magic_azure.AZURE_AVAILABLE", False)
    @patch("clustrix.notebook_magic_gcp.GCP_AVAILABLE", False)
    def test_error_handling_consistency(self):
        """Test that all providers handle errors consistently."""
        # Test with SDK not available (consistent failure scenario)
        aws_result = configure_aws_credentials()
        azure_result = configure_azure_auth()
        gcp_result = configure_gcp_credentials()

        # All should fail gracefully with missing SDKs
        assert aws_result["success"] is False
        assert azure_result["success"] is False
        assert gcp_result["success"] is False

        # All should have consistent error structure
        for result in [aws_result, azure_result, gcp_result]:
            assert "error" in result
            assert isinstance(result["error"], str)
            assert len(result["error"]) > 0

    def test_region_lists_not_empty(self):
        """Test that all providers return non-empty region lists."""
        aws_regions = get_aws_regions()
        azure_regions = get_azure_regions()
        gcp_regions = get_gcp_regions()

        assert len(aws_regions) > 0
        assert len(azure_regions) > 0
        assert len(gcp_regions) > 0

        # Check common regions exist
        assert "us-east-1" in aws_regions
        assert "eastus" in azure_regions
        assert "us-central1" in gcp_regions

    def test_instance_type_lists_not_empty(self):
        """Test that all providers return non-empty instance/VM type lists."""
        aws_types = get_aws_instance_types()
        azure_types = get_azure_vm_sizes()
        gcp_types = get_gcp_machine_types()

        assert len(aws_types) > 0
        assert len(azure_types) > 0
        assert len(gcp_types) > 0

        # Check that they contain expected instance types
        assert any("t3." in t for t in aws_types)
        assert any("Standard_" in t for t in azure_types)
        assert any("e2-" in t or "n1-" in t for t in gcp_types)

    @patch("clustrix.notebook_magic_aws.IPYTHON_AVAILABLE", False)
    @patch("clustrix.notebook_magic_azure.IPYTHON_AVAILABLE", False)
    @patch("clustrix.notebook_magic_gcp.IPYTHON_AVAILABLE", False)
    def test_widget_creation_without_ipython(self):
        """Test that widget creation fails gracefully without IPython."""
        with pytest.raises(ImportError, match="IPython and ipywidgets are required"):
            create_aws_config_widget()

        with pytest.raises(ImportError, match="IPython and ipywidgets are required"):
            create_azure_config_widget()

        with pytest.raises(ImportError, match="IPython and ipywidgets are required"):
            create_gcp_config_widget()


if __name__ == "__main__":
    pytest.main([__file__])
