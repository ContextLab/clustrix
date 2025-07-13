#!/usr/bin/env python3
"""
Simplified cluster test to debug hanging issues.
"""

import sys
import os
sys.path.insert(0, '/Users/jmanning/clustrix')

from clustrix.config import load_config, configure, get_config
from clustrix import cluster
from tests.real_world import TestCredentials

def test_basic_connection():
    """Test basic SSH connection without cluster execution."""
    print("🧪 Testing basic SSH connection...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Test SSH connection directly
    import paramiko
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh_client.connect(
            hostname=tensor01_creds['host'],
            username=tensor01_creds['username'],
            password=tensor01_creds['password'],
            port=tensor01_creds.get('port', 22),
            timeout=30
        )
        print("✅ SSH connection successful")
        
        # Test simple command
        stdin, stdout, stderr = ssh_client.exec_command('echo "Hello from tensor01"')
        output = stdout.read().decode().strip()
        print(f"✅ Command output: {output}")
        
        # Test GPU detection command directly
        stdin, stdout, stderr = ssh_client.exec_command('python -c "import torch; print(f\'GPU_COUNT:{torch.cuda.device_count()}\'"')
        gpu_output = stdout.read().decode().strip()
        gpu_error = stderr.read().decode().strip()
        print(f"🎯 GPU test output: {gpu_output}")
        if gpu_error:
            print(f"⚠️  GPU test stderr: {gpu_error}")
            
        ssh_client.close()
        return True
        
    except Exception as e:
        print(f"❌ SSH connection failed: {e}")
        return False

def test_minimal_cluster_with_simpler_config():
    """Test cluster with minimal complexity to avoid venv issues."""
    print("🧪 Testing minimal cluster execution...")
    
    from clustrix.config import ClusterConfig
    
    # Create minimal config to avoid complex setup
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Configure with minimal settings
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=True,  # Clean up to avoid issues
        auto_gpu_parallel=False,  # Disable GPU parallelization
        auto_parallel=False,      # Disable auto-parallelization
    )
    
    @cluster(cores=1, memory='1GB', time='00:02:00')
    def simple_test():
        """Extremely simple test function."""
        return "success"
    
    try:
        result = simple_test()
        print(f"✅ Cluster execution successful: {result}")
        return True
    except Exception as e:
        print(f"❌ Cluster execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Starting cluster debugging...")
    
    # Test 1: Basic SSH connection
    ssh_works = test_basic_connection()
    
    if ssh_works:
        print("\n" + "="*50)
        # Test 2: Minimal cluster execution
        cluster_works = test_minimal_cluster_with_simpler_config()
        
        if cluster_works:
            print("🎉 All tests passed!")
        else:
            print("❌ Cluster execution failed")
    else:
        print("❌ SSH connection failed - skipping cluster test")