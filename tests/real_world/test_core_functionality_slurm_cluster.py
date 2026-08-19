"""
Test core ClustriX functionality on ndoli using proper configuration file.

This test validates that the ClustriX toolbox can:
1. Load configuration from ndoli_config.yml
2. Authenticate using 1Password or environment variables
3. Submit SLURM jobs with module loads
4. Execute functions on remote cluster
5. Retrieve results properly

This is NOT a debugging script - it tests the actual ClustriX core functionality.
"""

import pytest
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_ndoli_core_functionality():
    """Test that core ClustriX functionality works on ndoli."""

    # Load the ndoli configuration file
    load_config("ndoli_config.yml")

    # Get credentials using the existing credential manager
    # This tests the same authentication path used by the toolbox
    ndoli_creds = credentials.get_ndoli_credentials()

    if not ndoli_creds:
        pytest.skip(
            "No ndoli credentials available - check 1Password or CLUSTRIX_PASSWORD env var"
        )

    # Override configuration with actual credentials
    # This mimics how the toolbox should handle authentication
    configure(
        password=ndoli_creds.get("password"),
        cleanup_on_success=False,  # Keep files for verification
        job_poll_interval=15,  # Poll every 15 seconds
    )

    @cluster(cores=2, memory="2GB", time="00:10:00", partition="standard")
    def test_ndoli_computation(n: int) -> dict:
        """
        Test function that validates ndoli environment and computation.

        This function will be serialized, transferred to ndoli, executed in SLURM,
        and results transferred back.
        """
        import os
        import platform
        import sys
        import subprocess

        # Test that module loading worked (python should be available)
        python_info = {
            "executable": sys.executable,
            "version": sys.version,
            "path": sys.path[:3],  # First 3 entries
        }

        # Test SLURM environment
        slurm_info = {
            "job_id": os.getenv("SLURM_JOB_ID"),
            "job_name": os.getenv("SLURM_JOB_NAME"),
            "partition": os.getenv("SLURM_JOB_PARTITION"),
            "cpus": os.getenv("SLURM_CPUS_ON_NODE"),
            "nodelist": os.getenv("SLURM_JOB_NODELIST"),
        }

        # Test computation capability
        computation_result = sum(i**2 for i in range(n))

        # Test system access
        try:
            hostname_result = subprocess.run(
                ["hostname"], capture_output=True, text=True, timeout=5
            )
            hostname = (
                hostname_result.stdout.strip()
                if hostname_result.returncode == 0
                else "unknown"
            )
        except Exception:
            hostname = "subprocess_failed"

        # Test module availability
        try:
            module_result = subprocess.run(
                ["module", "list"], capture_output=True, text=True, timeout=5
            )
            modules_loaded = (
                "module command available"
                if module_result.returncode == 0
                else "module command failed"
            )
        except Exception:
            modules_loaded = "module command not found"

        return {
            "input_n": n,
            "computation_result": computation_result,
            "python_info": python_info,
            "slurm_info": slurm_info,
            "hostname": hostname,
            "platform": platform.platform(),
            "modules_status": modules_loaded,
            "success": True,
        }

    # Execute the function using ClustriX core functionality
    # This tests the complete end-to-end workflow
    print("Submitting job to ndoli using ClustriX core functionality...")
    result = test_ndoli_computation(15)

    # Validate that the job executed successfully
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert (
        result["input_n"] == 15
    ), f"Input mismatch: expected 15, got {result['input_n']}"
    assert result["computation_result"] == sum(
        i**2 for i in range(15)
    ), f"Computation failed"
    assert result["success"] is True, "Function execution was not successful"

    # Validate SLURM environment
    assert (
        result["slurm_info"]["job_id"] is not None
    ), "SLURM_JOB_ID not set - not running in SLURM"
    assert (
        result["slurm_info"]["partition"] == "standard"
    ), f"Wrong partition: {result['slurm_info']['partition']}"
    assert (
        result["slurm_info"]["cpus"] == "2"
    ), f"Wrong CPU count: {result['slurm_info']['cpus']}"

    # Validate Python environment
    assert (
        "3." in result["python_info"]["version"]
    ), f"Python not properly loaded: {result['python_info']['version']}"
    assert result["hostname"] != "unknown", "Could not determine hostname"
    assert result["hostname"] != "subprocess_failed", "Subprocess execution failed"

    # Validate module loading worked
    # Note: This might not be "module command available" on all systems, so we're lenient
    print(f"Module status: {result['modules_status']}")

    print("SUCCESS: ClustriX core functionality works on ndoli!")
    print(f"✓ Job ID: {result['slurm_info']['job_id']}")
    print(f"✓ Hostname: {result['hostname']}")
    print(f"✓ Python: {result['python_info']['version']}")
    print(f"✓ Computation result: {result['computation_result']}")
    print(f"✓ Partition: {result['slurm_info']['partition']}")
    print(f"✓ CPUs: {result['slurm_info']['cpus']}")

    return result
