"""
Test SLURM job script generation for ndoli.
"""

import pytest
import paramiko
import time
from tests.real_world import credentials
from clustrix.utils import create_job_script
from clustrix.config import ClusterConfig


@pytest.mark.real_world
def test_generate_slurm_script_for_ndoli():
    """Test generating SLURM job script for ndoli."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Create a test configuration
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        remote_work_dir="/tmp/clustrix_slurm_test",
        python_executable="python3",
        cleanup_on_success=True,
        job_poll_interval=5,
    )

    # Create a job config like our test
    job_config = {
        "cores": 2,
        "memory": "2GB",
        "time": "00:10:00",
        "partition": "standard",
    }

    # Generate the job script
    script = create_job_script(
        "slurm", job_config, "/tmp/clustrix_slurm_test/job_test123", config
    )

    print("Generated SLURM job script:")
    print("=" * 60)
    print(script)
    print("=" * 60)

    # Check if the script looks correct
    assert "#!/bin/bash" in script
    assert "#SBATCH" in script
    assert "--cpus-per-task=2" in script
    assert "--mem=2GB" in script
    assert "--partition=standard" in script
    assert "python3" in script

    # Now let's also check what happens when we submit a simple job manually
    print("\nTesting simple SLURM job submission...")

    # Connect via SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh_client.connect(
        hostname=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        port=22,
    )

    # Create a simple test script
    simple_script = """#!/bin/bash
#SBATCH --job-name=test_simple
#SBATCH --partition=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=1GB
#SBATCH --time=00:05:00
#SBATCH --output=/tmp/test_simple_%j.out
#SBATCH --error=/tmp/test_simple_%j.err

echo "Starting simple test job"
echo "Hostname: $(hostname)"
echo "Date: $(date)"
echo "Python version: $(python3 --version)"
echo "Working directory: $(pwd)"
echo "User: $(whoami)"
echo "Environment variables:"
env | grep SLURM | head -10
echo "Job completed successfully"
"""

    # Write the script to a file
    script_path = "/tmp/simple_test_script.sh"
    stdin, stdout, stderr = ssh_client.exec_command(
        f"cat > {script_path} << 'EOF'\n{simple_script}\nEOF"
    )
    stdout.channel.recv_exit_status()

    # Make it executable
    stdin, stdout, stderr = ssh_client.exec_command(f"chmod +x {script_path}")
    stdout.channel.recv_exit_status()

    # Submit the job
    stdin, stdout, stderr = ssh_client.exec_command(f"sbatch {script_path}")
    submit_output = stdout.read().decode().strip()
    submit_error = stderr.read().decode().strip()

    print(f"Job submission output: {submit_output}")
    print(f"Job submission error: {submit_error}")

    # Extract job ID if successful
    if "Submitted batch job" in submit_output:
        job_id = submit_output.split()[-1]
        print(f"Job ID: {job_id}")

        # Wait a bit and check job status
        time.sleep(10)

        stdin, stdout, stderr = ssh_client.exec_command(f"squeue -j {job_id}")
        queue_status = stdout.read().decode().strip()
        print(f"Queue status: {queue_status}")

        # Check job accounting
        stdin, stdout, stderr = ssh_client.exec_command(
            f"sacct -j {job_id} --format=JobID,JobName,State,ExitCode,Start,End,Elapsed"
        )
        job_status = stdout.read().decode().strip()
        print(f"Job accounting: {job_status}")

        # Check for output files
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la /tmp/test_simple_{job_id}.out /tmp/test_simple_{job_id}.err 2>/dev/null || echo 'No output files found'"
        )
        output_files = stdout.read().decode().strip()
        print(f"Output files: {output_files}")

        # Read output if available
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cat /tmp/test_simple_{job_id}.out 2>/dev/null || echo 'No output file'"
        )
        job_output = stdout.read().decode().strip()
        print(f"Job output: {job_output}")

        # Read error if available
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cat /tmp/test_simple_{job_id}.err 2>/dev/null || echo 'No error file'"
        )
        job_error = stdout.read().decode().strip()
        print(f"Job error: {job_error}")

    ssh_client.close()

    print("\n=== SLURM Script Generation Test Complete ===")
    return True
