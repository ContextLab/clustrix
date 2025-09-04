"""Performance regression tests for core clustrix functionality."""

import pytest
from clustrix import cluster, configure
from clustrix.utils import serialize_function, deserialize_function, detect_loops
from clustrix.config import ClusterConfig
import time


@pytest.mark.performance
class TestPerformanceRegression:
    """Performance regression tests to ensure core operations stay fast."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Configure for local execution for performance tests."""
        configure(cluster_host=None)

    def test_function_serialization_performance(self, benchmark):
        """Ensure function serialization stays performant."""

        def sample_function(x, y=10):
            return x + y + sum(range(100))

        result = benchmark(serialize_function, sample_function, [], {})
        assert result is not None

    def test_function_deserialization_performance(self, benchmark):
        """Ensure function deserialization stays performant."""

        def sample_function(x, y=10):
            return x + y + sum(range(100))

        serialized = serialize_function(sample_function, [], {})
        result = benchmark(deserialize_function, serialized)
        assert result is not None

    def test_loop_detection_performance(self, benchmark):
        """Ensure loop detection stays performant."""

        def loop_function():
            results = []
            for i in range(100):
                results.append(i * 2)
            return results

        result = benchmark(detect_loops, loop_function, [], {})
        # detect_loops can return None for functions without parallelizable loops
        # The important thing is that it completes quickly

    def test_config_creation_performance(self, benchmark):
        """Ensure config creation stays performant."""
        result = benchmark(ClusterConfig, cluster_type="local", cluster_host=None)
        assert result is not None

    def test_decorator_overhead_performance(self, benchmark):
        """Ensure decorator overhead is minimal."""

        @cluster(cores=1)
        def decorated_function(x):
            return x * 2

        # Benchmark just the decorator application time
        def create_decorated_function():
            @cluster(cores=1)
            def temp_function(x):
                return x * 2

            return temp_function

        result = benchmark(create_decorated_function)
        assert result is not None

    @pytest.mark.slow
    def test_local_execution_performance(self, benchmark):
        """Ensure local execution performance is acceptable."""

        @cluster(cores=1)
        def compute_heavy_function():
            return sum(i * i for i in range(1000))

        result = benchmark(compute_heavy_function)
        assert result == sum(i * i for i in range(1000))
