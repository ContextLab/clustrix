#!/usr/bin/env python3
"""Debug SLURM job issues on the cluster."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
import paramiko


def main():
    # Get credentials
    creds = ValidationCredentials()
    cluster_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")

    if not cluster_creds:
        print("No credentials found")
        return 1

    hostname = cluster_creds.get("hostname")
    username = cluster_creds.get("username")
    password = cluster_creds.get("password")

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh_client.connect(
            hostname=hostname, username=username, password=password, timeout=30
        )
        print("Connected to SLURM cluster")

        # Check recent jobs
        print("\n📋 Recent SLURM jobs:")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"sacct -u {username} --format=JobID,JobName,State,ExitCode,Start,End -n | tail -10"
        )
        output = stdout.read().decode().strip()
        print(output)

        # Check for clustrix test directories
        print("\n📁 Clustrix test directories:")
        stdin, stdout, stderr = ssh_client.exec_command(
            "ls -la /tmp/clustrix_* 2>/dev/null | head -20"
        )
        output = stdout.read().decode().strip()
        if output:
            print(output)
        else:
            print("No test directories found")

        # Submit a minimal test job
        print("\n🧪 Submitting minimal test job...")
        test_dir = f"/tmp/clustrix_debug_{int(time.time())}"
        stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {test_dir}")

        job_script = f"""#!/bin/bash
#SBATCH --job-name=debug_test
#SBATCH --time=00:01:00
#SBATCH --output={test_dir}/output.txt
#SBATCH --error={test_dir}/error.txt

echo "Test output"
pwd
ls -la
echo "Done"
"""

        # Write and submit
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && echo '{job_script}' > job.sh && sbatch job.sh"
        )
        submit_output = stdout.read().decode().strip()
        submit_error = stderr.read().decode().strip()

        print(f"Submit output: {submit_output}")
        if submit_error:
            print(f"Submit error: {submit_error}")

        if "Submitted batch job" in submit_output:
            job_id = submit_output.split()[-1]
            print(f"Job ID: {job_id}")

            # Wait a bit
            print("Waiting for job to complete...")
            time.sleep(10)

            # Check output
            stdin, stdout, stderr = ssh_client.exec_command(f"ls -la {test_dir}/")
            print(f"Directory contents: {stdout.read().decode()}")

            stdin, stdout, stderr = ssh_client.exec_command(
                f"cat {test_dir}/output.txt 2>/dev/null"
            )
            output_content = stdout.read().decode().strip()
            if output_content:
                print(f"Output file content:\n{output_content}")
            else:
                print("No output file found")

                # Check with different output path
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"find {test_dir} -name '*.out' -o -name '*.txt' 2>/dev/null"
                )
                files = stdout.read().decode().strip()
                print(f"Found files: {files}")

        ssh_client.close()
        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
