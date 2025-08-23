#!/usr/bin/env python3
"""
Simple complexity test to verify the threshold issue.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_level1_simple():
    """Test level 1 complexity - should work."""
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={"CUDA_VISIBLE_DEVICES": "0"}
    )
    
    @cluster(cores=1, memory="2GB")
    def simple_level1():
        """Level 1: Minimal complexity - SHOULD WORK."""
        import subprocess
        result = subprocess.run(
            ["python", "-c", "print('LEVEL1_SUCCESS')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )
        return {"success": result.returncode == 0, "output": result.stdout}
    
    print("Testing level 1 complexity...")
    try:
        result = simple_level1()
        print(f"✅ Level 1 result: {result}")
        if result["success"] and "LEVEL1_SUCCESS" in result["output"]:
            print("🎉 Level 1 PASSED")
            return True
        else:
            print("❌ Level 1 FAILED")
            return False
    except Exception as e:
        print(f"❌ Level 1 EXCEPTION: {e}")
        return False

def test_level3_complex():
    """Test level 3 complexity - should fail."""
    load_config("tensor01_config.yml")
    
    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False
    
    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={"CUDA_VISIBLE_DEVICES": "0"}
    )
    
    @cluster(cores=1, memory="4GB")
    def complex_level3():
        """Level 3: Medium complexity - SHOULD FAIL."""
        import subprocess
        import json
        
        # Multiple operations and data structures
        results = {}
        metadata = {"steps": [], "timing": []}
        
        # Step 1
        try:
            result1 = subprocess.run(
                ["python", "-c", "print('STEP1_OK')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            results["step1"] = {"success": result1.returncode == 0, "output": result1.stdout}
            metadata["steps"].append("step1")
        except Exception as e:
            results["step1"] = {"error": str(e)}
        
        # Step 2 
        try:
            result2 = subprocess.run(
                ["python", "-c", "import torch; print(f'GPU_COUNT:{torch.cuda.device_count()}')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            results["step2"] = {"success": result2.returncode == 0, "output": result2.stdout}
            metadata["steps"].append("step2")
        except Exception as e:
            results["step2"] = {"error": str(e)}
        
        # Step 3
        try:
            result3 = subprocess.run(
                ["python", "-c", "import os; print(f'ENV:{os.environ.get(\"CUDA_VISIBLE_DEVICES\", \"none\")}')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            results["step3"] = {"success": result3.returncode == 0, "output": result3.stdout}
            metadata["steps"].append("step3")
        except Exception as e:
            results["step3"] = {"error": str(e)}
        
        # Analysis
        success_count = sum(1 for step in results.values() if step.get("success", False))
        metadata["analysis"] = {
            "total_steps": len(results),
            "successful_steps": success_count,
            "success_rate": success_count / len(results),
            "overall_success": success_count == len(results)
        }
        
        return {"results": results, "metadata": metadata}
    
    print("Testing level 3 complexity...")
    try:
        result = complex_level3()
        print(f"✅ Level 3 result: {result}")
        print("🎉 Level 3 PASSED (unexpected!)")
        return True
    except Exception as e:
        print(f"❌ Level 3 FAILED (expected): {e}")
        return False

if __name__ == "__main__":
    print("=== Complexity Threshold Analysis ===")
    level1_ok = test_level1_simple()
    level3_ok = test_level3_complex()
    
    print(f"\n=== Summary ===")
    print(f"Level 1 (simple): {'PASS' if level1_ok else 'FAIL'}")
    print(f"Level 3 (complex): {'PASS' if level3_ok else 'FAIL'}")
    
    if level1_ok and not level3_ok:
        print("\n✅ Complexity threshold confirmed: simple works, complex fails")
    elif level1_ok and level3_ok:
        print("\n⚠️  Both levels work - threshold may be higher")
    else:
        print("\n❌ Unexpected pattern - investigate further")