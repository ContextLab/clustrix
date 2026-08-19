"""
Test the SLURM workflow by running the actual job script.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_run_actual_job_script():
    """Test running the actual job script that was generated."""
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

    print("Testing by running the actual job script...")

    # Get the most recent job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -1td /tmp/clustrix_slurm_working/job_* 2>/dev/null | head -1"
    )
    latest_job_dir = stdout.read().decode().strip()

    if latest_job_dir:
        print(f"Running job script in: {latest_job_dir}")

        # Make the job script executable and run it
        make_executable_cmd = f"chmod +x '{latest_job_dir}/job.sh'"
        stdin, stdout, stderr = ssh_client.exec_command(make_executable_cmd)
        stdout.channel.recv_exit_status()

        # Run the job script directly
        print("Running the job script...")
        run_script_cmd = f"cd '{latest_job_dir}' && bash job.sh"
        stdin, stdout, stderr = ssh_client.exec_command(run_script_cmd)

        # Read output with timeout
        script_output = stdout.read().decode()
        script_error = stderr.read().decode()
        exit_status = stdout.channel.recv_exit_status()

        print(f"Script exit status: {exit_status}")
        print(f"Script output:\n{script_output}")
        if script_error:
            print(f"Script error:\n{script_error}")

        # Check what files were created
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -la '{latest_job_dir}/'")
        files_after = stdout.read().decode().strip()
        print(f"Files after script execution:\n{files_after}")

        # Check for result file
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/result.pkl' 2>/dev/null || echo 'No result.pkl found'"
        )
        result_file_check = stdout.read().decode().strip()
        print(f"Result file check: {result_file_check}")

        # If result.pkl exists, read it
        if "No result.pkl found" not in result_file_check:
            print("Reading result.pkl...")
            read_result_cmd = f"""
cd '{latest_job_dir}'
python3 -c "
import pickle
try:
    with open('result.pkl', 'rb') as f:
        result = pickle.load(f)
    print('SUCCESS: Result loaded successfully')
    print('Result:', result)
    print('Result type:', type(result))
except Exception as e:
    print('Error reading result:', str(e))
    import traceback
    traceback.print_exc()
"
"""
            stdin, stdout, stderr = ssh_client.exec_command(read_result_cmd)
            result_output = stdout.read().decode().strip()
            result_error = stderr.read().decode().strip()
            print(f"Result reading output:\n{result_output}")
            if result_error:
                print(f"Result reading error:\n{result_error}")

        # Check for error file
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/error.pkl' 2>/dev/null || echo 'No error.pkl found'"
        )
        error_file_check = stdout.read().decode().strip()
        print(f"Error file check: {error_file_check}")

        # If error.pkl exists, read it
        if "No error.pkl found" not in error_file_check:
            print("Reading error.pkl...")
            read_error_cmd = f"""
cd '{latest_job_dir}'
python3 -c "
import pickle
try:
    with open('error.pkl', 'rb') as f:
        error = pickle.load(f)
    print('Error details:', error)
except Exception as e:
    print('Error reading error.pkl:', str(e))
"
"""
            stdin, stdout, stderr = ssh_client.exec_command(read_error_cmd)
            error_output = stdout.read().decode().strip()
            error_error = stderr.read().decode().strip()
            print(f"Error reading output:\n{error_output}")
            if error_error:
                print(f"Error reading error:\n{error_error}")

    ssh_client.close()

    print("\n=== Job Script Execution Test Complete ===")
    return True
