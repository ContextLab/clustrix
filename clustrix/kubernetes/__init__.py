"""
Kubernetes cluster provisioning and management for Clustrix.

This module provides from-scratch Kubernetes cluster provisioning across
supported cloud providers with complete infrastructure setup and Clustrix
integration.
"""

from .cluster_provisioner import (
    KubernetesClusterProvisioner,
    ClusterSpec,
    provision_kubernetes_cluster,
    destroy_kubernetes_cluster,
    list_kubernetes_clusters,
)

# Provider-specific provisioners (imported lazily to avoid dependency issues)
__all__ = [
    "KubernetesClusterProvisioner",
    "ClusterSpec",
    "provision_kubernetes_cluster",
    "destroy_kubernetes_cluster",
    "list_kubernetes_clusters",
]


# Lazy imports for optional provider dependencies
def _get_aws_provisioner():
    """Lazy import for AWS EKS provisioner."""
    from .aws_provisioner import AWSEKSFromScratchProvisioner

    return AWSEKSFromScratchProvisioner


def _get_gcp_provisioner():
    """Lazy import for GCP GKE provisioner."""
    from .gcp_provisioner import GCPGKEFromScratchProvisioner

    return GCPGKEFromScratchProvisioner


def _get_azure_provisioner():
    """Lazy import for Azure AKS provisioner."""
    from .azure_provisioner import AzureAKSFromScratchProvisioner

    return AzureAKSFromScratchProvisioner


def _get_huggingface_provisioner():
    """Lazy import for HuggingFace Spaces adapter."""
    from .huggingface_provisioner import HuggingFaceKubernetesProvisioner

    return HuggingFaceKubernetesProvisioner


def _get_lambda_provisioner():
    """Lazy import for Lambda Cloud adapter."""
    from .lambda_provisioner import LambdaCloudKubernetesProvisioner

    return LambdaCloudKubernetesProvisioner
