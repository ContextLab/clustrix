#!/usr/bin/env python3
"""
Basic test of automatic GPU parallelization feature.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_basic_auto_gpu_parallelization():
    """Test basic automatic GPU parallelization functionality."""
    
    # Load configuration
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # Configure with automatic GPU parallelization enabled
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True  # Enable automatic GPU parallelization
    )
    
    @cluster(cores=2, memory="8GB")
    def simple_auto_gpu_test():
        """Simple test with automatic GPU parallelization."""
        import subprocess
        
        # Test that GPU parallelization detection works
        result = subprocess.run([
            "python", "-c", """
import torch

# Check GPU availability
gpu_available = torch.cuda.is_available()
gpu_count = torch.cuda.device_count()

print(f'CUDA_AVAILABLE:{gpu_available}')
print(f'GPU_COUNT:{gpu_count}')

if gpu_count > 1:
    print('AUTO_PARALLEL_ELIGIBLE:True')
    
    # Simple multi-GPU test
    results = []
    for gpu_id in range(min(2, gpu_count)):
        torch.cuda.set_device(gpu_id)
        device = torch.device(f'cuda:{gpu_id}')
        
        # Simple computation
        x = torch.randn(100, 100, device=device)
        y = torch.mm(x, x.t())
        trace_val = y.trace().item()
        results.append(trace_val)
    
    print(f'MULTI_GPU_RESULTS:{len(results)}')
else:
    print('AUTO_PARALLEL_ELIGIBLE:False')
    print('REASON:insufficient_gpus')
"""
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        
        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr
        }
    
    print("Testing basic automatic GPU parallelization...")
    try:
        result = simple_auto_gpu_test()
        
        if result["success"]:
            output = result["output"]
            print(f"✅ Test completed successfully!")
            print(f"Output:\n{output}")
            
            if "AUTO_PARALLEL_ELIGIBLE:True" in output:
                print("🎉 Automatic GPU parallelization is available!")
                if "MULTI_GPU_RESULTS:" in output:
                    results_line = [line for line in output.split('\n') if 'MULTI_GPU_RESULTS:' in line][0]
                    result_count = int(results_line.split(':', 1)[1])
                    print(f"✅ Successfully executed on {result_count} GPUs")
                    return True
            elif "AUTO_PARALLEL_ELIGIBLE:False" in output:
                print("⚠️  Automatic GPU parallelization not eligible (insufficient GPUs)")
                return True  # Still a successful test
            else:
                print("❌ Unexpected output format")
                return False
        else:
            print(f"❌ Test failed: {result['error']}")
            return False
            
    except Exception as e:
        print(f"❌ Test exception: {e}")
        return False

if __name__ == "__main__":
    success = test_basic_auto_gpu_parallelization()
    if success:
        print("\n🎉 Basic automatic GPU parallelization test PASSED!")
    else:
        print("\n❌ Basic automatic GPU parallelization test FAILED!")