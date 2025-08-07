"""
Verified CUDA functionality tests for tensor01.

These tests demonstrate the working PyTorch CUDA integration on tensor01
using the conda-based two-venv system.
"""

import pytest
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_tensor01_gpu_detection_verified():
    """Test verified GPU detection on tensor01."""

    # Load configuration
    load_config("tensor01_config.yml")

    # Get credentials
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        pytest.skip("No tensor01 credentials available")

    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
    )

    @cluster(cores=1, memory="2GB")
    def verify_gpu_hardware():
        """Verify GPU hardware detection."""
        import subprocess
        import os

        result = {
            "conda_env": os.environ.get("CONDA_DEFAULT_ENV", "None"),
            "nvidia_smi": None,
        }

        # Test nvidia-smi
        try:
            nvidia_result = subprocess.run(
                ["nvidia-smi", "-L"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10,
            )
            if nvidia_result.returncode == 0:
                gpu_lines = [
                    line for line in nvidia_result.stdout.split("\n") if "GPU" in line
                ]
                result["nvidia_smi"] = {
                    "success": True,
                    "gpu_count": len(gpu_lines),
                    "gpus": gpu_lines,
                }
            else:
                result["nvidia_smi"] = {"success": False, "error": nvidia_result.stderr}
        except Exception as e:
            result["nvidia_smi"] = {"success": False, "error": str(e)}

        return result

    # Execute test
    result = verify_gpu_hardware()

    # Validate results
    assert isinstance(result, dict)
    assert result["conda_env"] != "None", "Should be running in conda environment"
    assert result["nvidia_smi"][
        "success"
    ], f"nvidia-smi failed: {result['nvidia_smi']['error']}"
    assert (
        result["nvidia_smi"]["gpu_count"] == 8
    ), f"Expected 8 GPUs, got {result['nvidia_smi']['gpu_count']}"

    # Verify all GPUs are RTX A6000
    for gpu_line in result["nvidia_smi"]["gpus"]:
        assert "NVIDIA RTX A6000" in gpu_line, f"Unexpected GPU type: {gpu_line}"

    print(f"✅ Verified: 8x NVIDIA RTX A6000 GPUs detected")


@pytest.mark.real_world
def test_tensor01_pytorch_cuda_verified():
    """Test verified PyTorch CUDA functionality on tensor01."""

    # Load configuration
    load_config("tensor01_config.yml")

    # Get credentials
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        pytest.skip("No tensor01 credentials available")

    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
    )

    @cluster(cores=1, memory="4GB")
    def verify_pytorch_cuda():
        """Verify PyTorch CUDA functionality."""
        import subprocess

        result = {"pytorch_check": None}

        # Test PyTorch CUDA
        try:
            pytorch_result = subprocess.run(
                [
                    "python",
                    "-c",
                    "import torch; print(f'VERSION:{torch.__version__}'); print(f'CUDA:{torch.cuda.is_available()}'); print(f'DEVICES:{torch.cuda.device_count()}')",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            if pytorch_result.returncode == 0:
                lines = pytorch_result.stdout.strip().split("\n")
                pytorch_info = {}
                for line in lines:
                    if line.startswith("VERSION:"):
                        pytorch_info["version"] = line.split(":", 1)[1]
                    elif line.startswith("CUDA:"):
                        pytorch_info["cuda_available"] = line.split(":", 1)[1] == "True"
                    elif line.startswith("DEVICES:"):
                        pytorch_info["device_count"] = int(line.split(":", 1)[1])

                result["pytorch_check"] = {"success": True, "info": pytorch_info}
            else:
                result["pytorch_check"] = {
                    "success": False,
                    "error": pytorch_result.stderr,
                }
        except Exception as e:
            result["pytorch_check"] = {"success": False, "error": str(e)}

        return result

    # Execute test
    result = verify_pytorch_cuda()

    # Validate results
    assert isinstance(result, dict)
    assert result["pytorch_check"][
        "success"
    ], f"PyTorch check failed: {result['pytorch_check']['error']}"

    info = result["pytorch_check"]["info"]
    assert info["version"].startswith(
        "2.7"
    ), f"Expected PyTorch 2.7.x, got {info['version']}"
    assert "+cu118" in info["version"], f"Expected CUDA 11.8, got {info['version']}"
    assert info["cuda_available"], "CUDA should be available"
    assert (
        info["device_count"] >= 1
    ), f"Expected at least 1 GPU, got {info['device_count']}"

    print(f"✅ Verified: PyTorch {info['version']} with CUDA working")


@pytest.mark.real_world
def test_tensor01_simple_gpu_computation_verified():
    """Test verified simple GPU computation on tensor01."""

    # Load configuration
    load_config("tensor01_config.yml")

    # Get credentials
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        pytest.skip("No tensor01 credentials available")

    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
    )

    @cluster(cores=1, memory="4GB")
    def verify_simple_gpu_computation():
        """Verify simple GPU computation."""
        import subprocess

        result = {"gpu_math": None}

        # Simple GPU computation test
        try:
            gpu_result = subprocess.run(
                [
                    "python",
                    "-c",
                    "import torch; a=torch.tensor([1.0, 2.0]).cuda(); b=torch.tensor([3.0, 4.0]).cuda(); c=a+b; print(f'RESULT:{c.cpu().tolist()}')",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            if gpu_result.returncode == 0:
                output = gpu_result.stdout.strip()
                if "RESULT:" in output:
                    result_str = output.split("RESULT:")[1]
                    result["gpu_math"] = {"success": True, "result": result_str}
                else:
                    result["gpu_math"] = {"success": False, "unexpected": output}
            else:
                result["gpu_math"] = {"success": False, "error": gpu_result.stderr}
        except Exception as e:
            result["gpu_math"] = {"success": False, "error": str(e)}

        return result

    # Execute test
    result = verify_simple_gpu_computation()

    # Validate results
    assert isinstance(result, dict)
    assert result["gpu_math"][
        "success"
    ], f"GPU computation failed: {result['gpu_math']['error']}"

    # Check computation result
    result_str = result["gpu_math"]["result"]
    assert "[4.0, 6.0]" == result_str, f"Expected [4.0, 6.0], got {result_str}"

    print(f"✅ Verified: GPU computation result {result_str}")
