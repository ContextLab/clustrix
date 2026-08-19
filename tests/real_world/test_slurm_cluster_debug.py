"""
Debug test for ndoli execution issues.
"""

import pytest
from clustrix import cluster, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_ndoli_simple_debug():
    """Simple test to debug ndoli execution."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Configure with very short polling interval
    configure(
        cluster_type="ssh",
        cluster_host=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        key_file=ndoli_creds.get("private_key_path"),
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

    print(f"SUCCESS: ndoli test completed with result: {result}")
