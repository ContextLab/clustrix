#!/usr/bin/env python3
"""
Debug VENV2 execution issues by examining what's happening on the remote cluster.
"""

import sys
import os
sys.path.insert(0, '/Users/jmanning/clustrix')

from clustrix.config import load_config, configure, get_config
from clustrix import cluster
from tests.real_world import TestCredentials
import paramiko

def debug_remote_execution():
    """Debug what's happening in the remote execution environment."""
    print("🔍 Debugging remote VENV2 execution...")
    
    # Load config and credentials
    load_config('tensor01_config.yml')
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    # Configure 
    configure(
        cluster_type="ssh",
        cluster_host=tensor01_creds['host'],
        username=tensor01_creds['username'],
        password=tensor01_creds['password'],
        cleanup_on_success=False,  # Keep files for debugging
        use_two_venv=True,
        venv_setup_timeout=600,
        auto_gpu_parallel=False,
        auto_parallel=False,
    )
    
    @cluster(cores=1, memory='4GB', time='00:05:00')
    def simple_debug_function():
        """Simple function to debug execution environment."""
        import sys
        import os
        import torch
        
        debug_info = {
            "python_executable": sys.executable,
            "python_version": sys.version,
            "working_directory": os.getcwd(),
            "environment_vars": dict(os.environ),
            "torch_available": True,
            "cuda_available": False,
            "gpu_count": 0,
        }
        
        try:
            debug_info["cuda_available"] = torch.cuda.is_available()
            debug_info["gpu_count"] = torch.cuda.device_count()
        except Exception as e:
            debug_info["torch_error"] = str(e)
        
        return debug_info
    
    try:
        print("Executing simple debug function...")
        result = simple_debug_function()
        
        print(f"✅ Debug function successful!")
        print(f"   Python executable: {result.get('python_executable', 'Unknown')}")
        print(f"   Python version: {result.get('python_version', 'Unknown')[:50]}...")
        print(f"   Working directory: {result.get('working_directory', 'Unknown')}")
        print(f"   PyTorch available: {result.get('torch_available', 'Unknown')}")
        print(f"   CUDA available: {result.get('cuda_available', 'Unknown')}")
        print(f"   GPU count: {result.get('gpu_count', 'Unknown')}")
        
        if 'torch_error' in result:
            print(f"   ⚠️  PyTorch error: {result['torch_error']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Debug function failed: {e}")
        
        # Try to connect directly and check what's in the remote directory
        try:
            print("\n🔍 Connecting directly to check remote files...")
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=tensor01_creds['host'],
                username=tensor01_creds['username'],
                password=tensor01_creds['password'],
                port=int(tensor01_creds.get('port', 22)),
                timeout=30
            )
            
            # Check what's in the remote work directory
            stdin, stdout, stderr = ssh_client.exec_command('ls -la /tmp/clustrix_tensor01/')
            ls_output = stdout.read().decode().strip()
            ls_error = stderr.read().decode().strip()
            
            print(f"Remote directory listing:")
            print(ls_output)
            if ls_error:
                print(f"Error: {ls_error}")
            
            # Find the most recent job directory
            stdin, stdout, stderr = ssh_client.exec_command('ls -t /tmp/clustrix_tensor01/ | head -1')
            latest_job = stdout.read().decode().strip()
            
            if latest_job:
                print(f"\nMost recent job directory: {latest_job}")
                
                # Check what's in the job directory
                stdin, stdout, stderr = ssh_client.exec_command(f'ls -la /tmp/clustrix_tensor01/{latest_job}/')
                job_output = stdout.read().decode().strip()
                print(f"Job directory contents:")
                print(job_output)
                
                # Check for error logs
                stdin, stdout, stderr = ssh_client.exec_command(f'find /tmp/clustrix_tensor01/{latest_job}/ -name "*.err" -o -name "*.out" -o -name "*.log" | head -5')
                log_files = stdout.read().decode().strip()
                
                if log_files:
                    print(f"\nFound log files:")
                    for log_file in log_files.split('\n'):
                        if log_file.strip():
                            print(f"  {log_file}")
                            stdin, stdout, stderr = ssh_client.exec_command(f'tail -20 {log_file}')
                            log_content = stdout.read().decode().strip()
                            if log_content:
                                print(f"    Last 20 lines of {log_file}:")
                                print(f"    {log_content}")
                            print()
                
                # Check if venv directories exist
                stdin, stdout, stderr = ssh_client.exec_command(f'ls -la /tmp/clustrix_tensor01/{latest_job}/venv*/')
                venv_output = stdout.read().decode().strip()
                if venv_output:
                    print(f"Virtual environment directories:")
                    print(venv_output)
            
            ssh_client.close()
            
        except Exception as ssh_e:
            print(f"❌ Direct SSH debug failed: {ssh_e}")
        
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Starting VENV2 execution debugging...")
    debug_remote_execution()