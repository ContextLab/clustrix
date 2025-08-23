#!/usr/bin/env python3
"""
Test the shared filesystem fix on SLURM cluster.

This script tests whether the cluster detection logic correctly identifies
when we're running on the target cluster and switches to local filesystem
operations instead of SSH.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clustrix.config import ClusterConfig
from clustrix.file_packaging import package_function_for_execution
from clustrix.secure_credentials import ValidationCredentials
import tempfile
import zipfile
import json


def test_shared_filesystem_detection():
    """Test if filesystem operations work correctly on shared storage."""
    import socket
    from clustrix.filesystem import ClusterFilesystem

    # Get current hostname for comparison
    current_hostname = socket.gethostname()

    # Test 1: Verify we can access shared filesystem directly
    try:
        # Try to access the known shared directory structure
        test_dir = "/dartfs-hpc/rc/home/b/f002d6b/"
        files = os.listdir(test_dir)
        shared_fs_accessible = True
        file_count = len(files)
    except Exception as e:
        shared_fs_accessible = False
        file_count = 0
        error = str(e)

    # Test 2: Create config and test cluster detection
    config = ClusterConfig(
        cluster_type="slurm", cluster_host="ndoli.dartmouth.edu", username="f002d6b"
    )

    # Test 3: Initialize ClusterFilesystem and check if it detects we're on cluster
    fs = ClusterFilesystem(config)
    detected_as_local = fs.config.cluster_type == "local"

    # Test 4: Try filesystem operations
    try:
        # Test basic directory listing
        files = fs.ls(".")
        ls_success = True
        ls_result = f"Found {len(files)} files/directories"
    except Exception as e:
        ls_success = False
        ls_result = f"Error: {str(e)}"

    try:
        # Test file existence check
        exists_result = fs.exists(".")
        exists_success = True
    except Exception as e:
        exists_result = False
        exists_success = False
        exists_error = str(e)

    return {
        "hostname": current_hostname,
        "shared_filesystem_test": {
            "accessible": shared_fs_accessible,
            "file_count": file_count,
            "error": error if not shared_fs_accessible else None,
        },
        "cluster_detection": {
            "original_cluster_type": "slurm",
            "detected_as_local": detected_as_local,
            "final_cluster_type": fs.config.cluster_type,
        },
        "filesystem_operations": {
            "ls_test": {"success": ls_success, "result": ls_result},
            "exists_test": {
                "success": exists_success,
                "result": exists_result,
                "error": exists_error if not exists_success else None,
            },
        },
        "test_status": (
            "SUCCESS"
            if (
                shared_fs_accessible
                and detected_as_local
                and ls_success
                and exists_success
            )
            else "PARTIAL"
        ),
    }


def test_filesystem_integration_advanced():
    """More comprehensive filesystem integration test."""
    from clustrix.filesystem import cluster_ls, cluster_exists, cluster_stat
    from clustrix.config import ClusterConfig

    config = ClusterConfig(
        cluster_type="slurm", cluster_host="ndoli.dartmouth.edu", username="f002d6b"
    )

    try:
        # Test the convenience functions that should now work
        current_files = cluster_ls(".", config)

        # Test file existence
        current_exists = cluster_exists(".", config)

        # Test file stat if possible
        try:
            current_stat = cluster_stat(".", config)
            stat_success = True
            stat_result = f"Directory size info available: {current_stat.size if hasattr(current_stat, 'size') else 'N/A'}"
        except Exception as e:
            stat_success = False
            stat_result = f"Stat error: {str(e)}"

        return {
            "advanced_filesystem_test": "SUCCESS",
            "cluster_ls_result": f"Found {len(current_files)} items",
            "cluster_exists_result": current_exists,
            "cluster_stat_result": stat_result,
            "cluster_stat_success": stat_success,
        }

    except Exception as e:
        return {"advanced_filesystem_test": "FAILED", "error": str(e)}


if __name__ == "__main__":
    print("🧪 Testing Shared Filesystem Fix")
    print("=" * 50)

    # Test 1: Basic shared filesystem detection
    print("🔍 Test 1: Shared filesystem detection...")
    result1 = test_shared_filesystem_detection()
    print(json.dumps(result1, indent=2))

    print("\n" + "=" * 50)

    # Test 2: Advanced filesystem operations
    print("🔍 Test 2: Advanced filesystem operations...")
    result2 = test_filesystem_integration_advanced()
    print(json.dumps(result2, indent=2))

    # Save combined results
    combined_results = {
        "test_timestamp": __import__("datetime").datetime.now().isoformat(),
        "basic_test": result1,
        "advanced_test": result2,
    }

    with open("shared_filesystem_test_results.json", "w") as f:
        json.dump(combined_results, f, indent=2)

    print(f"\n✅ Test results saved to: shared_filesystem_test_results.json")
