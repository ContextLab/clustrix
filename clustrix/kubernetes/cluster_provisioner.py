"""
Core Kubernetes cluster provisioning infrastructure.

Provides from-scratch Kubernetes cluster creation across supported cloud providers
with complete infrastructure setup and Clustrix integration.
"""

import logging
import time
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import ClusterConfig
from ..credential_manager import get_credential_manager

logger = logging.getLogger(__name__)


@dataclass
class ClusterSpec:
    """Specification for Kubernetes cluster provisioning."""

    provider: str  # aws, gcp, azure, huggingface, lambda
    cluster_name: str
    region: str
    node_count: int = 2
    node_type: Optional[str] = None
    kubernetes_version: str = "1.28"
    from_scratch: bool = True
    auto_cleanup: bool = True

    # Provider-specific configurations (defaults)
    aws_instance_type: str = "t3.medium"
    gcp_machine_type: str = "e2-standard-4"
    azure_vm_size: str = "Standard_D2s_v3"

    def __post_init__(self):
        """Set provider-specific node types based on generic node_type if provided."""
        if self.node_type:
            if self.provider == "aws":
                self.aws_instance_type = self.node_type
            elif self.provider == "gcp":
                self.gcp_machine_type = self.node_type
            elif self.provider == "azure":
                self.azure_vm_size = self.node_type


class BaseKubernetesProvisioner(ABC):
    """Abstract base class for Kubernetes cluster provisioners."""

    def __init__(self, credentials: Dict[str, str], region: str):
        self.credentials = credentials
        self.region = region
        self.cluster_info: Optional[Dict[str, Any]] = None

    @abstractmethod
    def provision_complete_infrastructure(self, spec: ClusterSpec) -> Dict[str, Any]:
        """Provision complete Kubernetes cluster infrastructure from scratch."""
        pass

    @abstractmethod
    def destroy_cluster_infrastructure(self, cluster_id: str) -> bool:
        """Destroy cluster and all associated infrastructure."""
        pass

    @abstractmethod
    def get_cluster_status(self, cluster_id: str) -> Dict[str, Any]:
        """Get detailed cluster status and health information."""
        pass

    @abstractmethod
    def validate_credentials(self) -> bool:
        """Validate that credentials have required permissions."""
        pass


class KubernetesClusterProvisioner:
    """
    Central orchestrator for Kubernetes cluster provisioning and management.

    This class provides the main interface for from-scratch Kubernetes cluster
    provisioning across all supported cloud providers. It handles credential
    management, provider selection, and cluster lifecycle operations.
    """

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.credential_manager = get_credential_manager()
        self._provisioners: Dict[str, BaseKubernetesProvisioner] = {}

    def provision_cluster_if_needed(self, cluster_spec: ClusterSpec) -> Dict[str, Any]:
        """
        Main entry point for cluster provisioning.

        Checks if a suitable cluster exists, otherwise provisions a new one
        from scratch with complete infrastructure setup.

        Args:
            cluster_spec: Complete specification for the desired cluster

        Returns:
            Dictionary containing cluster configuration ready for job execution
        """
        logger.info(
            f"🚀 Starting Kubernetes cluster provisioning for {cluster_spec.provider}"
        )

        try:
            # 1. Get and validate credentials
            credentials = self._get_provider_credentials(cluster_spec.provider)
            if not credentials:
                raise ValueError(
                    f"No credentials found for provider: {cluster_spec.provider}"
                )

            # 2. Initialize provider-specific provisioner
            provisioner = self._get_provisioner(
                cluster_spec.provider, credentials, cluster_spec.region
            )

            # 3. Validate credentials and permissions
            if not provisioner.validate_credentials():
                raise ValueError(
                    f"Invalid or insufficient credentials for {cluster_spec.provider}"
                )

            # 4. Check for existing cluster
            existing_cluster = self._find_existing_cluster(provisioner, cluster_spec)
            if existing_cluster:
                logger.info(
                    f"✅ Found existing cluster: {existing_cluster['cluster_id']}"
                )
                return existing_cluster

            # 5. Provision new cluster from scratch
            logger.info("🏗️ No suitable cluster found, provisioning from scratch...")
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)

            # 6. Verify cluster is ready for Clustrix jobs
            self._verify_cluster_ready(provisioner, cluster_info["cluster_id"])

            logger.info(
                f"✅ Cluster provisioning completed: {cluster_info['cluster_id']}"
            )
            return cluster_info

        except Exception as e:
            logger.error(f"❌ Cluster provisioning failed: {e}")
            raise

    def destroy_cluster(self, cluster_id: str, provider: str) -> bool:
        """
        Completely destroy cluster and all associated infrastructure.

        Args:
            cluster_id: Unique identifier for the cluster
            provider: Cloud provider (aws, gcp, azure, etc.)

        Returns:
            True if destruction was successful
        """
        logger.info(f"🧹 Destroying cluster: {cluster_id}")

        try:
            credentials = self._get_provider_credentials(provider)
            if not credentials:
                raise ValueError(f"No credentials available for provider: {provider}")

            region = self.config.k8s_region or "us-east-1"  # Default region
            provisioner = self._get_provisioner(provider, credentials, region)

            success = provisioner.destroy_cluster_infrastructure(cluster_id)

            if success:
                logger.info(f"✅ Cluster destroyed successfully: {cluster_id}")
            else:
                logger.error(f"❌ Failed to destroy cluster: {cluster_id}")

            return success

        except Exception as e:
            logger.error(f"❌ Error destroying cluster {cluster_id}: {e}")
            return False

    def list_clusters(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all managed Kubernetes clusters.

        Args:
            provider: Specific provider to list, or None for all providers

        Returns:
            List of cluster information dictionaries
        """
        clusters = []

        providers_to_check = (
            [provider] if provider else ["aws", "gcp", "azure", "huggingface", "lambda"]
        )

        for prov in providers_to_check:
            try:
                credentials = self._get_provider_credentials(prov)
                if not credentials:
                    continue

                region = self.config.k8s_region or "us-east-1"  # Default region
                provisioner = self._get_provisioner(prov, credentials, region)
                provider_clusters = self._list_provider_clusters(provisioner, prov)
                clusters.extend(provider_clusters)

            except Exception as e:
                logger.debug(f"Error listing clusters for {prov}: {e}")

        return clusters

    def _get_provider_credentials(self, provider: str) -> Optional[Dict[str, str]]:
        """Get credentials for specified Kubernetes provider with provider-specific mapping."""
        # Local providers don't need real credentials
        if provider in ["local", "local-docker"]:
            return {"type": "local"}

        return self.credential_manager.ensure_kubernetes_provider_credentials(provider)

    def _get_provisioner(
        self, provider: str, credentials: Dict[str, str], region: str
    ) -> BaseKubernetesProvisioner:
        """Get or create provider-specific provisioner."""
        provisioner_key = f"{provider}_{region}"

        if provisioner_key not in self._provisioners:
            if provider == "aws":
                from .aws_provisioner import AWSEKSFromScratchProvisioner

                self._provisioners[provisioner_key] = AWSEKSFromScratchProvisioner(
                    credentials, region
                )
            elif provider == "gcp":
                from .gcp_provisioner import GCPGKEFromScratchProvisioner

                self._provisioners[provisioner_key] = GCPGKEFromScratchProvisioner(
                    credentials, region
                )
            elif provider == "azure":
                from .azure_provisioner import AzureAKSFromScratchProvisioner

                self._provisioners[provisioner_key] = AzureAKSFromScratchProvisioner(
                    credentials, region
                )
            elif provider == "huggingface":
                from .huggingface_provisioner import HuggingFaceKubernetesProvisioner

                self._provisioners[provisioner_key] = HuggingFaceKubernetesProvisioner(
                    credentials, region
                )
            elif provider == "lambda":
                from .lambda_provisioner import LambdaCloudKubernetesProvisioner

                self._provisioners[provisioner_key] = LambdaCloudKubernetesProvisioner(
                    credentials, region
                )
            elif provider == "local" or provider == "local-docker":
                from .local_provisioner import LocalDockerKubernetesProvisioner

                self._provisioners[provisioner_key] = LocalDockerKubernetesProvisioner(
                    credentials, region
                )
            else:
                raise ValueError(f"Unsupported provider: {provider}")

        return self._provisioners[provisioner_key]

    def _find_existing_cluster(
        self, provisioner: BaseKubernetesProvisioner, spec: ClusterSpec
    ) -> Optional[Dict[str, Any]]:
        """Check for existing cluster that meets specifications."""
        try:
            # Implementation will check for clusters with matching tags/labels
            # For now, always provision new cluster
            return None
        except Exception as e:
            logger.debug(f"Error checking for existing clusters: {e}")
            return None

    def _verify_cluster_ready(
        self, provisioner: BaseKubernetesProvisioner, cluster_id: str
    ) -> bool:
        """Verify cluster is ready for Clustrix job execution."""
        max_attempts = 30
        wait_time = 30  # seconds

        for attempt in range(max_attempts):
            try:
                status = provisioner.get_cluster_status(cluster_id)
                if status.get("ready_for_jobs", False):
                    return True

                logger.info(
                    f"⏳ Cluster not ready yet, waiting... ({attempt + 1}/{max_attempts})"
                )
                time.sleep(wait_time)

            except Exception as e:
                logger.debug(f"Error checking cluster status: {e}")
                time.sleep(wait_time)

        raise RuntimeError(
            f"Cluster {cluster_id} not ready after {max_attempts * wait_time} seconds"
        )

    def _list_provider_clusters(
        self, provisioner: BaseKubernetesProvisioner, provider: str
    ) -> List[Dict[str, Any]]:
        """List clusters for specific provider."""
        # Implementation would call provider-specific cluster listing
        # For now, return empty list
        return []


# Convenience functions for direct usage


def provision_kubernetes_cluster(
    provider: str,
    cluster_name: str,
    region: str,
    node_count: int = 2,
    node_type: Optional[str] = None,
    kubernetes_version: str = "1.28",
    from_scratch: bool = True,
    config: Optional[ClusterConfig] = None,
) -> Dict[str, Any]:
    """
    Provision a Kubernetes cluster with specified configuration.

    This is a convenience function for direct cluster provisioning outside
    of the @cluster decorator workflow.

    Args:
        provider: Cloud provider (aws, gcp, azure, huggingface, lambda)
        cluster_name: Name for the cluster
        region: Cloud provider region
        node_count: Number of worker nodes
        node_type: Provider-specific instance type
        kubernetes_version: Kubernetes version to install
        from_scratch: Whether to create all infrastructure from scratch
        config: Optional ClusterConfig to use

    Returns:
        Dictionary containing cluster configuration

    Example:
        >>> cluster_config = provision_kubernetes_cluster(
        ...     provider="aws",
        ...     cluster_name="my-cluster",
        ...     region="us-west-2",
        ...     node_count=3,
        ...     node_type="t3.large"
        ... )
        >>> print(f"Cluster endpoint: {cluster_config['endpoint']}")
    """
    if config is None:
        config = ClusterConfig(
            k8s_provider=provider,
            k8s_region=region,
            k8s_node_count=node_count,
            k8s_version=kubernetes_version,
        )

    spec = ClusterSpec(
        provider=provider,
        cluster_name=cluster_name,
        region=region,
        node_count=node_count,
        node_type=node_type,
        kubernetes_version=kubernetes_version,
        from_scratch=from_scratch,
    )

    provisioner = KubernetesClusterProvisioner(config)
    return provisioner.provision_cluster_if_needed(spec)


def destroy_kubernetes_cluster(
    cluster_id: str, provider: str, config: Optional[ClusterConfig] = None
) -> bool:
    """
    Destroy a Kubernetes cluster and all associated infrastructure.

    Args:
        cluster_id: Unique identifier for the cluster
        provider: Cloud provider
        config: Optional ClusterConfig to use

    Returns:
        True if destruction was successful
    """
    if config is None:
        config = ClusterConfig(k8s_provider=provider)

    provisioner = KubernetesClusterProvisioner(config)
    return provisioner.destroy_cluster(cluster_id, provider)


def list_kubernetes_clusters(
    provider: Optional[str] = None, config: Optional[ClusterConfig] = None
) -> List[Dict[str, Any]]:
    """
    List all managed Kubernetes clusters.

    Args:
        provider: Specific provider to list, or None for all
        config: Optional ClusterConfig to use

    Returns:
        List of cluster information dictionaries
    """
    if config is None:
        config = ClusterConfig()

    provisioner = KubernetesClusterProvisioner(config)
    return provisioner.list_clusters(provider)
