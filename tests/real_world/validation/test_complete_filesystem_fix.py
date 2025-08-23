#!/usr/bin/env python3
"""
Complete test of the shared filesystem fix using the packaging system.
"""


def test_complete_filesystem_operations(config):
    """
    Complete test function that validates all filesystem operations work
    with the new cluster detection and shared storage fix.
    """
    import socket
    import os
    from clustrix.filesystem import ClusterFilesystem, cluster_ls, cluster_exists

    hostname = socket.gethostname()
    slurm_job_id = os.environ.get("SLURM_JOB_ID", "not_set")

    # Test 1: Direct ClusterFilesystem usage
    try:
        fs = ClusterFilesystem(config)
        detected_as_local = fs.config.cluster_type == "local"

        # Test basic operations
        files = fs.ls(".")
        exists_test = fs.exists(".")

        filesystem_success = True
        filesystem_error = None
        file_count = len(files)

    except Exception as e:
        filesystem_success = False
        filesystem_error = str(e)
        detected_as_local = False
        file_count = 0
        exists_test = False

    # Test 2: Convenience functions
    try:
        convenience_files = cluster_ls(".", config)
        convenience_exists = cluster_exists(".", config)
        convenience_success = True
        convenience_count = len(convenience_files)

    except Exception as e:
        convenience_success = False
        convenience_error = str(e)
        convenience_count = 0
        convenience_exists = False

    # Test 3: Shared directory access
    shared_access_tests = {}
    test_paths = ["/dartfs-hpc/rc/home/b/f002d6b", "/tmp", "."]

    for path in test_paths:
        try:
            if os.path.exists(path):
                items = os.listdir(path)
                shared_access_tests[path] = {
                    "accessible": True,
                    "item_count": len(items),
                }
            else:
                shared_access_tests[path] = {
                    "accessible": False,
                    "reason": "Path does not exist",
                }
        except Exception as e:
            shared_access_tests[path] = {"accessible": False, "reason": str(e)}

    return {
        "test_metadata": {
            "hostname": hostname,
            "slurm_job_id": slurm_job_id,
            "test_timestamp": __import__("datetime").datetime.now().isoformat(),
        },
        "cluster_detection": {
            "detected_as_local": detected_as_local,
            "original_cluster_type": "slurm",
            "detection_working": detected_as_local,
        },
        "cluster_filesystem": {
            "success": filesystem_success,
            "file_count": file_count,
            "exists_test": exists_test,
            "error": filesystem_error if not filesystem_success else None,
        },
        "convenience_functions": {
            "success": convenience_success,
            "file_count": convenience_count,
            "exists_test": convenience_exists,
            "error": convenience_error if not convenience_success else None,
        },
        "shared_directory_access": shared_access_tests,
        "overall_test_status": (
            "SUCCESS"
            if (detected_as_local and filesystem_success and convenience_success)
            else "FAILED"
        ),
        "shared_filesystem_fix_working": detected_as_local and filesystem_success,
    }
