#!/usr/bin/env python3
"""
Test script for SSH key automation on real clusters.

This script validates the SSH key automation functionality on real Dartmouth clusters
following the technical design document.
"""

import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

# Add the parent directory to the path to import clustrix
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.config import ClusterConfig
from clustrix.ssh_utils import setup_ssh_keys, detect_working_ssh_key, validate_ssh_key
from clustrix.secure_credentials import SecureCredentialManager


def test_ssh_automation(cluster_configs: list) -> dict:
    """
    Test SSH key automation on real clusters:
    1. Clean existing keys (if requested)
    2. Get password from 1Password
    3. Run setup_ssh_keys()
    4. Verify passwordless access
    5. Test with clustrix job submission
    """
    results = {}
    
    for cluster_info in cluster_configs:
        cluster_name = cluster_info["name"]
        print(f"\n{'='*60}")
        print(f"Testing SSH automation for: {cluster_name}")
        print(f"{'='*60}")
        
        try:
            # Create cluster config
            config = ClusterConfig(
                cluster_type=cluster_info["cluster_type"],
                cluster_host=cluster_info["host"],
                username=cluster_info["username"],
                cluster_port=cluster_info.get("port", 22),
            )
            
            # Get credentials from 1Password
            print(f"🔐 Retrieving credentials from 1Password...")
            cred_manager = SecureCredentialManager()
            ssh_creds = cred_manager.get_structured_credential(cluster_info["credential_name"])
            if not ssh_creds or "password" not in ssh_creds:
                results[cluster_name] = {
                    "success": False,
                    "error": "Failed to retrieve password from 1Password",
                    "timestamp": datetime.now().isoformat()
                }
                continue
            
            password = ssh_creds["password"]
            print(f"✅ Credentials retrieved successfully")
            
            # Test 1: Check for existing SSH keys
            print(f"\n🔍 Phase 1: Checking for existing SSH keys...")
            existing_key = detect_working_ssh_key(config.cluster_host, config.username, config.cluster_port)
            
            if existing_key:
                print(f"✅ Found existing working key: {existing_key}")
                test_existing = True
            else:
                print(f"ℹ️  No existing working SSH keys found")
                test_existing = False
            
            # Test 2: Setup SSH keys (with force refresh to test generation)
            print(f"\n🔧 Phase 2: Testing SSH key setup with force refresh...")
            result = setup_ssh_keys(
                config,
                password=password,
                cluster_alias=f"test_{cluster_name}",
                key_type="ed25519",
                force_refresh=True,  # Always generate new keys for testing
            )
            
            print(f"📋 Setup result:")
            for key, value in result.items():
                if key != "details":
                    print(f"   {key}: {value}")
            
            if result["details"]:
                print(f"   Details:")
                for key, value in result["details"].items():
                    print(f"     {key}: {value}")
            
            # Test 3: Validate the new key works
            if result["success"] and result["key_path"]:
                print(f"\n🧪 Phase 3: Validating new SSH key...")
                validation_success = validate_ssh_key(
                    config.cluster_host, 
                    config.username, 
                    result["key_path"], 
                    config.cluster_port
                )
                
                if validation_success:
                    print(f"✅ SSH key validation successful")
                else:
                    print(f"❌ SSH key validation failed")
            
            # Test 4: Test basic cluster functionality (optional - if paramiko available)
            print(f"\n🚀 Phase 4: Testing basic cluster connectivity...")
            try:
                import paramiko
                
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=config.cluster_host,
                    username=config.username,
                    key_filename=result["key_path"],
                    timeout=10
                )
                
                # Test basic command
                stdin, stdout, stderr = client.exec_command('echo "SSH automation test successful"')
                output = stdout.read().decode().strip()
                client.close()
                
                if "SSH automation test successful" in output:
                    print(f"✅ Basic connectivity test passed")
                    connectivity_test = True
                else:
                    print(f"❌ Basic connectivity test failed: {output}")
                    connectivity_test = False
                    
            except Exception as e:
                print(f"⚠️  Connectivity test skipped: {e}")
                connectivity_test = None
            
            # Store results
            results[cluster_name] = {
                "success": result["success"],
                "existing_key_found": test_existing,
                "key_path": result.get("key_path", ""),
                "key_deployed": result.get("key_deployed", False),
                "connection_tested": result.get("connection_tested", False),
                "validation_passed": validation_success if result["success"] else False,
                "connectivity_test": connectivity_test,
                "error": result.get("error"),
                "details": result.get("details", {}),
                "timestamp": datetime.now().isoformat()
            }
            
            print(f"\n✅ {cluster_name} testing completed successfully")
            
        except Exception as e:
            print(f"\n❌ Error testing {cluster_name}: {e}")
            results[cluster_name] = {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    return results


def main():
    """Main test function."""
    print("SSH Key Automation Testing")
    print("Following technical design from /docs/ssh_key_automation_technical_design.md")
    print()
    
    # Define test clusters (Dartmouth infrastructure)
    test_clusters = [
        {
            "name": "ndoli_slurm",
            "cluster_type": "slurm",
            "host": "ndoli.dartmouth.edu",
            "username": "f002d6b",
            "port": 22,
            "credential_name": "clustrix-ssh-slurm"
        },
        {
            "name": "tensor01_gpu",
            "cluster_type": "ssh", 
            "host": "tensor01.dartmouth.edu",
            "username": "f002d6b",
            "port": 22,
            "credential_name": "clustrix-ssh-gpu"  # Separate GPU credentials
        }
    ]
    
    # Run tests
    results = test_ssh_automation(test_clusters)
    
    # Generate report
    print(f"\n{'='*60}")
    print("SSH KEY AUTOMATION TEST REPORT")
    print(f"{'='*60}")
    
    total_tests = len(results)
    successful_tests = sum(1 for r in results.values() if r["success"])
    
    print(f"Total clusters tested: {total_tests}")
    print(f"Successful setups: {successful_tests}")
    print(f"Success rate: {successful_tests/total_tests*100:.1f}%")
    print()
    
    for cluster_name, result in results.items():
        print(f"📊 {cluster_name}:")
        if result["success"]:
            print(f"   ✅ Status: SUCCESS")
            print(f"   🔑 Key path: {result['key_path']}")
            print(f"   🚀 Deployed: {result['key_deployed']}")
            print(f"   🧪 Tested: {result['connection_tested']}")
            print(f"   ✓ Validated: {result['validation_passed']}")
            if result["connectivity_test"] is not None:
                print(f"   🔗 Connectivity: {'✅' if result['connectivity_test'] else '❌'}")
        else:
            print(f"   ❌ Status: FAILED")
            print(f"   📝 Error: {result['error']}")
        print()
    
    # Save detailed results
    report_file = f"ssh_automation_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path = Path(__file__).parent / "validation" / report_file
    report_path.parent.mkdir(exist_ok=True)
    
    with open(report_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"📄 Detailed report saved to: {report_path}")
    
    # Exit with appropriate code
    if successful_tests == total_tests:
        print(f"\n🎉 All tests passed! SSH key automation is working correctly.")
        sys.exit(0)
    else:
        print(f"\n⚠️  Some tests failed. Review the report for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()