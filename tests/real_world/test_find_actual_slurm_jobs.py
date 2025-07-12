"""
Find and check actual SLURM jobs running on ndoli.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_find_actual_slurm_jobs():
    """Find and check actual SLURM jobs on ndoli."""
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

    print("Finding actual SLURM jobs on ndoli...")

    # Check all clustrix directories
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'clustrix*' -type d 2>/dev/null | sort"
    )
    clustrix_dirs = stdout.read().decode().strip()
    print(f"All clustrix directories:\n{clustrix_dirs}")

    # Check the most promising directories for job subdirectories
    potential_dirs = [
        "/tmp/clustrix_slurm_working",
        "/tmp/clustrix_ndoli_ssh_331ab7a0",
        "/tmp/clustrix_ndoli_ssh_5a76d868",
    ]

    for base_dir in potential_dirs:
        print(f"\n=== Checking {base_dir} ===")

        # Check if directory exists and list contents
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{base_dir}' 2>/dev/null || echo 'Directory does not exist'"
        )
        dir_contents = stdout.read().decode().strip()
        print(f"Directory contents:\n{dir_contents}")

        # Look for job directories
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -1td '{base_dir}'/job_* 2>/dev/null | head -5"
        )
        job_dirs = stdout.read().decode().strip()
        if job_dirs:
            print(f"Job directories found:\n{job_dirs}")

            # Check the latest job directory
            latest_job_dir = job_dirs.split("\n")[0]
            print(f"Latest job directory: {latest_job_dir}")

            # Check job directory contents
            stdin, stdout, stderr = ssh_client.exec_command(
                f"ls -la '{latest_job_dir}'"
            )
            job_contents = stdout.read().decode().strip()
            print(f"Job directory contents:\n{job_contents}")

            # Check for result files
            stdin, stdout, stderr = ssh_client.exec_command(
                f"ls -la '{latest_job_dir}'/result.pkl '{latest_job_dir}'/error.pkl 2>/dev/null || echo 'No result files'"
            )
            result_files = stdout.read().decode().strip()
            print(f"Result files:\n{result_files}")

            # If result.pkl exists, read it
            if "result.pkl" in result_files and "No result files" not in result_files:
                print("Reading result.pkl...")
                read_result_cmd = f"""
cd '{latest_job_dir}'
python3 -c "
import pickle
try:
    with open('result.pkl', 'rb') as f:
        result = pickle.load(f)
    print('SUCCESS: Result loaded')
    print('Result:', result)
    print('Result type:', type(result))
    if isinstance(result, dict):
        print('Result keys:', list(result.keys()))
except Exception as e:
    print('Error reading result:', str(e))
"
"""
                stdin, stdout, stderr = ssh_client.exec_command(read_result_cmd)
                result_output = stdout.read().decode().strip()
                result_error = stderr.read().decode().strip()
                print(f"Result reading output:\n{result_output}")
                if result_error:
                    print(f"Result reading error:\n{result_error}")

            # Check SLURM files
            stdin, stdout, stderr = ssh_client.exec_command(
                f"ls -la '{latest_job_dir}'/slurm-* 2>/dev/null || echo 'No SLURM files'"
            )
            slurm_files = stdout.read().decode().strip()
            print(f"SLURM files:\n{slurm_files}")

            # If there are SLURM files, read them
            if "slurm-" in slurm_files and "No SLURM files" not in slurm_files:
                print("Reading SLURM output...")
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cat '{latest_job_dir}'/slurm-*.out 2>/dev/null | head -20"
                )
                slurm_stdout = stdout.read().decode().strip()
                if slurm_stdout:
                    print(f"SLURM stdout:\n{slurm_stdout}")

                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cat '{latest_job_dir}'/slurm-*.err 2>/dev/null | head -20"
                )
                slurm_stderr = stdout.read().decode().strip()
                if slurm_stderr:
                    print(f"SLURM stderr:\n{slurm_stderr}")
        else:
            print("No job directories found in this location")

    # Also check current running SLURM jobs
    print("\n=== Checking current SLURM jobs ===")
    stdin, stdout, stderr = ssh_client.exec_command(
        "squeue -u $USER 2>/dev/null || echo 'No squeue access or no jobs'"
    )
    current_jobs = stdout.read().decode().strip()
    print(f"Current SLURM jobs:\n{current_jobs}")

    # Check recent completed jobs
    print("\n=== Checking recent completed jobs ===")
    stdin, stdout, stderr = ssh_client.exec_command(
        "sacct -u $USER --format=JobID,JobName,State,ExitCode,Start,End --starttime=today 2>/dev/null || echo 'No sacct access'"
    )
    recent_jobs = stdout.read().decode().strip()
    print(f"Recent completed jobs:\n{recent_jobs}")

    ssh_client.close()
    print("\n=== Find Actual SLURM Jobs Complete ===")
    return True
