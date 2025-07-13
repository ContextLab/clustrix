#!/usr/bin/env python3
"""
Super simple multi-GPU test using the exact working pattern.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_super_simple_multi_gpu():
    """Super simple multi-GPU test."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={"CUDA_VISIBLE_DEVICES": "0,1,2,3"}
    )
    
    @cluster(cores=1, memory="4GB")
    def simple_multi_gpu_test():
        """Super simple multi-GPU test using exact working pattern."""
        import subprocess
        result = subprocess.run(
            ["python", "-c", "import torch; print(f'GPUS:{torch.cuda.device_count()}'); x=torch.tensor([1.0]).cuda(); print(f'GPU0:ok'); torch.cuda.set_device(1); y=torch.tensor([2.0]).cuda(); print(f'GPU1:ok')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )
        return {"output": result.stdout}
    
    print("Testing super simple multi-GPU...")
    try:
        result = simple_multi_gpu_test()
        print(f"Result: {result}")
        
        output = result["output"]
        if "GPUS:" in output and "GPU0:ok" in output and "GPU1:ok" in output:
            gpu_count = int(output.split("GPUS:", 1)[1].split('\n')[0])
            print(f"🎉 Successfully used multiple GPUs! Count: {gpu_count}")
            print("✅ GPU0 and GPU1 both accessible")
            return True
        elif "GPUS:" in output:
            gpu_count = int(output.split("GPUS:", 1)[1].split('\n')[0])
            print(f"⚠️  GPUs detected: {gpu_count}, but multi-GPU access may have failed")
            return False
        
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_super_simple_multi_gpu()