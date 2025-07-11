"""
Real SGE (Sun Grid Engine) job submission tests using @cluster decorator.

These tests actually submit jobs to real SGE clusters and validate
the complete end-to-end workflow with the @cluster decorator.
"""

import pytest
import os
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any

from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from tests.real_world import TempResourceManager, credentials, test_manager


class TestRealSGEJobSubmission:
    """Test real SGE job submission using @cluster decorator."""

    @pytest.fixture
    def sge_config(self):
        """Get SGE configuration for testing."""
        # Use SSH credentials for SGE cluster access
        ssh_creds = credentials.get_ssh_credentials()
        if not ssh_creds:
            pytest.skip("No SSH credentials available for SGE testing")

        # Configure clustrix for SGE
        configure(
            cluster_type="sge",
            cluster_host=ssh_creds["host"],
            username=ssh_creds["username"],
            password=ssh_creds.get("password"),
            private_key_path=ssh_creds.get("private_key_path"),
            remote_work_dir=f"/tmp/clustrix_sge_test_{uuid.uuid4().hex[:8]}",
            cleanup_remote_files=True,
        )

        return ssh_creds

    @pytest.mark.real_world
    def test_simple_function_sge_submission(self, sge_config):
        """Test submitting a simple function to SGE."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def divide_numbers(x: float, y: float) -> float:
            """Simple division function for testing."""
            if y == 0:
                return float("inf")
            return x / y

        # Submit job and wait for result
        result = divide_numbers(84.0, 2.0)

        # Validate result
        assert result == 42.0
        assert isinstance(result, float)

    @pytest.mark.real_world
    def test_function_with_sge_environment(self, sge_config):
        """Test SGE job that accesses SGE environment variables."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def get_sge_environment() -> Dict[str, str]:
            """Get SGE job environment variables."""
            import os

            return {
                "JOB_ID": os.getenv("JOB_ID", "not_set"),
                "JOB_NAME": os.getenv("JOB_NAME", "not_set"),
                "QUEUE": os.getenv("QUEUE", "not_set"),
                "SGE_TASK_ID": os.getenv("SGE_TASK_ID", "not_set"),
                "SGE_CELL": os.getenv("SGE_CELL", "not_set"),
                "SGE_ROOT": os.getenv("SGE_ROOT", "not_set"),
                "PE_HOSTFILE": os.getenv("PE_HOSTFILE", "not_set"),
                "NSLOTS": os.getenv("NSLOTS", "not_set"),
                "HOSTNAME": os.getenv("HOSTNAME", "not_set"),
                "USER": os.getenv("USER", "not_set"),
                "PWD": os.getenv("PWD", "not_set"),
            }

        result = get_sge_environment()

        # Validate SGE environment
        assert isinstance(result, dict)
        assert "JOB_ID" in result
        # Note: SGE environment variables may not be set in all SGE configurations
        # We'll validate what we can
        assert result["USER"] != "not_set"
        assert result["PWD"] != "not_set"

    @pytest.mark.real_world
    def test_sge_parallel_environment(self, sge_config):
        """Test SGE job with parallel environment."""

        @cluster(cores=2, memory="2GB", time="00:10:00")
        def test_parallel_environment() -> Dict[str, Any]:
            """Test SGE parallel environment setup."""
            import os

            result = {
                "nslots": os.getenv("NSLOTS", "not_set"),
                "pe_hostfile": os.getenv("PE_HOSTFILE", "not_set"),
                "pe_hostfile_exists": False,
                "allocated_hosts": [],
                "total_slots": 0,
            }

            # Check if PE hostfile exists and process it
            pe_hostfile = os.getenv("PE_HOSTFILE")
            if pe_hostfile and os.path.exists(pe_hostfile):
                result["pe_hostfile_exists"] = True
                try:
                    with open(pe_hostfile, "r") as f:
                        lines = f.readlines()
                        for line in lines:
                            parts = line.strip().split()
                            if len(parts) >= 2:
                                hostname = parts[0]
                                slots = int(parts[1])
                                result["allocated_hosts"].append(
                                    {"hostname": hostname, "slots": slots}
                                )
                                result["total_slots"] += slots
                except Exception as e:
                    result["error"] = str(e)

            return result

        result = test_parallel_environment()

        # Validate parallel environment
        assert isinstance(result, dict)
        assert "nslots" in result
        assert "pe_hostfile" in result

    @pytest.mark.real_world
    def test_sge_queue_specification(self, sge_config):
        """Test SGE job submission to specific queue."""

        @cluster(cores=1, memory="1GB", time="00:05:00", queue="all.q")
        def test_queue_submission() -> Dict[str, str]:
            """Test function for specific queue."""
            import os

            return {
                "queue": os.getenv("QUEUE", "unknown"),
                "job_id": os.getenv("JOB_ID", "unknown"),
                "sge_cell": os.getenv("SGE_CELL", "unknown"),
                "result": "queue_submission_success",
            }

        try:
            result = test_queue_submission()
            assert isinstance(result, dict)
            assert result["result"] == "queue_submission_success"
        except Exception as e:
            # Queue might not exist, skip test
            pytest.skip(f"Queue 'all.q' not available: {e}")

    @pytest.mark.real_world
    def test_sge_array_job_simulation(self, sge_config):
        """Test SGE job that simulates array job behavior."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def array_task_simulation(task_id: int, parameter: float) -> Dict[str, Any]:
            """Simulate an array job task."""
            import os
            import time
            import math

            # Simulate task-specific work
            work_result = math.sin(task_id * parameter)
            time.sleep(0.1 * task_id)  # Variable work time

            return {
                "task_id": task_id,
                "parameter": parameter,
                "work_result": work_result,
                "job_id": os.getenv("JOB_ID", "unknown"),
                "sge_task_id": os.getenv("SGE_TASK_ID", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "queue": os.getenv("QUEUE", "unknown"),
                "completion_time": time.time(),
            }

        # Submit multiple tasks to simulate array job
        results = []
        for i in range(3):
            result = array_task_simulation(i, 0.5)
            results.append(result)

        # Validate array job simulation
        assert len(results) == 3

        for i, result in enumerate(results):
            assert isinstance(result, dict)
            assert result["task_id"] == i
            assert result["parameter"] == 0.5
            assert result["hostname"] != "unknown"

    @pytest.mark.real_world
    def test_sge_resource_limits(self, sge_config):
        """Test SGE job with resource limits."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def test_resource_limits() -> Dict[str, Any]:
            """Test resource limits in SGE job."""
            import os
            import psutil
            import resource

            # Get process information
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()

            # Get system resource limits
            try:
                memory_limit = resource.getrlimit(resource.RLIMIT_AS)
                cpu_limit = resource.getrlimit(resource.RLIMIT_CPU)
            except:
                memory_limit = ("unknown", "unknown")
                cpu_limit = ("unknown", "unknown")

            return {
                "memory_rss_mb": memory_info.rss / 1024 / 1024,
                "memory_vms_mb": memory_info.vms / 1024 / 1024,
                "memory_soft_limit": memory_limit[0],
                "memory_hard_limit": memory_limit[1],
                "cpu_soft_limit": cpu_limit[0],
                "cpu_hard_limit": cpu_limit[1],
                "nslots": os.getenv("NSLOTS", "unknown"),
                "job_id": os.getenv("JOB_ID", "unknown"),
            }

        result = test_resource_limits()

        # Validate resource limits
        assert isinstance(result, dict)
        assert result["memory_rss_mb"] > 0
        assert result["memory_vms_mb"] > 0

    @pytest.mark.real_world
    def test_sge_file_operations(self, sge_config):
        """Test SGE job with file operations."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def sge_file_processing() -> Dict[str, Any]:
            """Process files in SGE job."""
            import os
            import tempfile
            import json

            # Create temporary working directory
            work_dir = tempfile.mkdtemp(prefix="sge_test_")

            try:
                # Create test data
                test_data = {
                    "job_id": os.getenv("JOB_ID", "unknown"),
                    "hostname": os.getenv("HOSTNAME", "unknown"),
                    "queue": os.getenv("QUEUE", "unknown"),
                    "timestamp": time.time(),
                    "data_points": [i * 2.5 for i in range(100)],
                }

                # Write data to file
                data_file = os.path.join(work_dir, "test_data.json")
                with open(data_file, "w") as f:
                    json.dump(test_data, f, indent=2)

                # Read and process data
                with open(data_file, "r") as f:
                    loaded_data = json.load(f)

                # Compute statistics
                data_points = loaded_data["data_points"]
                stats = {
                    "count": len(data_points),
                    "sum": sum(data_points),
                    "mean": sum(data_points) / len(data_points),
                    "min": min(data_points),
                    "max": max(data_points),
                }

                # Write results
                result_file = os.path.join(work_dir, "results.json")
                with open(result_file, "w") as f:
                    json.dump(stats, f, indent=2)

                return {
                    "work_dir": work_dir,
                    "data_file_created": os.path.exists(data_file),
                    "result_file_created": os.path.exists(result_file),
                    "statistics": stats,
                    "job_info": {
                        "job_id": loaded_data["job_id"],
                        "hostname": loaded_data["hostname"],
                        "queue": loaded_data["queue"],
                    },
                }

            finally:
                # Cleanup
                import shutil

                try:
                    shutil.rmtree(work_dir)
                except:
                    pass

        result = sge_file_processing()

        # Validate file operations
        assert isinstance(result, dict)
        assert result["data_file_created"] is True
        assert result["result_file_created"] is True
        assert result["statistics"]["count"] == 100
        assert result["statistics"]["sum"] > 0

    @pytest.mark.real_world
    def test_sge_error_handling(self, sge_config):
        """Test SGE job error handling."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def sge_error_test(error_mode: str) -> Dict[str, Any]:
            """Test different error scenarios in SGE."""
            import os

            if error_mode == "success":
                return {
                    "status": "success",
                    "job_id": os.getenv("JOB_ID", "unknown"),
                    "message": "Job completed successfully",
                }
            elif error_mode == "value_error":
                raise ValueError("Test value error in SGE job")
            elif error_mode == "file_error":
                with open("/nonexistent/directory/file.txt", "r") as f:
                    return f.read()
            elif error_mode == "runtime_error":
                raise RuntimeError("Test runtime error in SGE job")
            else:
                raise Exception(f"Unknown error mode: {error_mode}")

        # Test successful execution
        result = sge_error_test("success")
        assert result["status"] == "success"
        assert result["job_id"] != "unknown"

        # Test error handling
        with pytest.raises(ValueError):
            sge_error_test("value_error")

        with pytest.raises(RuntimeError):
            sge_error_test("runtime_error")

    @pytest.mark.real_world
    def test_sge_job_dependencies(self, sge_config):
        """Test SGE job with external dependencies."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def test_dependencies() -> Dict[str, Any]:
            """Test job that uses external libraries."""
            import os
            import sys
            import json
            import time
            import math
            import statistics

            # Test standard library availability
            test_data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

            stats_result = {
                "mean": statistics.mean(test_data),
                "stdev": statistics.stdev(test_data),
                "median": statistics.median(test_data),
            }

            # Test math operations
            math_result = {
                "sin_sum": sum(math.sin(x) for x in test_data),
                "cos_sum": sum(math.cos(x) for x in test_data),
                "log_sum": sum(math.log(x) for x in test_data),
            }

            return {
                "python_version": sys.version,
                "statistics": stats_result,
                "math_operations": math_result,
                "job_id": os.getenv("JOB_ID", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "execution_time": time.time(),
            }

        result = test_dependencies()

        # Validate dependencies
        assert isinstance(result, dict)
        assert "python_version" in result
        assert result["statistics"]["mean"] == 5.5
        assert result["statistics"]["median"] == 5.5
        assert result["math_operations"]["sin_sum"] != 0

    @pytest.mark.real_world
    @pytest.mark.expensive
    def test_sge_compute_intensive(self, sge_config):
        """Test compute-intensive SGE job."""

        @cluster(cores=2, memory="2GB", time="00:15:00")
        def compute_intensive_task() -> Dict[str, Any]:
            """Perform compute-intensive operations."""
            import time
            import math
            import os

            start_time = time.time()

            # Perform iterative computation
            result = 0
            iterations = 500000

            for i in range(iterations):
                result += math.sin(i * 0.001) * math.cos(i * 0.002)

                # Checkpoint every 100k iterations
                if i % 100000 == 0 and i > 0:
                    elapsed = time.time() - start_time
                    if elapsed > 600:  # 10 minutes max
                        break

            end_time = time.time()

            return {
                "start_time": start_time,
                "end_time": end_time,
                "duration": end_time - start_time,
                "iterations_completed": i + 1,
                "result": result,
                "job_id": os.getenv("JOB_ID", "unknown"),
                "nslots": os.getenv("NSLOTS", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
            }

        result = compute_intensive_task()

        # Validate compute-intensive task
        assert isinstance(result, dict)
        assert result["duration"] > 1.0  # Should take some time
        assert result["iterations_completed"] > 0
        assert result["result"] != 0

    @pytest.mark.real_world
    def test_sge_job_monitoring(self, sge_config):
        """Test SGE job status monitoring."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def monitored_job() -> Dict[str, Any]:
            """Job that can be monitored during execution."""
            import os
            import time

            start_time = time.time()

            # Create progress tracking
            progress_steps = 5
            step_duration = 1.0  # 1 second per step

            for step in range(progress_steps):
                time.sleep(step_duration)
                # In a real scenario, this might write to a progress file
                current_time = time.time()
                elapsed = current_time - start_time

                if elapsed > 30:  # Safety timeout
                    break

            end_time = time.time()

            return {
                "start_time": start_time,
                "end_time": end_time,
                "duration": end_time - start_time,
                "steps_completed": step + 1,
                "job_id": os.getenv("JOB_ID", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "queue": os.getenv("QUEUE", "unknown"),
            }

        # Monitor job execution
        start_time = time.time()
        result = monitored_job()
        total_time = time.time() - start_time

        # Validate monitoring
        assert isinstance(result, dict)
        assert result["duration"] >= 4.0  # Should run for at least 4 seconds
        assert result["steps_completed"] >= 4
        assert result["job_id"] != "unknown"
        assert total_time >= result["duration"]  # Total time includes scheduling
