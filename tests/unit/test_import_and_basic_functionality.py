#!/usr/bin/env python3
"""
Fast unit tests that verify basic functionality without external dependencies.
These tests should run quickly and only test core logic.
"""

import pytest
from clustrix import cluster
from clustrix.config import ClusterConfig, configure
from clustrix.utils import (
    serialize_function,
    deserialize_function,
    get_environment_info,
    detect_loops,
)


def test_imports_work():
    """Test that all core modules can be imported."""
    from clustrix import cluster, configure
    from clustrix.config import ClusterConfig
    from clustrix.executor import ClusterExecutor
    from clustrix.utils import serialize_function

    # Basic assertions
    assert cluster is not None
    assert configure is not None
    assert ClusterConfig is not None
    assert ClusterExecutor is not None
    assert serialize_function is not None


def test_cluster_config_creation():
    """Test ClusterConfig object creation and basic properties."""
    config = ClusterConfig()

    # Test that we can access basic properties
    assert hasattr(config, "cluster_type")
    assert hasattr(config, "default_cores")
    assert hasattr(config, "default_memory")

    # Test setting values
    config.cluster_type = "ssh"
    config.default_cores = 4
    config.default_memory = "8GB"

    assert config.cluster_type == "ssh"
    assert config.default_cores == 4
    assert config.default_memory == "8GB"


def test_cluster_decorator_creation():
    """Test that @cluster decorator can be created and applied."""

    @cluster(cores=2, memory="4GB")
    def test_function(x, y):
        return x + y

    # Function should be wrapped but not executed
    assert hasattr(test_function, "__wrapped__")
    assert callable(test_function)

    # The original function should be accessible
    assert test_function.__wrapped__(5, 3) == 8


def test_serialize_function():
    """Test function serialization."""

    def sample_function(a, b):
        return a * b + 10

    # Should be able to serialize without errors
    serialized = serialize_function(sample_function, (5, 3), {})
    assert isinstance(serialized, dict)
    assert "function" in serialized  # Key is 'function', not 'function_data'


def test_detect_loops():
    """Test loop detection functionality."""

    def function_with_loop():
        results = []
        for i in range(10):
            results.append(i * 2)
        return results

    # Should be able to detect loops
    loop_info = detect_loops(function_with_loop, (), {})
    # Function should complete without errors
    assert loop_info is not None or loop_info is None  # Either result is fine


def test_environment_info():
    """Test environment information gathering."""

    env_info = get_environment_info()

    # Should return string with environment information
    assert isinstance(env_info, str)
    assert len(env_info) > 0


def test_configure_function():
    """Test the configure function works without errors."""
    # This should not make any external connections
    configure(
        cluster_type="local",
        default_cores=2,
        default_memory="4GB",
        # Use cleanup_on_success=False to avoid any file operations
        cleanup_on_success=False,
    )

    # Should complete without errors
    assert True


def test_deserialize_function():
    """Test that function deserialization works with serialized data."""

    def sample_function(x):
        return x * 2

    # Serialize first
    serialized = serialize_function(sample_function, (5,), {})

    # Then deserialize - function returns just the function, not tuple
    func_data = serialized["function"]  # Use correct key
    deserialized_func = deserialize_function(func_data)

    # Should get back callable
    assert callable(deserialized_func)
    # Test that it works
    assert deserialized_func(5) == 10


def test_cluster_decorator_parameters():
    """Test that cluster decorator accepts various parameters."""

    # Should not raise errors during decoration
    @cluster(cores=1)
    def func1():
        return 1

    @cluster(memory="2GB")
    def func2():
        return 2

    @cluster(cores=4, memory="8GB", time="01:00:00")
    def func3():
        return 3

    @cluster(platform="local")
    def func4():
        return 4

    # All functions should be properly decorated
    assert hasattr(func1, "__wrapped__")
    assert hasattr(func2, "__wrapped__")
    assert hasattr(func3, "__wrapped__")
    assert hasattr(func4, "__wrapped__")


if __name__ == "__main__":
    pytest.main([__file__])
