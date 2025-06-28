"""Lambda Cloud provider integration for Clustrix."""

import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from .base import CloudProvider
from . import PROVIDERS

logger = logging.getLogger(__name__)


class LambdaCloudProvider(CloudProvider):
    """Lambda Cloud provider implementation."""

    def __init__(self):
        """Initialize Lambda Cloud provider."""
        super().__init__()
        self.api_key = None
        self.base_url = "https://cloud.lambdalabs.com/api/v1"
        self.session = None

    def authenticate(self, **credentials) -> bool:
        """
        Authenticate with Lambda Cloud.

        Args:
            **credentials: Lambda Cloud credentials including:
                - api_key: Lambda Cloud API key

        Returns:
            bool: True if authentication successful
        """
        api_key = credentials.get("api_key")

        if not api_key:
            logger.error("api_key is required")
            return False

        try:
            # Create session with API key
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
            )

            # Test credentials by getting account info
            response = self.session.get(f"{self.base_url}/instance-types")

            if response.status_code == 200:
                self.api_key = api_key
                self.credentials = credentials
                self.authenticated = True
                logger.info("Successfully authenticated with Lambda Cloud")
                return True
            elif response.status_code == 401:
                logger.error("Invalid Lambda Cloud API key")
                return False
            else:
                logger.error(
                    f"Lambda Cloud authentication failed: {response.status_code}"
                )
                return False

        except requests.RequestException as e:
            logger.error(f"Failed to connect to Lambda Cloud API: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during Lambda Cloud authentication: {e}")
            return False

    def validate_credentials(self) -> bool:
        """Validate current Lambda Cloud credentials."""
        if not self.authenticated or not self.session:
            return False

        try:
            response = self.session.get(f"{self.base_url}/instance-types")
            return response.status_code == 200
        except Exception:
            return False

    def create_instance(
        self,
        instance_name: str,
        instance_type: str = "gpu_1x_a10",
        region: str = "us-east-1",
        ssh_key_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a Lambda Cloud instance.

        Args:
            instance_name: Name for the instance
            instance_type: GPU instance type (e.g., gpu_1x_a10)
            region: Lambda Cloud region
            ssh_key_name: SSH key name for access

        Returns:
            Dict with instance information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Lambda Cloud")

        try:
            # Prepare instance creation request
            instance_data = {
                "region_name": region,
                "instance_type_name": instance_type,
                "ssh_key_names": [ssh_key_name] if ssh_key_name else [],
                "file_system_names": [],
                "quantity": 1,
                "name": instance_name,
            }

            # Create the instance
            response = self.session.post(
                f"{self.base_url}/instance-operations/launch", json=instance_data
            )

            if response.status_code == 200:
                result = response.json()
                instance_ids = result.get("instance_ids", [])

                if instance_ids:
                    instance_id = instance_ids[0]
                    logger.info(
                        f"Created Lambda Cloud instance '{instance_name}' with ID {instance_id}"
                    )

                    return {
                        "instance_name": instance_name,
                        "instance_id": instance_id,
                        "instance_type": instance_type,
                        "region": region,
                        "status": "booting",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                else:
                    raise RuntimeError("No instance ID returned from Lambda Cloud")
            else:
                error_msg = f"Failed to create instance: {response.status_code}"
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                ):
                    error_data = response.json()
                    error_msg += f" - {error_data.get('error', 'Unknown error')}"
                raise RuntimeError(error_msg)

        except requests.RequestException as e:
            logger.error(f"Failed to create Lambda Cloud instance: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating Lambda Cloud instance: {e}")
            raise

    def create_cluster(self, cluster_name: str, **kwargs) -> Dict[str, Any]:
        """Create a Lambda Cloud instance."""
        return self.create_instance(cluster_name, **kwargs)

    def delete_cluster(self, cluster_identifier: str) -> bool:
        """Delete a Lambda Cloud instance."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Lambda Cloud")

        try:
            # Terminate the instance
            response = self.session.post(
                f"{self.base_url}/instance-operations/terminate",
                json={"instance_ids": [cluster_identifier]},
            )

            if response.status_code == 200:
                logger.info(f"Terminated Lambda Cloud instance '{cluster_identifier}'")
                return True
            else:
                error_msg = f"Failed to terminate instance: {response.status_code}"
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                ):
                    error_data = response.json()
                    error_msg += f" - {error_data.get('error', 'Unknown error')}"
                logger.error(error_msg)
                return False

        except requests.RequestException as e:
            logger.error(f"Failed to terminate Lambda Cloud instance: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error terminating Lambda Cloud instance: {e}")
            return False

    def get_cluster_status(self, cluster_identifier: str) -> Dict[str, Any]:
        """Get status of a Lambda Cloud instance."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Lambda Cloud")

        try:
            # Get instance details
            response = self.session.get(
                f"{self.base_url}/instances/{cluster_identifier}"
            )

            if response.status_code == 200:
                instance_data = response.json()
                return {
                    "instance_id": cluster_identifier,
                    "status": instance_data.get("status", "unknown").lower(),
                    "instance_type": instance_data.get("instance_type", {}).get(
                        "name", "unknown"
                    ),
                    "region": instance_data.get("region", {}).get("name", "unknown"),
                    "provider": "lambda",
                    "cluster_type": "ssh",
                }
            elif response.status_code == 404:
                return {
                    "instance_id": cluster_identifier,
                    "status": "not_found",
                    "provider": "lambda",
                }
            else:
                raise RuntimeError(
                    f"Failed to get instance status: {response.status_code}"
                )

        except requests.RequestException as e:
            logger.error(f"Failed to get Lambda Cloud instance status: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting Lambda Cloud instance status: {e}")
            raise

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all Lambda Cloud instances."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Lambda Cloud")

        try:
            # List all instances
            response = self.session.get(f"{self.base_url}/instances")

            if response.status_code == 200:
                instances_data = response.json()
                instances = instances_data.get("data", [])

                clusters = []
                for instance in instances:
                    clusters.append(
                        {
                            "name": instance.get("name", instance.get("id", "unknown")),
                            "instance_id": instance.get("id"),
                            "type": "gpu",
                            "status": instance.get("status", "unknown").lower(),
                            "instance_type": instance.get("instance_type", {}).get(
                                "name", "unknown"
                            ),
                            "region": instance.get("region", {}).get("name", "unknown"),
                        }
                    )

                return clusters
            else:
                logger.error(f"Failed to list instances: {response.status_code}")
                return []

        except requests.RequestException as e:
            logger.error(f"Failed to list Lambda Cloud instances: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error listing Lambda Cloud instances: {e}")
            return []

    def get_cluster_config(self, cluster_identifier: str) -> Dict[str, Any]:
        """Get Clustrix configuration for a Lambda Cloud instance."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Lambda Cloud")

        try:
            # Get instance details
            response = self.session.get(
                f"{self.base_url}/instances/{cluster_identifier}"
            )

            if response.status_code == 200:
                instance_data = response.json()

                # Get public IP
                public_ip = ""
                ip_address = instance_data.get("ip")
                if ip_address:
                    public_ip = ip_address

                instance_type = instance_data.get("instance_type", {}).get(
                    "name", "unknown"
                )

                return {
                    "name": f"Lambda Cloud - {cluster_identifier}",
                    "cluster_type": "ssh",
                    "cluster_host": public_ip,
                    "username": "ubuntu",  # Default for Lambda Cloud instances
                    "cluster_port": 22,
                    "default_cores": 8,  # Lambda Cloud instances typically have high core counts
                    "default_memory": "32GB",  # GPU instances typically have large memory
                    "remote_work_dir": "/home/ubuntu/clustrix",
                    "package_manager": "conda",
                    "cost_monitoring": True,
                    "provider": "lambda",
                    "provider_config": {
                        "instance_id": cluster_identifier,
                        "instance_type": instance_type,
                        "region": instance_data.get("region", {}).get(
                            "name", "unknown"
                        ),
                    },
                }
            else:
                # Return basic config if instance details can't be retrieved
                return {
                    "name": f"Lambda Cloud - {cluster_identifier}",
                    "cluster_type": "ssh",
                    "cluster_host": "placeholder.lambdalabs.com",
                    "username": "ubuntu",
                    "provider": "lambda",
                }

        except Exception as e:
            logger.error(f"Failed to get Lambda Cloud instance config: {e}")
            # Return basic config on error
            return {
                "name": f"Lambda Cloud - {cluster_identifier}",
                "cluster_type": "ssh",
                "cluster_host": "placeholder.lambdalabs.com",
                "username": "ubuntu",
                "provider": "lambda",
            }

    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """Estimate Lambda Cloud costs."""
        instance_type = kwargs.get("instance_type", "gpu_1x_a10")
        hours = kwargs.get("hours", 1)

        # Lambda Cloud pricing (as of 2024) - prices per hour
        instance_prices = {
            "gpu_1x_a10": 0.75,
            "gpu_1x_a6000": 0.80,
            "gpu_1x_h100": 1.99,
            "gpu_1x_a100": 1.29,
            "gpu_2x_a10": 1.50,
            "gpu_2x_a6000": 1.60,
            "gpu_2x_a100": 2.58,
            "gpu_4x_a10": 3.00,
            "gpu_4x_a6000": 3.20,
            "gpu_4x_a100": 5.16,
            "gpu_8x_a100": 10.32,
            "gpu_8x_v100": 4.40,
        }

        base_price = instance_prices.get(instance_type, 1.0)  # Default price
        total = base_price * hours

        return {"gpu_instance": total, "total": total}

    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """Get available Lambda Cloud instance types."""
        if not self.authenticated:
            # Return common instance types if not authenticated
            return [
                "gpu_1x_a10",
                "gpu_1x_a6000",
                "gpu_1x_h100",
                "gpu_1x_a100",
                "gpu_2x_a10",
                "gpu_2x_a6000",
                "gpu_2x_a100",
                "gpu_4x_a10",
                "gpu_4x_a6000",
                "gpu_4x_a100",
                "gpu_8x_a100",
                "gpu_8x_v100",
            ]

        try:
            # Query Lambda Cloud API for available instance types
            response = self.session.get(f"{self.base_url}/instance-types")

            if response.status_code == 200:
                instance_types_data = response.json()
                instance_types = instance_types_data.get("data", [])

                # Extract instance type names
                available_types = []
                for instance_type in instance_types:
                    name = instance_type.get("name")
                    if name:
                        available_types.append(name)

                # Sort by GPU count and type for better UX
                def sort_key(instance_name):
                    # Extract GPU count from name (e.g., "gpu_1x_a10" -> 1)
                    parts = instance_name.split("_")
                    if len(parts) >= 2 and "x" in parts[1]:
                        try:
                            gpu_count = int(parts[1].split("x")[0])
                            return gpu_count
                        except ValueError:
                            return 999
                    return 999

                available_types.sort(key=sort_key)
                return available_types
            else:
                logger.warning(
                    f"Failed to fetch instance types: {response.status_code}"
                )
                # Return default list on error
                return [
                    "gpu_1x_a10",
                    "gpu_1x_a6000",
                    "gpu_1x_h100",
                    "gpu_1x_a100",
                    "gpu_2x_a10",
                    "gpu_2x_a6000",
                    "gpu_2x_a100",
                    "gpu_4x_a10",
                    "gpu_4x_a6000",
                    "gpu_4x_a100",
                    "gpu_8x_a100",
                    "gpu_8x_v100",
                ]

        except Exception as e:
            logger.warning(f"Failed to fetch Lambda Cloud instance types: {e}")
            # Return default list on error
            return [
                "gpu_1x_a10",
                "gpu_1x_a6000",
                "gpu_1x_h100",
                "gpu_1x_a100",
                "gpu_2x_a10",
                "gpu_2x_a6000",
                "gpu_2x_a100",
                "gpu_4x_a10",
                "gpu_4x_a6000",
                "gpu_4x_a100",
                "gpu_8x_a100",
                "gpu_8x_v100",
            ]

    def get_available_regions(self) -> List[str]:
        """Get available Lambda Cloud regions."""
        if not self.authenticated:
            # Lambda Cloud has limited regions
            return ["us-east-1", "us-west-1", "us-west-2"]

        try:
            # Lambda Cloud doesn't have a dedicated regions endpoint,
            # but we can get regions from instance types
            response = self.session.get(f"{self.base_url}/instance-types")

            if response.status_code == 200:
                instance_types_data = response.json()
                instance_types = instance_types_data.get("data", [])

                # Extract unique regions from instance types
                regions = set()
                for instance_type in instance_types:
                    regions_available = instance_type.get(
                        "regions_with_capacity_available", []
                    )
                    for region_info in regions_available:
                        if isinstance(region_info, dict) and "name" in region_info:
                            regions.add(region_info["name"])
                        elif isinstance(region_info, str):
                            regions.add(region_info)

                if regions:
                    return sorted(list(regions))
                else:
                    # Fallback to known regions
                    return ["us-east-1", "us-west-1", "us-west-2"]
            else:
                logger.warning(f"Failed to fetch regions: {response.status_code}")
                return ["us-east-1", "us-west-1", "us-west-2"]

        except Exception as e:
            logger.warning(f"Failed to fetch Lambda Cloud regions: {e}")
            return ["us-east-1", "us-west-1", "us-west-2"]


# Register the provider
PROVIDERS["lambda"] = LambdaCloudProvider
