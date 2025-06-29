"""Automatic dependency installation for cloud providers."""

import subprocess
import sys
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Define cloud provider dependency mappings
CLOUD_PROVIDER_DEPS: Dict[str, List[str]] = {
    "aws": [
        "boto3>=1.26.0",
        "kubernetes>=20.13.0",
    ],
    "azure": [
        "azure-identity>=1.12.0",
        "azure-mgmt-compute>=30.0.0",
        "azure-mgmt-containerservice>=20.0.0",
        "azure-mgmt-resource>=23.0.0",
        "azure-mgmt-network>=25.0.0",
        "kubernetes>=20.13.0",
    ],
    "gcp": [
        "google-cloud-compute>=1.11.0",
        "google-cloud-container>=2.15.0",
        "google-auth>=2.15.0",
        "kubernetes>=20.13.0",
    ],
    "kubernetes": [
        "kubernetes>=20.13.0",
    ],
    "lambda_cloud": [
        "requests>=2.25.0",  # Already in main requirements
    ],
    "huggingface_spaces": [
        "huggingface_hub>=0.16.0",  # Already in main requirements
    ],
}

# Cluster types that require cloud providers
CLUSTER_TYPE_TO_PROVIDER: Dict[str, str] = {
    "kubernetes": "kubernetes",
    "aws_ec2": "aws",
    "aws_eks": "aws", 
    "azure_vm": "azure",
    "azure_aks": "azure",
    "gcp_vm": "gcp",
    "gcp_gke": "gcp",
    "lambda_cloud": "lambda_cloud",
    "huggingface_spaces": "huggingface_spaces",
}


def check_dependencies_installed(provider: str) -> bool:
    """
    Check if dependencies for a cloud provider are installed.
    
    Args:
        provider: Cloud provider name (aws, azure, gcp, etc.)
    
    Returns:
        True if all dependencies are installed, False otherwise
    """
    if provider not in CLOUD_PROVIDER_DEPS:
        return True  # No special dependencies needed
    
    # Try importing key modules for each provider
    try:
        if provider == "aws":
            import boto3  # noqa: F401
        elif provider == "azure":
            from azure.identity import ClientSecretCredential  # noqa: F401
            from azure.mgmt.compute import ComputeManagementClient  # noqa: F401
        elif provider == "gcp":
            from google.cloud import compute_v1  # noqa: F401
            from google.cloud import container_v1  # noqa: F401
        elif provider == "kubernetes":
            import kubernetes  # noqa: F401
        
        return True
    except ImportError:
        return False


def install_provider_dependencies(
    provider: str, 
    auto_install: bool = True,
    quiet: bool = False
) -> bool:
    """
    Install dependencies for a cloud provider.
    
    Args:
        provider: Cloud provider name
        auto_install: If True, install automatically. If False, just check.
        quiet: If True, suppress output messages
    
    Returns:
        True if dependencies are available, False otherwise
    """
    if provider not in CLOUD_PROVIDER_DEPS:
        return True  # No special dependencies needed
    
    # Check if already installed
    if check_dependencies_installed(provider):
        return True
    
    if not auto_install:
        if not quiet:
            deps = CLOUD_PROVIDER_DEPS[provider]
            logger.warning(
                f"Missing dependencies for {provider} provider. "
                f"Install with: pip install {' '.join(deps)}"
            )
        return False
    
    # Install dependencies
    deps_to_install = CLOUD_PROVIDER_DEPS[provider]
    
    if not quiet:
        logger.info(f"Installing {provider} dependencies: {', '.join(deps_to_install)}")
    
    try:
        cmd = [sys.executable, "-m", "pip", "install"] + deps_to_install
        if quiet:
            cmd.append("--quiet")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        if not quiet:
            logger.info(f"Successfully installed {provider} dependencies")
        
        return True
        
    except subprocess.CalledProcessError as e:
        if not quiet:
            logger.error(f"Failed to install {provider} dependencies: {e}")
            if e.stderr:
                logger.error(f"Error output: {e.stderr}")
        return False
    except Exception as e:
        if not quiet:
            logger.error(f"Unexpected error installing {provider} dependencies: {e}")
        return False


def ensure_cloud_provider_dependencies(
    cluster_type: Optional[str] = None,
    cloud_provider: Optional[str] = None,
    auto_install: bool = True,
    quiet: bool = False
) -> bool:
    """
    Ensure cloud provider dependencies are available for a given configuration.
    
    Args:
        cluster_type: Cluster type (kubernetes, aws_ec2, etc.)
        cloud_provider: Cloud provider (aws, azure, gcp, etc.)
        auto_install: Whether to automatically install missing dependencies
        quiet: Whether to suppress output messages
    
    Returns:
        True if all required dependencies are available, False otherwise
    """
    # Determine which provider to check
    provider_to_check = None
    
    if cloud_provider and cloud_provider != "manual":
        provider_to_check = cloud_provider
    elif cluster_type and cluster_type in CLUSTER_TYPE_TO_PROVIDER:
        provider_to_check = CLUSTER_TYPE_TO_PROVIDER[cluster_type]
    
    if not provider_to_check:
        return True  # No cloud provider dependencies needed
    
    return install_provider_dependencies(
        provider_to_check,
        auto_install=auto_install,
        quiet=quiet
    )


def get_installation_command(cluster_type: Optional[str] = None, cloud_provider: Optional[str] = None) -> Optional[str]:
    """
    Get the pip install command for missing dependencies.
    
    Args:
        cluster_type: Cluster type
        cloud_provider: Cloud provider
    
    Returns:
        Pip install command string, or None if no dependencies needed
    """
    provider_to_check = None
    
    if cloud_provider and cloud_provider != "manual":
        provider_to_check = cloud_provider
    elif cluster_type and cluster_type in CLUSTER_TYPE_TO_PROVIDER:
        provider_to_check = CLUSTER_TYPE_TO_PROVIDER[cluster_type]
    
    if not provider_to_check or provider_to_check not in CLOUD_PROVIDER_DEPS:
        return None
    
    deps = CLOUD_PROVIDER_DEPS[provider_to_check]
    return f"pip install {' '.join(deps)}"