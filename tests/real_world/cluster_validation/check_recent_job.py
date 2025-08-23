#!/usr/bin/env python3
"""
Check most recent job files and logs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
import paramiko


def check_recent_job():
    """Check the most recent job files and logs."""
    print("🔍 Checking Recent Job")
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
        stdin, stdout, stderr = ssh.exec_command(
            f"ls -dt {work_dir}/job_* 2>/dev/null | head -1"
        )
        recent_job = stdout.read().decode().strip()

        if recent_job:
            print(f"📂 Most recent job: {recent_job}")

            # List files
            stdin, stdout, stderr = ssh.exec_command(f"ls -la {recent_job}/")
            files = stdout.read().decode().strip()
            print(f"\n📄 Files:")
            for line in files.split("\n"):
                print(f"   {line}")

            # Check specific files
            file_checks = [
                ("job.sh", "Job Script"),
                ("slurm-*.out", "SLURM Output"),
                ("slurm-*.err", "SLURM Error"),
                ("function_data.pkl", "Function Data"),
                ("result.pkl", "Result"),
                ("error.pkl", "Error Result"),
            ]

            for pattern, description in file_checks:
                print(f"\n📋 {description}:")
                stdin, stdout, stderr = ssh.exec_command(
                    f"find {recent_job}/ -name '{pattern}' -exec echo 'FILE: {{}}' \\; -exec cat {{}} \\; 2>/dev/null"
                )
                content = stdout.read().decode().strip()
                if content:
                    print(content)
                else:
                    print(f"   No {pattern} files found")

        else:
            print("❌ No recent job directories found")

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh.close()


if __name__ == "__main__":
    check_recent_job()
