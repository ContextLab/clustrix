"""
Debug the SLURM script syntax issues.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_debug_slurm_script():
    """Debug the SLURM script syntax issues."""
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

    print("Debugging SLURM script syntax...")

    # Get the most recent job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -1td /tmp/clustrix_slurm_working/job_* 2>/dev/null | head -1"
    )
    latest_job_dir = stdout.read().decode().strip()

    if latest_job_dir:
        print(f"Debugging job directory: {latest_job_dir}")

        # Read the complete job script
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cat '{latest_job_dir}/job.sh'"
        )
        full_script = stdout.read().decode()
        print("=== FULL JOB SCRIPT ===")
        print(full_script)
        print("=== END SCRIPT ===")

        # Try to run the script manually step by step
        print("\nTesting script execution step by step...")

        # Step 1: Test the basic setup
        step1_script = f"""
cd '{latest_job_dir}'
echo "Current directory: $(pwd)"
echo "Files in directory:"
ls -la
echo "Virtual environments:"
ls -la venv*
"""

        stdin, stdout, stderr = ssh_client.exec_command(step1_script)
        step1_output = stdout.read().decode().strip()
        step1_error = stderr.read().decode().strip()
        print(f"Step 1 - Basic setup:\nOUTPUT:\n{step1_output}\nERROR:\n{step1_error}")

        # Step 2: Test VENV1 activation
        step2_script = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
echo "VENV1 activated"
python --version
which python
"""

        stdin, stdout, stderr = ssh_client.exec_command(step2_script)
        step2_output = stdout.read().decode().strip()
        step2_error = stderr.read().decode().strip()
        print(
            f"Step 2 - VENV1 activation:\nOUTPUT:\n{step2_output}\nERROR:\n{step2_error}"
        )

        # Step 3: Test simple Python execution in VENV1
        step3_script = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
python -c "import sys; print('Python version:', sys.version); print('Working directory:', import os; os.getcwd() if 'os' in globals() else 'os not imported')"
"""

        stdin, stdout, stderr = ssh_client.exec_command(step3_script)
        step3_output = stdout.read().decode().strip()
        step3_error = stderr.read().decode().strip()
        print(
            f"Step 3 - Simple Python in VENV1:\nOUTPUT:\n{step3_output}\nERROR:\n{step3_error}"
        )

        # Step 4: Test pickle loading
        step4_script = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
python -c "
import pickle
import sys
print('Testing pickle loading...')
try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    print('Successfully loaded function_data.pkl')
    print('Data type:', type(data))
    print('Data keys:', list(data.keys()) if isinstance(data, dict) else 'Not a dict')
except Exception as e:
    print('Error:', str(e))
    import traceback
    traceback.print_exc()
"
"""

        stdin, stdout, stderr = ssh_client.exec_command(step4_script)
        step4_output = stdout.read().decode().strip()
        step4_error = stderr.read().decode().strip()
        print(
            f"Step 4 - Pickle loading test:\nOUTPUT:\n{step4_output}\nERROR:\n{step4_error}"
        )

        # Step 5: Test the exact script syntax that's failing
        print("\nTesting the failing script syntax...")

        # Extract just the Python code part from the full script
        python_code_start = full_script.find(
            '/tmp/clustrix_slurm_working/job_1752261696/venv1_serialization/bin/python -c "'
        )
        if python_code_start != -1:
            python_code_start = full_script.find('-c "', python_code_start) + 4
            python_code_end = full_script.find('"\n\n# Step 2:', python_code_start)
            if python_code_end == -1:
                python_code_end = full_script.find(
                    '"', python_code_start + 100
                )  # Find next quote

            python_code = full_script[python_code_start:python_code_end]
            print(f"Extracted Python code (first 500 chars):\n{python_code[:500]}")

            # Save this to a file and test it
            test_script_content = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
cat > test_python_script.py << 'PYTHON_EOF'
{python_code}
PYTHON_EOF

echo "Testing extracted Python script..."
python test_python_script.py
"""

            stdin, stdout, stderr = ssh_client.exec_command(test_script_content)
            extracted_output = stdout.read().decode().strip()
            extracted_error = stderr.read().decode().strip()
            print(
                f"Extracted script test:\nOUTPUT:\n{extracted_output}\nERROR:\n{extracted_error}"
            )

    ssh_client.close()

    print("\n=== SLURM Script Debug Complete ===")
    return True
