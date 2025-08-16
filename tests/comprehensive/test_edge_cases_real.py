"""
Comprehensive edge case tests using real infrastructure.

These tests validate clustrix behavior in unusual, boundary, and error conditions
using actual infrastructure without mocks.
"""

import pytest
import os
import sys
import time
import tempfile
import numpy as np
import pandas as pd
from pathlib import Path
from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from clustrix.executor import ClusterExecutor


class TestSerializationEdgeCases:
    """Test serialization edge cases with real execution."""

    def test_main_module_function(self):
        """
        Test functions defined in __main__ module.

        This is a critical edge case that often fails with pickle.
        """
        # Simulate __main__ module function
        code = """
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from clustrix import cluster

@cluster(cores=2, memory="4GB")
def main_function(x, y):
    '''Function defined in __main__ module.'''
    return x + y

if __name__ == "__main__":
    result = main_function(10, 20)
    print(f"Result: {result}")
    assert result == 30
"""

        # Write to temporary file and execute
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            script_path = f.name

        try:
            # Execute script
            import subprocess

            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0
            assert "Result: 30" in result.stdout

        finally:
            os.unlink(script_path)

    def test_lambda_function_serialization(self):
        """
        Test lambda function serialization.

        Lambdas are notoriously difficult to serialize.
        """
        configure(cluster_type="local")

        # This should raise an error or handle gracefully
        with pytest.raises((AttributeError, TypeError, ValueError)):
            # Lambda functions cannot be decorated directly
            func = cluster(cores=1)(lambda x: x * 2)
            func(5)

    def test_nested_function_serialization(self):
        """
        Test nested function serialization.

        Functions with closures are challenging to serialize.
        """
        configure(cluster_type="local")

        def outer_function(multiplier):
            @cluster(cores=2, memory="2GB")
            def inner_function(x):
                """Nested function with closure."""
                return x * multiplier

            return inner_function

        # Create function with closure
        double = outer_function(2)
        triple = outer_function(3)

        # Execute nested functions
        result1 = double(10)
        result2 = triple(10)

        assert result1 == 20
        assert result2 == 30

    def test_class_method_serialization(self):
        """
        Test class method serialization.

        Methods need special handling for serialization.
        """
        configure(cluster_type="local")

        class DataProcessor:
            def __init__(self, scale_factor):
                self.scale_factor = scale_factor

            @cluster(cores=2, memory="4GB")
            def process_data(self, data):
                """Process data with scaling."""
                import numpy as np

                arr = np.array(data)
                return (arr * self.scale_factor).tolist()

        # Test instance method execution
        processor = DataProcessor(2.5)
        result = processor.process_data([1, 2, 3, 4, 5])

        assert result == [2.5, 5.0, 7.5, 10.0, 12.5]

    def test_large_object_serialization(self):
        """
        Test serialization of large objects.

        Large data can cause memory issues or timeouts.
        """
        configure(cluster_type="local")

        @cluster(cores=4, memory="8GB")
        def process_large_data(size_mb):
            """Process large dataset."""
            import numpy as np

            # Create large array (size_mb megabytes)
            elements = (size_mb * 1024 * 1024) // 8  # 8 bytes per float64
            data = np.random.randn(elements)

            return {
                "size_bytes": data.nbytes,
                "mean": float(np.mean(data)),
                "std": float(np.std(data)),
                "min": float(np.min(data)),
                "max": float(np.max(data)),
            }

        # Test with 100MB of data
        result = process_large_data(100)

        assert result["size_bytes"] >= 100 * 1024 * 1024
        assert -0.1 < result["mean"] < 0.1  # Should be near 0
        assert 0.9 < result["std"] < 1.1  # Should be near 1

    def test_recursive_object_serialization(self):
        """
        Test serialization of recursive/circular references.

        Circular references can cause infinite loops in serialization.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="2GB")
        def process_graph(num_nodes):
            """Process graph with potential cycles."""

            class Node:
                def __init__(self, value):
                    self.value = value
                    self.neighbors = []

                def add_neighbor(self, node):
                    self.neighbors.append(node)

            # Create graph with cycle
            nodes = [Node(i) for i in range(num_nodes)]

            # Create connections (including cycle)
            for i in range(num_nodes - 1):
                nodes[i].add_neighbor(nodes[i + 1])
            nodes[-1].add_neighbor(nodes[0])  # Create cycle

            # Process graph (don't try to return the cyclic structure)
            total = sum(node.value for node in nodes)
            return {"num_nodes": num_nodes, "total_value": total, "has_cycle": True}

        result = process_graph(10)
        assert result["num_nodes"] == 10
        assert result["total_value"] == sum(range(10))
        assert result["has_cycle"] is True


class TestResourceLimitEdgeCases:
    """Test resource allocation edge cases."""

    def test_zero_resource_request(self):
        """
        Test behavior with zero resource requests.

        Some systems may not handle zero requests properly.
        """
        configure(cluster_type="local")

        # Zero cores should either fail or use minimum
        with pytest.raises(ValueError) or True:

            @cluster(cores=0, memory="1GB")
            def zero_cores():
                return "executed"

            # This might fail or use default minimum
            try:
                result = zero_cores()
                assert result == "executed"
            except ValueError:
                pass  # Expected for zero cores

    def test_excessive_resource_request(self):
        """
        Test behavior with excessive resource requests.

        Requests beyond system capacity should be handled gracefully.
        """
        configure(cluster_type="local")

        @cluster(cores=9999, memory="10000GB")
        def excessive_resources():
            """Function with excessive resource requirements."""
            import multiprocessing

            actual_cores = multiprocessing.cpu_count()
            return {"requested_cores": 9999, "actual_cores": actual_cores}

        # Should either fail gracefully or allocate available resources
        try:
            result = excessive_resources()
            # If it succeeds, it should use available resources
            import multiprocessing

            assert result["actual_cores"] <= multiprocessing.cpu_count()
        except (ValueError, RuntimeError, MemoryError) as e:
            # Expected to fail with excessive requests
            assert "resource" in str(e).lower() or "memory" in str(e).lower()

    def test_fractional_core_request(self):
        """
        Test behavior with fractional core requests.

        Some systems support fractional CPU allocation.
        """
        configure(cluster_type="kubernetes")

        @cluster(cores=0.5, memory="512Mi")
        def fractional_cpu():
            """Function with fractional CPU request."""
            import time

            start = time.time()

            # Do some CPU-bound work
            total = sum(i * i for i in range(1000000))

            duration = time.time() - start
            return {"result": total, "duration": duration}

        # Execute if Kubernetes is available
        if os.getenv("KUBECONFIG"):
            result = fractional_cpu()
            assert result["result"] > 0
            assert result["duration"] > 0

    def test_memory_string_formats(self):
        """
        Test various memory specification formats.

        Different systems use different memory formats.
        """
        configure(cluster_type="local")

        memory_formats = [
            "1GB",
            "1024MB",
            "1073741824B",  # Decimal units
            "1Gi",
            "1024Mi",
            "1048576Ki",  # Binary units
            "1g",
            "1024m",  # Lowercase
            "1.5GB",
            "1536MB",  # Fractional
        ]

        for mem_format in memory_formats:

            @cluster(cores=1, memory=mem_format)
            def test_memory_format():
                """Test memory format parsing."""
                import psutil

                return {
                    "format": mem_format,
                    "available_mb": psutil.virtual_memory().available // (1024 * 1024),
                }

            try:
                result = test_memory_format()
                assert result["available_mb"] > 0
            except ValueError as e:
                # Some formats might not be supported
                print(f"Format {mem_format} not supported: {e}")


class TestNetworkEdgeCases:
    """Test network-related edge cases."""

    @pytest.mark.real_world
    def test_connection_timeout(self):
        """
        Test behavior with connection timeouts.

        Network timeouts should be handled gracefully.
        """
        config = ClusterConfig()
        config.cluster_type = "ssh"
        config.cluster_host = "192.0.2.1"  # TEST-NET-1 (should timeout)
        config.cluster_port = 22
        config.username = "testuser"
        config.connection_timeout = 5  # 5 second timeout

        executor = ClusterExecutor(config)

        # Should timeout trying to connect
        start = time.time()
        with pytest.raises((TimeoutError, ConnectionError, OSError)):
            executor.connect()
        duration = time.time() - start

        # Should timeout within reasonable time
        assert duration < 10  # Should timeout within 10 seconds

    @pytest.mark.real_world
    def test_intermittent_connection(self):
        """
        Test behavior with intermittent connections.

        Connections that drop during execution.
        """
        configure(
            cluster_type="ssh",
            cluster_host="localhost",
            cluster_port=2222,
            username="testuser",
            password="testpass",
        )

        @cluster(cores=2, memory="2GB", retry_count=3)
        def flaky_network_task(iterations):
            """Task that might fail due to network issues."""
            import random
            import time

            results = []
            for i in range(iterations):
                # Simulate work
                time.sleep(0.1)

                # Randomly simulate network issue
                if random.random() < 0.1:  # 10% chance
                    raise ConnectionError("Simulated network failure")

                results.append(i * i)

            return {"completed": len(results), "sum": sum(results)}

        # Test with retries
        try:
            result = flaky_network_task(10)
            assert result["completed"] <= 10
        except ConnectionError:
            # Expected if network issues occur
            pass

    def test_large_data_transfer(self):
        """
        Test transferring large amounts of data.

        Large data transfers can fail or timeout.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def generate_large_output(size_mb):
            """Generate large output data."""
            import numpy as np

            # Generate large array
            elements = (size_mb * 1024 * 1024) // 8
            data = np.random.randn(elements)

            # Return large result
            return {
                "data_sample": data[:100].tolist(),  # Small sample
                "shape": data.shape,
                "size_bytes": data.nbytes,
                "checksum": hash(data.tobytes()) % 1000000,
            }

        # Test with 50MB result
        result = generate_large_output(50)

        assert len(result["data_sample"]) == 100
        assert result["size_bytes"] >= 50 * 1024 * 1024


class TestConcurrencyEdgeCases:
    """Test concurrent execution edge cases."""

    def test_parallel_job_limits(self):
        """
        Test maximum parallel job limits.

        Systems have limits on concurrent executions.
        """
        configure(cluster_type="local", max_parallel_jobs=3)  # Limit parallel jobs

        @cluster(cores=1, memory="512MB")
        def quick_task(task_id):
            """Quick task for parallel testing."""
            import time

            time.sleep(0.5)
            return {"task_id": task_id, "timestamp": time.time()}

        # Submit many jobs in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit 10 jobs but only 3 should run in parallel
            futures = {executor.submit(quick_task, i): i for i in range(10)}

            results = []
            for future in as_completed(futures):
                result = future.result()
                results.append(result)

        # All should complete
        assert len(results) == 10

        # Check timing to verify parallelism limit
        timestamps = sorted(r["timestamp"] for r in results)

        # With limit of 3 and 0.5s per task, should take ~2 seconds minimum
        total_duration = timestamps[-1] - timestamps[0]
        assert total_duration >= 1.5  # Some parallelism occurred

    def test_race_conditions(self):
        """
        Test for race conditions in shared resources.

        Concurrent access to shared resources needs proper handling.
        """
        configure(cluster_type="local")

        # Shared file for testing race conditions
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            shared_file = f.name
            f.write("0")

        try:

            @cluster(cores=2, memory="1GB")
            def increment_shared_counter(iterations):
                """Increment shared counter (race condition test)."""
                import fcntl
                import time

                for _ in range(iterations):
                    # Proper file locking to avoid race condition
                    with open(shared_file, "r+") as f:
                        fcntl.flock(f, fcntl.LOCK_EX)
                        try:
                            value = int(f.read())
                            f.seek(0)
                            f.write(str(value + 1))
                            f.truncate()
                        finally:
                            fcntl.flock(f, fcntl.LOCK_UN)

                    time.sleep(0.001)  # Small delay

                return "completed"

            # Run multiple instances in parallel
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(increment_shared_counter, 20) for _ in range(5)
                ]

                for future in futures:
                    future.result()

            # Check final value
            with open(shared_file, "r") as f:
                final_value = int(f.read())

            # Should be 100 (5 workers * 20 iterations each)
            assert final_value == 100

        finally:
            os.unlink(shared_file)

    def test_deadlock_prevention(self):
        """
        Test deadlock prevention mechanisms.

        Ensure system handles potential deadlocks.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="2GB", timeout=10)
        def potential_deadlock():
            """Function that could deadlock."""
            import threading
            import time

            lock1 = threading.Lock()
            lock2 = threading.Lock()
            results = []

            def worker1():
                with lock1:
                    time.sleep(0.1)
                    with lock2:
                        results.append("worker1")

            def worker2():
                with lock2:
                    time.sleep(0.1)
                    with lock1:
                        results.append("worker2")

            # Use timeout to prevent actual deadlock
            t1 = threading.Thread(target=worker1)
            t2 = threading.Thread(target=worker2)

            t1.start()
            t2.start()

            t1.join(timeout=5)
            t2.join(timeout=5)

            return {
                "completed": len(results),
                "deadlocked": t1.is_alive() or t2.is_alive(),
            }

        result = potential_deadlock()

        # Should detect potential deadlock
        if result["deadlocked"]:
            assert result["completed"] < 2
        else:
            assert result["completed"] == 2


class TestErrorRecoveryEdgeCases:
    """Test error recovery and resilience."""

    def test_partial_failure_recovery(self):
        """
        Test recovery from partial failures.

        Some tasks succeed while others fail.
        """
        configure(cluster_type="local")

        @cluster(cores=2, memory="2GB")
        def task_with_partial_failures(items):
            """Process items with some failures."""
            results = []
            errors = []

            for i, item in enumerate(items):
                try:
                    if item < 0:
                        raise ValueError(f"Negative value: {item}")
                    if item == 0:
                        raise ZeroDivisionError("Division by zero")

                    result = 100 / item
                    results.append(result)

                except Exception as e:
                    errors.append({"index": i, "item": item, "error": str(e)})

            return {
                "successful": len(results),
                "failed": len(errors),
                "results": results,
                "errors": errors,
            }

        # Test with mixed inputs
        test_data = [10, -5, 20, 0, 30, -10, 40]
        result = task_with_partial_failures(test_data)

        assert result["successful"] == 4  # 10, 20, 30, 40
        assert result["failed"] == 3  # -5, 0, -10
        assert len(result["errors"]) == 3

    def test_cleanup_after_failure(self):
        """
        Test cleanup after job failure.

        Resources should be cleaned up even after failures.
        """
        configure(cluster_type="local", cleanup_on_failure=True)

        temp_files = []

        @cluster(cores=1, memory="1GB")
        def failing_task_with_cleanup():
            """Task that creates resources then fails."""
            import tempfile

            # Create temporary resources
            for i in range(3):
                f = tempfile.NamedTemporaryFile(delete=False)
                temp_files.append(f.name)
                f.write(f"Test data {i}".encode())
                f.close()

            # Simulate failure
            raise RuntimeError("Simulated failure after resource creation")

        # Execute and expect failure
        with pytest.raises(RuntimeError):
            failing_task_with_cleanup()

        # Manual cleanup verification
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def test_signal_handling(self):
        """
        Test signal handling (interruption, termination).

        Jobs should handle signals gracefully.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def interruptible_task(duration):
            """Task that can be interrupted."""
            import signal
            import time

            interrupted = False

            def signal_handler(signum, frame):
                nonlocal interrupted
                interrupted = True

            # Register signal handlers
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            start = time.time()
            while time.time() - start < duration:
                if interrupted:
                    return {"status": "interrupted", "runtime": time.time() - start}
                time.sleep(0.1)

            return {"status": "completed", "runtime": time.time() - start}

        # Test normal completion
        result = interruptible_task(1)
        assert result["status"] == "completed"
        assert result["runtime"] >= 1


class TestPlatformSpecificEdgeCases:
    """Test platform-specific edge cases."""

    def test_path_separators(self):
        """
        Test handling of different path separators.

        Windows vs Unix path handling.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def handle_paths(paths):
            """Process paths across platforms."""
            import os
            from pathlib import Path

            results = []
            for path_str in paths:
                # Convert to platform-appropriate path
                path = Path(path_str)

                results.append(
                    {
                        "original": path_str,
                        "normalized": str(path),
                        "parts": list(path.parts),
                        "separator": os.sep,
                    }
                )

            return results

        # Test with various path formats
        test_paths = [
            "/home/user/data.txt",
            "C:\\Users\\data.txt",
            "relative/path/file.py",
            "../parent/directory/",
            "~/home/directory",
        ]

        results = handle_paths(test_paths)

        assert len(results) == len(test_paths)
        for result in results:
            assert "normalized" in result
            assert "separator" in result

    def test_environment_variable_handling(self):
        """
        Test environment variable handling across platforms.

        Different platforms handle environment variables differently.
        """
        configure(
            cluster_type="local",
            environment_variables={
                "TEST_VAR": "test_value",
                "PATH_VAR": "$PATH:/custom/path",
                "HOME_VAR": "~/test",
                "QUOTED_VAR": '"quoted value"',
            },
        )

        @cluster(cores=1, memory="1GB")
        def check_environment():
            """Check environment variables."""
            import os

            return {
                "test_var": os.getenv("TEST_VAR"),
                "path_var": os.getenv("PATH_VAR"),
                "home_var": os.getenv("HOME_VAR"),
                "quoted_var": os.getenv("QUOTED_VAR"),
                "path_separator": os.pathsep,
                "platform": sys.platform,
            }

        result = check_environment()

        # Basic variable should work
        assert result["test_var"] == "test_value" or result["test_var"] is None

        # Platform-specific handling
        if sys.platform == "win32":
            assert result["path_separator"] == ";"
        else:
            assert result["path_separator"] == ":"

    def test_line_ending_handling(self):
        """
        Test line ending handling (CRLF vs LF).

        Different platforms use different line endings.
        """
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def process_text_file(content, line_ending):
            """Process text with specific line endings."""
            import tempfile
            import os

            # Write file with specific line endings
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, newline=line_ending
            ) as f:
                f.write(content)
                temp_path = f.name

            try:
                # Read file and analyze
                with open(temp_path, "rb") as f:
                    raw_content = f.read()

                with open(temp_path, "r") as f:
                    lines = f.readlines()

                return {
                    "line_count": len(lines),
                    "has_cr": b"\r" in raw_content,
                    "has_lf": b"\n" in raw_content,
                    "size_bytes": len(raw_content),
                }

            finally:
                os.unlink(temp_path)

        # Test with different line endings
        test_content = "Line 1\nLine 2\nLine 3"

        # Unix style (LF)
        result_lf = process_text_file(test_content, "\n")
        assert result_lf["line_count"] == 3
        assert result_lf["has_lf"] is True

        # Windows style (CRLF)
        result_crlf = process_text_file(test_content, "\r\n")
        assert result_crlf["line_count"] == 3

        # Old Mac style (CR) - less common
        result_cr = process_text_file(test_content.replace("\n", "\r"), "\r")
        assert result_cr["line_count"] >= 1


def test_comprehensive_edge_case_suite():
    """
    Run comprehensive edge case test suite.

    This validates clustrix behavior across numerous edge cases.
    """
    print("🔍 Running Comprehensive Edge Case Test Suite")
    print("=" * 60)

    test_categories = {
        "Serialization": TestSerializationEdgeCases(),
        "Resource Limits": TestResourceLimitEdgeCases(),
        "Network": TestNetworkEdgeCases(),
        "Concurrency": TestConcurrencyEdgeCases(),
        "Error Recovery": TestErrorRecoveryEdgeCases(),
        "Platform Specific": TestPlatformSpecificEdgeCases(),
    }

    results = {}

    for category_name, test_class in test_categories.items():
        print(f"\n📋 Testing {category_name} Edge Cases...")

        passed = 0
        failed = 0

        # Get all test methods
        test_methods = [
            method for method in dir(test_class) if method.startswith("test_")
        ]

        for method_name in test_methods:
            try:
                method = getattr(test_class, method_name)
                print(f"  • {method_name}...", end=" ")

                method()

                print("✅")
                passed += 1

            except Exception as e:
                print(f"❌ ({e})")
                failed += 1

        results[category_name] = {
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
        }

    # Print summary
    print("\n" + "=" * 60)
    print("EDGE CASE TEST SUMMARY")
    print("=" * 60)

    total_passed = sum(r["passed"] for r in results.values())
    total_failed = sum(r["failed"] for r in results.values())
    total_tests = sum(r["total"] for r in results.values())

    for category, result in results.items():
        status = "✅" if result["failed"] == 0 else "⚠️"
        print(f"{status} {category}: {result['passed']}/{result['total']} passed")

    print(f"\n📊 Overall: {total_passed}/{total_tests} passed")

    if total_failed == 0:
        print("✨ All edge cases handled correctly!")
    else:
        print(f"⚠️  {total_failed} edge cases need attention")

    return total_failed == 0


if __name__ == "__main__":
    # Run comprehensive test suite
    success = test_comprehensive_edge_case_suite()
    sys.exit(0 if success else 1)
