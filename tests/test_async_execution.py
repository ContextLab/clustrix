"""Tests for asynchronous job execution functionality."""

import time
import pytest
from unittest.mock import patch

from clustrix import cluster, configure
from clustrix.async_executor_simple import AsyncJobResult


class TestAsyncExecution:
    """Test async job submission functionality."""

    def test_async_local_execution(self):
        """Test async execution with local cluster type."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def async_task(x, delay=0.1):
            time.sleep(delay)
            return x * 2

        start_time = time.time()
        result = async_task(5)
        submit_time = time.time() - start_time

        # Should return immediately (async behavior)
        assert (
            submit_time < 0.1
        ), f"Submission took {submit_time:.3f}s, should be immediate"

        # Should return AsyncJobResult
        assert isinstance(result, AsyncJobResult)
        assert hasattr(result, "job_id")
        assert hasattr(result, "get_status")
        assert hasattr(result, "get_result")

        # Initially should be running
        status = result.get_status()
        assert status in ["running", "completed"]

        # Get final result
        final_result = result.get_result(timeout=5.0)
        assert final_result == 10

        # Final status should be completed
        assert result.get_status() == "completed"

    def test_async_job_result_properties(self):
        """Test AsyncJobResult object properties and methods."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def quick_task(x):
            return x + 1

        result = quick_task(42)

        # Test job ID format
        assert isinstance(result.job_id, str)
        assert result.job_id.startswith("async_")

        # Test runtime tracking
        runtime = result.get_runtime()
        assert isinstance(runtime, float)
        assert runtime >= 0

        # Test wait method (alias for get_result)
        final_result = result.wait(timeout=5.0)
        assert final_result == 43

        # Test completion check
        assert result.is_complete()

    def test_async_job_cancellation(self):
        """Test job cancellation functionality."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def long_task():
            time.sleep(5.0)  # Long enough to cancel
            return "completed"

        result = long_task()

        # Try to cancel immediately
        was_cancelled = result.cancel()

        # Cancellation may or may not succeed depending on timing
        # but the method should not raise an exception
        assert isinstance(was_cancelled, bool)

    def test_async_job_timeout(self):
        """Test job timeout handling."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def timeout_task():
            time.sleep(2.0)
            return "done"

        result = timeout_task()

        # Should timeout after 0.1 seconds
        with pytest.raises(TimeoutError):
            result.get_result(timeout=0.1)

    def test_async_job_error_handling(self):
        """Test error handling in async jobs."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def failing_task():
            raise ValueError("Test error")

        result = failing_task()

        # Should propagate the error
        with pytest.raises(RuntimeError) as exc_info:
            result.get_result(timeout=5.0)

        assert "Test error" in str(exc_info.value)
        assert result.get_status() == "failed"

    def test_sync_vs_async_behavior(self):
        """Test that sync and async modes produce same results."""
        configure(cluster_type="local")

        def test_function(x, y):
            return x * y + 10

        # Sync execution
        sync_decorated = cluster(async_submit=False)(test_function)
        sync_result = sync_decorated(3, 4)

        # Async execution
        async_decorated = cluster(async_submit=True)(test_function)
        async_result_obj = async_decorated(3, 4)
        async_result = async_result_obj.get_result(timeout=5.0)

        # Results should be identical
        assert sync_result == async_result
        assert sync_result == 22

    def test_async_config_parameter_inheritance(self):
        """Test that async_submit parameter is properly stored in config."""

        @cluster(async_submit=True, cores=8)
        def test_func():
            return "test"

        config = test_func._cluster_config
        assert config["async_submit"] is True
        assert config["cores"] == 8

        @cluster(async_submit=False)
        def test_func2():
            return "test"

        config2 = test_func2._cluster_config
        assert config2["async_submit"] is False

    def test_global_async_config(self):
        """Test global async configuration setting."""
        # Test with global async enabled
        configure(cluster_type="local", async_submit=True)

        @cluster()  # No explicit async_submit parameter
        def global_async_task(x):
            return x * 3

        result = global_async_task(7)

        # Should return AsyncJobResult due to global config
        assert isinstance(result, AsyncJobResult)
        final_result = result.get_result(timeout=5.0)
        assert final_result == 21

        # Reset config
        configure(cluster_type="local", async_submit=False)

    @patch("clustrix.executor.ClusterExecutor")
    def test_async_with_remote_clusters(self, mock_executor_class):
        """Test async execution with remote cluster types."""
        # Mock the executor behavior
        mock_executor = mock_executor_class.return_value
        mock_executor.submit_job.return_value = "job_123"
        mock_executor.wait_for_result.return_value = "remote_result"

        configure(
            cluster_type="slurm", cluster_host="test.cluster.com", username="testuser"
        )

        @cluster(async_submit=True)
        def remote_task(data):
            return f"processed_{data}"

        # This should use the async executor with remote cluster execution
        result = remote_task("test_data")

        assert isinstance(result, AsyncJobResult)
        # The actual execution would be mocked, so we can't test the full flow
        # but we can verify the async structure is in place
        assert hasattr(result, "job_id")
        assert hasattr(result, "get_result")

    def test_multiple_async_jobs(self):
        """Test submitting multiple async jobs concurrently."""
        configure(cluster_type="local")

        @cluster(async_submit=True)
        def concurrent_task(task_id, delay=0.1):
            time.sleep(delay)
            return f"task_{task_id}_done"

        # Submit multiple jobs
        results = []
        start_time = time.time()

        for i in range(3):
            result = concurrent_task(i, delay=0.2)
            results.append(result)

        submit_time = time.time() - start_time

        # All jobs should submit quickly
        assert submit_time < 0.5, f"Submitting 3 jobs took {submit_time:.3f}s"

        # Collect all results
        final_results = []
        for result in results:
            final_results.append(result.get_result(timeout=5.0))

        # All should complete successfully
        expected_results = ["task_0_done", "task_1_done", "task_2_done"]
        assert final_results == expected_results

        # All should be completed
        for result in results:
            assert result.is_complete()
            assert result.get_status() == "completed"
