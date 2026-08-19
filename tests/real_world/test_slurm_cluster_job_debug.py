"""
Debug SLURM availability on ndoli.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_ndoli_slurm_availability():
    """Test if SLURM is available on ndoli."""
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

    # Check if SLURM commands are available
    commands_to_test = [
        "which sbatch",
        "which squeue",
        "which sinfo",
        "which sacct",
        "sbatch --version",
        "sinfo",
        "squeue -u $(whoami)",
    ]

    results = {}

    for cmd in commands_to_test:
        try:
            stdin, stdout, stderr = ssh_client.exec_command(cmd)
            stdout_data = stdout.read().decode().strip()
            stderr_data = stderr.read().decode().strip()
            exit_code = stdout.channel.recv_exit_status()

            results[cmd] = {
                "exit_code": exit_code,
                "stdout": stdout_data,
                "stderr": stderr_data,
                "success": exit_code == 0,
            }

        except Exception as e:
            results[cmd] = {"error": str(e), "success": False}

    ssh_client.close()

    # Print results
    print("SLURM availability check results:")
    for cmd, result in results.items():
        print(f"\n{cmd}:")
        if result["success"]:
            print(f"  SUCCESS: {result['stdout']}")
        else:
            print(
                f"  FAILED: {result.get('stderr', result.get('error', 'Unknown error'))}"
            )

    # Check if basic SLURM commands are available
    basic_commands = ["which sbatch", "which squeue"]
    slurm_available = all(results[cmd]["success"] for cmd in basic_commands)

    print(f"\nSLURM availability: {'YES' if slurm_available else 'NO'}")

    return results
