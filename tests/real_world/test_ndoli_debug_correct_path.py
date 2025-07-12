"""
Debug SLURM job execution using the correct remote_work_dir from config.
"""

import pytest
import paramiko
import uuid
from clustrix import configure
from clustrix.config import ClusterConfig
from tests.real_world import credentials


@pytest.mark.real_world
def test_debug_slurm_with_correct_config():
    """Debug SLURM job execution using the actual configuration paths."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Create the same configuration as used in the SLURM tests
    remote_work_dir = f"/tmp/clustrix_ndoli_slurm_{uuid.uuid4().hex[:8]}"

    # Configure clustrix for SLURM-based execution on ndoli
    configure(
        cluster_type="slurm",
        cluster_host=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        key_file=ndoli_creds.get("private_key_path"),
        remote_work_dir=remote_work_dir,
        python_executable="python3",
        cleanup_on_success=False,
        job_poll_interval=10,
    )

    # Get the current configuration
    config = ClusterConfig()
    print(f"Using remote_work_dir: {config.remote_work_dir}")

    # Connect via SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh_client.connect(
        hostname=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        port=22,
    )

    print(f"Debugging SLURM job in the correct directory: {config.remote_work_dir}")

    # Check if the remote work directory exists
    stdin, stdout, stderr = ssh_client.exec_command(
        f"ls -la '{config.remote_work_dir}' 2>/dev/null || echo 'Directory does not exist'"
    )
    dir_check = stdout.read().decode().strip()
    print(f"Remote work directory check:\n{dir_check}")

    # Look for job directories in the correct location
    stdin, stdout, stderr = ssh_client.exec_command(
        f"ls -1td '{config.remote_work_dir}'/job_* 2>/dev/null | head -5"
    )
    job_dirs = stdout.read().decode().strip()
    print(f"Job directories found:\n{job_dirs}")

    if job_dirs and "job_" in job_dirs:
        latest_job_dir = job_dirs.split("\n")[0]
        print(f"Latest job directory: {latest_job_dir}")

        # Check contents of the latest job directory
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -la '{latest_job_dir}'")
        job_contents = stdout.read().decode().strip()
        print(f"Job directory contents:\n{job_contents}")

        # Check for SLURM output files
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}'/slurm-* 2>/dev/null || echo 'No SLURM files'"
        )
        slurm_files = stdout.read().decode().strip()
        print(f"SLURM files:\n{slurm_files}")

        # Check for result files
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}'/result.pkl '{latest_job_dir}'/error.pkl 2>/dev/null || echo 'No result files'"
        )
        result_files = stdout.read().decode().strip()
        print(f"Result files:\n{result_files}")

        # If result.pkl exists, read it
        if "result.pkl" in result_files:
            print("Reading result.pkl...")
            read_result_cmd = f"""
cd '{latest_job_dir}'
python3 -c "
import pickle
try:
    with open('result.pkl', 'rb') as f:
        result = pickle.load(f)
    print('Result loaded successfully')
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

        # Check the job script
        stdin, stdout, stderr = ssh_client.exec_command(
            f"head -50 '{latest_job_dir}/job.sh' 2>/dev/null || echo 'No job script'"
        )
        job_script = stdout.read().decode().strip()
        print(f"Job script (first 50 lines):\n{job_script}")

        # Try to manually run the job script
        print("\nTrying to run the job script manually...")
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd '{latest_job_dir}' && chmod +x job.sh && timeout 60 bash job.sh"
        )
        manual_output = stdout.read().decode().strip()
        manual_error = stderr.read().decode().strip()
        exit_status = stdout.channel.recv_exit_status()
        print(f"Manual execution exit status: {exit_status}")
        print(f"Manual execution output:\n{manual_output}")
        if manual_error:
            print(f"Manual execution error:\n{manual_error}")

    else:
        print("No job directories found in the correct location")

        # Check if there are any other clustrix directories
        stdin, stdout, stderr = ssh_client.exec_command(
            "find /tmp -name 'clustrix*' -type d 2>/dev/null | head -10"
        )
        other_dirs = stdout.read().decode().strip()
        print(f"Other clustrix directories found:\n{other_dirs}")

    ssh_client.close()
    print("\n=== Debug with Correct Config Complete ===")
    return True
