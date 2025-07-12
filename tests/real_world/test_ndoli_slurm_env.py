"""
Test environment setup for SLURM jobs on ndoli.
"""

import pytest
import paramiko
import tempfile
import pickle
import os
from tests.real_world import credentials
from clustrix.utils import setup_remote_environment
from clustrix.config import ClusterConfig


@pytest.mark.real_world
def test_ndoli_slurm_environment_setup():
    """Test environment setup for SLURM jobs on ndoli."""
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

    # Create test directory
    test_dir = "/tmp/clustrix_env_test"
    stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {test_dir}")
    stdout.channel.recv_exit_status()

    # Create config for environment setup
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        remote_work_dir=test_dir,
        python_executable="python3",
        cleanup_on_success=True,
        job_poll_interval=5,
    )

    # Test requirements (simple ones)
    requirements = {
        "pickle": "1.0",  # Built-in
        "os": "1.0",  # Built-in
        "sys": "1.0",  # Built-in
    }

    print("Testing environment setup...")

    try:
        # Test environment setup
        result = setup_remote_environment(ssh_client, test_dir, requirements, config)
        print(f"Environment setup result: {result}")

        # Check if virtual environment was created
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -la {test_dir}/")
        dir_contents = stdout.read().decode().strip()
        print(f"Directory contents: {dir_contents}")

        # Check if venv directory exists
        stdin, stdout, stderr = ssh_client.exec_command(f"ls -la {test_dir}/venv/")
        venv_contents = stdout.read().decode().strip()
        print(f"Venv contents: {venv_contents}")

        # Test if we can activate the environment
        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && source venv/bin/activate && python3 --version"
        )
        python_version = stdout.read().decode().strip()
        print(f"Python version in venv: {python_version}")

        # Test if we can run a simple Python script
        simple_script = """
import sys
import os
print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")
print(f"Working directory: {os.getcwd()}")
print(f"PATH: {os.environ.get('PATH', 'not set')[:100]}...")
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && source venv/bin/activate && python3 -c '{simple_script}'"
        )
        script_output = stdout.read().decode().strip()
        script_error = stderr.read().decode().strip()

        print(f"Script output: {script_output}")
        print(f"Script error: {script_error}")

        # Now test the actual job script approach
        print("\nTesting job script approach...")

        # Create a simple function data
        func_data = {
            "function": b"dummy_function_data",
            "args": pickle.dumps((), protocol=4),
            "kwargs": pickle.dumps({}, protocol=4),
            "requirements": requirements,
        }

        # Upload function data
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            pickle.dump(func_data, f, protocol=4)
            local_pickle_path = f.name

        # Upload to remote
        sftp = ssh_client.open_sftp()
        sftp.put(local_pickle_path, f"{test_dir}/function_data.pkl")
        sftp.close()

        os.unlink(local_pickle_path)

        # Test the job script Python execution
        job_script_python = """
import pickle
import sys
import traceback
import os

print("Starting job script test...")
print(f"Working directory: {os.getcwd()}")
print(f"Python executable: {sys.executable}")

try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    print("Successfully loaded function data")
    print(f"Function data keys: {list(data.keys())}")
    
    # Try to load args and kwargs
    args = pickle.loads(data['args'])
    kwargs = pickle.loads(data['kwargs'])
    print(f"Args: {args}")
    print(f"Kwargs: {kwargs}")
    
    # Test result writing
    result = {"test": "success", "message": "Job script test completed"}
    with open('result.pkl', 'wb') as f:
        pickle.dump(result, f, protocol=4)
    
    print("Successfully wrote result")
    
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
    
    # Write error
    with open('error.pkl', 'wb') as f:
        pickle.dump({'error': str(e), 'traceback': traceback.format_exc()}, f, protocol=4)
"""

        stdin, stdout, stderr = ssh_client.exec_command(
            f"cd {test_dir} && source venv/bin/activate && python3 -c '{job_script_python}'"
        )
        job_output = stdout.read().decode().strip()
        job_error = stderr.read().decode().strip()

        print(f"Job script output: {job_output}")
        print(f"Job script error: {job_error}")

        # Check if result file was created
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la {test_dir}/result.pkl {test_dir}/error.pkl 2>/dev/null || echo 'No result files'"
        )
        result_files = stdout.read().decode().strip()
        print(f"Result files: {result_files}")

        # Try to read result
        stdin, stdout, stderr = ssh_client.exec_command(
            f'cd {test_dir} && source venv/bin/activate && python3 -c \'import pickle; print(pickle.load(open("result.pkl", "rb")))\''
        )
        result_content = stdout.read().decode().strip()
        print(f"Result content: {result_content}")

    except Exception as e:
        print(f"Environment setup failed: {e}")
        import traceback

        traceback.print_exc()

    finally:
        # Cleanup
        stdin, stdout, stderr = ssh_client.exec_command(f"rm -rf {test_dir}")
        stdout.channel.recv_exit_status()

    ssh_client.close()

    print("\n=== Environment Setup Test Complete ===")
    return True
