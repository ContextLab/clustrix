#!/usr/bin/env python3

import logging
from clustrix import cluster
from clustrix.config import ClusterConfig

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

print("=== Testing Cluster Execution Debug ===")

@cluster(
    cluster_type="ssh",
    cluster_host="tensor01.csail.mit.edu", 
    username="jmanning",
    remote_work_dir="/tmp/clustrix_debug_test"
)
def simple_test():
    """Simple test function to see where it executes."""
    import socket
    import os
    
    return {
        "hostname": socket.gethostname(),
        "current_dir": os.getcwd(),
        "python_version": os.sys.version,
        "message": "Hello from cluster execution test"
    }

if __name__ == "__main__":
    print("Executing test function...")
    try:
        result = simple_test()
        print(f"Result: {result}")
        print(f"Executed on hostname: {result['hostname']}")
        
        if "dartmouth.edu" in result['hostname']:
            print("❌ EXECUTED LOCALLY - not on remote cluster!")
        else:
            print("✅ EXECUTED REMOTELY - on cluster!")
            
    except Exception as e:
        print(f"Error during execution: {e}")
        import traceback
        traceback.print_exc()