#!/usr/bin/env python
"""Tear down a Clustrix-managed EKS cluster and its dependent AWS resources.

Restored (and hardened) from ``destroy_cluster.py``, deleted by commit
b9c836f ("Issue #72: Delete obsolete development scripts") on the false
claim that it had been migrated to ``scripts/aws/`` -- that directory never
existed until this file. See GitHub issue #95. The original source was
recovered from git history (``git show b9c836f^:destroy_cluster.py``).

WHAT THIS DELETES
------------------
The named EKS cluster's node groups, the EKS cluster itself, its VPC and
dependent networking resources (subnets, non-default security groups,
internet gateway), and its two Clustrix-created IAM roles.

SAFETY
------
* Defaults to a DRY RUN. Nothing is deleted unless you pass ``--execute``.
* Every resource considered for deletion is printed first, with its AWS
  region and resource id, in both dry-run and execute mode.
* Refuses to act on a cluster it cannot positively identify as
  Clustrix-managed (see IDENTIFICATION below) -- it exits with an error
  instead of guessing.

IDENTIFICATION / TAGGING CONVENTION
------------------------------------
This script only recognizes resources created by
``clustrix.kubernetes.aws_provisioner.AWSEKSFromScratchProvisioner``
(see ``clustrix/kubernetes/aws_provisioner.py``), which tags/names them as:
  * EKS cluster: tagged clustrix:managed=true, clustrix:cluster=<name>
  * VPC: tagged clustrix:managed=true, clustrix:cluster=<name>
  * IAM roles: named exactly "clustrix-eks-cluster-role-<name>" and
    "clustrix-eks-node-role-<name>"
If the named cluster exists but lacks the clustrix:managed=true tag, this
script refuses to touch it or anything associated with it.

CREDENTIALS
-----------
AWS credentials are loaded via ``clustrix.credential_manager.
FlexibleCredentialManager`` (environment variables or ``~/.clustrix/.env``).
If no credentials are found, this script exits immediately with an error --
it never silently falls back to boto3's default credential chain.

Usage:
    python scripts/aws/destroy_cluster.py CLUSTER_NAME [--region REGION] [--execute]
"""

import argparse
import sys

from clustrix.credential_manager import FlexibleCredentialManager

MANAGED_TAG_KEY = "clustrix:managed"
MANAGED_TAG_VALUE = "true"
CLUSTER_TAG_KEY = "clustrix:cluster"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the real argparse parser used by both main() and the tests."""
    parser = argparse.ArgumentParser(
        description=(
            "Destroy a Clustrix-managed EKS cluster (node groups, the "
            "cluster, its VPC, and its IAM roles). Defaults to a DRY RUN "
            "that only prints what would be deleted. Refuses to act unless "
            f"the cluster is tagged {MANAGED_TAG_KEY}={MANAGED_TAG_VALUE} "
            f"and {CLUSTER_TAG_KEY}=<cluster_name> -- the tags "
            "clustrix.kubernetes.aws_provisioner applies to every cluster "
            "it creates."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "cluster_name",
        help="Name of the EKS cluster to destroy.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region the cluster lives in.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help=(
            "Actually delete the cluster and its resources. Without this "
            "flag the script only performs a dry run: it lists exactly "
            "what it would delete and deletes nothing."
        ),
    )
    return parser


def get_clients(region: str):
    """Build real boto3 clients, failing loudly if no credentials."""
    # Imported here, not at module scope: --help must work on a machine
    # with no AWS SDK installed. boto3 is not a clustrix dependency.
    import boto3

    manager = FlexibleCredentialManager()
    creds = manager.ensure_credential("aws")
    if (
        not creds
        or not creds.get("access_key_id")
        or not creds.get("secret_access_key")
    ):
        print(
            "ERROR: No AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY, or configure them in ~/.clustrix/.env, "
            "before running this script.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    eks = boto3.client(
        "eks",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )
    iam = boto3.client(
        "iam",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )
    return eks, ec2, iam


def iam_role_names(cluster_name: str) -> tuple:
    """The exact IAM role names clustrix.kubernetes.aws_provisioner creates
    for a given cluster."""
    return (
        f"clustrix-eks-cluster-role-{cluster_name}",
        f"clustrix-eks-node-role-{cluster_name}",
    )


def plan_destruction(eks, ec2, iam, cluster_name: str) -> dict:
    """Return a plan describing exactly what would be destroyed, or raise
    SystemExit with a clear error if the cluster cannot be positively
    identified as Clustrix-managed. Read-only: makes only describe_*/
    list_*/get_* calls, never a delete/detach call."""
    try:
        cluster = eks.describe_cluster(name=cluster_name)["cluster"]
    except eks.exceptions.ResourceNotFoundException:
        print(f"ERROR: EKS cluster '{cluster_name}' not found.", file=sys.stderr)
        raise SystemExit(1)

    tags = cluster.get("tags", {})
    if (
        tags.get(MANAGED_TAG_KEY) != MANAGED_TAG_VALUE
        or tags.get(CLUSTER_TAG_KEY) != cluster_name
    ):
        print(
            f"ERROR: Cluster '{cluster_name}' is not tagged "
            f"{MANAGED_TAG_KEY}={MANAGED_TAG_VALUE} / "
            f"{CLUSTER_TAG_KEY}={cluster_name}. Refusing to touch a cluster "
            "that cannot be positively identified as Clustrix-managed.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    plan: dict = {
        "cluster_name": cluster_name,
        "nodegroups": eks.list_nodegroups(clusterName=cluster_name).get(
            "nodegroups", []
        ),
        "eks_cluster": cluster_name,
        "vpc_ids": [],
        "subnets": [],
        "security_groups": [],
        "internet_gateways": [],
        "iam_roles": [],
    }

    vpcs = ec2.describe_vpcs(
        Filters=[
            {"Name": f"tag:{MANAGED_TAG_KEY}", "Values": [MANAGED_TAG_VALUE]},
            {"Name": f"tag:{CLUSTER_TAG_KEY}", "Values": [cluster_name]},
        ]
    )
    for vpc in vpcs.get("Vpcs", []):
        vpc_id = vpc["VpcId"]
        plan["vpc_ids"].append(vpc_id)

        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
        for subnet in subnets.get("Subnets", []):
            plan["subnets"].append((vpc_id, subnet["SubnetId"]))

        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        for sg in sgs.get("SecurityGroups", []):
            if sg["GroupName"] != "default":
                plan["security_groups"].append((vpc_id, sg["GroupId"]))

        igws = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )
        for igw in igws.get("InternetGateways", []):
            plan["internet_gateways"].append((vpc_id, igw["InternetGatewayId"]))

    for role_name in iam_role_names(cluster_name):
        try:
            iam.get_role(RoleName=role_name)
            plan["iam_roles"].append(role_name)
        except iam.exceptions.NoSuchEntityException:
            continue

    return plan


def print_plan(plan: dict, region: str, execute: bool) -> None:
    verb = "Deleting" if execute else "Would delete (dry run)"
    print(
        f"{verb} the following in region {region} for cluster '{plan['cluster_name']}':"
    )
    for ng in plan["nodegroups"]:
        print(f"  [{region}] eks_nodegroup: {ng} (cluster={plan['cluster_name']})")
    print(f"  [{region}] eks_cluster: {plan['eks_cluster']}")
    for vpc_id, subnet_id in plan["subnets"]:
        print(f"  [{region}] subnet: {subnet_id} (vpc={vpc_id})")
    for vpc_id, sg_id in plan["security_groups"]:
        print(f"  [{region}] security_group: {sg_id} (vpc={vpc_id})")
    for vpc_id, igw_id in plan["internet_gateways"]:
        print(f"  [{region}] internet_gateway: {igw_id} (vpc={vpc_id})")
    for vpc_id in plan["vpc_ids"]:
        print(f"  [{region}] vpc: {vpc_id}")
    for role_name in plan["iam_roles"]:
        print(f"  [{region}] iam_role: {role_name}")


def execute_plan(eks, ec2, iam, plan: dict) -> bool:
    """Actually delete the planned resources.

    This is the ONLY function in this script that calls a destructive
    boto3 method (delete_*, detach_*). It is only ever invoked from main()
    inside ``if args.execute:``.
    """
    cluster_name = plan["cluster_name"]
    ok = True

    for ng_name in plan["nodegroups"]:
        try:
            print(f"  deleting nodegroup: {ng_name}")
            eks.delete_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)
            waiter = eks.get_waiter("nodegroup_deleted")
            waiter.wait(
                clusterName=cluster_name,
                nodegroupName=ng_name,
                WaiterConfig={"Delay": 30, "MaxAttempts": 40},
            )
            print(f"  deleted nodegroup: {ng_name}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to delete nodegroup {ng_name}: {e}", file=sys.stderr)
            ok = False

    try:
        print(f"  deleting eks_cluster: {cluster_name}")
        eks.delete_cluster(name=cluster_name)
        waiter = eks.get_waiter("cluster_deleted")
        waiter.wait(name=cluster_name, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
        print(f"  deleted eks_cluster: {cluster_name}")
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED to delete eks_cluster {cluster_name}: {e}", file=sys.stderr)
        ok = False

    for vpc_id, subnet_id in plan["subnets"]:
        try:
            ec2.delete_subnet(SubnetId=subnet_id)
            print(f"  deleted subnet: {subnet_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to delete subnet {subnet_id}: {e}", file=sys.stderr)
            ok = False

    for vpc_id, sg_id in plan["security_groups"]:
        try:
            ec2.delete_security_group(GroupId=sg_id)
            print(f"  deleted security_group: {sg_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to delete security_group {sg_id}: {e}", file=sys.stderr)
            ok = False

    for vpc_id, igw_id in plan["internet_gateways"]:
        try:
            ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            print(f"  deleted internet_gateway: {igw_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to delete internet_gateway {igw_id}: {e}", file=sys.stderr)
            ok = False

    for vpc_id in plan["vpc_ids"]:
        try:
            ec2.delete_vpc(VpcId=vpc_id)
            print(f"  deleted vpc: {vpc_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to delete vpc {vpc_id}: {e}", file=sys.stderr)
            ok = False

    for role_name in plan["iam_roles"]:
        try:
            policies = iam.list_attached_role_policies(RoleName=role_name)
            for policy in policies.get("AttachedPolicies", []):
                iam.detach_role_policy(
                    RoleName=role_name, PolicyArn=policy["PolicyArn"]
                )
            iam.delete_role(RoleName=role_name)
            print(f"  deleted iam_role: {role_name}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED to delete iam_role {role_name}: {e}", file=sys.stderr)
            ok = False

    return ok


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    eks, ec2, iam = get_clients(args.region)

    plan = plan_destruction(eks, ec2, iam, args.cluster_name)
    print_plan(plan, args.region, args.execute)

    if args.execute:
        ok = execute_plan(eks, ec2, iam, plan)
        return 0 if ok else 1

    print("\nDry run only -- nothing was deleted. Re-run with --execute to delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
