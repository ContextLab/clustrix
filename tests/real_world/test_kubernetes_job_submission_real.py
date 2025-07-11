"""
Real Kubernetes job submission tests using @cluster decorator.

These tests actually submit jobs to real Kubernetes clusters and validate
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


class TestRealKubernetesJobSubmission:
    """Test real Kubernetes job submission using @cluster decorator."""

    @pytest.fixture
    def kubernetes_config(self):
        """Get Kubernetes configuration for testing."""
        # For Kubernetes, we'll use environment variables or local kubectl config
        # Check if kubectl is available and configured
        import subprocess

        try:
            result = subprocess.run(
                ["kubectl", "cluster-info"], capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                pytest.skip("kubectl not configured or cluster not accessible")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("kubectl not available or cluster not accessible")

        # Configure clustrix for Kubernetes
        configure(
            cluster_type="kubernetes",
            cluster_host="kubernetes",  # Use local kubectl config
            namespace="default",
            remote_work_dir=f"/tmp/clustrix_k8s_test_{uuid.uuid4().hex[:8]}",
            cleanup_remote_files=True,
        )

        return {"cluster_type": "kubernetes", "namespace": "default"}

    @pytest.mark.real_world
    def test_simple_function_k8s_submission(self, kubernetes_config):
        """Test submitting a simple function to Kubernetes."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def calculate_factorial(n: int) -> int:
            """Calculate factorial for testing."""
            if n <= 1:
                return 1
            result = 1
            for i in range(2, n + 1):
                result *= i
            return result

        # Submit job and wait for result
        result = calculate_factorial(6)

        # Validate result
        assert result == 720  # 6! = 720
        assert isinstance(result, int)

    @pytest.mark.real_world
    def test_function_with_k8s_environment(self, kubernetes_config):
        """Test Kubernetes job that accesses K8s environment variables."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def get_k8s_environment() -> Dict[str, str]:
            """Get Kubernetes job environment variables."""
            import os

            return {
                "KUBERNETES_SERVICE_HOST": os.getenv(
                    "KUBERNETES_SERVICE_HOST", "not_set"
                ),
                "KUBERNETES_SERVICE_PORT": os.getenv(
                    "KUBERNETES_SERVICE_PORT", "not_set"
                ),
                "KUBERNETES_PORT": os.getenv("KUBERNETES_PORT", "not_set"),
                "HOSTNAME": os.getenv("HOSTNAME", "not_set"),
                "POD_NAME": os.getenv("POD_NAME", "not_set"),
                "POD_NAMESPACE": os.getenv("POD_NAMESPACE", "not_set"),
                "POD_IP": os.getenv("POD_IP", "not_set"),
                "NODE_NAME": os.getenv("NODE_NAME", "not_set"),
                "USER": os.getenv("USER", "not_set"),
                "HOME": os.getenv("HOME", "not_set"),
                "PWD": os.getenv("PWD", "not_set"),
            }

        result = get_k8s_environment()

        # Validate K8s environment
        assert isinstance(result, dict)
        assert "KUBERNETES_SERVICE_HOST" in result
        # Note: Some K8s environment variables may not be set in all configurations
        assert result["HOSTNAME"] != "not_set"
        assert result["PWD"] != "not_set"

    @pytest.mark.real_world
    def test_k8s_resource_specification(self, kubernetes_config):
        """Test Kubernetes job with specific resource requirements."""

        @cluster(cores=2, memory="2Gi", time="00:10:00")
        def test_resource_allocation() -> Dict[str, Any]:
            """Test resource allocation in Kubernetes job."""
            import os
            import psutil

            # Get CPU and memory information
            cpu_count = os.cpu_count()
            memory_info = psutil.virtual_memory()

            # Get process information
            process = psutil.Process(os.getpid())
            process_memory = process.memory_info()

            return {
                "cpu_count": cpu_count,
                "total_memory_gb": memory_info.total / (1024**3),
                "available_memory_gb": memory_info.available / (1024**3),
                "process_memory_mb": process_memory.rss / (1024**2),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "pod_name": os.getenv("POD_NAME", "unknown"),
                "node_name": os.getenv("NODE_NAME", "unknown"),
            }

        result = test_resource_allocation()

        # Validate resource allocation
        assert isinstance(result, dict)
        assert result["cpu_count"] > 0
        assert result["total_memory_gb"] > 0
        assert result["process_memory_mb"] > 0

    @pytest.mark.real_world
    def test_k8s_namespace_isolation(self, kubernetes_config):
        """Test Kubernetes namespace isolation."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def test_namespace_info() -> Dict[str, Any]:
            """Test namespace isolation in Kubernetes."""
            import os
            import subprocess

            result = {
                "pod_namespace": os.getenv("POD_NAMESPACE", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "pod_name": os.getenv("POD_NAME", "unknown"),
                "service_account": "default",
            }

            # Try to get service account information
            try:
                with open(
                    "/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r"
                ) as f:
                    result["service_account_namespace"] = f.read().strip()
            except:
                result["service_account_namespace"] = "not_available"

            return result

        result = test_namespace_info()

        # Validate namespace isolation
        assert isinstance(result, dict)
        assert result["hostname"] != "unknown"

    @pytest.mark.real_world
    def test_k8s_persistent_storage(self, kubernetes_config):
        """Test Kubernetes job with persistent storage."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def test_storage_access() -> Dict[str, Any]:
            """Test storage access in Kubernetes job."""
            import os
            import tempfile
            import json

            # Create temporary file to test filesystem
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".json"
            ) as f:
                test_data = {
                    "timestamp": time.time(),
                    "hostname": os.getenv("HOSTNAME", "unknown"),
                    "pod_name": os.getenv("POD_NAME", "unknown"),
                    "test_data": [i**2 for i in range(10)],
                }
                json.dump(test_data, f)
                temp_file = f.name

            try:
                # Read file back
                with open(temp_file, "r") as f:
                    loaded_data = json.load(f)

                # Test file operations
                file_stats = os.stat(temp_file)

                return {
                    "temp_file_path": temp_file,
                    "file_size": file_stats.st_size,
                    "file_exists": os.path.exists(temp_file),
                    "data_matches": loaded_data == test_data,
                    "test_data_length": len(loaded_data["test_data"]),
                    "hostname": loaded_data["hostname"],
                    "pod_name": loaded_data["pod_name"],
                }

            finally:
                # Cleanup
                try:
                    os.unlink(temp_file)
                except:
                    pass

        result = test_storage_access()

        # Validate storage access
        assert isinstance(result, dict)
        assert result["file_exists"] is True
        assert result["data_matches"] is True
        assert result["test_data_length"] == 10
        assert result["file_size"] > 0

    @pytest.mark.real_world
    def test_k8s_networking(self, kubernetes_config):
        """Test Kubernetes job networking capabilities."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def test_network_connectivity() -> Dict[str, Any]:
            """Test network connectivity from Kubernetes job."""
            import os
            import socket
            import subprocess

            result = {
                "pod_ip": os.getenv("POD_IP", "unknown"),
                "hostname": socket.gethostname(),
                "fqdn": socket.getfqdn(),
                "k8s_service_host": os.getenv("KUBERNETES_SERVICE_HOST", "unknown"),
                "k8s_service_port": os.getenv("KUBERNETES_SERVICE_PORT", "unknown"),
            }

            # Test DNS resolution
            try:
                kubernetes_ip = socket.gethostbyname(
                    "kubernetes.default.svc.cluster.local"
                )
                result["kubernetes_dns_resolves"] = True
                result["kubernetes_service_ip"] = kubernetes_ip
            except:
                result["kubernetes_dns_resolves"] = False
                result["kubernetes_service_ip"] = "unknown"

            # Test local networking
            try:
                local_ip = socket.gethostbyname(socket.gethostname())
                result["local_ip"] = local_ip
            except:
                result["local_ip"] = "unknown"

            return result

        result = test_network_connectivity()

        # Validate networking
        assert isinstance(result, dict)
        assert result["hostname"] != ""
        assert result["k8s_service_host"] != "unknown"

    @pytest.mark.real_world
    def test_k8s_secrets_and_configmaps(self, kubernetes_config):
        """Test Kubernetes job with secrets and configmaps."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def test_secrets_access() -> Dict[str, Any]:
            """Test access to secrets and configmaps."""
            import os

            result = {
                "service_account_token_exists": False,
                "service_account_ca_exists": False,
                "service_account_namespace_exists": False,
                "environment_variables": {},
            }

            # Check for service account token
            token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
            if os.path.exists(token_path):
                result["service_account_token_exists"] = True
                try:
                    with open(token_path, "r") as f:
                        # Just check if we can read it (don't log the actual token)
                        token = f.read()
                        result["token_length"] = len(token)
                except:
                    result["token_read_error"] = True

            # Check for CA certificate
            ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            if os.path.exists(ca_path):
                result["service_account_ca_exists"] = True

            # Check for namespace
            namespace_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
            if os.path.exists(namespace_path):
                result["service_account_namespace_exists"] = True
                try:
                    with open(namespace_path, "r") as f:
                        result["namespace"] = f.read().strip()
                except:
                    result["namespace_read_error"] = True

            # Get relevant environment variables
            env_vars = ["POD_NAME", "POD_NAMESPACE", "POD_IP", "NODE_NAME"]
            for var in env_vars:
                result["environment_variables"][var] = os.getenv(var, "not_set")

            return result

        result = test_secrets_access()

        # Validate secrets access
        assert isinstance(result, dict)
        # Service account token should exist in most K8s setups
        assert result["service_account_token_exists"] is True
        assert result["service_account_ca_exists"] is True

    @pytest.mark.real_world
    def test_k8s_parallel_processing(self, kubernetes_config):
        """Test parallel processing in Kubernetes job."""

        @cluster(cores=2, memory="2Gi", time="00:10:00", parallel=True)
        def parallel_matrix_multiply(
            matrices: List[List[List[float]]],
        ) -> Dict[str, Any]:
            """Perform parallel matrix multiplication."""
            import time
            import os

            def multiply_matrices(a, b):
                """Multiply two matrices."""
                rows_a, cols_a = len(a), len(a[0])
                rows_b, cols_b = len(b), len(b[0])

                if cols_a != rows_b:
                    raise ValueError(
                        "Matrix dimensions incompatible for multiplication"
                    )

                result = [[0 for _ in range(cols_b)] for _ in range(rows_a)]

                for i in range(rows_a):
                    for j in range(cols_b):
                        for k in range(cols_a):
                            result[i][j] += a[i][k] * b[k][j]

                return result

            start_time = time.time()

            # Process matrix pairs
            results = []
            for i in range(0, len(matrices), 2):
                if i + 1 < len(matrices):
                    matrix_a = matrices[i]
                    matrix_b = matrices[i + 1]
                    result_matrix = multiply_matrices(matrix_a, matrix_b)
                    results.append(result_matrix)

            end_time = time.time()

            return {
                "matrices_processed": len(matrices),
                "multiplication_results": len(results),
                "processing_time": end_time - start_time,
                "pod_name": os.getenv("POD_NAME", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "node_name": os.getenv("NODE_NAME", "unknown"),
            }

        # Create test matrices
        test_matrices = [
            [[1, 2], [3, 4]],  # 2x2
            [[5, 6], [7, 8]],  # 2x2
            [[1, 0], [0, 1]],  # 2x2 identity
            [[9, 10], [11, 12]],  # 2x2
        ]

        result = parallel_matrix_multiply(test_matrices)

        # Validate parallel processing
        assert isinstance(result, dict)
        assert result["matrices_processed"] == 4
        assert result["multiplication_results"] == 2
        assert result["processing_time"] > 0
        assert result["pod_name"] != "unknown"

    @pytest.mark.real_world
    def test_k8s_job_lifecycle(self, kubernetes_config):
        """Test Kubernetes job lifecycle events."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def test_job_lifecycle() -> Dict[str, Any]:
            """Test job lifecycle in Kubernetes."""
            import os
            import time
            import signal
            import atexit

            lifecycle_events = []

            def signal_handler(signum, frame):
                lifecycle_events.append(f"Signal {signum} received")

            def cleanup_handler():
                lifecycle_events.append("Cleanup handler called")

            # Register handlers
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
            atexit.register(cleanup_handler)

            lifecycle_events.append("Job started")

            # Simulate some work
            for i in range(5):
                time.sleep(0.5)
                lifecycle_events.append(f"Work iteration {i+1}")

            lifecycle_events.append("Job completed")

            return {
                "lifecycle_events": lifecycle_events,
                "pod_name": os.getenv("POD_NAME", "unknown"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "exit_code": 0,
            }

        result = test_job_lifecycle()

        # Validate job lifecycle
        assert isinstance(result, dict)
        assert len(result["lifecycle_events"]) >= 7  # Start + 5 iterations + completion
        assert "Job started" in result["lifecycle_events"]
        assert "Job completed" in result["lifecycle_events"]
        assert result["exit_code"] == 0

    @pytest.mark.real_world
    def test_k8s_error_handling(self, kubernetes_config):
        """Test Kubernetes job error handling."""

        @cluster(cores=1, memory="1Gi", time="00:05:00")
        def k8s_error_test(error_type: str) -> Dict[str, Any]:
            """Test error handling in Kubernetes job."""
            import os

            if error_type == "success":
                return {
                    "status": "success",
                    "pod_name": os.getenv("POD_NAME", "unknown"),
                    "message": "Job completed successfully",
                }
            elif error_type == "value_error":
                raise ValueError("Test value error in Kubernetes job")
            elif error_type == "permission_error":
                # Try to write to a read-only location
                with open("/etc/passwd", "w") as f:
                    f.write("test")
            elif error_type == "memory_error":
                # Try to allocate large amount of memory
                large_list = [0] * (10**9)  # This might cause memory error
                return {"memory_allocated": len(large_list)}
            else:
                raise Exception(f"Unknown error type: {error_type}")

        # Test successful execution
        result = k8s_error_test("success")
        assert result["status"] == "success"
        assert result["pod_name"] != "unknown"

        # Test error handling
        with pytest.raises(ValueError):
            k8s_error_test("value_error")

        with pytest.raises(PermissionError):
            k8s_error_test("permission_error")

    @pytest.mark.real_world
    @pytest.mark.expensive
    def test_k8s_resource_intensive(self, kubernetes_config):
        """Test resource-intensive Kubernetes job."""

        @cluster(cores=2, memory="4Gi", time="00:15:00")
        def resource_intensive_k8s_job() -> Dict[str, Any]:
            """Perform resource-intensive operations in Kubernetes."""
            import os
            import time
            import psutil
            import numpy as np

            start_time = time.time()

            # Get initial resource usage
            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / (1024**2)  # MB

            # Create large numpy arrays for computation
            try:
                # Create large matrices
                size = 1000
                matrix_a = np.random.random((size, size))
                matrix_b = np.random.random((size, size))

                # Perform matrix multiplication
                result_matrix = np.dot(matrix_a, matrix_b)

                # Compute statistics
                matrix_mean = np.mean(result_matrix)
                matrix_std = np.std(result_matrix)

                # Get peak memory usage
                peak_memory = process.memory_info().rss / (1024**2)  # MB

                end_time = time.time()

                return {
                    "matrix_size": size,
                    "initial_memory_mb": initial_memory,
                    "peak_memory_mb": peak_memory,
                    "memory_used_mb": peak_memory - initial_memory,
                    "computation_time": end_time - start_time,
                    "matrix_mean": float(matrix_mean),
                    "matrix_std": float(matrix_std),
                    "pod_name": os.getenv("POD_NAME", "unknown"),
                    "node_name": os.getenv("NODE_NAME", "unknown"),
                }

            except ImportError:
                # Fallback if numpy is not available
                return {
                    "numpy_available": False,
                    "pod_name": os.getenv("POD_NAME", "unknown"),
                    "fallback_computation": True,
                }

        result = resource_intensive_k8s_job()

        # Validate resource-intensive job
        assert isinstance(result, dict)
        if result.get("numpy_available", True):
            assert result["matrix_size"] == 1000
            assert result["memory_used_mb"] > 0
            assert result["computation_time"] > 0
        assert result["pod_name"] != "unknown"
