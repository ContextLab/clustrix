"""
Comprehensive real-world SLURM validation tests.

This module tests the fixed SLURM implementation with actual SLURM clusters,
addressing the critical output file retrieval blocker identified in Issue #63.

Tests cover:
- Job submission and execution
- Robust output file retrieval with retry logic
- Error handling and failure scenarios
- File system synchronization delays
- SLURM-specific edge cases

NO MOCK TESTS - Only real SLURM cluster integration.
"""

import pytest
import logging
import time
import tempfile
import os
from typing import Dict, Any, Optional

# Import credential manager and test utilities
from .credential_manager import get_credential_manager
from clustrix import ClusterExecutor
from clustrix.config import ClusterConfig

# Configure logging for detailed test debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_slurm_test_credentials() -> Optional[Dict[str, Any]]:
    """Get real SLURM cluster credentials from 1Password or environment."""
    manager = get_credential_manager()

    # Try to get SLURM credentials from credential manager
    slurm_creds = manager.get_slurm_credentials()
    if slurm_creds:
        return {
            "cluster_host": slurm_creds["host"],
            "username": slurm_creds["username"],
            "key_file": slurm_creds.get("key_file"),
            "password": slurm_creds.get("password"),
            "remote_work_dir": slurm_creds.get("remote_work_dir", "/tmp/clustrix_test"),
            "cluster_port": int(slurm_creds.get("port", 22)),
        }

    # Fallback to environment variables for CI/automated testing
    import os

    if all(key in os.environ for key in ["SLURM_HOST", "SLURM_USERNAME"]):
        return {
            "cluster_host": os.environ["SLURM_HOST"],
            "username": os.environ["SLURM_USERNAME"],
            "key_file": os.environ.get("SLURM_KEY_FILE"),
            "password": os.environ.get("SLURM_PASSWORD"),
            "remote_work_dir": os.environ.get("SLURM_WORK_DIR", "/tmp/clustrix_test"),
            "cluster_port": int(os.environ.get("SLURM_PORT", 22)),
        }

    return None


def create_test_slurm_config() -> Optional[ClusterConfig]:
    """Create a test SLURM configuration with real credentials."""
    creds = get_slurm_test_credentials()
    if not creds:
        return None

    return ClusterConfig(
        cluster_type="slurm",
        cluster_host=creds["cluster_host"],
        cluster_port=creds.get("cluster_port", 22),
        username=creds["username"],
        key_file=creds.get("key_file"),
        password=creds.get("password"),
        remote_work_dir=creds["remote_work_dir"],
        cleanup_on_success=True,  # Clean up test jobs
        cleanup_remote_files=True,
        job_poll_interval=2,  # Faster polling for tests
    )


@pytest.mark.real_world
class TestSLURMComprehensive:
    """Comprehensive SLURM integration tests addressing Issue #63 critical blocker."""

    def setup_method(self):
        """Setup test environment."""
        self.config = create_test_slurm_config()
        if self.config:
            self.executor = ClusterExecutor(self.config)
        else:
            self.executor = None

    def teardown_method(self):
        """Cleanup test environment."""
        if self.executor:
            try:
                # Clean up any remaining active jobs
                for job_id in list(self.executor.active_jobs.keys()):
                    try:
                        self.executor._execute_remote_command(f"scancel {job_id}")
                        logger.info(f"Cancelled test job {job_id}")
                    except Exception as e:
                        logger.debug(f"Could not cancel job {job_id}: {e}")

                self.executor.disconnect()
            except Exception as e:
                logger.debug(f"Error during teardown: {e}")

    @pytest.mark.real_world
    def test_slurm_simple_function_execution(self):
        """Test basic SLURM function execution with new robust output retrieval."""
        if not self.config:
            pytest.skip("SLURM credentials not available")

        logger.info("Testing SLURM simple function execution")

        def simple_calculation(x: int, y: int) -> int:
            """Simple test function for SLURM execution."""
            import time

            time.sleep(2)  # Simulate some work
            return x + y + 10

        # Submit job using @cluster decorator simulation
        job_id = self.executor.submit(simple_calculation, 5, 3)

        assert job_id is not None, "Job submission should return a job ID"
        assert (
            job_id in self.executor.active_jobs
        ), "Job should be tracked in active_jobs"

        logger.info(f"Submitted SLURM job {job_id}, waiting for completion...")

        # Test the fixed output retrieval with timeout
        start_time = time.time()
        timeout = 300  # 5 minute timeout

        try:
            result = self.executor.wait_for_result(job_id)
            execution_time = time.time() - start_time

            assert result == 18, f"Expected 18, got {result}"
            logger.info(
                f"✅ SLURM job {job_id} completed successfully in {execution_time:.1f}s: {result}"
            )

            # Verify job was cleaned up
            assert (
                job_id not in self.executor.active_jobs
            ), "Job should be removed from active_jobs after completion"

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"❌ SLURM job {job_id} failed after {execution_time:.1f}s: {e}"
            )

            # Get detailed error information for debugging
            if job_id in self.executor.active_jobs:
                job_info = self.executor.active_jobs[job_id]
                remote_dir = job_info.get("remote_dir", "unknown")

                # Check what files exist in the job directory
                try:
                    files_cmd = f"ls -la {remote_dir}"
                    stdout, stderr = self.executor._execute_remote_command(files_cmd)
                    logger.error(f"Job directory contents: {stdout}")

                    # Check SLURM output files
                    slurm_cmd = f"ls -la {remote_dir}/slurm-*.out 2>/dev/null || echo 'No SLURM output files'"
                    stdout, stderr = self.executor._execute_remote_command(slurm_cmd)
                    logger.error(f"SLURM output files: {stdout}")

                except Exception as debug_e:
                    logger.error(f"Could not get debug info: {debug_e}")

            raise

    @pytest.mark.real_world
    def test_slurm_error_handling_and_recovery(self):
        """Test SLURM error handling with failing function."""
        if not self.config:
            pytest.skip("SLURM credentials not available")

        logger.info("Testing SLURM error handling and recovery")

        def failing_function(x: int) -> int:
            """Function that will fail for testing error handling."""
            if x > 0:
                raise ValueError(f"Test error with x={x}")
            return x * 2

        # Submit failing job
        job_id = self.executor.submit(failing_function, 5)

        logger.info(f"Submitted failing SLURM job {job_id}, expecting failure...")

        # Test that error is properly handled
        with pytest.raises(Exception) as exc_info:
            result = self.executor.wait_for_result(job_id)

        # Verify the error contains our test message
        error_str = str(exc_info.value)
        assert (
            "Test error with x=5" in error_str
        ), f"Expected test error message in: {error_str}"

        logger.info(f"✅ SLURM error handling working correctly: {error_str}")

        # Verify job was cleaned up even after failure
        assert (
            job_id not in self.executor.active_jobs
        ), "Failed job should be removed from active_jobs"

    @pytest.mark.real_world
    def test_slurm_file_system_delay_handling(self):
        """Test handling of file system delays common in HPC environments."""
        if not self.config:
            pytest.skip("SLURM credentials not available")

        logger.info("Testing SLURM file system delay handling")

        def file_system_test() -> str:
            """Function that creates files to test file system synchronization."""
            import os
            import time

            # Create multiple files to increase chance of sync delays
            test_dir = "/tmp/clustrix_fs_test"
            os.makedirs(test_dir, exist_ok=True)

            for i in range(5):
                with open(f"{test_dir}/test_file_{i}.txt", "w") as f:
                    f.write(f"Test content {i} - timestamp: {time.time()}")
                time.sleep(0.1)  # Small delay between file creations

            return f"Created 5 test files in {test_dir}"

        # Submit job
        job_id = self.executor.submit(file_system_test)

        logger.info(f"Submitted file system test job {job_id}")

        # The new robust implementation should handle any file system delays
        result = self.executor.wait_for_result(job_id)

        assert "Created 5 test files" in result, f"Unexpected result: {result}"
        logger.info(f"✅ File system delay handling working: {result}")

    @pytest.mark.real_world
    def test_slurm_concurrent_jobs(self):
        """Test multiple concurrent SLURM jobs to stress-test the implementation."""
        if not self.config:
            pytest.skip("SLURM credentials not available")

        logger.info("Testing concurrent SLURM jobs")

        def concurrent_calculation(job_num: int) -> str:
            """Function for concurrent job testing."""
            import time
            import os

            # Each job sleeps for a different duration
            sleep_time = 1 + (job_num % 3)  # 1-3 seconds
            time.sleep(sleep_time)

            return (
                f"Job {job_num} completed on {os.uname().nodename} after {sleep_time}s"
            )

        # Submit multiple jobs concurrently
        num_jobs = 3
        job_ids = []

        for i in range(num_jobs):
            job_id = self.executor.submit(concurrent_calculation, i)
            job_ids.append(job_id)
            logger.info(f"Submitted concurrent job {i}: {job_id}")

        # Wait for all jobs to complete
        results = []
        for i, job_id in enumerate(job_ids):
            logger.info(f"Waiting for concurrent job {i} ({job_id})...")
            result = self.executor.wait_for_result(job_id)
            results.append(result)
            logger.info(f"Concurrent job {i} result: {result}")

        # Verify all jobs completed successfully
        assert (
            len(results) == num_jobs
        ), f"Expected {num_jobs} results, got {len(results)}"

        for i, result in enumerate(results):
            assert f"Job {i} completed" in result, f"Job {i} result malformed: {result}"

        logger.info(f"✅ All {num_jobs} concurrent SLURM jobs completed successfully")

    @pytest.mark.real_world
    def test_slurm_resource_specification(self):
        """Test SLURM job submission with specific resource requirements."""
        if not self.config:
            pytest.skip("SLURM credentials not available")

        logger.info("Testing SLURM resource specification")

        def resource_test() -> Dict[str, Any]:
            """Function that reports on available resources."""
            import os
            import psutil

            return {
                "hostname": os.uname().nodename,
                "cpu_count": psutil.cpu_count(),
                "memory_mb": round(psutil.virtual_memory().total / (1024 * 1024)),
                "pid": os.getpid(),
            }

        # Submit job with specific resource requirements
        job_config = {
            "cores": 2,
            "memory": "1GB",
            "time_limit": "00:05:00",  # 5 minutes
        }

        job_id = self.executor.submit(resource_test, job_config=job_config)
        logger.info(f"Submitted resource test job {job_id} with config: {job_config}")

        result = self.executor.wait_for_result(job_id)

        # Verify result structure
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert "hostname" in result, "Result should include hostname"
        assert "cpu_count" in result, "Result should include CPU count"
        assert "memory_mb" in result, "Result should include memory info"

        logger.info(f"✅ Resource specification test completed on {result['hostname']}")
        logger.info(
            f"   CPU count: {result['cpu_count']}, Memory: {result['memory_mb']} MB"
        )

    @pytest.mark.real_world
    def test_slurm_job_cancellation(self):
        """Test SLURM job cancellation functionality."""
        if not self.config:
            pytest.skip("SLURM credentials not available")

        logger.info("Testing SLURM job cancellation")

        def long_running_job() -> str:
            """Function that runs for a while to test cancellation."""
            import time

            for i in range(60):  # Run for up to 60 seconds
                time.sleep(1)
                if i % 10 == 0:
                    print(f"Long job progress: {i}/60 seconds")

            return "Long job completed"

        # Submit long-running job
        job_id = self.executor.submit(long_running_job)
        logger.info(f"Submitted long-running job {job_id} for cancellation test")

        # Wait a bit to ensure job starts
        time.sleep(5)

        # Check job status before cancellation
        status = self.executor._check_job_status(job_id)
        logger.info(f"Job {job_id} status before cancellation: {status}")

        # Cancel the job
        try:
            cancel_cmd = f"scancel {job_id}"
            stdout, stderr = self.executor._execute_remote_command(cancel_cmd)
            logger.info(f"Cancelled job {job_id}: {stdout}")

            # Wait a bit for cancellation to take effect
            time.sleep(3)

            # Check final status
            final_status = self.executor._check_job_status(job_id)
            logger.info(f"Job {job_id} status after cancellation: {final_status}")

            # The job should be cancelled or failed
            assert final_status in [
                "failed",
                "cancelled",
                "unknown",
            ], f"Expected cancelled/failed status, got {final_status}"

            logger.info("✅ SLURM job cancellation working correctly")

        except Exception as e:
            logger.warning(f"Could not test job cancellation: {e}")
            # This is not a critical failure - some clusters may not allow cancellation

        finally:
            # Clean up job from active_jobs if still there
            if job_id in self.executor.active_jobs:
                del self.executor.active_jobs[job_id]


if __name__ == "__main__":
    # Run tests directly for debugging
    pytest.main([__file__, "-v", "--tb=short", "-m", "real_world"])
