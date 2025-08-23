#!/usr/bin/env python
"""Test AWS EKS provisioning with detailed step-by-step execution."""

import sys
import time
import logging
import traceback
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("aws_provision_test.log"),
    ],
)
logger = logging.getLogger()


def test_provisioning():
    """Test EKS provisioning step by step."""

    print("=" * 70)
    print("AWS EKS Step-by-Step Provisioning Test")
    print("=" * 70)

    # Get credentials
    logger.info("Loading credentials...")
    manager = FlexibleCredentialManager()
    aws_creds = manager.ensure_credential("aws")

    if not aws_creds:
        logger.error("No AWS credentials found")
        return False

    # Create spec
    cluster_name = f"test-steps-{int(time.time())}"
    spec = ClusterSpec(
        cluster_name=cluster_name,
        provider="aws",
        region=aws_creds.get("region", "us-east-1"),
        node_count=1,
        node_type="t3.small",
        kubernetes_version="1.27",
    )

    logger.info(f"Cluster: {cluster_name}")
    logger.info(f"Region: {spec.region}")

    # Initialize provisioner
    logger.info("Initializing provisioner...")
    provisioner = AWSEKSFromScratchProvisioner(aws_creds, spec.region)

    try:
        # Test Step 1: VPC Infrastructure
        logger.info("\n" + "=" * 50)
        logger.info("STEP 1: Testing VPC Infrastructure Creation")
        logger.info("=" * 50)

        start = time.time()
        vpc_config = provisioner._create_vpc_infrastructure(spec)
        elapsed = time.time() - start

        logger.info(f"✅ VPC created in {elapsed:.1f}s")
        logger.info(f"   VPC ID: {vpc_config.get('vpc_id')}")
        logger.info(f"   Subnets: {vpc_config.get('subnet_ids', [])[:2]}...")

        # Clean up VPC to avoid charges
        logger.info("\nCleaning up test VPC...")
        import boto3

        ec2 = boto3.client(
            "ec2",
            aws_access_key_id=aws_creds["access_key_id"],
            aws_secret_access_key=aws_creds["secret_access_key"],
            region_name=spec.region,
        )

        # Delete subnets
        for subnet_id in vpc_config.get("subnet_ids", []):
            try:
                ec2.delete_subnet(SubnetId=subnet_id)
                logger.info(f"   Deleted subnet: {subnet_id}")
            except Exception as e:
                logger.warning(f"   Could not delete subnet {subnet_id}: {e}")

        # Delete internet gateway
        if "internet_gateway_id" in vpc_config:
            try:
                ec2.detach_internet_gateway(
                    InternetGatewayId=vpc_config["internet_gateway_id"],
                    VpcId=vpc_config["vpc_id"],
                )
                ec2.delete_internet_gateway(
                    InternetGatewayId=vpc_config["internet_gateway_id"]
                )
                logger.info(f"   Deleted IGW: {vpc_config['internet_gateway_id']}")
            except Exception as e:
                logger.warning(f"   Could not delete IGW: {e}")

        # Delete NAT gateways (these cost money!)
        for nat_id in vpc_config.get("nat_gateway_ids", []):
            try:
                ec2.delete_nat_gateway(NatGatewayId=nat_id)
                logger.info(f"   Deleted NAT Gateway: {nat_id}")
            except Exception as e:
                logger.warning(f"   Could not delete NAT Gateway {nat_id}: {e}")

        # Delete VPC
        try:
            ec2.delete_vpc(VpcId=vpc_config["vpc_id"])
            logger.info(f"   Deleted VPC: {vpc_config['vpc_id']}")
        except Exception as e:
            logger.warning(f"   Could not delete VPC: {e}")
            logger.warning("   Check AWS Console and delete manually to avoid charges!")

        return True

    except Exception as e:
        logger.error(f"Test failed: {e}")
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\nThis test will:")
    print("1. Create a VPC with subnets")
    print("2. Immediately delete it")
    print("3. Show where provisioning might be hanging")
    print("\nNo long-term resources will be created.\n")

    success = test_provisioning()

    if success:
        print("\n✅ VPC infrastructure test successful!")
        print("Check aws_provision_test.log for details")
    else:
        print("\n❌ Test failed - check aws_provision_test.log")

    sys.exit(0 if success else 1)
