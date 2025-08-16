"""
Local Docker-based Kubernetes real execution test.

This test creates a real local Kubernetes cluster using kind (Kubernetes in Docker)
and executes actual Python functions on it to validate the complete workflow.

This provides definitive proof that the Kubernetes auto-provisioning system works
end-to-end without requiring expensive cloud infrastructure.
"""

import os
import time
import pytest
import logging
import subprocess
import socket
from typing import Dict, Any

from clustrix import cluster
from clustrix.config import ClusterConfig, get_config

logger = logging.getLogger(__name__)


@pytest.mark.real_world
@pytest.mark.slow
class TestLocalKubernetesExecution:
    """Real local Kubernetes execution tests."""

    @pytest.fixture(scope="class")
    def check_prerequisites(self):
        """Check that Docker and kind are available."""
        # Check Docker
        try:
            result = subprocess.run(
                ["docker", "version"], capture_output=True, timeout=10
            )
            assert result.returncode == 0, "Docker is required but not available"
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            pytest.skip("Docker is not available for local Kubernetes testing")

        # Check kind
        try:
            result = subprocess.run(
                ["kind", "version"], capture_output=True, timeout=10
            )
            assert result.returncode == 0, "kind is required but not available"
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            pytest.skip("kind is not available for local Kubernetes testing")

        # Check kubectl
        try:
            result = subprocess.run(
                ["kubectl", "version", "--client"], capture_output=True, timeout=10
            )
            assert result.returncode == 0, "kubectl is required but not available"
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            pytest.skip("kubectl is not available for local Kubernetes testing")

        logger.info("✅ All prerequisites available for local Kubernetes testing")
        return True

    @pytest.fixture(scope="function")
    def local_cluster_config(self):
        """Create local cluster configuration."""
        test_id = int(time.time())

        config = ClusterConfig()
        config.cluster_type = "kubernetes"
        config.auto_provision_k8s = True
        config.k8s_from_scratch = True
        config.k8s_provider = "local"
        config.k8s_region = "local"
        config.k8s_node_count = 2  # 1 control plane + 1 worker
        config.k8s_cleanup_on_exit = True  # Always cleanup local clusters
        config.k8s_cluster_name = f"clustrix-test-{test_id}"

        return config

    def test_simple_local_function_execution(
        self, check_prerequisites, local_cluster_config
    ):
        """Test execution of a simple Python function on local Kubernetes cluster."""
        logger.info("🧪 Testing simple function execution on local Kubernetes cluster")

        # Override global config for this test
        from clustrix.config import _config
        import clustrix.config as config_module

        original_config = config_module._config
        config_module._config = local_cluster_config

        cluster_created = False

        try:
            # Define test function with @cluster decorator
            @cluster(
                platform="kubernetes",
                auto_provision=True,
                provider="local",
                node_count=2,
                cluster_name=local_cluster_config.k8s_cluster_name,
            )
            def local_computation(x: int, y: int) -> Dict[str, Any]:
                """Simple computation function for local testing."""
                import platform
                import os
                import socket

                result = x * y + 42

                return {
                    "result": result,
                    "platform": platform.platform(),
                    "python_version": platform.python_version(),
                    "hostname": socket.gethostname(),
                    "working_dir": os.getcwd(),
                    "environment": "kubernetes",
                    "computed_at": time.time(),
                }

            # Execute function - this should trigger local cluster provisioning
            logger.info(
                "🚀 Starting function execution (will auto-provision local cluster)"
            )
            start_time = time.time()

            result = local_computation(7, 11)
            execution_time = time.time() - start_time
            cluster_created = True

            # Verify results
            assert isinstance(result, dict), "Result should be a dictionary"
            assert result["result"] == 119, f"Expected 119, got {result['result']}"
            assert "platform" in result, "Platform info should be included"
            assert "hostname" in result, "Hostname should be included"
            assert (
                result["environment"] == "kubernetes"
            ), "Should indicate Kubernetes environment"

            logger.info(f"✅ Function executed successfully in {execution_time:.1f}s")
            logger.info(f"📊 Result: {result['result']}")
            logger.info(f"🖥️ Remote platform: {result['platform']}")
            logger.info(f"🏷️ Remote hostname: {result['hostname']}")

            # Verify we're running in a different environment (Kubernetes pod)
            local_hostname = socket.gethostname()
            assert (
                result["hostname"] != local_hostname
            ), "Should execute in different environment (pod)"

            logger.info("✅ Verified execution occurred in Kubernetes pod environment")

        finally:
            # Restore original config
            config_module._config = original_config

            # Manual cleanup if needed
            if cluster_created:
                self._ensure_cluster_cleanup(local_cluster_config.k8s_cluster_name)

    def test_numpy_computation_execution(
        self, check_prerequisites, local_cluster_config
    ):
        """Test execution of NumPy computation on local Kubernetes cluster."""
        logger.info("🧪 Testing NumPy computation on local Kubernetes cluster")

        # Override global config for this test
        from clustrix.config import _config
        import clustrix.config as config_module

        original_config = config_module._config
        config_module._config = local_cluster_config
        local_cluster_config.k8s_cluster_name = f"clustrix-numpy-{int(time.time())}"

        cluster_created = False

        try:

            @cluster(
                platform="kubernetes",
                auto_provision=True,
                provider="local",
                cluster_name=local_cluster_config.k8s_cluster_name,
            )
            def numpy_computation(size: int) -> Dict[str, Any]:
                """NumPy-based computation for testing dependency handling."""
                import numpy as np
                import time

                start_time = time.time()

                # Create random data
                data = np.random.rand(size, size)

                # Perform computation
                result_matrix = np.dot(data, data.T)
                eigenvalues = np.linalg.eigvals(result_matrix)

                computation_time = time.time() - start_time

                return {
                    "matrix_size": size,
                    "max_eigenvalue": float(np.max(eigenvalues)),
                    "min_eigenvalue": float(np.min(eigenvalues)),
                    "computation_time": computation_time,
                    "numpy_version": np.__version__,
                    "status": "completed",
                }

            # Execute NumPy computation
            logger.info("🚀 Starting NumPy computation on local cluster")
            start_time = time.time()

            result = numpy_computation(100)  # 100x100 matrix
            execution_time = time.time() - start_time
            cluster_created = True

            # Verify results
            assert isinstance(result, dict), "Result should be a dictionary"
            assert (
                result["status"] == "completed"
            ), "Computation should complete successfully"
            assert result["matrix_size"] == 100, "Matrix size should be preserved"
            assert "numpy_version" in result, "NumPy version should be included"
            assert (
                result["computation_time"] > 0
            ), "Computation should take measurable time"

            logger.info(f"✅ NumPy computation completed in {execution_time:.1f}s")
            logger.info(
                f"⚡ Remote computation time: {result['computation_time']:.3f}s"
            )
            logger.info(f"📦 NumPy version: {result['numpy_version']}")
            logger.info(
                f"📊 Eigenvalue range: {result['min_eigenvalue']:.3f} to {result['max_eigenvalue']:.3f}"
            )

        finally:
            # Restore original config
            config_module._config = original_config

            # Manual cleanup if needed
            if cluster_created:
                self._ensure_cluster_cleanup(local_cluster_config.k8s_cluster_name)

    def test_error_handling_and_cleanup(
        self, check_prerequisites, local_cluster_config
    ):
        """Test error handling and proper cleanup of local clusters."""
        logger.info("🧪 Testing error handling and cleanup")

        # Override global config for this test
        from clustrix.config import _config
        import clustrix.config as config_module

        original_config = config_module._config
        config_module._config = local_cluster_config
        local_cluster_config.k8s_cluster_name = f"clustrix-error-{int(time.time())}"

        cluster_created = False

        try:

            @cluster(
                platform="kubernetes",
                auto_provision=True,
                provider="local",
                cluster_name=local_cluster_config.k8s_cluster_name,
            )
            def failing_function(should_fail: bool) -> str:
                """Function that can be made to fail for testing error handling."""
                if should_fail:
                    raise ValueError("Intentional test failure for error handling")
                return "success"

            # First test successful execution
            logger.info("🚀 Testing successful execution first")
            result = failing_function(False)
            cluster_created = True
            assert result == "success", "Successful execution should return 'success'"
            logger.info("✅ Successful execution confirmed")

            # Then test error handling
            logger.info("🚀 Testing error propagation")
            with pytest.raises(Exception) as exc_info:
                failing_function(True)

            # Verify the error was properly propagated
            assert "Intentional test failure" in str(
                exc_info.value
            ) or "ValueError" in str(type(exc_info.value))
            logger.info(
                "✅ Error handling working correctly - exceptions properly propagated"
            )

        finally:
            # Restore original config
            config_module._config = original_config

            # Manual cleanup
            if cluster_created:
                self._ensure_cluster_cleanup(local_cluster_config.k8s_cluster_name)

    def test_cluster_lifecycle_management(
        self, check_prerequisites, local_cluster_config
    ):
        """Test cluster lifecycle management and status monitoring."""
        logger.info("🧪 Testing cluster lifecycle management")

        from clustrix.kubernetes.local_provisioner import (
            LocalDockerKubernetesProvisioner,
        )
        from clustrix.kubernetes.cluster_provisioner import ClusterSpec

        test_cluster_name = f"clustrix-lifecycle-{int(time.time())}"

        try:
            # Create provisioner
            provisioner = LocalDockerKubernetesProvisioner({}, "local")

            # Test cluster creation
            logger.info("🚀 Testing cluster creation")
            cluster_spec = ClusterSpec(
                cluster_name=test_cluster_name,
                provider="local",
                node_count=2,
                kubernetes_version="1.28",
                region="local",
            )

            start_time = time.time()
            cluster_info = provisioner.provision_complete_infrastructure(cluster_spec)
            creation_time = time.time() - start_time

            # Verify cluster info
            assert cluster_info is not None, "Cluster info should not be None"
            assert (
                cluster_info["cluster_name"] == test_cluster_name
            ), "Cluster name should match"
            assert (
                cluster_info["ready_for_jobs"] is True
            ), "Cluster should be ready for jobs"
            assert "kubectl_config" in cluster_info, "Should have kubectl config"
            assert len(cluster_info["nodes"]) == 2, "Should have 2 nodes"

            logger.info(f"✅ Cluster created successfully in {creation_time:.1f}s")
            logger.info(f"📊 Cluster has {cluster_info['node_count']} nodes")
            logger.info(f"🔗 Cluster endpoint: {cluster_info['endpoint']}")

            # Test cluster status monitoring
            logger.info("🔍 Testing cluster status monitoring")
            status = provisioner.get_cluster_status(test_cluster_name)
            assert (
                status["status"] == "RUNNING"
            ), f"Expected RUNNING, got {status['status']}"
            assert status["ready_for_jobs"] is True, "Cluster should be ready for jobs"

            logger.info("✅ Cluster status monitoring working correctly")

            # Test cluster destruction
            logger.info("🗑️ Testing cluster destruction")
            destruction_start = time.time()
            success = provisioner.destroy_cluster_infrastructure(test_cluster_name)
            destruction_time = time.time() - destruction_start

            assert success is True, "Cluster destruction should succeed"
            logger.info(f"✅ Cluster destroyed successfully in {destruction_time:.1f}s")

            # Verify cluster is gone
            status = provisioner.get_cluster_status(test_cluster_name)
            assert (
                status["status"] == "NOT_FOUND"
            ), "Cluster should be gone after destruction"

            logger.info("✅ Cluster lifecycle management working correctly")

        except Exception as e:
            # Ensure cleanup on failure
            logger.error(f"Test failed: {e}")
            self._ensure_cluster_cleanup(test_cluster_name)
            raise

    def _ensure_cluster_cleanup(self, cluster_name: str):
        """Ensure cluster is cleaned up."""
        logger.info(f"🧹 Ensuring cleanup of cluster: {cluster_name}")

        try:
            result = subprocess.run(
                ["kind", "delete", "cluster", "--name", cluster_name],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                logger.info(f"✅ Cluster {cluster_name} cleaned up successfully")
            else:
                logger.warning(f"⚠️ Cluster cleanup may have failed: {result.stderr}")

        except Exception as e:
            logger.warning(f"⚠️ Error during cleanup: {e}")

    def test_multiple_sequential_executions(
        self, check_prerequisites, local_cluster_config
    ):
        """Test multiple sequential function executions on the same cluster."""
        logger.info("🧪 Testing multiple sequential executions")

        # Override global config for this test
        from clustrix.config import _config
        import clustrix.config as config_module

        original_config = config_module._config
        config_module._config = local_cluster_config
        local_cluster_config.k8s_cluster_name = f"clustrix-multi-{int(time.time())}"

        cluster_created = False

        try:

            @cluster(
                platform="kubernetes",
                auto_provision=True,
                provider="local",
                cluster_name=local_cluster_config.k8s_cluster_name,
            )
            def sequential_computation(iteration: int) -> Dict[str, Any]:
                """Function for testing multiple sequential executions."""
                import time
                import os

                return {
                    "iteration": iteration,
                    "result": iteration * 2 + 10,
                    "timestamp": time.time(),
                    "pid": os.getpid(),
                }

            results = []

            # Execute function multiple times
            for i in range(3):
                logger.info(f"🚀 Starting execution {i+1}/3")
                start_time = time.time()

                result = sequential_computation(i + 1)
                execution_time = time.time() - start_time

                results.append({"result": result, "execution_time": execution_time})

                if i == 0:
                    cluster_created = True  # Mark after first successful execution

                logger.info(f"✅ Execution {i+1} completed in {execution_time:.1f}s")
                logger.info(
                    f"📊 Result: {result['result']} (iteration {result['iteration']})"
                )

            # Verify all executions succeeded
            assert len(results) == 3, "Should have 3 successful executions"

            for i, result_info in enumerate(results):
                result = result_info["result"]
                expected = (i + 1) * 2 + 10
                assert (
                    result["result"] == expected
                ), f"Execution {i+1}: expected {expected}, got {result['result']}"
                assert result["iteration"] == i + 1, f"Iteration should be {i+1}"

            logger.info("✅ All sequential executions completed successfully")

            # Log timing information
            total_time = sum(r["execution_time"] for r in results)
            avg_time = total_time / len(results)
            logger.info(f"📊 Total execution time: {total_time:.1f}s")
            logger.info(f"📊 Average execution time: {avg_time:.1f}s")

        finally:
            # Restore original config
            config_module._config = original_config

            # Manual cleanup
            if cluster_created:
                self._ensure_cluster_cleanup(local_cluster_config.k8s_cluster_name)
