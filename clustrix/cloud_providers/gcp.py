"""Google Cloud Platform provider integration for Clustrix."""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

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

from .base import CloudProvider
from . import PROVIDERS

logger = logging.getLogger(__name__)


class GCPProvider(CloudProvider):
    """Google Cloud Platform provider implementation."""

    def __init__(self):
        """Initialize GCP provider."""
        super().__init__()
        self.project_id = None
        self.region = "us-central1"
        self.zone = "us-central1-a"
        self.compute_client = None
        self.container_client = None
        self.service_account_info = None

    def authenticate(self, **credentials) -> bool:
        """
        Authenticate with Google Cloud.

        Args:
            **credentials: GCP credentials including:
                - project_id: GCP project ID
                - service_account_key: Service account JSON key (as string)
                - region: GCP region (default: us-central1)

        Returns:
            bool: True if authentication successful
        """
        project_id = credentials.get("project_id")
        service_account_key = credentials.get("service_account_key")
        region = credentials.get("region", "us-central1")

        if not all([project_id, service_account_key]):
            logger.error("project_id and service_account_key are required")
            return False

        if not GCP_AVAILABLE:
            logger.error(
                "google-cloud-compute is not installed. Install with: pip install google-cloud-compute"
            )
            return False

        try:
            # Parse service account key JSON
            if isinstance(service_account_key, str):
                try:
                    self.service_account_info = json.loads(service_account_key)
                except json.JSONDecodeError:
                    logger.error("Invalid service account key JSON format")
                    return False
            else:
                self.service_account_info = service_account_key

            # Create credentials from service account info
            creds = service_account.Credentials.from_service_account_info(
                self.service_account_info
            )

            # Initialize compute client
            self.compute_client = compute_v1.InstancesClient(credentials=creds)

            # Initialize container client
            self.container_client = container_v1.ClusterManagerClient(credentials=creds)

            # Test credentials by making a simple API call
            # List instances to verify access (this should work even if no instances exist)
            try:
                self.compute_client.list(project=project_id, zone=f"{region}-a")
            except Exception as e:
                logger.error(f"Failed to verify GCP credentials: {e}")
                return False

            self.project_id = project_id
            self.region = region
            self.zone = f"{region}-a"  # Default to first zone in region
            self.credentials = credentials
            self.authenticated = True
            logger.info(f"Successfully authenticated with GCP project {project_id}")
            return True

        except DefaultCredentialsError:
            logger.error("Invalid GCP service account credentials")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during GCP authentication: {e}")
            return False

    def validate_credentials(self) -> bool:
        """Validate current GCP credentials."""
        if not self.authenticated or not self.compute_client:
            return False

        try:
            # Try to list instances to verify credentials are still valid
            self.compute_client.list(project=self.project_id, zone=self.zone)
            return True
        except Exception:
            return False

    def create_compute_instance(
        self,
        instance_name: str,
        machine_type: str = "e2-medium",
        image_family: str = "ubuntu-2004-lts",
        image_project: str = "ubuntu-os-cloud",
    ) -> Dict[str, Any]:
        """
        Create a Compute Engine instance.

        Args:
            instance_name: Name for the instance
            machine_type: Machine type (e.g., e2-medium)
            image_family: Image family to use
            image_project: Project containing the image

        Returns:
            Dict with instance information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with GCP")

        try:
            # Get the latest image from the family
            images_client = compute_v1.ImagesClient(
                credentials=service_account.Credentials.from_service_account_info(
                    self.service_account_info
                )
            )
            image = images_client.get_from_family(
                project=image_project, family=image_family
            )

            # Instance configuration
            machine_type_url = f"zones/{self.zone}/machineTypes/{machine_type}"

            instance_config = {
                "name": instance_name,
                "machine_type": machine_type_url,
                "disks": [
                    {
                        "boot": True,
                        "auto_delete": True,
                        "initialize_params": {
                            "source_image": image.self_link,
                            "disk_size_gb": "20",
                        },
                    }
                ],
                "network_interfaces": [
                    {
                        "network": "global/networks/default",
                        "access_configs": [
                            {"type": "ONE_TO_ONE_NAT", "name": "External NAT"}
                        ],
                    }
                ],
                "tags": {"items": ["clustrix-managed", "http-server", "https-server"]},
                "metadata": {
                    "items": [
                        {
                            "key": "startup-script",
                            "value": (
                                "#!/bin/bash\n# Clustrix instance setup\n"
                                "sudo apt-get update\nsudo apt-get install -y python3 python3-pip\n"
                            ),
                        }
                    ]
                },
            }

            # Create the instance
            operation = self.compute_client.insert(
                project=self.project_id,
                zone=self.zone,
                instance_resource=instance_config,
            )

            # Wait for operation to complete (simplified)
            logger.info(
                f"Creating GCP instance '{instance_name}' - operation: {operation.name}"
            )

            return {
                "instance_name": instance_name,
                "instance_id": instance_name,  # In GCP, name is the ID
                "machine_type": machine_type,
                "zone": self.zone,
                "region": self.region,
                "status": "creating",
                "operation": operation.name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to create GCP instance: {e}")
            raise

    def create_gke_cluster(
        self,
        cluster_name: str,
        node_count: int = 3,
        machine_type: str = "e2-medium",
        kubernetes_version: Optional[str] = None,
        disk_size_gb: int = 100,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Create a GKE (Google Kubernetes Engine) cluster.

        Args:
            cluster_name: Name for the GKE cluster
            node_count: Number of nodes in the default node pool
            machine_type: Machine type for nodes (default: e2-medium)
            kubernetes_version: Kubernetes version (uses default if None)
            disk_size_gb: Boot disk size in GB for nodes
            **kwargs: Additional cluster configuration

        Returns:
            Dict containing cluster information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with GCP")

        try:
            logger.info(f"Creating GKE cluster '{cluster_name}' in {self.region}...")

            # Set up cluster location (zone or region)
            location = self.zone  # Using zonal cluster for simplicity

            # Define GKE cluster configuration
            cluster_config = {
                "name": cluster_name,
                "description": "GKE cluster created by Clustrix",
                "initial_node_count": node_count,
                "node_config": {
                    "machine_type": machine_type,
                    "disk_size_gb": disk_size_gb,
                    "oauth_scopes": [
                        "https://www.googleapis.com/auth/devstorage.read_only",
                        "https://www.googleapis.com/auth/logging.write",
                        "https://www.googleapis.com/auth/monitoring",
                        "https://www.googleapis.com/auth/service.management.readonly",
                        "https://www.googleapis.com/auth/servicecontrol",
                        "https://www.googleapis.com/auth/trace.append",
                    ],
                    "labels": {
                        "created_by": "clustrix",
                        "cluster_name": cluster_name.replace("_", "-"),
                    },
                },
                "master_auth": {
                    "client_certificate_config": {"issue_client_certificate": False}
                },
                "ip_allocation_policy": {"use_ip_aliases": True},
                "network_policy": {"enabled": False},
                "addons_config": {
                    "http_load_balancing": {"disabled": False},
                    "horizontal_pod_autoscaling": {"disabled": False},
                },
            }

            if kubernetes_version:
                cluster_config["initial_cluster_version"] = kubernetes_version

            # Create cluster using the Container API
            parent = f"projects/{self.project_id}/locations/{location}"
            request = container_v1.CreateClusterRequest(
                parent=parent,
                cluster=cluster_config,
            )

            operation = self.container_client.create_cluster(request=request)

            logger.info(f"GKE cluster creation initiated - operation: {operation.name}")

            return {
                "cluster_name": cluster_name,
                "status": "creating",
                "region": self.region,
                "zone": self.zone,
                "provider": "gcp",
                "cluster_type": "gke",
                "project_id": self.project_id,
                "location": location,
                "node_count": node_count,
                "machine_type": machine_type,
                "disk_size_gb": disk_size_gb,
                "kubernetes_version": kubernetes_version,
                "operation_name": operation.name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to create GKE cluster: {e}")
            raise

    def create_cluster(
        self, cluster_name: str, cluster_type: str = "compute", **kwargs
    ) -> Dict[str, Any]:
        """Create a GCP cluster (Compute Engine VM or GKE)."""
        if cluster_type == "compute":
            return self.create_compute_instance(cluster_name, **kwargs)
        elif cluster_type == "gke":
            return self.create_gke_cluster(cluster_name, **kwargs)
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def delete_cluster(
        self, cluster_identifier: str, cluster_type: str = "compute"
    ) -> bool:
        """Delete a GCP cluster."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with GCP")

        try:
            if cluster_type == "compute":
                # Delete Compute Engine instance
                operation = self.compute_client.delete(
                    project=self.project_id, zone=self.zone, instance=cluster_identifier
                )
                logger.info(
                    f"Deleting GCP instance '{cluster_identifier}' - operation: {operation.name}"
                )
                return True
            elif cluster_type == "gke":
                # Delete GKE cluster
                location = self.zone  # Using same location as creation
                name = f"projects/{self.project_id}/locations/{location}/clusters/{cluster_identifier}"

                request = container_v1.DeleteClusterRequest(name=name)
                operation = self.container_client.delete_cluster(request=request)

                logger.info(
                    f"Deleting GKE cluster '{cluster_identifier}' - operation: {operation.name}"
                )
                return True
            else:
                raise ValueError(f"Unknown cluster type: {cluster_type}")

        except Exception as e:
            logger.error(f"Failed to delete GCP cluster: {e}")
            return False

    def get_cluster_status(
        self, cluster_identifier: str, cluster_type: str = "compute"
    ) -> Dict[str, Any]:
        """Get status of a GCP cluster."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with GCP")

        try:
            if cluster_type == "compute":
                instance = self.compute_client.get(
                    project=self.project_id, zone=self.zone, instance=cluster_identifier
                )
                return {
                    "instance_name": cluster_identifier,
                    "status": instance.status.lower(),
                    "machine_type": instance.machine_type.split("/")[-1],
                    "zone": self.zone,
                    "provider": "gcp",
                    "cluster_type": "compute",
                }
            elif cluster_type == "gke":
                # Get GKE cluster status
                location = self.zone  # Using same location as creation
                name = f"projects/{self.project_id}/locations/{location}/clusters/{cluster_identifier}"

                request = container_v1.GetClusterRequest(name=name)
                cluster = self.container_client.get_cluster(request=request)

                return {
                    "cluster_name": cluster_identifier,
                    "status": cluster.status.name.lower(),
                    "endpoint": cluster.endpoint,
                    "current_master_version": cluster.current_master_version,
                    "current_node_version": cluster.current_node_version,
                    "node_count": cluster.current_node_count,
                    "location": cluster.location,
                    "zone": cluster.zone if cluster.zone else None,
                    "create_time": cluster.create_time,
                    "provider": "gcp",
                    "cluster_type": "gke",
                    "project_id": self.project_id,
                }
            else:
                raise ValueError(f"Unknown cluster type: {cluster_type}")
        except Exception as e:
            logger.error(f"Failed to get cluster status: {e}")
            raise

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all GCP clusters."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with GCP")

        clusters = []

        try:
            # List Compute Engine instances with Clustrix tag
            instances = self.compute_client.list(
                project=self.project_id, zone=self.zone
            )

            for instance in instances:
                # Check if instance has clustrix-managed tag
                tags = getattr(instance, "tags", {})
                tag_items = getattr(tags, "items", [])

                if "clustrix-managed" in tag_items:
                    clusters.append(
                        {
                            "name": instance.name,
                            "instance_id": instance.name,
                            "type": "compute",
                            "status": instance.status.lower(),
                            "zone": self.zone,
                            "machine_type": (
                                instance.machine_type.split("/")[-1]
                                if instance.machine_type
                                else "unknown"
                            ),
                        }
                    )

        except Exception as e:
            logger.error(f"Failed to list GCP instances: {e}")

        # List GKE clusters
        try:
            location = self.zone  # List clusters in our zone
            parent = f"projects/{self.project_id}/locations/{location}"
            request = container_v1.ListClustersRequest(parent=parent)

            response = self.container_client.list_clusters(request=request)

            for cluster in response.clusters:
                # Only include clusters with clustrix labels
                labels = cluster.resource_labels or {}
                if labels.get("created_by") == "clustrix":
                    clusters.append(
                        {
                            "name": cluster.name,
                            "cluster_id": cluster.name,
                            "type": "gke",
                            "status": cluster.status.name.lower(),
                            "endpoint": cluster.endpoint,
                            "current_master_version": cluster.current_master_version,
                            "node_count": cluster.current_node_count,
                            "location": cluster.location,
                            "zone": cluster.zone if cluster.zone else None,
                            "project_id": self.project_id,
                        }
                    )
        except Exception as e:
            logger.error(f"Failed to list GKE clusters: {e}")

        return clusters

    def get_cluster_config(
        self, cluster_identifier: str, cluster_type: str = "compute"
    ) -> Dict[str, Any]:
        """Get Clustrix configuration for a GCP cluster."""
        if cluster_type == "compute":
            # Get instance details
            try:
                instance = self.compute_client.get(
                    project=self.project_id, zone=self.zone, instance=cluster_identifier
                )

                # Get external IP
                external_ip = ""
                for interface in instance.network_interfaces:
                    for access_config in interface.access_configs:
                        if access_config.nat_i_p:
                            external_ip = access_config.nat_i_p
                            break

                return {
                    "name": f"GCP Compute - {cluster_identifier}",
                    "cluster_type": "ssh",
                    "cluster_host": external_ip,
                    "username": "ubuntu",  # Default for Ubuntu images
                    "cluster_port": 22,
                    "default_cores": 2,  # Would need to map machine type to cores
                    "default_memory": "4GB",  # Would need to map machine type to memory
                    "remote_work_dir": "/home/ubuntu/clustrix",
                    "package_manager": "conda",
                    "cost_monitoring": True,
                    "provider": "gcp",
                    "provider_config": {
                        "instance_name": cluster_identifier,
                        "zone": self.zone,
                        "project_id": self.project_id,
                    },
                }
            except Exception as e:
                logger.error(f"Failed to get instance details: {e}")
                # Return basic config
                return {
                    "name": f"GCP Compute - {cluster_identifier}",
                    "cluster_type": "ssh",
                    "cluster_host": "placeholder.gcp.com",
                    "provider": "gcp",
                }
        elif cluster_type == "gke":
            return {
                "name": f"GCP GKE - {cluster_identifier}",
                "cluster_type": "kubernetes",
                "cluster_host": f"{cluster_identifier}.gke.{self.region}.gcp.com",
                "cluster_port": 443,
                "k8s_namespace": "default",
                "k8s_image": "python:3.11",
                "default_cores": 2,
                "default_memory": "4GB",
                "cost_monitoring": True,
                "provider": "gcp",
                "provider_config": {
                    "cluster_name": cluster_identifier,
                    "region": self.region,
                    "project_id": self.project_id,
                },
            }
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """Estimate GCP costs."""
        cluster_type = kwargs.get("cluster_type", "compute")
        machine_type = kwargs.get("machine_type", "e2-medium")
        hours = kwargs.get("hours", 1)

        # Simplified pricing - real implementation would use GCP Pricing API
        instance_prices = {
            "e2-micro": 0.0056,
            "e2-small": 0.0112,
            "e2-medium": 0.0225,
            "e2-standard-2": 0.0450,
            "e2-standard-4": 0.0900,
            "n1-standard-1": 0.0475,
            "n1-standard-2": 0.0950,
            "n1-standard-4": 0.1900,
            "c2-standard-4": 0.1892,
            "c2-standard-8": 0.3784,
        }

        base_price = instance_prices.get(machine_type, 0.05)  # Default price

        if cluster_type == "gke":
            # GKE has cluster management fee
            cluster_fee = 0.10 * hours  # $0.10/hour cluster management fee
            node_cost = base_price * hours
            total = cluster_fee + node_cost

            return {
                "cluster_management": cluster_fee,
                "nodes": node_cost,
                "total": total,
            }
        else:  # compute
            total = base_price * hours
            return {"instance": total, "total": total}

    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """Get available GCP machine types."""
        if not self.authenticated:
            # Return common machine types if not authenticated
            return [
                "e2-micro",
                "e2-small",
                "e2-medium",
                "e2-standard-2",
                "e2-standard-4",
                "n1-standard-1",
                "n1-standard-2",
                "n1-standard-4",
                "n2-standard-2",
                "n2-standard-4",
                "c2-standard-4",
            ]

        try:
            # Use specified region or current region
            query_region = region or self.region
            zone = f"{query_region}-a"  # Use first zone in region

            # Get machine types for the zone
            machine_types_client = compute_v1.MachineTypesClient(
                credentials=service_account.Credentials.from_service_account_info(
                    self.service_account_info
                )
            )

            machine_types = machine_types_client.list(
                project=self.project_id, zone=zone
            )

            # Extract machine type names and filter to common families
            all_types = [mt.name for mt in machine_types]

            # Filter to common machine families for better UX
            common_families = ["e2", "n1", "n2", "c2", "f1", "g1"]
            filtered_types = []

            for family in common_families:
                family_types = [t for t in all_types if t.startswith(family + "-")]
                # Sort by size (micro, small, medium, standard-1, standard-2, etc.)
                family_types.sort(
                    key=lambda x: (
                        "micro" in x
                        and 0
                        or "small" in x
                        and 1
                        or "medium" in x
                        and 2
                        or "standard" in x
                        and int(x.split("-")[-1])
                        if x.split("-")[-1].isdigit()
                        else 99
                    )
                )
                filtered_types.extend(family_types[:8])  # Limit to 8 per family

            return filtered_types[:30]  # Limit total to 30 for better UX

        except Exception as e:
            logger.warning(
                f"Failed to fetch machine types for region {query_region}: {e}"
            )
            # Return default list on error
            return [
                "e2-micro",
                "e2-small",
                "e2-medium",
                "e2-standard-2",
                "n1-standard-1",
                "n1-standard-2",
                "n1-standard-4",
                "c2-standard-4",
                "c2-standard-8",
            ]

    def get_available_regions(self) -> List[str]:
        """Get available GCP regions."""
        if not self.authenticated:
            # Return common regions if not authenticated
            return [
                "us-central1",
                "us-east1",
                "us-west1",
                "us-west2",
                "europe-west1",
                "europe-west2",
                "asia-southeast1",
                "asia-northeast1",
            ]

        try:
            # Get all available regions
            regions_client = compute_v1.RegionsClient(
                credentials=service_account.Credentials.from_service_account_info(
                    self.service_account_info
                )
            )

            regions = regions_client.list(project=self.project_id)
            region_names = [region.name for region in regions]
            region_names.sort()

            # Prioritize common regions
            priority_regions = [
                "us-central1",
                "us-east1",
                "us-west1",
                "us-west2",
                "europe-west1",
                "europe-west2",
                "asia-southeast1",
                "asia-northeast1",
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
            logger.warning(f"Failed to fetch GCP regions: {e}")
            return [
                "us-central1",
                "us-east1",
                "us-west1",
                "us-west2",
                "europe-west1",
                "europe-west2",
                "asia-southeast1",
                "asia-northeast1",
            ]


# Register the provider
if GCP_AVAILABLE:
    PROVIDERS["gcp"] = GCPProvider
