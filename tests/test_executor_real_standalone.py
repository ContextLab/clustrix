"""
Standalone real-world tests for ClusterExecutor functionality.

These tests can run without pytest, using real infrastructure.
"""

import os
import time
import tempfile
from pathlib import Path
from clustrix import cluster
from clustrix.executor import ClusterExecutor
from clustrix.config import ClusterConfig
import clustrix.config as config_module


class TestClusterExecutorRealStandalone:
    """Test ClusterExecutor with real infrastructure - standalone version."""

    def create_local_config(self):
        """Create configuration for local testing."""
        config = ClusterConfig()
        config.cluster_type = "local"
        config.cleanup_remote_files = True
        return config

    def test_executor_initialization_and_connection(self):
        """Test executor initialization and connection setup."""
        config = self.create_local_config()
        executor = ClusterExecutor(config)

        # Verify initialization
        assert executor.config == config
        assert executor.config.cluster_type == "local"

        # For local execution, no SSH connection needed
        executor.connect()
        assert executor.ssh_client is None  # Local doesn't use SSH

        # Cleanup
        executor.disconnect()
        print("✅ Executor initialization test passed")

    def test_job_submission_local(self):
        """Test job submission with local execution."""
        config = self.create_local_config()
        executor = ClusterExecutor(config)
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

            print("✅ Local job submission test passed")

        finally:
            executor.disconnect()

    def test_error_handling_real(self):
        """Test error handling with real execution."""
        config = self.create_local_config()
        executor = ClusterExecutor(config)
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
            try:
                executor.wait_for_result(job_id)
                assert False, "Should have raised an exception"
            except Exception as e:
                # Verify error message
                assert "Negative value" in str(e) or "error" in str(e).lower()

            print("✅ Error handling test passed")

        finally:
            executor.disconnect()

    def test_parallel_job_submission(self):
        """Test parallel job submission and management."""
        config = self.create_local_config()
        executor = ClusterExecutor(config)
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

            for i in range(3):  # Reduced for faster testing
                func_data = serialize_function(compute_square, (i,), {})
                job_config = {"cores": 1, "memory": "512MB"}
                job_id = executor.submit_job(func_data, job_config)
                job_ids.append(job_id)
                expected_results[job_id] = i * i

            # Verify all jobs submitted
            assert len(job_ids) == 3
            assert len(set(job_ids)) == 3  # All unique IDs

            # Collect results
            results = {}
            for job_id in job_ids:
                result = executor.wait_for_result(job_id, timeout=30)
                results[job_id] = result

            # Validate results
            for job_id, expected in expected_results.items():
                assert results[job_id] == expected

            print("✅ Parallel job submission test passed")

        finally:
            executor.disconnect()


class TestDecoratorRealStandalone:
    """Test @cluster decorator with real execution - standalone version."""

    def test_basic_decoration_and_execution(self):
        """Test basic function decoration and execution."""
        # Configure for local execution
        from clustrix import configure

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

        print("✅ Basic decoration test passed")

    def test_decorator_without_parameters(self):
        """Test decorator without parameters (using defaults)."""
        from clustrix import configure

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

        print("✅ Decorator without parameters test passed")

    def test_error_handling_in_decorated_function(self):
        """Test error handling and propagation through decorator."""
        from clustrix import configure

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
        try:
            function_with_error(-5, "value")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Negative value not allowed: -5" in str(e)

        # Test successful execution
        result = function_with_error(5, "none")
        assert result == 10

        print("✅ Error handling test passed")


def run_all_tests():
    """Run all standalone tests."""
    print("=" * 70)
    print("RUNNING STANDALONE REFACTORED TESTS")
    print("=" * 70)

    # Test executor
    print("\n📋 Testing ClusterExecutor...")
    executor_tests = TestClusterExecutorRealStandalone()

    try:
        executor_tests.test_executor_initialization_and_connection()
    except Exception as e:
        print(f"❌ Executor initialization test failed: {e}")

    try:
        executor_tests.test_job_submission_local()
    except Exception as e:
        print(f"❌ Local job submission test failed: {e}")

    try:
        executor_tests.test_error_handling_real()
    except Exception as e:
        print(f"❌ Error handling test failed: {e}")

    try:
        executor_tests.test_parallel_job_submission()
    except Exception as e:
        print(f"❌ Parallel job submission test failed: {e}")

    # Test decorator
    print("\n📋 Testing @cluster decorator...")
    decorator_tests = TestDecoratorRealStandalone()

    try:
        decorator_tests.test_basic_decoration_and_execution()
    except Exception as e:
        print(f"❌ Basic decoration test failed: {e}")

    try:
        decorator_tests.test_decorator_without_parameters()
    except Exception as e:
        print(f"❌ Decorator without parameters test failed: {e}")

    try:
        decorator_tests.test_error_handling_in_decorated_function()
    except Exception as e:
        print(f"❌ Error handling test failed: {e}")

    print("\n" + "=" * 70)
    print("✅ Standalone test execution complete!")
    print("   These tests demonstrate real-world usage patterns")
    print("   without mocks or artificial constructs.")


if __name__ == "__main__":
    run_all_tests()
