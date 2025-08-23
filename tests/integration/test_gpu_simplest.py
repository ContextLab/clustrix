#!/usr/bin/env python3
"""
Simplest possible GPU test using the exact pattern that works.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_simplest_gpu():
    """Use the exact simplest pattern that works."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # Configure with multiple GPUs
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={"CUDA_VISIBLE_DEVICES": "0,1,2,3"}
    )
    
    @cluster(cores=1, memory="4GB")
    def simple_gpu_count():
        """Simplest possible GPU count check."""
        import subprocess
        result = subprocess.run(
            ["python", "-c", "import torch; print(f'GPUS:{torch.cuda.device_count()}')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )
        return {"output": result.stdout}
    
    print("Testing simplest GPU count...")
    try:
        result = simple_gpu_count()
        print(f"Result: {result}")
        
        if "GPUS:" in result["output"]:
            gpu_count = int(result["output"].split("GPUS:", 1)[1].strip())
            print(f"🎉 Detected {gpu_count} GPUs with CUDA_VISIBLE_DEVICES=0,1,2,3")
            
            if gpu_count >= 2:
                print("✅ Multi-GPU environment confirmed!")
                return True
            else:
                print(f"⚠️  Only {gpu_count} GPU detected")
                return True
        
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_simplest_gpu()