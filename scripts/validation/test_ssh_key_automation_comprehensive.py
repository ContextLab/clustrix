#!/usr/bin/env python3
"""
Comprehensive SSH Key Automation Validation Script for Issue 57

Tests SSH key automation on real clusters (ndoli.dartmouth.edu and tensor01.dartmouth.edu)
to validate the SSH key setup functionality works end-to-end.

Usage:
    python test_ssh_key_automation_comprehensive.py [--cluster CLUSTER] [--clean-keys]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def run_command(cmd: List[str], timeout: int = 30, capture_output: bool = True) -> Tuple[int, str, str]:
    """Run a command and return (return_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, 
            capture_output=capture_output, 
            text=True, 
            timeout=timeout
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return -1, "", str(e)


def test_ssh_connectivity(hostname: str, username: str) -> Dict:
    """Test basic SSH connectivity to a cluster."""
    print(f"Testing SSH connectivity to {hostname}...")
    
    # Test SSH connection without keys (should prompt for password or fail gracefully)
    cmd = [
        "ssh", 
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",  # Don't prompt for password
        "-o", "StrictHostKeyChecking=no",
        f"{username}@{hostname}", 
        "echo 'SSH test successful'"
    ]
    
    return_code, stdout, stderr = run_command(cmd, timeout=15)
    
    return {
        "hostname": hostname,
        "username": username,
        "connection_attempted": True,
        "return_code": return_code,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "key_auth_working": return_code == 0,
        "connection_refused": "Connection refused" in stderr,
        "permission_denied": "Permission denied" in stderr,
        "host_unreachable": "No route to host" in stderr or "Host unreachable" in stderr
    }


def check_existing_ssh_keys() -> Dict:
    """Check for existing SSH keys in ~/.ssh/."""
    ssh_dir = Path.home() / ".ssh"
    key_files = []
    
    common_key_names = [
        "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"
    ]
    
    for key_name in common_key_names:
        private_key = ssh_dir / key_name
        public_key = ssh_dir / f"{key_name}.pub"
        
        if private_key.exists():
            key_files.append({
                "name": key_name,
                "private_key_exists": True,
                "public_key_exists": public_key.exists(),
                "private_key_path": str(private_key),
                "public_key_path": str(public_key) if public_key.exists() else None,
                "permissions": oct(private_key.stat().st_mode)[-4:] if private_key.exists() else None
            })
    
    return {
        "ssh_dir_exists": ssh_dir.exists(),
        "ssh_dir_path": str(ssh_dir),
        "key_files_found": key_files,
        "total_keys": len(key_files)
    }


def backup_ssh_keys(backup_suffix: Optional[str] = None) -> Dict:
    """Backup existing SSH keys before testing."""
    if backup_suffix is None:
        backup_suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    ssh_dir = Path.home() / ".ssh"
    backup_dir = ssh_dir / f"backup_{backup_suffix}"
    
    try:
        if not backup_dir.exists():
            backup_dir.mkdir()
        
        backed_up_files = []
        common_key_names = ["id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"]
        
        for key_name in common_key_names:
            for suffix in ["", ".pub"]:
                key_file = ssh_dir / f"{key_name}{suffix}"
                if key_file.exists():
                    backup_file = backup_dir / f"{key_name}{suffix}"
                    key_file.rename(backup_file)
                    backed_up_files.append({
                        "original": str(key_file),
                        "backup": str(backup_file)
                    })
        
        return {
            "success": True,
            "backup_dir": str(backup_dir),
            "backed_up_files": backed_up_files,
            "backup_count": len(backed_up_files)
        }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "backup_dir": str(backup_dir) if 'backup_dir' in locals() else None
        }


def test_clustrix_ssh_automation(hostname: str, username: str) -> Dict:
    """Test the clustrix SSH automation functionality."""
    print(f"Testing clustrix SSH automation for {hostname}...")
    
    try:
        # Import clustrix SSH utilities
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from clustrix.ssh_utils import setup_ssh_keys
        from clustrix.config import ClusterConfig
        
        # Create cluster configuration
        config = ClusterConfig(
            cluster_type="slurm",  # or "ssh" 
            cluster_host=hostname,
            username=username,
            remote_work_dir=f"/dartfs-hpc/rc/home/b/{username}" if "ndoli" in hostname else f"/home/{username}"
        )
        
        # Test SSH key setup
        print("Running SSH key automation...")
        setup_result = setup_ssh_keys(config, password="test_password")
        
        return {
            "automation_attempted": True,
            "config_created": True,
            "setup_result": setup_result,
            "success": setup_result.get("success", False),
            "error": setup_result.get("error", None),
            "key_generated": setup_result.get("key_generated", False),
            "key_deployed": setup_result.get("key_deployed", False),
            "ssh_config_updated": setup_result.get("ssh_config_updated", False)
        }
    
    except ImportError as e:
        return {
            "automation_attempted": False,
            "import_error": str(e),
            "success": False
        }
    except Exception as e:
        return {
            "automation_attempted": True,
            "setup_error": str(e),
            "success": False
        }


def test_post_setup_connectivity(hostname: str, username: str) -> Dict:
    """Test SSH connectivity after running clustrix SSH automation."""
    print(f"Testing post-setup SSH connectivity to {hostname}...")
    
    # Test SSH connection with key authentication
    cmd = [
        "ssh", 
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",  # Don't prompt for password
        "-o", "StrictHostKeyChecking=no",
        "-o", "PreferredAuthentications=publickey",  # Only use key auth
        f"{username}@{hostname}", 
        "echo 'Post-setup SSH test successful' && hostname && whoami"
    ]
    
    return_code, stdout, stderr = run_command(cmd, timeout=15)
    
    return {
        "hostname": hostname,
        "username": username,
        "post_setup_test": True,
        "return_code": return_code,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "key_auth_successful": return_code == 0,
        "authentication_failed": "Permission denied" in stderr
    }


def verify_home_directory(hostname: str, username: str) -> Dict:
    """Verify the home directory path on the remote cluster."""
    print(f"Verifying home directory on {hostname}...")
    
    cmd = [
        "ssh", 
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        f"{username}@{hostname}", 
        "echo $HOME && pwd && ls -la ~ | head -5"
    ]
    
    return_code, stdout, stderr = run_command(cmd, timeout=15)
    
    return {
        "hostname": hostname,
        "verification_attempted": True,
        "return_code": return_code,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "home_directory_accessible": return_code == 0,
        "detected_home_dir": stdout.split('\n')[0] if stdout and return_code == 0 else None
    }


def run_comprehensive_test(hostname: str, username: str, clean_keys: bool = False) -> Dict:
    """Run comprehensive SSH key automation test."""
    
    test_results = {
        "test_metadata": {
            "hostname": hostname,
            "username": username,
            "timestamp": datetime.now().isoformat(),
            "test_type": "ssh_key_automation_comprehensive",
            "clean_keys_requested": clean_keys
        }
    }
    
    # Step 1: Check existing SSH keys
    print("=== Step 1: Checking existing SSH keys ===")
    test_results["existing_keys"] = check_existing_ssh_keys()
    
    # Step 2: Backup and clean keys if requested
    if clean_keys:
        print("=== Step 2: Backing up and cleaning existing keys ===")
        test_results["key_backup"] = backup_ssh_keys()
    
    # Step 3: Test initial SSH connectivity (should fail without keys)
    print("=== Step 3: Testing initial SSH connectivity ===")
    test_results["initial_connectivity"] = test_ssh_connectivity(hostname, username)
    
    # Step 4: Verify home directory
    print("=== Step 4: Verifying home directory ===")
    test_results["home_directory"] = verify_home_directory(hostname, username)
    
    # Step 5: Test clustrix SSH automation
    print("=== Step 5: Testing clustrix SSH automation ===")
    test_results["ssh_automation"] = test_clustrix_ssh_automation(hostname, username)
    
    # Step 6: Test post-setup connectivity
    print("=== Step 6: Testing post-setup SSH connectivity ===")
    test_results["post_setup_connectivity"] = test_post_setup_connectivity(hostname, username)
    
    # Step 7: Overall assessment
    automation_success = test_results["ssh_automation"].get("success", False)
    post_setup_success = test_results["post_setup_connectivity"].get("key_auth_successful", False)
    
    test_results["overall_assessment"] = {
        "ssh_automation_completed": automation_success,
        "post_setup_auth_working": post_setup_success,
        "issue_57_resolved": automation_success and post_setup_success,
        "test_status": "SUCCESS" if (automation_success and post_setup_success) else "FAILED",
        "primary_issue": "SSH key authentication still failing" if automation_success and not post_setup_success else None
    }
    
    return test_results


def main():
    parser = argparse.ArgumentParser(description="Test SSH key automation for Issue 57")
    parser.add_argument("--cluster", choices=["ndoli", "tensor01", "both"], default="both",
                        help="Which cluster to test")
    parser.add_argument("--clean-keys", action="store_true",
                        help="Backup and remove existing SSH keys before testing")
    parser.add_argument("--username", default="f002d6b",
                        help="Username for cluster access")
    
    args = parser.parse_args()
    
    # Define cluster configurations
    clusters = {
        "ndoli": "ndoli.dartmouth.edu",
        "tensor01": "tensor01.dartmouth.edu"
    }
    
    if args.cluster == "both":
        test_clusters = ["ndoli", "tensor01"]
    else:
        test_clusters = [args.cluster]
    
    all_results = {}
    
    for cluster_name in test_clusters:
        hostname = clusters[cluster_name]
        print(f"\n{'='*60}")
        print(f"TESTING CLUSTER: {cluster_name} ({hostname})")
        print(f"{'='*60}")
        
        try:
            results = run_comprehensive_test(hostname, args.username, args.clean_keys)
            all_results[cluster_name] = results
            
            # Print summary
            overall = results["overall_assessment"]
            print(f"\n--- SUMMARY FOR {cluster_name.upper()} ---")
            print(f"SSH Automation: {'✅ SUCCESS' if overall['ssh_automation_completed'] else '❌ FAILED'}")
            print(f"Post-Setup Auth: {'✅ SUCCESS' if overall['post_setup_auth_working'] else '❌ FAILED'}")
            print(f"Issue 57 Status: {'✅ RESOLVED' if overall['issue_57_resolved'] else '❌ NOT RESOLVED'}")
            
            if overall.get("primary_issue"):
                print(f"Primary Issue: {overall['primary_issue']}")
            
        except Exception as e:
            print(f"ERROR testing {cluster_name}: {e}")
            all_results[cluster_name] = {"error": str(e)}
    
    # Save detailed results
    results_file = f"ssh_key_automation_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    results_path = Path(__file__).parent / results_file
    
    with open(results_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print(f"\n📁 Detailed results saved to: {results_path}")
    
    # Final summary
    print(f"\n{'='*60}")
    print("FINAL TEST SUMMARY")
    print(f"{'='*60}")
    
    for cluster_name, results in all_results.items():
        if "error" in results:
            print(f"{cluster_name}: ❌ ERROR - {results['error']}")
        else:
            overall = results["overall_assessment"]
            status = "✅ SUCCESS" if overall["issue_57_resolved"] else "❌ FAILED"
            print(f"{cluster_name}: {status}")


if __name__ == "__main__":
    main()