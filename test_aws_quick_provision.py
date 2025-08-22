#!/usr/bin/env python
"""
Quick test of AWS EKS provisioning - skips expensive/slow NAT gateways.
This creates a simplified VPC for testing purposes only.
"""

import sys
import time
import logging
import boto3
from clustrix.credential_manager import FlexibleCredentialManager
from clustrix.kubernetes.cluster_provisioner import ClusterSpec

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

def quick_provision_test():
    """Test simplified provisioning without NAT gateways."""
    
    print("=" * 70)
    print("Quick AWS Provisioning Test (No NAT Gateways)")
    print("=" * 70)
    
    # Get credentials
    manager = FlexibleCredentialManager()
    aws_creds = manager.ensure_credential("aws")
    
    if not aws_creds:
        logger.error("No AWS credentials")
        return False
    
    region = aws_creds.get('region', 'us-east-1')
    cluster_name = f"quick-test-{int(time.time())}"
    
    # Create AWS clients
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=aws_creds['access_key_id'],
        aws_secret_access_key=aws_creds['secret_access_key'],
        region_name=region
    )
    
    eks = boto3.client(
        'eks',
        aws_access_key_id=aws_creds['access_key_id'],
        aws_secret_access_key=aws_creds['secret_access_key'],
        region_name=region
    )
    
    iam = boto3.client(
        'iam',
        aws_access_key_id=aws_creds['access_key_id'],
        aws_secret_access_key=aws_creds['secret_access_key']
    )
    
    created_resources = {
        'vpc_id': None,
        'subnet_ids': [],
        'igw_id': None,
        'sg_id': None,
        'role_arn': None
    }
    
    try:
        # 1. Create VPC
        logger.info("Creating VPC...")
        vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
        vpc_id = vpc['Vpc']['VpcId']
        created_resources['vpc_id'] = vpc_id
        
        ec2.create_tags(
            Resources=[vpc_id],
            Tags=[{'Key': 'Name', 'Value': f'vpc-{cluster_name}'}]
        )
        
        # Enable DNS
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
        logger.info(f"  ✅ VPC created: {vpc_id}")
        
        # 2. Create Internet Gateway
        logger.info("Creating Internet Gateway...")
        igw = ec2.create_internet_gateway()
        igw_id = igw['InternetGateway']['InternetGatewayId']
        created_resources['igw_id'] = igw_id
        
        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        logger.info(f"  ✅ IGW created and attached: {igw_id}")
        
        # 3. Create PUBLIC subnets only (no NAT needed)
        logger.info("Creating public subnets...")
        
        # Get availability zones
        azs = ec2.describe_availability_zones()['AvailabilityZones']
        az_names = [az['ZoneName'] for az in azs[:2]]  # Use first 2 AZs
        
        for i, az in enumerate(az_names):
            subnet = ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock=f'10.0.{i}.0/24',
                AvailabilityZone=az
            )
            subnet_id = subnet['Subnet']['SubnetId']
            created_resources['subnet_ids'].append(subnet_id)
            
            # Make it public
            ec2.modify_subnet_attribute(
                SubnetId=subnet_id,
                MapPublicIpOnLaunch={'Value': True}
            )
            
            ec2.create_tags(
                Resources=[subnet_id],
                Tags=[{'Key': 'Name', 'Value': f'public-subnet-{i}-{cluster_name}'}]
            )
            logger.info(f"  ✅ Subnet created: {subnet_id} in {az}")
        
        # 4. Update main route table
        logger.info("Setting up routing...")
        route_tables = ec2.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        main_rt = route_tables['RouteTables'][0]['RouteTableId']
        
        # Add route to IGW
        ec2.create_route(
            RouteTableId=main_rt,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id
        )
        logger.info(f"  ✅ Routes configured")
        
        # 5. Create security group
        logger.info("Creating security group...")
        sg = ec2.create_security_group(
            GroupName=f'eks-sg-{cluster_name}',
            Description='EKS cluster security group',
            VpcId=vpc_id
        )
        sg_id = sg['GroupId']
        created_resources['sg_id'] = sg_id
        
        # Allow all traffic within VPC
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'IpProtocol': '-1',
                'FromPort': -1,
                'ToPort': -1,
                'IpRanges': [{'CidrIp': '10.0.0.0/16'}]
            }]
        )
        logger.info(f"  ✅ Security group created: {sg_id}")
        
        # 6. Create IAM role for EKS
        logger.info("Creating IAM role...")
        
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "eks.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        
        role_name = f'eks-role-{cluster_name}'
        try:
            role = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=str(assume_role_policy).replace("'", '"')
            )
            role_arn = role['Role']['Arn']
            created_resources['role_arn'] = role_arn
            
            # Attach EKS policy
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/AmazonEKSClusterPolicy'
            )
            logger.info(f"  ✅ IAM role created: {role_name}")
        except iam.exceptions.EntityAlreadyExistsException:
            logger.info(f"  ℹ️  Role already exists: {role_name}")
            role = iam.get_role(RoleName=role_name)
            role_arn = role['Role']['Arn']
            created_resources['role_arn'] = role_arn
        
        # 7. Create EKS cluster
        logger.info("\nCreating EKS cluster (this takes 10-15 minutes)...")
        
        cluster_response = eks.create_cluster(
            name=cluster_name,
            version='1.27',
            roleArn=role_arn,
            resourcesVpcConfig={
                'subnetIds': created_resources['subnet_ids'],
                'securityGroupIds': [sg_id],
                'endpointPublicAccess': True,
                'endpointPrivateAccess': False
            }
        )
        
        logger.info(f"  ⏳ Cluster creation started: {cluster_name}")
        logger.info(f"     Status: {cluster_response['cluster']['status']}")
        
        # Wait for cluster to be active
        logger.info("  ⏳ Waiting for cluster to be active...")
        waiter = eks.get_waiter('cluster_active')
        waiter.wait(
            name=cluster_name,
            WaiterConfig={'Delay': 30, 'MaxAttempts': 40}
        )
        
        # Get cluster info
        cluster = eks.describe_cluster(name=cluster_name)['cluster']
        logger.info(f"  ✅ Cluster active!")
        logger.info(f"     Endpoint: {cluster['endpoint']}")
        logger.info(f"     Status: {cluster['status']}")
        
        print("\n" + "=" * 70)
        print("✅ CLUSTER CREATED SUCCESSFULLY!")
        print("=" * 70)
        print(f"Cluster Name: {cluster_name}")
        print(f"Endpoint: {cluster['endpoint']}")
        print(f"Region: {region}")
        print("\n⚠️  To destroy this cluster:")
        print(f"   python destroy_cluster.py {cluster_name} {region}")
        
        # Save destroy script
        with open(f"destroy_{cluster_name}.sh", "w") as f:
            f.write(f"#!/bin/bash\n")
            f.write(f"python destroy_cluster.py {cluster_name} {region}\n")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed: {e}")
        
        # Clean up any created resources
        logger.info("\nCleaning up resources...")
        
        if created_resources['subnet_ids']:
            for subnet_id in created_resources['subnet_ids']:
                try:
                    ec2.delete_subnet(SubnetId=subnet_id)
                    logger.info(f"  Deleted subnet: {subnet_id}")
                except:
                    pass
        
        if created_resources['igw_id'] and created_resources['vpc_id']:
            try:
                ec2.detach_internet_gateway(
                    InternetGatewayId=created_resources['igw_id'],
                    VpcId=created_resources['vpc_id']
                )
                ec2.delete_internet_gateway(InternetGatewayId=created_resources['igw_id'])
                logger.info(f"  Deleted IGW: {created_resources['igw_id']}")
            except:
                pass
        
        if created_resources['vpc_id']:
            try:
                ec2.delete_vpc(VpcId=created_resources['vpc_id'])
                logger.info(f"  Deleted VPC: {created_resources['vpc_id']}")
            except:
                pass
        
        return False

if __name__ == "__main__":
    print("\n⚠️  This will create a REAL EKS cluster (costs ~$0.10/hour)")
    response = input("Type 'yes' to proceed: ")
    
    if response.lower() != 'yes':
        print("Cancelled")
        sys.exit(0)
    
    success = quick_provision_test()
    sys.exit(0 if success else 1)