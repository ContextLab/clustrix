"""
Reference patterns for Kubernetes workflows.

These patterns demonstrate how users would use clustrix with Kubernetes,
including auto-provisioning, multi-node deployments, and GPU workloads.
"""

import pytest
import os
import time
from clustrix import cluster
from clustrix.config import ClusterConfig
import clustrix.config as config_module


def test_kubernetes_auto_provisioning_workflow():
    """
    Reference pattern for Kubernetes auto-provisioning.

    This demonstrates:
    - K8s auto-provisioning configuration
    - Real cloud provider integration (AWS/GCP/Azure)
    - Container-based execution
    - Cleanup on exit
    """

    # User configures for auto-provisioning
    config = ClusterConfig()
    config.cluster_type = "kubernetes"
    config.auto_provision_k8s = True

    # Use environment to determine provider (local for CI, cloud for integration tests)
    provider = os.getenv("K8S_TEST_PROVIDER", "local")
    config.k8s_provider = provider

    if provider == "aws":
        config.k8s_region = os.getenv("AWS_REGION", "us-west-2")
        config.k8s_node_type = "t3.medium"
    elif provider == "gcp":
        config.k8s_region = os.getenv("GCP_REGION", "us-central1")
        config.k8s_node_type = "e2-medium"
    elif provider == "azure":
        config.k8s_region = os.getenv("AZURE_REGION", "eastus")
        config.k8s_node_type = "Standard_B2s"

    config.k8s_node_count = 2
    config.k8s_cleanup_on_exit = True
    config.k8s_cluster_name = f"test-cluster-{int(time.time())}"

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(
            platform="kubernetes",
            auto_provision=True,
            provider=provider,
            node_count=2,
            cores=2,
            memory="4Gi",
            parallel=False,
        )
        def train_model(data_size, epochs=10):
            """Train a simple ML model on Kubernetes."""
            import numpy as np
            import time
            from sklearn.linear_model import LogisticRegression
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import accuracy_score

            start_time = time.time()

            # Generate synthetic dataset
            np.random.seed(42)
            X = np.random.randn(data_size, 20)  # 20 features
            # Create labels with some pattern
            y = (X[:, 0] + X[:, 1] * 0.5 + np.random.randn(data_size) * 0.1 > 0).astype(
                int
            )

            # Split data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )

            # Train model
            model = LogisticRegression(max_iter=epochs * 10)
            model.fit(X_train, y_train)

            # Evaluate
            train_score = accuracy_score(y_train, model.predict(X_train))
            test_score = accuracy_score(y_test, model.predict(X_test))

            training_time = time.time() - start_time

            # Get environment info to verify K8s execution
            import socket
            import platform

            return {
                "train_accuracy": float(train_score),
                "test_accuracy": float(test_score),
                "data_size": data_size,
                "epochs": epochs,
                "training_time": training_time,
                "environment": {
                    "hostname": socket.gethostname(),
                    "platform": platform.platform(),
                    "python_version": platform.python_version(),
                },
                "model_coefficients": model.coef_.tolist()[0][
                    :5
                ],  # First 5 coefficients
            }

        # Execute with auto-provisioning
        result = train_model(1000, epochs=5)

        # Validate results
        assert result["data_size"] == 1000
        assert result["epochs"] == 5
        assert (
            0.4 <= result["test_accuracy"] <= 1.0
        )  # Should achieve reasonable accuracy
        assert result["training_time"] > 0
        assert len(result["model_coefficients"]) == 5

        # Verify Kubernetes execution (hostname should indicate pod/node)
        hostname = result["environment"]["hostname"]
        # In K8s, hostname typically contains pod name or node identifier
        assert len(hostname) > 0

    finally:
        config_module._config = original_config


def test_kubernetes_multi_node_workflow():
    """
    Reference pattern for multi-node Kubernetes workloads.

    This demonstrates:
    - Multi-node cluster configuration
    - Distributed computation
    - Node coordination
    - Resource distribution
    """

    config = ClusterConfig()
    config.cluster_type = "kubernetes"
    config.auto_provision_k8s = True
    config.k8s_provider = os.getenv("K8S_TEST_PROVIDER", "local")
    config.k8s_node_count = 3  # Multi-node cluster
    config.k8s_cleanup_on_exit = True

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(
            platform="kubernetes",
            auto_provision=True,
            node_count=3,
            cores=4,
            memory="8Gi",
        )
        def distributed_matrix_computation(matrix_size):
            """Perform distributed matrix operations."""
            import numpy as np
            from scipy import linalg
            import time

            start_time = time.time()

            # Create large random matrix
            np.random.seed(42)
            A = np.random.randn(matrix_size, matrix_size)
            B = np.random.randn(matrix_size, matrix_size)

            # Perform various matrix operations
            results = {}

            # Matrix multiplication
            C = np.matmul(A, B)
            results["matmul_shape"] = C.shape
            results["matmul_trace"] = float(np.trace(C))

            # Eigenvalue decomposition (computationally intensive)
            eigenvalues, _ = linalg.eig(A[:100, :100])  # Use subset for speed
            results["eigenvalues"] = {
                "count": len(eigenvalues),
                "max_real": float(np.max(eigenvalues.real)),
                "min_real": float(np.min(eigenvalues.real)),
            }

            # SVD (another intensive operation)
            U, s, Vt = linalg.svd(B[:100, :100], full_matrices=False)
            results["svd"] = {
                "singular_values": s[:5].tolist(),  # First 5 singular values
                "condition_number": float(s[0] / s[-1]),
            }

            computation_time = time.time() - start_time
            results["computation_time"] = computation_time
            results["matrix_size"] = matrix_size

            return results

        # Execute distributed computation
        result = distributed_matrix_computation(500)

        # Validate results
        assert result["matmul_shape"] == (500, 500)
        assert result["eigenvalues"]["count"] == 100
        assert len(result["svd"]["singular_values"]) == 5
        assert result["computation_time"] > 0
        assert result["svd"]["condition_number"] > 1  # Should be > 1 for random matrix

    finally:
        config_module._config = original_config


def test_kubernetes_gpu_workflow():
    """
    Reference pattern for GPU-enabled Kubernetes workloads.

    This demonstrates:
    - GPU resource requests
    - CUDA computation
    - GPU memory management
    - Performance comparison
    """

    # Skip if no GPU available
    gpu_available = os.getenv("K8S_GPU_AVAILABLE", "false").lower() == "true"
    if not gpu_available:
        pytest.skip("GPU not available for testing")

    config = ClusterConfig()
    config.cluster_type = "kubernetes"
    config.auto_provision_k8s = True
    config.k8s_provider = os.getenv("K8S_GPU_PROVIDER", "aws")
    config.k8s_node_type = (
        "p3.2xlarge" if config.k8s_provider == "aws" else "n1-standard-4"
    )
    config.k8s_node_count = 1
    config.k8s_cleanup_on_exit = True

    original_config = config_module._config
    config_module._config = config

    try:

        @cluster(
            platform="kubernetes",
            auto_provision=True,
            cores=4,
            memory="16Gi",
            gpu=1,  # Request 1 GPU
            parallel=False,
        )
        def gpu_computation(array_size):
            """Perform GPU-accelerated computation."""
            import numpy as np
            import time

            # Try to use CuPy for GPU acceleration
            try:
                import cupy as cp

                gpu_available = True
            except ImportError:
                cp = np  # Fallback to NumPy
                gpu_available = False

            start_time = time.time()

            # Create large arrays
            if gpu_available:
                # GPU computation
                a_gpu = cp.random.randn(array_size, array_size).astype(cp.float32)
                b_gpu = cp.random.randn(array_size, array_size).astype(cp.float32)

                # Matrix multiplication on GPU
                c_gpu = cp.matmul(a_gpu, b_gpu)

                # Ensure computation completes
                cp.cuda.Stream.null.synchronize()

                result = {
                    "computation_device": "GPU",
                    "array_size": array_size,
                    "result_sum": float(cp.sum(c_gpu)),
                    "result_mean": float(cp.mean(c_gpu)),
                    "gpu_memory_used": cp.get_default_memory_pool().used_bytes(),
                }
            else:
                # CPU computation for comparison
                a_cpu = np.random.randn(array_size, array_size).astype(np.float32)
                b_cpu = np.random.randn(array_size, array_size).astype(np.float32)

                c_cpu = np.matmul(a_cpu, b_cpu)

                result = {
                    "computation_device": "CPU",
                    "array_size": array_size,
                    "result_sum": float(np.sum(c_cpu)),
                    "result_mean": float(np.mean(c_cpu)),
                }

            computation_time = time.time() - start_time
            result["computation_time"] = computation_time

            return result

        # Execute GPU computation
        result = gpu_computation(1000)

        # Validate results
        assert result["array_size"] == 1000
        assert result["computation_time"] > 0
        assert "result_sum" in result
        assert "result_mean" in result

        # If GPU was used, should be faster than CPU for large matrices
        if result["computation_device"] == "GPU":
            assert result["gpu_memory_used"] > 0
            # GPU should complete in reasonable time
            assert result["computation_time"] < 10  # seconds

    finally:
        config_module._config = original_config
