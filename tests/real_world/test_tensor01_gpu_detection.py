#!/usr/bin/env python3
"""
Pytest tests for GPU detection on tensor01.
"""

import pytest
from clustrix import cluster
from clustrix.config import load_config, configure


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_8_gpu_detection(tensor01_credentials):
    """Test that tensor01 correctly detects all 8 GPUs."""
    
    load_config("tensor01_config.yml")
    
    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Disable to avoid complexity issues
    )
    
    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def detect_all_gpus():
        """Detect all available GPUs on tensor01."""
        import subprocess
        
        result = subprocess.run([
            "python", "-c", """
import torch
import os

# Show environment
cuda_env = os.environ.get('CUDA_VISIBLE_DEVICES', 'ALL_GPUS')
print(f'CUDA_VISIBLE_DEVICES: {cuda_env}')

# PyTorch GPU detection
print(f'TORCH_VERSION: {torch.__version__}')
print(f'CUDA_AVAILABLE: {torch.cuda.is_available()}')
print(f'GPU_COUNT: {torch.cuda.device_count()}')

if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        print(f'GPU_{i}: {props.name} ({props.total_memory // 1024**3}GB)')
else:
    print('NO_CUDA_DETECTED')
"""
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
           universal_newlines=True, timeout=60)
        
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    
    result = detect_all_gpus()
    
    # Verify the command executed successfully
    assert result["returncode"] == 0, f"GPU detection failed: {result['stderr']}"
    
    stdout = result["stdout"]
    print(f"GPU Detection Output:\n{stdout}")
    
    # Parse GPU count
    gpu_count = None
    for line in stdout.split('\n'):
        if line.startswith('GPU_COUNT: '):
            gpu_count = int(line.split(': ', 1)[1])
            break
    
    # Verify we detected exactly 8 GPUs
    assert gpu_count is not None, "Could not parse GPU count from output"
    assert gpu_count == 8, f"Expected 8 GPUs, detected {gpu_count}"
    
    # Verify CUDA is available
    assert "CUDA_AVAILABLE: True" in stdout, "CUDA not available on tensor01"
    
    # Verify we can see individual GPU details
    gpu_lines = [line for line in stdout.split('\n') if line.startswith('GPU_')]
    assert len(gpu_lines) >= 8, f"Expected at least 8 GPU detail lines, got {len(gpu_lines)}"
    
    print(f"✅ Successfully detected {gpu_count} GPUs on tensor01")


@pytest.mark.dartmouth_network  
@pytest.mark.real_world
def test_tensor01_gpu_accessibility(tensor01_credentials):
    """Test that we can actually access and use the GPUs."""
    
    load_config("tensor01_config.yml")
    
    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,
    )
    
    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def test_gpu_access():
        """Test actual GPU tensor operations."""
        import subprocess
        
        result = subprocess.run([
            "python", "-c", """
import torch

print(f'GPU_COUNT: {torch.cuda.device_count()}')

if torch.cuda.device_count() >= 2:
    # Test accessing multiple GPUs
    device0 = torch.device('cuda:0')
    device1 = torch.device('cuda:1')
    
    # Create tensors on different GPUs
    x0 = torch.randn(100, 100, device=device0)
    x1 = torch.randn(100, 100, device=device1)
    
    # Perform operations
    y0 = torch.mm(x0, x0.t())
    y1 = torch.mm(x1, x1.t())
    
    result0 = y0.trace().item()
    result1 = y1.trace().item()
    
    print(f'GPU_0_RESULT: {result0}')
    print(f'GPU_1_RESULT: {result1}')
    print(f'GPU_ACCESS: SUCCESS')
else:
    print(f'GPU_ACCESS: INSUFFICIENT_GPUS')
"""
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
           universal_newlines=True, timeout=60)
        
        return {
            "stdout": result.stdout,
            "stderr": result.stderr, 
            "returncode": result.returncode
        }
    
    result = test_gpu_access()
    
    assert result["returncode"] == 0, f"GPU access test failed: {result['stderr']}"
    
    stdout = result["stdout"]
    print(f"GPU Access Test Output:\n{stdout}")
    
    # Verify successful GPU operations
    assert "GPU_ACCESS: SUCCESS" in stdout, "GPU access test did not complete successfully"
    assert "GPU_0_RESULT:" in stdout, "GPU 0 operation failed"
    assert "GPU_1_RESULT:" in stdout, "GPU 1 operation failed"
    
    print("✅ Successfully accessed and used multiple GPUs")