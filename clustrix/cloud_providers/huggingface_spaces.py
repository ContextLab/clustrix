"""HuggingFace Spaces provider integration for Clustrix."""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

try:
    from huggingface_hub import HfApi, SpaceHardware
    from huggingface_hub.utils import HfHubHTTPError

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    HfApi = None  # type: ignore
    SpaceHardware = None  # type: ignore
    HfHubHTTPError = Exception  # type: ignore

from .base import CloudProvider
from . import PROVIDERS

logger = logging.getLogger(__name__)


class HuggingFaceSpacesProvider(CloudProvider):
    """HuggingFace Spaces provider implementation."""

    def __init__(self):
        """Initialize HuggingFace Spaces provider."""
        super().__init__()
        self.api_token = None
        self.username = None
        self.api = None

    def authenticate(self, **credentials) -> bool:
        """
        Authenticate with HuggingFace Hub.

        Args:
            **credentials: HuggingFace credentials including:
                - token: HuggingFace API token
                - username: HuggingFace username

        Returns:
            bool: True if authentication successful
        """
        token = credentials.get("token")
        username = credentials.get("username")

        if not token:
            logger.error("token is required")
            return False

        if not username:
            logger.error("username is required for HuggingFace Spaces")
            return False

        if not HF_AVAILABLE:
            logger.error(
                "huggingface_hub is not installed. Install with: pip install huggingface_hub"
            )
            return False

        try:
            # Create HuggingFace API client
            self.api = HfApi(token=token)

            # Test credentials by getting user info
            user_info = self.api.whoami()

            if user_info and user_info.get("name") == username:
                self.api_token = token
                self.username = username
                self.credentials = credentials
                self.authenticated = True
                logger.info(
                    f"Successfully authenticated with HuggingFace as {username}"
                )
                return True
            else:
                logger.error("Invalid HuggingFace credentials or username mismatch")
                return False

        except HfHubHTTPError as e:
            logger.error(f"HuggingFace authentication failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during HuggingFace authentication: {e}")
            return False

    def validate_credentials(self) -> bool:
        """Validate current HuggingFace credentials."""
        if not self.authenticated or not self.api:
            return False

        try:
            user_info = self.api.whoami()
            return user_info is not None
        except Exception:
            return False

    def create_space(
        self,
        space_name: str,
        hardware: str = "cpu-basic",
        sdk: str = "gradio",
        private: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a HuggingFace Space.

        Args:
            space_name: Name for the space
            hardware: Hardware tier (cpu-basic, cpu-upgrade, t4-small, t4-medium, a10g-small, etc.)
            sdk: SDK type (gradio, streamlit, docker)
            private: Whether the space should be private

        Returns:
            Dict with space information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with HuggingFace")

        try:
            # Create the space
            space_id = f"{self.username}/{space_name}"

            # Create space with basic configuration
            space_url = self.api.create_repo(
                repo_id=space_id, repo_type="space", space_sdk=sdk, private=private
            )

            # Set hardware if not cpu-basic
            if hardware != "cpu-basic":
                try:
                    # Map hardware string to SpaceHardware enum
                    hardware_map = {
                        "cpu-upgrade": SpaceHardware.CPU_UPGRADE,
                        "t4-small": SpaceHardware.T4_SMALL,
                        "t4-medium": SpaceHardware.T4_MEDIUM,
                        "a10g-small": SpaceHardware.A10G_SMALL,
                        "a10g-large": SpaceHardware.A10G_LARGE,
                        "a100-large": SpaceHardware.A100_LARGE,
                    }

                    if hardware in hardware_map:
                        self.api.request_space_hardware(
                            repo_id=space_id, hardware=hardware_map[hardware]
                        )
                        logger.info(
                            f"Requested {hardware} hardware for space {space_id}"
                        )
                    else:
                        logger.warning(
                            f"Unknown hardware type: {hardware}, using cpu-basic"
                        )
                        hardware = "cpu-basic"

                except Exception as e:
                    logger.warning(f"Failed to set hardware for space: {e}")
                    hardware = "cpu-basic"

            logger.info(f"Created HuggingFace Space '{space_id}' with {sdk} SDK")

            return {
                "space_name": space_name,
                "space_id": space_id,
                "space_url": space_url,
                "sdk": sdk,
                "hardware": hardware,
                "private": private,
                "status": "creating",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except HfHubHTTPError as e:
            logger.error(f"Failed to create HuggingFace Space: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating HuggingFace Space: {e}")
            raise

    def create_cluster(self, cluster_name: str, **kwargs) -> Dict[str, Any]:
        """Create a HuggingFace Space."""
        return self.create_space(cluster_name, **kwargs)

    def delete_cluster(self, cluster_identifier: str) -> bool:
        """Delete a HuggingFace Space."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with HuggingFace")

        try:
            # Delete the space repository
            self.api.delete_repo(repo_id=cluster_identifier, repo_type="space")

            logger.info(f"Deleted HuggingFace Space '{cluster_identifier}'")
            return True

        except HfHubHTTPError as e:
            logger.error(f"Failed to delete HuggingFace Space: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting HuggingFace Space: {e}")
            return False

    def get_cluster_status(self, cluster_identifier: str) -> Dict[str, Any]:
        """Get status of a HuggingFace Space."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with HuggingFace")

        try:
            # Get space info
            space_info = self.api.space_info(cluster_identifier)

            # Get space runtime info
            try:
                runtime = self.api.get_space_runtime(cluster_identifier)
                stage = runtime.stage if runtime else "unknown"
                hardware = runtime.hardware if runtime else "unknown"
            except Exception:
                stage = "unknown"
                hardware = "unknown"

            return {
                "space_id": cluster_identifier,
                "status": stage.lower() if stage != "unknown" else "unknown",
                "hardware": hardware,
                "sdk": space_info.sdk if space_info else "unknown",
                "provider": "huggingface",
                "cluster_type": "spaces",
            }

        except HfHubHTTPError as e:
            if "404" in str(e):
                return {
                    "space_id": cluster_identifier,
                    "status": "not_found",
                    "provider": "huggingface",
                }
            logger.error(f"Failed to get HuggingFace Space status: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting HuggingFace Space status: {e}")
            raise

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all HuggingFace Spaces."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with HuggingFace")

        try:
            # List user spaces
            spaces = self.api.list_spaces(author=self.username)

            clusters = []
            for space in spaces:
                # Get runtime info for each space
                try:
                    runtime = self.api.get_space_runtime(space.id)
                    stage = runtime.stage if runtime else "unknown"
                    hardware = runtime.hardware if runtime else "unknown"
                except Exception:
                    stage = "unknown"
                    hardware = "unknown"

                clusters.append(
                    {
                        "name": space.id.split("/")[
                            -1
                        ],  # Get space name without username
                        "space_id": space.id,
                        "type": "space",
                        "status": stage.lower() if stage != "unknown" else "unknown",
                        "sdk": space.sdk,
                        "hardware": hardware,
                        "private": space.private,
                    }
                )

            return clusters

        except HfHubHTTPError as e:
            logger.error(f"Failed to list HuggingFace Spaces: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error listing HuggingFace Spaces: {e}")
            return []

    def get_cluster_config(self, cluster_identifier: str) -> Dict[str, Any]:
        """Get Clustrix configuration for a HuggingFace Space."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with HuggingFace")

        try:
            # Get space info
            space_info = self.api.space_info(cluster_identifier)

            # Get space runtime info
            try:
                runtime = self.api.get_space_runtime(cluster_identifier)
                hardware = runtime.hardware if runtime else "cpu-basic"
            except Exception:
                hardware = "cpu-basic"

            # Generate space URL
            space_url = f"https://huggingface.co/spaces/{cluster_identifier}"

            return {
                "name": f"HuggingFace Space - {cluster_identifier}",
                "cluster_type": "api",  # Spaces are accessed via HTTP API
                "cluster_host": space_url,
                "api_endpoint": f"{space_url}/api/predict",
                "default_cores": self._hardware_to_cores(hardware),
                "default_memory": self._hardware_to_memory(hardware),
                "cost_monitoring": True,
                "provider": "huggingface",
                "provider_config": {
                    "space_id": cluster_identifier,
                    "hardware": hardware,
                    "sdk": space_info.sdk if space_info else "unknown",
                    "api_token": "***",  # Don't expose token
                },
            }

        except Exception as e:
            logger.error(f"Failed to get HuggingFace Space config: {e}")
            # Return basic config on error
            return {
                "name": f"HuggingFace Space - {cluster_identifier}",
                "cluster_type": "api",
                "cluster_host": f"https://huggingface.co/spaces/{cluster_identifier}",
                "provider": "huggingface",
            }

    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """Estimate HuggingFace Spaces costs."""
        hardware = kwargs.get("hardware", "cpu-basic")
        hours = kwargs.get("hours", 1)

        # HuggingFace Spaces pricing (as of 2024) - prices per hour
        hardware_prices = {
            "cpu-basic": 0.0,  # Free tier
            "cpu-upgrade": 0.03,  # $0.03/hour
            "t4-small": 0.60,  # $0.60/hour
            "t4-medium": 0.90,  # $0.90/hour
            "a10g-small": 1.05,  # $1.05/hour
            "a10g-large": 3.15,  # $3.15/hour
            "a100-large": 4.13,  # $4.13/hour
        }

        base_price = hardware_prices.get(hardware, 0.0)  # Default to free
        total = base_price * hours

        return {"compute": total, "total": total}

    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """Get available HuggingFace Spaces hardware options."""
        # HuggingFace Spaces hardware options
        return [
            "cpu-basic",
            "cpu-upgrade",
            "t4-small",
            "t4-medium",
            "a10g-small",
            "a10g-large",
            "a100-large",
        ]

    def get_available_regions(self) -> List[str]:
        """Get available HuggingFace Spaces regions."""
        # HuggingFace Spaces are globally available
        return ["global"]

    def _hardware_to_cores(self, hardware: str) -> int:
        """Map hardware type to CPU cores."""
        hardware_cores = {
            "cpu-basic": 2,
            "cpu-upgrade": 8,
            "t4-small": 4,
            "t4-medium": 8,
            "a10g-small": 4,
            "a10g-large": 12,
            "a100-large": 12,
        }
        return hardware_cores.get(hardware, 2)

    def _hardware_to_memory(self, hardware: str) -> str:
        """Map hardware type to memory."""
        hardware_memory = {
            "cpu-basic": "16GB",
            "cpu-upgrade": "32GB",
            "t4-small": "15GB",
            "t4-medium": "15GB",
            "a10g-small": "24GB",
            "a10g-large": "96GB",
            "a100-large": "142GB",
        }
        return hardware_memory.get(hardware, "16GB")


# Register the provider
if HF_AVAILABLE:
    PROVIDERS["huggingface"] = HuggingFaceSpacesProvider
