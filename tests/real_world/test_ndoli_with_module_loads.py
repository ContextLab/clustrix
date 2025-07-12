"""
Test ndoli SLURM execution with proper module loads configuration.
"""

import pytest
import uuid
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_ndoli_slurm_with_module_loads():
    """Test SLURM job execution on ndoli with proper module loads."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Load the existing ndoli configuration that has module_loads
    load_config("ndoli_config.yml")

    # Override with credentials and some test-specific settings
    configure(
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        key_file=ndoli_creds.get("private_key_path"),
        remote_work_dir=f"/tmp/clustrix_ndoli_slurm_modules_{uuid.uuid4().hex[:8]}",
        cleanup_on_success=False,
        job_poll_interval=10,
        environment_variables={"PYTHONUNBUFFERED": "1", "CLUSTRIX_DEBUG": "1"},
    )

    @cluster(cores=1, memory="1GB", time="00:05:00", partition="standard")
    def test_simple_computation(n: int) -> dict:
        """Test simple computation that requires Python."""
        import os
        import platform
        import sys

        # Test that we can actually use Python
        result = {
            "input": n,
            "computation": sum(i * i for i in range(n)),
            "python_version": sys.version,
            "platform": platform.platform(),
            "hostname": platform.node(),
            "python_executable": sys.executable,
            "environment": {
                "SLURM_JOB_ID": os.getenv("SLURM_JOB_ID"),
                "SLURM_JOB_NAME": os.getenv("SLURM_JOB_NAME"),
                "SLURM_CPUS_ON_NODE": os.getenv("SLURM_CPUS_ON_NODE"),
                "PYTHONUNBUFFERED": os.getenv("PYTHONUNBUFFERED"),
                "CLUSTRIX_DEBUG": os.getenv("CLUSTRIX_DEBUG"),
            },
            "success": True,
        }

        return result

    print("Submitting SLURM job with module loads...")
    result = test_simple_computation(10)

    # Validate the result
    assert isinstance(result, dict)
    assert result["input"] == 10
    assert result["computation"] == sum(
        i * i for i in range(10)
    )  # 0 + 1 + 4 + 9 + ... + 81 = 285
    assert result["success"] is True
    assert "python" in result["python_version"].lower()
    assert result["hostname"] != ""
    assert result["environment"]["PYTHONUNBUFFERED"] == "1"
    assert result["environment"]["CLUSTRIX_DEBUG"] == "1"

    print(f"SUCCESS: SLURM job completed successfully!")
    print(f"Python version: {result['python_version']}")
    print(f"Hostname: {result['hostname']}")
    print(f"SLURM Job ID: {result['environment']['SLURM_JOB_ID']}")
    print(f"Computation result: {result['computation']}")

    return True
