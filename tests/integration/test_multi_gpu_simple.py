#!/usr/bin/env python3
"""
Extremely simple multi-GPU test using the exact working pattern.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_multi_gpu_simple():
    """Test extremely simple multi-GPU usage."""
    
    # Load config and modify for multi-GPU access
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    # Configure with 2 GPUs visible
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={
            "CUDA_VISIBLE_DEVICES": "0,1"  # Just 2 GPUs
        }
    )
    
    @cluster(cores=1, memory="4GB")
    def simple_two_gpu_check():
        """Simple two GPU check using verified pattern."""
        import subprocess
        
        # Extremely simple check using exact working pattern
        result = subprocess.run(
            ["python", "-c", "import torch; import os; print(f'ENV:{os.environ.get(\"CUDA_VISIBLE_DEVICES\", \"none\")}'); print(f'COUNT:{torch.cuda.device_count()}'); a=torch.tensor([1.0]).cuda(0); b=torch.tensor([2.0]).cuda(1); print(f'GPU0:{a.device}'); print(f'GPU1:{b.device}')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )
        
        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr
        }
    
    print("Testing simple two-GPU...")
    try:
        result = simple_two_gpu_check()
        print("✅ Test completed!")
        
        if result["success"]:
            print("Output:")
            for line in result["output"].strip().split('\n'):
                print(f"  {line}")
            
            # Check if we accessed both GPUs
            output = result["output"]
            if "GPU0:cuda:0" in output and "GPU1:cuda:1" in output:
                print(f"\n🎉 SUCCESS: Accessed both GPU 0 and GPU 1!")
                return True
            else:
                print(f"\n⚠️  Could not access both GPUs")
                return False
        else:
            print(f"Error: {result['error']}")
            return False
            
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    test_multi_gpu_simple()