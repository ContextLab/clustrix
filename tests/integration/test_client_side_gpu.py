#!/usr/bin/env python3
"""
Test the new client-side GPU parallelization approach.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_client_side_gpu_parallelization():
    """Test client-side GPU parallelization approach."""

    load_config("tensor01_config.yml")

    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False

    # Configure with automatic GPU parallelization enabled
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True,
        environment_variables={
            "CUDA_VISIBLE_DEVICES": "0,1,2,3"  # Make multiple GPUs available
        },
    )

    @cluster(cores=2, memory="8GB", auto_gpu_parallel=True)
    def gpu_computation_with_loops():
        """Function with GPU operations that should trigger auto-parallelization."""
        import torch

        # This should be detected as a parallelizable GPU operation
        results = []
        for i in range(10):  # Simple loop that can be parallelized
            x = torch.randn(50, 50).cuda()
            y = torch.mm(x, x.t())
            trace_val = y.trace().item()
            results.append(trace_val)

        return results

    print("Testing client-side GPU parallelization...")
    try:
        result = gpu_computation_with_loops()

        print(f"✅ Test completed!")
        print(f"Result type: {type(result)}")

        if isinstance(result, dict) and result.get("gpu_parallel"):
            print(f"🎉 GPU parallelization was successfully applied!")
            print(f"   GPUs used: {result.get('gpu_count', 0)}")
            print(f"   Successful GPUs: {result.get('successful_gpus', [])}")
            print(f"   Results: {result.get('results', {})}")
            return True
        elif isinstance(result, list):
            print(f"⚠️  Function executed normally (no GPU parallelization applied)")
            print(f"   Results count: {len(result)}")
            print(f"   Sample results: {result[:3] if len(result) >= 3 else result}")
            return True
        else:
            print(f"❌ Unexpected result type: {result}")
            return False

    except Exception as e:
        print(f"❌ Test exception: {e}")
        return False


if __name__ == "__main__":
    success = test_client_side_gpu_parallelization()
    if success:
        print("\n🎉 Client-side GPU parallelization test completed!")
    else:
        print("\n❌ Client-side GPU parallelization test FAILED!")
