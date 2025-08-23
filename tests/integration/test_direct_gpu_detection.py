#!/usr/bin/env python3
"""
Direct GPU detection test that bypasses two-venv setup issues.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_direct_gpu_detection():
    """Test GPU detection using simplest possible approach."""

    load_config("tensor01_config.yml")

    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False

    # Configure with simplified settings
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
    )

    @cluster(cores=1, memory="4GB")
    def direct_gpu_check():
        """Direct GPU check without complex setup."""
        import subprocess
        import os

        # Show environment first
        cuda_env = os.environ.get("CUDA_VISIBLE_DEVICES", "NOT_SET")

        # Simple GPU count check
        result = subprocess.run(
            [
                "python",
                "-c",
                f"""
import os
print(f'CUDA_VISIBLE_DEVICES_ENV: {cuda_env}')

try:
    import torch
    print(f'TORCH_VERSION: {torch.__version__}')
    print(f'CUDA_AVAILABLE: {torch.cuda.is_available()}')
    print(f'GPU_COUNT: {torch.cuda.device_count()}')
    
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            print(f'GPU_{i}: {props.name} ({props.total_memory // 1024**3}GB)')
except ImportError as e:
    print(f'TORCH_IMPORT_ERROR: {e}')
except Exception as e:
    print(f'TORCH_ERROR: {e}')

# Try nvidia-smi as backup
try:
    import subprocess
    result = subprocess.run(['nvidia-smi', '--list-gpus'], 
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                          universal_newlines=True, timeout=10)
    if result.returncode == 0:
        gpu_lines = [line for line in result.stdout.strip().split('\\n') if line.strip()]
        print(f'NVIDIA_SMI_GPU_COUNT: {len(gpu_lines)}')
        for i, line in enumerate(gpu_lines):
            print(f'NVIDIA_GPU_{i}: {line}')
    else:
        print(f'NVIDIA_SMI_ERROR: {result.stderr}')
except Exception as e:
    print(f'NVIDIA_SMI_EXCEPTION: {e}')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    print("Testing direct GPU detection (bypassing two-venv)...")
    try:
        result = direct_gpu_check()

        print(f"✅ Test completed (return code: {result['returncode']})")
        print(f"STDOUT:\n{result['stdout']}")

        if result["stderr"]:
            print(f"STDERR:\n{result['stderr']}")

        # Parse GPU count from output
        stdout = result["stdout"]
        if "GPU_COUNT:" in stdout:
            gpu_count_line = [
                line for line in stdout.split("\n") if "GPU_COUNT:" in line
            ][0]
            gpu_count = int(gpu_count_line.split(":", 1)[1].strip())
            print(f"\n🎯 DETECTED {gpu_count} GPUs via PyTorch")

            if gpu_count == 8:
                print("✅ PERFECT: All 8 GPUs detected!")
            elif gpu_count > 0:
                print(f"⚠️  Only {gpu_count} GPUs detected (expected 8)")
            else:
                print("❌ No GPUs detected via PyTorch")

        if "NVIDIA_SMI_GPU_COUNT:" in stdout:
            nvidia_count_line = [
                line for line in stdout.split("\n") if "NVIDIA_SMI_GPU_COUNT:" in line
            ][0]
            nvidia_count = int(nvidia_count_line.split(":", 1)[1].strip())
            print(f"🔍 nvidia-smi reports {nvidia_count} GPUs")

        return result

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return None


if __name__ == "__main__":
    result = test_direct_gpu_detection()
    if result:
        print(f"\n📋 Test completed successfully")
    else:
        print(f"\n❌ Test failed")
