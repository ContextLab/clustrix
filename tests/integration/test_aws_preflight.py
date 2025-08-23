#!/usr/bin/env python
"""Pre-flight check for AWS EKS provisioning."""

import os
import sys
import subprocess
import json


def check_aws_cli():
    """Check if AWS CLI is installed and configured."""
    print("🔍 Checking AWS CLI...")
    try:
        result = subprocess.run(
            ["aws", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            print(f"  ✅ AWS CLI installed: {result.stdout.strip()}")
            return True
        else:
            print("  ❌ AWS CLI not found")
            return False
    except Exception as e:
        print(f"  ❌ Error checking AWS CLI: {e}")
        return False


def check_aws_credentials():
    """Check if AWS credentials are configured."""
    print("\n🔑 Checking AWS credentials...")

    # Check credential manager
    from clustrix.credential_manager import FlexibleCredentialManager

    manager = FlexibleCredentialManager()
    creds = manager.ensure_credential("aws")

    if creds:
        print(f"  ✅ Credentials loaded from credential manager")
        print(f"     Access Key: {creds['access_key_id'][:10]}...")
        print(f"     Region: {creds.get('region', 'not set')}")

        # Test credentials with STS
        try:
            import boto3

            sts = boto3.client(
                "sts",
                aws_access_key_id=creds["access_key_id"],
                aws_secret_access_key=creds["secret_access_key"],
                region_name=creds.get("region", "us-west-2"),
            )
            identity = sts.get_caller_identity()
            print(f"  ✅ Credentials valid for account: {identity['Account']}")
            print(f"     ARN: {identity['Arn']}")
            return True, creds
        except Exception as e:
            print(f"  ❌ Credentials invalid: {e}")
            return False, None
    else:
        print("  ❌ No AWS credentials found")
        return False, None


def check_aws_permissions(creds):
    """Check if we have necessary AWS permissions."""
    print("\n🔐 Checking AWS permissions...")

    import boto3

    # Services we need access to
    required_services = {
        "ec2": ["DescribeVpcs", "CreateVpc"],
        "eks": ["ListClusters", "CreateCluster"],
        "iam": ["ListRoles", "CreateRole"],
    }

    session = boto3.Session(
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-west-2"),
    )

    all_good = True
    for service, actions in required_services.items():
        print(f"\n  Checking {service.upper()} permissions:")

        try:
            if service == "ec2":
                client = session.client("ec2")
                # Try to describe VPCs (read permission)
                client.describe_vpcs(MaxResults=5)
                print(f"    ✅ Can read {service.upper()} resources")

            elif service == "eks":
                client = session.client("eks")
                # Try to list clusters (read permission)
                client.list_clusters(maxResults=5)
                print(f"    ✅ Can read {service.upper()} resources")

            elif service == "iam":
                client = session.client("iam")
                # Try to list roles (read permission)
                client.list_roles(MaxItems=5)
                print(f"    ✅ Can read {service.upper()} resources")

        except Exception as e:
            print(f"    ❌ Cannot access {service.upper()}: {str(e)[:100]}")
            all_good = False

    return all_good


def check_kubectl():
    """Check if kubectl is installed."""
    print("\n🔧 Checking kubectl...")
    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            print(f"  ✅ kubectl installed: {result.stdout.strip()}")
            return True
        else:
            print("  ❌ kubectl not found")
            print("     Install with: brew install kubectl")
            return False
    except Exception as e:
        print(f"  ❌ Error checking kubectl: {e}")
        return False


def estimate_costs():
    """Estimate costs for AWS EKS cluster."""
    print("\n💰 Cost Estimation:")
    print("  EKS Control Plane: $0.10/hour")
    print("  t3.medium (2 nodes): $0.0416/hour x 2 = $0.0832/hour")
    print("  NAT Gateway: $0.045/hour x 2 = $0.09/hour")
    print("  Data transfer: ~$0.01/hour (estimate)")
    print("  ─────────────────────────────────────")
    print("  TOTAL: ~$0.28/hour ($6.72/day)")
    print("\n  ⚠️  Remember to destroy cluster after testing!")


def check_existing_clusters(creds):
    """Check for existing EKS clusters."""
    print("\n🔍 Checking for existing EKS clusters...")

    try:
        import boto3

        eks = boto3.client(
            "eks",
            aws_access_key_id=creds["access_key_id"],
            aws_secret_access_key=creds["secret_access_key"],
            region_name=creds.get("region", "us-west-2"),
        )

        clusters = eks.list_clusters()
        if clusters["clusters"]:
            print(f"  ⚠️  Found {len(clusters['clusters'])} existing cluster(s):")
            for cluster in clusters["clusters"]:
                print(f"     - {cluster}")
            print("\n  Make sure to clean these up if they're test clusters!")
        else:
            print("  ✅ No existing EKS clusters found")

    except Exception as e:
        print(f"  ⚠️  Could not check existing clusters: {e}")


def main():
    """Run all pre-flight checks."""
    print("=" * 60)
    print("AWS EKS Provisioning Pre-Flight Check")
    print("=" * 60)

    all_checks_passed = True

    # Check AWS CLI
    if not check_aws_cli():
        all_checks_passed = False

    # Check credentials
    creds_valid, creds = check_aws_credentials()
    if not creds_valid:
        all_checks_passed = False
        print("\n❌ Cannot proceed without valid AWS credentials")
        return 1

    # Check permissions
    if not check_aws_permissions(creds):
        print("\n⚠️  Some permissions missing. Provisioning might fail.")
        print("   Ensure your IAM user has full access to EC2, EKS, and IAM")

    # Check kubectl
    if not check_kubectl():
        print("\n⚠️  kubectl not installed. Won't be able to interact with cluster.")

    # Check existing clusters
    check_existing_clusters(creds)

    # Show cost estimate
    estimate_costs()

    # Summary
    print("\n" + "=" * 60)
    if all_checks_passed:
        print("✅ All pre-flight checks passed!")
        print("\nReady to provision AWS EKS cluster.")
        print("Run: python test_aws_eks_real.py")
    else:
        print("⚠️  Some checks failed. Review issues above.")
        print("\nYou can still try provisioning, but it might fail.")

    print("=" * 60)

    return 0 if all_checks_passed else 1


if __name__ == "__main__":
    sys.exit(main())
