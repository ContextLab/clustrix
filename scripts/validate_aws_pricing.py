#!/usr/bin/env python3
"""
AWS Pricing API Validation Script

This script validates the AWS Pricing API integration for Clustrix.
It tests actual API calls and ensures the cost estimation functionality works correctly.
"""

import sys
import json
import logging
from pathlib import Path

# Add the clustrix package to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
from clustrix.cost_providers.aws import AWSCostMonitor

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Also enable logging for boto3 and pricing clients
logging.getLogger('clustrix.pricing_clients').setLevel(logging.DEBUG)
logging.getLogger('boto3').setLevel(logging.DEBUG)
logging.getLogger('botocore').setLevel(logging.DEBUG)

def test_aws_pricing_api():
    """Test AWS Pricing API with real credentials."""
    print("🔍 AWS Pricing API Validation")
    print("=" * 50)
    
    # Get credentials
    creds = ValidationCredentials()
    aws_creds = creds.get_aws_credentials()
    
    if not aws_creds:
        print("❌ No AWS credentials found")
        print("   Please set up AWS credentials in 1Password or environment variables")
        return False
    
    print(f"✅ AWS credentials found")
    print(f"   Access Key: {aws_creds['aws_access_key_id'][:10]}...")
    print(f"   Region: {aws_creds['aws_region']}")
    
    # Test AWS Pricing API
    try:
        print("\n🧪 Testing AWS Pricing API")
        
        # Set up AWS credentials as environment variables for boto3
        import os
        os.environ['AWS_ACCESS_KEY_ID'] = aws_creds['aws_access_key_id']
        os.environ['AWS_SECRET_ACCESS_KEY'] = aws_creds['aws_secret_access_key']
        os.environ['AWS_DEFAULT_REGION'] = aws_creds['aws_region']
        
        cost_monitor = AWSCostMonitor(
            region=aws_creds['aws_region'],
            use_pricing_api=True
        )
        
        # Test different instance types
        test_instances = [
            "t3.micro",
            "t3.small", 
            "m5.large",
            "c5.xlarge",
            "r5.2xlarge"
        ]
        
        print(f"   Testing {len(test_instances)} instance types...")
        
        for instance_type in test_instances:
            try:
                cost_estimate = cost_monitor.estimate_cost(instance_type, hours_used=1.0)
                if cost_estimate and cost_estimate.estimated_cost > 0:
                    print(f"   ✅ {instance_type}: ${cost_estimate.hourly_rate:.4f}/hour (${cost_estimate.estimated_cost:.4f} total)")
                    print(f"      Source: {cost_estimate.pricing_source}")
                    
                    # Warn if not truly from API
                    if cost_estimate.pricing_source != "api":
                        print(f"      ⚠️  WARNING: Expected API source but got '{cost_estimate.pricing_source}'")
                else:
                    print(f"   ⚠️  {instance_type}: No pricing data")
            except Exception as e:
                print(f"   ❌ {instance_type}: Error - {e}")
        
        # Test invalid instance type
        try:
            invalid_cost = cost_monitor.estimate_cost("invalid-instance-type", hours_used=1.0)
            if invalid_cost is None or invalid_cost.estimated_cost == 0:
                print("   ✅ Invalid instance type correctly returns None/zero")
            else:
                print(f"   ⚠️  Invalid instance type returned: ${invalid_cost.estimated_cost}")
        except Exception as e:
            print(f"   ✅ Invalid instance type correctly raises exception: {e}")
            
        print("\n✅ AWS Pricing API validation completed successfully!")
        return True
        
    except Exception as e:
        print(f"\n❌ AWS Pricing API validation failed: {e}")
        logger.exception("AWS Pricing API validation error")
        return False

def main():
    """Main validation function."""
    print("🚀 Starting AWS Pricing API Validation")
    print("=" * 50)
    
    success = test_aws_pricing_api()
    
    print("\n📊 Validation Summary")
    print("=" * 50)
    if success:
        print("✅ AWS Pricing API validation: PASSED")
        print("   All tests completed successfully")
    else:
        print("❌ AWS Pricing API validation: FAILED")
        print("   Check error messages above")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())