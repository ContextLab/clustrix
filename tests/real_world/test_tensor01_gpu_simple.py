"""
Simple GPU detection test for tensor01 to debug issues.
"""

import pytest
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
def test_tensor01_basic_gpu_detection():
    """Test basic GPU detection on tensor01."""

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
    def simple_gpu_detection() -> dict:
        """
        Simple GPU detection to debug issues.
        """
        import subprocess
        import os

        result = {
            "hostname": "unknown",
            "gpu_info": {"available": False, "count": 0, "error": None},
            "cuda_env_vars": {},
            "python_version": "unknown",
        }

        # Get hostname (Python 3.6 compatible)
        try:
            hostname_result = subprocess.run(
                ["hostname"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5,
            )
            if hostname_result.returncode == 0:
                result["hostname"] = hostname_result.stdout.strip()
        except Exception as e:
            result["hostname_error"] = str(e)

        # Get Python version
        try:
            import sys

            result["python_version"] = sys.version
        except Exception as e:
            result["python_version_error"] = str(e)

        # Try basic nvidia-smi
        try:
            nvidia_result = subprocess.run(
                ["nvidia-smi", "-L"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10,
            )
            if nvidia_result.returncode == 0:
                gpu_lines = nvidia_result.stdout.strip().split("\n")
                gpu_count = len([line for line in gpu_lines if "GPU" in line])
                result["gpu_info"] = {
                    "available": True,
                    "count": gpu_count,
                    "list_output": nvidia_result.stdout.strip(),
                }
            else:
                result["gpu_info"] = {
                    "available": False,
                    "count": 0,
                    "error": f"nvidia-smi failed: {nvidia_result.stderr}",
                }
        except Exception as e:
            result["gpu_info"] = {"available": False, "count": 0, "error": str(e)}

        # Test PyTorch CUDA availability (should be installed in VENV2)
        try:
            import torch

            result["pytorch_info"] = {
                "available": True,
                "version": torch.__version__,
                "cuda_available": torch.cuda.is_available(),
                "cuda_device_count": (
                    torch.cuda.device_count() if torch.cuda.is_available() else 0
                ),
            }

            if torch.cuda.is_available():
                result["pytorch_info"]["device_names"] = [
                    torch.cuda.get_device_name(i)
                    for i in range(torch.cuda.device_count())
                ]
        except ImportError:
            result["pytorch_info"] = {
                "available": False,
                "error": "PyTorch not installed",
            }
        except Exception as e:
            result["pytorch_info"] = {"available": False, "error": str(e)}

        # Check CUDA environment variables
        cuda_vars = ["CUDA_VISIBLE_DEVICES", "CUDA_HOME", "CUDA_PATH"]
        for var in cuda_vars:
            result["cuda_env_vars"][var] = os.getenv(var)

        return result

    # Execute the simple detection
    print("Running simple GPU detection on tensor01...")
    result = simple_gpu_detection()

    # Validate results
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"

    print(f"✓ Hostname: {result['hostname']}")
    print(f"✓ Python: {result['python_version']}")
    print(f"✓ GPU available: {result['gpu_info']['available']}")

    if result["gpu_info"]["available"]:
        print(f"✓ GPU count: {result['gpu_info']['count']}")
        print(f"✓ GPU list: {result['gpu_info']['list_output']}")
    else:
        print(f"✗ GPU error: {result['gpu_info']['error']}")

    # Show PyTorch info
    pytorch_info = result.get("pytorch_info", {})
    if pytorch_info.get("available", False):
        print(f"✓ PyTorch: {pytorch_info['version']}")
        print(f"✓ PyTorch CUDA: {pytorch_info['cuda_available']}")
        if pytorch_info.get("cuda_available", False):
            print(f"✓ PyTorch GPU count: {pytorch_info['cuda_device_count']}")
            if "device_names" in pytorch_info:
                for i, name in enumerate(pytorch_info["device_names"]):
                    print(f"  - GPU {i}: {name}")
    else:
        print(f"✗ PyTorch error: {pytorch_info.get('error', 'Unknown')}")

    return result
