#!/usr/bin/env python
"""Test real AWS EKS provisioning."""

import os
import sys
import time

# Ensure 1Password is disabled
os.environ["CLUSTRIX_USE_1PASSWORD"] = "false"

from clustrix import cluster, configure
from clustrix.kubernetes.cluster_provisioner import (
    KubernetesClusterProvisioner,
    ClusterSpec,
)
from clustrix.config import ClusterConfig


def test_aws_eks_provisioning():
    """Test actual AWS EKS cluster provisioning."""
    print("=" * 60)
    print("Testing AWS EKS Provisioning with REAL Credentials")
    print("=" * 60)

    # Configure for AWS
    config = ClusterConfig()
    config.cluster_type = "kubernetes"
    config.auto_provision_k8s = True
    config.k8s_provider = "aws"
    config.k8s_region = "us-west-2"
    config.use_1password = False  # Explicitly disable

    print(f"\nConfiguration:")
    print(f"  Provider: AWS")
    print(f"  Region: us-west-2")
    print(f"  1Password: DISABLED")

    # Create cluster specification
    cluster_name = f"clustrix-test-{int(time.time())}"
    spec = ClusterSpec(
        provider="aws",
        cluster_name=cluster_name,
        region="us-west-2",
        node_count=2,
        node_type="t3.medium",
        kubernetes_version="1.27",
        from_scratch=True,
    )

    print(f"\nCluster Specification:")
    print(f"  Name: {cluster_name}")
    print(f"  Nodes: 2 x t3.medium")
    print(f"  K8s Version: 1.27")

    try:
        # Initialize provisioner
        print("\n🚀 Initializing provisioner...")
        provisioner = KubernetesClusterProvisioner(config)

        # Check credentials
        print("\n🔑 Checking AWS credentials...")
        credentials = provisioner._get_provider_credentials("aws")
        if not credentials:
            print("❌ No AWS credentials available")
            return False

        print(f"✅ AWS credentials loaded")
        print(f"   Access key: {credentials.get('access_key_id', '')[:10]}...")

        # IMPORTANT: Ask for confirmation before spending money
        print("\n" + "⚠️ " * 20)
        print("WARNING: This will provision REAL AWS resources and incur costs!")
        print("Estimated cost: ~$0.10-0.20/hour for 2 x t3.medium nodes")
        print("⚠️ " * 20)

        response = input("\nDo you want to continue? (yes/no): ")
        if response.lower() != "yes":
            print("Provisioning cancelled by user")
            return False

        # Provision cluster
        print("\n🌟 Starting cluster provisioning...")
        print("This may take 10-15 minutes...")

        start_time = time.time()
        cluster_info = provisioner.provision_cluster_if_needed(spec)

        provision_time = time.time() - start_time

        print(f"\n✅ Cluster provisioned successfully in {provision_time:.1f} seconds!")
        print(f"\nCluster Information:")
        print(f"  ID: {cluster_info.get('cluster_id')}")
        print(f"  Status: {cluster_info.get('status')}")
        print(f"  Endpoint: {cluster_info.get('endpoint')}")
        print(f"  Nodes: {cluster_info.get('node_count')}")
        print(f"  Cost Estimate: ${cluster_info.get('cost_estimate', 0):.2f}/hour")

        # Test cluster connectivity
        print("\n🔧 Testing cluster connectivity...")
        status = provisioner._get_cluster_status(cluster_name)
        print(f"  Cluster ready: {status.get('ready_for_jobs', False)}")

        # Cleanup prompt
        print("\n" + "=" * 60)
        print("IMPORTANT: Remember to destroy this cluster when done!")
        print(
            f"Run: python -c \"from clustrix.kubernetes.cluster_provisioner import KubernetesClusterProvisioner; p = KubernetesClusterProvisioner(); p._destroy_cluster('{cluster_name}')\""
        )
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n❌ Provisioning failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run the test."""
    success = test_aws_eks_provisioning()

    if success:
        print("\n✅ AWS EKS provisioning test completed successfully!")
        print("   No 1Password popup should have appeared")
    else:
        print("\n❌ AWS EKS provisioning test failed")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
