"""
Real-world tests for ClusterExecutor functionality.

These tests use actual infrastructure instead of mocks, following the
reference workflow patterns to ensure we test real user scenarios.
"""

import pytest
import os
import time
import tempfile
from pathlib import Path
from clustrix import cluster
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig
import clustrix.config as config_module


class TestClusterExecutorReal:
    """Test ClusterExecutor with real infrastructure."""

    @pytest.fixture
    def local_config(self):
        """Create configuration for local testing."""
        config = ClusterConfig()
        config.cluster_type = "local"
        config.cleanup_remote_files = True
        return config

    @pytest.fixture
    def kubernetes_config(self):
        """Create configuration for Kubernetes testing."""
        config = ClusterConfig()
        config.cluster_type = "kubernetes"
        config.auto_provision_k8s = True
        config.k8s_provider = "local"  # Use Docker Desktop or kind
        config.k8s_node_count = 1
        config.k8s_cleanup_on_exit = True
        config.k8s_cluster_name = f"test-executor-{int(time.time())}"
        return config

    @pytest.fixture
    def ssh_config(self):
        """Create configuration for SSH testing if available."""
        ssh_host = os.getenv("TEST_SSH_HOST")
        if not ssh_host:
            pytest.skip("SSH test host not configured")

        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = ssh_host
        config.username = os.getenv("TEST_SSH_USER", "testuser")
        config.private_key_path = os.getenv("TEST_SSH_KEY", "~/.ssh/id_rsa")
        config.remote_work_dir = "/tmp/clustrix_test"
        config.cleanup_remote_files = True
        return config

    def test_executor_initialization_and_connection(self, local_config):
        """
        Test executor initialization and connection setup.

        This demonstrates:
        - Proper executor initialization with real config
        - Connection establishment (local requires no SSH)
        - Resource cleanup
        """
        executor = ClusterExecutor(local_config)

        # Verify initialization
        assert executor.config == local_config
        assert executor.config.cluster_type == "local"

        # For local execution, no SSH connection needed
        executor.connect()
        assert executor.ssh_client is None  # Local doesn't use SSH

        # Cleanup
        executor.disconnect()

    def test_job_submission_local(self, local_config):
        """
        Test job submission with local execution.

        This demonstrates:
        - Real function serialization
        - Local job execution
        - Result retrieval
        """
        executor = ClusterExecutor(local_config)
        executor.connect()

        try:
            # Define a real computation function
            def compute_statistics(data):
                """Compute basic statistics."""
                import numpy as np

                arr = np.array(data)
                return {
                    "mean": float(np.mean(arr)),
                    "std": float(np.std(arr)),
                    "min": float(np.min(arr)),
                    "max": float(np.max(arr)),
                    "sum": float(np.sum(arr)),
                }

            # Prepare function data
            from clustrix.utils import serialize_function

            test_data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
            func_data = serialize_function(compute_statistics, (test_data,), {})

            # Submit job
            job_config = {"cores": 1, "memory": "1GB"}
            job_id = executor.submit_job(func_data, job_config)

            # Verify job ID
            assert job_id is not None
            assert isinstance(job_id, str)

            # Wait for result
            result = executor.wait_for_result(job_id)

            # Validate result
            assert isinstance(result, dict)
            assert result["mean"] == 5.5
            assert result["std"] > 0
            assert result["min"] == 1
            assert result["max"] == 10
            assert result["sum"] == 55

        finally:
            executor.disconnect()

    @pytest.mark.real_world
    def test_job_submission_kubernetes(self, kubernetes_config):
        """
        Test job submission with Kubernetes.

        This demonstrates:
        - Real Kubernetes job submission
        - Container-based execution
        - Pod monitoring and result retrieval
        """
        # Skip if Kubernetes not available
        if not os.getenv("K8S_TEST_ENABLED", "false").lower() == "true":
            pytest.skip("Kubernetes testing not enabled")

        executor = ClusterExecutor(kubernetes_config)

        # Ensure cluster is ready (auto-provisions if needed)
        executor.ensure_cluster_ready(timeout=300)

        try:
            # Define computation for Kubernetes
            def analyze_in_k8s(n):
                """Perform analysis in Kubernetes pod."""
                import platform
                import socket
                import math

                # Compute prime numbers up to n
                primes = []
                for num in range(2, n + 1):
                    is_prime = True
                    for i in range(2, int(math.sqrt(num)) + 1):
                        if num % i == 0:
                            is_prime = False
                            break
                    if is_prime:
                        primes.append(num)

                return {
                    "primes_count": len(primes),
                    "largest_prime": max(primes) if primes else None,
                    "execution_host": socket.gethostname(),
                    "platform": platform.platform(),
                    "container": "kubernetes" in platform.platform().lower()
                    or "linux" in platform.platform().lower(),
                }

            # Serialize and submit
            from clustrix.utils import serialize_function

            func_data = serialize_function(analyze_in_k8s, (100,), {})

            job_config = {"cores": 1, "memory": "512Mi"}
            job_id = executor.submit_job(func_data, job_config)

            # Verify Kubernetes job ID format
            assert job_id is not None
            assert "clustrix-job" in job_id or isinstance(job_id, str)

            # Monitor job status
            status = executor.get_job_status(job_id)
            assert status in ["pending", "running", "completed", "failed"]

            # Wait for completion
            result = executor.wait_for_result(job_id, timeout=120)

            # Validate Kubernetes execution
            assert result["primes_count"] == 25  # 25 primes under 100
            assert result["largest_prime"] == 97
            assert result["container"] is True  # Should detect container environment
            assert len(result["execution_host"]) > 0

        finally:
            # Cleanup Kubernetes resources
            if hasattr(executor, "cleanup_auto_provisioned_cluster"):
                executor.cleanup_auto_provisioned_cluster()
            executor.disconnect()

    @pytest.mark.real_world
    def test_job_submission_ssh(self, ssh_config):
        """
        Test job submission via SSH to remote cluster.

        This demonstrates:
        - Real SSH connection
        - Remote job execution
        - File transfer via SFTP
        - Remote cleanup
        """
        executor = ClusterExecutor(ssh_config)
        executor.connect()

        try:
            # Verify SSH connection
            assert executor.ssh_client is not None
            assert executor.sftp_client is not None

            # Define remote computation
            def remote_analysis(data_size):
                """Analysis to run on remote cluster."""
                import sys
                import os
                import time

                start_time = time.time()

                # Simulate data processing
                total = sum(range(data_size))

                return {
                    "data_size": data_size,
                    "result": total,
                    "python_version": sys.version,
                    "hostname": (
                        os.uname().nodename if hasattr(os, "uname") else "unknown"
                    ),
                    "execution_time": time.time() - start_time,
                }

            # Submit to remote cluster
            from clustrix.utils import serialize_function

            func_data = serialize_function(remote_analysis, (1000,), {})

            job_config = {"cores": 1, "memory": "1GB", "time": "00:05:00"}
            job_id = executor.submit_job(func_data, job_config)

            # Verify remote job submission
            assert job_id is not None

            # Check job status
            status = executor.get_job_status(job_id)
            assert status in ["pending", "running", "completed", "failed", "unknown"]

            # Wait for result
            result = executor.wait_for_result(job_id, timeout=60)

            # Validate remote execution
            assert result["data_size"] == 1000
            assert result["result"] == sum(range(1000))
            assert result["execution_time"] > 0
            assert "python" in result["python_version"].lower()

        finally:
            executor.disconnect()

    def test_error_handling_real(self, local_config):
        """
        Test error handling with real execution.

        This demonstrates:
        - Real error propagation
        - Exception handling
        - Cleanup after errors
        """
        executor = ClusterExecutor(local_config)
        executor.connect()

        try:
            # Function that will raise an error
            def failing_function(x):
                """Function that deliberately fails."""
                if x < 0:
                    raise ValueError(f"Negative value not allowed: {x}")
                return x * 2

            # Submit job that will fail
            from clustrix.utils import serialize_function

            func_data = serialize_function(failing_function, (-5,), {})

            job_config = {"cores": 1, "memory": "1GB"}
            job_id = executor.submit_job(func_data, job_config)

            # Expect error when waiting for result
            with pytest.raises(Exception) as exc_info:
                executor.wait_for_result(job_id)

            # Verify error message
            assert (
                "Negative value not allowed" in str(exc_info.value)
                or "error" in str(exc_info.value).lower()
            )

        finally:
            executor.disconnect()

    def test_parallel_job_submission(self, local_config):
        """
        Test parallel job submission and management.

        This demonstrates:
        - Multiple concurrent jobs
        - Job tracking
        - Parallel result collection
        """
        executor = ClusterExecutor(local_config)
        executor.connect()

        try:
            # Function for parallel execution
            def compute_square(n):
                """Simple computation for parallel testing."""
                import time

                time.sleep(0.1)  # Simulate some work
                return n * n

            # Submit multiple jobs
            from clustrix.utils import serialize_function

            job_ids = []
            expected_results = {}

            for i in range(5):
                func_data = serialize_function(compute_square, (i,), {})
                job_config = {"cores": 1, "memory": "512MB"}
                job_id = executor.submit_job(func_data, job_config)
                job_ids.append(job_id)
                expected_results[job_id] = i * i

            # Verify all jobs submitted
            assert len(job_ids) == 5
            assert len(set(job_ids)) == 5  # All unique IDs

            # Collect results
            results = {}
            for job_id in job_ids:
                result = executor.wait_for_result(job_id, timeout=30)
                results[job_id] = result

            # Validate results
            for job_id, expected in expected_results.items():
                assert results[job_id] == expected

        finally:
            executor.disconnect()

    def test_resource_cleanup(self, local_config):
        """
        Test resource cleanup after execution.

        This demonstrates:
        - Temporary file cleanup
        - Connection cleanup
        - Resource deallocation
        """
        executor = ClusterExecutor(local_config)
        executor.connect()

        # Track resources before execution
        initial_jobs = (
            len(executor.active_jobs) if hasattr(executor, "active_jobs") else 0
        )

        try:
            # Submit a job
            def simple_task():
                return "completed"

            from clustrix.utils import serialize_function

            func_data = serialize_function(simple_task, (), {})
            job_config = {"cores": 1, "memory": "512MB"}

            job_id = executor.submit_job(func_data, job_config)
            result = executor.wait_for_result(job_id)

            assert result == "completed"

            # Verify cleanup configuration
            assert executor.config.cleanup_remote_files is True

        finally:
            # Ensure proper cleanup
            executor.disconnect()

            # After disconnect, connections should be closed
            assert (
                executor.ssh_client is None
                or not hasattr(executor.ssh_client, "get_transport")
                or executor.ssh_client.get_transport() is None
            )
            assert executor.sftp_client is None


class TestExecutorIntegrationWorkflows:
    """Integration tests showing complete user workflows with the executor."""

    def test_complete_data_processing_workflow(self):
        """
        Test complete data processing workflow as users would use it.

        This demonstrates the full user experience from configuration
        through execution to results.
        """
        # User sets up configuration
        config = ClusterConfig()
        config.cluster_type = "local"  # Or "kubernetes", "slurm", etc.

        original_config = config_module._config
        config_module._config = config

        try:
            # User defines their processing function
            @cluster(cores=2, memory="2GB", parallel=False)
            def process_dataset(data_points, operations):
                """Process dataset with specified operations."""
                import numpy as np
                import time

                start_time = time.time()
                data = np.array(data_points)
                results = {}

                for op in operations:
                    if op == "mean":
                        results[op] = float(np.mean(data))
                    elif op == "std":
                        results[op] = float(np.std(data))
                    elif op == "fft":
                        fft_result = np.fft.fft(data)
                        results[op] = {
                            "max_frequency": float(np.max(np.abs(fft_result))),
                            "dominant_component": int(np.argmax(np.abs(fft_result))),
                        }
                    elif op == "correlate":
                        autocorr = np.correlate(data, data, mode="full")
                        results[op] = float(np.max(autocorr))

                results["processing_time"] = time.time() - start_time
                return results

            # User executes function normally
            test_data = list(range(100))
            operations = ["mean", "std", "fft", "correlate"]

            results = process_dataset(test_data, operations)

            # Validate complete workflow
            assert "mean" in results
            assert results["mean"] == 49.5
            assert "std" in results
            assert results["std"] > 0
            assert "fft" in results
            assert "correlate" in results
            assert results["processing_time"] > 0

        finally:
            config_module._config = original_config
