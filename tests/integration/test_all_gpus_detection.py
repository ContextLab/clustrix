#!/usr/bin/env python3
"""
Test dynamic detection of ALL available GPUs without hard-coding numbers.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_all_gpus_detection():
    """Test detection of all available GPUs dynamically."""

    load_config("tensor01_config.yml")

    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False

    # Configure WITHOUT specifying CUDA_VISIBLE_DEVICES to detect all GPUs
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        # No CUDA_VISIBLE_DEVICES - let it detect all available GPUs
    )

    @cluster(cores=1, memory="4GB")
    def detect_all_gpus():
        """Detect all available GPUs dynamically."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "-c",
                """
import torch
import os

# Show actual environment
cuda_env = os.environ.get('CUDA_VISIBLE_DEVICES', 'all_gpus')
gpu_count = torch.cuda.device_count()
cuda_available = torch.cuda.is_available()

print(f'CUDA_VISIBLE_DEVICES:{cuda_env}')
print(f'CUDA_AVAILABLE:{cuda_available}')
print(f'TOTAL_GPU_COUNT:{gpu_count}')

if cuda_available and gpu_count > 0:
    for i in range(gpu_count):
        try:
            props = torch.cuda.get_device_properties(i)
            print(f'GPU_{i}:{props.name}:MEMORY_{props.total_memory}')
        except:
            print(f'GPU_{i}:info_unavailable')
else:
    print('NO_GPUS_DETECTED')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )
        return {"success": result.returncode == 0, "output": result.stdout}

    print("Testing dynamic detection of all GPUs...")
    try:
        result = detect_all_gpus()

        if result["success"]:
            output = result["output"]
            print(f"✅ GPU detection completed!")
            print(f"Output:\n{output}")

            # Parse results
            lines = output.split("\n")
            gpu_count_line = [line for line in lines if "TOTAL_GPU_COUNT:" in line]

            if gpu_count_line:
                total_gpus = int(gpu_count_line[0].split(":", 1)[1])
                print(f"\n🎉 DETECTED {total_gpus} TOTAL GPUs on tensor01!")

                # Count GPU info lines
                gpu_info_lines = [
                    line for line in lines if line.startswith("GPU_") and ":" in line
                ]
                print(f"📋 GPU Details ({len(gpu_info_lines)} devices):")
                for gpu_line in gpu_info_lines:
                    print(f"   {gpu_line}")

                if total_gpus == 8:
                    print("✅ CONFIRMED: All 8 GPUs detected as expected!")
                elif total_gpus > 0:
                    print(f"⚠️  Detected {total_gpus} GPUs (expected 8)")
                else:
                    print("❌ No GPUs detected")

                return total_gpus
            else:
                print("❌ Could not parse GPU count")
                return 0
        else:
            print(f"❌ Detection failed")
            return 0

    except Exception as e:
        print(f"❌ Test exception: {e}")
        return 0


if __name__ == "__main__":
    gpu_count = test_all_gpus_detection()
    print(f"\n📊 Final Result: {gpu_count} GPUs detected on tensor01")
