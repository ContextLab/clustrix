#!/usr/bin/env python3
"""
Test GPU detection bypassing automatic GPU parallelization.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_bypass_gpu_parallel():
    """Test GPU detection with GPU parallelization disabled."""

    load_config("gpu_cluster_config.yml")

    gpu_cluster_creds = credentials.get_gpu_cluster_credentials()
    if not gpu_cluster_creds:
        print("No credentials available")
        return False

    # Configure with GPU parallelization explicitly disabled
    configure(
        password=gpu_cluster_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Disable automatic GPU parallelization
    )

    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def simple_gpu_count_check():
        """Simplest GPU count without any parallelization logic."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "-c",
                """
import torch
print(f'GPU_COUNT:{torch.cuda.device_count()}')
print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')

if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        print(f'GPU_{i}:{props.name}')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )

        return {"output": result.stdout, "success": result.returncode == 0}

    print("Testing GPU count with parallelization disabled...")
    try:
        result = simple_gpu_count_check()

        if result["success"]:
            output = result["output"]
            print(f"✅ GPU detection successful!")
            print(f"Output:\n{output}")

            # Parse GPU count
            if "GPU_COUNT:" in output:
                gpu_count_line = [
                    line for line in output.split("\n") if "GPU_COUNT:" in line
                ][0]
                gpu_count = int(gpu_count_line.split(":", 1)[1])

                print(f"\n🎯 DETECTED {gpu_count} GPUs on gpu_cluster")

                if gpu_count == 8:
                    print("✅ PERFECT: All 8 GPUs detected as expected!")
                elif gpu_count > 0:
                    print(f"⚠️  Detected {gpu_count} GPUs (expected 8)")
                    print(
                        "This could be due to SLURM job allocation or CUDA_VISIBLE_DEVICES"
                    )
                else:
                    print("❌ No GPUs detected")

                return gpu_count
            else:
                print("❌ Could not parse GPU count from output")
                return 0
        else:
            print(f"❌ GPU detection failed")
            return 0

    except Exception as e:
        print(f"❌ Test exception: {e}")
        return 0


if __name__ == "__main__":
    gpu_count = test_bypass_gpu_parallel()
    print(f"\n📊 Final Result: {gpu_count} GPUs detected on gpu_cluster")
