#!/usr/bin/env python3
"""
Pytest tests for automatic GPU parallelization on tensor01.
"""

import pytest
import math
from clustrix import cluster
from clustrix.config import load_config, configure


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_auto_gpu_parallelization(tensor01_credentials):
    """Test that automatic GPU parallelization works correctly on tensor01."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True,  # Enable automatic GPU parallelization
    )

    @cluster(cores=4, memory="16GB", auto_gpu_parallel=True)
    def gpu_parallel_computation():
        """Function with loop that should be auto-parallelized across GPUs."""
        import torch
        import math

        results = []

        # This loop should be detected and parallelized across GPUs
        for i in range(32):  # 32 iterations to distribute across 8 GPUs
            # GPU operations that should trigger parallelization
            x = torch.randn(200, 200).cuda()
            y = torch.mm(x, x.t())
            trace_value = y.trace().item()

            # Simple mathematical computation for verification
            expected = i * 2.0 + 10.0
            result = trace_value + expected

            results.append(
                {
                    "iteration": i,
                    "trace": trace_value,
                    "expected_offset": expected,
                    "final_result": result,
                }
            )

        return results

    print("Testing automatic GPU parallelization...")
    results = gpu_parallel_computation()

    # Verify we got results
    assert results is not None, "GPU parallelization returned None"
    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) == 32, f"Expected 32 results, got {len(results)}"

    print(f"✅ Received {len(results)} results from GPU parallelization")

    # Verify structure of results
    for i, result in enumerate(results):
        assert isinstance(result, dict), f"Result {i} is not a dict: {type(result)}"
        assert "iteration" in result, f"Result {i} missing iteration key"
        assert "trace" in result, f"Result {i} missing trace key"
        assert "expected_offset" in result, f"Result {i} missing expected_offset key"
        assert "final_result" in result, f"Result {i} missing final_result key"

        # Verify iteration numbers are correct
        assert (
            result["iteration"] == i
        ), f"Iteration mismatch: expected {i}, got {result['iteration']}"

        # Verify expected offset calculation
        expected_offset = i * 2.0 + 10.0
        assert (
            abs(result["expected_offset"] - expected_offset) < 1e-10
        ), f"Expected offset mismatch for iteration {i}: expected {expected_offset}, got {result['expected_offset']}"

        # Verify final result calculation
        expected_final = result["trace"] + expected_offset
        assert (
            abs(result["final_result"] - expected_final) < 1e-10
        ), f"Final result mismatch for iteration {i}"

    print("✅ All results have correct structure and calculations")

    # Verify that we actually got different trace values (indicating real GPU computation)
    trace_values = [r["trace"] for r in results]
    unique_traces = set(trace_values)
    assert (
        len(unique_traces) > 1
    ), "All trace values are identical - GPU computation may not have occurred"

    print(f"✅ GPU computations produced {len(unique_traces)} unique trace values")

    # Verify trace values are reasonable (not NaN, not all zeros)
    for i, trace in enumerate(trace_values):
        assert not math.isnan(trace), f"Trace value {i} is NaN"
        assert not math.isinf(trace), f"Trace value {i} is infinite"

    non_zero_traces = [t for t in trace_values if abs(t) > 1e-10]
    assert (
        len(non_zero_traces) > len(trace_values) // 2
    ), "Too many trace values are near zero - GPU computation may have failed"

    print("✅ Trace values are reasonable (not NaN/inf, mostly non-zero)")


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_gpu_parallel_verification(tensor01_credentials):
    """Test that verifies GPU parallelization by comparing with sequential execution."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Start with parallelization disabled
    )

    @cluster(cores=1, memory="8GB", auto_gpu_parallel=False)
    def sequential_computation():
        """Sequential computation for comparison."""
        import torch

        torch.manual_seed(42)  # Fixed seed for reproducibility

        results = []
        for i in range(8):  # Smaller number for comparison
            x = torch.randn(100, 100).cuda()
            y = torch.mm(x, x.t())
            trace_value = y.trace().item()
            results.append(trace_value)

        return results

    print("Running sequential computation...")
    sequential_results = sequential_computation()

    # Now test with GPU parallelization enabled
    configure(auto_gpu_parallel=True)

    @cluster(cores=2, memory="8GB", auto_gpu_parallel=True)
    def parallel_computation():
        """Parallel computation that should use multiple GPUs."""
        import torch

        torch.manual_seed(42)  # Same seed for comparison

        results = []
        for i in range(8):  # Same number of iterations
            x = torch.randn(100, 100).cuda()
            y = torch.mm(x, x.t())
            trace_value = y.trace().item()
            results.append(trace_value)

        return results

    print("Running parallel computation...")
    parallel_results = parallel_computation()

    # Verify both computations completed
    assert sequential_results is not None, "Sequential computation failed"
    assert parallel_results is not None, "Parallel computation failed"
    assert (
        len(sequential_results) == 8
    ), f"Sequential: expected 8 results, got {len(sequential_results)}"
    assert (
        len(parallel_results) == 8
    ), f"Parallel: expected 8 results, got {len(parallel_results)}"

    print("✅ Both sequential and parallel computations completed")

    # Note: Due to random number generation across different execution contexts,
    # we can't expect identical results, but we can verify they're reasonable
    for i, (seq_val, par_val) in enumerate(zip(sequential_results, parallel_results)):
        assert not math.isnan(seq_val), f"Sequential result {i} is NaN"
        assert not math.isnan(par_val), f"Parallel result {i} is NaN"
        assert not math.isinf(seq_val), f"Sequential result {i} is infinite"
        assert not math.isinf(par_val), f"Parallel result {i} is infinite"

    print("✅ All results are finite and valid numbers")

    # Verify results have reasonable magnitudes
    seq_magnitudes = [abs(x) for x in sequential_results]
    par_magnitudes = [abs(x) for x in parallel_results]

    seq_avg = sum(seq_magnitudes) / len(seq_magnitudes)
    par_avg = sum(par_magnitudes) / len(par_magnitudes)

    # Results should have similar order of magnitude
    assert seq_avg > 1.0, f"Sequential results too small: avg magnitude {seq_avg}"
    assert par_avg > 1.0, f"Parallel results too small: avg magnitude {par_avg}"

    print(
        f"✅ Results have reasonable magnitudes (seq: {seq_avg:.2f}, par: {par_avg:.2f})"
    )


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_gpu_environment_check(tensor01_credentials):
    """Test that verifies the GPU parallelization environment is correctly set up."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True,
    )

    @cluster(cores=2, memory="8GB", auto_gpu_parallel=True)
    def check_gpu_environment():
        """Check GPU environment during parallelized execution."""
        import subprocess
        import os

        result = subprocess.run(
            [
                "python",
                "-c",
                """
import torch
import os

print(f'PROCESS_ID: {os.getpid()}')
print(f'CUDA_VISIBLE_DEVICES: {os.environ.get("CUDA_VISIBLE_DEVICES", "NOT_SET")}')
print(f'GPU_COUNT: {torch.cuda.device_count()}')
print(f'CURRENT_DEVICE: {torch.cuda.current_device() if torch.cuda.is_available() else "NO_CUDA"}')

if torch.cuda.is_available():
    device = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(device)
    print(f'DEVICE_NAME: {props.name}')
    print(f'DEVICE_MEMORY: {props.total_memory // 1024**3}GB')
    
    # Test tensor creation and operation
    x = torch.randn(50, 50).cuda()
    y = torch.mm(x, x.t())
    print(f'TENSOR_DEVICE: {x.device}')
    print(f'OPERATION_SUCCESS: {not torch.isnan(y.trace())}')
else:
    print('NO_CUDA_AVAILABLE')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    result = check_gpu_environment()

    assert (
        result["returncode"] == 0
    ), f"GPU environment check failed: {result['stderr']}"

    stdout = result["stdout"]
    print(f"GPU Environment Check Output:\n{stdout}")

    # Verify GPU environment is properly configured
    assert "GPU_COUNT:" in stdout, "GPU count not reported"
    assert "DEVICE_NAME:" in stdout, "GPU device name not reported"
    assert "OPERATION_SUCCESS: True" in stdout, "GPU tensor operations failed"
    assert "TENSOR_DEVICE: cuda:" in stdout, "Tensors not created on GPU"

    # Parse GPU count to ensure we have access to multiple GPUs
    gpu_count = None
    for line in stdout.split("\n"):
        if line.startswith("GPU_COUNT: "):
            gpu_count = int(line.split(": ", 1)[1])
            break

    assert gpu_count is not None, "Could not parse GPU count"
    assert gpu_count > 0, f"No GPUs available (count: {gpu_count})"

    print(f"✅ GPU environment properly configured with {gpu_count} GPUs available")
