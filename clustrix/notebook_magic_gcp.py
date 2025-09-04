"""
Google Cloud Platform notebook magic integration for Clustrix.

This module provides GCP-specific configuration widgets and functionality
for use in Jupyter notebooks with Clustrix.
"""

import json
import logging
from typing import Dict, List, Optional, Any

try:
    import ipywidgets as widgets  # type: ignore

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import widgets

try:
    from google.cloud import compute_v1
    from google.cloud import container_v1
    from google.oauth2 import service_account
    from google.auth.exceptions import DefaultCredentialsError

    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False
    compute_v1 = None  # type: ignore
    container_v1 = None  # type: ignore
    service_account = None  # type: ignore
    DefaultCredentialsError = Exception  # type: ignore

from .cloud_providers.gcp import GCPProvider

logger = logging.getLogger(__name__)


def configure_gcp_credentials(
    project_id: Optional[str] = None,
    service_account_key: Optional[str] = None,
    region: str = "us-central1",
) -> Dict[str, Any]:
    """
    Configure GCP credentials for Clustrix.

    Args:
        project_id: GCP project ID
        service_account_key: Service account JSON key (as string)
        region: GCP region (default: us-central1)

    Returns:
        Dict with credential configuration status
    """
    if not GCP_AVAILABLE:
        return {
            "success": False,
            "error": (
                "Google Cloud SDK is not installed. Install with: "
                "pip install google-cloud-compute google-cloud-container"
            ),
            "credentials_valid": False,
        }

    try:
        # Create GCP provider instance
        provider = GCPProvider()

        # Build credentials dict
        credentials = {
            "project_id": project_id,
            "service_account_key": service_account_key,
            "region": region,
        }

        # Test authentication
        auth_success = provider.authenticate(**credentials)

        if auth_success:
            return {
                "success": True,
                "message": f"Successfully configured GCP credentials for project {project_id}",
                "credentials_valid": True,
                "region": region,
                "project_id": project_id,
                "provider": "gcp",
            }
        else:
            return {
                "success": False,
                "error": "Failed to authenticate with provided GCP credentials",
                "credentials_valid": False,
            }

    except Exception as e:
        logger.error(f"Error configuring GCP credentials: {e}")
        return {
            "success": False,
            "error": f"Error configuring GCP credentials: {str(e)}",
            "credentials_valid": False,
        }


def validate_gcp_connection(
    project_id: Optional[str] = None,
    service_account_key: Optional[str] = None,
    region: str = "us-central1",
) -> Dict[str, Any]:
    """
    Validate GCP connection and credentials.

    Args:
        project_id: GCP project ID
        service_account_key: Service account JSON key (as string)
        region: GCP region

    Returns:
        Dict with validation results
    """
    if not GCP_AVAILABLE:
        return {
            "valid": False,
            "error": "Google Cloud SDK is not installed",
            "details": {},
        }

    try:
        # Parse service account key JSON
        if isinstance(service_account_key, str):
            try:
                service_account_info = json.loads(service_account_key)
            except json.JSONDecodeError:
                return {
                    "valid": False,
                    "error": "Invalid service account key JSON format",
                    "details": {},
                }
        else:
            service_account_info = service_account_key

        # Create credentials from service account info
        creds = service_account.Credentials.from_service_account_info(
            service_account_info
        )

        # Test with Compute Engine to list instances
        compute_client = compute_v1.InstancesClient(credentials=creds)
        zone = f"{region}-a"

        # List instances to validate connection (works even if no instances)
        instances_list = compute_client.list(project=project_id, zone=zone)
        instance_count = len(list(instances_list))

        # Test with Container service
        try:
            container_client = container_v1.ClusterManagerClient(credentials=creds)
            clusters_response = container_client.list_clusters(
                parent=f"projects/{project_id}/locations/{region}"
            )
            cluster_count = len(clusters_response.clusters)
        except Exception:
            cluster_count = 0  # GKE might not be enabled

        return {
            "valid": True,
            "message": "GCP connection validated successfully",
            "details": {
                "project_id": project_id,
                "region": region,
                "zone": zone,
                "service_account_email": service_account_info.get("client_email"),
                "compute_instances": instance_count,
                "gke_clusters": cluster_count,
            },
        }

    except DefaultCredentialsError as e:
        return {
            "valid": False,
            "error": f"GCP authentication failed: {str(e)}",
            "details": {},
        }
    except Exception as e:
        return {
            "valid": False,
            "error": f"Connection validation failed: {str(e)}",
            "details": {},
        }


def list_gcp_resources(
    project_id: Optional[str] = None,
    service_account_key: Optional[str] = None,
    region: str = "us-central1",
    resource_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    List available GCP resources.

    Args:
        project_id: GCP project ID
        service_account_key: Service account JSON key (as string)
        region: GCP region
        resource_types: List of resource types to query (defaults to common types)

    Returns:
        Dict with discovered resources
    """
    if not GCP_AVAILABLE:
        return {
            "success": False,
            "error": "Google Cloud SDK is not installed",
            "resources": {},
        }

    if resource_types is None:
        resource_types = ["compute_instances", "gke_clusters", "zones"]

    try:
        # Parse service account key
        if isinstance(service_account_key, str):
            service_account_info = json.loads(service_account_key)
        else:
            service_account_info = service_account_key

        creds = service_account.Credentials.from_service_account_info(
            service_account_info
        )
        resources = {}

        # List Compute Engine instances
        if "compute_instances" in resource_types:
            try:
                compute_client = compute_v1.InstancesClient(credentials=creds)
                zones_client = compute_v1.ZonesClient(credentials=creds)

                instances = []
                # List zones in the region
                zones_list = zones_client.list(project=project_id)
                region_zones = [
                    z.name for z in zones_list if z.region.endswith(f"/{region}")
                ]

                for zone in region_zones[:3]:  # Limit to first 3 zones
                    zone_instances = compute_client.list(project=project_id, zone=zone)
                    for instance in zone_instances:
                        instances.append(
                            {
                                "name": instance.name,
                                "zone": zone,
                                "machine_type": instance.machine_type.split("/")[-1],
                                "status": instance.status,
                                "internal_ip": (
                                    instance.network_interfaces[0].network_i_p
                                    if instance.network_interfaces
                                    else None
                                ),
                                "external_ip": (
                                    instance.network_interfaces[0]
                                    .access_configs[0]
                                    .nat_i_p
                                    if instance.network_interfaces
                                    and instance.network_interfaces[0].access_configs
                                    else None
                                ),
                            }
                        )
                resources["compute_instances"] = instances
            except Exception as e:
                resources["compute_instances"] = [{"error": str(e)}]

        # List GKE clusters
        if "gke_clusters" in resource_types:
            try:
                container_client = container_v1.ClusterManagerClient(credentials=creds)
                clusters_response = container_client.list_clusters(
                    parent=f"projects/{project_id}/locations/{region}"
                )

                clusters = []
                for cluster in clusters_response.clusters:
                    clusters.append(
                        {
                            "name": cluster.name,
                            "location": cluster.location,
                            "status": (
                                cluster.status.name if cluster.status else "UNKNOWN"
                            ),
                            "current_master_version": cluster.current_master_version,
                            "node_count": sum(
                                pool.initial_node_count for pool in cluster.node_pools
                            ),
                            "endpoint": cluster.endpoint,
                        }
                    )
                resources["gke_clusters"] = clusters
            except Exception as e:
                resources["gke_clusters"] = [{"error": str(e)}]

        # List available zones
        if "zones" in resource_types:
            try:
                zones_client = compute_v1.ZonesClient(credentials=creds)
                zones_list = zones_client.list(project=project_id)

                zones = []
                for zone in zones_list:
                    if zone.region.endswith(f"/{region}"):
                        zones.append(
                            {
                                "name": zone.name,
                                "region": zone.region.split("/")[-1],
                                "status": zone.status,
                                "deprecated": zone.deprecated is not None,
                            }
                        )
                resources["zones"] = zones
            except Exception as e:
                resources["zones"] = [{"error": str(e)}]

        return {
            "success": True,
            "project_id": project_id,
            "region": region,
            "resources": resources,
        }

    except Exception as e:
        logger.error(f"Error listing GCP resources: {e}")
        return {
            "success": False,
            "error": f"Failed to list GCP resources: {str(e)}",
            "resources": {},
        }


def get_gcp_regions() -> List[str]:
    """
    Get list of available GCP regions.

    Returns:
        List of GCP region names
    """
    return [
        "us-central1",
        "us-east1",
        "us-east4",
        "us-west1",
        "us-west2",
        "us-west3",
        "us-west4",
        "europe-west1",
        "europe-west2",
        "europe-west3",
        "europe-west4",
        "europe-west6",
        "europe-central2",
        "europe-north1",
        "asia-east1",
        "asia-east2",
        "asia-northeast1",
        "asia-northeast2",
        "asia-northeast3",
        "asia-southeast1",
        "asia-south1",
        "australia-southeast1",
        "southamerica-east1",
        "northamerica-northeast1",
    ]


def get_gcp_machine_types(region: str = "us-central1") -> List[str]:
    """
    Get available GCP machine types for a region.

    Args:
        region: GCP region

    Returns:
        List of machine type names
    """
    return [
        "e2-micro",
        "e2-small",
        "e2-medium",
        "e2-standard-2",
        "e2-standard-4",
        "e2-standard-8",
        "n1-standard-1",
        "n1-standard-2",
        "n1-standard-4",
        "n1-standard-8",
        "n1-standard-16",
        "n2-standard-2",
        "n2-standard-4",
        "n2-standard-8",
        "n2-standard-16",
        "c2-standard-4",
        "c2-standard-8",
        "c2-standard-16",
        "n2-highmem-2",
        "n2-highmem-4",
        "n2-highmem-8",
    ]


def create_gcp_config_widget() -> widgets.Widget:
    """
    Create GCP configuration widget for Jupyter notebooks.

    Returns:
        IPython widget for GCP configuration
    """
    if not IPYTHON_AVAILABLE:
        raise ImportError("IPython and ipywidgets are required")

    # Style configuration
    style = {"description_width": "150px"}
    layout = widgets.Layout(width="400px")
    text_area_layout = widgets.Layout(width="400px", height="150px")

    # Create input widgets
    project_widget = widgets.Text(
        description="Project ID:",
        style=style,
        layout=layout,
        placeholder="Enter GCP project ID",
    )

    service_account_widget = widgets.Textarea(
        description="Service Account:",
        style=style,
        layout=text_area_layout,
        placeholder="Paste service account JSON key here",
    )

    region_widget = widgets.Dropdown(
        description="Region:",
        options=get_gcp_regions(),
        value="us-central1",
        style=style,
        layout=layout,
    )

    # Upload service account file option
    upload_widget = widgets.FileUpload(
        accept=".json", multiple=False, description="Upload SA Key"
    )

    # Test connection button
    test_button = widgets.Button(
        description="Test Connection",
        button_style="primary",
        layout=widgets.Layout(width="150px"),
    )

    # Output area
    output = widgets.Output()

    def on_file_upload(change):
        """Handle service account file upload."""
        for filename, file_info in upload_widget.value.items():
            try:
                content = file_info["content"].decode("utf-8")
                # Validate it's valid JSON
                json.loads(content)
                service_account_widget.value = content
                with output:
                    output.clear_output()
                    print(f"✅ Loaded service account key from {filename}")
            except Exception as e:
                with output:
                    output.clear_output()
                    print(f"❌ Error loading {filename}: {str(e)}")

    upload_widget.observe(on_file_upload, names="value")

    def on_test_click(button):
        """Handle test connection button click."""
        with output:
            output.clear_output()
            print("Testing GCP connection...")

            result = validate_gcp_connection(
                project_id=project_widget.value or None,
                service_account_key=service_account_widget.value or None,
                region=region_widget.value,
            )

            if result["valid"]:
                print("✅ Connection successful!")
                if result.get("details"):
                    print(f"Project: {result['details'].get('project_id', 'N/A')}")
                    print(f"Region: {result['details'].get('region', 'N/A')}")
                    print(
                        f"Service Account: {result['details'].get('service_account_email', 'N/A')}"
                    )
            else:
                print(f"❌ Connection failed: {result.get('error', 'Unknown error')}")

    test_button.on_click(on_test_click)

    # Create layout
    return widgets.VBox(
        [
            widgets.HTML("<h3>Google Cloud Platform Configuration</h3>"),
            project_widget,
            widgets.HTML("<b>Service Account Key:</b>"),
            service_account_widget,
            widgets.HTML("<i>Or upload JSON file:</i>"),
            upload_widget,
            region_widget,
            test_button,
            output,
        ]
    )
