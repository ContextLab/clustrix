"""Base class for cloud provider integrations."""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


class CloudProvider(ABC):
    """Abstract base class for cloud provider integrations."""

    def __init__(self):
        """Initialize the cloud provider."""
        self.authenticated = False
        self.credentials = {}

    @abstractmethod
    def authenticate(self, **credentials) -> bool:
        """
        Authenticate with the cloud provider.

        Args:
            **credentials: Provider-specific credentials

        Returns:
            bool: True if authentication successful
        """
        pass

    @abstractmethod
    def validate_credentials(self) -> bool:
        """
        Validate that current credentials are valid.

        Returns:
            bool: True if credentials are valid
        """
        pass

    @abstractmethod
    def create_cluster(self, cluster_name: str, **kwargs) -> Dict[str, Any]:
        """
        Create a new cluster.

        Args:
            cluster_name: Name for the cluster
            **kwargs: Provider-specific cluster configuration

        Returns:
            Dict containing cluster information
        """
        pass

    @abstractmethod
    def delete_cluster(self, cluster_identifier: str) -> bool:
        """
        Delete a cluster.

        Args:
            cluster_identifier: Cluster name or ID

        Returns:
            bool: True if deletion successful
        """
        pass

    @abstractmethod
    def get_cluster_status(self, cluster_identifier: str) -> Dict[str, Any]:
        """
        Get current status of a cluster.

        Args:
            cluster_identifier: Cluster name or ID

        Returns:
            Dict containing cluster status information
        """
        pass

    @abstractmethod
    def list_clusters(self) -> List[Dict[str, Any]]:
        """
        List all clusters for the authenticated account.

        Returns:
            List of cluster information dictionaries
        """
        pass

    @abstractmethod
    def get_cluster_config(self, cluster_identifier: str) -> Dict[str, Any]:
        """
        Get Clustrix configuration for connecting to a cluster.

        Args:
            cluster_identifier: Cluster name or ID

        Returns:
            Dict containing Clustrix configuration
        """
        pass

    @abstractmethod
    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """
        Estimate cost for given configuration.

        Args:
            **kwargs: Provider-specific configuration

        Returns:
            Dict with cost breakdown
        """
        pass

    @abstractmethod
    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """
        Get list of available instance types for the provider.

        Args:
            region: Optional region to filter instance types

        Returns:
            List of available instance type names
        """
        pass

    @abstractmethod
    def get_available_regions(self) -> List[str]:
        """
        Get list of available regions for the provider.

        Returns:
            List of available region names
        """
        pass

    def is_authenticated(self) -> bool:
        """Check if provider is authenticated."""
        return self.authenticated
