#!/usr/bin/env python3
"""
Test multi-GPU detection on tensor01.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_multi_gpu_detection():
    """Test detection of multiple GPUs."""
    
    # Load config and modify for multi-GPU access
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # Configure with more GPUs visible
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={
            "CUDA_VISIBLE_DEVICES": "0,1,2,3"  # Make 4 GPUs visible
        }
    )
    
    @cluster(cores=2, memory="8GB")
    def multi_gpu_detection():
        """Detect multiple GPUs."""
        import subprocess
        import os
        
        result = {
            "cuda_visible_devices": os.environ.get('CUDA_VISIBLE_DEVICES', 'default'),
            "nvidia_smi": None,
            "pytorch_detection": None,
        }
        
        # Test nvidia-smi GPU count
        try:
            nvidia_result = subprocess.run(
                ["nvidia-smi", "-L"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10
            )
            if nvidia_result.returncode == 0:
                gpu_lines = [line for line in nvidia_result.stdout.split('\n') if 'GPU' in line]
                result["nvidia_smi"] = {
                    "success": True,
                    "gpu_count": len(gpu_lines),
                    "gpus": gpu_lines
                }
            else:
                result["nvidia_smi"] = {
                    "success": False,
                    "error": nvidia_result.stderr
                }
        except Exception as e:
            result["nvidia_smi"] = {
                "success": False,
                "error": str(e)
            }
        
        # Test PyTorch GPU detection
        try:
            pytorch_result = subprocess.run(
                ["python", "-c", """
import torch
print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')
print(f'DEVICE_COUNT:{torch.cuda.device_count()}')
for i in range(torch.cuda.device_count()):
    print(f'GPU_{i}:{torch.cuda.get_device_name(i)}')
"""],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            
            if pytorch_result.returncode == 0:
                lines = pytorch_result.stdout.strip().split('\n')
                pytorch_info = {"gpu_names": []}
                
                for line in lines:
                    if line.startswith('CUDA_AVAILABLE:'):
                        pytorch_info['cuda_available'] = line.split(':', 1)[1] == 'True'
                    elif line.startswith('DEVICE_COUNT:'):
                        pytorch_info['device_count'] = int(line.split(':', 1)[1])
                    elif line.startswith('GPU_'):
                        gpu_name = line.split(':', 1)[1]
                        pytorch_info['gpu_names'].append(gpu_name)
                
                result["pytorch_detection"] = {
                    "success": True,
                    "info": pytorch_info
                }
            else:
                result["pytorch_detection"] = {
                    "success": False,
                    "error": pytorch_result.stderr
                }
        except Exception as e:
            result["pytorch_detection"] = {
                "success": False,
                "error": str(e)
            }
        
        return result
    
    print("Testing multi-GPU detection...")
    try:
        result = multi_gpu_detection()
        
        print(f"\n=== Multi-GPU Detection Results ===")
        print(f"CUDA_VISIBLE_DEVICES: {result['cuda_visible_devices']}")
        
        if result['nvidia_smi']['success']:
            print(f"nvidia-smi: {result['nvidia_smi']['gpu_count']} GPUs detected")
            for gpu in result['nvidia_smi']['gpus']:
                print(f"  {gpu.strip()}")
        else:
            print(f"nvidia-smi failed: {result['nvidia_smi']['error']}")
        
        if result['pytorch_detection']['success']:
            info = result['pytorch_detection']['info']
            print(f"PyTorch: {info['device_count']} GPUs available")
            for i, gpu_name in enumerate(info['gpu_names']):
                print(f"  GPU {i}: {gpu_name}")
        else:
            print(f"PyTorch detection failed: {result['pytorch_detection']['error']}")
        
        # Check if we have multiple GPUs available
        pytorch_gpus = result['pytorch_detection']['info']['device_count'] if result['pytorch_detection']['success'] else 0
        
        if pytorch_gpus > 1:
            print(f"\n🎉 SUCCESS: {pytorch_gpus} GPUs detected and available!")
            return True
        elif pytorch_gpus == 1:
            print(f"\n⚠️  Only 1 GPU available (expected multiple)")
            return False
        else:
            print(f"\n❌ No GPUs detected")
            return False
            
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    test_multi_gpu_detection()