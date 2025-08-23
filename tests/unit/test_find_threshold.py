#!/usr/bin/env python3
"""
Find the exact complexity threshold where functions start failing.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials

def test_level5_higher_complexity():
    """Test level 5 complexity - higher chance of failure."""
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
    def complex_level5():
        """Level 5: Higher complexity."""
        import subprocess
        import json
        import time
        import os
        
        # Complex multi-phase workflow 
        workflow = {
            "phase1_setup": None,
            "phase2_detection": None,
            "phase3_computation": None,
            "phase4_analysis": None,
            "phase5_summary": None
        }
        
        execution_log = []
        
        # Phase 1: Environment setup analysis
        try:
            execution_log.append("Starting Phase 1: Environment Setup")
            
            env_checks = {
                "cuda_env": os.environ.get('CUDA_VISIBLE_DEVICES', 'default'),
                "python_path": None,
                "torch_version": None
            }
            
            # Python path check
            path_result = subprocess.run(
                ["which", "python"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10
            )
            env_checks["python_path"] = path_result.stdout.strip() if path_result.returncode == 0 else "unknown"
            
            # PyTorch version check
            torch_result = subprocess.run(
                ["python", "-c", "import torch; print(torch.__version__)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15
            )
            env_checks["torch_version"] = torch_result.stdout.strip() if torch_result.returncode == 0 else "unknown"
            
            workflow["phase1_setup"] = {"success": True, "data": env_checks}
            execution_log.append("Phase 1 completed successfully")
            
        except Exception as e:
            workflow["phase1_setup"] = {"success": False, "error": str(e)}
            execution_log.append(f"Phase 1 failed: {str(e)}")
        
        # Phase 2: GPU detection and analysis
        try:
            execution_log.append("Starting Phase 2: GPU Detection")
            
            gpu_analysis = {
                "device_count": None,
                "device_names": [],
                "memory_info": [],
                "cuda_available": None
            }
            
            # Device count
            count_result = subprocess.run(
                ["python", "-c", "import torch; print(torch.cuda.device_count())"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15
            )
            gpu_analysis["device_count"] = int(count_result.stdout.strip()) if count_result.returncode == 0 else 0
            
            # CUDA availability
            cuda_result = subprocess.run(
                ["python", "-c", "import torch; print(torch.cuda.is_available())"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15
            )
            gpu_analysis["cuda_available"] = cuda_result.stdout.strip() == "True" if cuda_result.returncode == 0 else False
            
            # Device names (if available)
            if gpu_analysis["device_count"] > 0:
                for i in range(min(gpu_analysis["device_count"], 2)):  # Limit to 2 devices to avoid complexity
                    name_result = subprocess.run(
                        ["python", "-c", f"import torch; print(torch.cuda.get_device_name({i}))"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=15
                    )
                    if name_result.returncode == 0:
                        gpu_analysis["device_names"].append(name_result.stdout.strip())
            
            workflow["phase2_detection"] = {"success": True, "data": gpu_analysis}
            execution_log.append("Phase 2 completed successfully")
            
        except Exception as e:
            workflow["phase2_detection"] = {"success": False, "error": str(e)}
            execution_log.append(f"Phase 2 failed: {str(e)}")
        
        # Phase 3: Computational testing
        try:
            execution_log.append("Starting Phase 3: Computational Testing")
            
            computation_tests = {
                "basic_math": None,
                "gpu_tensor": None,
                "matrix_ops": None
            }
            
            # Basic math
            math_result = subprocess.run(
                ["python", "-c", "import math; print(f'SQRT_16:{math.sqrt(16)}')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10
            )
            computation_tests["basic_math"] = {
                "success": math_result.returncode == 0,
                "output": math_result.stdout.strip() if math_result.returncode == 0 else math_result.stderr
            }
            
            # GPU tensor creation  
            tensor_result = subprocess.run(
                ["python", "-c", "import torch; x=torch.tensor([1.0, 2.0]).cuda(); print(f'TENSOR_SUM:{x.sum().item()}')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=20
            )
            computation_tests["gpu_tensor"] = {
                "success": tensor_result.returncode == 0,
                "output": tensor_result.stdout.strip() if tensor_result.returncode == 0 else tensor_result.stderr
            }
            
            # Matrix operations
            matrix_result = subprocess.run(
                ["python", "-c", "import torch; a=torch.randn(10,10).cuda(); b=torch.mm(a,a.t()); print(f'MATRIX_TRACE:{b.trace().item():.2f}')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )
            computation_tests["matrix_ops"] = {
                "success": matrix_result.returncode == 0,
                "output": matrix_result.stdout.strip() if matrix_result.returncode == 0 else matrix_result.stderr
            }
            
            workflow["phase3_computation"] = {"success": True, "data": computation_tests}
            execution_log.append("Phase 3 completed successfully")
            
        except Exception as e:
            workflow["phase3_computation"] = {"success": False, "error": str(e)}
            execution_log.append(f"Phase 3 failed: {str(e)}")
        
        # Phase 4: Performance analysis
        try:
            execution_log.append("Starting Phase 4: Performance Analysis")
            
            performance_data = {
                "timing_tests": [],
                "memory_usage": None,
                "system_load": None
            }
            
            # Timing tests
            for size in [100, 500]:  # Limit sizes to reduce complexity
                timing_result = subprocess.run(
                    ["python", "-c", f"""
import torch
import time
start = time.time()
x = torch.randn({size}, {size}).cuda()
y = torch.mm(x, x.t())
torch.cuda.synchronize()
end = time.time()
print(f'SIZE_{size}_TIME:{{end-start:.4f}}')
"""],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=60
                )
                
                if timing_result.returncode == 0:
                    performance_data["timing_tests"].append({
                        "size": size,
                        "success": True,
                        "output": timing_result.stdout.strip()
                    })
                else:
                    performance_data["timing_tests"].append({
                        "size": size,
                        "success": False,
                        "error": timing_result.stderr
                    })
            
            # Memory usage
            memory_result = subprocess.run(
                ["python", "-c", "import torch; print(f'MEMORY_ALLOCATED:{torch.cuda.memory_allocated()/1024/1024:.1f}MB') if torch.cuda.is_available() else print('NO_CUDA')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15
            )
            performance_data["memory_usage"] = {
                "success": memory_result.returncode == 0,
                "output": memory_result.stdout.strip() if memory_result.returncode == 0 else memory_result.stderr
            }
            
            workflow["phase4_analysis"] = {"success": True, "data": performance_data}
            execution_log.append("Phase 4 completed successfully")
            
        except Exception as e:
            workflow["phase4_analysis"] = {"success": False, "error": str(e)}
            execution_log.append(f"Phase 4 failed: {str(e)}")
        
        # Phase 5: Summary compilation
        try:
            execution_log.append("Starting Phase 5: Summary Compilation")
            
            # Count successful phases
            successful_phases = sum(1 for phase in workflow.values() if phase and phase.get("success", False))
            total_phases = len([k for k in workflow.keys() if k != "phase5_summary"])
            
            # Analyze computation results
            computation_success = 0
            if workflow["phase3_computation"] and workflow["phase3_computation"]["success"]:
                comp_data = workflow["phase3_computation"]["data"]
                computation_success = sum(1 for test in comp_data.values() if test and test.get("success", False))
            
            # Analyze performance results
            performance_success = 0
            if workflow["phase4_analysis"] and workflow["phase4_analysis"]["success"]:
                perf_data = workflow["phase4_analysis"]["data"]
                performance_success = sum(1 for test in perf_data["timing_tests"] if test.get("success", False))
                if perf_data["memory_usage"] and perf_data["memory_usage"].get("success", False):
                    performance_success += 1
            
            summary = {
                "total_execution_phases": total_phases,
                "successful_phases": successful_phases,
                "phase_success_rate": successful_phases / total_phases if total_phases > 0 else 0,
                "computation_tests_passed": computation_success,
                "performance_tests_passed": performance_success,
                "overall_success": successful_phases == total_phases and computation_success >= 2 and performance_success >= 1,
                "execution_log_length": len(execution_log),
                "complexity_score": len(str(workflow)) + len(str(execution_log))  # Rough complexity measure
            }
            
            workflow["phase5_summary"] = {"success": True, "data": summary}
            execution_log.append("Phase 5 completed successfully")
            
        except Exception as e:
            workflow["phase5_summary"] = {"success": False, "error": str(e)}
            execution_log.append(f"Phase 5 failed: {str(e)}")
        
        return {
            "workflow": workflow,
            "execution_log": execution_log,
            "final_status": workflow["phase5_summary"]["data"] if workflow["phase5_summary"] and workflow["phase5_summary"]["success"] else {"error": "Summary failed"}
        }
    
    print("Testing level 5 complexity...")
    try:
        result = complex_level5()
        print(f"✅ Level 5 completed!")
        
        if "final_status" in result and "overall_success" in result["final_status"]:
            overall_success = result["final_status"]["overall_success"]
            complexity_score = result["final_status"].get("complexity_score", "unknown")
            print(f"Overall success: {overall_success}")
            print(f"Complexity score: {complexity_score}")
            print(f"🎉 Level 5 PASSED (complexity threshold is very high!)")
            return True
        else:
            print(f"✅ Level 5 completed but with issues")
            return True
            
    except Exception as e:
        print(f"❌ Level 5 FAILED: {e}")
        if "result_raw.pkl not found" in str(e):
            print("🎯 Found complexity threshold at Level 5!")
        return False

if __name__ == "__main__":
    print("=== Higher Complexity Threshold Test ===")
    level5_ok = test_level5_higher_complexity()
    
    if level5_ok:
        print("\n⚠️  Level 5 complexity still works - threshold is very high")
    else:
        print("\n✅ Found complexity threshold at Level 5")