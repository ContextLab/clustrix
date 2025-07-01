#!/usr/bin/env python3
"""
Check SLURM logs for failed job
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
import paramiko

def check_slurm_logs():
    """Check SLURM logs for the failed job."""
    print("📋 Checking SLURM Logs")
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
        
        # List recent job directories
        print(f"\n📁 Checking job directories in {work_dir}")
        stdin, stdout, stderr = ssh.exec_command(f"ls -lat {work_dir}/ | head -10")
        output = stdout.read().decode().strip()
        print(output)
        
        # Check for most recent job directory
        stdin, stdout, stderr = ssh.exec_command(f"ls -t {work_dir}/job_* 2>/dev/null | head -1")
        recent_job = stdout.read().decode().strip()
        
        if recent_job:
            print(f"\n📂 Most recent job directory: {recent_job}")
            
            # Check SLURM output files
            stdin, stdout, stderr = ssh.exec_command(f"ls -la {recent_job}/")
            files = stdout.read().decode().strip()
            print(f"\n📄 Files in job directory:")
            print(files)
            
            # Check SLURM error file
            stdin, stdout, stderr = ssh.exec_command(f"find {recent_job}/ -name '*.err' -exec cat {{}} \\;")
            errors = stdout.read().decode().strip()
            if errors:
                print(f"\n❌ SLURM Error Log:")
                print(errors)
            
            # Check SLURM output file
            stdin, stdout, stderr = ssh.exec_command(f"find {recent_job}/ -name '*.out' -exec cat {{}} \\;")
            output = stdout.read().decode().strip()
            if output:
                print(f"\n📋 SLURM Output Log:")
                print(output)
                
            # Check job script
            stdin, stdout, stderr = ssh.exec_command(f"find {recent_job}/ -name 'job.sh' -exec cat {{}} \\;")
            script = stdout.read().decode().strip()
            if script:
                print(f"\n📜 Job Script:")
                print(script)
        else:
            print("❌ No recent job directories found")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    check_slurm_logs()