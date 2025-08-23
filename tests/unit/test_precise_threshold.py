#!/usr/bin/env python3
"""
Find the precise complexity threshold by testing Levels 2, 3, 4, and 5.
"""

from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


def test_level2_complexity():
    """Test level 2 complexity."""
    load_config("tensor01_config.yml")

    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False

    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={"CUDA_VISIBLE_DEVICES": "0"},
    )

    @cluster(cores=1, memory="2GB")
    def complex_level2():
        """Level 2: Low-medium complexity."""
        import subprocess

        # Two subprocess calls with basic data structure
        results = {}

        # Step 1
        result1 = subprocess.run(
            ["python", "-c", "print('STEP1_COMPLETE')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        results["step1"] = {
            "success": result1.returncode == 0,
            "output": result1.stdout.strip(),
        }

        # Step 2
        result2 = subprocess.run(
            [
                "python",
                "-c",
                "import torch; print(f'GPU_AVAILABLE:{torch.cuda.is_available()}')",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        results["step2"] = {
            "success": result2.returncode == 0,
            "output": result2.stdout.strip(),
        }

        # Simple analysis
        success_count = sum(1 for step in results.values() if step["success"])

        return {
            "results": results,
            "summary": {
                "total_steps": len(results),
                "successful_steps": success_count,
                "overall_success": success_count == len(results),
            },
        }

    print("Testing level 2 complexity...")
    try:
        result = complex_level2()
        print(f"✅ Level 2 result: success={result['summary']['overall_success']}")
        print("🎉 Level 2 PASSED")
        return True
    except Exception as e:
        print(f"❌ Level 2 FAILED: {e}")
        return False


def test_level4_complexity():
    """Test level 4 complexity."""
    load_config("tensor01_config.yml")

    tensor01_creds = credentials.get_tensor01_credentials()
    if not tensor01_creds:
        print("No credentials available")
        return False

    configure(
        password=tensor01_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
        environment_variables={"CUDA_VISIBLE_DEVICES": "0"},
    )

    @cluster(cores=1, memory="4GB")
    def complex_level4():
        """Level 4: High complexity."""
        import subprocess
        import json
        import time

        # Multi-phase workflow with more complex operations
        phases = {
            "phase1_env": None,
            "phase2_gpu": None,
            "phase3_compute": None,
            "phase4_analysis": None,
        }

        metadata = {"start_time": time.time(), "execution_steps": [], "errors": []}

        # Phase 1: Environment check
        try:
            metadata["execution_steps"].append("Starting Phase 1")

            env_result = subprocess.run(
                [
                    "python",
                    "-c",
                    'import os; print(f\'CUDA_ENV:{os.environ.get("CUDA_VISIBLE_DEVICES", "none")}\')',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15,
            )

            phases["phase1_env"] = {
                "success": env_result.returncode == 0,
                "output": env_result.stdout.strip(),
                "timestamp": time.time(),
            }
            metadata["execution_steps"].append("Phase 1 completed")

        except Exception as e:
            phases["phase1_env"] = {"error": str(e)}
            metadata["errors"].append(f"Phase 1: {str(e)}")

        # Phase 2: GPU detection
        try:
            metadata["execution_steps"].append("Starting Phase 2")

            gpu_commands = [
                "import torch; print(f'CUDA_AVAILABLE:{torch.cuda.is_available()}')",
                "import torch; print(f'DEVICE_COUNT:{torch.cuda.device_count()}')",
                "import torch; print(f'DEVICE_NAME:{torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"none\"}')",
            ]

            gpu_results = {}
            for i, cmd in enumerate(gpu_commands):
                gpu_proc = subprocess.run(
                    ["python", "-c", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=20,
                )
                gpu_results[f"cmd_{i}"] = {
                    "success": gpu_proc.returncode == 0,
                    "output": (
                        gpu_proc.stdout.strip()
                        if gpu_proc.returncode == 0
                        else gpu_proc.stderr
                    ),
                }

            phases["phase2_gpu"] = {
                "success": all(r["success"] for r in gpu_results.values()),
                "data": gpu_results,
                "timestamp": time.time(),
            }
            metadata["execution_steps"].append("Phase 2 completed")

        except Exception as e:
            phases["phase2_gpu"] = {"error": str(e)}
            metadata["errors"].append(f"Phase 2: {str(e)}")

        # Phase 3: Computation tests
        try:
            metadata["execution_steps"].append("Starting Phase 3")

            compute_tests = {
                "basic_tensor": "import torch; x=torch.tensor([1.0, 2.0]).cuda(); print(f'TENSOR_SUM:{x.sum().item()}')",
                "matrix_mult": "import torch; a=torch.randn(50,50).cuda(); b=torch.mm(a,a.t()); print(f'TRACE:{b.trace().item():.2f}')",
                "memory_test": "import torch; x=torch.randn(1000,100).cuda(); print(f'MEMORY_USED:{torch.cuda.memory_allocated()/1024/1024:.1f}MB')",
            }

            compute_results = {}
            for test_name, test_code in compute_tests.items():
                comp_proc = subprocess.run(
                    ["python", "-c", test_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                compute_results[test_name] = {
                    "success": comp_proc.returncode == 0,
                    "output": (
                        comp_proc.stdout.strip()
                        if comp_proc.returncode == 0
                        else comp_proc.stderr
                    ),
                    "execution_time": time.time(),
                }

            phases["phase3_compute"] = {
                "success": sum(1 for r in compute_results.values() if r["success"])
                >= 2,
                "data": compute_results,
                "timestamp": time.time(),
            }
            metadata["execution_steps"].append("Phase 3 completed")

        except Exception as e:
            phases["phase3_compute"] = {"error": str(e)}
            metadata["errors"].append(f"Phase 3: {str(e)}")

        # Phase 4: Analysis and summary
        try:
            metadata["execution_steps"].append("Starting Phase 4")

            # Count successes
            successful_phases = sum(
                1 for phase in phases.values() if phase and phase.get("success", False)
            )
            total_phases = len(phases)

            # Analyze compute results
            compute_success_count = 0
            if phases["phase3_compute"] and phases["phase3_compute"]["success"]:
                compute_data = phases["phase3_compute"]["data"]
                compute_success_count = sum(
                    1 for test in compute_data.values() if test["success"]
                )

            # Performance metrics
            total_execution_time = time.time() - metadata["start_time"]

            analysis = {
                "phase_success_rate": successful_phases / total_phases,
                "compute_tests_passed": compute_success_count,
                "total_execution_time": total_execution_time,
                "total_steps": len(metadata["execution_steps"]),
                "error_count": len(metadata["errors"]),
                "overall_success": successful_phases >= 3
                and compute_success_count >= 2,
                "complexity_indicators": {
                    "nested_loops": 3,  # for loops in phase processing
                    "subprocess_calls": 7,  # total subprocess calls
                    "data_structures": 4,  # phases, metadata, results, analysis
                    "conditional_logic": 8,  # various if/else branches
                },
            }

            phases["phase4_analysis"] = {
                "success": True,
                "data": analysis,
                "timestamp": time.time(),
            }
            metadata["execution_steps"].append("Phase 4 completed")

        except Exception as e:
            phases["phase4_analysis"] = {"error": str(e)}
            metadata["errors"].append(f"Phase 4: {str(e)}")

        # Final metadata completion
        metadata["end_time"] = time.time()
        metadata["total_duration"] = metadata["end_time"] - metadata["start_time"]

        return {
            "phases": phases,
            "metadata": metadata,
            "final_success": (
                phases["phase4_analysis"]["data"]["overall_success"]
                if phases["phase4_analysis"] and phases["phase4_analysis"]["success"]
                else False
            ),
        }

    print("Testing level 4 complexity...")
    try:
        result = complex_level4()
        print(
            f"✅ Level 4 completed! Final success: {result.get('final_success', 'unknown')}"
        )
        print("🎉 Level 4 PASSED")
        return True
    except Exception as e:
        print(f"❌ Level 4 FAILED: {e}")
        if "result_raw.pkl not found" in str(e):
            print("🎯 Found complexity threshold at Level 4!")
        return False


def test_slurm_complexity():
    """Test complexity on SLURM cluster (ndoli)."""
    load_config("ndoli_config.yml")

    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        print("No ndoli credentials available")
        return False

    configure(
        password=ndoli_creds.get("password"),
        cleanup_on_success=False,
        job_poll_interval=5,
    )

    @cluster(cores=1, memory="4GB")
    def slurm_complex_test():
        """Complex function on SLURM - test if same threshold applies."""
        import subprocess
        import json
        import time

        # Multi-step SLURM workflow
        workflow = {
            "step1_env": None,
            "step2_system": None,
            "step3_python": None,
            "step4_computation": None,
        }

        # Step 1: Environment
        try:
            env_result = subprocess.run(
                [
                    "python",
                    "-c",
                    'import os; print(f\'SLURM_JOB_ID:{os.environ.get("SLURM_JOB_ID", "none")}\')',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15,
            )
            workflow["step1_env"] = {
                "success": env_result.returncode == 0,
                "output": env_result.stdout.strip(),
            }
        except Exception as e:
            workflow["step1_env"] = {"error": str(e)}

        # Step 2: System info
        try:
            sys_commands = ["hostname", "whoami", "pwd"]
            sys_results = {}
            for cmd in sys_commands:
                sys_proc = subprocess.run(
                    [cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=10,
                )
                sys_results[cmd] = (
                    sys_proc.stdout.strip() if sys_proc.returncode == 0 else "failed"
                )

            workflow["step2_system"] = {"success": True, "data": sys_results}
        except Exception as e:
            workflow["step2_system"] = {"error": str(e)}

        # Step 3: Python analysis
        try:
            python_checks = [
                "import sys; print(f'PYTHON_VERSION:{sys.version_info.major}.{sys.version_info.minor}')",
                "import platform; print(f'PLATFORM:{platform.platform()}')",
                'import os; print(f\'PATH_COUNT:{len(os.environ.get("PATH", "").split(":"))}\')',
            ]

            python_results = {}
            for i, check in enumerate(python_checks):
                py_proc = subprocess.run(
                    ["python", "-c", check],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=15,
                )
                python_results[f"check_{i}"] = {
                    "success": py_proc.returncode == 0,
                    "output": (
                        py_proc.stdout.strip()
                        if py_proc.returncode == 0
                        else py_proc.stderr
                    ),
                }

            workflow["step3_python"] = {
                "success": all(r["success"] for r in python_results.values()),
                "data": python_results,
            }
        except Exception as e:
            workflow["step3_python"] = {"error": str(e)}

        # Step 4: Computational tests
        try:
            compute_commands = [
                "import math; result = sum(math.sqrt(i) for i in range(1000)); print(f'SQRT_SUM:{result:.2f}')",
                "import random; random.seed(42); data = [random.random() for _ in range(100)]; print(f'RANDOM_MEAN:{sum(data)/len(data):.4f}')",
                "import time; start=time.time(); [i**2 for i in range(10000)]; print(f'COMPUTE_TIME:{time.time()-start:.4f}')",
            ]

            compute_results = {}
            for i, cmd in enumerate(compute_commands):
                comp_proc = subprocess.run(
                    ["python", "-c", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                compute_results[f"compute_{i}"] = {
                    "success": comp_proc.returncode == 0,
                    "output": (
                        comp_proc.stdout.strip()
                        if comp_proc.returncode == 0
                        else comp_proc.stderr
                    ),
                }

            workflow["step4_computation"] = {
                "success": sum(1 for r in compute_results.values() if r["success"])
                >= 2,
                "data": compute_results,
            }
        except Exception as e:
            workflow["step4_computation"] = {"error": str(e)}

        # Summary
        successful_steps = sum(
            1 for step in workflow.values() if step and step.get("success", False)
        )

        return {
            "workflow": workflow,
            "summary": {
                "successful_steps": successful_steps,
                "total_steps": len(workflow),
                "overall_success": successful_steps >= 3,
            },
        }

    print("Testing SLURM complexity...")
    try:
        result = slurm_complex_test()
        success = result["summary"]["overall_success"]
        print(f"✅ SLURM test completed! Success: {success}")
        print("🎉 SLURM complexity test PASSED")
        return True
    except Exception as e:
        print(f"❌ SLURM complexity test FAILED: {e}")
        if "result_raw.pkl not found" in str(e):
            print("🎯 SLURM has same complexity threshold issue!")
        return False


if __name__ == "__main__":
    print("=== Precise Complexity Threshold Analysis ===")

    # Test all levels to find exact threshold
    level2_ok = test_level2_complexity()
    level4_ok = test_level4_complexity()
    slurm_ok = test_slurm_complexity()

    print(f"\n=== Results Summary ===")
    print(f"Level 1 (simple): PASS (from previous test)")
    print(f"Level 2 (low-medium): {'PASS' if level2_ok else 'FAIL'}")
    print(f"Level 3 (medium): PASS (from previous test)")
    print(f"Level 4 (high): {'PASS' if level4_ok else 'FAIL'}")
    print(f"Level 5 (very high): FAIL (from previous test)")
    print(f"SLURM complex: {'PASS' if slurm_ok else 'FAIL'}")

    # Determine threshold
    if level2_ok and not level4_ok:
        print(f"\n🎯 Complexity threshold is between Level 3 and Level 4")
    elif level4_ok:
        print(f"\n⚠️  Threshold is between Level 4 and Level 5 (very high)")
    else:
        print(f"\n🎯 Threshold is at or below Level 2")

    # Check if SLURM has same issue
    if not slurm_ok:
        print(f"🔍 SLURM cluster has same complexity limitation")
    else:
        print(f"🔍 SLURM cluster handles complexity better than SSH")
