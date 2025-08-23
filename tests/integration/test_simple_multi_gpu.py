#!/usr/bin/env python3
"""
Simple multi-GPU test using working pattern.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_simple_multi_gpu():
    """Test simple multi-GPU detection."""

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
        },
    )

    @cluster(cores=1, memory="4GB")
    def simple_multi_gpu_check():
        """Simple multi-GPU check."""
        import subprocess
        import os

        result = {
            "cuda_env": os.environ.get("CUDA_VISIBLE_DEVICES", "default"),
            "gpu_count": None,
        }

        # Simple PyTorch GPU count check
        try:
            pytorch_result = subprocess.run(
                [
                    "python",
                    "-c",
                    "import torch; print(f'COUNT:{torch.cuda.device_count()}')",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )

            if pytorch_result.returncode == 0:
                output = pytorch_result.stdout.strip()
                if "COUNT:" in output:
                    count = int(output.split("COUNT:")[1])
                    result["gpu_count"] = count
                else:
                    result["gpu_count"] = -1  # Error
            else:
                result["gpu_count"] = -2  # Command failed
        except Exception as e:
            result["gpu_count"] = -3  # Exception

        return result

    print("Testing simple multi-GPU...")
    try:
        result = simple_multi_gpu_check()
        print("✅ Test completed!")
        print(f"CUDA_VISIBLE_DEVICES: {result['cuda_env']}")
        print(f"GPU count detected: {result['gpu_count']}")

        if result["gpu_count"] > 1:
            print(f"\n🎉 SUCCESS: {result['gpu_count']} GPUs detected!")
            return True
        elif result["gpu_count"] == 1:
            print(f"\n⚠️  Only 1 GPU available (expected multiple)")
            return False
        else:
            print(f"\n❌ GPU detection failed (code: {result['gpu_count']})")
            return False

    except Exception as e:
        print(f"Test failed: {e}")
        return False


if __name__ == "__main__":
    test_simple_multi_gpu()
