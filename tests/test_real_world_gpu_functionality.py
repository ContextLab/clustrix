"""
Real-world tests for GPU functionality on actual clusters.

These tests create actual jobs on tensor01 and ndoli to validate:
- GPU detection and VENV setup
- Function flattening with varying complexity
- GPU-enabled package installation
- Cross-cluster compatibility
"""

import pytest
import time
import logging
from typing import Dict, Any, List

from clustrix import cluster
from clustrix.config import ClusterConfig
from clustrix.utils import detect_gpu_capabilities, enhanced_setup_two_venv_environment
from clustrix.function_flattening import analyze_function_complexity

logger = logging.getLogger(__name__)

# Test configurations for different clusters
TEST_CLUSTERS = {
    "tensor01": {
        "cluster_type": "ssh",
        "cluster_host": "tensor01.csail.mit.edu",
        "username": "jmanning",
        "gpu_detection_enabled": True,
        "auto_gpu_packages": True,
        "use_two_venv": True,
        "remote_work_dir": "/tmp/clustrix_gpu_tests",
    },
    "ndoli": {
        "cluster_type": "slurm",
        "cluster_host": "ndoli.csail.mit.edu",
        "username": "jmanning",
        "gpu_detection_enabled": True,
        "auto_gpu_packages": True,
        "use_two_venv": True,
        "remote_work_dir": "/tmp/clustrix_gpu_tests",
    },
}


class TestGPUFunctionalityRealWorld:
    """Real-world GPU functionality tests on actual clusters."""

    @pytest.mark.parametrize("cluster_name", ["tensor01", "ndoli"])
    def test_gpu_detection_real_cluster(self, cluster_name):
        """Test GPU detection on real clusters."""
        config_data = TEST_CLUSTERS[cluster_name]
        config = ClusterConfig(**config_data)

        print(f"\n🧪 Testing GPU detection on {cluster_name}...")

        # Test GPU detection directly
        from clustrix.executor import ClusterExecutor

        try:
            executor = ClusterExecutor(config)

            # Ensure SSH connection is established
            if executor.ssh_client is None:
                executor.connect()

            if executor.ssh_client is None:
                pytest.skip(f"Could not establish SSH connection to {cluster_name}")

            # Use the internal SSH client to test GPU detection
            gpu_info = detect_gpu_capabilities(executor.ssh_client, config)

            print(f"GPU Detection Results for {cluster_name}:")
            print(f"  - GPU Available: {gpu_info['gpu_available']}")
            print(f"  - GPU Count: {gpu_info['gpu_count']}")
            print(f"  - Detection Method: {gpu_info['detection_method']}")
            print(f"  - CUDA Available: {gpu_info['cuda_available']}")
            if gpu_info["cuda_available"]:
                print(f"  - CUDA Version: {gpu_info['cuda_version']}")
            if gpu_info["gpu_devices"]:
                for i, device in enumerate(gpu_info["gpu_devices"]):
                    print(
                        f"  - GPU {i}: {device['name']} ({device['memory_total_mb']}MB)"
                    )

            # Assert basic functionality
            assert isinstance(gpu_info, dict)
            assert "gpu_available" in gpu_info
            assert "gpu_count" in gpu_info
            assert "detection_method" in gpu_info

            if gpu_info["detection_errors"]:
                print(f"  - Detection Errors: {gpu_info['detection_errors']}")

            print(f"✅ GPU detection successful on {cluster_name}")

        except Exception as e:
            # Skip on connection failures (expected in testing environments)
            if "nodename" in str(e) or "connection" in str(e).lower():
                pytest.skip(f"Cannot connect to {cluster_name}: {e}")
            else:
                pytest.fail(f"GPU detection failed on {cluster_name}: {e}")

    @pytest.mark.parametrize("cluster_name", ["tensor01", "ndoli"])
    def test_simple_function_execution(self, cluster_name):
        """Test simple function execution (no flattening needed)."""
        config_data = TEST_CLUSTERS[cluster_name]

        print(f"\n🧪 Testing simple function execution on {cluster_name}...")

        @cluster(**config_data)
        def simple_computation(n=100):
            """Simple function that should not require flattening."""
            import math

            return {
                "result": sum(math.sqrt(i) for i in range(n)),
                "count": n,
                "cluster_info": {
                    "python_version": __import__("sys").version,
                    "hostname": __import__("socket").gethostname(),
                },
            }

        # Check complexity
        complexity = analyze_function_complexity(simple_computation)
        print(f"Simple function complexity: {complexity}")
        assert not complexity.get(
            "is_complex", False
        ), "Simple function should not be complex"

        # Execute and validate
        result = simple_computation(50)

        assert isinstance(result, dict)
        assert "result" in result
        assert "count" in result
        assert result["count"] == 50
        assert "cluster_info" in result
        assert "hostname" in result["cluster_info"]

        print(f"✅ Simple function executed successfully on {cluster_name}")
        print(f"   Result: {result['result']:.2f}")
        print(f"   Hostname: {result['cluster_info']['hostname']}")

    @pytest.mark.parametrize("cluster_name", ["tensor01", "ndoli"])
    def test_nested_function_flattening(self, cluster_name):
        """Test function with nested functions (requires flattening)."""
        config_data = TEST_CLUSTERS[cluster_name]

        print(f"\n🧪 Testing nested function flattening on {cluster_name}...")

        @cluster(**config_data)
        def nested_computation(data_size=100):
            """Function with nested functions that requires flattening."""

            def generate_data(size):
                """Generate test data."""
                import random

                return [random.random() for _ in range(size)]

            def process_chunk(chunk):
                """Process a data chunk."""
                return sum(x * x for x in chunk)

            def analyze_results(results):
                """Analyze processed results."""
                total = sum(results)
                avg = total / len(results) if results else 0
                return {
                    "total": total,
                    "average": avg,
                    "max": max(results) if results else 0,
                }

            # Main computation
            data = generate_data(data_size)
            chunk_size = 10
            chunks = [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]
            results = [process_chunk(chunk) for chunk in chunks]
            analysis = analyze_results(results)

            return {
                "data_size": data_size,
                "chunks_processed": len(chunks),
                "analysis": analysis,
                "cluster_info": {
                    "hostname": __import__("socket").gethostname(),
                    "pid": __import__("os").getpid(),
                },
            }

        # Check complexity
        complexity = analyze_function_complexity(nested_computation)
        print(f"Nested function complexity: {complexity}")
        assert complexity.get("is_complex", False), "Nested function should be complex"
        assert (
            complexity.get("nested_functions", 0) > 0
        ), "Should detect nested functions"

        # Execute and validate
        result = nested_computation(80)

        assert isinstance(result, dict)
        assert "data_size" in result
        assert result["data_size"] == 80
        assert "chunks_processed" in result
        assert result["chunks_processed"] > 0
        assert "analysis" in result
        assert "total" in result["analysis"]
        assert "cluster_info" in result

        print(f"✅ Nested function executed successfully on {cluster_name}")
        print(f"   Processed {result['chunks_processed']} chunks")
        print(f"   Total: {result['analysis']['total']:.2f}")
        print(f"   Hostname: {result['cluster_info']['hostname']}")

    @pytest.mark.parametrize("cluster_name", ["tensor01", "ndoli"])
    def test_gpu_simulation_computation(self, cluster_name):
        """Test GPU simulation computation (complex nested functions)."""
        config_data = TEST_CLUSTERS[cluster_name]

        print(f"\n🧪 Testing GPU simulation computation on {cluster_name}...")

        @cluster(**config_data)
        def gpu_simulation_computation(matrix_size=20):
            """Simulate GPU computation with complex nested functions."""

            def create_random_matrix(rows, cols):
                """Create a random matrix."""
                import random

                return [[random.random() for _ in range(cols)] for _ in range(rows)]

            def matrix_multiply(a, b):
                """Multiply two matrices."""
                if len(a[0]) != len(b):
                    raise ValueError("Matrix dimensions don't match")

                result = []
                for i in range(len(a)):
                    row = []
                    for j in range(len(b[0])):
                        sum_val = 0
                        for k in range(len(b)):
                            sum_val += a[i][k] * b[k][j]
                        row.append(sum_val)
                    result.append(row)
                return result

            def detect_gpu_capability():
                """Simulate GPU detection."""
                import subprocess
                import os

                gpu_info = {
                    "nvidia_smi_available": False,
                    "cuda_available": False,
                    "hostname": (
                        os.uname().nodename if hasattr(os, "uname") else "unknown"
                    ),
                }

                try:
                    result = subprocess.run(
                        ["nvidia-smi", "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        gpu_info["nvidia_smi_available"] = True
                except:
                    pass

                try:
                    result = subprocess.run(
                        ["nvcc", "--version"], capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        gpu_info["cuda_available"] = True
                except:
                    pass

                return gpu_info

            def compute_matrix_stats(matrix):
                """Compute statistics for a matrix."""
                flat = [val for row in matrix for val in row]
                return {
                    "mean": sum(flat) / len(flat),
                    "min": min(flat),
                    "max": max(flat),
                    "size": len(flat),
                }

            # Main computation pipeline
            matrix_a = create_random_matrix(matrix_size, matrix_size)
            matrix_b = create_random_matrix(matrix_size, matrix_size)

            # Perform computation
            result_matrix = matrix_multiply(matrix_a, matrix_b)
            stats = compute_matrix_stats(result_matrix)
            gpu_info = detect_gpu_capability()

            return {
                "matrix_size": matrix_size,
                "computation_stats": stats,
                "gpu_detection": gpu_info,
                "execution_info": {
                    "hostname": __import__("socket").gethostname(),
                    "python_version": __import__("sys").version_info[:2],
                },
            }

        # Check complexity
        complexity = analyze_function_complexity(gpu_simulation_computation)
        print(f"GPU simulation complexity: {complexity}")
        assert complexity.get("is_complex", False), "GPU simulation should be complex"
        assert (
            complexity.get("nested_functions", 0) >= 1
        ), "Should have nested functions"
        assert (
            complexity.get("complexity_score", 0) >= 50
        ), "Should have high complexity score"

        # Execute and validate
        result = gpu_simulation_computation(15)

        assert isinstance(result, dict)
        assert "matrix_size" in result
        assert result["matrix_size"] == 15
        assert "computation_stats" in result
        assert "gpu_detection" in result
        assert "execution_info" in result

        stats = result["computation_stats"]
        assert "mean" in stats
        assert "size" in stats
        assert stats["size"] == 15 * 15  # matrix_size^2

        print(f"✅ GPU simulation executed successfully on {cluster_name}")
        print(f"   Matrix computation: {stats['size']} elements")
        print(f"   Mean value: {stats['mean']:.4f}")
        print(
            f"   GPU detection: nvidia-smi={result['gpu_detection']['nvidia_smi_available']}"
        )
        print(f"   Hostname: {result['execution_info']['hostname']}")

    @pytest.mark.parametrize("cluster_name", ["tensor01", "ndoli"])
    def test_inline_function_pattern(self, cluster_name):
        """Test inline function pattern commonly used in GPU code."""
        config_data = TEST_CLUSTERS[cluster_name]

        print(f"\n🧪 Testing inline function pattern on {cluster_name}...")

        @cluster(**config_data)
        def test_gpu_computation_pattern():
            """Test pattern similar to actual GPU test cases."""

            def simple_gpu_matrix_mult():
                """Inline GPU computation function."""
                # Simulate what would be GPU computation
                import time
                import os

                start_time = time.time()

                # Simulate matrix operations
                size = 50
                result = 0
                for i in range(size):
                    for j in range(size):
                        result += i * j

                end_time = time.time()

                return {
                    "success": True,
                    "device": "cpu_simulation",  # Would be "cuda:0" with real GPU
                    "result_value": result,
                    "computation_time": end_time - start_time,
                    "matrix_size": size,
                    "hostname": (
                        os.uname().nodename if hasattr(os, "uname") else "unknown"
                    ),
                }

            # Execute the inline function
            result = simple_gpu_matrix_mult()
            return result

        # Check complexity
        complexity = analyze_function_complexity(test_gpu_computation_pattern)
        print(f"Inline function complexity: {complexity}")
        assert (
            complexity.get("nested_functions", 0) > 0
        ), "Should detect inline nested function"

        # Execute and validate
        result = test_gpu_computation_pattern()

        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is True
        assert "device" in result
        assert "result_value" in result
        assert "computation_time" in result
        assert "matrix_size" in result

        print(f"✅ Inline function pattern executed successfully on {cluster_name}")
        print(f"   Computation result: {result['result_value']}")
        print(f"   Time: {result['computation_time']:.4f}s")
        print(f"   Hostname: {result['hostname']}")

    @pytest.mark.parametrize("cluster_name", ["tensor01", "ndoli"])
    def test_enhanced_venv_setup_integration(self, cluster_name):
        """Test enhanced VENV setup with mock GPU packages."""
        config_data = TEST_CLUSTERS[cluster_name]

        print(f"\n🧪 Testing enhanced VENV setup on {cluster_name}...")

        @cluster(**config_data)
        def test_package_detection():
            """Test that enhanced VENV setup works with package detection."""
            import sys
            import importlib.util
            import os

            # Check what packages are available
            packages_found = {}
            test_packages = ["numpy", "dill", "cloudpickle"]

            for pkg in test_packages:
                try:
                    spec = importlib.util.find_spec(pkg)
                    packages_found[pkg] = spec is not None
                except ImportError:
                    packages_found[pkg] = False

            # Get system info
            system_info = {
                "python_version": sys.version,
                "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
                "platform": sys.platform,
                "packages": packages_found,
            }

            return system_info

        # Execute and validate
        result = test_package_detection()

        assert isinstance(result, dict)
        assert "python_version" in result
        assert "hostname" in result
        assert "packages" in result

        # Check that essential packages are available
        packages = result["packages"]
        assert packages.get("dill", False), "dill should be available for serialization"
        assert packages.get("cloudpickle", False), "cloudpickle should be available"

        print(f"✅ Enhanced VENV setup working on {cluster_name}")
        print(f"   Python: {result['python_version'].split()[0]}")
        print(f"   Essential packages: {[k for k, v in packages.items() if v]}")
        print(f"   Hostname: {result['hostname']}")

    def test_cross_cluster_compatibility(self):
        """Test that the same complex function works across different clusters."""
        print(f"\n🧪 Testing cross-cluster compatibility...")

        def complex_cross_cluster_function(data_size=30):
            """Complex function to test across clusters."""

            def fibonacci_sequence(n):
                """Generate fibonacci sequence."""
                if n <= 0:
                    return []
                elif n == 1:
                    return [0]
                elif n == 2:
                    return [0, 1]

                fib = [0, 1]
                for i in range(2, n):
                    fib.append(fib[i - 1] + fib[i - 2])
                return fib

            def prime_factors(n):
                """Find prime factors of a number."""
                factors = []
                d = 2
                while d * d <= n:
                    while n % d == 0:
                        factors.append(d)
                        n //= d
                    d += 1
                if n > 1:
                    factors.append(n)
                return factors

            def compute_statistics(numbers):
                """Compute various statistics."""
                if not numbers:
                    return {}

                return {
                    "count": len(numbers),
                    "sum": sum(numbers),
                    "mean": sum(numbers) / len(numbers),
                    "min": min(numbers),
                    "max": max(numbers),
                }

            # Main computation
            fib_seq = fibonacci_sequence(data_size)
            factors_list = [
                prime_factors(max(1, num)) for num in fib_seq[:10]
            ]  # First 10
            stats = compute_statistics(fib_seq)

            return {
                "data_size": data_size,
                "fibonacci_stats": stats,
                "factors_count": len(factors_list),
                "cluster_info": {
                    "hostname": __import__("socket").gethostname(),
                    "python_version": __import__("sys").version_info[:2],
                },
            }

        results = {}

        # Test on each cluster
        for cluster_name, config_data in TEST_CLUSTERS.items():
            print(f"\n  Testing on {cluster_name}...")

            # Create cluster-specific function
            cluster_func = cluster(**config_data)(complex_cross_cluster_function)

            # Execute
            result = cluster_func(25)
            results[cluster_name] = result

            # Validate individual result
            assert isinstance(result, dict)
            assert "data_size" in result
            assert result["data_size"] == 25
            assert "fibonacci_stats" in result
            assert "cluster_info" in result

            print(
                f"    ✅ {cluster_name}: {result['fibonacci_stats']['count']} fibonacci numbers"
            )
            print(f"       Hostname: {result['cluster_info']['hostname']}")

        # Compare results across clusters
        if len(results) > 1:
            cluster_names = list(results.keys())
            result1 = results[cluster_names[0]]
            result2 = results[cluster_names[1]]

            # Results should be mathematically identical
            assert result1["data_size"] == result2["data_size"]
            assert (
                result1["fibonacci_stats"]["count"]
                == result2["fibonacci_stats"]["count"]
            )
            assert (
                result1["fibonacci_stats"]["sum"] == result2["fibonacci_stats"]["sum"]
            )
            assert result1["factors_count"] == result2["factors_count"]

            print(f"\n✅ Cross-cluster compatibility verified")
            print(f"   Both clusters produced identical mathematical results")

        return results


if __name__ == "__main__":
    # Allow running individual tests
    import sys

    if len(sys.argv) > 1:
        cluster_name = sys.argv[1]
        if cluster_name in TEST_CLUSTERS:
            # Run tests for specific cluster
            test_instance = TestGPUFunctionalityRealWorld()

            print(f"🚀 Running GPU functionality tests on {cluster_name}")
            print("=" * 60)

            tests = [
                test_instance.test_gpu_detection_real_cluster,
                test_instance.test_simple_function_execution,
                test_instance.test_nested_function_flattening,
                test_instance.test_gpu_simulation_computation,
                test_instance.test_inline_function_pattern,
                test_instance.test_enhanced_venv_setup_integration,
            ]

            results = []
            for test_func in tests:
                try:
                    test_func(cluster_name)
                    results.append(True)
                    print(f"✅ {test_func.__name__} passed")
                except Exception as e:
                    print(f"❌ {test_func.__name__} failed: {e}")
                    results.append(False)

            passed = sum(results)
            total = len(results)
            print(f"\n📊 Results: {passed}/{total} tests passed on {cluster_name}")
        else:
            print(f"Unknown cluster: {cluster_name}")
            print(f"Available clusters: {list(TEST_CLUSTERS.keys())}")
    else:
        print("Usage: python test_real_world_gpu_functionality.py <cluster_name>")
        print(f"Available clusters: {list(TEST_CLUSTERS.keys())}")
