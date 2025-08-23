#!/usr/bin/env python3
"""
Very simple test of automatic GPU parallelization using the simplest pattern.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_simple_auto_gpu():
    """Test very simple automatic GPU parallelization."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=True
    )
    
    @cluster(cores=1, memory="4GB", auto_gpu_parallel=True)
    def test_gpu_count():
        """Super simple GPU count test."""
        import subprocess
        
        result = subprocess.run([
            "python", "-c", "import torch; print(f'GPUS:{torch.cuda.device_count()}')"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        
        return {"output": result.stdout}
    
    print("Testing super simple GPU detection...")
    try:
        result = test_gpu_count()
        print(f"✅ Result: {result}")
        
        if "GPUS:" in result["output"]:
            gpu_count = int(result["output"].split("GPUS:", 1)[1].strip())
            print(f"🎉 Detected {gpu_count} GPUs!")
            return True
        
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_simple_auto_gpu()