#!/usr/bin/env python
"""Clean up test AWS resources to avoid charges."""

import boto3
from clustrix.credential_manager import FlexibleCredentialManager

def cleanup_resources():
    manager = FlexibleCredentialManager()
    creds = manager.ensure_credential('aws')
    
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=creds['access_key_id'],
        aws_secret_access_key=creds['secret_access_key'],
        region_name='us-east-1'
    )
    
    print("Cleaning up test resources...")
    print("=" * 60)
    
    # Find NAT gateways
    nats = ec2.describe_nat_gateways(
        Filters=[{'Name': 'state', 'Values': ['pending', 'available']}]
    )
    
    for nat in nats['NatGateways']:
        nat_id = nat['NatGatewayId']
        vpc_id = nat['VpcId']
        print(f"\nCleaning VPC {vpc_id} with NAT {nat_id}")
        
        # Delete NAT gateway first
        try:
            ec2.delete_nat_gateway(NatGatewayId=nat_id)
            print(f"  ✅ Deleted NAT gateway: {nat_id}")
        except Exception as e:
            print(f"  ❌ Could not delete NAT: {e}")
        
        # Release Elastic IPs
        for addr in nat.get('NatGatewayAddresses', []):
            if 'AllocationId' in addr:
                try:
                    ec2.release_address(AllocationId=addr['AllocationId'])
                    print(f"  ✅ Released Elastic IP: {addr['AllocationId']}")
                except:
                    pass
        
        # Delete all subnets in the VPC
        subnets = ec2.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        for subnet in subnets['Subnets']:
            try:
                ec2.delete_subnet(SubnetId=subnet['SubnetId'])
                print(f"  ✅ Deleted subnet: {subnet['SubnetId']}")
            except Exception as e:
                print(f"  ⚠️  Could not delete subnet: {e}")
        
        # Delete route tables (except main)
        rts = ec2.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        for rt in rts['RouteTables']:
            if not rt.get('Associations', []):  # Not the main route table
                try:
                    ec2.delete_route_table(RouteTableId=rt['RouteTableId'])
                    print(f"  ✅ Deleted route table: {rt['RouteTableId']}")
                except:
                    pass
        
        # Detach and delete internet gateways
        igws = ec2.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
        )
        for igw in igws['InternetGateways']:
            igw_id = igw['InternetGatewayId']
            try:
                ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                ec2.delete_internet_gateway(InternetGatewayId=igw_id)
                print(f"  ✅ Deleted internet gateway: {igw_id}")
            except:
                pass
        
        # Delete security groups (except default)
        sgs = ec2.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        for sg in sgs['SecurityGroups']:
            if sg['GroupName'] != 'default':
                try:
                    ec2.delete_security_group(GroupId=sg['GroupId'])
                    print(f"  ✅ Deleted security group: {sg['GroupId']}")
                except:
                    pass
        
        # Finally delete the VPC
        try:
            ec2.delete_vpc(VpcId=vpc_id)
            print(f"  ✅ Deleted VPC: {vpc_id}")
        except Exception as e:
            print(f"  ❌ Could not delete VPC: {e}")
            print("     (NAT gateways may still be deleting, try again in a few minutes)")
    
    print("\n" + "=" * 60)
    print("Cleanup complete!")
    print("Check AWS Console to verify all resources are deleted.")

if __name__ == "__main__":
    cleanup_resources()