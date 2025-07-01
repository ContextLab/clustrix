#!/usr/bin/env python3
"""
Check error pickle file from failed job
"""

import sys
from pathlib import Path
import pickle

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
import paramiko

def check_error_pickle():
    """Download and check the error pickle file."""
    print("🔍 Checking Error Pickle")
    print("=" * 30)
    
    # Get credentials
    creds = ValidationCredentials()
    slurm_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")
    
    if not slurm_creds:
        print("❌ No credentials found")
        return
    
    hostname = slurm_creds.get("hostname")
    username = slurm_creds.get("username")
    password = slurm_creds.get("password")
    
    # Connect via SSH
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(hostname, username=username, password=password)
        print(f"✅ Connected to {hostname}")
        
        work_dir = f"/dartfs-hpc/rc/home/b/{username}/clustrix"
        
        # Get most recent job directory
        stdin, stdout, stderr = ssh.exec_command(f"ls -dt {work_dir}/job_* 2>/dev/null | head -1")
        recent_job = stdout.read().decode().strip()
        
        if recent_job:
            print(f"📂 Job directory: {recent_job}")
            
            # Download error.pkl
            sftp = ssh.open_sftp()
            local_error_file = "/tmp/clustrix_error.pkl"
            
            try:
                sftp.get(f"{recent_job}/error.pkl", local_error_file)
                print(f"📥 Downloaded error.pkl to {local_error_file}")
                
                # Read and display error
                with open(local_error_file, 'rb') as f:
                    error_data = pickle.load(f)
                
                print(f"\n❌ Error: {error_data.get('error', 'Unknown error')}")
                print(f"\n📋 Traceback:")
                print(error_data.get('traceback', 'No traceback available'))
                
            except Exception as e:
                print(f"❌ Could not download/read error.pkl: {e}")
            finally:
                sftp.close()
                
        else:
            print("❌ No recent job directories found")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    check_error_pickle()