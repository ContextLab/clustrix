#!/usr/bin/env python3
"""
Test functions for shared filesystem validation.
"""

def test_shared_filesystem_detection(config):
    """Test if cluster detection and filesystem operations work correctly."""
    import socket
    import os
    from clustrix.filesystem import ClusterFilesystem
    
    hostname = socket.gethostname()
    
    # Test 1: Direct filesystem access
    try:
        direct_files = os.listdir(".")
        direct_success = True
        direct_count = len(direct_files)
    except Exception as e:
        direct_success = False
        direct_count = 0
        direct_error = str(e)
    
    # Test 2: ClusterFilesystem with detection
    try:
        fs = ClusterFilesystem(config)
        detected_as_local = (fs.config.cluster_type == "local")
        
        # Test filesystem operations
        fs_files = fs.ls(".")
        fs_success = True
        fs_count = len(fs_files)
        
        # Test exists
        exists_result = fs.exists(".")
        
    except Exception as e:
        detected_as_local = False
        fs_success = False
        fs_count = 0
        exists_result = False
        fs_error = str(e)
    
    return {
        "hostname": hostname,
        "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
        "direct_filesystem": {
            "success": direct_success,
            "file_count": direct_count,
            "error": direct_error if not direct_success else None
        },
        "cluster_detection": {
            "detected_as_local": detected_as_local
        },
        "cluster_filesystem": {
            "success": fs_success,
            "file_count": fs_count,
            "exists_result": exists_result,
            "error": fs_error if not fs_success else None
        },
        "test_status": "SUCCESS" if (direct_success and detected_as_local and fs_success) else "FAILED"
    }