#!/usr/bin/env python3
"""
Check the status of jobs on tensor01 to debug execution issues.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def check_tensor01_status():
    """Check the remote job status to debug execution issues."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,  # Keep files for debugging
        job_poll_interval=5,
        auto_gpu_parallel=False,
    )
    
    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def check_remote_environment():
        """Check the remote environment status."""
        import subprocess
        import os
        
        # Simple environment check
        result = subprocess.run(
            ["python", "-c", """
import sys
print(f'PYTHON_VERSION:{sys.version}')
print(f'PYTHON_PATH:{sys.executable}')

import os
cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'NOT_SET')
print(f'CUDA_VISIBLE_DEVICES:{cuda_devices}')

try:
    import torch
    print(f'TORCH_IMPORTED:SUCCESS')
    print(f'TORCH_VERSION:{torch.__version__}')
    print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')
    print(f'DEVICE_COUNT:{torch.cuda.device_count()}')
except Exception as e:
    print(f'TORCH_ERROR:{e}')

print('ENVIRONMENT_CHECK:COMPLETE')
"""],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60
        )
        
        return {"output": result.stdout, "error": result.stderr, "code": result.returncode}
    
    print("Checking tensor01 remote environment...")
    try:
        result = check_remote_environment()
        
        print(f"✅ Environment check completed (code: {result['code']})")
        print(f"STDOUT:\n{result['output']}")
        
        if result['error']:
            print(f"STDERR:\n{result['error']}")
        
        # Parse results
        output = result['output']
        if 'DEVICE_COUNT:' in output:
            device_line = [line for line in output.split('\n') if 'DEVICE_COUNT:' in line][0]
            device_count = device_line.split(':', 1)[1]
            print(f"\n🎯 DEVICE COUNT: {device_count}")
        
        if 'CUDA_VISIBLE_DEVICES:' in output:
            cuda_line = [line for line in output.split('\n') if 'CUDA_VISIBLE_DEVICES:' in line][0]
            cuda_devices = cuda_line.split(':', 1)[1]
            print(f"🔍 CUDA_VISIBLE_DEVICES: {cuda_devices}")
        
        return result
        
    except Exception as e:
        print(f"❌ Environment check failed: {e}")
        return None

if __name__ == "__main__":
    result = check_tensor01_status()
    if result:
        print(f"\n📋 Environment check completed")
    else:
        print(f"\n❌ Environment check failed")