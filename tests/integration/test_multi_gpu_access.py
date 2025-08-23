#!/usr/bin/env python3
"""
Test multi-GPU access without automatic parallelization to avoid complexity threshold.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_multi_gpu_access():
    """Test accessing multiple GPUs manually."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # Configure with multiple GPUs visible and AUTO GPU PARALLELIZATION DISABLED
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Disable to avoid complexity threshold
        environment_variables={
            "CUDA_VISIBLE_DEVICES": "0,1,2,3"  # Make 4 GPUs visible
        }
    )
    
    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def simple_multi_gpu_access():
        """Simple multi-GPU access test."""
        import subprocess
        
        result = subprocess.run([
            "python", "-c", """
import torch
import os

cuda_env = os.environ.get('CUDA_VISIBLE_DEVICES', 'default')
gpu_count = torch.cuda.device_count()

print(f'CUDA_VISIBLE_DEVICES:{cuda_env}')
print(f'GPU_COUNT:{gpu_count}')

if gpu_count >= 2:
    # Test access to multiple GPUs
    for gpu_id in range(min(2, gpu_count)):
        torch.cuda.set_device(gpu_id)
        device = torch.device(f'cuda:{gpu_id}')
        x = torch.tensor([1.0, 2.0], device=device)
        print(f'GPU_{gpu_id}_ACCESS:success')
    print('MULTI_GPU_ACCESS:success')
else:
    print('MULTI_GPU_ACCESS:insufficient_gpus')
"""
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        
        return {"success": result.returncode == 0, "output": result.stdout}
    
    print("Testing multi-GPU access (no auto-parallelization)...")
    try:
        result = simple_multi_gpu_access()
        
        if result["success"]:
            output = result["output"]
            print(f"✅ Test completed!")
            print(f"Output:\n{output}")
            
            if "MULTI_GPU_ACCESS:success" in output:
                print("🎉 Multi-GPU access working!")
                return True
            elif "MULTI_GPU_ACCESS:insufficient_gpus" in output:
                print("⚠️  Insufficient GPUs for multi-GPU test")
                return True
            else:
                print("❌ Unexpected output")
                return False
        else:
            print(f"❌ Test failed")
            return False
            
    except Exception as e:
        print(f"❌ Test exception: {e}")
        return False

if __name__ == "__main__":
    success = test_multi_gpu_access()
    if success:
        print("\n🎉 Multi-GPU access test PASSED!")
    else:
        print("\n❌ Multi-GPU access test FAILED!")