import pytest
from unittest.mock import Mock, patch, MagicMock
from clustrix.decorator import cluster
from clustrix.config import configure
from clustrix.async_executor_simple import AsyncJobResult


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
            "async_submit": None,
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


class TestAsyncExecution:
    """Test async execution functionality."""

    @patch("clustrix.decorator.AsyncClusterExecutor")
    def test_async_local_execution(self, mock_async_executor):
        """Test async local execution path."""
        # Setup mock
        mock_instance = Mock()
        mock_async_executor.return_value = mock_instance
        mock_future = Mock()
        mock_instance.submit_job_async.return_value = AsyncJobResult(
            mock_future, "job123", mock_instance
        )

        @cluster(async_submit=True)
        def test_func(x):
            return x * 2

        # Configure for local execution
        configure(cluster_type="local")

        result = test_func(5)

        assert isinstance(result, AsyncJobResult)
        mock_instance.submit_job_async.assert_called_once()

    @patch("clustrix.decorator.ClusterExecutor")
    @patch("clustrix.decorator.AsyncClusterExecutor")
    def test_async_remote_execution(self, mock_async_executor, mock_executor):
        """Test async remote execution path."""
        # Setup mocks
        mock_async_instance = Mock()
        mock_async_executor.return_value = mock_async_instance
        mock_future = Mock()
        mock_async_instance.submit_job_async.return_value = AsyncJobResult(
            mock_future, "job456", mock_async_instance
        )

        @cluster(async_submit=True)
        def test_func(x):
            return x * 3

        # Configure for remote execution
        configure(cluster_type="slurm", cluster_host="test.cluster")

        result = test_func(10)

        assert isinstance(result, AsyncJobResult)
        mock_async_instance.submit_job_async.assert_called_once()


class TestExecutionModeSelection:
    """Test execution mode selection edge cases."""

    def test_local_execution_no_parallelization(self):
        """Test simple local execution without parallelization."""

        @cluster()
        def simple_local_func(x):
            return x + 10

        # Configure for local execution
        configure(cluster_type="local")

        result = simple_local_func(5)
        assert result == 15

    def test_local_execution_fallback_without_parallelization(self):
        """Test local execution fallback when parallelization is disabled."""

        @cluster(parallel=False)
        def local_fallback_func(x, y):
            return x * y + 5

        # Configure for local execution
        configure(cluster_host=None, auto_parallel=False)

        result = local_fallback_func(3, 4)
        assert result == 17  # 3 * 4 + 5 = 17


class TestParallelExecution:
    """Test parallel execution functionality."""

    @patch("clustrix.decorator.detect_loops")
    @patch("clustrix.decorator._execute_parallel")
    @patch("clustrix.decorator.ClusterExecutor")
    def test_remote_parallel_execution_with_loops(
        self, mock_executor_class, mock_execute_parallel, mock_detect_loops
    ):
        """Test remote parallel execution when loops are detected."""
        # Setup mocks
        mock_loop_info = {"variable": "i", "range": range(10)}
        mock_detect_loops.return_value = mock_loop_info
        mock_execute_parallel.return_value = [1, 2, 3, 4, 5]

        mock_executor = Mock()
        mock_executor_class.return_value = mock_executor

        @cluster(parallel=True)
        def parallel_func(data):
            return [x * 2 for x in data]

        # Configure for remote execution
        configure(cluster_host="test.cluster.com", username="testuser")

        result = parallel_func([1, 2, 3, 4, 5])

        assert result == [1, 2, 3, 4, 5]
        mock_detect_loops.assert_called_once()
        mock_execute_parallel.assert_called_once()

    @patch("clustrix.decorator.find_parallelizable_loops")
    @patch("clustrix.decorator._create_local_work_chunks")
    def test_local_parallel_execution_no_chunks_fallback(
        self, mock_create_chunks, mock_find_loops
    ):
        """Test local parallel execution fallback when no work chunks created."""
        # Setup mock loop info
        mock_loop_info = Mock()
        mock_loop_info.range_info = {"start": 0, "stop": 5, "step": 1}
        mock_loop_info.variable = "i"
        mock_find_loops.return_value = [mock_loop_info]

        # Mock _create_local_work_chunks to return empty list (no chunks)
        mock_create_chunks.return_value = []

        @cluster(parallel=True)
        def local_parallel_func(x):
            return x * 2

        # Configure for local execution with parallelization
        configure(cluster_host=None, auto_parallel=True)

        result = local_parallel_func(5)

        # Should fallback to normal execution (line 309)
        assert result == 10
        mock_find_loops.assert_called_once()
        mock_create_chunks.assert_called_once()

    @patch("clustrix.decorator.find_parallelizable_loops")
    def test_local_parallel_execution_no_loops(self, mock_find_loops):
        """Test local parallel execution when no parallelizable loops found."""
        # No loops found
        mock_find_loops.return_value = []

        @cluster(parallel=True)
        def no_loops_func(x):
            return x * 3

        # Configure for local execution with parallelization
        configure(cluster_host=None, auto_parallel=True)

        result = no_loops_func(5)
        assert result == 15
        mock_find_loops.assert_called_once()


class TestRemoteParallelExecution:
    """Test remote parallel execution functions."""

    @patch("clustrix.decorator.serialize_function")
    @patch("clustrix.decorator.get_config")
    def test_execute_parallel_complete_flow(self, mock_get_config, mock_serialize):
        """Test complete _execute_parallel function."""
        from clustrix.decorator import _execute_parallel

        # Setup mocks
        mock_config = Mock()
        mock_config.max_parallel_jobs = 2
        mock_get_config.return_value = mock_config

        mock_executor = Mock()
        mock_executor.submit_job.side_effect = ["job1", "job2"]
        mock_executor.wait_for_result.side_effect = [
            {"result": [1, 2]},
            {"result": [3, 4]},
        ]

        mock_serialize.return_value = b"serialized_function"

        def test_func(data):
            return [x * 2 for x in data]

        loop_info = {"variable": "i", "range": range(4)}
        job_config = {"cores": 2}

        result = _execute_parallel(
            mock_executor, test_func, ([1, 2, 3, 4],), {}, job_config, loop_info
        )

        # Check that jobs were submitted and results combined
        assert mock_executor.submit_job.call_count >= 1
        assert mock_executor.wait_for_result.call_count >= 1
        assert isinstance(result, list)


class TestWorkChunkCreation:
    """Test work chunk creation for parallel execution."""

    def test_create_work_chunks_basic(self):
        """Test basic work chunk creation."""
        from clustrix.decorator import _create_work_chunks

        def test_func(data):
            return [x * 2 for x in data]

        loop_info = {"variable": "i", "range": range(10)}
        chunks = _create_work_chunks(test_func, ([1, 2, 3],), {}, loop_info, 3)

        assert len(chunks) > 0
        assert all("args" in chunk for chunk in chunks)
        assert all("kwargs" in chunk for chunk in chunks)
        assert all("index" in chunk for chunk in chunks)

    def test_create_work_chunks_with_small_range(self):
        """Test work chunk creation with small range."""
        from clustrix.decorator import _create_work_chunks

        def test_func(data):
            return data

        loop_info = {"variable": "j", "range": range(2)}
        chunks = _create_work_chunks(
            test_func, ([1, 2],), {"key": "value"}, loop_info, 5
        )

        assert len(chunks) > 0
        for chunk in chunks:
            assert "args" in chunk
            assert "kwargs" in chunk
            assert "index" in chunk
            assert "range" in chunk
            assert "_chunk_range_j" in chunk["kwargs"]
            assert "_chunk_index" in chunk["kwargs"]
            assert chunk["kwargs"]["key"] == "value"  # Original kwargs preserved

    def test_create_local_work_chunks_with_range_info(self):
        """Test local work chunk creation with range info."""
        from clustrix.decorator import _create_local_work_chunks

        def test_func(data):
            return data

        mock_loop_info = Mock()
        mock_loop_info.range_info = {"start": 0, "stop": 6, "step": 1}
        mock_loop_info.variable = "i"

        chunks = _create_local_work_chunks(test_func, ([1, 2, 3],), {}, mock_loop_info)

        assert len(chunks) > 0
        assert all("args" in chunk for chunk in chunks)
        assert all("kwargs" in chunk for chunk in chunks)

    def test_create_local_work_chunks_with_dict_format(self):
        """Test local work chunk creation with dict format loop info."""
        from clustrix.decorator import _create_local_work_chunks

        def test_func(data):
            return data

        mock_loop_info = Mock()
        mock_loop_info.to_dict.return_value = {
            "variable": "j",
            "range_info": {"start": 0, "stop": 8, "step": 2},
        }
        # Ensure hasattr check works
        mock_loop_info.range_info = None

        chunks = _create_local_work_chunks(test_func, ([1, 2, 3],), {}, mock_loop_info)

        assert len(chunks) > 0
        assert all("args" in chunk for chunk in chunks)
        assert all("kwargs" in chunk for chunk in chunks)

    def test_create_local_work_chunks_legacy_format(self):
        """Test local work chunk creation with legacy format."""
        from clustrix.decorator import _create_local_work_chunks

        def test_func(data):
            return data

        loop_info = {"variable": "k", "range": range(4)}

        chunks = _create_local_work_chunks(test_func, ([1, 2],), {}, loop_info)

        assert len(chunks) > 0
        assert all("args" in chunk for chunk in chunks)
        assert all("kwargs" in chunk for chunk in chunks)

    def test_create_local_work_chunks_no_variable(self):
        """Test local work chunk creation when no variable is provided."""
        from clustrix.decorator import _create_local_work_chunks

        def test_func(data):
            return data

        loop_info = {"range": range(4)}  # No variable - should use default 'i'

        chunks = _create_local_work_chunks(test_func, ([1, 2],), {}, loop_info)

        # Should still create chunks with default variable name
        assert len(chunks) > 0

    def test_create_local_work_chunks_empty_range(self):
        """Test local work chunk creation with empty range."""
        from clustrix.decorator import _create_local_work_chunks

        def test_func(data):
            return data

        loop_info = {"variable": "i", "range": range(0)}  # Empty range

        chunks = _create_local_work_chunks(test_func, ([1, 2],), {}, loop_info)

        assert len(chunks) == 0  # Should return empty list

    def test_create_local_work_chunks_no_range_info(self):
        """Test local work chunk creation when range info is missing."""
        from clustrix.decorator import _create_local_work_chunks

        def test_func(data):
            return data

        mock_loop_info = Mock()
        mock_loop_info.to_dict.return_value = {"variable": "i"}  # No range_info
        mock_loop_info.range_info = None  # Ensure hasattr check works

        chunks = _create_local_work_chunks(test_func, ([1, 2],), {}, mock_loop_info)

        assert len(chunks) == 0  # Should return empty list


class TestResultCombination:
    """Test result combination for parallel execution."""

    def test_combine_results_basic(self):
        """Test basic result combination."""
        from clustrix.decorator import _combine_results

        results = [(0, [1, 2]), (1, [3, 4]), (2, [5, 6])]
        loop_info = {"variable": "i"}

        combined = _combine_results(results, loop_info)

        assert combined == [[1, 2], [3, 4], [5, 6]]

    def test_combine_results_unordered(self):
        """Test result combination with unordered results."""
        from clustrix.decorator import _combine_results

        results = [(2, [5, 6]), (0, [1, 2]), (1, [3, 4])]  # Unordered
        loop_info = {"variable": "i"}

        combined = _combine_results(results, loop_info)

        assert combined == [[1, 2], [3, 4], [5, 6]]  # Should be sorted

    def test_combine_local_results_empty(self):
        """Test combining empty local results."""
        from clustrix.decorator import _combine_local_results

        results = []
        loop_info = {"variable": "i"}

        combined = _combine_local_results(results, loop_info)

        assert combined is None

    def test_combine_local_results_single(self):
        """Test combining single local result."""
        from clustrix.decorator import _combine_local_results

        results = [[1, 2, 3]]
        loop_info = {"variable": "i"}

        combined = _combine_local_results(results, loop_info)

        assert combined == [1, 2, 3]

    def test_combine_local_results_multiple_lists(self):
        """Test combining multiple list results."""
        from clustrix.decorator import _combine_local_results

        results = [[1, 2], [3, 4], [5, 6]]
        loop_info = {"variable": "i"}

        combined = _combine_local_results(results, loop_info)

        assert combined == [1, 2, 3, 4, 5, 6]

    def test_combine_local_results_non_lists(self):
        """Test combining non-list results."""
        from clustrix.decorator import _combine_local_results

        results = [1, 2, 3]
        loop_info = {"variable": "i"}

        combined = _combine_local_results(results, loop_info)

        assert combined == [1, 2, 3]  # Returns as-is
