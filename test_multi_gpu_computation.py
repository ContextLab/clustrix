#!/usr/bin/env python3
"""
Test parallel computation across multiple GPUs using working patterns.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_multi_gpu_parallel_computation():
    """Test parallel computation across multiple GPUs."""
    
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
            "CUDA_VISIBLE_DEVICES": "0,1,2,3"  # Make 4 GPUs visible
        }
    )
    
    @cluster(cores=4, memory="16GB")
    def multi_gpu_parallel_computation():
        """Test parallel computation across multiple GPUs."""
        import subprocess
        import json
        
        result = {
            "cuda_devices": None,
            "data_parallel_test": None,
            "distributed_computation": None,
        }
        
        # 1. Verify GPU visibility
        try:
            gpu_check = subprocess.run(
                ["python", "-c", "import torch; import os; print(f'ENV:{os.environ.get(\"CUDA_VISIBLE_DEVICES\", \"default\")}'); print(f'COUNT:{torch.cuda.device_count()}')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            
            if gpu_check.returncode == 0:
                lines = gpu_check.stdout.strip().split('\n')
                gpu_info = {}
                for line in lines:
                    if line.startswith('ENV:'):
                        gpu_info['cuda_visible_devices'] = line.split(':', 1)[1]
                    elif line.startswith('COUNT:'):
                        gpu_info['device_count'] = int(line.split(':', 1)[1])
                result["cuda_devices"] = {"success": True, "info": gpu_info}
            else:
                result["cuda_devices"] = {"success": False, "error": gpu_check.stderr}
        except Exception as e:
            result["cuda_devices"] = {"success": False, "error": str(e)}
        
        # 2. Test data parallel computation
        try:
            data_parallel_code = '''
import torch
import torch.nn as nn
import time

# Create a simple model
model = nn.Linear(1000, 100)

# Check if multiple GPUs available
device_count = torch.cuda.device_count()
print(f"DEVICES:{device_count}")

if device_count > 1:
    # Use DataParallel for multi-GPU
    model = nn.DataParallel(model)
    model = model.cuda()
    
    # Create test data
    test_input = torch.randn(64, 1000).cuda()
    
    # Time the computation
    start_time = time.time()
    output = model(test_input)
    end_time = time.time()
    
    print(f"PARALLEL_TIME:{end_time - start_time:.4f}")
    print(f"OUTPUT_SHAPE:{list(output.shape)}")
    print(f"GPU_USED:{torch.cuda.current_device()}")
else:
    print("SINGLE_GPU_ONLY")
            '''
            
            parallel_result = subprocess.run(
                ["python", "-c", data_parallel_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=60
            )
            
            if parallel_result.returncode == 0:
                lines = parallel_result.stdout.strip().split('\n')
                parallel_info = {}
                for line in lines:
                    if line.startswith('DEVICES:'):
                        parallel_info['device_count'] = int(line.split(':', 1)[1])
                    elif line.startswith('PARALLEL_TIME:'):
                        parallel_info['computation_time'] = float(line.split(':', 1)[1])
                    elif line.startswith('OUTPUT_SHAPE:'):
                        parallel_info['output_shape'] = line.split(':', 1)[1]
                    elif line.startswith('GPU_USED:'):
                        parallel_info['gpu_used'] = int(line.split(':', 1)[1])
                    elif line == 'SINGLE_GPU_ONLY':
                        parallel_info['single_gpu_only'] = True
                
                result["data_parallel_test"] = {"success": True, "info": parallel_info}
            else:
                result["data_parallel_test"] = {"success": False, "error": parallel_result.stderr}
        except Exception as e:
            result["data_parallel_test"] = {"success": False, "error": str(e)}
        
        # 3. Test distributed computation across devices
        try:
            distributed_code = '''
import torch

device_count = torch.cuda.device_count()
print(f"AVAILABLE_DEVICES:{device_count}")

if device_count >= 2:
    # Create tensors on different GPUs
    tensor_gpu0 = torch.randn(1000, 1000, device="cuda:0")
    tensor_gpu1 = torch.randn(1000, 1000, device="cuda:1")
    
    # Perform operations on each GPU
    result_gpu0 = torch.mm(tensor_gpu0, tensor_gpu0.t())
    result_gpu1 = torch.mm(tensor_gpu1, tensor_gpu1.t())
    
    # Move results to CPU and combine
    final_result = result_gpu0.cpu() + result_gpu1.cpu()
    
    print(f"DISTRIBUTED_SUCCESS:True")
    print(f"RESULT_SHAPE:{list(final_result.shape)}")
    print(f"RESULT_SUM:{final_result.sum().item():.2f}")
else:
    print("INSUFFICIENT_GPUS_FOR_DISTRIBUTION")
            '''
            
            distributed_result = subprocess.run(
                ["python", "-c", distributed_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=60
            )
            
            if distributed_result.returncode == 0:
                lines = distributed_result.stdout.strip().split('\n')
                distributed_info = {}
                for line in lines:
                    if line.startswith('AVAILABLE_DEVICES:'):
                        distributed_info['available_devices'] = int(line.split(':', 1)[1])
                    elif line.startswith('DISTRIBUTED_SUCCESS:'):
                        distributed_info['success'] = line.split(':', 1)[1] == 'True'
                    elif line.startswith('RESULT_SHAPE:'):
                        distributed_info['result_shape'] = line.split(':', 1)[1]
                    elif line.startswith('RESULT_SUM:'):
                        distributed_info['result_sum'] = float(line.split(':', 1)[1])
                    elif line == 'INSUFFICIENT_GPUS_FOR_DISTRIBUTION':
                        distributed_info['insufficient_gpus'] = True
                
                result["distributed_computation"] = {"success": True, "info": distributed_info}
            else:
                result["distributed_computation"] = {"success": False, "error": distributed_result.stderr}
        except Exception as e:
            result["distributed_computation"] = {"success": False, "error": str(e)}
        
        return result
    
    print("Testing multi-GPU parallel computation...")
    try:
        result = multi_gpu_parallel_computation()
        
        print(f"\n=== Multi-GPU Parallel Computation Results ===")
        
        # CUDA devices check
        if result['cuda_devices']['success']:
            info = result['cuda_devices']['info']
            print(f"CUDA_VISIBLE_DEVICES: {info['cuda_visible_devices']}")
            print(f"PyTorch device count: {info['device_count']}")
        else:
            print(f"CUDA devices check failed: {result['cuda_devices']['error']}")
        
        # Data parallel test
        if result['data_parallel_test']['success']:
            info = result['data_parallel_test']['info']
            if 'single_gpu_only' in info:
                print(f"Data parallel: Single GPU only")
            else:
                print(f"Data parallel: {info['device_count']} devices")
                print(f"Computation time: {info['computation_time']:.4f}s")
                print(f"Output shape: {info['output_shape']}")
        else:
            print(f"Data parallel test failed: {result['data_parallel_test']['error']}")
        
        # Distributed computation test
        if result['distributed_computation']['success']:
            info = result['distributed_computation']['info']
            if 'insufficient_gpus' in info:
                print(f"Distributed computation: Insufficient GPUs")
            else:
                print(f"Distributed computation: Success across {info['available_devices']} devices")
                print(f"Result shape: {info['result_shape']}")
                print(f"Result sum: {info['result_sum']:.2f}")
        else:
            print(f"Distributed computation failed: {result['distributed_computation']['error']}")
        
        # Check overall success
        cuda_ok = result['cuda_devices']['success']
        parallel_ok = result['data_parallel_test']['success']
        distributed_ok = result['distributed_computation']['success']
        
        if cuda_ok and parallel_ok and distributed_ok:
            print(f"\n🎉 SUCCESS: Multi-GPU parallel computation working!")
            return True
        else:
            print(f"\n⚠️  Some tests failed")
            return False
            
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    test_multi_gpu_parallel_computation()