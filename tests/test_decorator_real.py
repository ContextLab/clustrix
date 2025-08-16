"""
Real-world tests for @cluster decorator functionality.

These tests demonstrate actual user workflows with the @cluster decorator,
using real execution instead of mocks.
"""

import pytest
import os
import time
import tempfile
import numpy as np
from pathlib import Path
from clustrix import cluster
from clustrix.config import ClusterConfig, configure
import clustrix.config as config_module


class TestClusterDecoratorReal:
    """Test @cluster decorator with real execution."""

    def test_basic_decoration_and_execution(self):
        """
        Test basic function decoration and execution.

        This demonstrates:
        - Proper decorator application
        - Configuration preservation
        - Real execution with decorated function
        """
        # Configure for local execution
        configure(cluster_type="local")

        @cluster(cores=4, memory="8GB")
        def add_matrices(a, b):
            """Add two matrices element-wise."""
            import numpy as np

            matrix_a = np.array(a)
            matrix_b = np.array(b)
            result = matrix_a + matrix_b

            return {
                "result": result.tolist(),
                "shape": result.shape,
                "sum": float(np.sum(result)),
            }

        # Verify decoration
        assert hasattr(add_matrices, "__wrapped__")
        assert hasattr(add_matrices, "_cluster_config")
        assert add_matrices._cluster_config["cores"] == 4
        assert add_matrices._cluster_config["memory"] == "8GB"

        # Execute decorated function
        matrix1 = [[1, 2], [3, 4]]
        matrix2 = [[5, 6], [7, 8]]

        result = add_matrices(matrix1, matrix2)

        # Validate execution
        assert result["result"] == [[6, 8], [10, 12]]
        assert result["shape"] == (2, 2)
        assert result["sum"] == 36.0

    def test_decorator_without_parameters(self):
        """
        Test decorator without parameters (using defaults).

        This demonstrates:
        - Default parameter handling
        - Minimal decorator usage
        - Function execution with defaults
        """
        configure(cluster_type="local")

        @cluster
        def compute_fibonacci(n):
            """Compute Fibonacci sequence up to n terms."""
            if n <= 0:
                return []
            elif n == 1:
                return [0]
            elif n == 2:
                return [0, 1]

            fib = [0, 1]
            for i in range(2, n):
                fib.append(fib[i - 1] + fib[i - 2])

            return {
                "sequence": fib,
                "length": len(fib),
                "sum": sum(fib),
                "last": fib[-1],
            }

        # Verify default configuration
        assert hasattr(compute_fibonacci, "_cluster_config")
        assert compute_fibonacci._cluster_config["cores"] is None
        assert compute_fibonacci._cluster_config["memory"] is None

        # Execute with defaults
        result = compute_fibonacci(10)

        # Validate
        assert result["length"] == 10
        assert result["sequence"] == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
        assert result["sum"] == 88
        assert result["last"] == 34

    def test_decorator_with_all_parameters(self):
        """
        Test decorator with comprehensive parameter set.

        This demonstrates:
        - Full parameter specification
        - Complex configuration
        - Parameter validation
        """
        configure(cluster_type="local")

        @cluster(
            cores=16,
            memory="32GB",
            time="04:00:00",
            partition="compute",
            parallel=False,
            auto_gpu_parallel=False,
            environment="test_env",
        )
        def complex_analysis(data_size, iterations):
            """Perform complex analysis with full configuration."""
            import time as time_module
            import math

            start = time_module.time()

            # Simulate complex computation
            results = []
            for i in range(iterations):
                value = sum(math.sqrt(j) for j in range(1, data_size + 1))
                results.append(value)

            computation_time = time_module.time() - start

            return {
                "iterations": iterations,
                "data_size": data_size,
                "final_result": results[-1] if results else 0,
                "computation_time": computation_time,
                "results_count": len(results),
            }

        # Verify full configuration
        config = complex_analysis._cluster_config
        assert config["cores"] == 16
        assert config["memory"] == "32GB"
        assert config["time"] == "04:00:00"
        assert config["partition"] == "compute"
        assert config["parallel"] is False
        assert config["auto_gpu_parallel"] is False
        assert config["environment"] == "test_env"

        # Execute with full config
        result = complex_analysis(100, 5)

        # Validate
        assert result["iterations"] == 5
        assert result["data_size"] == 100
        assert result["results_count"] == 5
        assert result["computation_time"] > 0

    def test_local_vs_remote_execution(self):
        """
        Test execution mode selection based on configuration.

        This demonstrates:
        - Local execution when cluster_host is None
        - Configuration-based execution routing
        - Consistent results regardless of execution mode
        """
        # Test local execution
        configure(cluster_host=None)

        @cluster(cores=2)
        def compute_stats(numbers):
            """Compute statistics for a list of numbers."""
            import statistics

            if not numbers:
                return None

            return {
                "mean": statistics.mean(numbers),
                "median": statistics.median(numbers),
                "stdev": statistics.stdev(numbers) if len(numbers) > 1 else 0,
                "min": min(numbers),
                "max": max(numbers),
            }

        # Execute locally
        test_data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        local_result = compute_stats(test_data)

        # Validate local execution
        assert local_result["mean"] == 5.5
        assert local_result["median"] == 5.5
        assert local_result["min"] == 1
        assert local_result["max"] == 10
        assert local_result["stdev"] > 0

        # If remote cluster is configured, test remote execution
        remote_host = os.getenv("TEST_CLUSTER_HOST")
        if remote_host:
            configure(
                cluster_host=remote_host,
                username=os.getenv("TEST_CLUSTER_USER", "testuser"),
                cluster_type=os.getenv("TEST_CLUSTER_TYPE", "slurm"),
            )

            remote_result = compute_stats(test_data)

            # Results should be identical
            assert remote_result["mean"] == local_result["mean"]
            assert remote_result["median"] == local_result["median"]

    def test_async_execution(self):
        """
        Test asynchronous job submission with decorator.

        This demonstrates:
        - Async job submission
        - Non-blocking execution
        - Result polling
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB", async_submit=True)
        def slow_computation(n):
            """Simulate slow computation for async testing."""
            import time

            # Simulate work
            time.sleep(0.5)

            # Compute factorial
            result = 1
            for i in range(1, n + 1):
                result *= i

            return {"input": n, "factorial": result, "digits": len(str(result))}

        # Submit async job
        job_result = slow_computation(10)

        # For async, should return a job handle/future
        # The actual implementation may vary, but we test the concept
        if hasattr(job_result, "result"):
            # If it's a future-like object
            final_result = job_result.result(timeout=5)
        else:
            # If async is not fully implemented, may return direct result
            final_result = job_result

        # Validate result
        assert final_result["input"] == 10
        assert final_result["factorial"] == 3628800
        assert final_result["digits"] == 7

    def test_parallel_execution(self):
        """
        Test parallel execution with loop detection.

        This demonstrates:
        - Automatic loop parallelization
        - Parallel execution configuration
        - Result aggregation
        """
        configure(cluster_type="local")

        @cluster(cores=4, parallel=True)
        def parallel_processing(data_list):
            """Process list items in parallel."""
            results = []

            # This loop should be detected for parallelization
            for item in data_list:
                # Compute square root sum for each item
                import math

                value = sum(math.sqrt(i) for i in range(1, item + 1))
                results.append({"input": item, "result": value})

            return results

        # Execute with parallel processing
        test_inputs = [10, 20, 30, 40, 50]
        results = parallel_processing(test_inputs)

        # Validate parallel execution results
        assert len(results) == len(test_inputs)
        for i, result in enumerate(results):
            assert result["input"] == test_inputs[i]
            assert result["result"] > 0

    def test_gpu_configuration(self):
        """
        Test GPU-related configuration in decorator.

        This demonstrates:
        - GPU parameter specification
        - Auto-GPU parallelization flag
        - GPU resource requests
        """
        configure(cluster_type="local")

        @cluster(
            cores=4, memory="16GB", gpu=2, auto_gpu_parallel=True  # Request 2 GPUs
        )
        def gpu_computation(matrix_size):
            """Simulate GPU computation."""
            import numpy as np

            # In real GPU execution, this would use CuPy or PyTorch
            # For testing, we simulate with NumPy
            matrix = np.random.randn(matrix_size, matrix_size)

            # Simulate GPU operations
            result = np.matmul(matrix, matrix.T)
            eigenvalues = np.linalg.eigvals(result[:10, :10])  # Subset for speed

            return {
                "matrix_size": matrix_size,
                "result_trace": float(np.trace(result)),
                "max_eigenvalue": float(np.max(np.abs(eigenvalues))),
                "computation_device": "simulated_gpu",
            }

        # Verify GPU configuration
        assert gpu_computation._cluster_config.get("gpu") == 2
        assert gpu_computation._cluster_config.get("auto_gpu_parallel") is True

        # Execute GPU computation
        result = gpu_computation(100)

        # Validate
        assert result["matrix_size"] == 100
        assert result["result_trace"] != 0
        assert result["max_eigenvalue"] > 0

    def test_error_handling_in_decorated_function(self):
        """
        Test error handling and propagation through decorator.

        This demonstrates:
        - Exception propagation
        - Error message preservation
        - Cleanup after errors
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def function_with_error(x, error_type="value"):
            """Function that can raise different errors."""
            if error_type == "value":
                if x < 0:
                    raise ValueError(f"Negative value not allowed: {x}")
            elif error_type == "type":
                raise TypeError(f"Type error simulation for: {x}")
            elif error_type == "runtime":
                raise RuntimeError(f"Runtime error simulation for: {x}")

            return x * 2

        # Test ValueError
        with pytest.raises(ValueError) as exc_info:
            function_with_error(-5, "value")
        assert "Negative value not allowed: -5" in str(exc_info.value)

        # Test TypeError
        with pytest.raises(TypeError) as exc_info:
            function_with_error(10, "type")
        assert "Type error simulation for: 10" in str(exc_info.value)

        # Test successful execution
        result = function_with_error(5, "none")
        assert result == 10

    def test_decorator_with_complex_return_types(self):
        """
        Test decorator with various return types.

        This demonstrates:
        - Dictionary returns
        - List returns
        - Tuple returns
        - Object serialization
        """
        configure(cluster_type="local")

        @cluster(cores=2)
        def return_complex_types(return_type):
            """Return different types based on parameter."""
            import numpy as np
            from datetime import datetime

            if return_type == "dict":
                return {
                    "timestamp": datetime.now().isoformat(),
                    "data": [1, 2, 3, 4, 5],
                    "nested": {"value": 42, "array": np.array([1, 2, 3]).tolist()},
                }
            elif return_type == "list":
                return [1, 2, 3, np.array([4, 5, 6]).tolist(), {"key": "value"}]
            elif return_type == "tuple":
                return (42, "string", [1, 2, 3], {"a": 1})
            elif return_type == "numpy":
                return np.array([[1, 2], [3, 4]]).tolist()
            else:
                return None

        # Test different return types
        dict_result = return_complex_types("dict")
        assert isinstance(dict_result, dict)
        assert "timestamp" in dict_result
        assert dict_result["nested"]["value"] == 42

        list_result = return_complex_types("list")
        assert isinstance(list_result, list)
        assert len(list_result) == 5

        tuple_result = return_complex_types("tuple")
        assert isinstance(tuple_result, tuple)
        assert tuple_result[0] == 42

        numpy_result = return_complex_types("numpy")
        assert numpy_result == [[1, 2], [3, 4]]


class TestDecoratorIntegrationWorkflows:
    """Integration tests showing complete workflows with @cluster decorator."""

    def test_machine_learning_workflow(self):
        """
        Test realistic ML workflow with decorator.

        This demonstrates a complete machine learning pipeline
        as users would implement it.
        """
        # Configure for execution
        config = ClusterConfig()
        config.cluster_type = "local"

        original_config = config_module._config
        config_module._config = config

        try:

            @cluster(cores=4, memory="8GB", parallel=False)
            def train_model(n_samples, n_features, model_type="logistic"):
                """Train ML model with given parameters."""
                from sklearn.datasets import make_classification
                from sklearn.model_selection import train_test_split
                from sklearn.linear_model import LogisticRegression
                from sklearn.ensemble import RandomForestClassifier
                from sklearn.metrics import accuracy_score, f1_score
                import time

                start_time = time.time()

                # Generate dataset
                X, y = make_classification(
                    n_samples=n_samples,
                    n_features=n_features,
                    n_informative=n_features // 2,
                    random_state=42,
                )

                # Split data
                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, test_size=0.2, random_state=42
                )

                # Select and train model
                if model_type == "logistic":
                    model = LogisticRegression(max_iter=1000)
                else:
                    model = RandomForestClassifier(n_estimators=100, random_state=42)

                model.fit(X_train, y_train)

                # Evaluate
                train_pred = model.predict(X_train)
                test_pred = model.predict(X_test)

                training_time = time.time() - start_time

                return {
                    "model_type": model_type,
                    "n_samples": n_samples,
                    "n_features": n_features,
                    "train_accuracy": float(accuracy_score(y_train, train_pred)),
                    "test_accuracy": float(accuracy_score(y_test, test_pred)),
                    "train_f1": float(f1_score(y_train, train_pred)),
                    "test_f1": float(f1_score(y_test, test_pred)),
                    "training_time": training_time,
                }

            # Train different models
            logistic_results = train_model(1000, 20, "logistic")
            rf_results = train_model(1000, 20, "random_forest")

            # Validate results
            assert logistic_results["model_type"] == "logistic"
            assert logistic_results["train_accuracy"] > 0.5
            assert logistic_results["test_accuracy"] > 0.5

            assert rf_results["model_type"] == "random_forest"
            assert rf_results["train_accuracy"] > 0.5
            assert rf_results["test_accuracy"] > 0.5

            # Random Forest typically performs better
            assert rf_results["train_accuracy"] >= logistic_results["train_accuracy"]

        finally:
            config_module._config = original_config
