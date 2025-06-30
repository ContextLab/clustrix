import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from clustrix.cloud_providers.azure import AzureProvider


class TestAzureProvider:
    """Test Azure provider functionality."""

    @pytest.fixture
    def provider(self):
        """Create AzureProvider instance."""
        return AzureProvider()

    @pytest.fixture
    def authenticated_provider(self):
        """Create authenticated AzureProvider instance."""
        provider = AzureProvider()
        provider.authenticated = True
        provider.subscription_id = "test-subscription-id"
        provider.client_id = "test-client-id"
        provider.tenant_id = "test-tenant-id"
        provider.region = "eastus"
        provider.resource_group = "test-rg"
        provider.compute_client = Mock()
        provider.resource_client = Mock()
        provider.network_client = Mock()
        provider.container_client = Mock()
        provider.credential = Mock()
        provider.credentials = {
            "subscription_id": "test-subscription-id",
            "client_id": "test-client-id",
            "client_secret": "test-secret",
            "tenant_id": "test-tenant-id",
        }
        return provider

    def test_initialization(self, provider):
        """Test provider initialization."""
        assert provider.subscription_id is None
        assert provider.client_id is None
        assert provider.tenant_id is None
        assert provider.region == "eastus"
        assert provider.resource_group == "clustrix-rg"
        assert provider.compute_client is None
        assert provider.resource_client is None
        assert provider.network_client is None
        assert provider.container_client is None
        assert provider.credential is None
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.azure.AZURE_AVAILABLE", True)
    @patch("clustrix.cloud_providers.azure.ClientSecretCredential")
    @patch("clustrix.cloud_providers.azure.ComputeManagementClient")
    @patch("clustrix.cloud_providers.azure.ResourceManagementClient")
    @patch("clustrix.cloud_providers.azure.NetworkManagementClient")
    @patch("clustrix.cloud_providers.azure.ContainerServiceClient")
    def test_authenticate_success(
        self,
        mock_container,
        mock_network,
        mock_resource,
        mock_compute,
        mock_credential,
        provider,
    ):
        """Test successful authentication."""
        # Mock credential
        mock_cred = Mock()
        mock_credential.return_value = mock_cred

        # Mock clients
        mock_compute_client = Mock()
        mock_resource_client = Mock()
        mock_network_client = Mock()
        mock_container_client = Mock()

        mock_compute.return_value = mock_compute_client
        mock_resource.return_value = mock_resource_client
        mock_network.return_value = mock_network_client
        mock_container.return_value = mock_container_client

        # Mock successful resource group list
        mock_resource_client.resource_groups.list.return_value = []

        result = provider.authenticate(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
            region="westus",
            resource_group="custom-rg",
        )

        assert result is True
        assert provider.authenticated is True
        assert provider.subscription_id == "test-subscription"
        assert provider.client_id == "test-client"
        assert provider.tenant_id == "test-tenant"
        assert provider.region == "westus"
        assert provider.resource_group == "custom-rg"

        # Verify clients were created
        mock_credential.assert_called_once_with(
            tenant_id="test-tenant",
            client_id="test-client",
            client_secret="test-secret",
        )

    @patch("clustrix.cloud_providers.azure.AZURE_AVAILABLE", False)
    def test_authenticate_azure_not_available(self, provider):
        """Test authentication when Azure packages not available."""
        result = provider.authenticate(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
        )

        assert result is False
        assert not provider.authenticated

    def test_authenticate_missing_credentials(self, provider):
        """Test authentication with missing credentials."""
        # Test missing subscription_id
        result = provider.authenticate(
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
        )
        assert result is False

        # Test missing client_id
        result = provider.authenticate(
            subscription_id="test-subscription",
            client_secret="test-secret",
            tenant_id="test-tenant",
        )
        assert result is False

        # Test missing client_secret
        result = provider.authenticate(
            subscription_id="test-subscription",
            client_id="test-client",
            tenant_id="test-tenant",
        )
        assert result is False

        # Test missing tenant_id
        result = provider.authenticate(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
        )
        assert result is False

    @patch("clustrix.cloud_providers.azure.AZURE_AVAILABLE", True)
    @patch("clustrix.cloud_providers.azure.ClientSecretCredential")
    def test_authenticate_credentials_error(self, mock_credential, provider):
        """Test authentication with credential error."""
        from clustrix.cloud_providers.azure import ClientAuthenticationError

        mock_credential.side_effect = ClientAuthenticationError("Invalid credentials")

        result = provider.authenticate(
            subscription_id="test-subscription",
            client_id="test-client",
            client_secret="test-secret",
            tenant_id="test-tenant",
        )

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.azure.AZURE_AVAILABLE", True)
    @patch("clustrix.cloud_providers.azure.ClientSecretCredential")
    @patch("clustrix.cloud_providers.azure.ResourceManagementClient")
    def test_authenticate_api_test_failure(
        self, mock_resource, mock_credential, provider
    ):
        """Test authentication when API test fails."""
        mock_cred = Mock()
        mock_credential.return_value = mock_cred

        mock_resource_client = Mock()
        mock_resource.return_value = mock_resource_client
        mock_resource_client.resource_groups.list.side_effect = Exception("API error")

        with patch("clustrix.cloud_providers.azure.ComputeManagementClient"), patch(
            "clustrix.cloud_providers.azure.NetworkManagementClient"
        ), patch("clustrix.cloud_providers.azure.ContainerServiceClient"):
            result = provider.authenticate(
                subscription_id="test-subscription",
                client_id="test-client",
                client_secret="test-secret",
                tenant_id="test-tenant",
            )

        assert result is False
        assert not provider.authenticated

    def test_validate_credentials_success(self, authenticated_provider):
        """Test successful credential validation."""
        authenticated_provider.resource_client.resource_groups.list.return_value = []

        result = authenticated_provider.validate_credentials()

        assert result is True

    def test_validate_credentials_failure(self, authenticated_provider):
        """Test failed credential validation."""
        authenticated_provider.resource_client.resource_groups.list.side_effect = (
            Exception("API error")
        )

        result = authenticated_provider.validate_credentials()

        assert result is False

    def test_validate_credentials_not_authenticated(self, provider):
        """Test credential validation when not authenticated."""
        result = provider.validate_credentials()

        assert result is False

    def test_ensure_resource_group_exists(self, authenticated_provider):
        """Test ensuring resource group when it already exists."""
        # Mock resource group exists
        mock_rg = Mock()
        authenticated_provider.resource_client.resource_groups.get.return_value = (
            mock_rg
        )

        result = authenticated_provider._ensure_resource_group()

        assert result is True
        authenticated_provider.resource_client.resource_groups.get.assert_called_once_with(
            "test-rg"
        )

    def test_ensure_resource_group_create(self, authenticated_provider):
        """Test creating resource group when it doesn't exist."""
        from clustrix.cloud_providers.azure import ResourceNotFoundError

        # Mock resource group doesn't exist
        authenticated_provider.resource_client.resource_groups.get.side_effect = (
            ResourceNotFoundError()
        )

        result = authenticated_provider._ensure_resource_group()

        assert result is True
        authenticated_provider.resource_client.resource_groups.create_or_update.assert_called_once_with(
            "test-rg", {"location": "eastus", "tags": {"created_by": "clustrix"}}
        )

    def test_ensure_resource_group_error(self, authenticated_provider):
        """Test resource group creation error."""
        authenticated_provider.resource_client.resource_groups.get.side_effect = (
            Exception("API error")
        )

        result = authenticated_provider._ensure_resource_group()

        assert result is False

    def test_create_vm_success(self, authenticated_provider):
        """Test successful VM creation."""
        # Mock resource group check
        with patch.object(
            authenticated_provider, "_ensure_resource_group", return_value=True
        ):
            # Mock network resources
            mock_vnet = Mock()
            mock_vnet.subnets = [Mock()]
            mock_vnet.subnets[0].id = (
                "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Network/virtualNetworks/test-vm-vnet/subnets/test-vm-subnet"
            )
            authenticated_provider.network_client.virtual_networks.begin_create_or_update.return_value.result.return_value = (
                mock_vnet
            )

            mock_public_ip = Mock()
            mock_public_ip.id = "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Network/publicIPAddresses/test-vm-ip"
            mock_public_ip.ip_address = "1.2.3.4"
            authenticated_provider.network_client.public_ip_addresses.begin_create_or_update.return_value.result.return_value = (
                mock_public_ip
            )

            mock_nsg = Mock()
            mock_nsg.id = "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Network/networkSecurityGroups/test-vm-nsg"
            authenticated_provider.network_client.network_security_groups.begin_create_or_update.return_value.result.return_value = (
                mock_nsg
            )

            mock_nic = Mock()
            mock_nic.id = "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Network/networkInterfaces/test-vm-nic"
            authenticated_provider.network_client.network_interfaces.begin_create_or_update.return_value.result.return_value = (
                mock_nic
            )

            # Mock VM creation
            mock_vm = Mock()
            mock_vm.id = "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/test-vm"
            authenticated_provider.compute_client.virtual_machines.begin_create_or_update.return_value.result.return_value = (
                mock_vm
            )

            with patch("clustrix.cloud_providers.azure.datetime") as mock_datetime:
                mock_datetime.now.return_value.isoformat.return_value = (
                    "2024-01-01T00:00:00+00:00"
                )
                mock_datetime.timezone = timezone

                result = authenticated_provider.create_vm(
                    vm_name="test-vm",
                    vm_size="Standard_D2s_v3",
                    admin_username="testuser",
                    admin_password="testpass123",
                )

        assert result["vm_name"] == "test-vm"
        assert result["vm_size"] == "Standard_D2s_v3"
        assert result["region"] == "eastus"
        assert result["resource_group"] == "test-rg"
        assert result["status"] == "creating"
        assert result["public_ip"] == "1.2.3.4"
        assert result["admin_username"] == "testuser"

    def test_create_vm_not_authenticated(self, provider):
        """Test VM creation when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_vm("test-vm")

    def test_create_vm_resource_group_failure(self, authenticated_provider):
        """Test VM creation when resource group creation fails."""
        with patch.object(
            authenticated_provider, "_ensure_resource_group", return_value=False
        ):
            with pytest.raises(
                RuntimeError, match="Failed to create or access resource group"
            ):
                authenticated_provider.create_vm("test-vm")

    def test_create_vm_exception(self, authenticated_provider):
        """Test VM creation with exception."""
        with patch.object(
            authenticated_provider, "_ensure_resource_group", return_value=True
        ):
            authenticated_provider.network_client.virtual_networks.begin_create_or_update.side_effect = Exception(
                "Network error"
            )

            with pytest.raises(Exception, match="Network error"):
                authenticated_provider.create_vm("test-vm")

    def test_create_aks_cluster_success(self, authenticated_provider):
        """Test successful AKS cluster creation."""
        mock_operation = Mock()
        mock_operation.__str__ = Mock(return_value="operation-aks-12345")
        authenticated_provider.container_client.managed_clusters.begin_create_or_update.return_value = (
            mock_operation
        )

        with patch("clustrix.cloud_providers.azure.datetime") as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = (
                "2024-01-01T00:00:00+00:00"
            )
            mock_datetime.timezone = timezone

            result = authenticated_provider.create_aks_cluster(
                cluster_name="test-cluster",
                node_count=5,
                node_vm_size="Standard_DS2_v2",
                kubernetes_version="1.25.0",
            )

        assert result["cluster_name"] == "test-cluster"
        assert result["status"] == "creating"
        assert result["region"] == "eastus"
        assert result["provider"] == "azure"
        assert result["cluster_type"] == "aks"
        assert result["resource_group"] == "test-rg"
        assert result["node_count"] == 5
        assert result["node_vm_size"] == "Standard_DS2_v2"
        assert result["kubernetes_version"] == "1.25.0"

    def test_create_aks_cluster_not_authenticated(self, provider):
        """Test AKS cluster creation when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_aks_cluster("test-cluster")

    def test_create_aks_cluster_exception(self, authenticated_provider):
        """Test AKS cluster creation with exception."""
        authenticated_provider.container_client.managed_clusters.begin_create_or_update.side_effect = Exception(
            "API error"
        )

        with pytest.raises(Exception, match="API error"):
            authenticated_provider.create_aks_cluster("test-cluster")

    def test_create_cluster_vm(self, authenticated_provider):
        """Test create_cluster with VM type."""
        with patch.object(authenticated_provider, "create_vm") as mock_create:
            mock_create.return_value = {"vm_id": "test-vm"}

            result = authenticated_provider.create_cluster(
                "test-cluster", cluster_type="vm", vm_size="Standard_D2s_v3"
            )

            mock_create.assert_called_once_with(
                "test-cluster", vm_size="Standard_D2s_v3"
            )
            assert result == {"vm_id": "test-vm"}

    def test_create_cluster_aks(self, authenticated_provider):
        """Test create_cluster with AKS type."""
        with patch.object(authenticated_provider, "create_aks_cluster") as mock_create:
            mock_create.return_value = {"cluster_name": "test-cluster"}

            result = authenticated_provider.create_cluster(
                "test-cluster", cluster_type="aks", node_count=3
            )

            mock_create.assert_called_once_with("test-cluster", node_count=3)
            assert result == {"cluster_name": "test-cluster"}

    def test_create_cluster_unknown_type(self, authenticated_provider):
        """Test create_cluster with unknown type."""
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.create_cluster(
                "test-cluster", cluster_type="unknown"
            )

    def test_delete_cluster_vm_success(self, authenticated_provider):
        """Test successful VM deletion."""
        # Mock successful VM deletion
        authenticated_provider.compute_client.virtual_machines.begin_delete.return_value.result.return_value = (
            None
        )

        # Mock successful associated resource deletion
        authenticated_provider.network_client.network_interfaces.begin_delete.return_value.result.return_value = (
            None
        )
        authenticated_provider.network_client.public_ip_addresses.begin_delete.return_value.result.return_value = (
            None
        )
        authenticated_provider.network_client.network_security_groups.begin_delete.return_value.result.return_value = (
            None
        )
        authenticated_provider.network_client.virtual_networks.begin_delete.return_value.result.return_value = (
            None
        )

        result = authenticated_provider.delete_cluster("test-vm", cluster_type="vm")

        assert result is True
        authenticated_provider.compute_client.virtual_machines.begin_delete.assert_called_once_with(
            "test-rg", "test-vm"
        )

    def test_delete_cluster_vm_associated_resources_fail(self, authenticated_provider):
        """Test VM deletion when associated resources fail to delete."""
        # Mock successful VM deletion
        authenticated_provider.compute_client.virtual_machines.begin_delete.return_value.result.return_value = (
            None
        )

        # Mock failure in associated resource deletion
        authenticated_provider.network_client.network_interfaces.begin_delete.side_effect = Exception(
            "Resource not found"
        )

        result = authenticated_provider.delete_cluster("test-vm", cluster_type="vm")

        # Should still return True even if associated resources fail
        assert result is True

    def test_delete_cluster_aks_success(self, authenticated_provider):
        """Test successful AKS cluster deletion."""
        mock_operation = Mock()
        authenticated_provider.container_client.managed_clusters.begin_delete.return_value = (
            mock_operation
        )

        result = authenticated_provider.delete_cluster(
            "test-cluster", cluster_type="aks"
        )

        assert result is True
        authenticated_provider.container_client.managed_clusters.begin_delete.assert_called_once_with(
            resource_group_name="test-rg", resource_name="test-cluster"
        )

    def test_delete_cluster_not_authenticated(self, provider):
        """Test cluster deletion when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.delete_cluster("test-cluster")

    def test_delete_cluster_unknown_type(self, authenticated_provider):
        """Test cluster deletion with unknown type."""
        result = authenticated_provider.delete_cluster(
            "test-cluster", cluster_type="unknown"
        )
        assert result is False

    def test_delete_cluster_exception(self, authenticated_provider):
        """Test cluster deletion with exception."""
        authenticated_provider.compute_client.virtual_machines.begin_delete.side_effect = Exception(
            "API error"
        )

        result = authenticated_provider.delete_cluster("test-vm", cluster_type="vm")

        assert result is False

    def test_get_cluster_status_vm_success(self, authenticated_provider):
        """Test successful VM status retrieval."""
        mock_vm = Mock()
        mock_vm.provisioning_state = "Succeeded"
        mock_vm.hardware_profile = Mock()
        mock_vm.hardware_profile.vm_size = "Standard_D2s_v3"
        mock_vm.location = "eastus"
        authenticated_provider.compute_client.virtual_machines.get.return_value = (
            mock_vm
        )

        result = authenticated_provider.get_cluster_status("test-vm", cluster_type="vm")

        assert result["vm_name"] == "test-vm"
        assert result["status"] == "succeeded"
        assert result["vm_size"] == "Standard_D2s_v3"
        assert result["region"] == "eastus"
        assert result["resource_group"] == "test-rg"
        assert result["provider"] == "azure"
        assert result["cluster_type"] == "vm"

    def test_get_cluster_status_vm_missing_fields(self, authenticated_provider):
        """Test VM status with missing fields."""
        mock_vm = Mock()
        mock_vm.provisioning_state = None
        mock_vm.hardware_profile = None
        mock_vm.location = "eastus"
        authenticated_provider.compute_client.virtual_machines.get.return_value = (
            mock_vm
        )

        result = authenticated_provider.get_cluster_status("test-vm", cluster_type="vm")

        assert result["status"] == "unknown"
        assert result["vm_size"] == "unknown"

    def test_get_cluster_status_aks_success(self, authenticated_provider):
        """Test successful AKS cluster status retrieval."""
        mock_cluster = Mock()
        mock_cluster.provisioning_state = "Succeeded"
        mock_cluster.kubernetes_version = "1.25.0"
        mock_cluster.agent_pool_profiles = [Mock()]
        mock_cluster.agent_pool_profiles[0].count = 3
        mock_cluster.fqdn = "test-cluster-123.hcp.eastus.azmk8s.io"
        mock_cluster.location = "eastus"
        authenticated_provider.container_client.managed_clusters.get.return_value = (
            mock_cluster
        )

        result = authenticated_provider.get_cluster_status(
            "test-cluster", cluster_type="aks"
        )

        assert result["cluster_name"] == "test-cluster"
        assert result["status"] == "succeeded"
        assert result["kubernetes_version"] == "1.25.0"
        assert result["node_count"] == 3
        assert result["fqdn"] == "test-cluster-123.hcp.eastus.azmk8s.io"
        assert result["region"] == "eastus"
        assert result["resource_group"] == "test-rg"
        assert result["provider"] == "azure"
        assert result["cluster_type"] == "aks"

    def test_get_cluster_status_aks_no_agent_pools(self, authenticated_provider):
        """Test AKS cluster status with no agent pools."""
        mock_cluster = Mock()
        mock_cluster.provisioning_state = "Succeeded"
        mock_cluster.kubernetes_version = "1.25.0"
        mock_cluster.agent_pool_profiles = []
        mock_cluster.fqdn = "test-cluster-123.hcp.eastus.azmk8s.io"
        mock_cluster.location = "eastus"
        authenticated_provider.container_client.managed_clusters.get.return_value = (
            mock_cluster
        )

        result = authenticated_provider.get_cluster_status(
            "test-cluster", cluster_type="aks"
        )

        assert result["node_count"] == 0

    def test_get_cluster_status_not_authenticated(self, provider):
        """Test cluster status when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_status("test-cluster")

    def test_get_cluster_status_unknown_type(self, authenticated_provider):
        """Test cluster status with unknown type."""
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.get_cluster_status(
                "test-cluster", cluster_type="unknown"
            )

    def test_get_cluster_status_exception(self, authenticated_provider):
        """Test cluster status with exception."""
        authenticated_provider.compute_client.virtual_machines.get.side_effect = (
            Exception("API error")
        )

        with pytest.raises(Exception, match="API error"):
            authenticated_provider.get_cluster_status("test-vm", cluster_type="vm")

    def test_list_clusters_success(self, authenticated_provider):
        """Test successful cluster listing."""
        # Mock VMs
        mock_vm = Mock()
        mock_vm.name = "clustrix-vm"
        mock_vm.id = "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/clustrix-vm"
        mock_vm.tags = {"created_by": "clustrix"}
        mock_vm.provisioning_state = "Succeeded"
        mock_vm.hardware_profile = Mock()
        mock_vm.hardware_profile.vm_size = "Standard_D2s_v3"
        mock_vm.location = "eastus"
        authenticated_provider.compute_client.virtual_machines.list.return_value = [
            mock_vm
        ]

        # Mock AKS clusters
        mock_cluster = Mock()
        mock_cluster.name = "clustrix-aks"
        mock_cluster.id = "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.ContainerService/managedClusters/clustrix-aks"
        mock_cluster.tags = {"created_by": "clustrix", "cluster_name": "test-cluster"}
        mock_cluster.provisioning_state = "Succeeded"
        mock_cluster.kubernetes_version = "1.25.0"
        mock_cluster.agent_pool_profiles = [Mock()]
        mock_cluster.agent_pool_profiles[0].count = 3
        mock_cluster.fqdn = "clustrix-aks-123.hcp.eastus.azmk8s.io"
        mock_cluster.location = "eastus"
        authenticated_provider.container_client.managed_clusters.list_by_resource_group.return_value = [
            mock_cluster
        ]

        result = authenticated_provider.list_clusters()

        assert len(result) == 2

        # Check VM
        vm_result = next(r for r in result if r["type"] == "vm")
        assert vm_result["name"] == "clustrix-vm"
        assert (
            vm_result["vm_id"]
            == "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/clustrix-vm"
        )
        assert vm_result["status"] == "succeeded"
        assert vm_result["vm_size"] == "Standard_D2s_v3"

        # Check AKS cluster
        aks_result = next(r for r in result if r["type"] == "aks")
        assert aks_result["name"] == "clustrix-aks"
        assert (
            aks_result["cluster_id"]
            == "/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.ContainerService/managedClusters/clustrix-aks"
        )
        assert aks_result["status"] == "succeeded"
        assert aks_result["kubernetes_version"] == "1.25.0"

    def test_list_clusters_no_clustrix_resources(self, authenticated_provider):
        """Test cluster listing with no Clustrix-managed resources."""
        # Mock VM without clustrix tag
        mock_vm = Mock()
        mock_vm.name = "other-vm"
        mock_vm.tags = {"created_by": "other"}
        authenticated_provider.compute_client.virtual_machines.list.return_value = [
            mock_vm
        ]

        # Mock AKS cluster without clustrix tag
        mock_cluster = Mock()
        mock_cluster.name = "other-aks"
        mock_cluster.tags = {"created_by": "other"}
        authenticated_provider.container_client.managed_clusters.list_by_resource_group.return_value = [
            mock_cluster
        ]

        result = authenticated_provider.list_clusters()

        assert len(result) == 0

    def test_list_clusters_not_authenticated(self, provider):
        """Test cluster listing when not authenticated."""
        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.list_clusters()

    def test_list_clusters_exceptions(self, authenticated_provider):
        """Test cluster listing with exceptions."""
        # Mock VM list exception
        authenticated_provider.compute_client.virtual_machines.list.side_effect = (
            Exception("VM API error")
        )

        # Mock AKS list exception
        authenticated_provider.container_client.managed_clusters.list_by_resource_group.side_effect = Exception(
            "AKS API error"
        )

        result = authenticated_provider.list_clusters()

        assert result == []

    def test_get_cluster_config_vm_success(self, authenticated_provider):
        """Test successful VM cluster config retrieval."""
        mock_vm = Mock()
        authenticated_provider.compute_client.virtual_machines.get.return_value = (
            mock_vm
        )

        mock_public_ip = Mock()
        mock_public_ip.ip_address = "1.2.3.4"
        authenticated_provider.network_client.public_ip_addresses.get.return_value = (
            mock_public_ip
        )

        result = authenticated_provider.get_cluster_config("test-vm", cluster_type="vm")

        assert result["name"] == "Azure VM - test-vm"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "1.2.3.4"
        assert result["username"] == "azureuser"
        assert result["cluster_port"] == 22
        assert result["default_cores"] == 2
        assert result["default_memory"] == "4GB"
        assert result["remote_work_dir"] == "/home/azureuser/clustrix"
        assert result["package_manager"] == "conda"
        assert result["cost_monitoring"] is True
        assert result["provider"] == "azure"
        assert result["provider_config"]["vm_name"] == "test-vm"
        assert result["provider_config"]["resource_group"] == "test-rg"

    def test_get_cluster_config_vm_no_public_ip(self, authenticated_provider):
        """Test VM cluster config with no public IP."""
        mock_vm = Mock()
        authenticated_provider.compute_client.virtual_machines.get.return_value = (
            mock_vm
        )
        authenticated_provider.network_client.public_ip_addresses.get.side_effect = (
            Exception("IP not found")
        )

        result = authenticated_provider.get_cluster_config("test-vm", cluster_type="vm")

        assert result["cluster_host"] == ""

    def test_get_cluster_config_vm_exception(self, authenticated_provider):
        """Test VM cluster config with exception."""
        authenticated_provider.compute_client.virtual_machines.get.side_effect = (
            Exception("VM not found")
        )

        result = authenticated_provider.get_cluster_config("test-vm", cluster_type="vm")

        # Should return basic config
        assert result["name"] == "Azure VM - test-vm"
        assert result["cluster_type"] == "ssh"
        assert result["cluster_host"] == "placeholder.azure.com"
        assert result["provider"] == "azure"

    def test_get_cluster_config_aks(self, authenticated_provider):
        """Test AKS cluster config retrieval."""
        result = authenticated_provider.get_cluster_config(
            "test-cluster", cluster_type="aks"
        )

        assert result["name"] == "Azure AKS - test-cluster"
        assert result["cluster_type"] == "kubernetes"
        assert result["cluster_host"] == "test-cluster.aks.eastus.azure.com"
        assert result["cluster_port"] == 443
        assert result["k8s_namespace"] == "default"
        assert result["k8s_image"] == "python:3.11"
        assert result["default_cores"] == 2
        assert result["default_memory"] == "4GB"
        assert result["cost_monitoring"] is True
        assert result["provider"] == "azure"
        assert result["provider_config"]["cluster_name"] == "test-cluster"
        assert result["provider_config"]["resource_group"] == "test-rg"

    def test_get_cluster_config_unknown_type(self, authenticated_provider):
        """Test cluster config with unknown type."""
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.get_cluster_config(
                "test-cluster", cluster_type="unknown"
            )

    def test_estimate_cost_vm(self, provider):
        """Test cost estimation for VM."""
        result = provider.estimate_cost(
            cluster_type="vm", vm_size="Standard_D2s_v3", hours=10
        )

        assert "vm" in result
        assert "total" in result
        assert result["vm"] == 0.096 * 10
        assert result["total"] == 0.096 * 10

    def test_estimate_cost_aks(self, provider):
        """Test cost estimation for AKS cluster."""
        result = provider.estimate_cost(
            cluster_type="aks", vm_size="Standard_D2s_v3", hours=5
        )

        cluster_fee = 0.0  # Free tier
        node_cost = 0.096 * 5
        total = cluster_fee + node_cost

        assert "cluster_management" in result
        assert "nodes" in result
        assert "total" in result
        assert result["cluster_management"] == cluster_fee
        assert result["nodes"] == node_cost
        assert result["total"] == total

    def test_estimate_cost_unknown_vm_size(self, provider):
        """Test cost estimation with unknown VM size."""
        result = provider.estimate_cost(vm_size="Unknown_Size", hours=2)

        assert result["vm"] == 0.10 * 2  # Default price
        assert result["total"] == 0.10 * 2

    def test_estimate_cost_defaults(self, provider):
        """Test cost estimation with default values."""
        result = provider.estimate_cost()

        assert result["vm"] == 0.096  # Standard_D2s_v3 for 1 hour
        assert result["total"] == 0.096

    def test_get_available_instance_types_not_authenticated(self, provider):
        """Test instance types when not authenticated."""
        result = provider.get_available_instance_types()

        # Should return default list
        assert "Standard_B1s" in result
        assert "Standard_D2s_v3" in result
        assert "Standard_E2s_v3" in result

    def test_get_available_instance_types_success(self, authenticated_provider):
        """Test successful instance types retrieval."""
        mock_size1 = Mock()
        mock_size1.name = "Standard_B1s"
        mock_size2 = Mock()
        mock_size2.name = "Standard_B2s"
        mock_size3 = Mock()
        mock_size3.name = "Standard_D2s_v3"
        mock_size4 = Mock()
        mock_size4.name = "Standard_D4s_v3"

        authenticated_provider.compute_client.virtual_machine_sizes.list.return_value = [
            mock_size1,
            mock_size2,
            mock_size3,
            mock_size4,
        ]

        result = authenticated_provider.get_available_instance_types()

        # Should contain the mocked VM sizes
        assert "Standard_B1s" in result
        assert "Standard_B2s" in result
        assert "Standard_D2s_v3" in result
        assert "Standard_D4s_v3" in result

    def test_get_available_instance_types_custom_region(self, authenticated_provider):
        """Test instance types retrieval for custom region."""
        authenticated_provider.compute_client.virtual_machine_sizes.list.return_value = (
            []
        )

        result = authenticated_provider.get_available_instance_types(region="westus")

        # Should query the correct region
        authenticated_provider.compute_client.virtual_machine_sizes.list.assert_called_once_with(
            "westus"
        )

    def test_get_available_instance_types_exception(self, authenticated_provider):
        """Test instance types retrieval with exception."""
        authenticated_provider.compute_client.virtual_machine_sizes.list.side_effect = (
            Exception("API error")
        )

        result = authenticated_provider.get_available_instance_types()

        # Should return default list
        assert "Standard_B1s" in result
        assert "Standard_D2s_v3" in result

    def test_get_available_regions_not_authenticated(self, provider):
        """Test regions when not authenticated."""
        result = provider.get_available_regions()

        assert "eastus" in result
        assert "westus2" in result
        assert "northeurope" in result

    def test_get_available_regions_success(self, authenticated_provider):
        """Test successful regions retrieval."""
        mock_location1 = Mock()
        mock_location1.name = "eastus"
        mock_location2 = Mock()
        mock_location2.name = "westus2"
        mock_location3 = Mock()
        mock_location3.name = "northeurope"
        mock_location4 = Mock()
        mock_location4.name = "southafricanorth"  # Not in priority list

        authenticated_provider.resource_client.subscriptions.list_locations.return_value = [
            mock_location1,
            mock_location2,
            mock_location3,
            mock_location4,
        ]

        result = authenticated_provider.get_available_regions()

        # Priority regions should come first
        assert result[0] == "eastus"
        assert result[1] == "westus2"
        assert result[2] == "northeurope"
        assert "southafricanorth" in result

    def test_get_available_regions_exception(self, authenticated_provider):
        """Test regions retrieval with exception."""
        authenticated_provider.resource_client.subscriptions.list_locations.side_effect = Exception(
            "API error"
        )

        result = authenticated_provider.get_available_regions()

        # Should return default list
        assert "eastus" in result
        assert "westus2" in result


class TestAzureProviderEdgeCases:
    """Test edge cases and error handling."""

    def test_vm_size_sorting_edge_cases(self):
        """Test VM size sorting with edge cases."""
        provider = AzureProvider()
        provider.authenticated = True
        provider.subscription_id = "test-subscription"
        provider.compute_client = Mock()

        # Mock VM sizes with various formats
        mock_sizes = []
        for name in [
            "Standard_B1s",
            "Standard_B2s",
            "Standard_D2s_v3",
            "Standard_InvalidName",
        ]:
            mock_size = Mock()
            mock_size.name = name
            mock_sizes.append(mock_size)

        provider.compute_client.virtual_machine_sizes.list.return_value = mock_sizes

        result = provider.get_available_instance_types()

        # Should handle various formats without error
        assert len(result) > 0

    def test_list_clusters_missing_attributes(self):
        """Test list_clusters with resources missing attributes."""
        provider = AzureProvider()
        provider.authenticated = True
        provider.resource_group = "test-rg"
        provider.compute_client = Mock()
        provider.container_client = Mock()

        # Mock VM without tags
        mock_vm = Mock()
        mock_vm.name = "test-vm"
        mock_vm.tags = None  # No tags
        provider.compute_client.virtual_machines.list.return_value = [mock_vm]

        # Mock AKS cluster without tags
        mock_cluster = Mock()
        mock_cluster.name = "test-cluster"
        mock_cluster.tags = None
        provider.container_client.managed_clusters.list_by_resource_group.return_value = [
            mock_cluster
        ]

        result = provider.list_clusters()

        # Should handle missing tags gracefully
        assert result == []

    def test_vm_creation_without_password(self):
        """Test VM creation without password (SSH key mode)."""
        provider = AzureProvider()
        provider.authenticated = True
        provider.resource_group = "test-rg"
        provider.region = "eastus"
        provider.compute_client = Mock()
        provider.network_client = Mock()

        with patch.object(provider, "_ensure_resource_group", return_value=True):
            # Setup mock network resources
            mock_vnet = Mock()
            mock_vnet.subnets = [Mock()]
            mock_vnet.subnets[0].id = "subnet-id"
            provider.network_client.virtual_networks.begin_create_or_update.return_value.result.return_value = (
                mock_vnet
            )

            mock_public_ip = Mock()
            mock_public_ip.id = "ip-id"
            mock_public_ip.ip_address = "1.2.3.4"
            provider.network_client.public_ip_addresses.begin_create_or_update.return_value.result.return_value = (
                mock_public_ip
            )

            mock_nsg = Mock()
            mock_nsg.id = "nsg-id"
            provider.network_client.network_security_groups.begin_create_or_update.return_value.result.return_value = (
                mock_nsg
            )

            mock_nic = Mock()
            mock_nic.id = "nic-id"
            provider.network_client.network_interfaces.begin_create_or_update.return_value.result.return_value = (
                mock_nic
            )

            mock_vm = Mock()
            mock_vm.id = "vm-id"
            provider.compute_client.virtual_machines.begin_create_or_update.return_value.result.return_value = (
                mock_vm
            )

            result = provider.create_vm(
                vm_name="test-vm",
                admin_username="testuser",
                # No admin_password provided
            )

            # Should succeed and disable password authentication
            assert result["vm_name"] == "test-vm"

    def test_get_cluster_config_public_ip_none(self):
        """Test cluster config when public IP is None."""
        provider = AzureProvider()
        provider.authenticated = True
        provider.resource_group = "test-rg"
        provider.compute_client = Mock()
        provider.network_client = Mock()

        mock_vm = Mock()
        provider.compute_client.virtual_machines.get.return_value = mock_vm

        mock_public_ip = Mock()
        mock_public_ip.ip_address = None  # No IP address
        provider.network_client.public_ip_addresses.get.return_value = mock_public_ip

        result = provider.get_cluster_config("test-vm", cluster_type="vm")

        assert result["cluster_host"] == ""
