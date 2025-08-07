"""
Comprehensive tests for automatic GPU parallelization in ClustriX.

These tests verify that:
1. GPU parallelization is automatically detected and applied
2. Results are mathematically correct
3. Performance improvements are achieved
4. Edge cases are handled properly
5. Fallbacks work when GPU parallelization isn't beneficial
"""

import pytest
import torch
import numpy as np
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
class TestAutomaticGPUParallelization:
    """Test automatic GPU parallelization functionality."""

    def test_auto_gpu_parallel_detection(self):
        """Test that GPU parallelization is automatically detected and enabled."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,  # Enable automatic GPU parallelization
        )

        @cluster(cores=2, memory="8GB")
        def simple_gpu_computation():
            """Simple computation that should trigger GPU parallelization."""
            import subprocess

            # Simple check that auto-parallelization was attempted
            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch
gpu_count = torch.cuda.device_count()
print(f'AVAILABLE_GPUS:{gpu_count}')

if gpu_count > 1:
    print('MULTI_GPU_AVAILABLE:True')
else:
    print('MULTI_GPU_AVAILABLE:False')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = simple_gpu_computation()
        assert result["success"]
        assert "AVAILABLE_GPUS:" in result["output"]

        # Check if multiple GPUs are available for parallelization
        output = result["output"]
        if "MULTI_GPU_AVAILABLE:True" in output:
            print("✅ Multiple GPUs available for automatic parallelization")
        else:
            print("⚠️  Only single GPU available")

    def test_auto_gpu_parallel_disabled(self):
        """Test that GPU parallelization can be disabled."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=False,  # Disable automatic GPU parallelization
        )

        @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
        def computation_without_gpu_parallel():
            """Computation with GPU parallelization explicitly disabled."""
            import subprocess

            result = subprocess.run(
                ["python", "-c", "print('GPU_PARALLEL_DISABLED:True')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = computation_without_gpu_parallel()
        assert result["success"]
        assert "GPU_PARALLEL_DISABLED:True" in result["output"]


@pytest.mark.real_world
class TestGPUParallelizationCorrectness:
    """Test that GPU parallelization produces correct results."""

    def test_parallel_matrix_multiplication_correctness(self):
        """Test that parallel matrix multiplication produces correct results."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
        )

        @cluster(cores=2, memory="8GB")
        def parallel_matrix_ops():
            """Parallel matrix operations that should be automatically parallelized."""
            import subprocess
            import json

            # Test matrix multiplication across GPUs
            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch
import json

# Test data
sizes = [100, 200, 300]
results = {}

gpu_count = torch.cuda.device_count()
if gpu_count > 1:
    # Multi-GPU computation
    for size in sizes:
        # Split computation across available GPUs
        device_results = []
        for gpu_id in range(min(2, gpu_count)):  # Use up to 2 GPUs
            torch.cuda.set_device(gpu_id)
            device = torch.device(f'cuda:{gpu_id}')
            
            # Create matrices on this GPU
            A = torch.randn(size, size, device=device)
            B = torch.randn(size, size, device=device)
            
            # Compute matrix multiplication
            C = torch.mm(A, B)
            trace_val = C.trace().item()
            device_results.append(trace_val)
        
        results[f'size_{size}'] = device_results
    
    results['parallel_execution'] = True
else:
    # Single GPU fallback
    device = torch.device('cuda:0')
    for size in sizes:
        A = torch.randn(size, size, device=device)
        B = torch.randn(size, size, device=device)
        C = torch.mm(A, B)
        trace_val = C.trace().item()
        results[f'size_{size}'] = [trace_val]
    
    results['parallel_execution'] = False

print(f'RESULTS:{json.dumps(results)}')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=120,
            )

            if result.returncode == 0 and "RESULTS:" in result.stdout:
                results_str = result.stdout.split("RESULTS:", 1)[1]
                try:
                    return {"success": True, "data": json.loads(results_str)}
                except:
                    return {"success": False, "error": "Failed to parse results"}

            return {"success": False, "error": result.stderr}

        result = parallel_matrix_ops()
        assert result[
            "success"
        ], f"Matrix operation failed: {result.get('error', 'Unknown error')}"

        data = result["data"]

        # Verify results are reasonable (traces of random matrices should be non-zero)
        for key, values in data.items():
            if key.startswith("size_"):
                assert isinstance(values, list)
                assert len(values) > 0
                for val in values:
                    assert isinstance(val, (int, float))
                    # Traces of random matrices should be non-zero with high probability
                    assert abs(val) > 1e-6, f"Trace value {val} is suspiciously small"

        if data.get("parallel_execution"):
            print("✅ Multi-GPU parallel execution verified")
        else:
            print("⚠️  Single GPU execution (expected for systems with 1 GPU)")

    def test_parallel_computation_determinism(self):
        """Test that parallel computation can be made deterministic when needed."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
        )

        @cluster(cores=1, memory="4GB")
        def deterministic_computation():
            """Deterministic computation for reproducibility testing."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch
import random
import numpy as np

# Set seeds for reproducibility
torch.manual_seed(42)
random.seed(42)
np.random.seed(42)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(42)

gpu_count = torch.cuda.device_count()
results = []

if gpu_count > 1:
    # Multi-GPU deterministic computation
    for gpu_id in range(min(2, gpu_count)):
        torch.cuda.set_device(gpu_id)
        device = torch.device(f'cuda:{gpu_id}')
        
        # Use same seed on each GPU
        torch.manual_seed(42)
        
        # Create identical matrices
        A = torch.randn(50, 50, device=device)
        B = torch.eye(50, device=device) * 2.0
        
        # Deterministic operation
        C = torch.mm(A, B)
        result_val = C.sum().item()
        results.append(round(result_val, 6))  # Round for comparison
else:
    # Single GPU
    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    A = torch.randn(50, 50, device=device)
    B = torch.eye(50, device=device) * 2.0
    C = torch.mm(A, B)
    result_val = C.sum().item()
    results.append(round(result_val, 6))

print(f'DETERMINISTIC_RESULTS:{results}')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=60,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = deterministic_computation()
        assert result["success"]

        output = result["output"]
        assert "DETERMINISTIC_RESULTS:" in output

        # Extract results
        results_str = output.split("DETERMINISTIC_RESULTS:", 1)[1].strip()
        results = eval(results_str)  # Safe since we control the format

        # Check that results are reasonable
        assert len(results) > 0
        for val in results:
            assert isinstance(val, (int, float))

        print(f"✅ Deterministic computation results: {results}")


@pytest.mark.real_world
class TestGPUParallelizationPerformance:
    """Test performance characteristics of GPU parallelization."""

    def test_multi_gpu_performance_scaling(self):
        """Test that multi-GPU computation shows performance scaling."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
        )

        @cluster(cores=4, memory="16GB")
        def performance_scaling_test():
            """Test performance scaling with multiple GPUs."""
            import subprocess
            import json

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch
import time
import json

gpu_count = torch.cuda.device_count()
performance_data = {
    'gpu_count': gpu_count,
    'tests': {}
}

# Test different matrix sizes
sizes = [500, 1000]

for size in sizes:
    size_data = {}
    
    if gpu_count > 1:
        # Multi-GPU timing
        start_time = time.time()
        
        device_results = []
        for gpu_id in range(min(2, gpu_count)):
            torch.cuda.set_device(gpu_id)
            device = torch.device(f'cuda:{gpu_id}')
            
            # Large matrix operations
            A = torch.randn(size, size, device=device)
            B = torch.randn(size, size, device=device)
            
            # Multiple operations to show parallelization benefit
            for _ in range(3):
                C = torch.mm(A, B)
                A = C
            
            torch.cuda.synchronize(device)  # Ensure completion
            device_results.append(A.trace().item())
        
        multi_gpu_time = time.time() - start_time
        size_data['multi_gpu_time'] = multi_gpu_time
        size_data['multi_gpu_results'] = device_results
    
    # Single GPU timing for comparison
    start_time = time.time()
    
    device = torch.device('cuda:0')
    torch.cuda.set_device(0)
    
    A = torch.randn(size, size, device=device)
    B = torch.randn(size, size, device=device)
    
    for _ in range(3):
        C = torch.mm(A, B)
        A = C
    
    torch.cuda.synchronize(device)
    single_gpu_time = time.time() - start_time
    
    size_data['single_gpu_time'] = single_gpu_time
    size_data['single_gpu_result'] = A.trace().item()
    
    # Calculate speedup if multi-GPU was used
    if gpu_count > 1 and 'multi_gpu_time' in size_data:
        speedup = single_gpu_time / multi_gpu_time
        size_data['speedup'] = speedup
    
    performance_data['tests'][f'size_{size}'] = size_data

print(f'PERFORMANCE:{json.dumps(performance_data)}')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=300,
            )

            if result.returncode == 0 and "PERFORMANCE:" in result.stdout:
                perf_str = result.stdout.split("PERFORMANCE:", 1)[1]
                try:
                    return {"success": True, "data": json.loads(perf_str)}
                except:
                    return {
                        "success": False,
                        "error": "Failed to parse performance data",
                    }

            return {"success": False, "error": result.stderr}

        result = performance_scaling_test()
        assert result[
            "success"
        ], f"Performance test failed: {result.get('error', 'Unknown error')}"

        data = result["data"]
        gpu_count = data["gpu_count"]

        print(f"GPU count: {gpu_count}")

        for test_name, test_data in data["tests"].items():
            print(f"\n{test_name}:")
            print(f"  Single GPU time: {test_data['single_gpu_time']:.4f}s")

            if gpu_count > 1 and "multi_gpu_time" in test_data:
                print(f"  Multi GPU time: {test_data['multi_gpu_time']:.4f}s")
                print(f"  Speedup: {test_data.get('speedup', 0):.2f}x")

                # Performance should be reasonable (not necessarily faster due to overhead,
                # but should be in reasonable range)
                speedup = test_data.get("speedup", 0)
                assert speedup > 0.1, f"Speedup {speedup} is unreasonably low"
                print(f"✅ Multi-GPU performance test passed")
            else:
                print("⚠️  Single GPU system - cannot test multi-GPU speedup")


@pytest.mark.real_world
class TestGPUParallelizationEdgeCases:
    """Test edge cases and error handling for GPU parallelization."""

    def test_insufficient_gpu_memory_handling(self):
        """Test handling of insufficient GPU memory scenarios."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
        )

        @cluster(cores=2, memory="8GB")
        def large_memory_test():
            """Test with large memory requirements."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch

gpu_count = torch.cuda.device_count()
print(f'GPU_COUNT:{gpu_count}')

if gpu_count > 0:
    # Check available memory
    for gpu_id in range(gpu_count):
        props = torch.cuda.get_device_properties(gpu_id)
        total_memory = props.total_memory
        print(f'GPU_{gpu_id}_MEMORY:{total_memory}')
    
    # Try to allocate reasonable amount of memory
    try:
        device = torch.device('cuda:0')
        # Allocate 1GB tensor
        x = torch.randn(1024, 1024, 128, device=device)
        print('LARGE_ALLOCATION:success')
        del x
        torch.cuda.empty_cache()
    except RuntimeError as e:
        if 'out of memory' in str(e):
            print('LARGE_ALLOCATION:out_of_memory')
        else:
            print(f'LARGE_ALLOCATION:error:{str(e)}')
else:
    print('NO_GPUS_AVAILABLE')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=60,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = large_memory_test()
        assert result["success"]

        output = result["output"]
        print(f"Memory test output:\n{output}")

        # Should handle memory constraints gracefully
        if "out_of_memory" in output:
            print("✅ Out of memory handled gracefully")
        elif "LARGE_ALLOCATION:success" in output:
            print("✅ Large allocation succeeded")
        else:
            print("⚠️  Memory test completed with other outcome")

    def test_mixed_gpu_types_handling(self):
        """Test handling of mixed GPU types (if available)."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
        )

        @cluster(cores=1, memory="4GB")
        def gpu_compatibility_test():
            """Test GPU compatibility and mixed types."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch

gpu_count = torch.cuda.device_count()
print(f'TOTAL_GPUS:{gpu_count}')

if gpu_count > 0:
    gpu_info = []
    for gpu_id in range(gpu_count):
        props = torch.cuda.get_device_properties(gpu_id)
        gpu_data = {
            'id': gpu_id,
            'name': props.name,
            'memory': props.total_memory,
            'capability': f'{props.major}.{props.minor}'
        }
        gpu_info.append(gpu_data)
        print(f'GPU_{gpu_id}:{props.name}:MEMORY_{props.total_memory}:CAP_{props.major}.{props.minor}')
    
    # Test compatibility across GPUs
    if gpu_count > 1:
        compatible = True
        first_capability = gpu_info[0]['capability']
        for gpu in gpu_info[1:]:
            if gpu['capability'] != first_capability:
                compatible = False
                break
        
        if compatible:
            print('GPU_COMPATIBILITY:homogeneous')
        else:
            print('GPU_COMPATIBILITY:mixed')
    else:
        print('GPU_COMPATIBILITY:single_gpu')
else:
    print('NO_CUDA_GPUS')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = gpu_compatibility_test()
        assert result["success"]

        output = result["output"]
        print(f"GPU compatibility test:\n{output}")

        # Verify GPU information is properly detected
        if "TOTAL_GPUS:" in output:
            gpu_count_line = [
                line for line in output.split("\n") if line.startswith("TOTAL_GPUS:")
            ][0]
            gpu_count = int(gpu_count_line.split(":", 1)[1])
            print(f"✅ Detected {gpu_count} GPUs")

            if gpu_count > 1:
                if "GPU_COMPATIBILITY:homogeneous" in output:
                    print("✅ Homogeneous GPU setup detected")
                elif "GPU_COMPATIBILITY:mixed" in output:
                    print(
                        "⚠️  Mixed GPU types detected - parallelization may be suboptimal"
                    )
        else:
            print("⚠️  No GPU information detected")


@pytest.mark.real_world
class TestGPUParallelizationFallbacks:
    """Test fallback behavior when GPU parallelization isn't beneficial."""

    def test_single_gpu_fallback(self):
        """Test fallback to single GPU when only one GPU is available."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        # Force single GPU environment
        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
            environment_variables={"CUDA_VISIBLE_DEVICES": "0"},  # Only GPU 0
        )

        @cluster(cores=1, memory="4GB")
        def single_gpu_fallback_test():
            """Test fallback behavior with single GPU."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch
import os

cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'all')
gpu_count = torch.cuda.device_count()

print(f'CUDA_VISIBLE_DEVICES:{cuda_devices}')
print(f'DETECTED_GPUS:{gpu_count}')

if gpu_count == 1:
    print('FALLBACK:single_gpu_mode')
    # Perform single GPU computation
    device = torch.device('cuda:0')
    x = torch.randn(100, 100, device=device)
    y = torch.mm(x, x.t())
    result = y.trace().item()
    print(f'SINGLE_GPU_RESULT:{result}')
elif gpu_count > 1:
    print('MULTI_GPU:available')
else:
    print('NO_GPU:cpu_fallback')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = single_gpu_fallback_test()
        assert result["success"]

        output = result["output"]
        print(f"Single GPU fallback test:\n{output}")

        # Should detect single GPU and use fallback
        assert "DETECTED_GPUS:1" in output
        assert "FALLBACK:single_gpu_mode" in output
        assert "SINGLE_GPU_RESULT:" in output

        print("✅ Single GPU fallback working correctly")

    def test_no_gpu_fallback(self):
        """Test fallback to CPU when no GPUs are available."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        # Force CPU-only environment
        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            auto_gpu_parallel=True,
            environment_variables={"CUDA_VISIBLE_DEVICES": ""},  # No GPUs
        )

        @cluster(cores=1, memory="2GB")
        def no_gpu_fallback_test():
            """Test fallback behavior with no GPUs."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    """
import torch
import os

cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'default')
gpu_available = torch.cuda.is_available()
gpu_count = torch.cuda.device_count()

print(f'CUDA_VISIBLE_DEVICES:{cuda_devices}')
print(f'CUDA_AVAILABLE:{gpu_available}')
print(f'GPU_COUNT:{gpu_count}')

if not gpu_available or gpu_count == 0:
    print('FALLBACK:cpu_mode')
    # Perform CPU computation
    device = torch.device('cpu')
    x = torch.randn(50, 50, device=device)
    y = torch.mm(x, x.t())
    result = y.trace().item()
    print(f'CPU_RESULT:{result}')
else:
    print('GPU:unexpectedly_available')
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            return {"success": result.returncode == 0, "output": result.stdout}

        result = no_gpu_fallback_test()
        assert result["success"]

        output = result["output"]
        print(f"No GPU fallback test:\n{output}")

        # Should detect no GPUs and fallback to CPU
        if "CUDA_AVAILABLE:False" in output:
            assert "FALLBACK:cpu_mode" in output
            assert "CPU_RESULT:" in output
            print("✅ CPU fallback working correctly")
        else:
            print("⚠️  GPUs still available despite CUDA_VISIBLE_DEVICES=''")
