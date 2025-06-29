"""Azure cloud provider integration for Clustrix."""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

try:
    from azure.identity import ClientSecretCredential
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.network import NetworkManagementClient
    from azure.mgmt.containerservice import ContainerServiceClient
    from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError

    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    ClientSecretCredential = None  # type: ignore
    ComputeManagementClient = None  # type: ignore
    ResourceManagementClient = None  # type: ignore
    NetworkManagementClient = None  # type: ignore
    ContainerServiceClient = None  # type: ignore
    ClientAuthenticationError = Exception  # type: ignore
    ResourceNotFoundError = Exception  # type: ignore

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
        self.container_client = None
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
            logger.error(
                "azure-identity and azure-mgmt-compute are not installed. "
                "Install with: pip install azure-identity azure-mgmt-compute "
                "azure-mgmt-resource azure-mgmt-network"
            )
            return False

        try:
            # Create credential object
            # Type assertions since we verified these are not None above
            assert isinstance(tenant_id, str)
            assert isinstance(client_id, str)
            assert isinstance(client_secret, str)
            assert isinstance(subscription_id, str)
            
            self.credential = ClientSecretCredential(
                tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
            )

            # Initialize Azure clients
            self.compute_client = ComputeManagementClient(
                credential=self.credential, subscription_id=subscription_id
            )

            self.resource_client = ResourceManagementClient(
                credential=self.credential, subscription_id=subscription_id
            )

            self.network_client = NetworkManagementClient(
                credential=self.credential, subscription_id=subscription_id
            )

            self.container_client = ContainerServiceClient(
                credential=self.credential, subscription_id=subscription_id
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
            logger.info(
                f"Successfully authenticated with Azure subscription {subscription_id}"
            )
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
                    "tags": {"created_by": "clustrix"},
                }
                self.resource_client.resource_groups.create_or_update(
                    self.resource_group, rg_params
                )
                logger.info(
                    f"Created resource group '{self.resource_group}' in {self.region}"
                )
                return True
        except Exception as e:
            logger.error(f"Failed to ensure resource group: {e}")
            return False

    def create_vm(
        self,
        vm_name: str,
        vm_size: str = "Standard_D2s_v3",
        admin_username: str = "azureuser",
        admin_password: Optional[str] = None,
    ) -> Dict[str, Any]:
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
                "subnets": [{"name": subnet_name, "address_prefix": "10.0.0.0/24"}],
            }

            vnet_result = self.network_client.virtual_networks.begin_create_or_update(
                self.resource_group, vnet_name, vnet_params
            ).result()

            # Create public IP
            public_ip_name = f"{vm_name}-ip"
            public_ip_params = {
                "location": self.region,
                "public_ip_allocation_method": "Static",
                "sku": {"name": "Standard"},
            }

            public_ip_result = (
                self.network_client.public_ip_addresses.begin_create_or_update(
                    self.resource_group, public_ip_name, public_ip_params
                ).result()
            )

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
                        "direction": "Inbound",
                    }
                ],
            }

            nsg_result = (
                self.network_client.network_security_groups.begin_create_or_update(
                    self.resource_group, nsg_name, nsg_params
                ).result()
            )

            # Create network interface
            nic_name = f"{vm_name}-nic"
            nic_params = {
                "location": self.region,
                "ip_configurations": [
                    {
                        "name": "ipconfig1",
                        "subnet": {"id": vnet_result.subnets[0].id},
                        "public_ip_address": {"id": public_ip_result.id},
                    }
                ],
                "network_security_group": {"id": nsg_result.id},
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
                            "public_keys": (
                                [] if admin_password else []
                            )  # Would need SSH key
                        },
                    },
                },
                "hardware_profile": {"vm_size": vm_size},
                "storage_profile": {
                    "image_reference": {
                        "publisher": "Canonical",
                        "offer": "0001-com-ubuntu-server-focal",
                        "sku": "20_04-lts-gen2",
                        "version": "latest",
                    },
                    "os_disk": {
                        "create_option": "FromImage",
                        "disk_size_gb": 30,
                        "managed_disk": {"storage_account_type": "Standard_LRS"},
                    },
                },
                "network_profile": {"network_interfaces": [{"id": nic_result.id}]},
                "tags": {"created_by": "clustrix"},
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
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to create Azure VM: {e}")
            raise

    def create_aks_cluster(
        self,
        cluster_name: str,
        node_count: int = 3,
        node_vm_size: str = "Standard_DS2_v2",
        kubernetes_version: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Create an AKS (Azure Kubernetes Service) cluster.

        Args:
            cluster_name: Name for the AKS cluster
            node_count: Number of nodes in the default node pool
            node_vm_size: VM size for nodes (default: Standard_DS2_v2)
            kubernetes_version: Kubernetes version (uses default if None)
            **kwargs: Additional cluster configuration

        Returns:
            Dict containing cluster information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Azure")

        try:
            logger.info(f"Creating AKS cluster '{cluster_name}' in {self.region}...")

            # Ensure resource group exists
            self.resource_client.resource_groups.create_or_update(
                self.resource_group, {"location": self.region}
            )

            # Define AKS cluster configuration
            cluster_config = {
                "location": self.region,
                "kubernetes_version": kubernetes_version,
                "agent_pool_profiles": [
                    {
                        "name": "default",
                        "count": node_count,
                        "vm_size": node_vm_size,
                        "os_type": "Linux",
                        "mode": "System",
                    }
                ],
                "service_principal_profile": {
                    "client_id": self.client_id,
                    "secret": self.credentials.get("client_secret"),
                },
                "network_profile": {"network_plugin": "kubenet"},
                "enable_rbac": True,
                "tags": {"created_by": "clustrix", "cluster_name": cluster_name},
            }

            # Start cluster creation (async operation)
            operation = self.container_client.managed_clusters.begin_create_or_update(
                resource_group_name=self.resource_group,
                resource_name=cluster_name,
                parameters=cluster_config,
            )

            logger.info(f"AKS cluster creation initiated - operation: {operation}")

            return {
                "cluster_name": cluster_name,
                "status": "creating",
                "region": self.region,
                "provider": "azure",
                "cluster_type": "aks",
                "resource_group": self.resource_group,
                "node_count": node_count,
                "node_vm_size": node_vm_size,
                "kubernetes_version": kubernetes_version,
                "operation_id": str(operation),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to create AKS cluster: {e}")
            raise

    def create_cluster(
        self, cluster_name: str, cluster_type: str = "vm", **kwargs
    ) -> Dict[str, Any]:
        """Create an Azure cluster (VM or AKS)."""
        if cluster_type == "vm":
            return self.create_vm(cluster_name, **kwargs)
        elif cluster_type == "aks":
            return self.create_aks_cluster(cluster_name, **kwargs)
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def delete_cluster(self, cluster_identifier: str, cluster_type: str = "vm") -> bool:
        """Delete an Azure cluster."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Azure")

        try:
            if cluster_type == "vm":
                # Delete VM and associated resources
                self.compute_client.virtual_machines.begin_delete(
                    self.resource_group, cluster_identifier
                ).result()

                # Also delete associated resources (NIC, IP, NSG, etc.)
                try:
                    self.network_client.network_interfaces.begin_delete(
                        self.resource_group, f"{cluster_identifier}-nic"
                    ).result()

                    self.network_client.public_ip_addresses.begin_delete(
                        self.resource_group, f"{cluster_identifier}-ip"
                    ).result()

                    self.network_client.network_security_groups.begin_delete(
                        self.resource_group, f"{cluster_identifier}-nsg"
                    ).result()

                    self.network_client.virtual_networks.begin_delete(
                        self.resource_group, f"{cluster_identifier}-vnet"
                    ).result()
                except Exception as e:
                    logger.warning(f"Failed to delete some associated resources: {e}")

                logger.info(f"Deleted Azure VM '{cluster_identifier}'")
                return True
            elif cluster_type == "aks":
                # Delete AKS cluster
                operation = self.container_client.managed_clusters.begin_delete(
                    resource_group_name=self.resource_group,
                    resource_name=cluster_identifier,
                )
                logger.info(
                    f"Deleting AKS cluster '{cluster_identifier}' - operation: {operation}"
                )
                return True
            else:
                raise ValueError(f"Unknown cluster type: {cluster_type}")

        except Exception as e:
            logger.error(f"Failed to delete Azure cluster: {e}")
            return False

    def get_cluster_status(
        self, cluster_identifier: str, cluster_type: str = "vm"
    ) -> Dict[str, Any]:
        """Get status of an Azure cluster."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Azure")

        try:
            if cluster_type == "vm":
                vm = self.compute_client.virtual_machines.get(
                    self.resource_group, cluster_identifier
                )
                return {
                    "vm_name": cluster_identifier,
                    "status": (
                        vm.provisioning_state.lower()
                        if vm.provisioning_state
                        else "unknown"
                    ),
                    "vm_size": (
                        vm.hardware_profile.vm_size
                        if vm.hardware_profile
                        else "unknown"
                    ),
                    "region": vm.location,
                    "resource_group": self.resource_group,
                    "provider": "azure",
                    "cluster_type": "vm",
                }
            elif cluster_type == "aks":
                # Get AKS cluster status
                cluster = self.container_client.managed_clusters.get(
                    resource_group_name=self.resource_group,
                    resource_name=cluster_identifier,
                )

                return {
                    "cluster_name": cluster_identifier,
                    "status": cluster.provisioning_state.lower(),
                    "kubernetes_version": cluster.kubernetes_version,
                    "node_count": (
                        cluster.agent_pool_profiles[0].count
                        if cluster.agent_pool_profiles
                        else 0
                    ),
                    "fqdn": cluster.fqdn,
                    "region": cluster.location,
                    "resource_group": self.resource_group,
                    "provider": "azure",
                    "cluster_type": "aks",
                }
            else:
                raise ValueError(f"Unknown cluster type: {cluster_type}")
        except Exception as e:
            logger.error(f"Failed to get cluster status: {e}")
            raise

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all Azure clusters."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Azure")

        clusters = []

        try:
            # List VMs with clustrix tag
            vms = self.compute_client.virtual_machines.list(self.resource_group)

            for vm in vms:
                # Check if VM has clustrix tag
                tags = getattr(vm, "tags", {})

                if tags and tags.get("created_by") == "clustrix":
                    clusters.append(
                        {
                            "name": vm.name,
                            "vm_id": vm.id,
                            "type": "vm",
                            "status": (
                                vm.provisioning_state.lower()
                                if vm.provisioning_state
                                else "unknown"
                            ),
                            "vm_size": (
                                vm.hardware_profile.vm_size
                                if vm.hardware_profile
                                else "unknown"
                            ),
                            "region": vm.location,
                            "resource_group": self.resource_group,
                        }
                    )

        except Exception as e:
            logger.error(f"Failed to list Azure VMs: {e}")

        # List AKS clusters
        try:
            aks_clusters = (
                self.container_client.managed_clusters.list_by_resource_group(
                    resource_group_name=self.resource_group
                )
            )

            for cluster in aks_clusters:
                # Only include clusters with clustrix tag
                tags = cluster.tags or {}
                if tags.get("created_by") == "clustrix":
                    clusters.append(
                        {
                            "name": cluster.name,
                            "cluster_id": cluster.id,
                            "type": "aks",
                            "status": cluster.provisioning_state.lower(),
                            "kubernetes_version": cluster.kubernetes_version,
                            "node_count": (
                                cluster.agent_pool_profiles[0].count
                                if cluster.agent_pool_profiles
                                else 0
                            ),
                            "fqdn": cluster.fqdn,
                            "region": cluster.location,
                            "resource_group": self.resource_group,
                        }
                    )
        except Exception as e:
            logger.error(f"Failed to list AKS clusters: {e}")

        return clusters

    def get_cluster_config(
        self, cluster_identifier: str, cluster_type: str = "vm"
    ) -> Dict[str, Any]:
        """Get Clustrix configuration for an Azure cluster."""
        if cluster_type == "vm":
            # Get VM details and public IP
            try:
                self.compute_client.virtual_machines.get(
                    self.resource_group, cluster_identifier
                )

                # Get public IP
                public_ip = ""
                try:
                    ip_result = self.network_client.public_ip_addresses.get(
                        self.resource_group, f"{cluster_identifier}-ip"
                    )
                    public_ip = ip_result.ip_address or ""
                except Exception:
                    pass

                return {
                    "name": f"Azure VM - {cluster_identifier}",
                    "cluster_type": "ssh",
                    "cluster_host": public_ip,
                    "username": "azureuser",  # Default admin username
                    "cluster_port": 22,
                    "default_cores": 2,  # Would need to map VM size to cores
                    "default_memory": "4GB",  # Would need to map VM size to memory
                    "remote_work_dir": "/home/azureuser/clustrix",
                    "package_manager": "conda",
                    "cost_monitoring": True,
                    "provider": "azure",
                    "provider_config": {
                        "vm_name": cluster_identifier,
                        "resource_group": self.resource_group,
                        "region": self.region,
                        "subscription_id": self.subscription_id,
                    },
                }
            except Exception as e:
                logger.error(f"Failed to get VM details: {e}")
                # Return basic config
                return {
                    "name": f"Azure VM - {cluster_identifier}",
                    "cluster_type": "ssh",
                    "cluster_host": "placeholder.azure.com",
                    "provider": "azure",
                }
        elif cluster_type == "aks":
            return {
                "name": f"Azure AKS - {cluster_identifier}",
                "cluster_type": "kubernetes",
                "cluster_host": f"{cluster_identifier}.aks.{self.region}.azure.com",
                "cluster_port": 443,
                "k8s_namespace": "default",
                "k8s_image": "python:3.11",
                "default_cores": 2,
                "default_memory": "4GB",
                "cost_monitoring": True,
                "provider": "azure",
                "provider_config": {
                    "cluster_name": cluster_identifier,
                    "resource_group": self.resource_group,
                    "region": self.region,
                    "subscription_id": self.subscription_id,
                },
            }
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """Estimate Azure costs."""
        cluster_type = kwargs.get("cluster_type", "vm")
        vm_size = kwargs.get("vm_size", "Standard_D2s_v3")
        hours = kwargs.get("hours", 1)

        # Simplified pricing - real implementation would use Azure Pricing API
        vm_prices = {
            "Standard_B1s": 0.0104,
            "Standard_B1ms": 0.0207,
            "Standard_B2s": 0.0416,
            "Standard_B2ms": 0.0832,
            "Standard_D2s_v3": 0.096,
            "Standard_D4s_v3": 0.192,
            "Standard_D8s_v3": 0.384,
            "Standard_F2s_v2": 0.0834,
            "Standard_F4s_v2": 0.1669,
            "Standard_E2s_v3": 0.126,
            "Standard_E4s_v3": 0.252,
        }

        base_price = vm_prices.get(vm_size, 0.10)  # Default price

        if cluster_type == "aks":
            # AKS has cluster management fee (free tier available)
            cluster_fee = 0.0  # Free tier for development
            node_cost = base_price * hours
            total = cluster_fee + node_cost

            return {
                "cluster_management": cluster_fee,
                "nodes": node_cost,
                "total": total,
            }
        else:  # vm
            total = base_price * hours
            return {"vm": total, "total": total}

    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """Get available Azure VM sizes."""
        if not self.authenticated:
            # Return common VM sizes if not authenticated
            return [
                "Standard_B1s",
                "Standard_B2s",
                "Standard_D2s_v3",
                "Standard_D4s_v3",
                "Standard_F2s_v2",
                "Standard_E2s_v3",
                "Standard_E4s_v3",
            ]

        try:
            # Use specified region or current region
            query_region = region or self.region

            # Get VM sizes for the region
            vm_sizes = self.compute_client.virtual_machine_sizes.list(query_region)

            # Extract VM size names and filter to common families
            all_sizes = [size.name for size in vm_sizes]

            # Filter to common VM families for better UX
            common_families = [
                "Standard_B",
                "Standard_D",
                "Standard_E",
                "Standard_F",
                "Standard_A",
            ]
            filtered_sizes = []

            for family in common_families:
                family_sizes = [s for s in all_sizes if s.startswith(family)]
                # Sort by size (1s, 2s, 4s, etc.)
                family_sizes.sort(
                    key=lambda x: (
                        int(x.split("_")[1][1:].split("s")[0])
                        if "s" in x.split("_")[1]
                        and x.split("_")[1][1:].split("s")[0].isdigit()
                        else 999
                    )
                )
                filtered_sizes.extend(family_sizes[:8])  # Limit to 8 per family

            return filtered_sizes[:30]  # Limit total to 30 for better UX

        except Exception as e:
            logger.warning(f"Failed to fetch VM sizes for region {query_region}: {e}")
            # Return default list on error
            return [
                "Standard_B1s",
                "Standard_B2s",
                "Standard_D2s_v3",
                "Standard_D4s_v3",
                "Standard_F2s_v2",
                "Standard_E2s_v3",
                "Standard_E4s_v3",
            ]

    def get_available_regions(self) -> List[str]:
        """Get available Azure regions."""
        if not self.authenticated:
            # Return common regions if not authenticated
            return [
                "eastus",
                "westus2",
                "northeurope",
                "westeurope",
                "centralus",
                "southeastasia",
                "japaneast",
                "australiaeast",
            ]

        try:
            # Get all available regions where VMs can be deployed
            subscription_client = self.resource_client
            locations = subscription_client.subscriptions.list_locations(
                self.subscription_id
            )

            region_names = [loc.name for loc in locations]
            region_names.sort()

            # Prioritize common regions
            priority_regions = [
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
                "japaneast",
                "australiaeast",
            ]

            # Put priority regions first, then others
            sorted_regions = []
            for region in priority_regions:
                if region in region_names:
                    sorted_regions.append(region)
                    region_names.remove(region)

            sorted_regions.extend(region_names)
            return sorted_regions

        except Exception as e:
            logger.warning(f"Failed to fetch Azure regions: {e}")
            return [
                "eastus",
                "westus2",
                "northeurope",
                "westeurope",
                "centralus",
                "southeastasia",
                "japaneast",
                "australiaeast",
            ]


# Register the provider
if AZURE_AVAILABLE:
    PROVIDERS["azure"] = AzureProvider
