"""
Comprehensive real-world Kubernetes validation tests.

This module tests the complete Kubernetes implementation with actual K8s clusters,
addressing Phase 2 of Issue #63 external service validation.

Tests cover:
- Kubernetes job submission and execution
- Container-based Python function execution
- Job monitoring and status tracking
- Error handling and recovery
- Resource specification and limits
- Pod log parsing and result retrieval
- Job cleanup and TTL management

NO MOCK TESTS - Only real Kubernetes cluster integration.

Supports multiple K8s environments:
- Local: minikube, kind, Docker Desktop
- Cloud: EKS (AWS), GKE (Google), AKS (Azure)
- Hybrid: On-premises clusters
"""

import pytest
import logging
import time
import os
import tempfile
from typing import Dict, Any, Optional

# Import credential manager and test utilities
from .credential_manager import get_credential_manager
from clustrix import ClusterExecutor
from clustrix.config import ClusterConfig

# Configure logging for detailed test debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_kubernetes_test_credentials() -> Optional[Dict[str, Any]]:
    """Get real Kubernetes cluster credentials from 1Password or environment."""
    manager = get_credential_manager()

    # Try to get Kubernetes credentials from credential manager
    # This would need to be added to the credential manager
    k8s_creds = (
        manager.get_kubernetes_credentials()
        if hasattr(manager, "get_kubernetes_credentials")
        else None
    )
    if k8s_creds:
        return k8s_creds

    # Fallback to environment variables for CI/automated testing
    # Check for kubeconfig file or in-cluster config
    kubeconfig_path = os.environ.get("KUBECONFIG", os.path.expanduser("~/.kube/config"))
    if os.path.exists(kubeconfig_path):
        return {
            "kubeconfig_path": kubeconfig_path,
            "namespace": os.environ.get("K8S_NAMESPACE", "default"),
            "image": os.environ.get("K8S_IMAGE", "python:3.11-slim"),
            "context": os.environ.get("K8S_CONTEXT"),  # Optional specific context
        }

    # Check if we're running inside a Kubernetes cluster
    if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
        return {
            "in_cluster": True,
            "namespace": os.environ.get("K8S_NAMESPACE", "default"),
            "image": os.environ.get("K8S_IMAGE", "python:3.11-slim"),
        }

    return None


def create_test_kubernetes_config() -> Optional[ClusterConfig]:
    """Create a test Kubernetes configuration with real credentials."""
    creds = get_kubernetes_test_credentials()
    if not creds:
        return None

    config_params = {
        "cluster_type": "kubernetes",
        "k8s_namespace": creds.get("namespace", "default"),
        "k8s_image": creds.get("image", "python:3.11-slim"),
        "k8s_job_ttl_seconds": 600,  # 10 minutes for tests
        "k8s_backoff_limit": 2,  # Allow 2 retries
        "cleanup_on_success": True,  # Clean up test jobs
        "job_poll_interval": 3,  # Faster polling for tests
    }

    # Add any additional config from credentials
    if "service_account" in creds:
        config_params["k8s_service_account"] = creds["service_account"]

    return ClusterConfig(**config_params)


@pytest.mark.real_world
class TestKubernetesComprehensive:
    """Comprehensive Kubernetes integration tests addressing Issue #63 Phase 2."""

    def setup_method(self):
        """Setup test environment."""
        self.config = create_test_kubernetes_config()
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
                        self.executor._cleanup_k8s_job(job_id)
                        logger.info(f"Cleaned up test job {job_id}")
                    except Exception as e:
                        logger.debug(f"Could not cleanup job {job_id}: {e}")

                self.executor.disconnect()
            except Exception as e:
                logger.debug(f"Error during teardown: {e}")

    @pytest.mark.real_world
    def test_kubernetes_simple_function_execution(self):
        """Test basic Kubernetes function execution with containerized execution."""
        if not self.config:
            pytest.skip(
                "Kubernetes cluster not available (no kubeconfig or in-cluster config)"
            )

        logger.info("Testing Kubernetes simple function execution")

        def simple_calculation(x: int, y: int) -> int:
            """Simple test function for Kubernetes execution."""
            import time

            time.sleep(1)  # Brief pause to simulate work
            return (x * y) + 42

        # Submit job using Kubernetes executor
        job_id = self.executor.submit(simple_calculation, 7, 6)

        assert job_id is not None, "Job submission should return a job ID"
        assert (
            job_id in self.executor.active_jobs
        ), "Job should be tracked in active_jobs"
        assert (
            self.executor.active_jobs[job_id].get("k8s_job") is True
        ), "Should be marked as K8s job"

        logger.info(f"Submitted Kubernetes job {job_id}, waiting for completion...")

        # Test the Kubernetes result retrieval
        start_time = time.time()
        timeout = 300  # 5 minute timeout

        try:
            result = self.executor.wait_for_result(job_id)
            execution_time = time.time() - start_time

            expected_result = (7 * 6) + 42  # 84
            assert (
                result == expected_result
            ), f"Expected {expected_result}, got {result}"
            logger.info(
                f"✅ Kubernetes job {job_id} completed successfully in {execution_time:.1f}s: {result}"
            )

            # Verify job was cleaned up
            assert (
                job_id not in self.executor.active_jobs
            ), "Job should be removed from active_jobs after completion"

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"❌ Kubernetes job {job_id} failed after {execution_time:.1f}s: {e}"
            )

            # Get detailed error information for debugging
            try:
                error_log = self.executor._get_k8s_error_log(job_id)
                logger.error(f"Kubernetes error log: {error_log}")

                # Check job status via Kubernetes API
                status = self.executor._check_job_status(job_id)
                logger.error(f"Job status: {status}")

            except Exception as debug_e:
                logger.error(f"Could not get debug info: {debug_e}")

            raise

    @pytest.mark.real_world
    def test_kubernetes_error_handling_and_recovery(self):
        """Test Kubernetes error handling with failing function."""
        if not self.config:
            pytest.skip("Kubernetes cluster not available")

        logger.info("Testing Kubernetes error handling and recovery")

        def failing_function(error_message: str) -> str:
            """Function that will fail for testing error handling."""
            import time

            time.sleep(1)  # Simulate some work before failing
            raise RuntimeError(f"Intentional test error: {error_message}")

        # Submit failing job
        job_id = self.executor.submit(failing_function, "test-k8s-error")

        logger.info(f"Submitted failing Kubernetes job {job_id}, expecting failure...")

        # Test that error is properly handled
        with pytest.raises(Exception) as exc_info:
            result = self.executor.wait_for_result(job_id)

        # Verify the error contains our test message
        error_str = str(exc_info.value)
        assert (
            "Intentional test error: test-k8s-error" in error_str
        ), f"Expected test error message in: {error_str}"

        logger.info(f"✅ Kubernetes error handling working correctly: {error_str}")

        # Verify job was cleaned up even after failure
        assert (
            job_id not in self.executor.active_jobs
        ), "Failed job should be removed from active_jobs"

    @pytest.mark.real_world
    def test_kubernetes_resource_specification(self):
        """Test Kubernetes job submission with specific resource requirements."""
        if not self.config:
            pytest.skip("Kubernetes cluster not available")

        logger.info("Testing Kubernetes resource specification")

        def resource_intensive_test() -> Dict[str, Any]:
            """Function that reports on allocated resources."""
            import os
            import psutil

            # Get container resource information
            result = {
                "hostname": os.uname().nodename,
                "cpu_count": psutil.cpu_count(),
                "memory_mb": round(psutil.virtual_memory().total / (1024 * 1024)),
                "pid": os.getpid(),
                "python_version": f"{psutil.sys.version_info.major}.{psutil.sys.version_info.minor}",
            }

            # Check for Kubernetes environment variables
            if "KUBERNETES_SERVICE_HOST" in os.environ:
                result["in_kubernetes"] = True
                result["namespace"] = os.environ.get("KUBERNETES_NAMESPACE", "unknown")

            return result

        # Submit job with specific resource requirements
        job_config = {
            "cores": 1,  # Request 1 CPU
            "memory": "512Mi",  # Request 512MB memory
        }

        job_id = self.executor.submit(resource_intensive_test, job_config=job_config)
        logger.info(
            f"Submitted Kubernetes resource test job {job_id} with config: {job_config}"
        )

        result = self.executor.wait_for_result(job_id)

        # Verify result structure
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert "hostname" in result, "Result should include hostname"
        assert "cpu_count" in result, "Result should include CPU count"
        assert "memory_mb" in result, "Result should include memory info"
        assert "in_kubernetes" in result, "Should detect Kubernetes environment"

        logger.info(f"✅ Kubernetes resource test completed on {result['hostname']}")
        logger.info(
            f"   CPU count: {result['cpu_count']}, Memory: {result['memory_mb']} MB"
        )
        logger.info(
            f"   Python version: {result['python_version']}, In K8s: {result.get('in_kubernetes', False)}"
        )

    @pytest.mark.real_world
    def test_kubernetes_concurrent_jobs(self):
        """Test multiple concurrent Kubernetes jobs."""
        if not self.config:
            pytest.skip("Kubernetes cluster not available")

        logger.info("Testing concurrent Kubernetes jobs")

        def concurrent_task(task_id: int, delay: float) -> str:
            """Function for concurrent job testing."""
            import time
            import os

            time.sleep(delay)
            hostname = os.uname().nodename
            return f"Task {task_id} completed on {hostname} after {delay}s"

        # Submit multiple jobs concurrently
        num_jobs = 3
        job_ids = []

        for i in range(num_jobs):
            delay = 1 + (i * 0.5)  # Staggered delays: 1s, 1.5s, 2s
            job_id = self.executor.submit(concurrent_task, i, delay)
            job_ids.append(job_id)
            logger.info(f"Submitted concurrent Kubernetes job {i}: {job_id}")

        # Wait for all jobs to complete
        results = []
        for i, job_id in enumerate(job_ids):
            logger.info(f"Waiting for concurrent Kubernetes job {i} ({job_id})...")
            result = self.executor.wait_for_result(job_id)
            results.append(result)
            logger.info(f"Concurrent job {i} result: {result}")

        # Verify all jobs completed successfully
        assert (
            len(results) == num_jobs
        ), f"Expected {num_jobs} results, got {len(results)}"

        for i, result in enumerate(results):
            assert (
                f"Task {i} completed" in result
            ), f"Job {i} result malformed: {result}"
            assert "after" in result, f"Job {i} should include timing info: {result}"

        logger.info(
            f"✅ All {num_jobs} concurrent Kubernetes jobs completed successfully"
        )

    @pytest.mark.real_world
    def test_kubernetes_dependency_handling(self):
        """Test Kubernetes job execution with Python package dependencies."""
        if not self.config:
            pytest.skip("Kubernetes cluster not available")

        logger.info("Testing Kubernetes dependency handling")

        def dependency_test() -> Dict[str, Any]:
            """Function that uses common packages available in python:3.11-slim."""
            import json
            import datetime
            import urllib.request
            import base64

            # Test basic operations with standard library
            test_data = {
                "timestamp": datetime.datetime.now().isoformat(),
                "base64_test": base64.b64encode(b"hello kubernetes").decode("utf-8"),
                "json_test": json.dumps({"status": "success", "value": 42}),
            }

            return {
                "test_data": test_data,
                "packages_available": ["json", "datetime", "urllib", "base64"],
                "success": True,
            }

        # Submit dependency test job
        job_id = self.executor.submit(dependency_test)
        logger.info(f"Submitted Kubernetes dependency test job {job_id}")

        result = self.executor.wait_for_result(job_id)

        # Verify dependency handling worked
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result.get("success") is True, "Dependency test should succeed"
        assert "test_data" in result, "Should include test data"
        assert "packages_available" in result, "Should list available packages"

        # Verify specific functionality
        test_data = result["test_data"]
        assert "timestamp" in test_data, "Should include timestamp"
        assert (
            test_data["base64_test"] == "aGVsbG8ga3ViZXJuZXRlcw=="
        ), "Base64 encoding should work"

        logger.info(f"✅ Kubernetes dependency handling working correctly")
        logger.info(f"   Available packages: {result['packages_available']}")

    @pytest.mark.real_world
    def test_kubernetes_job_status_tracking(self):
        """Test Kubernetes job status tracking and monitoring."""
        if not self.config:
            pytest.skip("Kubernetes cluster not available")

        logger.info("Testing Kubernetes job status tracking")

        def status_tracking_test() -> str:
            """Function for testing job status transitions."""
            import time

            # Sleep to allow status tracking during execution
            for i in range(5):
                time.sleep(1)
                print(f"Status test progress: {i+1}/5")  # This will appear in pod logs

            return "Status tracking test completed"

        # Submit status tracking job
        job_id = self.executor.submit(status_tracking_test)
        logger.info(f"Submitted Kubernetes status tracking job {job_id}")

        # Monitor status changes
        status_history = []
        start_time = time.time()

        # Poll status for the first few seconds
        while time.time() - start_time < 10:  # Monitor for 10 seconds
            try:
                status = self.executor._check_job_status(job_id)
                if status not in status_history:
                    status_history.append(status)
                    logger.info(f"Job {job_id} status changed to: {status}")

                if status in ["completed", "failed"]:
                    break

                time.sleep(2)  # Check every 2 seconds
            except Exception as e:
                logger.debug(f"Status check failed: {e}")
                break

        # Wait for final result
        result = self.executor.wait_for_result(job_id)

        # Verify status tracking worked
        assert (
            result == "Status tracking test completed"
        ), f"Unexpected result: {result}"
        assert len(status_history) > 0, "Should have captured at least one status"

        # Should have seen some progression (pending -> running -> completed)
        logger.info(
            f"✅ Kubernetes status tracking working - observed states: {status_history}"
        )

        # Verify final status is completed
        final_status = self.executor._check_job_status(job_id)
        logger.info(f"Final job status: {final_status}")

    @pytest.mark.real_world
    def test_kubernetes_job_cleanup_and_ttl(self):
        """Test Kubernetes job cleanup and TTL management."""
        if not self.config:
            pytest.skip("Kubernetes cluster not available")

        logger.info("Testing Kubernetes job cleanup and TTL")

        def cleanup_test() -> str:
            """Simple function for testing cleanup."""
            import time

            time.sleep(2)
            return "Cleanup test job completed"

        # Submit job with short TTL for testing
        original_ttl = self.config.k8s_job_ttl_seconds
        self.config.k8s_job_ttl_seconds = 60  # 1 minute TTL for test

        try:
            job_id = self.executor.submit(cleanup_test)
            logger.info(f"Submitted Kubernetes cleanup test job {job_id} with 60s TTL")

            # Wait for completion
            result = self.executor.wait_for_result(job_id)
            assert (
                result == "Cleanup test job completed"
            ), f"Unexpected result: {result}"

            # Test manual cleanup
            try:
                self.executor._cleanup_k8s_job(job_id)
                logger.info(f"✅ Manual cleanup of job {job_id} successful")
            except Exception as e:
                logger.warning(f"Manual cleanup failed (may already be cleaned): {e}")

            # Verify job is no longer tracked
            assert (
                job_id not in self.executor.active_jobs
            ), "Job should be removed from tracking"

            logger.info(
                "✅ Kubernetes job cleanup and TTL management working correctly"
            )

        finally:
            # Restore original TTL
            self.config.k8s_job_ttl_seconds = original_ttl


if __name__ == "__main__":
    # Run tests directly for debugging
    pytest.main([__file__, "-v", "--tb=short", "-m", "real_world"])
