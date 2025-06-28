"""Simplified asynchronous cluster executor using threading for non-blocking submission."""

import threading
import time
import logging
from typing import Any, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, Future

from .config import ClusterConfig
from .executor import ClusterExecutor

logger = logging.getLogger(__name__)


class AsyncJobResult:
    """Represents the result of an asynchronously submitted job."""

    def __init__(self, future: Future, job_id: str, executor: ClusterExecutor):
        self._future = future
        self.job_id = job_id
        self._executor = executor
        self._start_time = time.time()

    def is_complete(self) -> bool:
        """Check if the job has completed."""
        return self._future.done()

    def get_status(self) -> str:
        """Get current job status."""
        if self._future.done():
            if self._future.exception():
                return "failed"
            return "completed"
        return "running"

    def get_result(self, timeout: Optional[float] = None) -> Any:
        """
        Get job result, optionally with timeout.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            Job result

        Raises:
            TimeoutError: If timeout exceeded
            RuntimeError: If job failed
        """
        try:
            return self._future.result(timeout=timeout)
        except Exception as e:
            if isinstance(e, TimeoutError):
                raise TimeoutError(
                    f"Job {self.job_id} did not complete within {timeout} seconds"
                )
            else:
                raise RuntimeError(f"Job {self.job_id} failed: {e}")

    def cancel(self) -> bool:
        """Cancel the job if still running."""
        if not self._future.done():
            cancelled = self._future.cancel()
            if cancelled:
                logger.info(f"Cancelled job {self.job_id}")
            return cancelled
        return False

    def get_runtime(self) -> float:
        """Get current runtime in seconds."""
        return time.time() - self._start_time

    def wait(self, timeout: Optional[float] = None) -> Any:
        """Wait for completion and return result (alias for get_result)."""
        return self.get_result(timeout)


class SimpleAsyncClusterExecutor:
    """
    Simplified asynchronous cluster executor using threading.

    This version uses a thread pool to submit jobs asynchronously without
    the complexity of meta-jobs. Jobs are submitted in background threads
    and return AsyncJobResult objects immediately.
    """

    def __init__(self, config: ClusterConfig, max_workers: int = 4):
        self.config = config
        self.max_workers = max_workers
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self._active_jobs: Dict[str, AsyncJobResult] = {}
        self._job_counter = 0
        self._lock = threading.Lock()

    def submit_job_async(
        self, func: Callable, args: tuple, kwargs: dict, job_config: dict
    ) -> AsyncJobResult:
        """
        Submit a job asynchronously.

        Args:
            func: Function to execute
            args: Function arguments
            kwargs: Function keyword arguments
            job_config: Job configuration

        Returns:
            AsyncJobResult that can be used to check status and get results
        """
        with self._lock:
            self._job_counter += 1
            job_id = f"async_{int(time.time())}_{self._job_counter}"

        # Submit job in background thread
        future = self._thread_pool.submit(
            self._execute_job_sync, func, args, kwargs, job_config, job_id
        )

        # Create result object
        result = AsyncJobResult(future, job_id, None)

        # Track the job
        self._active_jobs[job_id] = result

        logger.info(f"Submitted async job {job_id}")
        return result

    def _execute_job_sync(
        self, func: Callable, args: tuple, kwargs: dict, job_config: dict, job_id: str
    ) -> Any:
        """
        Execute a job synchronously in a background thread.

        This method runs in a separate thread and uses either local execution
        or the regular ClusterExecutor based on configuration.
        """
        try:
            logger.debug(f"Starting execution of job {job_id} in thread")

            # Check if this should be local execution
            if self.config.cluster_type == "local" or not self.config.cluster_host:
                # Local execution in background thread
                logger.debug(f"Executing job {job_id} locally in background thread")
                result = func(*args, **kwargs)
                logger.info(f"Local job {job_id} completed successfully")
                return result
            else:
                # Remote execution via cluster
                executor = ClusterExecutor(self.config)

                # Import here to avoid circular imports
                from .utils import serialize_function

                # Serialize function and dependencies
                func_data = serialize_function(func, args, kwargs)

                # Submit job and wait for result
                actual_job_id = executor.submit_job(func_data, job_config)
                logger.debug(f"Job {job_id} submitted to cluster as {actual_job_id}")

                # Wait for completion and get result
                result = executor.wait_for_result(actual_job_id)

                logger.info(f"Job {job_id} completed successfully")
                return result

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            raise
        finally:
            # Clean up executor resources
            if "executor" in locals():
                executor.disconnect()

    def get_active_jobs(self) -> Dict[str, AsyncJobResult]:
        """Get dictionary of active job IDs and their results."""
        # Clean up completed jobs
        completed_jobs = [
            job_id
            for job_id, result in self._active_jobs.items()
            if result.is_complete()
        ]

        for job_id in completed_jobs:
            if self.config.cleanup_on_success:
                del self._active_jobs[job_id]

        return self._active_jobs.copy()

    def wait_for_all(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Wait for all active jobs to complete.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            Dictionary mapping job IDs to results
        """
        results = {}
        start_time = time.time()

        for job_id, result in self._active_jobs.items():
            remaining_timeout = None
            if timeout:
                elapsed = time.time() - start_time
                remaining_timeout = max(0, timeout - elapsed)

            try:
                results[job_id] = result.get_result(remaining_timeout)
            except Exception as e:
                results[job_id] = e

        return results

    def cancel_all(self) -> int:
        """Cancel all active jobs."""
        cancelled_count = 0
        for result in self._active_jobs.values():
            if result.cancel():
                cancelled_count += 1

        return cancelled_count

    def get_status_summary(self) -> Dict[str, int]:
        """Get summary of job statuses."""
        summary = {"running": 0, "completed": 0, "failed": 0}

        for result in self._active_jobs.values():
            status = result.get_status()
            if status in summary:
                summary[status] += 1

        return summary

    def shutdown(self, wait: bool = True):
        """Shutdown the async executor."""
        logger.info("Shutting down async executor")
        self._thread_pool.shutdown(wait=wait)


# Backward compatibility alias
AsyncClusterExecutor = SimpleAsyncClusterExecutor
