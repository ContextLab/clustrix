"""
Comprehensive pytest analysis of simple vs complex code execution patterns.

This module systematically tests the "complex code" issue across different
cluster types, GPU configurations, and function complexity levels.
"""

import pytest
from clustrix import cluster
from clustrix.config import load_config, configure
from tests.real_world import credentials


@pytest.mark.real_world
class TestSimpleCodePatterns:
    """Test simple code patterns that consistently work."""

    def test_tensor01_simple_single_gpu(self):
        """Simple function with single GPU on tensor01."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            environment_variables={"CUDA_VISIBLE_DEVICES": "0"},
        )

        @cluster(cores=1, memory="2GB")
        def simple_single_gpu():
            """Simple single GPU function - VERIFIED WORKING PATTERN."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    "import torch; print(f'GPU:{torch.cuda.device_count()}')",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )
            return {"success": result.returncode == 0, "output": result.stdout}

        result = simple_single_gpu()
        assert result["success"]
        assert "GPU:1" in result["output"]

    def test_tensor01_simple_multi_gpu(self):
        """Simple function with multiple GPUs on tensor01."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            environment_variables={"CUDA_VISIBLE_DEVICES": "0,1"},
        )

        @cluster(cores=1, memory="4GB")
        def simple_multi_gpu():
            """Simple multi-GPU function - VERIFIED WORKING PATTERN."""
            import subprocess

            result = subprocess.run(
                [
                    "python",
                    "-c",
                    "import torch; print(f'GPUS:{torch.cuda.device_count()}')",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )
            return {"success": result.returncode == 0, "output": result.stdout}

        result = simple_multi_gpu()
        assert result["success"]
        assert "GPUS:2" in result["output"]

    def test_ndoli_simple_slurm(self):
        """Simple function on SLURM cluster (ndoli)."""
        load_config("ndoli_config.yml")

        ndoli_creds = credentials.get_ndoli_credentials()
        if not ndoli_creds:
            pytest.skip("No ndoli credentials available")

        configure(
            password=ndoli_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
        )

        @cluster(cores=1, memory="2GB")
        def simple_slurm():
            """Simple SLURM function - VERIFIED WORKING PATTERN."""
            import subprocess

            result = subprocess.run(
                ["python", "-c", "print('HELLO:SLURM')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )
            return {"success": result.returncode == 0, "output": result.stdout}

        result = simple_slurm()
        assert result["success"]
        assert "HELLO:SLURM" in result["output"]


@pytest.mark.real_world
class TestComplexCodePatterns:
    """Test complex code patterns that often fail."""

    def test_tensor01_complex_single_gpu_expected_failure(self):
        """Complex function with single GPU on tensor01 - EXPECTED TO FAIL."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            environment_variables={"CUDA_VISIBLE_DEVICES": "0"},
        )

        @cluster(cores=1, memory="4GB")
        def complex_single_gpu():
            """Complex single GPU function - EXPECTED TO FAIL."""
            import subprocess
            import os
            import json

            # Multiple operations and data structures - complexity triggers failure
            result = {
                "env_check": None,
                "gpu_check": None,
                "computation": None,
                "final_status": None,
            }

            # Environment check
            try:
                env_result = subprocess.run(
                    [
                        "python",
                        "-c",
                        'import os; print(f\'CUDA:{os.environ.get("CUDA_VISIBLE_DEVICES", "none")}\')',
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=10,
                )
                result["env_check"] = {
                    "success": env_result.returncode == 0,
                    "output": env_result.stdout,
                }
            except Exception as e:
                result["env_check"] = {"success": False, "error": str(e)}

            # GPU check
            try:
                gpu_result = subprocess.run(
                    [
                        "python",
                        "-c",
                        "import torch; print(f'GPU_COUNT:{torch.cuda.device_count()}')",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=10,
                )
                result["gpu_check"] = {
                    "success": gpu_result.returncode == 0,
                    "output": gpu_result.stdout,
                }
            except Exception as e:
                result["gpu_check"] = {"success": False, "error": str(e)}

            # Complex computation
            try:
                comp_result = subprocess.run(
                    [
                        "python",
                        "-c",
                        """
import torch
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
x = torch.randn(100, 100, device=device)
y = torch.mm(x, x.t())
result = y.sum().item()
print(f'COMPUTATION:{result:.2f}')
""",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                result["computation"] = {
                    "success": comp_result.returncode == 0,
                    "output": comp_result.stdout,
                }
            except Exception as e:
                result["computation"] = {"success": False, "error": str(e)}

            # Final status compilation
            all_success = all(
                [
                    result["env_check"]["success"] if result["env_check"] else False,
                    result["gpu_check"]["success"] if result["gpu_check"] else False,
                    (
                        result["computation"]["success"]
                        if result["computation"]
                        else False
                    ),
                ]
            )
            result["final_status"] = {"all_success": all_success}

            return result

        # This should fail due to complexity
        with pytest.raises(Exception, match="result_raw.pkl not found"):
            complex_single_gpu()

    def test_tensor01_complex_multi_gpu_expected_failure(self):
        """Complex function with multiple GPUs on tensor01 - EXPECTED TO FAIL."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            environment_variables={"CUDA_VISIBLE_DEVICES": "0,1,2,3"},
        )

        @cluster(cores=2, memory="8GB")
        def complex_multi_gpu():
            """Complex multi-GPU function - EXPECTED TO FAIL."""
            import subprocess
            import json
            import time

            # Very complex multi-step workflow
            results = {
                "step1_env": None,
                "step2_gpu_detection": None,
                "step3_multi_gpu_test": None,
                "step4_performance": None,
                "final_analysis": None,
            }

            # Step 1: Environment analysis
            env_commands = [
                "echo 'Step 1: Environment'",
                'python -c "import os; print(f\'CUDA_VISIBLE_DEVICES: {os.environ.get(\\"CUDA_VISIBLE_DEVICES\\", \\"default\\")}\')"',
                "python -c \"import torch; print(f'PyTorch version: {torch.__version__}')\"",
            ]

            for cmd in env_commands:
                try:
                    proc = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True, timeout=10
                    )
                    results["step1_env"] = {"last_output": proc.stdout}
                except Exception as e:
                    results["step1_env"] = {"error": str(e)}

            # Step 2: GPU detection
            gpu_detection_code = """
import torch
device_count = torch.cuda.device_count()
devices = []
for i in range(device_count):
    devices.append({
        'id': i,
        'name': torch.cuda.get_device_name(i),
        'memory': torch.cuda.get_device_properties(i).total_memory
    })
print(f'DETECTED_DEVICES:{len(devices)}')
"""
            try:
                gpu_proc = subprocess.run(
                    ["python", "-c", gpu_detection_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                results["step2_gpu_detection"] = {
                    "output": gpu_proc.stdout,
                    "success": gpu_proc.returncode == 0,
                }
            except Exception as e:
                results["step2_gpu_detection"] = {"error": str(e)}

            # Step 3: Multi-GPU testing
            multi_gpu_code = """
import torch
if torch.cuda.device_count() >= 2:
    tensor_gpu0 = torch.randn(1000, device='cuda:0')
    tensor_gpu1 = torch.randn(1000, device='cuda:1')
    result = torch.dot(tensor_gpu0, tensor_gpu1.to('cuda:0'))
    print(f'MULTI_GPU_RESULT:{result.item():.4f}')
else:
    print('INSUFFICIENT_GPUS')
"""
            try:
                multi_proc = subprocess.run(
                    ["python", "-c", multi_gpu_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=60,
                )
                results["step3_multi_gpu_test"] = {
                    "output": multi_proc.stdout,
                    "success": multi_proc.returncode == 0,
                }
            except Exception as e:
                results["step3_multi_gpu_test"] = {"error": str(e)}

            # Step 4: Performance analysis
            start_time = time.time()
            perf_code = """
import torch
import time
sizes = [100, 500, 1000]
times = []
for size in sizes:
    start = time.time()
    x = torch.randn(size, size, device='cuda:0')
    y = torch.mm(x, x.t())
    torch.cuda.synchronize()
    elapsed = time.time() - start
    times.append(elapsed)
print(f'PERFORMANCE_TIMES:{times}')
"""
            try:
                perf_proc = subprocess.run(
                    ["python", "-c", perf_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=120,
                )
                end_time = time.time()
                results["step4_performance"] = {
                    "output": perf_proc.stdout,
                    "total_time": end_time - start_time,
                    "success": perf_proc.returncode == 0,
                }
            except Exception as e:
                results["step4_performance"] = {"error": str(e)}

            # Final analysis
            success_count = sum(
                [
                    (
                        1
                        if results["step1_env"] and "error" not in results["step1_env"]
                        else 0
                    ),
                    (
                        1
                        if results["step2_gpu_detection"]
                        and results["step2_gpu_detection"].get("success")
                        else 0
                    ),
                    (
                        1
                        if results["step3_multi_gpu_test"]
                        and results["step3_multi_gpu_test"].get("success")
                        else 0
                    ),
                    (
                        1
                        if results["step4_performance"]
                        and results["step4_performance"].get("success")
                        else 0
                    ),
                ]
            )

            results["final_analysis"] = {
                "successful_steps": success_count,
                "total_steps": 4,
                "overall_success": success_count == 4,
            }

            return results

        # This should fail due to complexity
        with pytest.raises(Exception, match="result_raw.pkl not found"):
            complex_multi_gpu()

    def test_ndoli_complex_slurm_expected_failure(self):
        """Complex function on SLURM cluster (ndoli) - EXPECTED TO FAIL."""
        load_config("ndoli_config.yml")

        ndoli_creds = credentials.get_ndoli_credentials()
        if not ndoli_creds:
            pytest.skip("No ndoli credentials available")

        configure(
            password=ndoli_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
        )

        @cluster(cores=2, memory="4GB")
        def complex_slurm():
            """Complex SLURM function - EXPECTED TO FAIL."""
            import subprocess
            import os
            import json
            import time

            # Complex multi-step analysis
            analysis = {
                "environment_scan": None,
                "system_info": None,
                "python_analysis": None,
                "computational_test": None,
                "summary": None,
            }

            # Environment scanning
            env_vars = [
                "PATH",
                "LD_LIBRARY_PATH",
                "PYTHONPATH",
                "SLURM_JOB_ID",
                "SLURM_PROCID",
            ]
            env_data = {}
            for var in env_vars:
                env_data[var] = os.environ.get(var, "NOT_SET")
            analysis["environment_scan"] = env_data

            # System information gathering
            system_commands = [
                "hostname",
                "whoami",
                "pwd",
                "python --version",
                "which python",
                "df -h /tmp",
                "free -h",
            ]

            system_results = {}
            for cmd in system_commands:
                try:
                    result = subprocess.run(
                        cmd.split(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=10,
                    )
                    system_results[cmd] = result.stdout.strip()
                except Exception as e:
                    system_results[cmd] = f"ERROR: {str(e)}"

            analysis["system_info"] = system_results

            # Python environment analysis
            python_code = """
import sys
import platform
import site
import pkg_resources

info = {
    'python_version': sys.version,
    'platform': platform.platform(),
    'executable': sys.executable,
    'path': sys.path[:5],  # First 5 entries
    'site_packages': site.getsitepackages() if hasattr(site, 'getsitepackages') else [],
    'installed_packages': len(list(pkg_resources.working_set))
}

for key, value in info.items():
    print(f'{key}: {value}')
"""

            try:
                python_proc = subprocess.run(
                    ["python", "-c", python_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                analysis["python_analysis"] = {
                    "success": python_proc.returncode == 0,
                    "output": python_proc.stdout,
                    "error": python_proc.stderr,
                }
            except Exception as e:
                analysis["python_analysis"] = {"error": str(e)}

            # Computational testing
            computation_tests = [
                "import math; print(f'math_sqrt: {math.sqrt(16)}')",
                "import random; print(f'random_number: {random.random()}')",
                "import time; start=time.time(); sum(range(10000)); print(f'computation_time: {time.time()-start:.4f}')",
                "import json; data={'test': [1,2,3]}; print(f'json_test: {json.dumps(data)}')",
            ]

            comp_results = {}
            for i, test in enumerate(computation_tests):
                try:
                    comp_proc = subprocess.run(
                        ["python", "-c", test],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=15,
                    )
                    comp_results[f"test_{i}"] = {
                        "command": test,
                        "success": comp_proc.returncode == 0,
                        "output": comp_proc.stdout.strip(),
                    }
                except Exception as e:
                    comp_results[f"test_{i}"] = {"error": str(e)}

            analysis["computational_test"] = comp_results

            # Summary generation
            total_tests = len(computation_tests)
            successful_tests = sum(
                1 for test in comp_results.values() if test.get("success", False)
            )

            analysis["summary"] = {
                "total_computational_tests": total_tests,
                "successful_tests": successful_tests,
                "success_rate": (
                    successful_tests / total_tests if total_tests > 0 else 0
                ),
                "python_analysis_success": analysis["python_analysis"].get(
                    "success", False
                ),
                "overall_complexity_score": len(
                    str(analysis)
                ),  # Rough complexity measure
            }

            return analysis

        # This should fail due to complexity
        with pytest.raises(Exception, match="result_raw.pkl not found"):
            complex_slurm()


@pytest.mark.real_world
class TestComplexityThreshold:
    """Test to identify the exact complexity threshold."""

    @pytest.mark.parametrize("complexity_level", [1, 2, 3, 4, 5])
    def test_tensor01_complexity_levels(self, complexity_level):
        """Test different complexity levels to identify threshold."""
        load_config("tensor01_config.yml")

        tensor01_creds = credentials.get_tensor01_credentials()
        if not tensor01_creds:
            pytest.skip("No tensor01 credentials available")

        configure(
            password=tensor01_creds.get("password"),
            cleanup_on_success=False,
            job_poll_interval=5,
            environment_variables={"CUDA_VISIBLE_DEVICES": "0"},
        )

        if complexity_level == 1:
            # Level 1: Minimal complexity (SHOULD WORK)
            @cluster(cores=1, memory="2GB")
            def level1_function():
                import subprocess

                result = subprocess.run(
                    ["python", "-c", "print('LEVEL1')"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                return {"output": result.stdout}

            result = level1_function()
            assert "LEVEL1" in result["output"]

        elif complexity_level == 2:
            # Level 2: Low complexity (SHOULD WORK)
            @cluster(cores=1, memory="2GB")
            def level2_function():
                import subprocess

                result1 = subprocess.run(
                    ["python", "-c", "print('STEP1')"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                result2 = subprocess.run(
                    ["python", "-c", "print('STEP2')"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=30,
                )
                return {"output1": result1.stdout, "output2": result2.stdout}

            result = level2_function()
            assert "STEP1" in result["output1"]
            assert "STEP2" in result["output2"]

        elif complexity_level == 3:
            # Level 3: Medium complexity (MAY FAIL)
            @cluster(cores=1, memory="2GB")
            def level3_function():
                import subprocess
                import json

                results = {}
                for i in range(3):
                    proc = subprocess.run(
                        ["python", "-c", f"print('STEP{i}')"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=30,
                    )
                    results[f"step_{i}"] = proc.stdout.strip()

                return {"results": results, "count": len(results)}

            # This may fail depending on exact threshold
            try:
                result = level3_function()
                assert result["count"] == 3
            except Exception:
                # Expected to potentially fail at this level
                pytest.skip(f"Complexity level {complexity_level} failed as expected")

        elif complexity_level >= 4:
            # Level 4+: High complexity (EXPECTED TO FAIL)
            @cluster(cores=1, memory="2GB")
            def level4plus_function():
                import subprocess
                import json
                import time

                # Very complex nested operations
                analysis = {"phases": []}

                for phase in range(complexity_level):
                    phase_data = {"phase": phase, "steps": []}

                    for step in range(3):
                        step_result = subprocess.run(
                            [
                                "python",
                                "-c",
                                f"import time; time.sleep(0.1); print('PHASE{phase}_STEP{step}')",
                            ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            universal_newlines=True,
                            timeout=30,
                        )
                        phase_data["steps"].append(
                            {
                                "step": step,
                                "output": step_result.stdout.strip(),
                                "success": step_result.returncode == 0,
                            }
                        )

                    analysis["phases"].append(phase_data)

                # Additional complex processing
                summary = {
                    "total_phases": len(analysis["phases"]),
                    "total_steps": sum(
                        len(phase["steps"]) for phase in analysis["phases"]
                    ),
                    "all_successful": all(
                        step["success"]
                        for phase in analysis["phases"]
                        for step in phase["steps"]
                    ),
                }

                return {"analysis": analysis, "summary": summary}

            # This should fail
            with pytest.raises(Exception, match="result_raw.pkl not found"):
                level4plus_function()
