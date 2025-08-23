#!/usr/bin/env python
"""Test AWS EKS provisioning - step by step with verbose output."""

import sys
import time
import traceback
import logging
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


def test_provisioning():
    # Get credentials
    logger.info("Getting AWS credentials...")
    credential_manager = FlexibleCredentialManager()
    aws_creds = credential_manager.ensure_credential("aws")

    if not aws_creds:
        logger.error("No AWS credentials found")
        return False

    logger.info(f"Got credentials for region: {aws_creds.get('region', 'us-east-1')}")

    # Create spec with unique name
    cluster_name = f"test-{int(time.time())}"
    spec = ClusterSpec(
        cluster_name=cluster_name,
        provider="aws",
        region=aws_creds.get("region", "us-east-1"),
        node_count=1,
        node_type="t3.small",
        kubernetes_version="1.27",
    )

    logger.info(f"Created cluster spec: {spec.cluster_name}")

    # Initialize provisioner
    logger.info("Initializing provisioner...")
    provisioner = AWSEKSFromScratchProvisioner(aws_creds, spec.region)

    # Override methods to add logging
    original_provision = provisioner.provision_complete_infrastructure

    def logged_provision(spec):
        logger.info(
            f"Starting provision_complete_infrastructure for {spec.cluster_name}"
        )
        try:
            # Call each step manually with logging
            logger.info("Step 1: Creating VPC...")
            # This would normally be inside provision_complete_infrastructure
            # but we're debugging to see where it hangs

            import boto3

            ec2 = boto3.client(
                "ec2",
                aws_access_key_id=aws_creds["access_key_id"],
                aws_secret_access_key=aws_creds["secret_access_key"],
                region_name=spec.region,
            )

            # Create VPC
            vpc_response = ec2.create_vpc(CidrBlock="10.0.0.0/16")
            vpc_id = vpc_response["Vpc"]["VpcId"]
            logger.info(f"Created VPC: {vpc_id}")

            # Tag it
            ec2.create_tags(
                Resources=[vpc_id],
                Tags=[
                    {"Key": "Name", "Value": f"eks-vpc-{spec.cluster_name}"},
                    {"Key": "ClusterName", "Value": spec.cluster_name},
                ],
            )
            logger.info(f"Tagged VPC")

            # Enable DNS
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
            logger.info("Enabled DNS for VPC")

            # Clean up test VPC
            logger.info("Cleaning up test VPC...")
            ec2.delete_vpc(VpcId=vpc_id)
            logger.info(f"Deleted test VPC: {vpc_id}")

            return {"status": "test_complete", "vpc_tested": vpc_id}

        except Exception as e:
            logger.error(f"Error in provision: {e}")
            traceback.print_exc()
            raise

    # Test the provisioning
    try:
        logger.info("Starting test provisioning...")
        result = logged_provision(spec)
        logger.info(f"Test complete: {result}")
        return True
    except Exception as e:
        logger.error(f"Provisioning failed: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("AWS EKS Provisioning Step-by-Step Test")
    print("=" * 60)

    success = test_provisioning()

    if success:
        print("\n✅ Test successful! VPC creation and deletion work.")
        print("The full provisioning appears to be hanging somewhere.")
        print("Check the AWS provisioner code for blocking operations.")
    else:
        print("\n❌ Test failed!")

    sys.exit(0 if success else 1)
