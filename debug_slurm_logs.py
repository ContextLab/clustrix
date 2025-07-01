#!/usr/bin/env python3
"""
Debug script to examine SLURM job logs and understand dependency installation issues.
"""

import os
import sys
import paramiko
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clustrix.secure_credentials import ValidationCredentials

def main():
    print("🔍 Debugging SLURM job logs...")
    
    # Get SSH credentials
    val_creds = ValidationCredentials()
    ssh_creds = val_creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")
    
    if not ssh_creds:
        print("❌ Could not get SSH credentials")
        return
    
    # Connect to cluster
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        hostname = ssh_creds.get("hostname", "ndoli.dartmouth.edu")
        username = ssh_creds.get("username", "f002d6b")
        password = ssh_creds.get("password")
        
        ssh_client.connect(hostname=hostname, username=username, password=password, timeout=30)
        print(f"✅ Connected to {hostname}")
        
        # List recent log files
        log_dir = "/dartfs-hpc/rc/home/b/f002d6b/clustrix/packaging_tests/logs"
        
        print(f"\n📂 Listing log files in {log_dir}:")
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -la {log_dir} | tail -20")
        output = stdout.read().decode().strip()
        print(output)
        
        # Get the most recent filesystem log file
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -t {log_dir}/clustrix_pkg_filesystem_*.out | head -1")
        latest_filesystem_log = stdout.read().decode().strip()
        
        if latest_filesystem_log:
            print(f"\n📄 Contents of latest filesystem log: {latest_filesystem_log}")
            stdin, stdout, stderr = ssh_client.exec_command(f"cat {latest_filesystem_log}")
            log_content = stdout.read().decode()
            print("=" * 80)
            print(log_content)
            print("=" * 80)
        
        # Also check error log
        if latest_filesystem_log:
            error_log = latest_filesystem_log.replace('.out', '.err')
            print(f"\n⚠️  Contents of error log: {error_log}")
            stdin, stdout, stderr = ssh_client.exec_command(f"cat {error_log}")
            error_content = stdout.read().decode()
            if error_content.strip():
                print("=" * 80)
                print(error_content)
                print("=" * 80)
            else:
                print("(No error log content)")
    
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh_client.close()

if __name__ == "__main__":
    main()