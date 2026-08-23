#!/usr/bin/env python3
"""Debug SLURM output file locations."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import paramiko
from clustrix.ssh_security import configure_host_key_policy
from tests.real_world.credential_manager import (
    CREDENTIAL_SETUP_HINT,
    get_cluster_credentials,
)


def main():
    # Credentials come from ~/.clustrix/.env or the environment.
    cluster_creds = get_cluster_credentials("slurm")

    if not cluster_creds:
        print(f"No SLURM cluster credentials found. {CREDENTIAL_SETUP_HINT}")
        return 1

    hostname = cluster_creds["host"]
    username = cluster_creds["username"]
    password = cluster_creds.get("password")

    ssh_client = paramiko.SSHClient()
    configure_host_key_policy(ssh_client)

    try:
        ssh_client.connect(
            hostname=hostname, username=username, password=password, timeout=30
        )
        print("🔌 Connected to SLURM cluster")

        # Check current directory and environment
        print("\n📍 Checking environment:")
        stdin, stdout, stderr = ssh_client.exec_command("pwd")
        home_dir = stdout.read().decode().strip()
        print(f"Home directory: {home_dir}")

        stdin, stdout, stderr = ssh_client.exec_command("echo $SLURM_SUBMIT_DIR")
        submit_dir = stdout.read().decode().strip()
        print(f"SLURM_SUBMIT_DIR: {submit_dir or 'Not set'}")

        # Submit a test job with various output configurations
        test_id = int(time.time())
        test_dir = f"/tmp/clustrix_output_test_{test_id}"

        stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {test_dir}")
        print(f"\n📁 Created test directory: {test_dir}")

        # Test 1: Default output location
        print("\n🧪 Test 1: Default output location")
        job_script1 = f"""#!/bin/bash
#SBATCH --job-name=test1_{test_id}
#SBATCH --time=00:01:00

echo "Test 1: Default output location"
echo "PWD: $(pwd)"
echo "SLURM_SUBMIT_DIR: $SLURM_SUBMIT_DIR"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"
echo "Output should be in: slurm-$SLURM_JOB_ID.out"
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && echo '{job_script1}' > test1.sh && sbatch test1.sh"
        )
        output = stdout.read().decode().strip()
        if "Submitted" in output:
            job1_id = output.split()[-1]
            print(f"✅ Job 1 submitted: {job1_id}")

        # Test 2: Absolute path output
        print("\n🧪 Test 2: Absolute path output")
        job_script2 = f"""#!/bin/bash
#SBATCH --job-name=test2_{test_id}
#SBATCH --time=00:01:00
#SBATCH --output={test_dir}/test2_output.txt
#SBATCH --error={test_dir}/test2_error.txt

echo "Test 2: Absolute path output"
echo "This should be in {test_dir}/test2_output.txt"
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && echo '{job_script2}' > test2.sh && sbatch test2.sh"
        )
        output = stdout.read().decode().strip()
        if "Submitted" in output:
            job2_id = output.split()[-1]
            print(f"✅ Job 2 submitted: {job2_id}")

        # Test 3: Relative path output
        print("\n🧪 Test 3: Relative path output")
        job_script3 = f"""#!/bin/bash
#SBATCH --job-name=test3_{test_id}
#SBATCH --time=00:01:00
#SBATCH --output=test3_output.txt
#SBATCH --error=test3_error.txt

echo "Test 3: Relative path output"
echo "This should be in the submission directory"
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && echo '{job_script3}' > test3.sh && sbatch test3.sh"
        )
        output = stdout.read().decode().strip()
        if "Submitted" in output:
            job3_id = output.split()[-1]
            print(f"✅ Job 3 submitted: {job3_id}")

        # Test 4: Pattern-based output
        print("\n🧪 Test 4: Pattern-based output")
        job_script4 = f"""#!/bin/bash
#SBATCH --job-name=test4_{test_id}
#SBATCH --time=00:01:00
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

echo "Test 4: Pattern-based output"
echo "This should be in slurm-\\$SLURM_JOB_ID.out"
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && echo '{job_script4}' > test4.sh && sbatch test4.sh"
        )
        output = stdout.read().decode().strip()
        if "Submitted" in output:
            job4_id = output.split()[-1]
            print(f"✅ Job 4 submitted: {job4_id}")

        # Wait for jobs to complete
        print("\n⏳ Waiting for jobs to complete...")
        time.sleep(15)

        # Check where output files were created
        print("\n📊 Checking output file locations:")

        # Check test directory
        print(f"\n📁 Contents of {test_dir}:")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la {test_dir}/ | head -20"
        )
        print(stdout.read().decode())

        # Check home directory
        print(f"\n📁 SLURM output files in home directory:")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {home_dir} && ls -la slurm-*.out 2>/dev/null | tail -10"
        )
        output = stdout.read().decode().strip()
        if output:
            print(output)
        else:
            print("No slurm-*.out files found in home directory")

        # Check current directory
        print(f"\n📁 Checking current directory for outputs:")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la slurm-*.out test*_output.txt 2>/dev/null | tail -10"
        )
        output = stdout.read().decode().strip()
        if output:
            print(output)

        # Try to find any output files
        print("\n🔍 Searching for output files:")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"find {test_dir} {home_dir} -name '*{test_id}*.out' -o -name '*{test_id}*.txt' 2>/dev/null | head -20"
        )
        found_files = stdout.read().decode().strip()
        if found_files:
            print("Found files:")
            print(found_files)

            # Cat the first found file
            first_file = found_files.split("\n")[0]
            print(f"\n📄 Content of {first_file}:")
            stdin, stdout, stderr = ssh_client.exec_command(f"cat {first_file}")
            print(stdout.read().decode()[:500])

        # Check job status
        print("\n📋 Job status:")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"sacct --format=JobID,JobName,State,ExitCode,WorkDir -j {job1_id},{job2_id},{job3_id},{job4_id} 2>/dev/null"
        )
        print(stdout.read().decode())

        # Cleanup
        stdin, stdout, stderr = ssh_client.exec_command(f"rm -rf {test_dir}")
        print("\n🧹 Cleaned up test directory")

        ssh_client.close()
        return 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
