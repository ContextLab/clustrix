"""
Check the latest SLURM job that was just submitted.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_check_latest_slurm_job():
    """Check the latest SLURM job status and results."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Connect via SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh_client.connect(
        hostname=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        port=22,
    )

    print("Checking latest SLURM job...")

    # Check job 5349528 specifically
    job_id = "5349528"

    # Check job status
    stdin, stdout, stderr = ssh_client.exec_command(
        f"sacct -j {job_id} --format=JobID,JobName,State,ExitCode,Start,End,Elapsed"
    )
    job_status = stdout.read().decode().strip()
    print(f"Job {job_id} status:\n{job_status}")

    # Check if there's a corresponding job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -1td /tmp/clustrix_slurm_working/job_* 2>/dev/null | head -1"
    )
    latest_job_dir = stdout.read().decode().strip()
    print(f"Latest job directory: {latest_job_dir}")

    if latest_job_dir:
        # Check if result.pkl exists in the latest job directory
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/result.pkl' 2>/dev/null || echo 'No result.pkl'"
        )
        result_check = stdout.read().decode().strip()
        print(f"Result file check: {result_check}")

        # Check for SLURM output files in the job directory
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}'/slurm-*.out '{latest_job_dir}'/slurm-*.err 2>/dev/null || echo 'No SLURM output files'"
        )
        slurm_files = stdout.read().decode().strip()
        print(f"SLURM output files: {slurm_files}")

        # If SLURM output files exist, read them
        if "No SLURM output files" not in slurm_files:
            print("Reading SLURM output files...")
            stdin, stdout, stderr = ssh_client.exec_command(
                f"cat '{latest_job_dir}'/slurm-*.out 2>/dev/null || echo 'No stdout'"
            )
            slurm_stdout = stdout.read().decode().strip()
            print(f"SLURM stdout:\n{slurm_stdout}")

            stdin, stdout, stderr = ssh_client.exec_command(
                f"cat '{latest_job_dir}'/slurm-*.err 2>/dev/null || echo 'No stderr'"
            )
            slurm_stderr = stdout.read().decode().strip()
            print(f"SLURM stderr:\n{slurm_stderr}")

        # If result.pkl exists, try to read it
        if "No result.pkl" not in result_check:
            print("Reading result.pkl...")
            read_result_cmd = f"""
cd '{latest_job_dir}'
python3 -c "
import pickle
try:
    with open('result.pkl', 'rb') as f:
        result = pickle.load(f)
    print('Result:', result)
except Exception as e:
    print('Error reading result:', str(e))
"
"""
            stdin, stdout, stderr = ssh_client.exec_command(read_result_cmd)
            result_output = stdout.read().decode().strip()
            print(f"Result content: {result_output}")

    # Also check if there are any SLURM output files in /tmp or the user's home directory
    stdin, stdout, stderr = ssh_client.exec_command(
        f"find /tmp -name 'slurm-{job_id}.out' -o -name 'slurm-{job_id}.err' 2>/dev/null || echo 'No files found'"
    )
    global_slurm_files = stdout.read().decode().strip()
    print(f"Global SLURM files for job {job_id}: {global_slurm_files}")

    # Check the user's home directory for SLURM files
    stdin, stdout, stderr = ssh_client.exec_command(
        f"find ~ -name 'slurm-{job_id}.out' -o -name 'slurm-{job_id}.err' 2>/dev/null || echo 'No files in home'"
    )
    home_slurm_files = stdout.read().decode().strip()
    print(f"SLURM files in home directory: {home_slurm_files}")

    ssh_client.close()

    print("\n=== Latest Job Check Complete ===")
    return True
