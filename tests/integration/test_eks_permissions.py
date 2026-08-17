#!/usr/bin/env python
"""Test specific EKS permissions.

This file is a diagnostic *script*, not a pytest module: it contains no test
functions. Its body previously ran at module scope, which meant that merely
importing it fetched real AWS credentials, constructed boto3 EKS and IAM
clients, called those APIs, and could `exit(1)` mid-collection (crashing pytest
with INTERNALERROR).

The body now lives in `main()` behind a `__main__` guard so that importing this
module is inert. See issue #109: the directory-level gate in conftest.py does
not protect against a path being named explicitly on the pytest command line,
because collect_ignore_glob only filters directory traversal.

Run deliberately with:

    python tests/integration/test_eks_permissions.py
"""

import sys

import boto3

from clustrix.credential_manager import FlexibleCredentialManager


def main():
    # Get credentials
    manager = FlexibleCredentialManager()
    creds = manager.ensure_credential("aws")

    if not creds:
        print("❌ No AWS credentials found")
        return 1

    print("Testing EKS permissions for user Clustrix...")
    print("=" * 60)

    # Create EKS client
    eks = boto3.client(
        "eks",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )

    # Test various EKS operations
    operations = [
        ("ListClusters", lambda: eks.list_clusters(maxResults=1)),
        (
            "DescribeCluster",
            lambda: eks.describe_cluster(name="test-nonexistent-cluster"),
        ),
    ]

    print("\nEKS Permission Tests:")
    for op_name, op_func in operations:
        try:
            op_func()
            print(f"  ✅ {op_name}: Allowed")
        except eks.exceptions.ResourceNotFoundException:
            print(f"  ✅ {op_name}: Allowed (resource not found)")
        except eks.exceptions.AccessDeniedException as e:
            print(f"  ❌ {op_name}: Access Denied")
            print(f"     Error: {str(e)[:200]}")
        except Exception as e:
            if "AccessDenied" in str(e):
                print(f"  ❌ {op_name}: Access Denied")
            else:
                print(f"  ⚠️  {op_name}: {type(e).__name__}")

    # Check attached policies
    print("\n" + "=" * 60)
    print("Checking attached policies...")

    iam = boto3.client(
        "iam",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
    )

    try:
        # Get user policies
        response = iam.list_attached_user_policies(UserName="Clustrix")

        print("\nAttached AWS Managed Policies:")
        eks_policies_found = []
        for policy in response["AttachedPolicies"]:
            print(f"  • {policy['PolicyName']}")
            if "EKS" in policy["PolicyName"]:
                eks_policies_found.append(policy["PolicyName"])

        print("\nEKS-related policies found:")
        if eks_policies_found:
            for p in eks_policies_found:
                print(f"  ✅ {p}")
        else:
            print("  ❌ No EKS policies found!")
            print("\nYou need to add these policies in AWS Console:")
            print("  • AmazonEKSClusterPolicy")
            print("  • AmazonEKSWorkerNodePolicy")
            print("  • AmazonEKS_CNI_Policy")
            print("  • AmazonEKSServicePolicy")

    except Exception as e:
        print(f"Could not list policies: {e}")

    print("\n" + "=" * 60)
    print("\nNext steps:")
    print("1. If EKS policies are missing, add them in AWS Console")
    print(
        "2. Direct link: "
        "https://console.aws.amazon.com/iam/home#/users/Clustrix?section=permissions"
    )
    print("3. Click 'Add permissions' and search for the EKS policies listed above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
