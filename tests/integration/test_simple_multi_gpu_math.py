#!/usr/bin/env python3
"""
Simple multi-GPU computation test using verified working patterns.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_simple_multi_gpu_math():
    """Test simple multi-GPU mathematical operations."""
    
    # Load config and modify for multi-GPU access
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # Configure with multiple GPUs visible
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={
            "CUDA_VISIBLE_DEVICES": "0,1"  # Start with just 2 GPUs
        }
    )
    
    @cluster(cores=2, memory="8GB")
    def simple_multi_gpu_math():
        """Simple multi-GPU mathematical operations."""
        import subprocess
        
        result = {"gpu_math": None}
        
        # Simple multi-GPU computation test
        try:
            gpu_code = """
import torch
import os

# Check environment
cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'default')
device_count = torch.cuda.device_count()

print(f'CUDA_VISIBLE_DEVICES:{cuda_devices}')
print(f'DEVICE_COUNT:{device_count}')

if device_count >= 2:
    # Create tensors on different GPUs and compute
    a = torch.tensor([1.0, 2.0], device='cuda:0')
    b = torch.tensor([3.0, 4.0], device='cuda:1')
    
    # Move to same device for computation
    c = a + b.to('cuda:0')
    result = c.cpu().tolist()
    
    print(f'RESULT:{result}')
    print(f'SUCCESS:True')
else:
    print(f'INSUFFICIENT_GPUS:{device_count}')
"""
            
            gpu_result = subprocess.run(
                ["python", "-c", gpu_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            
            if gpu_result.returncode == 0:
                lines = gpu_result.stdout.strip().split('\n')
                math_info = {}
                for line in lines:
                    if line.startswith('CUDA_VISIBLE_DEVICES:'):
                        math_info['cuda_devices'] = line.split(':', 1)[1]
                    elif line.startswith('DEVICE_COUNT:'):
                        math_info['device_count'] = int(line.split(':', 1)[1])
                    elif line.startswith('RESULT:'):
                        math_info['computation_result'] = line.split(':', 1)[1]
                    elif line.startswith('SUCCESS:'):
                        math_info['success'] = line.split(':', 1)[1] == 'True'
                    elif line.startswith('INSUFFICIENT_GPUS:'):
                        math_info['insufficient_gpus'] = int(line.split(':', 1)[1])
                
                result["gpu_math"] = {"success": True, "info": math_info}
            else:
                result["gpu_math"] = {"success": False, "error": gpu_result.stderr}
        except Exception as e:
            result["gpu_math"] = {"success": False, "error": str(e)}
        
        return result
    
    print("Testing simple multi-GPU math...")
    try:
        result = simple_multi_gpu_math()
        
        print(f"\n=== Simple Multi-GPU Math Results ===")
        
        if result['gpu_math']['success']:
            info = result['gpu_math']['info']
            print(f"CUDA_VISIBLE_DEVICES: {info.get('cuda_devices', 'unknown')}")
            print(f"Device count: {info.get('device_count', 'unknown')}")
            
            if 'insufficient_gpus' in info:
                print(f"Insufficient GPUs: only {info['insufficient_gpus']} available")
                return False
            elif info.get('success'):
                print(f"Computation result: {info.get('computation_result', 'unknown')}")
                print(f"\n🎉 SUCCESS: Multi-GPU computation working!")
                return True
            else:
                print(f"Computation failed")
                return False
        else:
            print(f"Test failed: {result['gpu_math']['error']}")
            return False
            
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    test_simple_multi_gpu_math()