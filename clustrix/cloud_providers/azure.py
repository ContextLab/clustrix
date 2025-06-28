"""Azure cloud provider integration for Clustrix."""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

try:
    from azure.identity import ClientSecretCredential
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.network import NetworkManagementClient
    from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    ClientSecretCredential = None
    ComputeManagementClient = None
    ResourceManagementClient = None
    NetworkManagementClient = None
    ClientAuthenticationError = Exception
    ResourceNotFoundError = Exception

from .base import CloudProvider
from . import PROVIDERS

logger = logging.getLogger(__name__)


class AzureProvider(CloudProvider):
    """Azure cloud provider implementation."""

    def __init__(self):
        """Initialize Azure provider."""
        super().__init__()
        self.subscription_id = None
        self.client_id = None
        self.tenant_id = None
        self.region = "eastus"
        self.resource_group = "clustrix-rg"
        self.compute_client = None
        self.resource_client = None
        self.network_client = None
        self.credential = None

    def authenticate(self, **credentials) -> bool:
        """
        Authenticate with Azure.

        Args:
            **credentials: Azure credentials including:
                - subscription_id: Azure subscription ID
                - client_id: Azure service principal client ID
                - client_secret: Azure service principal secret
                - tenant_id: Azure tenant ID
                - region: Azure region (default: eastus)
                - resource_group: Resource group name (default: clustrix-rg)

        Returns:
            bool: True if authentication successful
        """
        subscription_id = credentials.get("subscription_id")
        client_id = credentials.get("client_id")
        client_secret = credentials.get("client_secret")
        tenant_id = credentials.get("tenant_id")
        region = credentials.get("region", "eastus")
        resource_group = credentials.get("resource_group", "clustrix-rg")

        if not all([subscription_id, client_id, client_secret, tenant_id]):
            logger.error(
                "subscription_id, client_id, client_secret, and tenant_id are required"
            )
            return False

        if not AZURE_AVAILABLE:
            logger.error("azure-identity and azure-mgmt-compute are not installed. Install with: pip install azure-identity azure-mgmt-compute azure-mgmt-resource azure-mgmt-network")
            return False

        try:
            # Create credential object
            self.credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret
            )

            # Initialize Azure clients
            self.compute_client = ComputeManagementClient(
                credential=self.credential,
                subscription_id=subscription_id
            )
            
            self.resource_client = ResourceManagementClient(
                credential=self.credential,
                subscription_id=subscription_id
            )
            
            self.network_client = NetworkManagementClient(
                credential=self.credential,
                subscription_id=subscription_id
            )

            # Test credentials by listing resource groups
            try:
                list(self.resource_client.resource_groups.list())
            except Exception as e:
                logger.error(f"Failed to verify Azure credentials: {e}")
                return False

            self.subscription_id = subscription_id
            self.client_id = client_id
            self.tenant_id = tenant_id
            self.region = region
            self.resource_group = resource_group
            self.credentials = credentials
            self.authenticated = True
            logger.info(f"Successfully authenticated with Azure subscription {subscription_id}")
            return True

        except ClientAuthenticationError:
            logger.error("Invalid Azure credentials")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during Azure authentication: {e}")
            return False

    def validate_credentials(self) -> bool:
        """Validate current Azure credentials."""
        if not self.authenticated or not self.compute_client:
            return False
        
        try:
            # Try to list resource groups to verify credentials are still valid
            list(self.resource_client.resource_groups.list())
            return True
        except Exception:
            return False

    def _ensure_resource_group(self) -> bool:
        """Ensure the resource group exists, create if it doesn't."""
        try:
            # Check if resource group exists
            try:
                self.resource_client.resource_groups.get(self.resource_group)
                return True
            except ResourceNotFoundError:
                # Create resource group
                rg_params = {
                    "location": self.region,
                    "tags": {"created_by": "clustrix"}
                }
                self.resource_client.resource_groups.create_or_update(
                    self.resource_group, rg_params
                )
                logger.info(f"Created resource group '{self.resource_group}' in {self.region}")
                return True
        except Exception as e:
            logger.error(f"Failed to ensure resource group: {e}")
            return False

    def create_vm(self, vm_name: str, vm_size: str = "Standard_D2s_v3",
                  admin_username: str = "azureuser", admin_password: str = None) -> Dict[str, Any]:
        """
        Create an Azure Virtual Machine.
        
        Args:
            vm_name: Name for the VM
            vm_size: VM size (e.g., Standard_D2s_v3)
            admin_username: Admin username for the VM
            admin_password: Admin password (if None, uses SSH key)
            
        Returns:
            Dict with VM information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Azure")
            
        try:
            # Ensure resource group exists
            if not self._ensure_resource_group():
                raise RuntimeError("Failed to create or access resource group")
            
            # Create or get virtual network
            vnet_name = f"{vm_name}-vnet"
            subnet_name = f"{vm_name}-subnet"
            
            vnet_params = {
                "location": self.region,
                "address_space": {"address_prefixes": ["10.0.0.0/16"]},
                "subnets": [
                    {
                        "name": subnet_name,
                        "address_prefix": "10.0.0.0/24"
                    }
                ]
            }
            
            vnet_result = self.network_client.virtual_networks.begin_create_or_update(
                self.resource_group, vnet_name, vnet_params
            ).result()
            
            # Create public IP
            public_ip_name = f"{vm_name}-ip"
            public_ip_params = {
                "location": self.region,
                "public_ip_allocation_method": "Static",
                "sku": {"name": "Standard"}
            }
            
            public_ip_result = self.network_client.public_ip_addresses.begin_create_or_update(
                self.resource_group, public_ip_name, public_ip_params
            ).result()
            
            # Create network security group with SSH rule
            nsg_name = f"{vm_name}-nsg"
            nsg_params = {
                "location": self.region,
                "security_rules": [
                    {
                        "name": "SSH",
                        "protocol": "Tcp",
                        "source_port_range": "*",
                        "destination_port_range": "22",
                        "source_address_prefix": "*",
                        "destination_address_prefix": "*",
                        "access": "Allow",
                        "priority": 1000,
                        "direction": "Inbound"
                    }
                ]
            }
            
            nsg_result = self.network_client.network_security_groups.begin_create_or_update(
                self.resource_group, nsg_name, nsg_params
            ).result()
            
            # Create network interface
            nic_name = f"{vm_name}-nic"
            nic_params = {
                "location": self.region,
                "ip_configurations": [
                    {
                        "name": "ipconfig1",
                        "subnet": {"id": vnet_result.subnets[0].id},
                        "public_ip_address": {"id": public_ip_result.id}
                    }
                ],
                "network_security_group": {"id": nsg_result.id}
            }
            
            nic_result = self.network_client.network_interfaces.begin_create_or_update(
                self.resource_group, nic_name, nic_params
            ).result()
            
            # Create VM
            vm_params = {
                "location": self.region,
                "os_profile": {
                    "computer_name": vm_name,
                    "admin_username": admin_username,
                    "linux_configuration": {
                        "disable_password_authentication": admin_password is None,
                        "ssh": {
                            "public_keys": [] if admin_password else []  # Would need SSH key
                        }
                    }
                },
                "hardware_profile": {"vm_size": vm_size},
                "storage_profile": {
                    "image_reference": {
                        "publisher": "Canonical",
                        "offer": "0001-com-ubuntu-server-focal",
                        "sku": "20_04-lts-gen2",
                        "version": "latest"
                    },
                    "os_disk": {
                        "create_option": "FromImage",
                        "disk_size_gb": 30,
                        "managed_disk": {"storage_account_type": "Standard_LRS"}
                    }
                },
                "network_profile": {
                    "network_interfaces": [{"id": nic_result.id}]
                },
                "tags": {"created_by": "clustrix"}
            }
            
            # Add password if provided
            if admin_password:
                vm_params["os_profile"]["admin_password"] = admin_password
            
            # Create the VM
            vm_result = self.compute_client.virtual_machines.begin_create_or_update(
                self.resource_group, vm_name, vm_params
            ).result()
            
            logger.info(f"Created Azure VM '{vm_name}' with size {vm_size}")
            
            return {
                "vm_name": vm_name,
                "vm_id": vm_result.id,
                "vm_size": vm_size,
                "region": self.region,
                "resource_group": self.resource_group,
                "status": "creating",
                "public_ip": public_ip_result.ip_address,
                "admin_username": admin_username,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to create Azure VM: {e}")
            raise

    def create_cluster(self, cluster_name: str, cluster_type: str = "vm", **kwargs) -> Dict[str, Any]:
        """Create an Azure cluster (VM or AKS)."""
        if cluster_type == "vm":
            return self.create_vm(cluster_name, **kwargs)
        elif cluster_type == "aks":
            # TODO: Implement AKS cluster creation
            return {
                "cluster_name": cluster_name,
                "status": "creating",
                "region": self.region,
                "provider": "azure",
                "cluster_type": "aks"
            }
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def delete_cluster(self, cluster_identifier: str) -> bool:
        """Delete an Azure cluster."""
        # TODO: Implement Azure cluster deletion
        logger.info(f"Would delete Azure cluster: {cluster_identifier}")
        return True

    def get_cluster_status(self, cluster_identifier: str) -> Dict[str, Any]:
        """Get status of an Azure cluster."""
        # TODO: Implement Azure cluster status
        return {
            "cluster_name": cluster_identifier,
            "status": "running",
            "provider": "azure",
        }

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all Azure clusters."""
        # TODO: Implement Azure cluster listing
        return []

    def get_cluster_config(self, cluster_identifier: str) -> Dict[str, Any]:
        """Get Clustrix configuration for an Azure cluster."""
        # TODO: Implement Azure cluster config generation
        return {
            "name": f"Azure - {cluster_identifier}",
            "cluster_type": "ssh",  # or "kubernetes" for AKS
            "cluster_host": "placeholder.azure.com",
            "provider": "azure",
        }

    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """Estimate Azure costs."""
        # TODO: Implement Azure cost estimation
        return {"total": 0.0}

    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """Get available Azure VM sizes."""
        # TODO: Query Azure API for actual VM sizes
        return [
            "Standard_B1s",
            "Standard_B1ms",
            "Standard_B2s",
            "Standard_B2ms",
            "Standard_D2s_v3",
            "Standard_D4s_v3",
            "Standard_D8s_v3",
            "Standard_F2s_v2",
            "Standard_F4s_v2",
            "Standard_F8s_v2",
            "Standard_E2s_v3",
            "Standard_E4s_v3",
            "Standard_E8s_v3",
        ]

    def get_available_regions(self) -> List[str]:
        """Get available Azure regions."""
        # TODO: Query Azure API for actual regions
        return [
            "eastus",
            "eastus2",
            "westus",
            "westus2",
            "centralus",
            "northeurope",
            "westeurope",
            "uksouth",
            "ukwest",
            "southeastasia",
            "eastasia",
            "australiaeast",
            "japaneast",
        ]


# Register the provider (placeholder - will be enabled when azure-identity is available)
# PROVIDERS['azure'] = AzureProvider
