#!/usr/bin/env python
"""Debug AWS EKS provisioning - test initial steps only."""

import sys
import traceback
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager

# Get credentials
print("Getting AWS credentials...")
credential_manager = FlexibleCredentialManager()
aws_creds = credential_manager.ensure_credential("aws")

if not aws_creds:
    print("❌ No AWS credentials found")
    sys.exit(1)

print(f"✅ Got credentials for account: {aws_creds.get('account_id', 'unknown')}")
print(f"   Region: {aws_creds.get('region', 'us-east-1')}")

# Create spec
spec = ClusterSpec(
    cluster_name="test-debug",
    provider="aws",
    region=aws_creds.get("region", "us-east-1"),
    node_count=1,
    node_type="t3.small",
    kubernetes_version="1.27",
)

print(f"\nCluster spec created: {spec.cluster_name}")

# Initialize provisioner
print("\nInitializing provisioner...")
try:
    provisioner = AWSEKSFromScratchProvisioner(aws_creds, spec.region)
    print("✅ Provisioner initialized")

    # Check AWS connectivity
    print("\nTesting AWS connectivity...")
    import boto3

    eks = boto3.client(
        "eks",
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
        region_name=spec.region,
    )

    clusters = eks.list_clusters()
    print(f"✅ Can list EKS clusters. Found: {clusters.get('clusters', [])}")

    # Test VPC creation
    print("\nWould create VPC with:")
    print(f"  - Name: eks-vpc-{spec.cluster_name}")
    print(f"  - CIDR: 10.0.0.0/16")
    print(f"  - Region: {spec.region}")

except Exception as e:
    print(f"❌ Error: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n✅ All pre-flight checks passed!")
print("\nTo run full provisioning, use: python test_aws_eks_auto.py")
