"""
Debug test for slurm_cluster execution issues.
"""

import pytest
from clustrix import cluster, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_slurm_cluster_simple_debug():
    """Simple test to debug slurm_cluster execution."""
    slurm_cluster_creds = credentials.get_slurm_cluster_credentials()
    if not slurm_cluster_creds:
        pytest.skip("No slurm_cluster credentials available")

    # Configure with very short polling interval
    configure(
        cluster_type="ssh",
        cluster_host=slurm_cluster_creds["host"],
        username=slurm_cluster_creds["username"],
        password=slurm_cluster_creds.get("password"),
        key_file=slurm_cluster_creds.get("private_key_path"),
        remote_work_dir=f"/tmp/clustrix_debug_test",
        python_executable="python3",
        cleanup_on_success=True,
        job_poll_interval=2,  # Very short polling
    )

    @cluster(cores=1, memory="1GB")
    def simple_test():
        """Simplest possible test."""
        return {"result": "success", "value": 42}

    # Execute the simple test
    result = simple_test()

    # Verify result
    assert result["result"] == "success"
    assert result["value"] == 42

    print(f"SUCCESS: slurm_cluster test completed with result: {result}")
