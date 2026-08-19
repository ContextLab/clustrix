"""
Working SLURM job test for slurm_cluster using proper Python 3.6 syntax.
"""

import pytest
from clustrix import cluster, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_slurm_cluster_slurm_working():
    """Test SLURM job submission on slurm_cluster with proper Python 3.6 compatibility."""
    slurm_cluster_creds = credentials.get_slurm_cluster_credentials()
    if not slurm_cluster_creds:
        pytest.skip("No slurm_cluster credentials available")

    # Configure for SLURM execution
    configure(
        cluster_type="slurm",
        cluster_host=slurm_cluster_creds["host"],
        username=slurm_cluster_creds["username"],
        password=slurm_cluster_creds.get("password"),
        key_file=slurm_cluster_creds.get("private_key_path"),
        remote_work_dir="/tmp/clustrix_slurm_working",
        python_executable="python3",
        cleanup_on_success=True,
        job_poll_interval=5,
    )

    @cluster(cores=1, memory="1GB", time="00:05:00", partition="standard")
    def slurm_simple_job():
        """Simple SLURM job test compatible with Python 3.6."""
        import os
        import platform

        # Get SLURM environment variables (compatible with Python 3.6)
        slurm_info = {
            "job_id": os.getenv("SLURM_JOB_ID"),
            "job_name": os.getenv("SLURM_JOB_NAME"),
            "cpus": os.getenv("SLURM_CPUS_ON_NODE"),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
        }

        # Simple computation
        result = 42 * 2

        return {
            "computation_result": result,
            "slurm_info": slurm_info,
            "success": True,
        }

    # Execute the SLURM job
    print("Submitting SLURM job...")
    result = slurm_simple_job()

    # Verify result
    assert result["success"] is True
    assert result["computation_result"] == 84
    assert "slurm_info" in result

    print("SUCCESS: SLURM job completed successfully!")
    print("Result:", result)

    # Verify SLURM environment was detected
    slurm_detected = result["slurm_info"]["job_id"] is not None
    if slurm_detected:
        print("SLURM environment detected in job!")
    else:
        print("Warning: SLURM environment not detected, but job completed successfully")

    return result
