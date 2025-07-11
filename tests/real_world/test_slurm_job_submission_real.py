"""
Real SLURM job submission tests using @cluster decorator.

These tests actually submit jobs to real SLURM clusters and validate
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


class TestRealSLURMJobSubmission:
    """Test real SLURM job submission using @cluster decorator."""

    @pytest.fixture
    def slurm_config(self):
        """Get SLURM configuration for testing."""
        slurm_creds = credentials.get_slurm_credentials()
        if not slurm_creds:
            pytest.skip("No SLURM credentials available for testing")

        # Configure clustrix for SLURM
        configure(
            cluster_type="slurm",
            cluster_host=slurm_creds["host"],
            username=slurm_creds["username"],
            password=slurm_creds.get("password"),
            private_key_path=slurm_creds.get("private_key_path"),
            remote_work_dir=f"/tmp/clustrix_test_{uuid.uuid4().hex[:8]}",
            cleanup_remote_files=True,
        )

        return slurm_creds

    @pytest.mark.real_world
    def test_simple_function_slurm_submission(self, slurm_config):
        """Test submitting a simple function to SLURM."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def add_numbers(x: int, y: int) -> int:
            """Simple addition function for testing."""
            return x + y

        # Submit job and wait for result
        result = add_numbers(10, 32)

        # Validate result
        assert result == 42
        assert isinstance(result, int)

    @pytest.mark.real_world
    def test_function_with_dependencies_slurm(self, slurm_config):
        """Test SLURM job with function that has dependencies."""

        @cluster(cores=2, memory="2GB", time="00:10:00")
        def compute_statistics(data: List[float]) -> Dict[str, float]:
            """Compute statistics requiring standard library."""
            import statistics
            import math

            if not data:
                return {"mean": 0, "stdev": 0, "variance": 0}

            mean = statistics.mean(data)
            stdev = statistics.stdev(data) if len(data) > 1 else 0
            variance = statistics.variance(data) if len(data) > 1 else 0

            return {
                "mean": mean,
                "stdev": stdev,
                "variance": variance,
                "count": len(data),
            }

        # Submit job with test data
        test_data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        result = compute_statistics(test_data)

        # Validate results
        assert isinstance(result, dict)
        assert "mean" in result
        assert "stdev" in result
        assert "variance" in result
        assert "count" in result
        assert result["mean"] == 5.5
        assert result["count"] == 10
        assert result["stdev"] > 0

    @pytest.mark.real_world
    def test_function_with_environment_info_slurm(self, slurm_config):
        """Test SLURM job that accesses environment information."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def get_job_environment() -> Dict[str, str]:
            """Get SLURM job environment variables."""
            import os

            return {
                "SLURM_JOB_ID": os.getenv("SLURM_JOB_ID", "not_set"),
                "SLURM_JOB_NAME": os.getenv("SLURM_JOB_NAME", "not_set"),
                "SLURM_CPUS_PER_TASK": os.getenv("SLURM_CPUS_PER_TASK", "not_set"),
                "SLURM_MEM_PER_NODE": os.getenv("SLURM_MEM_PER_NODE", "not_set"),
                "HOSTNAME": os.getenv("HOSTNAME", "not_set"),
                "USER": os.getenv("USER", "not_set"),
                "PWD": os.getenv("PWD", "not_set"),
            }

        result = get_job_environment()

        # Validate SLURM environment
        assert isinstance(result, dict)
        assert "SLURM_JOB_ID" in result
        assert result["SLURM_JOB_ID"] != "not_set"
        assert result["SLURM_CPUS_PER_TASK"] != "not_set"
        assert result["USER"] != "not_set"

    @pytest.mark.real_world
    def test_function_with_file_io_slurm(self, slurm_config):
        """Test SLURM job that performs file I/O operations."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def process_text_file(content: str) -> Dict[str, Any]:
            """Process text content and write to file."""
            import tempfile
            import os

            # Create temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".txt"
            ) as f:
                f.write(content)
                temp_file = f.name

            try:
                # Read file and process
                with open(temp_file, "r") as f:
                    file_content = f.read()

                # Compute statistics
                lines = file_content.strip().split("\n")
                words = file_content.split()
                chars = len(file_content)

                # Create result file
                result_file = temp_file + ".result"
                with open(result_file, "w") as f:
                    f.write(f"Lines: {len(lines)}\n")
                    f.write(f"Words: {len(words)}\n")
                    f.write(f"Characters: {chars}\n")

                # Verify result file exists
                result_exists = os.path.exists(result_file)

                return {
                    "lines": len(lines),
                    "words": len(words),
                    "characters": chars,
                    "result_file_created": result_exists,
                    "temp_file_path": temp_file,
                    "result_file_path": result_file,
                }

            finally:
                # Cleanup
                try:
                    os.unlink(temp_file)
                    if os.path.exists(result_file):
                        os.unlink(result_file)
                except:
                    pass

        # Submit job with test content
        test_content = (
            "This is a test file.\nIt has multiple lines.\nFor testing file operations."
        )
        result = process_text_file(test_content)

        # Validate results
        assert isinstance(result, dict)
        assert result["lines"] == 3
        assert result["words"] == 12
        assert result["characters"] > 0
        assert result["result_file_created"] is True

    @pytest.mark.real_world
    def test_function_with_error_handling_slurm(self, slurm_config):
        """Test SLURM job error handling."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def function_with_error(should_fail: bool) -> str:
            """Function that can succeed or fail based on parameter."""
            if should_fail:
                raise ValueError("Intentional test error")
            return "Success!"

        # Test successful execution
        result = function_with_error(False)
        assert result == "Success!"

        # Test error handling
        with pytest.raises(Exception):
            function_with_error(True)

    @pytest.mark.real_world
    def test_parallel_loop_slurm(self, slurm_config):
        """Test parallel loop execution on SLURM."""

        @cluster(cores=4, memory="4GB", time="00:10:00", parallel=True)
        def compute_squares(numbers: List[int]) -> List[int]:
            """Compute squares of numbers (should be parallelized)."""
            import time

            results = []
            for num in numbers:
                # Simulate some work
                time.sleep(0.1)
                results.append(num * num)

            return results

        # Submit job with test data
        test_numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        result = compute_squares(test_numbers)

        # Validate results
        assert isinstance(result, list)
        assert len(result) == len(test_numbers)
        expected = [num * num for num in test_numbers]
        assert result == expected

    @pytest.mark.real_world
    def test_function_with_different_partitions_slurm(self, slurm_config):
        """Test SLURM job submission to different partitions."""

        @cluster(cores=1, memory="1GB", time="00:05:00", partition="compute")
        def test_compute_partition() -> Dict[str, str]:
            """Test function for compute partition."""
            import os

            return {
                "partition": os.getenv("SLURM_JOB_PARTITION", "unknown"),
                "node": os.getenv("SLURMD_NODENAME", "unknown"),
                "result": "compute_partition_success",
            }

        try:
            result = test_compute_partition()
            assert isinstance(result, dict)
            assert result["result"] == "compute_partition_success"
        except Exception as e:
            # Partition might not exist, skip test
            pytest.skip(f"Compute partition not available: {e}")

    @pytest.mark.real_world
    @pytest.mark.expensive
    def test_memory_intensive_slurm(self, slurm_config):
        """Test memory-intensive SLURM job."""

        @cluster(cores=2, memory="8GB", time="00:15:00")
        def memory_intensive_task(size_mb: int) -> Dict[str, Any]:
            """Create and process large data structures."""
            import gc
            import psutil
            import os

            # Get initial memory
            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / 1024 / 1024  # MB

            # Create large data structure
            large_data = [i * 2.5 for i in range(size_mb * 1000)]

            # Process data
            result_sum = sum(large_data)
            result_len = len(large_data)

            # Get peak memory
            peak_memory = process.memory_info().rss / 1024 / 1024  # MB

            # Cleanup
            del large_data
            gc.collect()

            return {
                "initial_memory_mb": initial_memory,
                "peak_memory_mb": peak_memory,
                "memory_used_mb": peak_memory - initial_memory,
                "result_sum": result_sum,
                "result_len": result_len,
            }

        # Submit memory-intensive job
        result = memory_intensive_task(100)  # 100MB of data

        # Validate results
        assert isinstance(result, dict)
        assert result["memory_used_mb"] > 50  # Should use significant memory
        assert result["result_len"] == 100000
        assert result["result_sum"] > 0

    @pytest.mark.real_world
    def test_job_status_monitoring_slurm(self, slurm_config):
        """Test job status monitoring during execution."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def long_running_task() -> Dict[str, Any]:
            """A task that runs for a measurable amount of time."""
            import time
            import os

            start_time = time.time()

            # Simulate work
            for i in range(10):
                time.sleep(0.5)

            end_time = time.time()

            return {
                "start_time": start_time,
                "end_time": end_time,
                "duration": end_time - start_time,
                "job_id": os.getenv("SLURM_JOB_ID", "unknown"),
                "iterations": 10,
            }

        # Submit and monitor job
        start_time = time.time()
        result = long_running_task()
        total_time = time.time() - start_time

        # Validate results
        assert isinstance(result, dict)
        assert result["duration"] >= 5.0  # Should run for at least 5 seconds
        assert result["iterations"] == 10
        assert result["job_id"] != "unknown"
        assert total_time >= result["duration"]  # Total time includes queue time

    @pytest.mark.real_world
    def test_multiple_job_submission_slurm(self, slurm_config):
        """Test submitting multiple jobs to SLURM."""

        @cluster(cores=1, memory="1GB", time="00:05:00")
        def job_with_id(job_number: int) -> Dict[str, Any]:
            """Job that identifies itself."""
            import os
            import time

            return {
                "job_number": job_number,
                "slurm_job_id": os.getenv("SLURM_JOB_ID", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "timestamp": time.time(),
            }

        # Submit multiple jobs
        results = []
        for i in range(3):
            result = job_with_id(i)
            results.append(result)

        # Validate results
        assert len(results) == 3

        for i, result in enumerate(results):
            assert isinstance(result, dict)
            assert result["job_number"] == i
            assert result["slurm_job_id"] != "unknown"
            assert result["hostname"] != "unknown"

        # Verify jobs had different SLURM job IDs
        job_ids = [r["slurm_job_id"] for r in results]
        assert len(set(job_ids)) == 3  # All different job IDs

    @pytest.mark.real_world
    def test_job_resource_validation_slurm(self, slurm_config):
        """Test that SLURM jobs respect resource specifications."""

        @cluster(cores=2, memory="2GB", time="00:05:00")
        def check_allocated_resources() -> Dict[str, Any]:
            """Check what resources were actually allocated."""
            import os

            return {
                "allocated_cpus": os.getenv("SLURM_CPUS_PER_TASK", "unknown"),
                "allocated_memory": os.getenv("SLURM_MEM_PER_NODE", "unknown"),
                "job_time_limit": os.getenv("SLURM_JOB_TIME_LIMIT", "unknown"),
                "job_partition": os.getenv("SLURM_JOB_PARTITION", "unknown"),
                "node_list": os.getenv("SLURM_JOB_NODELIST", "unknown"),
            }

        result = check_allocated_resources()

        # Validate resource allocation
        assert isinstance(result, dict)
        assert result["allocated_cpus"] != "unknown"
        assert result["allocated_memory"] != "unknown"

        # Check if allocated resources match requested
        try:
            allocated_cpus = int(result["allocated_cpus"])
            assert allocated_cpus >= 2  # Should have at least 2 CPUs
        except ValueError:
            pytest.skip("Could not parse allocated CPU count")
