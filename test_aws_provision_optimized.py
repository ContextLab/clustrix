#!/usr/bin/env python
"""
Test AWS EKS provisioning with optimizations:
1. Skip NAT gateways (use public subnets only for testing)
2. Smaller instance sizes
3. Single availability zone for faster provisioning
"""

import sys
import time
import logging
import boto3
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.credential_manager import FlexibleCredentialManager

# Monkey-patch the provisioner to skip NAT gateways
original_create_vpc = AWSEKSFromScratchProvisioner._create_vpc_infrastructure

def patched_create_vpc(self, spec):
    """Create VPC without NAT gateways for faster testing."""
    logger = logging.getLogger(__name__)
    logger.info("Creating VPC infrastructure (OPTIMIZED - no NAT gateways)...")
    
    # Create VPC
    vpc = self.ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    
    self.ec2.create_tags(
        Resources=[vpc_id],
        Tags=[
            {"Key": "Name", "Value": f"eks-vpc-{spec.cluster_name}"},
            {"Key": "ClusterName", "Value": spec.cluster_name},
        ],
    )
    
    # Enable DNS
    self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    
    # Create Internet Gateway
    igw = self.ec2.create_internet_gateway()
    igw_id = igw["InternetGateway"]["InternetGatewayId"]
    self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    
    # Get AZs
    azs = self.ec2.describe_availability_zones()["AvailabilityZones"]
    az_names = [az["ZoneName"] for az in azs[:2]]  # Use 2 AZs for EKS
    
    # Create public subnets only
    subnet_ids = []
    for i, az in enumerate(az_names):
        subnet = self.ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=f"10.0.{i}.0/24",
            AvailabilityZone=az
        )
        subnet_id = subnet["Subnet"]["SubnetId"]
        subnet_ids.append(subnet_id)
        
        # Enable auto-assign public IP
        self.ec2.modify_subnet_attribute(
            SubnetId=subnet_id,
            MapPublicIpOnLaunch={"Value": True}
        )
        
        self.ec2.create_tags(
            Resources=[subnet_id],
            Tags=[
                {"Key": "Name", "Value": f"eks-public-subnet-{i}-{spec.cluster_name}"},
                {"Key": "kubernetes.io/cluster/" + spec.cluster_name, "Value": "shared"},
            ],
        )
    
    # Update main route table
    route_tables = self.ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    main_rt = route_tables["RouteTables"][0]["RouteTableId"]
    
    self.ec2.create_route(
        RouteTableId=main_rt,
        DestinationCidrBlock="0.0.0.0/0",
        GatewayId=igw_id
    )
    
    # Create security group
    sg = self.ec2.create_security_group(
        GroupName=f"eks-cluster-sg-{spec.cluster_name}",
        Description="EKS cluster security group",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]
    
    # Allow all traffic within VPC
    self.ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{
            "IpProtocol": "-1",
            "FromPort": -1,
            "ToPort": -1,
            "IpRanges": [{"CidrIp": "10.0.0.0/16"}]
        }]
    )
    
    logger.info(f"✅ VPC infrastructure created (optimized)")
    
    return {
        "vpc_id": vpc_id,
        "subnet_ids": subnet_ids,
        "private_subnet_ids": subnet_ids,  # Use public as private for testing
        "public_subnet_ids": subnet_ids,
        "internet_gateway_id": igw_id,
        "nat_gateway_ids": [],  # No NAT gateways
        "security_group_ids": [sg_id],
    }

# Apply the patch
AWSEKSFromScratchProvisioner._create_vpc_infrastructure = patched_create_vpc

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

def test_optimized_provisioning():
    """Test EKS provisioning with optimizations."""
    
    print("=" * 70)
    print("Optimized AWS EKS Provisioning Test")
    print("=" * 70)
    print("\nOptimizations:")
    print("  • No NAT gateways (saves 5-10 minutes)")
    print("  • Public subnets only")
    print("  • Minimal configuration")
    print("\n" + "=" * 70)
    
    # Get credentials
    manager = FlexibleCredentialManager()
    aws_creds = manager.ensure_credential("aws")
    
    if not aws_creds:
        logger.error("No AWS credentials")
        return False
    
    # Create spec
    cluster_name = f"optimized-{int(time.time())}"
    spec = ClusterSpec(
        cluster_name=cluster_name,
        provider="aws",
        region=aws_creds.get('region', 'us-east-1'),
        node_count=1,
        node_type="t3.micro",  # Smallest instance
        kubernetes_version="1.27"
    )
    
    logger.info(f"Cluster: {cluster_name}")
    logger.info(f"Region: {spec.region}")
    
    # Initialize provisioner
    provisioner = AWSEKSFromScratchProvisioner(aws_creds, spec.region)
    
    try:
        # Start provisioning
        logger.info("\nStarting provisioning...")
        start_time = time.time()
        
        result = provisioner.provision_complete_infrastructure(spec)
        
        elapsed = time.time() - start_time
        
        if result:
            print("\n" + "=" * 70)
            print("✅ CLUSTER CREATED SUCCESSFULLY!")
            print("=" * 70)
            print(f"Cluster Name: {result.get('cluster_name')}")
            print(f"Endpoint: {result.get('endpoint')}")
            print(f"Time: {elapsed/60:.1f} minutes")
            
            # Save cleanup info
            with open(f"destroy_{cluster_name}.sh", "w") as f:
                f.write(f"#!/bin/bash\n")
                f.write(f"python destroy_cluster.py {cluster_name} {spec.region}\n")
            
            print(f"\n⚠️  To destroy: bash destroy_{cluster_name}.sh")
            return True
        else:
            logger.error("Provisioning failed")
            return False
            
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        
        print(f"\n❌ Failed. Check AWS Console for resources tagged: {cluster_name}")
        return False

if __name__ == "__main__":
    print("\n⚠️  This will create a REAL EKS cluster")
    print("   Cost: ~$0.10/hour for control plane")
    
    response = input("\nType 'yes' to proceed: ")
    if response.lower() != 'yes':
        print("Cancelled")
        sys.exit(0)
    
    success = test_optimized_provisioning()
    sys.exit(0 if success else 1)