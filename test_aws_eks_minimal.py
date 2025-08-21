#!/usr/bin/env python
"""
Minimal test for AWS EKS provisioning.
This will create a real EKS cluster - costs will be incurred!
"""

import sys
import time
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager

def test_aws_eks_provisioning():
    """Test AWS EKS cluster provisioning with minimal resources."""
    
    print("=" * 60)
    print("AWS EKS Provisioning Test (MINIMAL)")
    print("=" * 60)
    print("\n⚠️  WARNING: This will create real AWS resources!")
    print("   Estimated cost: ~$0.28/hour")
    print("   Resources: EKS cluster, VPC, subnets, IAM roles")
    print("\n" + "=" * 60)
    
    # Get confirmation
    response = input("\n🔴 Type 'yes' to proceed with provisioning: ")
    if response.lower() != 'yes':
        print("❌ Provisioning cancelled")
        return False
    
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
        name=f"clustrix-test-{int(time.time())}",
        provider="aws",
        region=aws_creds.get('region', 'us-east-1'),
        node_count=1,  # Minimal - just 1 node
        node_type="t3.small",  # Smaller instance to save costs
        disk_size=20,  # Minimal disk
        kubernetes_version="1.27"
    )
    
    print(f"\n📋 Cluster Configuration:")
    print(f"   Name: {spec.name}")
    print(f"   Nodes: {spec.node_count} x {spec.node_type}")
    print(f"   Region: {spec.region}")
    
    # Create provisioner
    provisioner = AWSEKSFromScratchProvisioner(spec, aws_creds)
    
    try:
        print("\n🚀 Creating EKS cluster...")
        print("   This will take 10-15 minutes...")
        
        # Start provisioning
        cluster_info = provisioner.create_cluster()
        
        if cluster_info:
            print("\n✅ Cluster created successfully!")
            print(f"   Cluster Name: {cluster_info.get('name')}")
            print(f"   Endpoint: {cluster_info.get('endpoint')}")
            print(f"   Status: {cluster_info.get('status')}")
            
            # Save cluster info for cleanup
            with open(f"cluster_info_{spec.name}.txt", "w") as f:
                f.write(f"Cluster Name: {spec.name}\n")
                f.write(f"Region: {spec.region}\n")
                f.write(f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("\nTo destroy this cluster, run:\n")
                f.write(f"python destroy_cluster.py {spec.name} {spec.region}\n")
            
            print(f"\n📝 Cluster info saved to: cluster_info_{spec.name}.txt")
            print("\n⚠️  IMPORTANT: Remember to destroy the cluster when done!")
            print(f"   Run: python destroy_cluster.py {spec.name} {spec.region}")
            
            return True
        else:
            print("\n❌ Cluster creation failed")
            return False
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Provisioning interrupted!")
        print("   Check AWS console for any resources that need cleanup")
        return False
    except Exception as e:
        print(f"\n❌ Error during provisioning: {e}")
        print("\n📋 Troubleshooting:")
        print("   1. Check AWS Console for partial resources")
        print("   2. Review CloudFormation stacks if any were created")
        print("   3. Check IAM roles and VPC resources")
        return False

if __name__ == "__main__":
    success = test_aws_eks_provisioning()
    sys.exit(0 if success else 1)