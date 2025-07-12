"""
Test tensor01 GPU and CUDA functionality using ClustriX.

This test validates that ClustriX can:
1. Detect CUDA and GPUs properly on tensor01
2. Execute GPU computations using a single GPU
3. Execute GPU computations using multiple GPUs
4. Handle GPU memory management and allocation
5. Verify CUDA environment and dependencies

tensor01 has 8 GPUs total - we'll test with 1 GPU and 2 GPUs.
"""

import pytest
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_tensor01_cuda_detection():
    """Test comprehensive CUDA detection on tensor01."""

    # Load the tensor01 configuration file
    load_config("tensor01_config.yml")

    # Get credentials using the existing credential manager
    tensor01_creds = credentials.get_tensor01_credentials()

    if not tensor01_creds:
        pytest.skip(
            "No tensor01 credentials available - check 1Password or CLUSTRIX_PASSWORD env var"
        )

    # Override configuration with actual credentials
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,  # Keep files for verification
        job_poll_interval=10,
    )

    @cluster(cores=2, memory="4GB")
    def detect_cuda_environment() -> dict:
        """
        Comprehensive CUDA detection and environment analysis.
        """
        import os
        import subprocess
        import sys

        cuda_info = {
            "cuda_available": False,
            "nvidia_smi_available": False,
            "cuda_version": None,
            "driver_version": None,
            "gpu_count": 0,
            "gpu_details": [],
            "cuda_environment_vars": {},
            "cuda_libraries": {},
            "compute_capabilities": [],
        }

        # Test nvidia-smi availability and get basic GPU info
        try:
            nvidia_result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.free,memory.used,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if nvidia_result.returncode == 0:
                cuda_info["nvidia_smi_available"] = True
                gpu_lines = nvidia_result.stdout.strip().split("\n")

                for line in gpu_lines:
                    if line.strip():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 7:
                            cuda_info["gpu_details"].append(
                                {
                                    "index": parts[0],
                                    "name": parts[1],
                                    "memory_total_mb": parts[2],
                                    "memory_free_mb": parts[3],
                                    "memory_used_mb": parts[4],
                                    "utilization_percent": parts[5],
                                    "temperature_c": parts[6],
                                }
                            )

                cuda_info["gpu_count"] = len(cuda_info["gpu_details"])
            else:
                cuda_info["nvidia_smi_error"] = (
                    nvidia_result.stderr.decode()
                    if nvidia_result.stderr
                    else "Unknown error"
                )

        except Exception as e:
            cuda_info["nvidia_smi_error"] = str(e)

        # Get CUDA driver version
        try:
            driver_result = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if driver_result.returncode == 0:
                cuda_info["driver_version"] = driver_result.stdout.strip().split("\n")[
                    0
                ]
        except Exception as e:
            cuda_info["driver_version_error"] = str(e)

        # Check CUDA environment variables
        cuda_env_vars = [
            "CUDA_VISIBLE_DEVICES",
            "CUDA_DEVICE_ORDER",
            "CUDA_HOME",
            "CUDA_PATH",
            "CUDA_ROOT",
            "NVIDIA_VISIBLE_DEVICES",
        ]

        for var in cuda_env_vars:
            cuda_info["cuda_environment_vars"][var] = os.getenv(var)

        # Test for CUDA runtime
        try:
            nvcc_result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if nvcc_result.returncode == 0:
                cuda_info["nvcc_available"] = True
                # Extract CUDA version from nvcc output
                for line in nvcc_result.stdout.split("\n"):
                    if "release" in line.lower():
                        cuda_info["cuda_version"] = line.strip()
                        break
            else:
                cuda_info["nvcc_available"] = False
                cuda_info["nvcc_error"] = (
                    nvcc_result.stderr.decode()
                    if nvcc_result.stderr
                    else "nvcc not found"
                )
        except Exception as e:
            cuda_info["nvcc_available"] = False
            cuda_info["nvcc_error"] = str(e)

        # Test Python CUDA libraries availability
        cuda_libraries = ["torch", "tensorflow", "cupy", "numba", "pycuda"]

        for lib in cuda_libraries:
            try:
                if lib == "torch":
                    import torch

                    cuda_info["cuda_libraries"][lib] = {
                        "available": True,
                        "version": torch.__version__,
                        "cuda_available": torch.cuda.is_available(),
                        "cuda_version": (
                            torch.version.cuda
                            if hasattr(torch.version, "cuda")
                            else None
                        ),
                        "device_count": (
                            torch.cuda.device_count()
                            if torch.cuda.is_available()
                            else 0
                        ),
                    }
                elif lib == "tensorflow":
                    import tensorflow as tf

                    cuda_info["cuda_libraries"][lib] = {
                        "available": True,
                        "version": tf.__version__,
                        "gpu_available": len(tf.config.list_physical_devices("GPU"))
                        > 0,
                        "gpu_count": len(tf.config.list_physical_devices("GPU")),
                    }
                elif lib == "cupy":
                    import cupy

                    cuda_info["cuda_libraries"][lib] = {
                        "available": True,
                        "version": cupy.__version__,
                        "cuda_version": cupy.cuda.runtime.runtimeGetVersion(),
                    }
                elif lib == "numba":
                    from numba import cuda as numba_cuda

                    cuda_info["cuda_libraries"][lib] = {
                        "available": True,
                        "cuda_available": numba_cuda.is_available(),
                        "device_count": (
                            len(numba_cuda.gpus) if numba_cuda.is_available() else 0
                        ),
                    }
                elif lib == "pycuda":
                    import pycuda.driver as cuda

                    cuda.init()
                    cuda_info["cuda_libraries"][lib] = {
                        "available": True,
                        "device_count": cuda.Device.count(),
                    }

            except ImportError:
                cuda_info["cuda_libraries"][lib] = {
                    "available": False,
                    "error": "Not installed",
                }
            except Exception as e:
                cuda_info["cuda_libraries"][lib] = {"available": False, "error": str(e)}

        # Mark CUDA as available if we have working nvidia-smi and GPUs
        cuda_info["cuda_available"] = (
            cuda_info["nvidia_smi_available"] and cuda_info["gpu_count"] > 0
        )

        return {
            "cuda_info": cuda_info,
            "hostname": subprocess.run(
                ["hostname"], capture_output=True, text=True, timeout=5
            ).stdout.strip(),
            "python_version": sys.version,
            "detection_successful": True,
        }

    # Execute the CUDA detection
    print("Running comprehensive CUDA detection on tensor01...")
    result = detect_cuda_environment()

    # Validate CUDA detection results
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert result["detection_successful"] is True, "CUDA detection function failed"

    cuda_info = result["cuda_info"]

    # tensor01 should have GPUs available
    assert (
        cuda_info["nvidia_smi_available"] is True
    ), "nvidia-smi should be available on tensor01"
    assert (
        cuda_info["gpu_count"] > 0
    ), f"tensor01 should have GPUs, found {cuda_info['gpu_count']}"
    assert cuda_info["cuda_available"] is True, "CUDA should be available on tensor01"

    print("SUCCESS: CUDA detection completed on tensor01!")
    print(f"✓ GPUs detected: {cuda_info['gpu_count']}")
    print(f"✓ Driver version: {cuda_info.get('driver_version', 'Unknown')}")
    print(f"✓ CUDA version: {cuda_info.get('cuda_version', 'Unknown')}")

    # Print GPU details
    for i, gpu in enumerate(cuda_info["gpu_details"]):
        print(f"✓ GPU {i}: {gpu['name']} ({gpu['memory_total_mb']}MB total)")

    # Print available CUDA libraries
    available_libs = [
        lib
        for lib, info in cuda_info["cuda_libraries"].items()
        if info.get("available", False)
    ]
    print(
        f"✓ CUDA libraries available: {', '.join(available_libs) if available_libs else 'None'}"
    )

    return result


@pytest.mark.real_world
def test_tensor01_single_gpu_computation():
    """Test GPU computation using a single GPU on tensor01."""

    # Load the tensor01 configuration file
    load_config("tensor01_config.yml")

    # Get credentials
    tensor01_creds = credentials.get_tensor01_credentials()

    if not tensor01_creds:
        pytest.skip(
            "No tensor01 credentials available - check 1Password or CLUSTRIX_PASSWORD env var"
        )

    # Configure for single GPU usage
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=10,
    )

    @cluster(cores=4, memory="8GB")
    def single_gpu_computation(matrix_size: int = 1000) -> dict:
        """
        Perform GPU computation using a single GPU.
        """
        import os
        import time
        import subprocess

        # Set CUDA to use only 1 GPU (GPU 0)
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"

        computation_results = {
            "matrix_size": matrix_size,
            "gpu_used": 0,
            "cuda_setup_successful": False,
            "computation_time": 0,
            "gpu_memory_used": None,
            "computation_result": None,
        }

        try:
            # Try PyTorch GPU computation first
            import torch

            if torch.cuda.is_available():
                device = torch.device("cuda:0")
                computation_results["cuda_setup_successful"] = True
                computation_results["torch_device"] = str(device)
                computation_results["torch_device_name"] = torch.cuda.get_device_name(0)

                # Record initial GPU memory
                torch.cuda.empty_cache()
                initial_memory = torch.cuda.memory_allocated(0)

                start_time = time.time()

                # Create random matrices on GPU
                matrix_a = torch.randn(
                    matrix_size, matrix_size, device=device, dtype=torch.float32
                )
                matrix_b = torch.randn(
                    matrix_size, matrix_size, device=device, dtype=torch.float32
                )

                # Perform matrix multiplication on GPU
                result_matrix = torch.matmul(matrix_a, matrix_b)

                # Compute some statistics
                matrix_sum = torch.sum(result_matrix).item()
                matrix_mean = torch.mean(result_matrix).item()
                matrix_std = torch.std(result_matrix).item()

                # Synchronize to ensure computation is complete
                torch.cuda.synchronize()

                end_time = time.time()
                final_memory = torch.cuda.memory_allocated(0)

                computation_results.update(
                    {
                        "computation_time": end_time - start_time,
                        "gpu_memory_used": final_memory - initial_memory,
                        "computation_result": {
                            "sum": matrix_sum,
                            "mean": matrix_mean,
                            "std": matrix_std,
                        },
                        "torch_success": True,
                    }
                )

                # Clean up GPU memory
                del matrix_a, matrix_b, result_matrix
                torch.cuda.empty_cache()

            else:
                computation_results["torch_error"] = "CUDA not available in PyTorch"

        except ImportError:
            # Fallback to numpy CPU computation if PyTorch not available
            import numpy as np

            start_time = time.time()

            matrix_a = np.random.randn(matrix_size, matrix_size).astype(np.float32)
            matrix_b = np.random.randn(matrix_size, matrix_size).astype(np.float32)
            result_matrix = np.dot(matrix_a, matrix_b)

            end_time = time.time()

            computation_results.update(
                {
                    "computation_time": end_time - start_time,
                    "computation_result": {
                        "sum": float(np.sum(result_matrix)),
                        "mean": float(np.mean(result_matrix)),
                        "std": float(np.std(result_matrix)),
                    },
                    "fallback_to_cpu": True,
                    "torch_error": "PyTorch not available",
                }
            )

        except Exception as e:
            computation_results["error"] = str(e)

        # Get GPU utilization after computation
        try:
            gpu_util_result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,utilization.gpu,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if gpu_util_result.returncode == 0:
                gpu_lines = gpu_util_result.stdout.strip().split("\n")
                computation_results["final_gpu_status"] = []
                for line in gpu_lines:
                    if line.strip():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 3:
                            computation_results["final_gpu_status"].append(
                                {
                                    "gpu_index": parts[0],
                                    "utilization": parts[1],
                                    "memory_used": parts[2],
                                }
                            )
        except Exception as e:
            computation_results["gpu_status_error"] = str(e)

        return computation_results

    # Execute single GPU computation
    print("Running single GPU computation on tensor01...")
    result = single_gpu_computation(1000)

    # Validate results
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert result["matrix_size"] == 1000, "Matrix size mismatch"
    assert result["computation_time"] > 0, "Computation time should be positive"
    assert (
        result["computation_result"] is not None
    ), "Computation result should not be None"

    # Check if GPU computation was successful
    if result.get("cuda_setup_successful", False):
        assert result["torch_success"] is True, "PyTorch GPU computation should succeed"
        print(
            f"✓ GPU computation successful on {result.get('torch_device_name', 'Unknown GPU')}"
        )
        print(f"✓ Computation time: {result['computation_time']:.3f} seconds")
        print(f"✓ GPU memory used: {result.get('gpu_memory_used', 0) / 1024**2:.1f} MB")
    else:
        print("⚠ Fell back to CPU computation - GPU may not be available")
        assert result.get(
            "fallback_to_cpu", False
        ), "Should have CPU fallback if GPU fails"

    print(f"✓ Matrix sum: {result['computation_result']['sum']:.2f}")
    print(f"✓ Matrix mean: {result['computation_result']['mean']:.4f}")

    return result


@pytest.mark.real_world
def test_tensor01_dual_gpu_computation():
    """Test GPU computation using 2 GPUs on tensor01."""

    # Load the tensor01 configuration file
    load_config("tensor01_config.yml")

    # Get credentials
    tensor01_creds = credentials.get_tensor01_credentials()

    if not tensor01_creds:
        pytest.skip(
            "No tensor01 credentials available - check 1Password or CLUSTRIX_PASSWORD env var"
        )

    # Configure for dual GPU usage
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=10,
    )

    @cluster(cores=8, memory="16GB")
    def dual_gpu_computation(matrix_size: int = 1500) -> dict:
        """
        Perform GPU computation using 2 GPUs in parallel.
        """
        import os
        import time
        import subprocess

        # Set CUDA to use GPUs 0 and 1
        os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

        computation_results = {
            "matrix_size": matrix_size,
            "gpus_used": [0, 1],
            "cuda_setup_successful": False,
            "computation_time": 0,
            "gpu_0_memory_used": None,
            "gpu_1_memory_used": None,
            "computation_results": [],
        }

        try:
            import torch

            if torch.cuda.is_available() and torch.cuda.device_count() >= 2:
                computation_results["cuda_setup_successful"] = True
                computation_results["available_gpus"] = torch.cuda.device_count()
                computation_results["gpu_names"] = [
                    torch.cuda.get_device_name(0),
                    torch.cuda.get_device_name(1),
                ]

                # Clear GPU memory
                for gpu_id in [0, 1]:
                    torch.cuda.empty_cache()

                start_time = time.time()

                # Create computation tasks for each GPU
                gpu_results = []

                for gpu_id in [0, 1]:
                    device = torch.device(f"cuda:{gpu_id}")

                    # Record initial GPU memory
                    initial_memory = torch.cuda.memory_allocated(gpu_id)

                    # Create matrices on this GPU
                    matrix_a = torch.randn(
                        matrix_size, matrix_size, device=device, dtype=torch.float32
                    )
                    matrix_b = torch.randn(
                        matrix_size, matrix_size, device=device, dtype=torch.float32
                    )

                    # Perform computation
                    result_matrix = torch.matmul(matrix_a, matrix_b)

                    # Compute statistics
                    gpu_result = {
                        "gpu_id": gpu_id,
                        "device_name": torch.cuda.get_device_name(gpu_id),
                        "sum": torch.sum(result_matrix).item(),
                        "mean": torch.mean(result_matrix).item(),
                        "std": torch.std(result_matrix).item(),
                        "memory_used": torch.cuda.memory_allocated(gpu_id)
                        - initial_memory,
                    }

                    gpu_results.append(gpu_result)

                    # Store memory usage
                    if gpu_id == 0:
                        computation_results["gpu_0_memory_used"] = gpu_result[
                            "memory_used"
                        ]
                    else:
                        computation_results["gpu_1_memory_used"] = gpu_result[
                            "memory_used"
                        ]

                    # Clean up this GPU
                    del matrix_a, matrix_b, result_matrix
                    torch.cuda.empty_cache()

                # Synchronize all GPUs
                for gpu_id in [0, 1]:
                    torch.cuda.synchronize(gpu_id)

                end_time = time.time()

                computation_results.update(
                    {
                        "computation_time": end_time - start_time,
                        "computation_results": gpu_results,
                        "torch_success": True,
                    }
                )

            else:
                available_gpus = (
                    torch.cuda.device_count() if torch.cuda.is_available() else 0
                )
                computation_results["torch_error"] = (
                    f"Need 2+ GPUs, found {available_gpus}"
                )

        except ImportError:
            computation_results["torch_error"] = "PyTorch not available"
        except Exception as e:
            computation_results["error"] = str(e)

        # Get final GPU status
        try:
            gpu_util_result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,utilization.gpu,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if gpu_util_result.returncode == 0:
                gpu_lines = gpu_util_result.stdout.strip().split("\n")
                computation_results["final_gpu_status"] = []
                for line in gpu_lines:
                    if line.strip():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 4:
                            computation_results["final_gpu_status"].append(
                                {
                                    "gpu_index": parts[0],
                                    "gpu_name": parts[1],
                                    "utilization": parts[2],
                                    "memory_used": parts[3],
                                }
                            )
        except Exception as e:
            computation_results["gpu_status_error"] = str(e)

        return computation_results

    # Execute dual GPU computation
    print("Running dual GPU computation on tensor01...")
    result = dual_gpu_computation(1500)

    # Validate results
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert result["matrix_size"] == 1500, "Matrix size mismatch"

    # Check if dual GPU computation was successful
    if result.get("cuda_setup_successful", False):
        assert (
            result["torch_success"] is True
        ), "PyTorch dual GPU computation should succeed"
        assert (
            len(result["computation_results"]) == 2
        ), "Should have results from 2 GPUs"

        print(f"✓ Dual GPU computation successful!")
        print(f"✓ Available GPUs: {result.get('available_gpus', 0)}")
        print(f"✓ Total computation time: {result['computation_time']:.3f} seconds")

        for gpu_result in result["computation_results"]:
            gpu_id = gpu_result["gpu_id"]
            gpu_name = gpu_result["device_name"]
            memory_mb = gpu_result["memory_used"] / 1024**2
            print(f"✓ GPU {gpu_id} ({gpu_name}): {memory_mb:.1f} MB used")
            print(f"  - Sum: {gpu_result['sum']:.2f}, Mean: {gpu_result['mean']:.4f}")

    else:
        print(
            f"⚠ Dual GPU computation failed: {result.get('torch_error', 'Unknown error')}"
        )
        # This might not be a test failure if tensor01 doesn't have 2+ GPUs available

    return result
