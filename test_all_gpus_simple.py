#!/usr/bin/env python3
"""
Super simple test to detect all GPUs using the proven working pattern.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_all_gpus_simple():
    """Super simple detection of all GPUs."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # No CUDA_VISIBLE_DEVICES restriction - detect all
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
    )
    
    @cluster(cores=1, memory="4GB")
    def simple_all_gpu_count():
        """Simplest possible all GPU count."""
        import subprocess
        result = subprocess.run(
            ["python", "-c", "import torch; print(f'ALL_GPUS:{torch.cuda.device_count()}')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )
        return {"output": result.stdout}
    
    print("Testing detection of ALL GPUs...")
    try:
        result = simple_all_gpu_count()
        print(f"Result: {result}")
        
        if "ALL_GPUS:" in result["output"]:
            gpu_count = int(result["output"].split("ALL_GPUS:", 1)[1].strip())
            print(f"🎉 DETECTED ALL {gpu_count} GPUs on tensor01!")
            
            if gpu_count == 8:
                print("✅ PERFECT: All 8 GPUs detected as expected!")
            elif gpu_count > 0:
                print(f"⚠️  Detected {gpu_count} GPUs (expected 8)")
            else:
                print("❌ No GPUs detected")
            
            return gpu_count
        
        return 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return 0

if __name__ == "__main__":
    gpu_count = test_all_gpus_simple()
    print(f"\n📊 FINAL: {gpu_count} GPUs available on tensor01")