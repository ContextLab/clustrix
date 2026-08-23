#!/usr/bin/env python3
"""
Check error pickle file from failed job
"""

import sys
from pathlib import Path
import pickle

sys.path.insert(0, str(Path(__file__).parent.parent))

import paramiko

from tests.real_world.credential_manager import (
    CREDENTIAL_SETUP_HINT,
    get_cluster_credentials,
    require_test_remote_work_dir,
)
from clustrix.ssh_security import configure_host_key_policy


def check_error_pickle():
    """Download and check the error pickle file."""
    print("🔍 Checking Error Pickle")
    print("=" * 30)

    # Credentials come from ~/.clustrix/.env or the environment.
    slurm_creds = get_cluster_credentials("slurm")

    if not slurm_creds:
        print(f"❌ No SLURM cluster credentials found. {CREDENTIAL_SETUP_HINT}")
        return

    hostname = slurm_creds["host"]
    username = slurm_creds["username"]
    password = slurm_creds.get("password")

    # Connect via SSH
    ssh = paramiko.SSHClient()
    configure_host_key_policy(ssh)

    try:
        ssh.connect(hostname, username=username, password=password)
        print(f"✅ Connected to {hostname}")

        work_dir = f"{require_test_remote_work_dir()}/clustrix"

        # Get most recent job directory
        stdin, stdout, stderr = ssh.exec_command(
            f"ls -dt {work_dir}/job_* 2>/dev/null | head -1"
        )
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
                with open(local_error_file, "rb") as f:
                    error_data = pickle.load(f)

                print(f"\n❌ Error: {error_data.get('error', 'Unknown error')}")
                print(f"\n📋 Traceback:")
                print(error_data.get("traceback", "No traceback available"))

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
