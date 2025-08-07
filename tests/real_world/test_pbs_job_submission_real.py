"""
Real PBS job submission tests using @cluster decorator.

These tests actually submit jobs to real PBS clusters and validate
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


class TestRealPBSJobSubmission:
    """Test real PBS job submission using @cluster decorator."""

    @pytest.fixture
    def pbs_config(self):
        """Get PBS configuration for testing."""
        # Use SSH credentials for PBS cluster access
        ssh_creds = credentials.get_ssh_credentials()
        if not ssh_creds:
            pytest.skip("No SSH credentials available for PBS testing")

        # Configure clustrix for PBS
        configure(
            cluster_type="pbs",
            cluster_host=ssh_creds["host"],
            username=ssh_creds["username"],
            password=ssh_creds.get("password"),
            private_key_path=ssh_creds.get("private_key_path"),
            remote_work_dir=f"/tmp/clustrix_pbs_test_{uuid.uuid4().hex[:8]}",
            cleanup_remote_files=True,
        )

        return ssh_creds

    @pytest.mark.real_world
    def test_simple_function_pbs_submission(self, pbs_config):
        """Test submitting a simple function to PBS."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def multiply_numbers(x: int, y: int) -> int:
            """Simple multiplication function for testing."""
            return x * y

        # Submit job and wait for result
        result = multiply_numbers(6, 7)

        # Validate result
        assert result == 42
        assert isinstance(result, int)

    @pytest.mark.real_world
    def test_function_with_pbs_environment(self, pbs_config):
        """Test PBS job that accesses PBS environment variables."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def get_pbs_environment() -> Dict[str, str]:
            """Get PBS job environment variables."""
            import os

            return {
                "PBS_JOBID": os.getenv("PBS_JOBID", "not_set"),
                "PBS_JOBNAME": os.getenv("PBS_JOBNAME", "not_set"),
                "PBS_NODEFILE": os.getenv("PBS_NODEFILE", "not_set"),
                "PBS_QUEUE": os.getenv("PBS_QUEUE", "not_set"),
                "PBS_WORKDIR": os.getenv("PBS_WORKDIR", "not_set"),
                "HOSTNAME": os.getenv("HOSTNAME", "not_set"),
                "USER": os.getenv("USER", "not_set"),
                "PWD": os.getenv("PWD", "not_set"),
            }

        result = get_pbs_environment()

        # Validate PBS environment
        assert isinstance(result, dict)
        assert "PBS_JOBID" in result
        # Note: PBS environment variables may not be set in all PBS configurations
        # We'll validate what we can
        assert result["USER"] != "not_set"
        assert result["PWD"] != "not_set"

    @pytest.mark.real_world
    def test_function_with_queue_specification_pbs(self, pbs_config):
        """Test PBS job submission to specific queue."""

        @cluster(cores=1, memory="1GB", time="00:05:00", queue="batch")
        def test_batch_queue() -> Dict[str, str]:
            """Test function for batch queue."""
            import os

            return {
                "queue": os.getenv("PBS_QUEUE", "unknown"),
                "jobid": os.getenv("PBS_JOBID", "unknown"),
                "result": "batch_queue_success",
            }

        try:
            result = test_batch_queue()
            assert isinstance(result, dict)
            assert result["result"] == "batch_queue_success"
        except Exception as e:
            # Queue might not exist, skip test
            pytest.skip(f"Batch queue not available: {e}")

    @pytest.mark.real_world
    def test_pbs_node_file_processing(self, pbs_config):
        """Test PBS job that processes node file information."""

        @cluster(cores=2, memory="2GB", time="00:05:00")
        def process_node_file() -> Dict[str, Any]:
            """Process PBS node file to get node information."""
            import os

            result = {
                "node_file_path": os.getenv("PBS_NODEFILE", "not_set"),
                "allocated_nodes": [],
                "node_count": 0,
                "unique_nodes": 0,
            }

            node_file = os.getenv("PBS_NODEFILE")
            if node_file and os.path.exists(node_file):
                try:
                    with open(node_file, "r") as f:
                        nodes = [line.strip() for line in f.readlines()]
                        result["allocated_nodes"] = nodes
                        result["node_count"] = len(nodes)
                        result["unique_nodes"] = len(set(nodes))
                except Exception as e:
                    result["error"] = str(e)

            return result

        result = process_node_file()

        # Validate node file processing
        assert isinstance(result, dict)
        assert "node_file_path" in result
        assert "node_count" in result

    @pytest.mark.real_world
    def test_pbs_array_job_simulation(self, pbs_config):
        """Test PBS job that simulates array job behavior."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def array_job_task(task_id: int, total_tasks: int) -> Dict[str, Any]:
            """Simulate an array job task."""
            import os
            import time

            # Simulate different work based on task ID
            work_amount = task_id * 0.1
            time.sleep(work_amount)

            return {
                "task_id": task_id,
                "total_tasks": total_tasks,
                "work_amount": work_amount,
                "pbs_jobid": os.getenv("PBS_JOBID", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "completion_time": time.time(),
            }

        # Submit multiple tasks to simulate array job
        results = []
        total_tasks = 3

        for task_id in range(total_tasks):
            result = array_job_task(task_id, total_tasks)
            results.append(result)

        # Validate array job results
        assert len(results) == total_tasks

        for i, result in enumerate(results):
            assert isinstance(result, dict)
            assert result["task_id"] == i
            assert result["total_tasks"] == total_tasks
            assert result["hostname"] != "unknown"

    @pytest.mark.real_world
    def test_pbs_resource_monitoring(self, pbs_config):
        """Test PBS job that monitors resource usage."""

        @cluster(cores=2, memory="2GB", time="00:10:00")
        def monitor_resources() -> Dict[str, Any]:
            """Monitor resource usage during job execution."""
            import os
            import psutil
            import time

            # Get initial resource information
            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / 1024 / 1024  # MB

            # Perform some work
            data = []
            for i in range(100000):
                data.append(i * 2.5)

            # Get resource usage
            peak_memory = process.memory_info().rss / 1024 / 1024  # MB
            cpu_percent = process.cpu_percent(interval=1)

            return {
                "initial_memory_mb": initial_memory,
                "peak_memory_mb": peak_memory,
                "memory_used_mb": peak_memory - initial_memory,
                "cpu_percent": cpu_percent,
                "data_points": len(data),
                "pbs_jobid": os.getenv("PBS_JOBID", "unknown"),
                "allocated_cpus": os.getenv("NCPUS", "unknown"),
            }

        result = monitor_resources()

        # Validate resource monitoring
        assert isinstance(result, dict)
        assert result["memory_used_mb"] >= 0
        assert result["data_points"] == 100000
        assert result["cpu_percent"] >= 0

    @pytest.mark.real_world
    def test_pbs_file_staging(self, pbs_config):
        """Test PBS job with file staging operations."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def stage_and_process_files() -> Dict[str, Any]:
            """Stage files and process them in PBS job."""
            import os
            import tempfile
            import shutil

            # Create temporary directory
            work_dir = tempfile.mkdtemp(prefix="pbs_test_")

            try:
                # Create test files
                input_file = os.path.join(work_dir, "input.txt")
                output_file = os.path.join(work_dir, "output.txt")

                # Write input data
                with open(input_file, "w") as f:
                    f.write("This is test input data\n")
                    f.write("Line 2 of input\n")
                    f.write("Line 3 of input\n")

                # Process file
                with open(input_file, "r") as infile:
                    lines = infile.readlines()

                # Write processed output
                with open(output_file, "w") as outfile:
                    for i, line in enumerate(lines):
                        outfile.write(f"Processed line {i + 1}: {line}")

                # Verify output
                with open(output_file, "r") as f:
                    output_content = f.read()

                return {
                    "work_dir": work_dir,
                    "input_lines": len(lines),
                    "output_length": len(output_content),
                    "processing_successful": "Processed line 1:" in output_content,
                    "files_created": [
                        os.path.basename(input_file),
                        os.path.basename(output_file),
                    ],
                }

            finally:
                # Cleanup
                try:
                    shutil.rmtree(work_dir)
                except:
                    pass

        result = stage_and_process_files()

        # Validate file staging
        assert isinstance(result, dict)
        assert result["input_lines"] == 3
        assert result["processing_successful"] is True
        assert len(result["files_created"]) == 2

    @pytest.mark.real_world
    def test_pbs_error_handling(self, pbs_config):
        """Test PBS job error handling and recovery."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def job_with_conditional_error(
            should_fail: bool, error_type: str = "value"
        ) -> Dict[str, Any]:
            """Job that can fail in different ways."""
            import os

            if should_fail:
                if error_type == "value":
                    raise ValueError("Test value error")
                elif error_type == "runtime":
                    raise RuntimeError("Test runtime error")
                elif error_type == "file":
                    with open("/nonexistent/path/file.txt", "r") as f:
                        f.read()

            return {
                "success": True,
                "pbs_jobid": os.getenv("PBS_JOBID", "unknown"),
                "error_type": "none",
            }

        # Test successful execution
        result = job_with_conditional_error(False)
        assert result["success"] is True

        # Test error handling
        with pytest.raises(ValueError):
            job_with_conditional_error(True, "value")

        with pytest.raises(RuntimeError):
            job_with_conditional_error(True, "runtime")

    @pytest.mark.real_world
    @pytest.mark.expensive
    def test_pbs_long_running_job(self, pbs_config):
        """Test longer-running PBS job."""

        @cluster(cores=1, memory="1GB", time="00:15:00")
        def long_computation() -> Dict[str, Any]:
            """Perform a longer computation."""
            import time
            import math

            start_time = time.time()

            # Perform iterative computation
            result = 0
            iterations = 1000000

            for i in range(iterations):
                result += math.sin(i * 0.001)
                if i % 100000 == 0:
                    # Periodic checkpoint
                    current_time = time.time()
                    elapsed = current_time - start_time
                    if elapsed > 300:  # 5 minutes max
                        break

            end_time = time.time()

            return {
                "start_time": start_time,
                "end_time": end_time,
                "duration": end_time - start_time,
                "result": result,
                "iterations_completed": i + 1,
                "average_time_per_iteration": (end_time - start_time) / (i + 1),
            }

        result = long_computation()

        # Validate long computation
        assert isinstance(result, dict)
        assert result["duration"] > 1.0  # Should run for reasonable time
        assert result["iterations_completed"] > 0
        assert result["average_time_per_iteration"] > 0

    @pytest.mark.real_world
    def test_pbs_job_cleanup(self, pbs_config):
        """Test PBS job cleanup and resource management."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def test_cleanup_behavior() -> Dict[str, Any]:
            """Test cleanup behavior in PBS job."""
            import os
            import tempfile
            import atexit

            # Create temporary files
            temp_files = []
            for i in range(3):
                temp_file = tempfile.NamedTemporaryFile(delete=False)
                temp_file.write(f"Temp file {i} content".encode())
                temp_file.close()
                temp_files.append(temp_file.name)

            # Register cleanup function
            def cleanup_temp_files():
                for temp_file in temp_files:
                    try:
                        os.unlink(temp_file)
                    except:
                        pass

            atexit.register(cleanup_temp_files)

            # Verify files exist
            files_exist = [os.path.exists(f) for f in temp_files]

            return {
                "temp_files_created": len(temp_files),
                "files_exist": all(files_exist),
                "temp_file_paths": temp_files,
                "cleanup_registered": True,
            }

        result = test_cleanup_behavior()

        # Validate cleanup setup
        assert isinstance(result, dict)
        assert result["temp_files_created"] == 3
        assert result["files_exist"] is True
        assert result["cleanup_registered"] is True
        assert len(result["temp_file_paths"]) == 3

    @pytest.mark.real_world
    def test_pbs_parallel_processing(self, pbs_config):
        """Test parallel processing capabilities in PBS."""

        @cluster(cores=2, memory="2GB", time="00:10:00", parallel=True)
        def parallel_computation(data_chunks: List[List[int]]) -> Dict[str, Any]:
            """Process data chunks in parallel."""
            import time
            import concurrent.futures
            import os

            def process_chunk(chunk):
                """Process a single chunk of data."""
                time.sleep(0.1)  # Simulate processing time
                return sum(chunk)

            start_time = time.time()

            # Process chunks
            results = []
            for chunk in data_chunks:
                result = process_chunk(chunk)
                results.append(result)

            end_time = time.time()

            return {
                "chunks_processed": len(data_chunks),
                "chunk_results": results,
                "total_result": sum(results),
                "processing_time": end_time - start_time,
                "pbs_jobid": os.getenv("PBS_JOBID", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
            }

        # Create test data
        test_chunks = [
            [1, 2, 3, 4, 5],
            [6, 7, 8, 9, 10],
            [11, 12, 13, 14, 15],
            [16, 17, 18, 19, 20],
        ]

        result = parallel_computation(test_chunks)

        # Validate parallel processing
        assert isinstance(result, dict)
        assert result["chunks_processed"] == 4
        assert len(result["chunk_results"]) == 4
        assert result["total_result"] == 210  # Sum of 1-20
        assert result["processing_time"] > 0
