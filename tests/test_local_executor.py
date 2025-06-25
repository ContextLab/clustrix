import pytest
import time
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from unittest.mock import Mock, patch, MagicMock

from clustrix.local_executor import (
    LocalExecutor,
    create_local_executor,
    choose_executor_type,
    _safe_pickle_test,
)


# Module-level functions for pickling tests
def cpu_bound_function(n):
    """A CPU-bound function for testing."""
    total = 0
    for i in range(n):
        total += i**2
    return total


def io_bound_function(filename):
    """An I/O-bound function for testing."""
    with open(filename, "r") as f:
        return f.read()


def fibonacci(n):
    """Fibonacci function for CPU-intensive tests."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


class TestLocalExecutor:
    """Test LocalExecutor class thoroughly."""

    def test_initialization_defaults(self):
        """Test default initialization."""
        executor = LocalExecutor()
        assert executor.max_workers == mp.cpu_count()
        assert executor.use_threads is False
        assert executor._executor is None

    def test_initialization_custom(self):
        """Test custom initialization parameters."""
        executor = LocalExecutor(max_workers=8, use_threads=True)
        assert executor.max_workers == 8
        assert executor.use_threads is True

    def test_context_manager_processes(self):
        """Test context manager with ProcessPoolExecutor."""
        with LocalExecutor(max_workers=2, use_threads=False) as executor:
            assert isinstance(executor._executor, ProcessPoolExecutor)
            assert executor._executor._max_workers == 2

    def test_context_manager_threads(self):
        """Test context manager with ThreadPoolExecutor."""
        with LocalExecutor(max_workers=3, use_threads=True) as executor:
            assert isinstance(executor._executor, ThreadPoolExecutor)
            assert executor._executor._max_workers == 3

    def test_execute_single_success(self):
        """Test successful single function execution."""
        executor = LocalExecutor()

        def test_func(x, y):
            return x * y

        result = executor.execute_single(test_func, (6, 7), {})
        assert result == 42

    def test_execute_single_with_kwargs(self):
        """Test single execution with keyword arguments."""
        executor = LocalExecutor()

        def test_func(x, y=10, z=5):
            return x + y + z

        result = executor.execute_single(test_func, (5,), {"z": 15})
        assert result == 30  # 5 + 10 + 15

    def test_execute_single_exception(self):
        """Test single execution with exception."""
        executor = LocalExecutor()

        def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            executor.execute_single(failing_func, (), {})

    def test_execute_parallel_empty_chunks(self):
        """Test parallel execution with empty work chunks."""
        executor = LocalExecutor(max_workers=2)
        result = executor.execute_parallel(lambda x: x, [])
        assert result == []

    def test_execute_parallel_single_chunk(self):
        """Test parallel execution with single chunk."""
        executor = LocalExecutor(max_workers=2)

        def test_func(x):
            return x * 2

        work_chunks = [{"args": (5,), "kwargs": {}}]
        results = executor.execute_parallel(test_func, work_chunks)

        assert len(results) == 1
        assert results[0] == 10

    def test_execute_parallel_multiple_chunks(self):
        """Test parallel execution with multiple chunks."""

        def test_func(x, multiplier=1):
            return x * multiplier

        work_chunks = [
            {"args": (1,), "kwargs": {"multiplier": 2}},
            {"args": (2,), "kwargs": {"multiplier": 3}},
            {"args": (3,), "kwargs": {"multiplier": 4}},
            {"args": (4,), "kwargs": {"multiplier": 5}},
        ]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            results = executor.execute_parallel(test_func, work_chunks)

        assert len(results) == 4
        assert results[0] == 2  # 1 * 2
        assert results[1] == 6  # 2 * 3
        assert results[2] == 12  # 3 * 4
        assert results[3] == 20  # 4 * 5

    def test_execute_parallel_with_timeout(self):
        """Test parallel execution with timeout."""

        def slow_func(delay):
            time.sleep(delay)
            return delay

        work_chunks = [{"args": (0.1,), "kwargs": {}}, {"args": (0.2,), "kwargs": {}}]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            # Should complete within timeout
            results = executor.execute_parallel(slow_func, work_chunks, timeout=1.0)
            assert len(results) == 2
            assert results[0] == 0.1
            assert results[1] == 0.2

    def test_execute_parallel_timeout_exceeded(self):
        """Test parallel execution when timeout is exceeded."""

        def very_slow_func(delay):
            time.sleep(delay)
            return delay

        # Use multiple tasks so some don't start and can be cancelled
        work_chunks = [
            {"args": (2.0,), "kwargs": {}},  # 2 second delay
            {"args": (3.0,), "kwargs": {}},  # 3 second delay
        ]

        with LocalExecutor(max_workers=1, use_threads=True) as executor:
            # Should timeout after 0.5 seconds - first task will start but second won't
            with pytest.raises(TimeoutError):
                executor.execute_parallel(very_slow_func, work_chunks, timeout=0.5)

    def test_execute_parallel_task_failure(self):
        """Test parallel execution when one task fails."""

        def failing_func(x):
            if x == 2:
                raise ValueError(f"Failed on {x}")
            return x * 2

        work_chunks = [
            {"args": (1,), "kwargs": {}},
            {"args": (2,), "kwargs": {}},  # This will fail
            {"args": (3,), "kwargs": {}},
        ]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            with pytest.raises(ValueError, match="Failed on 2"):
                executor.execute_parallel(failing_func, work_chunks)

    def test_execute_loop_parallel_range(self):
        """Test parallel loop execution with range."""

        def process_item(i):
            return i**2

        executor = LocalExecutor(max_workers=2, use_threads=True)

        results = executor.execute_loop_parallel(
            process_item, "i", range(5), chunk_size=2
        )

        # Results should be squares: [0, 1, 4, 9, 16]
        assert len(results) == 5
        expected = [i**2 for i in range(5)]
        assert results == expected

    def test_execute_loop_parallel_list(self):
        """Test parallel loop execution with list."""

        def process_item(item):
            return item * 3

        items = [1, 2, 3, 4, 5]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            results = executor.execute_loop_parallel(process_item, "item", items)

        assert len(results) == 5
        assert results == [3, 6, 9, 12, 15]

    def test_execute_loop_parallel_empty_iterable(self):
        """Test parallel loop execution with empty iterable."""

        def process_item(item):
            return item

        executor = LocalExecutor()
        results = executor.execute_loop_parallel(process_item, "item", [])
        assert results == []

    def test_execute_loop_parallel_with_extra_args(self):
        """Test parallel loop execution with additional function arguments."""

        def process_item(base, item, multiplier=1):
            return base + (item * multiplier)

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            results = executor.execute_loop_parallel(
                process_item,
                "item",
                [1, 2, 3],
                func_args=(10,),
                func_kwargs={"multiplier": 5},
            )

        # Results: 10 + (1*5) = 15, 10 + (2*5) = 20, 10 + (3*5) = 25
        assert results == [15, 20, 25]


class TestExecutorTypeSelection:
    """Test automatic executor type selection."""

    def test_safe_pickle_test_success(self):
        """Test successful pickle test."""
        # Use module-level function that can be pickled
        assert _safe_pickle_test(cpu_bound_function) is True
        assert _safe_pickle_test([1, 2, 3]) is True
        assert _safe_pickle_test({"key": "value"}) is True

    def test_safe_pickle_test_failure(self):
        """Test pickle test with unpicklable objects."""
        # Lambda functions are not picklable
        lambda_func = lambda x: x
        assert _safe_pickle_test(lambda_func) is False

        # File objects are not picklable
        with open(__file__, "r") as f:
            assert _safe_pickle_test(f) is False

    def test_choose_executor_type_unpicklable_function(self):
        """Test executor type choice with unpicklable function."""
        lambda_func = lambda x: x
        result = choose_executor_type(lambda_func, (), {})
        assert result is True  # Should use threads

    def test_choose_executor_type_unpicklable_args(self):
        """Test executor type choice with unpicklable arguments."""

        def simple_func(x):
            return x

        with open(__file__, "r") as f:
            result = choose_executor_type(simple_func, (f,), {})
            assert result is True  # Should use threads

    def test_choose_executor_type_unpicklable_kwargs(self):
        """Test executor type choice with unpicklable keyword arguments."""

        def simple_func(data=None):
            return str(data)

        with open(__file__, "r") as f:
            result = choose_executor_type(simple_func, (), {"data": f})
            assert result is True  # Should use threads

    def test_choose_executor_type_io_bound(self):
        """Test executor type choice for I/O bound functions."""

        def io_func():
            with open("test.txt", "w") as f:
                f.write("test")
            return "done"

        result = choose_executor_type(io_func, (), {})
        assert result is True  # Should use threads for I/O

    def test_choose_executor_type_network_io(self):
        """Test executor type choice for network I/O functions."""

        def network_func():
            import requests

            return requests.get("http://example.com")

        result = choose_executor_type(network_func, (), {})
        assert result is True  # Should use threads for network I/O

    def test_choose_executor_type_cpu_bound(self):
        """Test executor type choice for CPU-bound functions."""
        result = choose_executor_type(cpu_bound_function, (1000,), {})
        assert result is False  # Should use processes for CPU-bound

    def test_choose_executor_type_source_unavailable(self):
        """Test executor type choice when source code is unavailable."""
        # Built-in functions don't have source code
        result = choose_executor_type(len, ([1, 2, 3],), {})
        assert result is False  # Default to processes


class TestCreateLocalExecutor:
    """Test local executor factory function."""

    def test_create_default(self):
        """Test creating executor with defaults."""
        executor = create_local_executor()
        assert isinstance(executor, LocalExecutor)
        assert executor.use_threads is False  # Default to processes

    def test_create_with_custom_workers(self):
        """Test creating executor with custom worker count."""
        executor = create_local_executor(max_workers=6)
        assert executor.max_workers == 6

    def test_create_force_threads(self):
        """Test creating executor with forced thread usage."""
        executor = create_local_executor(use_threads=True)
        assert executor.use_threads is True

    def test_create_force_processes(self):
        """Test creating executor with forced process usage."""
        executor = create_local_executor(use_threads=False)
        assert executor.use_threads is False

    def test_create_with_function_analysis(self):
        """Test creating executor with function analysis."""

        def io_func():
            with open("test.txt", "w") as f:
                f.write("test")

        executor = create_local_executor(func=io_func, args=(), kwargs={})
        assert executor.use_threads is True  # Should detect I/O and use threads

    def test_create_with_cpu_function(self):
        """Test creating executor for CPU-bound function."""
        executor = create_local_executor(
            func=cpu_bound_function, args=(1000,), kwargs={}
        )
        assert executor.use_threads is False  # Should use processes for CPU-bound

    def test_create_with_unpicklable_function(self):
        """Test creating executor for unpicklable function."""
        lambda_func = lambda x: x * 2

        executor = create_local_executor(func=lambda_func, args=(5,), kwargs={})
        assert executor.use_threads is True  # Should use threads for unpicklable


class TestLocalExecutorIntegration:
    """Integration tests for LocalExecutor."""

    def test_cpu_intensive_workload(self):
        """Test with CPU-intensive workload."""
        work_chunks = [
            {"args": (20,), "kwargs": {}},
            {"args": (21,), "kwargs": {}},
            {"args": (22,), "kwargs": {}},
        ]

        # Use processes for CPU-bound work
        with LocalExecutor(max_workers=2, use_threads=False) as executor:
            results = executor.execute_parallel(fibonacci, work_chunks)

        assert len(results) == 3
        assert results[0] == 6765  # fibonacci(20)
        assert results[1] == 10946  # fibonacci(21)
        assert results[2] == 17711  # fibonacci(22)

    def test_mixed_workload_types(self):
        """Test with mixed computation types."""

        def mixed_func(operation, value):
            if operation == "square":
                return value**2
            elif operation == "double":
                return value * 2
            elif operation == "negative":
                return -value
            else:
                return value

        work_chunks = [
            {"args": ("square", 5), "kwargs": {}},
            {"args": ("double", 7), "kwargs": {}},
            {"args": ("negative", 3), "kwargs": {}},
            {"args": ("unknown", 10), "kwargs": {}},
        ]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            results = executor.execute_parallel(mixed_func, work_chunks)

        assert results[0] == 25  # 5^2
        assert results[1] == 14  # 7*2
        assert results[2] == -3  # -3
        assert results[3] == 10  # unchanged

    def test_error_recovery(self):
        """Test error handling and recovery."""

        def potentially_failing_func(x):
            if x == 0:
                raise ZeroDivisionError("Cannot divide by zero")
            return 10 / x

        # Test with all successful cases
        work_chunks = [
            {"args": (1,), "kwargs": {}},
            {"args": (2,), "kwargs": {}},
            {"args": (5,), "kwargs": {}},
        ]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            results = executor.execute_parallel(potentially_failing_func, work_chunks)

        assert results[0] == 10.0  # 10/1
        assert results[1] == 5.0  # 10/2
        assert results[2] == 2.0  # 10/5

        # Test with failure case
        failing_chunks = [
            {"args": (1,), "kwargs": {}},
            {"args": (0,), "kwargs": {}},  # This will fail
            {"args": (2,), "kwargs": {}},
        ]

        with LocalExecutor(max_workers=2, use_threads=True) as executor:
            with pytest.raises(ZeroDivisionError):
                executor.execute_parallel(potentially_failing_func, failing_chunks)

    def test_large_scale_parallel_execution(self):
        """Test large-scale parallel execution."""

        def simple_computation(x):
            # Simple computation that can be easily verified
            return x * x + 2 * x + 1  # (x+1)^2

        # Create 50 work items
        work_chunks = [{"args": (i,), "kwargs": {}} for i in range(50)]

        with LocalExecutor(max_workers=4, use_threads=True) as executor:
            results = executor.execute_parallel(simple_computation, work_chunks)

        assert len(results) == 50
        for i, result in enumerate(results):
            expected = (i + 1) ** 2
            assert (
                result == expected
            ), f"Mismatch at index {i}: expected {expected}, got {result}"
