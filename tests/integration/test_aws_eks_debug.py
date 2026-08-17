#!/usr/bin/env python
"""Debug AWS EKS provisioning - test initial steps only.

This file is a diagnostic *script*, not a pytest module: it contains no test
functions. Its body previously ran at module scope, which meant that merely
importing it fetched real AWS credentials, constructed a boto3 client, called
the EKS API, and could `sys.exit(1)` mid-collection (crashing pytest with
INTERNALERROR).

The body now lives in `main()` behind a `__main__` guard so that importing this
module is inert. See issue #109: the directory-level gate in conftest.py does
not protect against a path being named explicitly on the pytest command line,
because collect_ignore_glob only filters directory traversal.

Run deliberately with:

    python tests/integration/test_aws_eks_debug.py
"""

import sys
import traceback

from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager


def main():
    # Get credentials
    print("Getting AWS credentials...")
    credential_manager = FlexibleCredentialManager()
    aws_creds = credential_manager.ensure_credential("aws")

    if not aws_creds:
        print("❌ No AWS credentials found")
        return 1

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
        print(f"   Provisioner: {provisioner}")

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
        print("  - CIDR: 10.0.0.0/16")
        print(f"  - Region: {spec.region}")

    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        return 1

    print("\n✅ All pre-flight checks passed!")
    print("\nTo run full provisioning, use: python test_aws_eks_auto.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
