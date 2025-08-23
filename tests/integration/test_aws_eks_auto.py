#!/usr/bin/env python
"""
Automatic test for AWS EKS provisioning (no confirmation prompt).
This will create a real EKS cluster - costs will be incurred!
"""

import sys
import time
import traceback
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager


def test_aws_eks_provisioning():
    """Test AWS EKS cluster provisioning with minimal resources."""

    print("=" * 60)
    print("AWS EKS Provisioning Test (AUTOMATIC)")
    print("=" * 60)
    print("\n⚠️  WARNING: Creating real AWS resources!")
    print("   Estimated cost: ~$0.28/hour")
    print("   Resources: EKS cluster, VPC, subnets, IAM roles")
    print("\n" + "=" * 60)

    print("\n✅ Starting AWS EKS provisioning...")

    # Get credentials
    credential_manager = FlexibleCredentialManager()
    aws_creds = credential_manager.ensure_credential("aws")

    if not aws_creds:
        print("❌ No AWS credentials found")
        return False

    print(f"   Using AWS account: {aws_creds.get('account_id', 'unknown')}")
    print(f"   Region: {aws_creds.get('region', 'us-east-1')}")

    # Create minimal cluster spec
    spec = ClusterSpec(
        cluster_name=f"clustrix-test-{int(time.time())}",
        provider="aws",
        region=aws_creds.get("region", "us-east-1"),
        node_count=1,  # Minimal - just 1 node
        node_type="t3.small",  # Smaller instance to save costs
        kubernetes_version="1.27",
    )

    print(f"\n📋 Cluster Configuration:")
    print(f"   Name: {spec.cluster_name}")
    print(f"   Nodes: {spec.node_count} x {spec.node_type}")
    print(f"   Region: {spec.region}")

    # Create provisioner
    print("\n🔧 Initializing provisioner...")
    try:
        provisioner = AWSEKSFromScratchProvisioner(aws_creds, spec.region)
        provisioner.spec = spec  # Set the spec after initialization
    except Exception as e:
        print(f"❌ Failed to initialize provisioner: {e}")
        traceback.print_exc()
        return False

    try:
        print("\n🚀 Creating EKS cluster...")
        print("   Step 1: Creating VPC and networking...")
        print("   Step 2: Creating IAM roles...")
        print("   Step 3: Creating EKS cluster...")
        print("   Step 4: Creating node group...")
        print("\n   This will take 10-15 minutes...\n")

        # Start provisioning
        cluster_info = provisioner.provision_complete_infrastructure(spec)

        if cluster_info:
            print("\n✅ Cluster created successfully!")
            print(f"   Cluster Name: {cluster_info.get('name')}")
            print(f"   Endpoint: {cluster_info.get('endpoint')}")
            print(f"   Status: {cluster_info.get('status')}")

            # Save cluster info for cleanup
            filename = f"cluster_info_{spec.cluster_name}.txt"
            with open(filename, "w") as f:
                f.write(f"Cluster Name: {spec.cluster_name}\n")
                f.write(f"Region: {spec.region}\n")
                f.write(f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Endpoint: {cluster_info.get('endpoint', 'N/A')}\n")
                f.write("\nTo destroy this cluster, run:\n")
                f.write(
                    f"python destroy_cluster.py {spec.cluster_name} {spec.region}\n"
                )

            print(f"\n📝 Cluster info saved to: {filename}")
            print("\n⚠️  IMPORTANT: Remember to destroy the cluster when done!")
            print(
                f"   Run: python destroy_cluster.py {spec.cluster_name} {spec.region}"
            )

            # Test cluster connectivity
            print("\n🔍 Testing cluster connectivity...")
            try:
                kubeconfig = provisioner.get_kubeconfig()
                if kubeconfig:
                    print("   ✅ Kubeconfig retrieved successfully")
                else:
                    print("   ⚠️  Could not retrieve kubeconfig")
            except Exception as e:
                print(f"   ⚠️  Error getting kubeconfig: {e}")

            return True
        else:
            print("\n❌ Cluster creation failed (no cluster info returned)")
            return False

    except KeyboardInterrupt:
        print("\n\n⚠️  Provisioning interrupted!")
        print("   Check AWS console for any resources that need cleanup")
        print(f"   Cluster name was: {spec.cluster_name}")
        return False
    except Exception as e:
        print(f"\n❌ Error during provisioning: {e}")
        print("\nFull error details:")
        traceback.print_exc()
        print("\n📋 Troubleshooting:")
        print("   1. Check AWS Console for partial resources")
        print("   2. Review CloudFormation stacks if any were created")
        print("   3. Check IAM roles and VPC resources")
        print(f"   4. Look for resources tagged with: {spec.cluster_name}")
        return False


if __name__ == "__main__":
    print("🚀 Starting automatic EKS provisioning test...")
    print("   (No confirmation required - will proceed automatically)")
    print("")

    success = test_aws_eks_provisioning()

    if success:
        print("\n" + "=" * 60)
        print("✅ TEST SUCCESSFUL!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ TEST FAILED!")
        print("=" * 60)

    sys.exit(0 if success else 1)
