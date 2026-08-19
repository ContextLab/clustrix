"""
Check detailed SLURM job logs and files on ndoli.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_check_detailed_slurm_logs():
    """Check detailed SLURM job logs and files."""
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

    print("Checking detailed SLURM logs and files...")

    # Check the clustrix_slurm_working directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp/clustrix_slurm_working -type f -name '*' 2>/dev/null | head -20"
    )
    slurm_files = stdout.read().decode().strip()
    print(f"Files in SLURM working directory:\n{slurm_files}")

    # Check for any SLURM output files
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'slurm-*.out' -user $(whoami) -mtime -1 2>/dev/null | head -10"
    )
    slurm_outputs = stdout.read().decode().strip()
    print(f"Recent SLURM output files:\n{slurm_outputs}")

    # Check for any SLURM error files
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'slurm-*.err' -user $(whoami) -mtime -1 2>/dev/null | head -10"
    )
    slurm_errors = stdout.read().decode().strip()
    print(f"Recent SLURM error files:\n{slurm_errors}")

    # Check the content of the most recent job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -la /tmp/clustrix_slurm_working/job_* 2>/dev/null | tail -20"
    )
    job_dirs = stdout.read().decode().strip()
    print(f"Recent job directories:\n{job_dirs}")

    # Get the most recent job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -1td /tmp/clustrix_slurm_working/job_* 2>/dev/null | head -1"
    )
    latest_job_dir = stdout.read().decode().strip()

    if latest_job_dir:
        print(f"\nChecking latest job directory: {latest_job_dir}")

        # List all files in the latest job directory
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}' 2>/dev/null"
        )
        job_files = stdout.read().decode().strip()
        print(f"Files in latest job directory:\n{job_files}")

        # Check for job script
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/job.sh' 2>/dev/null"
        )
        job_script_exists = stdout.read().decode().strip()
        print(f"Job script: {job_script_exists}")

        # Read the job script if it exists
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cat '{latest_job_dir}/job.sh' 2>/dev/null | head -50"
        )
        job_script_content = stdout.read().decode().strip()
        print(f"Job script content (first 50 lines):\n{job_script_content}")

        # Check for any output files in the job directory
        stdin, stdout, stderr = ssh_client.exec_command(
            f"find '{latest_job_dir}' -name 'slurm-*.out' -o -name 'slurm-*.err' 2>/dev/null"
        )
        job_output_files = stdout.read().decode().strip()
        print(f"SLURM output files in job directory:\n{job_output_files}")

        # Read any output files
        if job_output_files:
            for output_file in job_output_files.split("\n")[:3]:  # First 3 files
                if output_file.strip():
                    stdin, stdout, stderr = ssh_client.exec_command(
                        f"cat '{output_file.strip()}' 2>/dev/null"
                    )
                    file_content = stdout.read().decode().strip()
                    print(f"\n--- Content of {output_file} ---")
                    print(file_content)

        # Check for any error files in the job directory
        stdin, stdout, stderr = ssh_client.exec_command(
            f"find '{latest_job_dir}' -name '*.pkl' -o -name 'error*' -o -name 'result*' 2>/dev/null"
        )
        result_files = stdout.read().decode().strip()
        print(f"Result/error files in job directory:\n{result_files}")

        # Check virtual environments
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/venv'* 2>/dev/null"
        )
        venv_dirs = stdout.read().decode().strip()
        print(f"Virtual environments:\n{venv_dirs}")

    ssh_client.close()

    print("\n=== Detailed SLURM Log Analysis Complete ===")
    return True
