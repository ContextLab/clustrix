"""Local parallel execution using multiprocessing and threading."""

import os
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, List, Callable, Dict, Optional, Union
import logging
import functools
import pickle
import time

logger = logging.getLogger(__name__)


class LocalExecutor:
    """Execute functions locally using multiprocessing or threading."""

    def __init__(self, max_workers: Optional[int] = None, use_threads: bool = False):
        """
        Initialize local executor.

        Args:
            max_workers: Maximum number of worker processes/threads
            use_threads: If True, use ThreadPoolExecutor, else ProcessPoolExecutor
        """
        self.max_workers = max_workers or os.cpu_count()
        self.use_threads = use_threads
        self._executor = None

    def __enter__(self):
        """Context manager entry."""
        self._create_executor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self._cleanup_executor()

    def _create_executor(self):
        """Create the appropriate executor."""
        if self.use_threads:
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        else:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)

    def _cleanup_executor(self):
        """Clean up the executor."""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

    def execute_single(self, func: Callable, args: tuple, kwargs: dict) -> Any:
        """
        Execute a single function call locally.

        Args:
            func: Function to execute
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            Function result
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Local execution failed: {e}")
            raise

    def execute_parallel(
        self,
        func: Callable,
        work_chunks: List[Dict[str, Any]],
        timeout: Optional[float] = None,
    ) -> List[Any]:
        """
        Execute function in parallel with different work chunks.

        Args:
            func: Function to execute
            work_chunks: List of work chunks, each containing args and kwargs
            timeout: Timeout for each task in seconds

        Returns:
            List of results in order of work_chunks
        """
        if not work_chunks:
            return []

        if len(work_chunks) == 1:
            # Single chunk, execute directly
            chunk = work_chunks[0]
            return [
                self.execute_single(
                    func, chunk.get("args", ()), chunk.get("kwargs", {})
                )
            ]

        # Create executor if not in context manager
        cleanup_needed = self._executor is None
        if cleanup_needed:
            self._create_executor()

        try:
            return self._execute_parallel_chunks(func, work_chunks, timeout)
        finally:
            if cleanup_needed:
                self._cleanup_executor()

    def _execute_parallel_chunks(
        self,
        func: Callable,
        work_chunks: List[Dict[str, Any]],
        timeout: Optional[float],
    ) -> List[Any]:
        """Execute chunks in parallel using the executor."""
        futures = {}
        results = [None] * len(work_chunks)

        # Submit all tasks
        for i, chunk in enumerate(work_chunks):
            args = chunk.get("args", ())
            kwargs = chunk.get("kwargs", {})
            future = self._executor.submit(func, *args, **kwargs)
            futures[future] = i

        # Collect results with timeout
        try:
            if timeout:
                # Wait for all futures with a global timeout
                done_futures = set()
                try:
                    # Use wait with timeout to check for completion
                    from concurrent.futures import wait, FIRST_COMPLETED, ALL_COMPLETED
                    import time

                    done, not_done = wait(
                        futures.keys(), timeout=timeout, return_when=ALL_COMPLETED
                    )

                    if not_done:
                        # Some tasks didn't complete within timeout
                        for future in not_done:
                            future.cancel()
                        raise TimeoutError(
                            f"Execution exceeded timeout of {timeout} seconds"
                        )

                    # All tasks completed within timeout, collect results
                    for future in done:
                        index = futures[future]
                        results[index] = future.result()

                except TimeoutError:
                    # Re-raise timeout errors
                    raise
            else:
                # No timeout, wait for all
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as e:
                        logger.error(f"Task {index} failed: {e}")
                        raise

        except Exception as e:
            # Cancel remaining futures
            for future in futures:
                if not future.done():
                    future.cancel()
            raise

        return results

    def execute_loop_parallel(
        self,
        func: Callable,
        loop_var: str,
        iterable: Union[range, List, tuple],
        func_args: tuple = (),
        func_kwargs: dict = None,
        chunk_size: Optional[int] = None,
    ) -> List[Any]:
        """
        Execute a function in parallel over an iterable using intelligent chunking.

        This method implements a sophisticated chunk-based parallelization strategy that
        handles the complex challenge of distributing iterable items across multiple
        workers while preserving order and handling functions that expect individual
        items rather than chunks.

        **Algorithm:**

        1. **Chunking**: Splits the iterable into approximately equal chunks based on
           worker count and optional chunk_size parameter.

        2. **Chunk Processing**: Creates a wrapper function that processes each chunk
           by iterating over individual items and calling the original function.

        3. **Result Flattening**: Collects results from all chunks and flattens them
           while preserving the original iteration order.

        **Key Innovation**: The chunk processor wrapper bridges the gap between
        chunk-based parallel execution and functions that expect individual items.

        **Example Workflow**:
        ```
        range(5) with chunk_size=2 → chunks: [[0,1], [2,3], [4]]
        Each chunk processed: func(args, loop_var=0), func(args, loop_var=1), etc.
        Results flattened: [result0, result1, result2, result3, result4]
        ```

        Args:
            func: Function to execute for each item. The function will be called with
                  func_args + the item as the loop_var keyword argument.
            loop_var: Name of the keyword argument that will receive each item from
                      the iterable.
            iterable: Iterable to process in parallel (range, list, tuple, etc.).
            func_args: Additional positional arguments passed to func for each call.
            func_kwargs: Additional keyword arguments passed to func for each call.
                        Note: loop_var will be added/overridden in these kwargs.
            chunk_size: Optional size of each work chunk. If None, automatically
                       calculated as len(items) // max_workers.

        Returns:
            List[Any]: Results from function execution in the same order as the
                      original iterable. Each element corresponds to func() called
                      with the respective item from the iterable.

        Examples:
            >>> executor = LocalExecutor(max_workers=4)
            >>> def square(x):
            ...     return x ** 2
            >>>
            >>> # Parallel computation over range
            >>> results = executor.execute_loop_parallel(
            ...     square, 'x', range(10), chunk_size=3
            ... )
            >>> # Results: [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]

            >>> # With additional arguments
            >>> def power(base, x, exp=2):
            ...     return (base + x) ** exp
            >>> results = executor.execute_loop_parallel(
            ...     power, 'x', [1, 2, 3], func_args=(10,), func_kwargs={'exp': 3}
            ... )
            >>> # Results: [11^3, 12^3, 13^3] = [1331, 1728, 2197]

        Raises:
            Exception: Any exception raised by the function during execution.

        Note:
            - Empty iterables return empty lists immediately
            - Single-item iterables are executed directly (no parallelization overhead)
            - Chunk size is automatically bounded by the number of available workers
            - The method preserves the original order of results
        """
        if func_kwargs is None:
            func_kwargs = {}

        # Convert iterable to list if needed
        if isinstance(iterable, range):
            items = list(iterable)
        else:
            items = list(iterable)

        if not items:
            return []

        # Determine chunk size
        if chunk_size is None:
            chunk_size = max(1, len(items) // self.max_workers)

        # Create a wrapper function that processes chunks
        def chunk_processor(*args, **kwargs):
            # Extract the chunk of items from kwargs
            chunk_items = kwargs.pop(loop_var)
            chunk_results = []

            # Process each item in the chunk individually
            for item in chunk_items:
                item_kwargs = kwargs.copy()
                item_kwargs[loop_var] = item
                result = func(*args, **item_kwargs)
                chunk_results.append(result)

            return chunk_results

        # Create work chunks
        work_chunks = []
        for i in range(0, len(items), chunk_size):
            chunk_items = items[i : i + chunk_size]
            chunk_kwargs = func_kwargs.copy()
            chunk_kwargs[loop_var] = chunk_items

            work_chunks.append({"args": func_args, "kwargs": chunk_kwargs})

        # Execute in parallel using the chunk processor
        chunk_results = self.execute_parallel(chunk_processor, work_chunks)

        # Flatten results if needed
        results = []
        for chunk_result in chunk_results:
            if isinstance(chunk_result, list):
                results.extend(chunk_result)
            else:
                results.append(chunk_result)

        return results


def _safe_pickle_test(obj) -> bool:
    """Test if an object can be safely pickled."""
    try:
        pickle.dumps(obj)
        return True
    except (pickle.PicklingError, TypeError, AttributeError):
        return False


def choose_executor_type(func: Callable, args: tuple, kwargs: dict) -> bool:
    """
    Intelligently choose between ThreadPoolExecutor and ProcessPoolExecutor based on function characteristics.

    This function implements a sophisticated analysis to determine the optimal execution model
    for parallel function execution. The decision tree prioritizes pickling constraints first,
    then analyzes function characteristics to detect I/O-bound vs CPU-bound workloads.

    **Decision Logic (in priority order):**

    1. **Pickling Check (Highest Priority)**: Functions and arguments must be picklable for
       multiprocessing. If any component fails pickling, threads are used.

    2. **I/O Detection**: Source code analysis looks for common I/O patterns that benefit
       from threads due to GIL release during I/O operations.

    3. **Default**: CPU-bound tasks default to processes for true parallelism.

    **Key Insight**: `inspect.getsource(lambda x: x)` succeeds but `pickle.dumps(lambda x: x)`
    fails, so pickling must be checked before source analysis.

    Args:
        func: Function to analyze for execution characteristics
        args: Positional arguments that will be passed to the function
        kwargs: Keyword arguments that will be passed to the function

    Returns:
        bool: True to use ThreadPoolExecutor (for I/O-bound or unpicklable functions),
              False to use ProcessPoolExecutor (for CPU-bound, picklable functions)

    Examples:
        >>> # Lambda function (unpicklable) -> threads
        >>> choose_executor_type(lambda x: x*2, (5,), {})
        True

        >>> # I/O function -> threads
        >>> def io_func(filename):
        ...     with open(filename, 'r') as f:
        ...         return f.read()
        >>> choose_executor_type(io_func, ("file.txt",), {})
        True

        >>> # CPU function -> processes
        >>> def cpu_func(n):
        ...     return sum(i**2 for i in range(n))
        >>> choose_executor_type(cpu_func, (1000,), {})
        False

    Note:
        This function performs static analysis and may not catch all edge cases.
        The analysis is designed to be conservative - when in doubt, it defaults
        to the safer option for the detected pattern.
    """
    # First check if function can be pickled (most important check)
    if not _safe_pickle_test(func):
        return True  # Use threads for unpicklable functions

    # Check if any arguments can't be pickled
    for arg in args:
        if not _safe_pickle_test(arg):
            return True

    for value in kwargs.values():
        if not _safe_pickle_test(value):
            return True

    # Check for common I/O bound indicators
    import inspect

    try:
        source = inspect.getsource(func)
        io_indicators = [
            "open(",
            "requests.",
            "urllib.",
            "http.",
            "ftp.",
            "sql",
            "database",
            "time.sleep",
            "threading.",
        ]
        if any(indicator in source.lower() for indicator in io_indicators):
            return True
    except (OSError, TypeError):
        pass  # If we can't get source, no problem

    # Default to processes for CPU-bound tasks
    return False


def create_local_executor(
    max_workers: Optional[int] = None,
    use_threads: Optional[bool] = None,
    func: Optional[Callable] = None,
    args: tuple = (),
    kwargs: dict = None,
) -> LocalExecutor:
    """
    Create a LocalExecutor with appropriate settings.

    Args:
        max_workers: Maximum number of workers
        use_threads: Force thread or process usage
        func: Function to analyze for executor type selection
        args: Function arguments for analysis
        kwargs: Function keyword arguments for analysis

    Returns:
        Configured LocalExecutor
    """
    if kwargs is None:
        kwargs = {}

    # Auto-detect executor type if not specified
    if use_threads is None and func is not None:
        use_threads = choose_executor_type(func, args, kwargs)
    elif use_threads is None:
        use_threads = False  # Default to processes

    return LocalExecutor(max_workers=max_workers, use_threads=use_threads)
