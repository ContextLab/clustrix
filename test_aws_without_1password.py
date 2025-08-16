#!/usr/bin/env python
"""Test AWS provisioning without triggering 1Password."""

import os
import sys

# Ensure 1Password is disabled
os.environ['CLUSTRIX_USE_1PASSWORD'] = 'false'

from clustrix import cluster, configure
from clustrix.credential_manager import FlexibleCredentialManager

def test_credential_loading():
    """Test that credentials load from .env without 1Password."""
    print("Testing credential loading...")
    
    # Create credential manager
    manager = FlexibleCredentialManager()
    
    # Check source priority
    print("\nCredential sources (in priority order):")
    for i, source in enumerate(manager.sources, 1):
        source_name = source.__class__.__name__
        is_available = source.is_available()
        print(f"  {i}. {source_name}: {'✅ Available' if is_available else '❌ Not available'}")
    
    # Load AWS credentials
    print("\nLoading AWS credentials...")
    aws_creds = manager.ensure_credential("aws")
    
    if aws_creds:
        print(f"✅ AWS credentials loaded successfully")
        print(f"   Access key starts with: {aws_creds['access_key_id'][:10]}...")
        print(f"   Region: {aws_creds.get('region', 'not set')}")
        return True
    else:
        print("❌ Failed to load AWS credentials")
        return False

def test_kubernetes_provisioner_init():
    """Test that Kubernetes provisioner can initialize without 1Password."""
    print("\nTesting Kubernetes provisioner initialization...")
    
    try:
        from clustrix.kubernetes.cluster_provisioner import KubernetesClusterProvisioner
        from clustrix.config import ClusterConfig
        
        # Create config with auto-provisioning
        config = ClusterConfig()
        config.cluster_type = "kubernetes"
        config.auto_provision_k8s = True
        config.k8s_provider = "aws"
        config.k8s_region = "us-west-2"
        config.use_1password = False  # Explicitly disable
        
        # Initialize provisioner
        provisioner = KubernetesClusterProvisioner(config)
        print("✅ Kubernetes provisioner initialized successfully")
        
        # Check if credentials are available
        credentials = provisioner._get_provider_credentials("aws")
        if credentials:
            print("✅ AWS credentials available for provisioning")
            return True
        else:
            print("❌ AWS credentials not available")
            return False
            
    except Exception as e:
        print(f"❌ Error initializing provisioner: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run tests."""
    print("=" * 60)
    print("Testing AWS Provisioning WITHOUT 1Password")
    print("=" * 60)
    
    # Verify 1Password is disabled
    from clustrix.config import get_config
    config = get_config()
    print(f"\n1Password status: {'ENABLED' if config.use_1password else 'DISABLED'}")
    
    if config.use_1password:
        print("⚠️  WARNING: 1Password is somehow enabled!")
        print("   This might trigger authentication popups")
    
    # Run tests
    success = True
    
    if not test_credential_loading():
        success = False
    
    if not test_kubernetes_provisioner_init():
        success = False
    
    # Summary
    print("\n" + "=" * 60)
    if success:
        print("✅ All tests passed - No 1Password popup should have appeared")
    else:
        print("❌ Some tests failed - Check output above")
    print("=" * 60)
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())