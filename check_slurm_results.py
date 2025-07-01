#!/usr/bin/env python3
"""
Check actual SLURM job result files to see if filesystem operations are working.
"""

import os
import sys
import paramiko
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clustrix.secure_credentials import ValidationCredentials

def main():
    print("🔍 Checking SLURM job result files...")
    
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
        
        # Look for recent result files
        result_dir = "/dartfs-hpc/rc/home/b/f002d6b/clustrix/packaging_tests"
        
        print(f"\n📂 Looking for recent result files (jobs 5230*):")
        
        # Look specifically for recent jobs
        stdin, stdout, stderr = ssh_client.exec_command(f"find {result_dir} -name 'result_*_523*.json' | sort")
        recent_files = stdout.read().decode().strip().split('\n')
        
        print(f"📂 Recent result files: {recent_files}")
        
        # Also check temp directories for result files
        stdin, stdout, stderr = ssh_client.exec_command(f"find /tmp -name 'result.pkl' -newer /tmp 2>/dev/null | head -5")
        temp_results = stdout.read().decode().strip()
        if temp_results:
            print(f"📂 Temp result files: {temp_results}")
        
        result_files = recent_files
        
        for result_file in result_files:
            if result_file.strip():
                print(f"\n📄 Contents of {result_file}:")
                stdin, stdout, stderr = ssh_client.exec_command(f"cat {result_file}")
                content = stdout.read().decode()
                try:
                    result_data = json.loads(content)
                    print(json.dumps(result_data, indent=2))
                except:
                    print(content)
    
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh_client.close()

if __name__ == "__main__":
    main()