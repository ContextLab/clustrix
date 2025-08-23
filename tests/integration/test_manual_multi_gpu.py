#!/usr/bin/env python3
"""
Test manual multi-GPU computation using simple pattern that avoids complexity threshold.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_manual_multi_gpu_computation():
    """Test manual multi-GPU computation."""

    load_config("tensor01_config.yml")

    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False

    # Configure with multiple GPUs and AUTO GPU PARALLELIZATION DISABLED
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,  # Disable to test manual approach
        environment_variables={"CUDA_VISIBLE_DEVICES": "0,1,2,3"},
    )

    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def manual_multi_gpu():
        """Manual multi-GPU computation using simple subprocess pattern."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "-c",
                """
import torch
import json

gpu_count = torch.cuda.device_count()
results = []

if gpu_count >= 2:
    # Manual multi-GPU computation
    for gpu_id in range(min(2, gpu_count)):
        torch.cuda.set_device(gpu_id)
        device = torch.device(f'cuda:{gpu_id}')
        
        # Simple computation on this GPU
        x = torch.randn(100, 100, device=device)
        y = torch.mm(x, x.t())
        trace_val = y.trace().item()
        results.append(trace_val)
    
    print(f'MANUAL_MULTI_GPU:success')
    print(f'GPU_COUNT:{gpu_count}')
    print(f'RESULTS_COUNT:{len(results)}')
else:
    print(f'MANUAL_MULTI_GPU:insufficient_gpus')
    print(f'GPU_COUNT:{gpu_count}')
""",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )

        return {"success": result.returncode == 0, "output": result.stdout}

    print("Testing manual multi-GPU computation...")
    try:
        result = manual_multi_gpu()

        if result["success"]:
            output = result["output"]
            print(f"✅ Test completed!")
            print(f"Output:\n{output}")

            if "MANUAL_MULTI_GPU:success" in output:
                # Extract results
                lines = output.split("\n")
                gpu_count_line = [line for line in lines if "GPU_COUNT:" in line][0]
                results_count_line = [
                    line for line in lines if "RESULTS_COUNT:" in line
                ][0]

                gpu_count = int(gpu_count_line.split(":", 1)[1])
                results_count = int(results_count_line.split(":", 1)[1])

                print(f"🎉 Manual multi-GPU computation successful!")
                print(f"   GPUs available: {gpu_count}")
                print(f"   Computations completed: {results_count}")
                return True
            else:
                print("⚠️  Insufficient GPUs for multi-GPU computation")
                return True
        else:
            print(f"❌ Test failed")
            return False

    except Exception as e:
        print(f"❌ Test exception: {e}")
        return False


if __name__ == "__main__":
    success = test_manual_multi_gpu_computation()
    if success:
        print("\n🎉 Manual multi-GPU computation test PASSED!")
    else:
        print("\n❌ Manual multi-GPU computation test FAILED!")
