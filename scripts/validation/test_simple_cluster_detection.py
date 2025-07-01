#!/usr/bin/env python3
"""
Simple test to validate cluster detection without complex packaging.
"""

def test_simple_cluster_detection():
    """Simple test that just tests the cluster detection logic."""
    import socket
    import os
    
    hostname = socket.gethostname()
    
    # Simulate config setup
    class MockConfig:
        def __init__(self):
            self.cluster_type = "slurm"
            self.cluster_host = "ndoli.dartmouth.edu"
    
    config = MockConfig()
    
    # Test basic detection logic (simplified version of our fix)
    current_hostname = hostname
    target_host = config.cluster_host
    
    is_on_target_cluster = (
        current_hostname == target_host or
        target_host in current_hostname or
        current_hostname in target_host or
        # Domain matching  
        len(current_hostname.split('.')) > 1 and len(target_host.split('.')) > 1 and
        current_hostname.split('.')[1:] == target_host.split('.')[1:]
    )
    
    if is_on_target_cluster:
        config.cluster_type = "local"
        detection_result = "SUCCESS - Detected on target cluster"
    else:
        detection_result = "FAILED - Not detected as target cluster"
    
    # Test basic filesystem operations
    try:
        files = os.listdir(".")
        fs_test = "SUCCESS"
        file_count = len(files)
    except Exception as e:
        fs_test = "FAILED"
        file_count = 0
        fs_error = str(e)
    
    return {
        "hostname": hostname,
        "original_cluster_type": "slurm",
        "final_cluster_type": config.cluster_type,
        "detection_result": detection_result,
        "is_on_target_cluster": is_on_target_cluster,
        "filesystem_test": fs_test,
        "file_count": file_count,
        "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
        "test_status": "SUCCESS" if (is_on_target_cluster and fs_test == "SUCCESS") else "PARTIAL"
    }