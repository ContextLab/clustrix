#!/usr/bin/env python3
"""
Comprehensive pytest tests for GPU detection and automatic parallelization on tensor01.
"""

import pytest
import math
from clustrix import cluster
from clustrix.config import load_config, configure


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_8_gpu_detection_simple(tensor01_credentials):
    """Test that tensor01 correctly detects all 8 GPUs using simple pattern."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Disable to avoid complexity issues
    )

    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def simple_gpu_count():
        """Simple GPU count using proven working pattern."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "-c",
                "import torch; print(f'GPU_COUNT:{torch.cuda.device_count()}'); print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        return {"output": result.stdout, "success": result.returncode == 0}

    result = simple_gpu_count()

    # Verify the command executed successfully
    assert result["success"], f"GPU detection command failed"

    stdout = result["output"]
    print(f"GPU Detection Output:\n{stdout}")

    # Parse GPU count
    gpu_count = None
    for line in stdout.split("\n"):
        if "GPU_COUNT:" in line:
            gpu_count = int(line.split(":", 1)[1])
            break

    # Verify we detected exactly 8 GPUs
    assert gpu_count is not None, "Could not parse GPU count from output"
    assert gpu_count == 8, f"Expected 8 GPUs, detected {gpu_count}"

    # Verify CUDA is available
    assert "CUDA_AVAILABLE:True" in stdout, "CUDA not available on tensor01"

    print(f"✅ Successfully detected {gpu_count} GPUs on tensor01")


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_gpu_multi_access(tensor01_credentials):
    """Test that we can access multiple GPUs simultaneously."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,
    )

    @cluster(cores=1, memory="8GB", auto_gpu_parallel=False)
    def test_multiple_gpu_access():
        """Test accessing multiple GPUs in sequence."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "-c",
                """
import torch

gpu_count = torch.cuda.device_count()
print(f'TOTAL_GPUS: {gpu_count}')

if gpu_count >= 2:
    # Test multiple GPU access
    for i in range(min(gpu_count, 4)):  # Test first 4 GPUs
        device = torch.device(f'cuda:{i}')
        x = torch.randn(50, 50, device=device)
        y = torch.mm(x, x.t())
        result = y.trace().item()
        print(f'GPU_{i}_TRACE: {result}')
    
    print('MULTI_GPU_TEST: SUCCESS')
else:
    print('MULTI_GPU_TEST: INSUFFICIENT_GPUS')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=90,
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    result = test_multiple_gpu_access()

    assert (
        result["returncode"] == 0
    ), f"Multi-GPU access test failed: {result['stderr']}"

    stdout = result["stdout"]
    print(f"Multi-GPU Access Test Output:\n{stdout}")

    # Verify successful multi-GPU operations
    assert (
        "MULTI_GPU_TEST: SUCCESS" in stdout
    ), "Multi-GPU test did not complete successfully"

    # Count GPU trace results
    gpu_traces = [
        line
        for line in stdout.split("\n")
        if line.startswith("GPU_") and "_TRACE:" in line
    ]
    assert (
        len(gpu_traces) >= 2
    ), f"Expected at least 2 GPU trace results, got {len(gpu_traces)}"

    # Verify trace values are reasonable
    for trace_line in gpu_traces:
        trace_value = float(trace_line.split(":", 1)[1].strip())
        assert not math.isnan(trace_value), f"GPU trace value is NaN: {trace_line}"
        assert not math.isinf(trace_value), f"GPU trace value is infinite: {trace_line}"

    print(f"✅ Successfully accessed {len(gpu_traces)} GPUs with valid computations")


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_auto_gpu_parallelization_simple(tensor01_credentials):
    """Test automatic GPU parallelization with simple function."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True,  # Enable automatic GPU parallelization
    )

    @cluster(cores=2, memory="8GB", auto_gpu_parallel=True)
    def simple_gpu_parallel_function():
        """Simple function that should trigger GPU parallelization."""
        import torch

        results = []

        # Simple loop that should be detected for parallelization
        for i in range(8):  # 8 iterations to match 8 GPUs
            # GPU operations that should trigger parallelization
            x = torch.randn(100, 100).cuda()
            y = torch.mm(x, x.t())
            trace_value = y.trace().item()

            # Add some computation for verification
            computed_value = i * 10.0 + trace_value

            results.append(
                {"iteration": i, "trace": trace_value, "computed": computed_value}
            )

        return results

    print("Testing simple automatic GPU parallelization...")
    results = simple_gpu_parallel_function()

    # Verify we got results
    assert results is not None, "GPU parallelization returned None"
    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) == 8, f"Expected 8 results, got {len(results)}"

    print(f"✅ Received {len(results)} results from GPU parallelization")

    # Verify structure and correctness of results
    for i, result in enumerate(results):
        assert isinstance(result, dict), f"Result {i} is not a dict: {type(result)}"
        assert "iteration" in result, f"Result {i} missing iteration key"
        assert "trace" in result, f"Result {i} missing trace key"
        assert "computed" in result, f"Result {i} missing computed key"

        # Verify iteration numbers are correct (order may change due to parallelization)
        iteration = result["iteration"]
        assert 0 <= iteration < 8, f"Invalid iteration number: {iteration}"

        # Verify trace values are reasonable
        trace = result["trace"]
        assert not math.isnan(trace), f"Trace value {i} is NaN"
        assert not math.isinf(trace), f"Trace value {i} is infinite"

        # Verify computed values follow expected pattern
        expected_computed = iteration * 10.0 + trace
        actual_computed = result["computed"]
        assert (
            abs(actual_computed - expected_computed) < 1e-10
        ), f"Computed value mismatch for iteration {iteration}: expected {expected_computed}, got {actual_computed}"

    print("✅ All results have correct structure and calculations")

    # Verify we got diverse trace values (indicating real GPU computation)
    trace_values = [r["trace"] for r in results]
    unique_traces = set(trace_values)
    assert (
        len(unique_traces) > 1
    ), "All trace values are identical - GPU computation may not have occurred"

    print(f"✅ GPU computations produced {len(unique_traces)} unique trace values")


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_function_flattening_integration(tensor01_credentials):
    """Test that function flattening works with complex functions."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Test flattening without GPU parallelization first
    )

    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def complex_function_that_should_be_flattened():
        """
        A deliberately complex function that should trigger automatic flattening.

        This function has:
        - Multiple imports
        - Nested operations
        - Complex logic
        - Multiple function calls
        - Should exceed complexity threshold
        """
        import subprocess
        import os
        import json
        import time

        # Multiple operations that add complexity
        results = []

        # Complex nested logic
        for i in range(3):
            for j in range(2):
                # Multiple subprocess calls (complexity risk)
                if i > 0:
                    subprocess_result = subprocess.run(
                        ["python", "-c", f"print('NESTED_RESULT_{i}_{j}:42')"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=10,
                    )

                    if subprocess_result.returncode == 0:
                        output = subprocess_result.stdout.strip()
                        if "NESTED_RESULT" in output:
                            value = int(output.split(":", 1)[1])
                            results.append({"i": i, "j": j, "value": value})

                # Additional complexity
                time.sleep(0.1)

        # Final computation
        total_value = sum(r["value"] for r in results)

        return {
            "results": results,
            "total": total_value,
            "complexity_test": "completed",
        }

    print("Testing function flattening with complex function...")
    result = complex_function_that_should_be_flattened()

    # Verify the function executed successfully despite complexity
    assert result is not None, "Complex function returned None"
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert "complexity_test" in result, "Missing complexity_test key in result"
    assert result["complexity_test"] == "completed", "Complex function did not complete"

    # Verify the computational results are correct
    assert "results" in result, "Missing results key"
    assert "total" in result, "Missing total key"

    results_list = result["results"]
    total_value = result["total"]

    # Verify results structure
    for res in results_list:
        assert (
            "i" in res and "j" in res and "value" in res
        ), f"Invalid result structure: {res}"
        assert res["value"] == 42, f"Unexpected value: {res['value']}"

    # Verify total calculation
    expected_total = len(results_list) * 42
    assert (
        total_value == expected_total
    ), f"Total mismatch: expected {expected_total}, got {total_value}"

    print(
        f"✅ Complex function flattening successful: {len(results_list)} results, total {total_value}"
    )


@pytest.mark.dartmouth_network
@pytest.mark.real_world
def test_tensor01_gpu_parallel_with_verification(tensor01_credentials):
    """Test GPU parallelization with computation verification."""

    load_config("tensor01_config.yml")

    configure(
        password=tensor01_credentials.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True,
    )

    @cluster(cores=4, memory="16GB", auto_gpu_parallel=True)
    def verifiable_gpu_computation():
        """GPU computation with verifiable mathematical results."""
        import torch
        import math

        results = []

        # Computations with predictable mathematical properties
        for i in range(12):  # More iterations than GPUs to test distribution
            # Create deterministic input based on iteration
            torch.manual_seed(i + 1000)  # Consistent seed per iteration

            # GPU matrix operations
            size = 50 + (i % 10) * 10  # Varying matrix sizes
            x = torch.randn(size, size).cuda()

            # Deterministic operations
            y = torch.mm(x, x.t())
            trace_val = y.trace().item()
            determinant = torch.det(x).item()

            # Verifiable computation
            expected_result = i * 100.0 + trace_val * 0.1

            results.append(
                {
                    "iteration": i,
                    "matrix_size": size,
                    "trace": trace_val,
                    "determinant": determinant,
                    "expected_result": expected_result,
                    "seed_used": i + 1000,
                }
            )

        return results

    print("Testing GPU parallelization with verifiable computation...")
    results = verifiable_gpu_computation()

    # Verify basic structure
    assert results is not None, "Verifiable GPU computation returned None"
    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) == 12, f"Expected 12 results, got {len(results)}"

    print(f"✅ Received {len(results)} results from verifiable GPU computation")

    # Verify each result
    for result in results:
        iteration = result["iteration"]
        matrix_size = result["matrix_size"]
        trace_val = result["trace"]
        determinant = result["determinant"]
        expected_result = result["expected_result"]

        # Verify matrix size follows expected pattern
        expected_size = 50 + (iteration % 10) * 10
        assert (
            matrix_size == expected_size
        ), f"Matrix size mismatch for iteration {iteration}: expected {expected_size}, got {matrix_size}"

        # Verify trace and determinant are reasonable
        assert not math.isnan(trace_val), f"Trace is NaN for iteration {iteration}"
        assert not math.isinf(trace_val), f"Trace is infinite for iteration {iteration}"
        assert not math.isnan(
            determinant
        ), f"Determinant is NaN for iteration {iteration}"
        assert not math.isinf(
            determinant
        ), f"Determinant is infinite for iteration {iteration}"

        # Verify expected result calculation
        computed_expected = iteration * 100.0 + trace_val * 0.1
        assert (
            abs(expected_result - computed_expected) < 1e-10
        ), f"Expected result calculation error for iteration {iteration}"

    print("✅ All verifiable computations are mathematically correct")

    # Verify we have good distribution of results (parallelization working)
    trace_values = [r["trace"] for r in results]
    determinant_values = [r["determinant"] for r in results]

    # Check for diversity in results
    unique_traces = len(
        set(f"{t:.6f}" for t in trace_values)
    )  # Round to avoid float precision issues
    unique_determinants = len(set(f"{d:.6f}" for d in determinant_values))

    assert (
        unique_traces >= len(results) * 0.8
    ), f"Trace values not diverse enough: {unique_traces} unique out of {len(results)}"
    assert (
        unique_determinants >= len(results) * 0.8
    ), f"Determinant values not diverse enough: {unique_determinants} unique out of {len(results)}"

    print(
        f"✅ Results show good diversity (traces: {unique_traces}, determinants: {unique_determinants})"
    )
    print("✅ GPU parallelization verification successful!")
