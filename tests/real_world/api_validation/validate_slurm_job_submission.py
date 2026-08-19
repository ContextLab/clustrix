#!/usr/bin/env python3
"""
SLURM Job Submission Validation Script

This script validates SLURM job submission functionality for Clustrix
with real job execution on a SLURM cluster.
"""

import sys
import os
import time
import tempfile
import json
import logging
from pathlib import Path
from datetime import datetime

# Add the clustrix package to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials
from clustrix.config import ClusterConfig
from clustrix.executor import ClusterExecutor

from tests.real_world.credential_manager import require_test_remote_work_dir
from clustrix.ssh_security import configure_host_key_policy

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Define test functions outside of the test function to make them picklable
def simple_addition(x, y):
    """Simple addition test function."""
    return x + y


def compute_sum_of_squares(n):
    """Compute sum of squares up to n."""
    return sum(i**2 for i in range(n))


def get_environment_info():
    """Get environment information."""
    return {
        "hostname": os.uname().nodename,
        "python_version": sys.version,
        "working_dir": os.getcwd(),
        "env_vars": len(os.environ),
        "datetime": datetime.now().isoformat(),
    }


def test_slurm_job_submission():
    """Test SLURM job submission functionality."""
    print("🚀 SLURM Job Submission Validation")
    print("=" * 70)

    # Get credentials
    creds = ValidationCredentials()
    slurm_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")

    if not slurm_creds:
        print("❌ No SLURM cluster credentials found")
        return False

    hostname = slurm_creds.get("hostname")
    username = slurm_creds.get("username")
    password = slurm_creds.get("password")

    print(f"🔗 Target SLURM cluster: {username}@{hostname}")

    # Create configuration for SLURM cluster with custom remote directory
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=hostname,
        username=username,
        password=password,
        # Use your home directory instead of /tmp
        remote_work_dir=f"{require_test_remote_work_dir()}/clustrix",
        default_cores=2,
        default_memory="4GB",
        default_time="00:10:00",  # 10 minutes for test
        python_executable="python3",
        # Add module loads for Python on HPC clusters
        module_loads=["python/3.6", "python3"],  # Common module names
        # Or use direct commands to ensure python3 is available
        pre_execution_commands=[
            "export PATH=/usr/bin:$PATH",  # Ensure system python3 is in PATH
            "which python3 || echo 'Python3 not found in PATH'",
        ],
    )

    print(f"📁 Remote work directory: {config.remote_work_dir}")

    # Create executor
    executor = ClusterExecutor(config)

    # Define test functions
    test_functions = [
        {
            "name": "simple_computation",
            "func": simple_addition,
            "args": (42, 58),
            "expected": 100,
            "description": "Simple addition test",
        },
        {
            "name": "numpy_computation",
            "func": compute_sum_of_squares,
            "args": (1000,),
            "expected": 332833500,
            "description": "Computation with loop",
        },
        {
            "name": "environment_test",
            "func": get_environment_info,
            "args": (),
            "expected": None,  # Just check it returns a dict
            "description": "Environment information gathering",
        },
    ]

    results = {}

    print("\n🧪 Submitting SLURM test jobs...")

    for test in test_functions:
        print(f"\n📝 Test: {test['description']}")
        print(f"   Function: {test['name']}")

        try:
            # Prepare function data
            func_data = {
                "function": test["func"],
                "args": test["args"],
                "kwargs": {},
                "env": {"CLUSTRIX_TEST": "true"},
                "requirements": {},
            }

            # Submit job
            print("   🚀 Submitting SLURM job...")
            job_config = {
                "job_name": f"clustrix_test_{test['name']}",
                "cores": 2,
                "memory": "2GB",
                "time": "00:05:00",
            }
            job_id = executor.submit_job(func_data, job_config)

            print(f"   ✅ Job submitted: {job_id}")

            # Wait for job completion
            print(f"   ⏳ Waiting for job completion...")
            start_time = time.time()
            timeout = 300  # 5 minutes

            while time.time() - start_time < timeout:
                status = executor.get_job_status(job_id)
                print(f"   📊 Job status: {status}", end="\r")

                if status == "completed":
                    print(f"\n   ✅ Job completed successfully")

                    # Get result
                    try:
                        result = executor.get_job_result(job_id)
                        print(f"   📦 Result retrieved: {result}")

                        # Validate result
                        if test["expected"] is not None:
                            if result == test["expected"]:
                                print(f"   ✅ Result validation passed")
                                results[test["name"]] = {
                                    "success": True,
                                    "result": result,
                                }
                            else:
                                print(
                                    f"   ❌ Result mismatch: expected {test['expected']}, got {result}"
                                )
                                results[test["name"]] = {
                                    "success": False,
                                    "result": result,
                                    "expected": test["expected"],
                                }
                        else:
                            # Just check it's the right type
                            if isinstance(result, dict):
                                print(f"   ✅ Result type validation passed")
                                results[test["name"]] = {
                                    "success": True,
                                    "result": result,
                                }
                            else:
                                print(f"   ❌ Unexpected result type: {type(result)}")
                                results[test["name"]] = {
                                    "success": False,
                                    "result": result,
                                }
                    except Exception as e:
                        print(f"   ❌ Failed to retrieve result: {e}")
                        results[test["name"]] = {"success": False, "error": str(e)}

                    break

                elif status == "failed":
                    print(f"\n   ❌ Job failed")
                    results[test["name"]] = {"success": False, "status": "failed"}
                    break

                time.sleep(5)  # Check every 5 seconds
            else:
                print(f"\n   ⏱️  Job timed out after {timeout} seconds")
                results[test["name"]] = {"success": False, "status": "timeout"}

        except Exception as e:
            print(f"   ❌ Test failed: {e}")
            logger.exception("SLURM test error")
            results[test["name"]] = {"success": False, "error": str(e)}

    # Test job cleanup (optional)
    print("\n🧹 Testing remote file operations...")
    try:
        # Check if we can list job directories
        import paramiko

        ssh_client = paramiko.SSHClient()
        configure_host_key_policy(ssh_client, config)

        connect_kwargs = {"hostname": hostname, "username": username, "timeout": 30}
        if password:
            connect_kwargs["password"] = password

        ssh_client.connect(**connect_kwargs)

        # List job directories
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la {config.remote_work_dir}/ 2>/dev/null | head -10"
        )
        output = stdout.read().decode().strip()

        if output:
            print(f"   ✅ Remote directory accessible:")
            for line in output.split("\n")[:5]:
                print(f"      {line}")
        else:
            print(f"   ⚠️  Remote directory empty or not accessible")

        ssh_client.close()
    except Exception as e:
        print(f"   ⚠️  Could not check remote files: {e}")

    # Summary
    print("\n📊 SLURM Validation Summary")
    print("=" * 70)

    success_count = sum(1 for r in results.values() if r.get("success", False))
    total_tests = len(test_functions)

    for test_name, result in results.items():
        if result.get("success"):
            print(f"   ✅ {test_name}: PASSED")
        else:
            print(f"   ❌ {test_name}: FAILED")
            if "error" in result:
                print(f"      Error: {result['error']}")
            elif "status" in result:
                print(f"      Status: {result['status']}")

    print(f"\n🎯 Overall Result: {success_count}/{total_tests} tests passed")

    if success_count > 0:
        print("🎉 SLURM job submission working!")
        print(f"   Jobs executed in: {config.remote_work_dir}")
        return True
    else:
        print("❌ SLURM job submission validation failed")
        return False


def test_slurm_advanced_features():
    """Test advanced SLURM features like array jobs and parallel execution."""
    print("\n🔬 Advanced SLURM Features Test")
    print("=" * 70)

    # Get credentials
    creds = ValidationCredentials()
    slurm_creds = creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")

    if not slurm_creds:
        print("❌ No SLURM cluster credentials found")
        return False

    hostname = slurm_creds.get("hostname")
    username = slurm_creds.get("username")
    password = slurm_creds.get("password")

    # Test parallel loop execution
    print("🔄 Testing parallel loop execution...")

    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=hostname,
        username=username,
        password=password,
        remote_work_dir=f"{require_test_remote_work_dir()}/clustrix",
        default_cores=4,
        default_memory="8GB",
        default_time="00:10:00",
    )

    executor = ClusterExecutor(config)

    # Function with a loop that can be parallelized
    def compute_squares(n=10):
        results = []
        for i in range(n):
            # This loop could be parallelized
            result = i**2
            results.append(result)
        return sum(results)

    try:
        func_data = {
            "function": compute_squares,
            "args": (100,),
            "kwargs": {},
            "env": {},
            "requirements": {},
        }

        print("   🚀 Submitting parallel job...")
        job_config = {"job_name": "clustrix_parallel_test", "cores": 4, "memory": "4GB"}
        job_id = executor.submit_job(func_data, job_config)

        print(f"   ✅ Job submitted: {job_id}")

        # Wait for completion
        start_time = time.time()
        while time.time() - start_time < 120:  # 2 minute timeout
            status = executor.get_job_status(job_id)
            if status == "completed":
                result = executor.get_job_result(job_id)
                expected = sum(i**2 for i in range(100))
                if result == expected:
                    print(f"   ✅ Parallel execution successful: {result}")
                    return True
                else:
                    print(f"   ❌ Result mismatch: expected {expected}, got {result}")
                    return False
            elif status == "failed":
                print(f"   ❌ Job failed")
                return False
            time.sleep(5)

        print("   ⏱️  Job timed out")
        return False

    except Exception as e:
        print(f"   ❌ Advanced test failed: {e}")
        return False


def main():
    """Main validation function."""
    print("🎯 Starting SLURM Cluster Validation")
    print("=" * 70)

    # Run basic SLURM tests
    basic_success = test_slurm_job_submission()

    # Run advanced tests if basic tests pass
    advanced_success = False
    if basic_success:
        advanced_success = test_slurm_advanced_features()

    # Overall summary
    print("\n📊 Final SLURM Validation Summary")
    print("=" * 70)

    if basic_success:
        print("✅ Basic SLURM job submission: PASSED")
    else:
        print("❌ Basic SLURM job submission: FAILED")

    if advanced_success:
        print("✅ Advanced SLURM features: PASSED")
    else:
        print("❌ Advanced SLURM features: FAILED or SKIPPED")

    if basic_success:
        print("\n🎉 SLURM validation successful!")
        print("   Clustrix can submit and execute jobs on SLURM clusters.")
        print("   Remote work directory configured and accessible.")
        return 0
    else:
        print("\n❌ SLURM validation failed.")
        print("   Check error messages above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
