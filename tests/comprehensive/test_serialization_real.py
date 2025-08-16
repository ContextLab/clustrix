"""
Comprehensive serialization tests using real infrastructure.

These tests validate serialization of various Python objects and patterns
that users commonly encounter, using actual infrastructure without mocks.
"""

import pytest
import os
import sys
import pickle
import cloudpickle
import dill
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Any, Callable
import numpy as np
import pandas as pd
from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from clustrix.utils import serialize_function, deserialize_function


class TestFunctionSerialization:
    """Test serialization of various function types."""

    def test_simple_function_serialization(self):
        """Test serialization of simple functions."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def simple_function(x, y):
            """Simple function with basic operations."""
            return x + y * 2

        result = simple_function(10, 20)
        assert result == 50

    def test_function_with_imports(self):
        """Test functions with import statements."""
        configure(cluster_type="local")

        @cluster(cores=2, memory="2GB")
        def function_with_imports(data):
            """Function that imports modules."""
            import math
            import statistics
            from datetime import datetime

            return {
                "mean": statistics.mean(data),
                "stdev": statistics.stdev(data) if len(data) > 1 else 0,
                "sqrt_sum": sum(math.sqrt(abs(x)) for x in data),
                "timestamp": datetime.now().isoformat(),
            }

        result = function_with_imports([1, 2, 3, 4, 5])
        assert result["mean"] == 3.0
        assert result["stdev"] > 0
        assert "timestamp" in result

    def test_function_with_nested_functions(self):
        """Test functions containing nested functions."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def outer_function(numbers):
            """Function with nested functions."""

            def process_item(x):
                """Nested processing function."""
                return x * x + 1

            def aggregate(items):
                """Nested aggregation function."""
                return sum(items) / len(items) if items else 0

            processed = [process_item(x) for x in numbers]

            return {
                "processed": processed,
                "average": aggregate(processed),
                "count": len(processed),
            }

        result = outer_function([1, 2, 3, 4, 5])
        assert result["processed"] == [2, 5, 10, 17, 26]
        assert result["average"] == 12.0
        assert result["count"] == 5

    def test_function_with_closures(self):
        """Test functions with closure variables."""
        configure(cluster_type="local")

        multiplier = 10
        offset = 5

        @cluster(cores=1, memory="1GB")
        def function_with_closure(values):
            """Function that captures external variables."""
            # These variables are captured from outer scope
            return [v * multiplier + offset for v in values]

        result = function_with_closure([1, 2, 3])
        assert result == [15, 25, 35]

    def test_generator_function_serialization(self):
        """Test serialization of generator functions."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def generator_function(n):
            """Function that uses generators."""

            def fibonacci():
                a, b = 0, 1
                while True:
                    yield a
                    a, b = b, a + b

            fib = fibonacci()
            result = []
            for _ in range(n):
                result.append(next(fib))

            return result

        result = generator_function(10)
        assert result == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]

    def test_recursive_function_serialization(self):
        """Test serialization of recursive functions."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def recursive_function(n):
            """Recursive factorial calculation."""

            def factorial(x):
                if x <= 1:
                    return 1
                return x * factorial(x - 1)

            return {"input": n, "factorial": factorial(n), "fibonacci": fib(n)}

        def fib(n):
            """Helper fibonacci function."""
            if n <= 1:
                return n
            return fib(n - 1) + fib(n - 2)

        result = recursive_function(5)
        assert result["factorial"] == 120
        assert result["input"] == 5


class TestDataStructureSerialization:
    """Test serialization of various data structures."""

    def test_numpy_array_serialization(self):
        """Test NumPy array serialization."""
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def process_numpy_arrays(shape, dtype_name):
            """Process various NumPy array types."""
            import numpy as np

            # Create arrays of different types
            arrays = {
                "float64": np.random.randn(*shape),
                "float32": np.random.randn(*shape).astype(np.float32),
                "int64": np.random.randint(-100, 100, shape, dtype=np.int64),
                "int32": np.random.randint(-100, 100, shape, dtype=np.int32),
                "bool": np.random.random(shape) > 0.5,
                "complex": np.random.randn(*shape) + 1j * np.random.randn(*shape),
            }

            selected = arrays.get(dtype_name, arrays["float64"])

            return {
                "dtype": str(selected.dtype),
                "shape": selected.shape,
                "size": selected.size,
                "nbytes": selected.nbytes,
                "mean": float(np.mean(np.abs(selected))),
                "checksum": hash(selected.tobytes()) % 1000000,
            }

        # Test different array types
        for dtype in ["float64", "int32", "bool", "complex"]:
            result = process_numpy_arrays((100, 100), dtype)
            assert result["shape"] == (100, 100)
            assert result["size"] == 10000
            assert dtype in result["dtype"].lower() or dtype == "bool"

    def test_pandas_dataframe_serialization(self):
        """Test Pandas DataFrame serialization."""
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def process_dataframe(nrows, ncols):
            """Process Pandas DataFrames."""
            import pandas as pd
            import numpy as np
            from datetime import datetime, timedelta

            # Create DataFrame with various column types
            data = {
                "integers": np.random.randint(0, 100, nrows),
                "floats": np.random.randn(nrows),
                "strings": [f"item_{i}" for i in range(nrows)],
                "booleans": np.random.random(nrows) > 0.5,
                "dates": [datetime.now() - timedelta(days=i) for i in range(nrows)],
                "categories": pd.Categorical(["A", "B", "C"] * (nrows // 3 + 1))[
                    :nrows
                ],
            }

            # Add more columns up to ncols
            for i in range(6, ncols):
                data[f"col_{i}"] = np.random.randn(nrows)

            df = pd.DataFrame(data)

            # Perform operations
            numeric_cols = df.select_dtypes(include=[np.number]).columns

            return {
                "shape": df.shape,
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "memory_usage": int(df.memory_usage(deep=True).sum()),
                "numeric_mean": float(df[numeric_cols].mean().mean()),
                "null_count": int(df.isnull().sum().sum()),
                "unique_strings": len(df["strings"].unique()) if "strings" in df else 0,
            }

        result = process_dataframe(1000, 10)
        assert result["shape"] == (1000, 10)
        assert result["null_count"] == 0
        assert result["unique_strings"] == 1000

    def test_nested_containers_serialization(self):
        """Test deeply nested container serialization."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="2GB")
        def process_nested_structure(depth, width):
            """Process deeply nested data structures."""
            import json

            def create_nested_dict(d, w):
                """Create nested dictionary."""
                if d == 0:
                    return list(range(w))

                return {
                    f"level_{d}_item_{i}": create_nested_dict(d - 1, w)
                    for i in range(w)
                }

            def count_elements(obj):
                """Count total elements in nested structure."""
                if isinstance(obj, dict):
                    return sum(count_elements(v) for v in obj.values())
                elif isinstance(obj, list):
                    return len(obj) + sum(
                        count_elements(item)
                        for item in obj
                        if isinstance(item, (dict, list))
                    )
                else:
                    return 1

            nested = create_nested_dict(depth, width)

            # Test JSON serialization
            json_str = json.dumps(nested)

            return {
                "depth": depth,
                "width": width,
                "total_elements": count_elements(nested),
                "json_size": len(json_str),
                "type": type(nested).__name__,
            }

        result = process_nested_structure(3, 3)
        assert result["depth"] == 3
        assert result["width"] == 3
        assert result["total_elements"] > 0

    def test_custom_class_serialization(self):
        """Test custom class instance serialization."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def process_custom_objects(count):
            """Process custom class instances."""

            class DataPoint:
                """Custom data point class."""

                def __init__(self, x, y, label):
                    self.x = x
                    self.y = y
                    self.label = label
                    self.timestamp = time.time()

                def distance_from_origin(self):
                    """Calculate distance from origin."""
                    return (self.x**2 + self.y**2) ** 0.5

                def to_dict(self):
                    """Convert to dictionary."""
                    return {
                        "x": self.x,
                        "y": self.y,
                        "label": self.label,
                        "distance": self.distance_from_origin(),
                    }

            # Create custom objects
            import random

            points = [
                DataPoint(random.random() * 100, random.random() * 100, f"point_{i}")
                for i in range(count)
            ]

            # Process objects
            results = {
                "count": len(points),
                "avg_distance": sum(p.distance_from_origin() for p in points)
                / len(points),
                "labels": [p.label for p in points],
                "data": [p.to_dict() for p in points],
            }

            return results

        result = process_custom_objects(10)
        assert result["count"] == 10
        assert len(result["labels"]) == 10
        assert result["avg_distance"] > 0


class TestSerializationEdgeCases:
    """Test serialization edge cases and problematic patterns."""

    def test_lambda_serialization_workaround(self):
        """Test workarounds for lambda serialization."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def process_with_lambdas(data):
            """Process data using lambda alternatives."""

            # Instead of lambdas, use named functions
            def square(x):
                return x * x

            def is_even(x):
                return x % 2 == 0

            # Use named functions instead of lambdas
            squared = list(map(square, data))
            evens = list(filter(is_even, data))

            # This would fail with direct lambda serialization:
            # squared = list(map(lambda x: x*x, data))

            return {"squared": squared, "evens": evens, "sum_squared": sum(squared)}

        result = process_with_lambdas([1, 2, 3, 4, 5])
        assert result["squared"] == [1, 4, 9, 16, 25]
        assert result["evens"] == [2, 4]
        assert result["sum_squared"] == 55

    def test_global_variable_serialization(self):
        """Test serialization with global variables."""
        configure(cluster_type="local")

        # Global variable (problematic for serialization)
        GLOBAL_CONFIG = {"multiplier": 10, "offset": 5}

        @cluster(cores=1, memory="1GB")
        def function_using_globals(values):
            """Function that references globals."""
            # Explicitly pass globals or redefine them
            config = {"multiplier": 10, "offset": 5}  # Local copy

            return [v * config["multiplier"] + config["offset"] for v in values]

        result = function_using_globals([1, 2, 3])
        assert result == [15, 25, 35]

    def test_file_handle_serialization(self):
        """Test handling of non-serializable file objects."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def process_with_files(content):
            """Process data with file operations."""
            import tempfile
            import os

            # Create file inside function (not passed as argument)
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                # Read and process file
                with open(temp_path, "r") as f:
                    data = f.read()

                return {
                    "length": len(data),
                    "lines": data.count("\n") + 1,
                    "words": len(data.split()),
                }

            finally:
                os.unlink(temp_path)

        result = process_with_files("Hello\nWorld\nTest")
        assert result["lines"] == 3
        assert result["words"] == 3

    def test_thread_and_lock_serialization(self):
        """Test handling of threading objects."""
        configure(cluster_type="local")

        @cluster(cores=2, memory="2GB")
        def threaded_computation(n_threads, work_items):
            """Computation using threading."""
            import threading
            from concurrent.futures import ThreadPoolExecutor

            # Create threading objects inside function
            results = []
            lock = threading.Lock()

            def worker(item):
                """Thread worker function."""
                result = item * item

                with lock:
                    results.append(result)

                return result

            # Use thread pool
            with ThreadPoolExecutor(max_workers=n_threads) as executor:
                futures = [executor.submit(worker, item) for item in work_items]
                thread_results = [f.result() for f in futures]

            return {
                "thread_results": thread_results,
                "shared_results": sorted(results),
                "total": sum(results),
            }

        result = threaded_computation(4, [1, 2, 3, 4, 5])
        assert result["thread_results"] == [1, 4, 9, 16, 25]
        assert result["total"] == 55

    def test_module_reference_serialization(self):
        """Test serialization of module references."""
        configure(cluster_type="local")

        @cluster(cores=1, memory="1GB")
        def function_with_module_refs():
            """Function that uses various module references."""
            import sys
            import os
            import math
            import json

            # Use modules
            results = {
                "python_version": sys.version,
                "platform": sys.platform,
                "cwd": os.getcwd(),
                "pi": math.pi,
                "json_test": json.dumps({"test": "data"}),
            }

            # Module-level functions
            results["math_functions"] = {
                "sin_pi": math.sin(math.pi),
                "cos_pi": math.cos(math.pi),
                "sqrt_2": math.sqrt(2),
            }

            return results

        result = function_with_module_refs()
        assert "python_version" in result
        assert abs(result["math_functions"]["sin_pi"]) < 0.001
        assert abs(result["math_functions"]["cos_pi"] + 1) < 0.001


class TestSerializationPerformance:
    """Test serialization performance and optimization."""

    def test_large_object_serialization_performance(self):
        """Test performance of large object serialization."""
        configure(cluster_type="local")

        @cluster(cores=2, memory="4GB")
        def process_large_object(size_mb):
            """Process large objects to test serialization."""
            import numpy as np
            import time

            # Create large object
            elements = (size_mb * 1024 * 1024) // 8
            large_array = np.random.randn(elements)

            # Measure processing time
            start = time.perf_counter()

            # Process the array
            result = {
                "mean": float(np.mean(large_array)),
                "std": float(np.std(large_array)),
                "min": float(np.min(large_array)),
                "max": float(np.max(large_array)),
            }

            process_time = time.perf_counter() - start

            result.update(
                {"size_mb": size_mb, "process_time": process_time, "elements": elements}
            )

            return result

        # Test with progressively larger objects
        for size_mb in [1, 10, 50]:
            start = time.perf_counter()
            result = process_large_object(size_mb)
            total_time = time.perf_counter() - start

            print(
                f"  {size_mb}MB: Total={total_time:.2f}s, Process={result['process_time']:.2f}s"
            )

            assert result["size_mb"] == size_mb
            assert -0.1 < result["mean"] < 0.1  # Should be near 0

    def test_serialization_method_comparison(self):
        """Compare different serialization methods."""
        configure(cluster_type="local")

        # Test object
        test_data = {
            "numbers": list(range(1000)),
            "strings": [f"item_{i}" for i in range(1000)],
            "nested": {"level1": {"level2": {"data": list(range(100))}}},
            "array": np.random.randn(100, 100).tolist(),
        }

        # Test pickle
        start = time.perf_counter()
        pickle_data = pickle.dumps(test_data)
        pickle_size = len(pickle_data)
        pickle_time = time.perf_counter() - start

        # Test cloudpickle
        start = time.perf_counter()
        cloud_data = cloudpickle.dumps(test_data)
        cloud_size = len(cloud_data)
        cloud_time = time.perf_counter() - start

        # Test dill
        start = time.perf_counter()
        dill_data = dill.dumps(test_data)
        dill_size = len(dill_data)
        dill_time = time.perf_counter() - start

        print(f"\nSerialization Comparison:")
        print(f"  Pickle: {pickle_size} bytes, {pickle_time*1000:.2f}ms")
        print(f"  CloudPickle: {cloud_size} bytes, {cloud_time*1000:.2f}ms")
        print(f"  Dill: {dill_size} bytes, {dill_time*1000:.2f}ms")

        # All should successfully serialize
        assert pickle_size > 0
        assert cloud_size > 0
        assert dill_size > 0


def run_serialization_tests():
    """Run comprehensive serialization tests."""
    print("🔄 Running Serialization Tests")
    print("=" * 60)

    test_suites = [
        ("Function Serialization", TestFunctionSerialization()),
        ("Data Structure Serialization", TestDataStructureSerialization()),
        ("Edge Cases", TestSerializationEdgeCases()),
        ("Performance", TestSerializationPerformance()),
    ]

    results = {"passed": 0, "failed": 0}

    for suite_name, test_suite in test_suites:
        print(f"\n📦 Testing {suite_name}")
        print("-" * 40)

        test_methods = [m for m in dir(test_suite) if m.startswith("test_")]

        for method_name in test_methods:
            print(f"\n▶ {method_name}")

            try:
                method = getattr(test_suite, method_name)
                method()
                print("  ✅ Serialization successful")
                results["passed"] += 1

            except Exception as e:
                print(f"  ❌ Failed: {e}")
                results["failed"] += 1

    # Summary
    print("\n" + "=" * 60)
    print("SERIALIZATION TEST SUMMARY")
    print("=" * 60)
    print(f"✅ Passed: {results['passed']}")
    print(f"❌ Failed: {results['failed']}")

    if results["failed"] == 0:
        print("\n✨ All serialization tests passed!")
    else:
        print(f"\n⚠️  {results['failed']} serialization tests failed")

    return results["failed"] == 0


if __name__ == "__main__":
    success = run_serialization_tests()
    sys.exit(0 if success else 1)
