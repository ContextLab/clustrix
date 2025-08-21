#!/usr/bin/env python
"""
Destroy an EKS cluster and clean up resources.
"""

import sys
import boto3
from clustrix.credential_manager import FlexibleCredentialManager

def destroy_cluster(cluster_name, region='us-east-1'):
    """Destroy an EKS cluster and associated resources."""
    
    print(f"🗑️  Destroying cluster: {cluster_name}")
    print(f"   Region: {region}")
    print("=" * 60)
    
    # Get credentials
    credential_manager = FlexibleCredentialManager()
    aws_creds = credential_manager.ensure_credential("aws")
    
    if not aws_creds:
        print("❌ No AWS credentials found")
        return False
    
    # Create clients
    eks = boto3.client(
        'eks',
        aws_access_key_id=aws_creds['access_key_id'],
        aws_secret_access_key=aws_creds['secret_access_key'],
        region_name=region
    )
    
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=aws_creds['access_key_id'],
        aws_secret_access_key=aws_creds['secret_access_key'],
        region_name=region
    )
    
    iam = boto3.client(
        'iam',
        aws_access_key_id=aws_creds['access_key_id'],
        aws_secret_access_key=aws_creds['secret_access_key']
    )
    
    try:
        # 1. Delete node groups first
        print("\n1️⃣ Deleting node groups...")
        try:
            nodegroups = eks.list_nodegroups(clusterName=cluster_name)
            for ng_name in nodegroups.get('nodegroups', []):
                print(f"   Deleting node group: {ng_name}")
                eks.delete_nodegroup(
                    clusterName=cluster_name,
                    nodegroupName=ng_name
                )
                # Wait for deletion
                waiter = eks.get_waiter('nodegroup_deleted')
                waiter.wait(
                    clusterName=cluster_name,
                    nodegroupName=ng_name,
                    WaiterConfig={'Delay': 30, 'MaxAttempts': 40}
                )
                print(f"   ✅ Node group {ng_name} deleted")
        except Exception as e:
            print(f"   ⚠️  Error deleting node groups: {e}")
        
        # 2. Delete the cluster
        print("\n2️⃣ Deleting EKS cluster...")
        eks.delete_cluster(name=cluster_name)
        
        # Wait for cluster deletion
        print("   Waiting for cluster deletion (this may take 5-10 minutes)...")
        waiter = eks.get_waiter('cluster_deleted')
        waiter.wait(
            name=cluster_name,
            WaiterConfig={'Delay': 30, 'MaxAttempts': 40}
        )
        print("   ✅ Cluster deleted")
        
        # 3. Clean up VPC if it was created for this cluster
        print("\n3️⃣ Cleaning up VPC resources...")
        # Look for VPC tagged with cluster name
        vpcs = ec2.describe_vpcs(
            Filters=[
                {'Name': 'tag:Name', 'Values': [f'*{cluster_name}*']},
            ]
        )
        
        for vpc in vpcs.get('Vpcs', []):
            vpc_id = vpc['VpcId']
            print(f"   Found VPC: {vpc_id}")
            
            # Delete subnets
            subnets = ec2.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            for subnet in subnets.get('Subnets', []):
                print(f"   Deleting subnet: {subnet['SubnetId']}")
                ec2.delete_subnet(SubnetId=subnet['SubnetId'])
            
            # Delete security groups (except default)
            sgs = ec2.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            for sg in sgs.get('SecurityGroups', []):
                if sg['GroupName'] != 'default':
                    print(f"   Deleting security group: {sg['GroupId']}")
                    try:
                        ec2.delete_security_group(GroupId=sg['GroupId'])
                    except Exception as e:
                        print(f"     ⚠️  Could not delete {sg['GroupId']}: {e}")
            
            # Delete internet gateway
            igws = ec2.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
            )
            for igw in igws.get('InternetGateways', []):
                igw_id = igw['InternetGatewayId']
                print(f"   Detaching and deleting IGW: {igw_id}")
                ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            
            # Delete VPC
            print(f"   Deleting VPC: {vpc_id}")
            try:
                ec2.delete_vpc(VpcId=vpc_id)
                print(f"   ✅ VPC {vpc_id} deleted")
            except Exception as e:
                print(f"   ⚠️  Could not delete VPC: {e}")
        
        # 4. Clean up IAM roles
        print("\n4️⃣ Cleaning up IAM roles...")
        role_names = [
            f"eksServiceRole-{cluster_name}",
            f"eksNodeRole-{cluster_name}",
            f"{cluster_name}-cluster-role",
            f"{cluster_name}-node-role"
        ]
        
        for role_name in role_names:
            try:
                # Detach policies
                policies = iam.list_attached_role_policies(RoleName=role_name)
                for policy in policies.get('AttachedPolicies', []):
                    iam.detach_role_policy(
                        RoleName=role_name,
                        PolicyArn=policy['PolicyArn']
                    )
                
                # Delete role
                iam.delete_role(RoleName=role_name)
                print(f"   ✅ Deleted role: {role_name}")
            except iam.exceptions.NoSuchEntityException:
                pass  # Role doesn't exist
            except Exception as e:
                print(f"   ⚠️  Error with role {role_name}: {e}")
        
        print("\n✅ Cluster destruction complete!")
        print("   Please check AWS Console to ensure all resources are cleaned up")
        return True
        
    except eks.exceptions.ResourceNotFoundException:
        print(f"❌ Cluster '{cluster_name}' not found in region {region}")
        return False
    except Exception as e:
        print(f"❌ Error destroying cluster: {e}")
        print("\n⚠️  Manual cleanup may be required in AWS Console")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python destroy_cluster.py <cluster_name> [region]")
        sys.exit(1)
    
    cluster_name = sys.argv[1]
    region = sys.argv[2] if len(sys.argv) > 2 else 'us-east-1'
    
    success = destroy_cluster(cluster_name, region)
    sys.exit(0 if success else 1)