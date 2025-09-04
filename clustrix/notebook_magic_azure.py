"""
Azure cloud provider notebook magic integration for Clustrix.

This module provides Azure-specific configuration widgets and functionality
for use in Jupyter notebooks with Clustrix.
"""

import logging
from typing import Dict, List, Optional, Any

try:
    import ipywidgets as widgets  # type: ignore

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import widgets

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

from .cloud_providers.azure import AzureProvider

logger = logging.getLogger(__name__)


def configure_azure_auth(
    subscription_id: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    tenant_id: Optional[str] = None,
    region: str = "eastus",
    resource_group: str = "clustrix-rg",
) -> Dict[str, Any]:
    """
    Configure Azure authentication for Clustrix.

    Args:
        subscription_id: Azure subscription ID
        client_id: Azure service principal client ID
        client_secret: Azure service principal secret
        tenant_id: Azure tenant ID
        region: Azure region (default: eastus)
        resource_group: Resource group name (default: clustrix-rg)

    Returns:
        Dict with authentication configuration status
    """
    if not AZURE_AVAILABLE:
        return {
            "success": False,
            "error": (
                "Azure SDK is not installed. Install with: "
                "pip install azure-identity azure-mgmt-compute "
                "azure-mgmt-resource azure-mgmt-network"
            ),
            "authenticated": False,
        }

    try:
        # Create Azure provider instance
        provider = AzureProvider()

        # Build credentials dict
        credentials = {
            "subscription_id": subscription_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "tenant_id": tenant_id,
            "region": region,
            "resource_group": resource_group,
        }

        # Test authentication
        auth_success = provider.authenticate(**credentials)

        if auth_success:
            return {
                "success": True,
                "message": f"Successfully configured Azure authentication for subscription {subscription_id}",
                "authenticated": True,
                "region": region,
                "resource_group": resource_group,
                "provider": "azure",
            }
        else:
            return {
                "success": False,
                "error": "Failed to authenticate with provided Azure credentials",
                "authenticated": False,
            }

    except Exception as e:
        logger.error(f"Error configuring Azure authentication: {e}")
        return {
            "success": False,
            "error": f"Error configuring Azure authentication: {str(e)}",
            "authenticated": False,
        }


def validate_azure_connection(
    subscription_id: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    tenant_id: Optional[str] = None,
    region: str = "eastus",
) -> Dict[str, Any]:
    """
    Validate Azure connection and credentials.

    Args:
        subscription_id: Azure subscription ID
        client_id: Azure service principal client ID
        client_secret: Azure service principal secret
        tenant_id: Azure tenant ID
        region: Azure region

    Returns:
        Dict with validation results
    """
    if not AZURE_AVAILABLE:
        return {"valid": False, "error": "Azure SDK is not installed", "details": {}}

    try:
        # Create credential object
        credential = ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )

        # Test with Resource Management to list resource groups
        resource_client = ResourceManagementClient(
            credential=credential, subscription_id=subscription_id
        )

        # List resource groups to validate connection
        resource_groups = list(resource_client.resource_groups.list())

        # Get compute client for additional validation
        compute_client = ComputeManagementClient(
            credential=credential, subscription_id=subscription_id
        )

        # List VM sizes in the region to verify region access
        vm_sizes = list(compute_client.virtual_machine_sizes.list(location=region))

        return {
            "valid": True,
            "message": "Azure connection validated successfully",
            "details": {
                "subscription_id": subscription_id,
                "tenant_id": tenant_id,
                "region": region,
                "resource_groups_count": len(resource_groups),
                "available_vm_sizes": len(vm_sizes),
            },
        }

    except ClientAuthenticationError as e:
        return {
            "valid": False,
            "error": f"Azure authentication failed: {str(e)}",
            "details": {},
        }
    except Exception as e:
        return {
            "valid": False,
            "error": f"Connection validation failed: {str(e)}",
            "details": {},
        }


def list_azure_resources(
    subscription_id: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    tenant_id: Optional[str] = None,
    region: str = "eastus",
    resource_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    List available Azure resources.

    Args:
        subscription_id: Azure subscription ID
        client_id: Azure service principal client ID
        client_secret: Azure service principal secret
        tenant_id: Azure tenant ID
        region: Azure region
        resource_types: List of resource types to query (defaults to common types)

    Returns:
        Dict with discovered resources
    """
    if not AZURE_AVAILABLE:
        return {
            "success": False,
            "error": "Azure SDK is not installed",
            "resources": {},
        }

    if resource_types is None:
        resource_types = ["virtual_machines", "aks_clusters", "resource_groups"]

    try:
        credential = ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )

        resources = {}

        # List Virtual Machines
        if "virtual_machines" in resource_types:
            try:
                compute_client = ComputeManagementClient(
                    credential=credential, subscription_id=subscription_id
                )
                vms = []
                for vm in compute_client.virtual_machines.list_all():
                    vm_info = {
                        "name": vm.name,
                        "location": vm.location,
                        "vm_size": (
                            vm.hardware_profile.vm_size if vm.hardware_profile else None
                        ),
                        "provisioning_state": vm.provisioning_state,
                        "resource_group": vm.id.split("/")[4] if vm.id else None,
                    }
                    vms.append(vm_info)
                resources["virtual_machines"] = vms
            except Exception as e:
                resources["virtual_machines"] = [{"error": str(e)}]

        # List AKS Clusters
        if "aks_clusters" in resource_types:
            try:
                container_client = ContainerServiceClient(
                    credential=credential, subscription_id=subscription_id
                )
                clusters = []
                for cluster in container_client.managed_clusters.list():
                    cluster_info = {
                        "name": cluster.name,
                        "location": cluster.location,
                        "kubernetes_version": cluster.kubernetes_version,
                        "provisioning_state": cluster.provisioning_state,
                        "agent_pool_count": (
                            len(cluster.agent_pool_profiles)
                            if cluster.agent_pool_profiles
                            else 0
                        ),
                        "resource_group": (
                            cluster.id.split("/")[4] if cluster.id else None
                        ),
                    }
                    clusters.append(cluster_info)
                resources["aks_clusters"] = clusters
            except Exception as e:
                resources["aks_clusters"] = [{"error": str(e)}]

        # List Resource Groups
        if "resource_groups" in resource_types:
            try:
                resource_client = ResourceManagementClient(
                    credential=credential, subscription_id=subscription_id
                )
                resource_groups = []
                for rg in resource_client.resource_groups.list():
                    rg_info = {
                        "name": rg.name,
                        "location": rg.location,
                        "provisioning_state": (
                            rg.properties.provisioning_state if rg.properties else None
                        ),
                    }
                    resource_groups.append(rg_info)
                resources["resource_groups"] = resource_groups
            except Exception as e:
                resources["resource_groups"] = [{"error": str(e)}]

        return {
            "success": True,
            "subscription_id": subscription_id,
            "region": region,
            "resources": resources,
        }

    except Exception as e:
        logger.error(f"Error listing Azure resources: {e}")
        return {
            "success": False,
            "error": f"Failed to list Azure resources: {str(e)}",
            "resources": {},
        }


def get_azure_regions() -> List[str]:
    """
    Get list of available Azure regions.

    Returns:
        List of Azure region names
    """
    # Common Azure regions - in practice this could be fetched via API
    return [
        "eastus",
        "westus",
        "westus2",
        "centralus",
        "southcentralus",
        "northcentralus",
        "westcentralus",
        "eastus2",
        "westeurope",
        "northeurope",
        "ukwest",
        "uksouth",
        "francecentral",
        "germanywestcentral",
        "switzerlandnorth",
        "southeastasia",
        "eastasia",
        "australiaeast",
        "australiasoutheast",
        "japaneast",
        "japanwest",
        "koreacentral",
        "koreasouth",
        "canadacentral",
        "canadaeast",
        "brazilsouth",
        "southafricanorth",
    ]


def get_azure_vm_sizes(region: str = "eastus") -> List[str]:
    """
    Get available Azure VM sizes for a region.

    Args:
        region: Azure region

    Returns:
        List of VM size names
    """
    # Common VM sizes - in practice this could be fetched via API
    return [
        "Standard_B1s",
        "Standard_B1ms",
        "Standard_B2s",
        "Standard_B2ms",
        "Standard_B4ms",
        "Standard_D2s_v3",
        "Standard_D4s_v3",
        "Standard_D8s_v3",
        "Standard_D16s_v3",
        "Standard_E2s_v3",
        "Standard_E4s_v3",
        "Standard_E8s_v3",
        "Standard_E16s_v3",
        "Standard_F2s_v2",
        "Standard_F4s_v2",
        "Standard_F8s_v2",
        "Standard_F16s_v2",
        "Standard_DS1_v2",
        "Standard_DS2_v2",
        "Standard_DS3_v2",
        "Standard_DS4_v2",
    ]


def create_azure_config_widget() -> widgets.Widget:
    """
    Create Azure configuration widget for Jupyter notebooks.

    Returns:
        IPython widget for Azure configuration
    """
    if not IPYTHON_AVAILABLE:
        raise ImportError("IPython and ipywidgets are required")

    # Style configuration
    style = {"description_width": "150px"}
    layout = widgets.Layout(width="400px")

    # Create input widgets
    subscription_widget = widgets.Text(
        description="Subscription ID:",
        style=style,
        layout=layout,
        placeholder="Enter Azure subscription ID",
    )

    client_id_widget = widgets.Text(
        description="Client ID:",
        style=style,
        layout=layout,
        placeholder="Enter service principal client ID",
    )

    client_secret_widget = widgets.Password(
        description="Client Secret:",
        style=style,
        layout=layout,
        placeholder="Enter service principal secret",
    )

    tenant_widget = widgets.Text(
        description="Tenant ID:",
        style=style,
        layout=layout,
        placeholder="Enter Azure tenant ID",
    )

    region_widget = widgets.Dropdown(
        description="Region:",
        options=get_azure_regions(),
        value="eastus",
        style=style,
        layout=layout,
    )

    resource_group_widget = widgets.Text(
        description="Resource Group:",
        style=style,
        layout=layout,
        value="clustrix-rg",
        placeholder="Enter resource group name",
    )

    # Test connection button
    test_button = widgets.Button(
        description="Test Connection",
        button_style="primary",
        layout=widgets.Layout(width="150px"),
    )

    # Output area
    output = widgets.Output()

    def on_test_click(button):
        """Handle test connection button click."""
        with output:
            output.clear_output()
            print("Testing Azure connection...")

            result = validate_azure_connection(
                subscription_id=subscription_widget.value or None,
                client_id=client_id_widget.value or None,
                client_secret=client_secret_widget.value or None,
                tenant_id=tenant_widget.value or None,
                region=region_widget.value,
            )

            if result["valid"]:
                print("✅ Connection successful!")
                if result.get("details"):
                    print(
                        f"Subscription: {result['details'].get('subscription_id', 'N/A')}"
                    )
                    print(f"Region: {result['details'].get('region', 'N/A')}")
                    print(
                        f"Resource Groups: {result['details'].get('resource_groups_count', 'N/A')}"
                    )
            else:
                print(f"❌ Connection failed: {result.get('error', 'Unknown error')}")

    test_button.on_click(on_test_click)

    # Create layout
    return widgets.VBox(
        [
            widgets.HTML("<h3>Azure Configuration</h3>"),
            subscription_widget,
            client_id_widget,
            client_secret_widget,
            tenant_widget,
            region_widget,
            resource_group_widget,
            test_button,
            output,
        ]
    )
