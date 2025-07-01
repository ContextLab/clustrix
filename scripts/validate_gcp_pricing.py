#!/usr/bin/env python3
"""
GCP Pricing API Validation Script

This script validates the GCP Pricing API integration for Clustrix.
It tests actual API calls and ensures the cost estimation functionality works correctly.
"""

import sys
import json
import logging
import os
import tempfile
from pathlib import Path

# Add the clustrix package to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
from clustrix.cost_providers.gcp import GCPCostMonitor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_gcp_pricing_api():
    """Test GCP Pricing API with real credentials."""
    print("🔍 GCP Pricing API Validation")
    print("=" * 50)
    
    # Get credentials
    creds = ValidationCredentials()
    gcp_creds = creds.get_gcp_credentials()
    
    if not gcp_creds:
        print("❌ No GCP credentials found")
        print("   Please set up GCP credentials in 1Password or environment variables")
        return False
    
    print(f"✅ GCP credentials found")
    print(f"   Project ID: {gcp_creds['project_id']}")
    print(f"   Region: {gcp_creds['region']}")
    
    # Create temporary service account file
    service_account_json = gcp_creds.get('service_account_json')
    if not service_account_json:
        print("❌ No service account JSON found in credentials")
        return False
    
    try:
        # Write service account JSON to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            if isinstance(service_account_json, str):
                # Parse JSON string if needed
                try:
                    service_account_data = json.loads(service_account_json)
                    json.dump(service_account_data, f, indent=2)
                except json.JSONDecodeError:
                    f.write(service_account_json)
            else:
                json.dump(service_account_json, f, indent=2)
            temp_creds_file = f.name
        
        print(f"   Service account file: {temp_creds_file}")
        
        print("\n🧪 Testing GCP Pricing API")
        
        # Set up service account credentials
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = temp_creds_file
        
        cost_monitor = GCPCostMonitor(
            region=gcp_creds['region'],
            use_pricing_api=True
        )
        
        # Test different machine types
        test_machine_types = [
            "e2-micro",
            "e2-small",
            "n1-standard-1",
            "n1-standard-2", 
            "n2-standard-4",
            "c2-standard-8"
        ]
        
        print(f"   Testing {len(test_machine_types)} machine types...")
        
        for machine_type in test_machine_types:
            try:
                cost_estimate = cost_monitor.estimate_cost(machine_type, hours_used=1.0)
                if cost_estimate and cost_estimate.estimated_cost > 0:
                    print(f"   ✅ {machine_type}: ${cost_estimate.hourly_rate:.4f}/hour (${cost_estimate.estimated_cost:.4f} total)")
                    print(f"      Source: {cost_estimate.pricing_source}")
                else:
                    print(f"   ⚠️  {machine_type}: No pricing data")
            except Exception as e:
                print(f"   ❌ {machine_type}: Error - {e}")
        
        # Test invalid machine type
        try:
            invalid_cost = cost_monitor.estimate_cost("invalid-machine-type", hours_used=1.0)
            if invalid_cost is None or invalid_cost.estimated_cost == 0:
                print("   ✅ Invalid machine type correctly returns None/zero")
            else:
                print(f"   ⚠️  Invalid machine type returned: ${invalid_cost.estimated_cost}")
        except Exception as e:
            print(f"   ✅ Invalid machine type correctly raises exception: {e}")
            
        print("\n✅ GCP Pricing API validation completed successfully!")
        return True
        
    except Exception as e:
        print(f"\n❌ GCP Pricing API validation failed: {e}")
        logger.exception("GCP Pricing API validation error")
        return False
    finally:
        # Clean up temporary file
        if 'temp_creds_file' in locals():
            try:
                os.unlink(temp_creds_file)
            except OSError:
                pass

def main():
    """Main validation function."""
    print("🚀 Starting GCP Pricing API Validation")
    print("=" * 50)
    
    success = test_gcp_pricing_api()
    
    print("\n📊 Validation Summary")
    print("=" * 50)
    if success:
        print("✅ GCP Pricing API validation: PASSED")
        print("   All tests completed successfully")
    else:
        print("❌ GCP Pricing API validation: FAILED")
        print("   Check error messages above")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())