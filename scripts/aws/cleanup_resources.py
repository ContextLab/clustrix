#!/usr/bin/env python
"""Delete leftover Clustrix-managed AWS networking resources.

Restored (and hardened) from ``cleanup_test_resources.py``, deleted by
commit b9c836f ("Issue #72: Delete obsolete development scripts") on the
false claim that it had been migrated to ``scripts/aws/`` -- that directory
never existed until this file. See GitHub issue #95. The original source
was recovered from git history (``git show b9c836f^:cleanup_test_resources.py``).

Clustrix does not create AWS resources -- there is no AWS backend and no
provisioner in the package. This is standalone operator tooling for an
account that already holds clustrix-tagged networking, run by hand when
something that should have been torn down is still on the bill.

WHAT THIS DELETES
------------------
NAT gateways, their Elastic IPs, subnets, non-default security groups,
non-main route tables, internet gateways, and the VPC itself.

SAFETY
------
* Defaults to a DRY RUN. Nothing is deleted unless you pass ``--execute``.
* Every resource considered for deletion is printed first, with its AWS
  region and resource id, in both dry-run and execute mode.
* Only resources that live inside a VPC positively identified as
  Clustrix-managed are ever touched.

IDENTIFICATION / TAGGING CONVENTION
------------------------------------
A VPC is only eligible for cleanup if it carries the tag
``clustrix:managed=true``. That tag is the whole of the identification: it
is what marks a VPC as clustrix's to delete, and a VPC without it is out of
scope no matter what else is true of it.
NAT gateways, subnets, security groups, route tables, and internet gateways
are only deleted when they belong to such a tagged VPC. Untagged VPCs --
including the account's default VPC and anything created by hand or by
another tool -- are never touched, no matter what they are named.

CREDENTIALS
-----------
AWS credentials come from boto3's own credential chain: the ``AWS_*``
environment variables, ``~/.aws/credentials``, or an instance profile. If it
resolves nothing, this script exits immediately with an error rather than
letting a call fail somewhere deeper.

This used to ask ``clustrix.credential_manager`` for provider ``"aws"``,
which has never existed in ``PROVIDER_ENV_NAMES`` -- the lookup always
returned ``None``, so the script could never authenticate at all. AWS keys
are also not something clustrix should be holding: it has no AWS backend.

Usage:
    python scripts/aws/cleanup_resources.py [--region REGION] [--execute]
"""

import argparse
import sys

MANAGED_TAG_KEY = "clustrix:managed"
MANAGED_TAG_VALUE = "true"

# Resource types that plan_cleanup() may emit, in the order they must be
# deleted (NAT/EIP before the VPC's subnets and gateways can go).
_DELETE_ORDER = (
    "nat_gateway",
    "elastic_ip",
    "subnet",
    "route_table",
    "internet_gateway",
    "security_group",
    "vpc",
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the real argparse parser used by both main() and the tests."""
    parser = argparse.ArgumentParser(
        description=(
            "Delete NAT gateways, VPCs, and their dependent networking "
            "resources tagged as Clustrix-managed. Defaults to a DRY RUN "
            "that only prints what would be deleted. Only ever touches "
            f"VPCs tagged {MANAGED_TAG_KEY}={MANAGED_TAG_VALUE} -- nothing "
            "else is ever deleted, regardless of naming."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region to scan and clean up.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help=(
            "Actually delete the resources that were found. Without this "
            "flag the script only performs a dry run: it lists exactly "
            "what it would delete and deletes nothing."
        ),
    )
    return parser


def get_ec2_client(region: str):
    """Build a real boto3 EC2 client, failing loudly if no credentials."""
    # Imported here, not at module scope: --help must work on a machine
    # with no AWS SDK installed. boto3 is not a clustrix dependency, so a
    # raw ImportError traceback is not an acceptable way to say it is
    # absent -- these scripts delete cloud resources and every failure mode
    # has to be legible.
    try:
        import boto3
    except ImportError:
        sys.exit(
            "ERROR: this script needs the AWS SDK, which is not installed.\n"
            "       Install it with:  pip install boto3\n"
            "       (boto3 is not a clustrix dependency; these AWS utilities\n"
            "        are the only thing in the project that needs it.)"
        )

    # boto3's own credential chain, deliberately. This used to ask the
    # clustrix credential manager for provider "aws", which has never been
    # in PROVIDER_ENV_NAMES -- the lookup always returned None, so this
    # script could never authenticate at all. Standard AWS environment
    # variables, ~/.aws/credentials and instance profiles are what an
    # operator running cleanup tooling already has, and going through the
    # SDK's chain also keeps AWS keys out of clustrix's credential surface.
    if boto3.Session().get_credentials() is None:
        print(
            "ERROR: No AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY, or configure a profile with `aws "
            "configure`, before running this script.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return boto3.client(
        "ec2",
        region_name=region,
    )


def find_managed_vpc_ids(ec2) -> set:
    """Return the ids of VPCs tagged as Clustrix-managed test resources."""
    response = ec2.describe_vpcs(
        Filters=[{"Name": f"tag:{MANAGED_TAG_KEY}", "Values": [MANAGED_TAG_VALUE]}]
    )
    return {vpc["VpcId"] for vpc in response.get("Vpcs", [])}


def plan_cleanup(ec2) -> list:
    """Return the ordered list of (resource_type, resource_id, vpc_id)
    tuples that are eligible for deletion. Read-only: makes only
    describe_* calls, never a delete/release call."""
    managed_vpc_ids = find_managed_vpc_ids(ec2)
    plan: list = []
    if not managed_vpc_ids:
        return plan

    nats = ec2.describe_nat_gateways(
        Filters=[{"Name": "state", "Values": ["pending", "available"]}]
    )
    for nat in nats.get("NatGateways", []):
        vpc_id = nat["VpcId"]
        if vpc_id not in managed_vpc_ids:
            continue
        plan.append(("nat_gateway", nat["NatGatewayId"], vpc_id))
        for addr in nat.get("NatGatewayAddresses", []):
            if "AllocationId" in addr:
                plan.append(("elastic_ip", addr["AllocationId"], vpc_id))

    for vpc_id in managed_vpc_ids:
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
        for subnet in subnets.get("Subnets", []):
            plan.append(("subnet", subnet["SubnetId"], vpc_id))

        route_tables = ec2.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        for rt in route_tables.get("RouteTables", []):
            if not rt.get("Associations", []):
                plan.append(("route_table", rt["RouteTableId"], vpc_id))

        igws = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )
        for igw in igws.get("InternetGateways", []):
            plan.append(("internet_gateway", igw["InternetGatewayId"], vpc_id))

        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        for sg in sgs.get("SecurityGroups", []):
            if sg["GroupName"] != "default":
                plan.append(("security_group", sg["GroupId"], vpc_id))

        plan.append(("vpc", vpc_id, vpc_id))

    order_index = {name: i for i, name in enumerate(_DELETE_ORDER)}
    plan.sort(key=lambda item: order_index[item[0]])
    return plan


def print_plan(plan: list, region: str, execute: bool) -> None:
    verb = "Deleting" if execute else "Would delete (dry run)"
    print(f"{verb} {len(plan)} resource(s) in region {region}:")
    for resource_type, resource_id, vpc_id in plan:
        print(f"  [{region}] {resource_type}: {resource_id} (vpc={vpc_id})")


def execute_plan(ec2, plan: list) -> None:
    """Actually delete the planned resources.

    This is the ONLY function in this script that calls a destructive
    boto3 method (delete_*, release_address, detach_internet_gateway).
    It is only ever invoked from main() inside ``if args.execute:``.
    """
    for resource_type, resource_id, vpc_id in plan:
        try:
            if resource_type == "nat_gateway":
                ec2.delete_nat_gateway(NatGatewayId=resource_id)
            elif resource_type == "elastic_ip":
                ec2.release_address(AllocationId=resource_id)
            elif resource_type == "subnet":
                ec2.delete_subnet(SubnetId=resource_id)
            elif resource_type == "route_table":
                ec2.delete_route_table(RouteTableId=resource_id)
            elif resource_type == "internet_gateway":
                ec2.detach_internet_gateway(InternetGatewayId=resource_id, VpcId=vpc_id)
                ec2.delete_internet_gateway(InternetGatewayId=resource_id)
            elif resource_type == "security_group":
                ec2.delete_security_group(GroupId=resource_id)
            elif resource_type == "vpc":
                ec2.delete_vpc(VpcId=resource_id)
            print(f"  deleted {resource_type}: {resource_id}")
        except Exception as e:  # noqa: BLE001
            print(
                f"  FAILED to delete {resource_type} {resource_id}: {e}",
                file=sys.stderr,
            )


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    ec2 = get_ec2_client(args.region)

    plan = plan_cleanup(ec2)
    if not plan:
        print(
            f"No resources tagged {MANAGED_TAG_KEY}={MANAGED_TAG_VALUE} "
            f"found in {args.region}. Nothing to do."
        )
        return 0

    print_plan(plan, args.region, args.execute)

    if args.execute:
        execute_plan(ec2, plan)
    else:
        print("\nDry run only -- nothing was deleted. Re-run with --execute to delete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
