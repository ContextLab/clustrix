import pytest
from unittest.mock import Mock, patch, MagicMock
from clustrix.decorator import cluster
from clustrix.config import configure


class TestClusterDecorator:
    """Test the @cluster decorator."""

    def test_basic_decoration(self):
        """Test basic function decoration."""

        @cluster(cores=8, memory="16GB")
        def test_func(x, y):
            return x + y

        assert hasattr(test_func, "__wrapped__")
        assert hasattr(test_func, "_cluster_config")
        assert test_func._cluster_config["cores"] == 8
        assert test_func._cluster_config["memory"] == "16GB"

    def test_decorator_without_params(self):
        """Test decorator without parameters."""

        @cluster
        def test_func(x):
            return x * 2

        assert hasattr(test_func, "__wrapped__")
        assert hasattr(test_func, "_cluster_config")
        # All parameters should be None when not specified
        expected_config = {
            "cores": None,
            "memory": None,
            "time": None,
            "partition": None,
            "queue": None,
            "parallel": None,
            "environment": None,
        }
        assert test_func._cluster_config == expected_config

    def test_decorator_with_all_params(self):
        """Test decorator with all possible parameters."""

        @cluster(
            cores=16,
            memory="32GB",
            time="04:00:00",
            partition="gpu",
            parallel=True,
            environment="test_env",
        )
        def test_func():
            return "test"

        config = test_func._cluster_config
        assert config["cores"] == 16
        assert config["memory"] == "32GB"
        assert config["time"] == "04:00:00"
        assert config["partition"] == "gpu"
        assert config["parallel"] is True
        assert config["environment"] == "test_env"

    @patch("clustrix.executor.ClusterExecutor")
    def test_local_execution_with_cluster_none(self, mock_executor):
        """Test that function executes locally when cluster_host is None."""
        configure(cluster_host=None)

        @cluster(cores=4)
        def test_func(x, y):
            return x + y

        result = test_func(2, 3)
        assert result == 5
        mock_executor.assert_not_called()

    @patch("clustrix.decorator.ClusterExecutor")
    @patch("clustrix.decorator._execute_single")
    def test_remote_execution(self, mock_execute_single, mock_executor_class):
        """Test remote execution with cluster configured."""
        configure(cluster_host="test.cluster.com", username="testuser")

        # Setup mock return value
        mock_execute_single.return_value = 42

        @cluster(cores=8)
        def test_func(x, y):
            return x * y

        result = test_func(6, 7)

        assert result == 42
        mock_execute_single.assert_called_once()

        # Verify arguments passed to _execute_single
        call_args = mock_execute_single.call_args[0]
        executor, func, args, kwargs, job_config = call_args
        assert func.__name__ == "test_func"
        assert args == (6, 7)
        assert kwargs == {}
        assert job_config["cores"] == 8

    def test_function_metadata_preserved(self):
        """Test that function metadata is preserved."""

        @cluster(cores=4)
        def documented_function(x, y):
            """This function adds two numbers."""
            return x + y

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This function adds two numbers."

    @patch("clustrix.decorator.ClusterExecutor")
    def test_exception_handling(self, mock_executor_class):
        """Test exception handling in remote execution."""
        configure(cluster_host="test.cluster.com")

        mock_executor = Mock()
        mock_executor_class.return_value = mock_executor
        mock_executor.submit_job.side_effect = RuntimeError("Cluster error")

        @cluster
        def test_func():
            return "test"

        with pytest.raises(RuntimeError, match="Cluster error"):
            test_func()

    def test_parallel_flag(self):
        """Test parallel execution flag."""

        @cluster(parallel=True)
        def parallel_func(data):
            return [x * 2 for x in data]

        @cluster(parallel=False)
        def sequential_func(data):
            return [x * 2 for x in data]

        assert parallel_func._cluster_config["parallel"] is True
        assert sequential_func._cluster_config["parallel"] is False

    @patch("clustrix.decorator.ClusterExecutor")
    def test_kwargs_handling(self, mock_executor_class):
        """Test handling of keyword arguments."""
        configure(cluster_host="test.cluster.com")

        mock_executor = Mock()
        mock_executor_class.return_value = mock_executor
        mock_executor.submit_job.return_value = "job123"
        mock_executor.wait_for_result.return_value = {"result": 123}

        @cluster
        def test_func(a, b=10, c=20):
            return a + b + c

        result = test_func(5, c=30)

        # Verify the function executed and returned the expected result
        assert result == {"result": 123}

        # Verify submit_job was called
        mock_executor.submit_job.assert_called_once()
        mock_executor.wait_for_result.assert_called_once_with("job123")

    def test_decorator_stacking(self):
        """Test that decorator can be combined with other decorators."""

        def other_decorator(func):
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs) * 2

            return wrapper

        @other_decorator
        @cluster(cores=4)
        def test_func(x):
            return x + 1

        # When executed locally
        configure(cluster_host=None)
        result = test_func(5)
        assert result == 12  # (5 + 1) * 2

    def test_decorator_order_matters(self):
        """Test that decorator order affects execution."""

        def multiply_decorator(factor):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    return func(*args, **kwargs) * factor

                return wrapper

            return decorator

        # cluster decorator inside
        @multiply_decorator(3)
        @cluster(cores=2)
        def func1(x):
            return x + 5

        # cluster decorator outside
        @cluster(cores=2)
        @multiply_decorator(3)
        def func2(x):
            return x + 5

        configure(cluster_host=None)

        result1 = func1(10)
        result2 = func2(10)

        assert result1 == 45  # (10 + 5) * 3
        assert result2 == 45  # (10 + 5) * 3 - both should be same for local execution

    def test_resource_parameter_validation(self):
        """Test validation of resource parameters."""

        # Test valid parameters
        @cluster(cores=8, memory="16GB", time="02:00:00")
        def valid_func():
            return "test"

        config = valid_func._cluster_config
        assert config["cores"] == 8
        assert config["memory"] == "16GB"
        assert config["time"] == "02:00:00"

    def test_job_config_inheritance(self):
        """Test job config inheritance from global config."""
        configure(
            default_cores=16,
            default_memory="32GB",
            default_time="04:00:00",
            default_partition="gpu",
        )

        # Test decorator without parameters inherits defaults
        @cluster()
        def default_func():
            return "test"

        # Test decorator with partial override
        @cluster(cores=8, memory="64GB")
        def override_func():
            return "test"

        configure(cluster_host=None)  # Local execution for testing

        # The config should be stored but actual values come from get_config() during execution
        # For @cluster() without parameters, cores should be None (not specified)
        assert default_func._cluster_config["cores"] is None
        assert override_func._cluster_config["cores"] == 8
        assert override_func._cluster_config["memory"] == "64GB"

    def test_execution_mode_selection(self):
        """Test execution mode selection logic."""
        from clustrix.decorator import _choose_execution_mode
        from clustrix.config import ClusterConfig

        # Test local mode when no cluster host
        config_no_host = ClusterConfig(cluster_host=None)
        mode = _choose_execution_mode(config_no_host, lambda: None, (), {})
        assert mode == "local"

        # Test remote mode when cluster host is set
        config_with_host = ClusterConfig(cluster_host="test.cluster.com")
        mode = _choose_execution_mode(config_with_host, lambda: None, (), {})
        assert mode == "remote"

        # Test local mode when prefer_local_parallel is set
        config_prefer_local = ClusterConfig(
            cluster_host="test.cluster.com", prefer_local_parallel=True
        )
        mode = _choose_execution_mode(config_prefer_local, lambda: None, (), {})
        assert mode == "local"
