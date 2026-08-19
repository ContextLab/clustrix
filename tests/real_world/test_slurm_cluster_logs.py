"""
Check SLURM job logs on ndoli to debug failures.
"""

import pytest
import paramiko
import time
from tests.real_world import credentials


@pytest.mark.real_world
def test_check_slurm_job_logs():
    """Check recent SLURM job logs to debug failures."""
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

    # Check recent job history for our user
    print("Checking recent SLURM job history...")

    # Get recent jobs
    stdin, stdout, stderr = ssh_client.exec_command(
        "sacct -u $(whoami) --format=JobID,JobName,State,ExitCode,Start,End,Elapsed,ReqMem,MaxRSS -S today"
    )
    job_history = stdout.read().decode().strip()
    print(f"Recent jobs:\n{job_history}")

    # Get any failed jobs
    stdin, stdout, stderr = ssh_client.exec_command(
        "sacct -u $(whoami) --state=FAILED --format=JobID,JobName,State,ExitCode,Start,End,Elapsed,ReqMem,MaxRSS -S today"
    )
    failed_jobs = stdout.read().decode().strip()
    print(f"\nFailed jobs:\n{failed_jobs}")

    # Check for clustrix-related jobs
    stdin, stdout, stderr = ssh_client.exec_command(
        "sacct -u $(whoami) --name=clustrix --format=JobID,JobName,State,ExitCode,Start,End,Elapsed,ReqMem,MaxRSS -S today"
    )
    clustrix_jobs = stdout.read().decode().strip()
    print(f"\nClustriX jobs:\n{clustrix_jobs}")

    # Check for any job output files in temp directories
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'slurm-*.out' -user $(whoami) -newer /tmp 2>/dev/null | head -10"
    )
    output_files = stdout.read().decode().strip()
    print(f"\nRecent SLURM output files:\n{output_files}")

    # Check if there are any job files
    if output_files:
        print("\nContents of recent SLURM output files:")
        for output_file in output_files.split("\n")[:3]:  # Check first 3 files
            if output_file.strip():
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cat '{output_file.strip()}' 2>/dev/null || echo 'Could not read file'"
                )
                file_content = stdout.read().decode().strip()
                print(f"\n--- {output_file} ---")
                print(file_content)

    # Check for error files
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'slurm-*.err' -user $(whoami) -newer /tmp 2>/dev/null | head -5"
    )
    error_files = stdout.read().decode().strip()
    print(f"\nRecent SLURM error files:\n{error_files}")

    if error_files:
        print("\nContents of recent SLURM error files:")
        for error_file in error_files.split("\n")[:3]:  # Check first 3 files
            if error_file.strip():
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cat '{error_file.strip()}' 2>/dev/null || echo 'Could not read file'"
                )
                file_content = stdout.read().decode().strip()
                print(f"\n--- {error_file} ---")
                print(file_content)

    # Check for any clustrix directories
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'clustrix*' -user $(whoami) -type d 2>/dev/null | head -10"
    )
    clustrix_dirs = stdout.read().decode().strip()
    print(f"\nClustriX directories:\n{clustrix_dirs}")

    if clustrix_dirs:
        print("\nContents of ClustriX directories:")
        for clustrix_dir in clustrix_dirs.split("\n")[:3]:  # Check first 3 directories
            if clustrix_dir.strip():
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"ls -la '{clustrix_dir.strip()}' 2>/dev/null || echo 'Could not list directory'"
                )
                dir_content = stdout.read().decode().strip()
                print(f"\n--- {clustrix_dir} ---")
                print(dir_content)

    ssh_client.close()

    print("\n=== SLURM Job Log Analysis Complete ===")
    return True
