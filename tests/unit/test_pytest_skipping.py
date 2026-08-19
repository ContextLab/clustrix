#!/usr/bin/env python3
"""
Test pytest automatic skipping for private-cluster tests.
"""

import pytest


# This should be skipped when no configured cluster is reachable
@pytest.mark.cluster_network
def test_cluster_network_marker():
    """Test that gets skipped when no configured cluster is reachable."""
    print("✅ This test runs because a configured cluster is reachable")
    assert True


def test_gpu_cluster_in_name():
    """Test with gpu_cluster in name - skipped when the cluster is absent."""
    print("✅ This gpu_cluster test runs because the cluster is reachable")
    assert True


def test_slurm_cluster_in_name():
    """Test with slurm_cluster in name - skipped when the cluster is absent."""
    print("✅ This slurm_cluster test runs because the cluster is reachable")
    assert True


def test_regular_test():
    """Regular test that should always run."""
    print("✅ This regular test always runs")
    assert True


if __name__ == "__main__":
    # Run pytest on this file
    import subprocess

    result = subprocess.run(
        ["python", "-m", "pytest", __file__, "-v", "-s"], capture_output=True, text=True
    )

    print("PYTEST OUTPUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)

    print(f"Exit code: {result.returncode}")
