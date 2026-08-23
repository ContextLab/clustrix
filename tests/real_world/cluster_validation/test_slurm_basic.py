#!/usr/bin/env python3
"""
Basic SLURM submission test - minimal validation
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import paramiko

from tests.real_world.credential_manager import (
    require_cluster_credentials,
    require_test_remote_work_dir,
)
from clustrix.ssh_security import configure_host_key_policy


def test_basic_slurm_submission():
    """Test basic SLURM submission without Clustrix abstractions."""
    print("🚀 Basic SLURM Test")
    print("=" * 50)

    # Credentials come from ~/.clustrix/.env or the environment; skip loudly
    # rather than return False, which pytest reports as a pass.
    slurm_creds = require_cluster_credentials("slurm")

    hostname = slurm_creds["host"]
    username = slurm_creds["username"]
    password = slurm_creds.get("password")

    # Connect via SSH
    ssh = paramiko.SSHClient()
    configure_host_key_policy(ssh)

    try:
        ssh.connect(hostname, username=username, password=password)
        print(f"✅ Connected to {hostname}")

        # Create test directory
        test_dir = f"{require_test_remote_work_dir()}/clustrix_test_{int(time.time())}"
        stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {test_dir}")

        # Create simple SLURM script
        slurm_script = f"""#!/bin/bash
#SBATCH --job-name=clustrix_test
#SBATCH --output={test_dir}/output.txt
#SBATCH --error={test_dir}/error.txt
#SBATCH --time=00:05:00
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1GB

echo "Clustrix SLURM test starting"
echo "Hostname: $(hostname)"
echo "Date: $(date)"
echo "Python3 location: $(which python3)"
echo "Python3 version: $(python3 --version)"

# Simple Python test
python3 -c "print('Hello from Clustrix SLURM test!')"
python3 -c "import sys; print(f'Python {{sys.version}}')"
python3 -c "result = 42 + 58; print(f'Computation result: {{result}}')"

echo "Test completed"
"""

        # Write script
        sftp = ssh.open_sftp()
        with sftp.open(f"{test_dir}/test.slurm", "w") as f:
            f.write(slurm_script)
        sftp.close()

        # Submit job
        stdin, stdout, stderr = ssh.exec_command(f"cd {test_dir} && sbatch test.slurm")
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if "Submitted batch job" in output:
            job_id = output.split()[-1]
            print(f"✅ Job submitted: {job_id}")

            # Wait for completion
            print("⏳ Waiting for job completion...")
            for i in range(30):  # Wait up to 150 seconds
                stdin, stdout, stderr = ssh.exec_command(f"squeue -j {job_id} -h")
                status = stdout.read().decode().strip()

                if not status:  # Job finished
                    print("✅ Job completed")

                    # Check output
                    stdin, stdout, stderr = ssh.exec_command(
                        f"cat {test_dir}/output.txt"
                    )
                    output = stdout.read().decode()
                    print("\n📄 Job Output:")
                    print("-" * 40)
                    print(output)
                    print("-" * 40)

                    # Check for errors
                    stdin, stdout, stderr = ssh.exec_command(
                        f"cat {test_dir}/error.txt"
                    )
                    errors = stdout.read().decode().strip()
                    if errors:
                        print(f"\n⚠️  Errors:\n{errors}")

                    # Cleanup
                    stdin, stdout, stderr = ssh.exec_command(f"rm -rf {test_dir}")

                    return "Computation result: 100" in output

                time.sleep(5)

            print("⏱️  Job timed out")
            return False
        else:
            print(f"❌ Job submission failed: {output} {error}")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    finally:
        ssh.close()


if __name__ == "__main__":
    success = test_basic_slurm_submission()
    if success:
        print("\n🎉 Basic SLURM test PASSED!")
        print("SLURM job submission and Python execution working correctly.")
    else:
        print("\n❌ Basic SLURM test FAILED!")
