"""
Check virtual environment Python versions on ndoli.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_check_venv_versions():
    """Check Python versions in virtual environments."""
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

    print("Checking virtual environment Python versions...")

    # Get the most recent job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -1td /tmp/clustrix_slurm_working/job_* 2>/dev/null | head -1"
    )
    latest_job_dir = stdout.read().decode().strip()

    if latest_job_dir:
        print(f"Checking virtual environments in: {latest_job_dir}")

        # Check VENV1 Python version
        stdin, stdout, stderr = ssh_client.exec_command(
            f"'{latest_job_dir}/venv1_serialization/bin/python' --version 2>&1"
        )
        venv1_version = stdout.read().decode().strip()
        print(f"VENV1 Python version: {venv1_version}")

        # Check VENV2 Python version
        stdin, stdout, stderr = ssh_client.exec_command(
            f"'{latest_job_dir}/venv2_execution/bin/python' --version 2>&1"
        )
        venv2_version = stdout.read().decode().strip()
        print(f"VENV2 Python version: {venv2_version}")

        # Check system Python version
        stdin, stdout, stderr = ssh_client.exec_command("python3 --version 2>&1")
        system_version = stdout.read().decode().strip()
        print(f"System Python version: {system_version}")

        # Test if we can run the VENV1 python manually
        test_script = """
import sys
print("Python executable:", sys.executable)
print("Python version:", sys.version)
print("Can import pickle:", end=" ")
try:
    import pickle
    print("YES")
except:
    print("NO")
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd '{latest_job_dir}' && source venv1_serialization/bin/activate && python -c '{test_script}' 2>&1"
        )
        venv1_test = stdout.read().decode().strip()
        print(f"VENV1 test output:\n{venv1_test}")

        # Test running the actual first part of the job script manually
        print("\nTesting VENV1 script execution manually...")
        venv1_script = """
import pickle
import sys
print("VENV1 - Starting function deserialization")
print("Python version:", sys.version)
try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    print("Successfully loaded function_data.pkl")
    print("Data keys:", list(data.keys()))
    
    # Check if we have func_info with source
    func_info = data.get('func_info', {})
    if func_info.get('source'):
        print("Function source code is available")
        print("Function name:", func_info.get('name', 'unknown'))
    else:
        print("No function source code available")
    
except Exception as e:
    print("Error loading function_data.pkl:", str(e))
    import traceback
    traceback.print_exc()
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd '{latest_job_dir}' && source venv1_serialization/bin/activate && python -c '{venv1_script}' 2>&1"
        )
        manual_test = stdout.read().decode().strip()
        print(f"Manual VENV1 execution:\n{manual_test}")

        # Check if there are any actual SLURM output files anywhere
        stdin, stdout, stderr = ssh_client.exec_command(
            f"find '{latest_job_dir}' -name 'slurm-*' 2>/dev/null"
        )
        slurm_files = stdout.read().decode().strip()
        print(f"SLURM files in job directory: {slurm_files}")

        # Also check the parent directory
        parent_dir = "/tmp/clustrix_slurm_working"
        stdin, stdout, stderr = ssh_client.exec_command(
            f"find '{parent_dir}' -name 'slurm-*' 2>/dev/null"
        )
        parent_slurm_files = stdout.read().decode().strip()
        print(f"SLURM files in parent directory: {parent_slurm_files}")

    ssh_client.close()

    print("\n=== Virtual Environment Check Complete ===")
    return True
