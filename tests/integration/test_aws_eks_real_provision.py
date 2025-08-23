#!/usr/bin/env python
"""
REAL AWS EKS provisioning test - this WILL create resources and incur costs!
Only run this if you're ready to pay for AWS EKS cluster.
"""

import sys
import time
import logging
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager

# Set up detailed logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()


def provision_real_cluster():
    """Actually provision a real EKS cluster."""

    print("=" * 70)
    print("🚨 REAL AWS EKS CLUSTER PROVISIONING 🚨")
    print("=" * 70)
    print("\nThis WILL create:")
    print("  • EKS Cluster ($0.10/hour)")
    print("  • EC2 instances")
    print("  • VPC, subnets, NAT gateways")
    print("  • IAM roles")
    print("\nEstimated cost: ~$0.28/hour")
    print("=" * 70)

    # Confirm
    response = input("\n⚠️  Type 'YES_PROVISION' to proceed: ")
    if response != "YES_PROVISION":
        print("Cancelled.")
        return False

    # Get credentials
    logger.info("Loading AWS credentials...")
    manager = FlexibleCredentialManager()
    aws_creds = manager.ensure_credential("aws")

    if not aws_creds:
        logger.error("No AWS credentials")
        return False

    # Create cluster spec
    cluster_name = f"clustrix-real-{int(time.time())}"
    spec = ClusterSpec(
        cluster_name=cluster_name,
        provider="aws",
        region=aws_creds.get("region", "us-east-1"),
        node_count=1,
        node_type="t3.small",
        kubernetes_version="1.27",
    )

    logger.info(f"Cluster name: {cluster_name}")
    logger.info(f"Region: {spec.region}")

    # Initialize provisioner
    logger.info("Initializing AWS EKS provisioner...")
    provisioner = AWSEKSFromScratchProvisioner(aws_creds, spec.region)

    try:
        # Start provisioning
        print("\n" + "=" * 70)
        print("🚀 STARTING PROVISIONING")
        print("=" * 70)
        print("\nThis will take 10-15 minutes. Progress will be shown below:\n")

        start_time = time.time()
        cluster_info = provisioner.provision_complete_infrastructure(spec)
        elapsed = time.time() - start_time

        if cluster_info:
            print("\n" + "=" * 70)
            print("✅ CLUSTER CREATED SUCCESSFULLY!")
            print("=" * 70)
            print(f"\nCluster Name: {cluster_info.get('cluster_name')}")
            print(f"Endpoint: {cluster_info.get('endpoint')}")
            print(f"Region: {cluster_info.get('region')}")
            print(f"Time taken: {elapsed/60:.1f} minutes")

            # Save info
            with open(f"DESTROY_CLUSTER_{cluster_name}.sh", "w") as f:
                f.write("#!/bin/bash\n")
                f.write(f"# Destroy cluster {cluster_name}\n")
                f.write(f"python destroy_cluster.py {cluster_name} {spec.region}\n")

            print(f"\n📝 To destroy: bash DESTROY_CLUSTER_{cluster_name}.sh")
            print("\n⚠️  IMPORTANT: Destroy the cluster when done to avoid charges!")

            return True
        else:
            logger.error("Provisioning returned no cluster info")
            return False

    except KeyboardInterrupt:
        print("\n\n❌ INTERRUPTED!")
        print(f"Check AWS Console for resources tagged: {cluster_name}")
        return False
    except Exception as e:
        logger.error(f"Provisioning failed: {e}", exc_info=True)
        print(f"\n❌ Check AWS Console for partial resources: {cluster_name}")
        return False


if __name__ == "__main__":
    success = provision_real_cluster()
    sys.exit(0 if success else 1)
