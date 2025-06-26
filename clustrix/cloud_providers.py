"""
Cloud provider integration for remote Kubernetes cluster management.

This module provides automatic configuration and management of Kubernetes
clusters across major cloud providers (AWS EKS, Azure AKS, Google GKE).
"""

import os
import subprocess
import logging
from typing import Optional, Dict, Any
from .config import ClusterConfig

logger = logging.getLogger(__name__)


class CloudProviderError(Exception):
    """Exception raised for cloud provider configuration errors."""
    pass


class CloudProviderDetector:
    """Detect and configure cloud provider Kubernetes clusters."""
    
    @staticmethod
    def detect_provider() -> str:
        """
        Auto-detect cloud provider from environment.
        
        Returns:
            str: Detected cloud provider ('aws', 'azure', 'gcp', or 'manual')
        """
        # Check for AWS credentials/context
        if CloudProviderDetector._check_aws_context():
            return "aws"
        
        # Check for Azure credentials/context
        elif CloudProviderDetector._check_azure_context():
            return "azure"
        
        # Check for GCP credentials/context
        elif CloudProviderDetector._check_gcp_context():
            return "gcp"
        
        return "manual"
    
    @staticmethod
    def _check_aws_context() -> bool:
        """Check if AWS environment is configured."""
        # Check for AWS credentials
        if os.getenv('AWS_ACCESS_KEY_ID') or os.getenv('AWS_PROFILE'):
            return True
        
        # Check if AWS CLI is configured
        try:
            result = subprocess.run(
                ['aws', 'sts', 'get-caller-identity'], 
                capture_output=True, 
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    @staticmethod
    def _check_azure_context() -> bool:
        """Check if Azure environment is configured."""
        # Check for Azure environment variables
        if os.getenv('AZURE_SUBSCRIPTION_ID') or os.getenv('AZURE_TENANT_ID'):
            return True
        
        # Check if Azure CLI is logged in
        try:
            result = subprocess.run(
                ['az', 'account', 'show'], 
                capture_output=True, 
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    @staticmethod
    def _check_gcp_context() -> bool:
        """Check if GCP environment is configured."""
        # Check for GCP environment variables
        if os.getenv('GOOGLE_APPLICATION_CREDENTIALS') or os.getenv('GCLOUD_PROJECT'):
            return True
        
        # Check if gcloud is configured
        try:
            result = subprocess.run(
                ['gcloud', 'auth', 'list', '--filter=status:ACTIVE'], 
                capture_output=True, 
                timeout=10
            )
            return result.returncode == 0 and 'ACTIVE' in result.stdout.decode()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False


class AWSEKSConfigurator:
    """AWS EKS cluster configuration and management."""
    
    def __init__(self, config: ClusterConfig):
        self.config = config
    
    def configure_cluster(self, cluster_name: str, region: str) -> Dict[str, Any]:
        """
        Configure AWS EKS cluster access.
        
        Args:
            cluster_name: EKS cluster name
            region: AWS region
            
        Returns:
            Dict with cluster configuration details
        """
        try:
            # Update kubeconfig for EKS cluster
            cmd = [
                'aws', 'eks', 'update-kubeconfig',
                '--region', region,
                '--name', cluster_name
            ]
            
            if self.config.aws_profile:
                cmd.extend(['--profile', self.config.aws_profile])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                raise CloudProviderError(
                    f"Failed to configure EKS cluster: {result.stderr}"
                )
            
            # Verify cluster access
            self._verify_cluster_access()
            
            return {
                'provider': 'aws',
                'cluster_name': cluster_name,
                'region': region,
                'configured': True
            }
            
        except subprocess.TimeoutExpired:
            raise CloudProviderError("Timeout configuring EKS cluster")
        except Exception as e:
            raise CloudProviderError(f"EKS configuration failed: {e}")
    
    def _verify_cluster_access(self):
        """Verify that kubectl can access the cluster."""
        try:
            result = subprocess.run(
                ['kubectl', 'cluster-info'], 
                capture_output=True, 
                timeout=30
            )
            if result.returncode != 0:
                raise CloudProviderError("Cannot access Kubernetes cluster")
        except subprocess.TimeoutExpired:
            raise CloudProviderError("Timeout verifying cluster access")


class AzureAKSConfigurator:
    """Azure AKS cluster configuration and management."""
    
    def __init__(self, config: ClusterConfig):
        self.config = config
    
    def configure_cluster(self, cluster_name: str, resource_group: str) -> Dict[str, Any]:
        """
        Configure Azure AKS cluster access.
        
        Args:
            cluster_name: AKS cluster name
            resource_group: Azure resource group name
            
        Returns:
            Dict with cluster configuration details
        """
        try:
            # Get AKS credentials
            cmd = [
                'az', 'aks', 'get-credentials',
                '--resource-group', resource_group,
                '--name', cluster_name,
                '--overwrite-existing'
            ]
            
            if self.config.azure_subscription_id:
                cmd.extend(['--subscription', self.config.azure_subscription_id])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                raise CloudProviderError(
                    f"Failed to configure AKS cluster: {result.stderr}"
                )
            
            # Verify cluster access
            self._verify_cluster_access()
            
            return {
                'provider': 'azure',
                'cluster_name': cluster_name,
                'resource_group': resource_group,
                'configured': True
            }
            
        except subprocess.TimeoutExpired:
            raise CloudProviderError("Timeout configuring AKS cluster")
        except Exception as e:
            raise CloudProviderError(f"AKS configuration failed: {e}")
    
    def _verify_cluster_access(self):
        """Verify that kubectl can access the cluster."""
        try:
            result = subprocess.run(
                ['kubectl', 'cluster-info'], 
                capture_output=True, 
                timeout=30
            )
            if result.returncode != 0:
                raise CloudProviderError("Cannot access Kubernetes cluster")
        except subprocess.TimeoutExpired:
            raise CloudProviderError("Timeout verifying cluster access")


class GoogleGKEConfigurator:
    """Google GKE cluster configuration and management."""
    
    def __init__(self, config: ClusterConfig):
        self.config = config
    
    def configure_cluster(self, cluster_name: str, zone: str, project_id: str) -> Dict[str, Any]:
        """
        Configure Google GKE cluster access.
        
        Args:
            cluster_name: GKE cluster name
            zone: GCP zone (e.g., 'us-central1-a')
            project_id: GCP project ID
            
        Returns:
            Dict with cluster configuration details
        """
        try:
            # Get GKE credentials
            cmd = [
                'gcloud', 'container', 'clusters', 'get-credentials',
                cluster_name,
                '--zone', zone,
                '--project', project_id
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                raise CloudProviderError(
                    f"Failed to configure GKE cluster: {result.stderr}"
                )
            
            # Verify cluster access
            self._verify_cluster_access()
            
            return {
                'provider': 'gcp',
                'cluster_name': cluster_name,
                'zone': zone,
                'project_id': project_id,
                'configured': True
            }
            
        except subprocess.TimeoutExpired:
            raise CloudProviderError("Timeout configuring GKE cluster")
        except Exception as e:
            raise CloudProviderError(f"GKE configuration failed: {e}")
    
    def _verify_cluster_access(self):
        """Verify that kubectl can access the cluster."""
        try:
            result = subprocess.run(
                ['kubectl', 'cluster-info'], 
                capture_output=True, 
                timeout=30
            )
            if result.returncode != 0:
                raise CloudProviderError("Cannot access Kubernetes cluster")
        except subprocess.TimeoutExpired:
            raise CloudProviderError("Timeout verifying cluster access")


class CloudProviderManager:
    """Main manager for cloud provider operations."""
    
    def __init__(self, config: ClusterConfig):
        self.config = config
        self.detector = CloudProviderDetector()
    
    def auto_configure(self) -> Dict[str, Any]:
        """
        Automatically detect and configure cloud provider.
        
        Returns:
            Dict with configuration results
        """
        if not self.config.cloud_auto_configure:
            return {'auto_configured': False, 'reason': 'Auto-configuration disabled'}
        
        provider = self.config.cloud_provider
        if provider == "manual":
            provider = self.detector.detect_provider()
        
        if provider == "manual":
            return {
                'auto_configured': False, 
                'reason': 'No cloud provider detected'
            }
        
        try:
            if provider == "aws":
                return self._configure_aws()
            elif provider == "azure":
                return self._configure_azure()
            elif provider == "gcp":
                return self._configure_gcp()
            else:
                return {
                    'auto_configured': False, 
                    'reason': f'Unsupported provider: {provider}'
                }
        except Exception as e:
            logger.error(f"Auto-configuration failed: {e}")
            return {
                'auto_configured': False, 
                'error': str(e)
            }
    
    def _configure_aws(self) -> Dict[str, Any]:
        """Configure AWS EKS."""
        if not self.config.eks_cluster_name or not self.config.cloud_region:
            return {
                'auto_configured': False,
                'reason': 'Missing EKS cluster name or region'
            }
        
        configurator = AWSEKSConfigurator(self.config)
        result = configurator.configure_cluster(
            self.config.eks_cluster_name,
            self.config.cloud_region
        )
        result['auto_configured'] = True
        return result
    
    def _configure_azure(self) -> Dict[str, Any]:
        """Configure Azure AKS."""
        if not self.config.aks_cluster_name or not self.config.azure_resource_group:
            return {
                'auto_configured': False,
                'reason': 'Missing AKS cluster name or resource group'
            }
        
        configurator = AzureAKSConfigurator(self.config)
        result = configurator.configure_cluster(
            self.config.aks_cluster_name,
            self.config.azure_resource_group
        )
        result['auto_configured'] = True
        return result
    
    def _configure_gcp(self) -> Dict[str, Any]:
        """Configure Google GKE."""
        if not all([self.config.gke_cluster_name, self.config.gcp_zone, self.config.gcp_project_id]):
            return {
                'auto_configured': False,
                'reason': 'Missing GKE cluster name, zone, or project ID'
            }
        
        configurator = GoogleGKEConfigurator(self.config)
        result = configurator.configure_cluster(
            self.config.gke_cluster_name,
            self.config.gcp_zone,
            self.config.gcp_project_id
        )
        result['auto_configured'] = True
        return result