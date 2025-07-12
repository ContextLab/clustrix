"""
Test SLURM job submission on ndoli.dartmouth.edu.
"""

import pytest
from clustrix import cluster, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_ndoli_slurm_job_submission():
    """Test SLURM job submission on ndoli."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Configure for SLURM execution
    configure(
        cluster_type="slurm",
        cluster_host=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        key_file=ndoli_creds.get("private_key_path"),
        remote_work_dir=f"/tmp/clustrix_slurm_test",
        python_executable="python3",
        cleanup_on_success=True,
        job_poll_interval=5,  # Poll every 5 seconds
    )

    @cluster(cores=2, memory="2GB", time="00:10:00", partition="standard")
    def slurm_test_job():
        """Simple SLURM job test."""
        import os
        import platform

        # Get SLURM environment variables
        slurm_info = {
            "job_id": os.getenv("SLURM_JOB_ID"),
            "job_name": os.getenv("SLURM_JOB_NAME"),
            "cpus": os.getenv("SLURM_CPUS_ON_NODE"),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
        }

        # Do some computation
        result = sum(i**2 for i in range(1000))

        return {
            "computation_result": result,
            "slurm_info": slurm_info,
            "success": True,
        }

    # Execute the SLURM job
    result = slurm_test_job()

    # Verify result
    assert result["success"] is True
    assert result["computation_result"] == sum(i**2 for i in range(1000))
    assert "slurm_info" in result

    print(f"SUCCESS: SLURM job completed with result: {result}")
