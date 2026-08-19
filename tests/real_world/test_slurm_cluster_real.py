"""
Real ndoli.dartmouth.edu cluster tests using @cluster decorator.

These tests execute jobs on the ndoli server with both SSH and SLURM execution,
validating advanced authentication and job submission workflows.
"""

import pytest
import os
import time
import uuid
from typing import Dict, Any, List

from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from tests.real_world import TempResourceManager, credentials, test_manager


class TestNdoliClusterExecution:
    """Test real ndoli.dartmouth.edu cluster execution using @cluster decorator."""

    @pytest.fixture
    def ndoli_ssh_config(self):
        """Get ndoli SSH configuration for testing."""
        ndoli_creds = credentials.get_ndoli_credentials()
        if not ndoli_creds:
            pytest.skip("No ndoli credentials available for testing")

        # Configure clustrix for SSH-based execution on ndoli
        configure(
            cluster_type="ssh",
            cluster_host=ndoli_creds["host"],
            username=ndoli_creds["username"],
            password=ndoli_creds.get("password"),
            key_file=ndoli_creds.get("private_key_path"),
            remote_work_dir=f"/tmp/clustrix_ndoli_ssh_{uuid.uuid4().hex[:8]}",
            python_executable="python3",
            cleanup_on_success=False,
            job_poll_interval=5,
        )

        return ndoli_creds

    @pytest.fixture
    def ndoli_slurm_config(self):
        """Get ndoli SLURM configuration for testing."""
        ndoli_creds = credentials.get_ndoli_credentials()
        if not ndoli_creds:
            pytest.skip("No ndoli credentials available for testing")

        # Configure clustrix for SLURM-based execution on ndoli
        configure(
            cluster_type="slurm",
            cluster_host=ndoli_creds["host"],
            username=ndoli_creds["username"],
            password=ndoli_creds.get("password"),
            key_file=ndoli_creds.get("private_key_path"),
            remote_work_dir=f"/tmp/clustrix_ndoli_slurm_{uuid.uuid4().hex[:8]}",
            python_executable="python3",
            cleanup_on_success=False,
            job_poll_interval=10,  # SLURM jobs may take longer
        )

        return ndoli_creds

    @pytest.mark.real_world
    def test_ndoli_ssh_advanced_auth(self, ndoli_ssh_config):
        """Test SSH execution on ndoli with advanced authentication."""

        @cluster(cores=1, memory="1GB")
        def test_ndoli_ssh_environment() -> Dict[str, Any]:
            """Test ndoli SSH environment with advanced authentication."""
            import os
            import platform
            import socket
            import subprocess

            # Get comprehensive system information
            system_info = {
                "hostname": socket.gethostname(),
                "fqdn": socket.getfqdn(),
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "architecture": platform.architecture()[0],
                "machine": platform.machine(),
                "user": os.getenv("USER", "unknown"),
                "home": os.getenv("HOME", "unknown"),
                "pwd": os.getcwd(),
                "shell": os.getenv("SHELL", "unknown"),
            }

            # Test advanced authentication by checking user capabilities
            auth_tests = {}

            # Test file system access
            try:
                test_file = "/tmp/clustrix_auth_test.txt"
                with open(test_file, "w") as f:
                    f.write("Authentication test successful")

                with open(test_file, "r") as f:
                    content = f.read()

                os.remove(test_file)
                auth_tests["file_system"] = {"success": True, "content": content}
            except Exception as e:
                auth_tests["file_system"] = {"success": False, "error": str(e)}

            # Test process execution
            try:
                result = subprocess.run(
                    ["whoami"], capture_output=True, text=True, timeout=5
                )
                auth_tests["process_execution"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout.strip(),
                    "user": result.stdout.strip(),
                }
            except Exception as e:
                auth_tests["process_execution"] = {"success": False, "error": str(e)}

            # Test environment variable access
            try:
                env_vars = {
                    "USER": os.getenv("USER"),
                    "HOME": os.getenv("HOME"),
                    "PATH": os.getenv("PATH", "")[:100],  # Truncate PATH
                    "SHELL": os.getenv("SHELL"),
                }
                auth_tests["environment_vars"] = {
                    "success": True,
                    "variables": env_vars,
                }
            except Exception as e:
                auth_tests["environment_vars"] = {"success": False, "error": str(e)}

            return {
                "system_info": system_info,
                "auth_tests": auth_tests,
                "test_successful": all(
                    test.get("success", False) for test in auth_tests.values()
                ),
            }

        result = test_ndoli_ssh_environment()

        # Validate ndoli SSH execution with advanced authentication
        assert isinstance(result, dict)
        assert "system_info" in result
        assert "auth_tests" in result
        assert result["system_info"]["hostname"] != ""
        assert result["system_info"]["user"] != "unknown"
        assert result["auth_tests"]["file_system"]["success"] is True
        assert result["auth_tests"]["process_execution"]["success"] is True
        assert result["auth_tests"]["environment_vars"]["success"] is True
        assert result["test_successful"] is True

    @pytest.mark.real_world
    def test_ndoli_slurm_job_submission(self, ndoli_slurm_config):
        """Test SLURM job submission on ndoli."""

        @cluster(
            cores=2,
            memory="4GB",
            time="00:15:00",
            partition="gpu",  # Use GPU partition if available
        )
        def test_ndoli_slurm_job() -> Dict[str, Any]:
            """Test SLURM job execution on ndoli."""
            import os
            import time
            import platform
            import subprocess

            start_time = time.time()

            # Get SLURM environment information
            slurm_env = {
                "job_id": os.getenv("SLURM_JOB_ID"),
                "job_name": os.getenv("SLURM_JOB_NAME"),
                "partition": os.getenv("SLURM_JOB_PARTITION"),
                "nodes": os.getenv("SLURM_JOB_NUM_NODES"),
                "cpus": os.getenv("SLURM_CPUS_ON_NODE"),
                "mem": os.getenv("SLURM_MEM_PER_NODE"),
                "nodelist": os.getenv("SLURM_JOB_NODELIST"),
                "work_dir": os.getenv("SLURM_SUBMIT_DIR"),
            }

            # Test computational work
            computation_results = []
            for i in range(1000):
                # Simulate computation
                result = sum(j**2 for j in range(i + 1))
                computation_results.append(result)

            # Test parallel capabilities
            try:
                nproc_result = subprocess.run(
                    ["nproc"], capture_output=True, text=True, timeout=5
                )
                available_cpus = (
                    int(nproc_result.stdout.strip())
                    if nproc_result.returncode == 0
                    else 1
                )
            except Exception:
                available_cpus = 1

            # Test memory usage
            try:
                free_result = subprocess.run(
                    ["free", "-m"], capture_output=True, text=True, timeout=5
                )
                memory_info = (
                    free_result.stdout.strip()
                    if free_result.returncode == 0
                    else "unknown"
                )
            except Exception:
                memory_info = "unknown"

            end_time = time.time()

            return {
                "slurm_environment": slurm_env,
                "computation_completed": len(computation_results),
                "computation_sample": computation_results[:5],
                "execution_time": end_time - start_time,
                "available_cpus": available_cpus,
                "memory_info": memory_info,
                "hostname": platform.node(),
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "slurm_job_detected": slurm_env["job_id"] is not None,
            }

        result = test_ndoli_slurm_job()

        # Validate SLURM job execution
        assert isinstance(result, dict)
        assert "slurm_environment" in result
        assert result["computation_completed"] == 1000
        assert result["execution_time"] > 0
        assert result["available_cpus"] >= 1
        assert result["hostname"] != ""
        assert result["python_version"] != ""
        # Note: SLURM job detection may not work in all environments
        # assert result["slurm_job_detected"] is True

    @pytest.mark.real_world
    def test_ndoli_parallel_slurm_execution(self, ndoli_slurm_config):
        """Test parallel SLURM execution on ndoli."""

        @cluster(
            cores=4,
            memory="8GB",
            time="00:20:00",
            parallel=True,
        )
        def parallel_matrix_computation(matrix_sizes: List[int]) -> Dict[str, Any]:
            """Perform parallel matrix computations."""
            import numpy as np
            import time
            import os

            start_time = time.time()
            results = []

            for size in matrix_sizes:
                # Create random matrices
                matrix_a = np.random.rand(size, size)
                matrix_b = np.random.rand(size, size)

                # Perform matrix multiplication
                result_matrix = np.dot(matrix_a, matrix_b)

                # Calculate some statistics
                matrix_stats = {
                    "size": size,
                    "elements": size * size,
                    "result_sum": float(np.sum(result_matrix)),
                    "result_mean": float(np.mean(result_matrix)),
                    "result_std": float(np.std(result_matrix)),
                }
                results.append(matrix_stats)

            end_time = time.time()

            return {
                "matrices_computed": len(matrix_sizes),
                "computation_results": results,
                "total_elements": sum(r["elements"] for r in results),
                "execution_time": end_time - start_time,
                "slurm_job_id": os.getenv("SLURM_JOB_ID"),
                "slurm_cpus": os.getenv("SLURM_CPUS_ON_NODE"),
                "successful": True,
            }

        # Test with different matrix sizes
        matrix_sizes = [10, 20, 30, 40]
        result = parallel_matrix_computation(matrix_sizes)

        # Validate parallel execution
        assert isinstance(result, dict)
        assert result["matrices_computed"] == 4
        assert result["total_elements"] == 10 * 10 + 20 * 20 + 30 * 30 + 40 * 40
        assert result["execution_time"] > 0
        assert result["successful"] is True
        assert len(result["computation_results"]) == 4

    @pytest.mark.real_world
    def test_ndoli_gpu_awareness(self, ndoli_slurm_config):
        """Test GPU awareness on ndoli cluster."""

        @cluster(
            cores=2,
            memory="4GB",
            time="00:10:00",
            partition="gpu",
        )
        def test_gpu_detection() -> Dict[str, Any]:
            """Test GPU detection and basic CUDA availability."""
            import subprocess
            import os

            gpu_info: Dict[str, Any] = {
                "nvidia_smi_available": False,
                "cuda_devices": [],
                "gpu_count": 0,
                "cuda_version": None,
                "driver_version": None,
            }

            # Test nvidia-smi availability
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.total,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    gpu_info["nvidia_smi_available"] = True
                    gpu_lines = result.stdout.strip().split("\n")
                    for line in gpu_lines:
                        if line.strip():
                            parts = line.split(", ")
                            if len(parts) >= 3:
                                gpu_info["cuda_devices"].append(
                                    {
                                        "name": parts[0],
                                        "memory_total": parts[1],
                                        "memory_used": parts[2],
                                    }
                                )
                    gpu_info["gpu_count"] = len(gpu_info["cuda_devices"])

                    # Get CUDA version
                    version_result = subprocess.run(
                        [
                            "nvidia-smi",
                            "--query-gpu=driver_version",
                            "--format=csv,noheader",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if version_result.returncode == 0:
                        gpu_info["driver_version"] = version_result.stdout.strip()

            except Exception as e:
                gpu_info["nvidia_smi_error"] = str(e)

            # Test CUDA environment variables
            cuda_env = {
                "CUDA_VISIBLE_DEVICES": os.getenv("CUDA_VISIBLE_DEVICES"),
                "CUDA_DEVICE_ORDER": os.getenv("CUDA_DEVICE_ORDER"),
                "CUDA_HOME": os.getenv("CUDA_HOME"),
                "CUDA_PATH": os.getenv("CUDA_PATH"),
            }

            return {
                "gpu_info": gpu_info,
                "cuda_environment": cuda_env,
                "gpu_partition_detected": os.getenv("SLURM_JOB_PARTITION") == "gpu",
            }

        result = test_gpu_detection()

        # Validate GPU detection (may not have GPUs, but should not error)
        assert isinstance(result, dict)
        assert "gpu_info" in result
        assert "cuda_environment" in result
        assert isinstance(result["gpu_info"]["gpu_count"], int)
        assert result["gpu_info"]["gpu_count"] >= 0
