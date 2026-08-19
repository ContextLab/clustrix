#!/usr/bin/env python3
"""
Debug environment setup on ndoli cluster step by step
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
import paramiko


def debug_ndoli_environment():
    """Debug environment setup step by step."""
    print("🔍 Debugging Ndoli Environment Setup")
    print("=" * 50)

    # Get credentials
    creds = ValidationCredentials()
    slurm_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")

    if not slurm_creds:
        print("❌ No credentials found")
        return False

    hostname = slurm_creds.get("hostname")
    username = slurm_creds.get("username")
    password = slurm_creds.get("password")

    # Connect via SSH
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(hostname, username=username, password=password)
        print(f"✅ Connected to {hostname}")

        work_dir = f"/dartfs-hpc/rc/home/b/{username}/clustrix_debug_{int(time.time())}"

        # Test each step individually
        steps = [
            {
                "name": "Create work directory",
                "command": f"mkdir -p {work_dir} && cd {work_dir} && pwd",
            },
            {
                "name": "Check available Python versions",
                "command": "which python; which python3; python --version 2>&1 || echo 'python not found'; python3 --version 2>&1 || echo 'python3 not found'",
            },
            {
                "name": "Check available modules",
                "command": "module avail python 2>&1 | head -10",
            },
            {
                "name": "Load python module",
                "command": "module load python && module list 2>&1",
            },
            {
                "name": "Check Python after module load",
                "command": "module load python && which python && which python3 && python --version && python3 --version",
            },
            {
                "name": "Set OMP_NUM_THREADS and check environment",
                "command": "module load python && export OMP_NUM_THREADS=1 && echo $OMP_NUM_THREADS && env | grep OMP",
            },
            {
                "name": "Try creating virtual environment with python",
                "command": f"cd {work_dir} && module load python && export OMP_NUM_THREADS=1 && python -m venv test_venv",
            },
            {
                "name": "Try creating virtual environment with python3",
                "command": f"cd {work_dir} && module load python && export OMP_NUM_THREADS=1 && python3 -m venv test_venv3",
            },
            {
                "name": "Test virtual environment activation",
                "command": f"cd {work_dir} && module load python && source test_venv3/bin/activate && which python && python --version",
            },
        ]

        results = {}

        for step in steps:
            print(f"\n🔍 {step['name']}")
            print(f"   Command: {step['command']}")

            try:
                stdin, stdout, stderr = ssh.exec_command(step["command"])
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                exit_code = stdout.channel.recv_exit_status()

                results[step["name"]] = {
                    "output": output,
                    "error": error,
                    "exit_code": exit_code,
                }

                if exit_code == 0:
                    print(f"   ✅ Success")
                    if output:
                        print(f"   Output: {output}")
                else:
                    print(f"   ❌ Failed (exit code: {exit_code})")
                    if error:
                        print(f"   Error: {error}")
                    if output:
                        print(f"   Output: {output}")

            except Exception as e:
                print(f"   ❌ Exception: {e}")
                results[step["name"]] = {"exception": str(e)}

        # Cleanup
        print(f"\n🧹 Cleaning up test directory...")
        stdin, stdout, stderr = ssh.exec_command(f"rm -rf {work_dir}")

        # Summary
        print("\n📊 Debug Summary")
        print("=" * 50)

        success_count = 0
        for step_name, result in results.items():
            if result.get("exit_code") == 0:
                print(f"✅ {step_name}")
                success_count += 1
            else:
                print(f"❌ {step_name}")
                if "error" in result and result["error"]:
                    print(f"   Error: {result['error']}")

        print(f"\n🎯 {success_count}/{len(steps)} steps successful")

        # Recommendations
        print("\n💡 Recommendations:")
        if results.get("Load python module", {}).get("exit_code") == 0:
            print("   - Module loading works")
        else:
            print("   - Module loading failed - check available modules")

        if (
            results.get("Try creating virtual environment with python3", {}).get(
                "exit_code"
            )
            == 0
        ):
            print("   - python3 venv creation works")
        else:
            print("   - python3 venv creation failed - check python3 availability")

        return success_count >= len(steps) // 2  # At least half should work

    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False
    finally:
        ssh.close()


if __name__ == "__main__":
    success = debug_ndoli_environment()
    if success:
        print(
            "\n🎉 Debug completed - environment setup should work with proper configuration"
        )
    else:
        print("\n❌ Debug revealed significant issues - need manual intervention")
