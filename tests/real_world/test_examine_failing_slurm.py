"""
Examine the failing SLURM job script and try to understand why it's failing.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_examine_failing_slurm_job():
    """Examine the failing SLURM job to understand the issue."""
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

    print("Examining failing SLURM job...")

    # Use the latest job directory we found
    latest_job_dir = "/tmp/clustrix_slurm_working/job_1752262089"

    print(f"Examining job directory: {latest_job_dir}")

    # Read the complete job script
    stdin, stdout, stderr = ssh_client.exec_command(f"cat '{latest_job_dir}/job.sh'")
    job_script = stdout.read().decode()
    print("=== COMPLETE JOB SCRIPT ===")
    print(job_script)
    print("=== END JOB SCRIPT ===")

    # Check if the virtual environments exist and are set up correctly
    print("\n=== Checking Virtual Environments ===")

    # Check VENV1
    stdin, stdout, stderr = ssh_client.exec_command(
        f"ls -la '{latest_job_dir}/venv1_serialization/bin/' | head -10"
    )
    venv1_contents = stdout.read().decode().strip()
    print(f"VENV1 bin contents:\n{venv1_contents}")

    # Check VENV2
    stdin, stdout, stderr = ssh_client.exec_command(
        f"ls -la '{latest_job_dir}/venv2_execution/bin/' | head -10"
    )
    venv2_contents = stdout.read().decode().strip()
    print(f"VENV2 bin contents:\n{venv2_contents}")

    # Test if we can activate VENV1
    print("\n=== Testing VENV1 Activation ===")
    stdin, stdout, stderr = ssh_client.exec_command(
        f"cd '{latest_job_dir}' && source venv1_serialization/bin/activate && echo 'VENV1 activated' && python --version"
    )
    venv1_test = stdout.read().decode().strip()
    venv1_error = stderr.read().decode().strip()
    print(f"VENV1 activation test:\nOUTPUT: {venv1_test}\nERROR: {venv1_error}")

    # Test if we can activate VENV2
    print("\n=== Testing VENV2 Activation ===")
    stdin, stdout, stderr = ssh_client.exec_command(
        f"cd '{latest_job_dir}' && source venv2_execution/bin/activate && echo 'VENV2 activated' && python --version"
    )
    venv2_test = stdout.read().decode().strip()
    venv2_error = stderr.read().decode().strip()
    print(f"VENV2 activation test:\nOUTPUT: {venv2_test}\nERROR: {venv2_error}")

    # Try to run the first few lines of the job script manually to see where it fails
    print("\n=== Testing Job Script Components ===")

    # Test basic directory navigation
    stdin, stdout, stderr = ssh_client.exec_command(
        f"cd '{latest_job_dir}' && pwd && ls -la"
    )
    basic_test = stdout.read().decode().strip()
    print(f"Basic directory test:\n{basic_test}")

    # Test the first Python command from the script
    print("\n=== Testing First Python Command ===")

    # Extract the first python command from the script
    if "venv1_serialization/bin/python -c" in job_script:
        # Find the python command
        start_idx = job_script.find('venv1_serialization/bin/python -c "')
        if start_idx != -1:
            # Find the end of the command
            start_quote = start_idx + len('venv1_serialization/bin/python -c "')
            # Look for the matching quote, accounting for escaped quotes
            quote_count = 0
            end_idx = start_quote
            while end_idx < len(job_script):
                if job_script[end_idx] == '"' and (
                    end_idx == start_quote or job_script[end_idx - 1] != "\\"
                ):
                    break
                end_idx += 1

            if end_idx < len(job_script):
                python_code = job_script[start_quote:end_idx]
                print(f"First Python command (first 500 chars):\n{python_code[:500]}")

                # Try to run just the python executable
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cd '{latest_job_dir}' && ./venv1_serialization/bin/python --version"
                )
                python_test = stdout.read().decode().strip()
                python_error = stderr.read().decode().strip()
                print(
                    f"Python executable test:\nOUTPUT: {python_test}\nERROR: {python_error}"
                )

                # Try to run a simple Python command
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cd '{latest_job_dir}' && ./venv1_serialization/bin/python -c 'print(\"Hello from VENV1\")'"
                )
                simple_test = stdout.read().decode().strip()
                simple_error = stderr.read().decode().strip()
                print(
                    f"Simple Python test:\nOUTPUT: {simple_test}\nERROR: {simple_error}"
                )

    # Check if there are any SLURM log files elsewhere
    print("\n=== Checking for SLURM Log Files ===")
    stdin, stdout, stderr = ssh_client.exec_command(
        "find /tmp -name 'slurm-5349528.*' 2>/dev/null"
    )
    slurm_logs = stdout.read().decode().strip()
    print(f"SLURM log files for job 5349528: {slurm_logs}")

    if slurm_logs:
        for log_file in slurm_logs.split("\n"):
            if log_file.strip():
                print(f"\n--- Content of {log_file} ---")
                stdin, stdout, stderr = ssh_client.exec_command(f"cat '{log_file}'")
                log_content = stdout.read().decode().strip()
                print(log_content)

    # Check the user's home directory for SLURM files
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -la ~/slurm-5349528.* 2>/dev/null || echo 'No SLURM files in home'"
    )
    home_slurm = stdout.read().decode().strip()
    print(f"SLURM files in home directory: {home_slurm}")

    ssh_client.close()
    print("\n=== Examine Failing SLURM Job Complete ===")
    return True
