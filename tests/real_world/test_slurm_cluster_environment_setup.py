#!/usr/bin/env python3
"""
Test environment setup on ndoli cluster with cluster-specific configuration
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
from clustrix.config import ClusterConfig
from clustrix.executor import ClusterExecutor


def simple_test():
    """Simple test function."""
    import os
    import sys

    return {
        "hostname": os.uname().nodename,
        "python_version": sys.version,
        "working_dir": os.getcwd(),
        "python_executable": sys.executable,
    }


def test_ndoli_environment_setup():
    """Test environment setup with ndoli-specific configuration."""
    print("🧪 Testing Ndoli Environment Setup")
    print("=" * 50)

    # Load ndoli-specific configuration
    config_path = Path(__file__).parent.parent / "ndoli_config.yml"
    config = ClusterConfig.load_from_file(str(config_path))

    # Get credentials for password
    creds = ValidationCredentials()
    slurm_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")

    if not slurm_creds:
        print("❌ No credentials found")
        return False

    # Update config with password
    config.password = slurm_creds.get("password")

    print(f"🔗 Target: {config.username}@{config.cluster_host}")
    print(f"📁 Work dir: {config.remote_work_dir}")
    print(f"🐍 Python: {config.python_executable}")
    print(f"📦 Modules: {config.module_loads}")
    print(f"🌍 Env vars: {config.environment_variables}")

    # Create executor
    executor = ClusterExecutor(config)

    # Use the globally defined test function

    try:
        # Prepare function data
        func_data = {
            "function": simple_test,
            "args": (),
            "kwargs": {},
            "env": {"CLUSTRIX_ENV_TEST": "true"},
            "requirements": {},  # No extra requirements to focus on env setup
        }

        # Submit job
        print("\n🚀 Submitting environment test job...")
        job_config = {
            "job_name": "clustrix_env_test",
            "cores": 1,
            "memory": "1GB",
            "time": "00:05:00",
        }
        job_id = executor.submit_job(func_data, job_config)

        print(f"✅ Job submitted: {job_id}")

        # Wait for completion
        print("⏳ Waiting for job completion...")
        start_time = time.time()
        timeout = 300  # 5 minutes

        while time.time() - start_time < timeout:
            status = executor.get_job_status(job_id)
            print(f"📊 Status: {status}", end="\r")

            if status == "completed":
                print("\n✅ Job completed successfully")

                # Get result
                try:
                    result = executor.get_job_result(job_id)
                    print("📦 Environment information:")
                    print(f"   Hostname: {result['hostname']}")
                    print(f"   Python version: {result['python_version']}")
                    print(f"   Working directory: {result['working_dir']}")
                    print(f"   Python executable: {result['python_executable']}")
                    return True
                except Exception as e:
                    print(f"❌ Failed to retrieve result: {e}")
                    return False

            elif status == "failed":
                print("\n❌ Job failed")
                return False

            time.sleep(5)

        print("\n⏱️ Job timed out")
        return False

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_ndoli_environment_setup()
    if success:
        print("\n🎉 Environment setup test PASSED!")
        print("The ndoli-specific configuration resolved the environment issue.")
    else:
        print("\n❌ Environment setup test FAILED!")
        print("Need to investigate further or adjust configuration.")
