#!/usr/bin/env python3
"""
Simple verification that all 8 GPUs are now detected after config fix.
Uses the proven working pattern from earlier tests.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_simple_gpu_verification():
    """Verify GPU detection using simplest possible approach."""
    
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("❌ No credentials available")
        return False
    
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        auto_gpu_parallel=False,
    )
    
    @cluster(cores=1, memory="4GB", auto_gpu_parallel=False)
    def super_simple_gpu_count():
        """Super simple GPU count using proven pattern."""
        import subprocess
        result = subprocess.run(
            ["python", "-c", "import torch; print(f'GPU_COUNT:{torch.cuda.device_count()}'); print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, 
            universal_newlines=True,
            timeout=30
        )
        return {"output": result.stdout, "success": result.returncode == 0}
    
    print("🔍 Testing GPU detection after configuration fix...")
    try:
        result = super_simple_gpu_count()
        
        if result["success"]:
            output = result["output"]
            print(f"✅ GPU detection successful!")
            print(f"Output: {output}")
            
            if "GPU_COUNT:" in output:
                gpu_count_line = [line for line in output.split('\n') if 'GPU_COUNT:' in line][0]
                gpu_count = int(gpu_count_line.split(':', 1)[1])
                
                print(f"\n🎯 DETECTED {gpu_count} GPUs on tensor01")
                
                if gpu_count == 8:
                    print("🎉 SUCCESS: All 8 GPUs detected as expected!")
                    print("✅ Configuration fix worked - CUDA_VISIBLE_DEVICES restriction removed")
                    return True
                elif gpu_count > 1:
                    print(f"⚠️  Detected {gpu_count} GPUs (expected 8)")
                    print("📋 This may be due to SLURM job allocation or other restrictions")
                    return False  
                elif gpu_count == 1:
                    print("❌ Still only detecting 1 GPU - config fix may not have taken effect")
                    return False
                else:
                    print("❌ No GPUs detected")
                    return False
            else:
                print("❌ Could not parse GPU count from output")
                return False
        else:
            print(f"❌ GPU detection failed")
            return False
            
    except Exception as e:
        print(f"❌ Test exception: {e}")
        return False

if __name__ == "__main__":
    success = test_simple_gpu_verification()
    if success:
        print(f"\n🎉 GPU DETECTION VERIFICATION: PASSED")
        print("The configuration fix successfully allows detection of all 8 GPUs!")
    else:
        print(f"\n❌ GPU DETECTION VERIFICATION: FAILED")
        print("Additional investigation needed.")