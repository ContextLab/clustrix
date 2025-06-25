import functools
import inspect
import pickle
import asyncio
from typing import Any, Callable, Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import get_config
from .executor import ClusterExecutor
from .local_executor import create_local_executor
from .loop_analysis import detect_loops_in_function, find_parallelizable_loops
from .utils import detect_loops, setup_environment, serialize_function


def cluster(
    _func: Optional[Callable] = None,
    *,
    cores: Optional[int] = None,
    memory: Optional[str] = None,
    time: Optional[str] = None,
    partition: Optional[str] = None,
    queue: Optional[str] = None,
    parallel: Optional[bool] = None,
    environment: Optional[str] = None,
    **kwargs,
):
    """
    Decorator to execute functions on a cluster.

    Args:
        cores: Number of CPU cores to request
        memory: Memory to request (e.g., "8GB")
        time: Time limit (e.g., "01:00:00")
        partition: Cluster partition to use
        queue: Queue to submit to
        parallel: Whether to parallelize loops automatically
        environment: Conda environment name
        **kwargs: Additional job parameters

    Returns:
        Decorated function that executes on cluster
    """

    def decorator(func: Callable) -> Callable:

        @functools.wraps(func)
        def wrapper(*args, **func_kwargs):
            config = get_config()

            # Use provided parameters or fall back to config defaults
            job_config = {
                "cores": cores or config.default_cores,
                "memory": memory or config.default_memory,
                "time": time or config.default_time,
                "partition": partition or config.default_partition,
                "queue": queue or config.default_queue,
                "environment": environment or config.conda_env_name,
            }

            # Determine execution mode
            execution_mode = _choose_execution_mode(config, func, args, func_kwargs)

            # Check if function contains loops that can be parallelized
            should_parallelize = (
                parallel if parallel is not None else config.auto_parallel
            )

            if execution_mode == "local":
                if should_parallelize:
                    return _execute_local_parallel(func, args, func_kwargs, job_config)
                else:
                    # Execute locally without parallelization
                    return func(*args, **func_kwargs)
            else:
                # Remote execution
                executor = ClusterExecutor(config)

                if should_parallelize:
                    loop_info = detect_loops(func, args, func_kwargs)
                    if loop_info:
                        return _execute_parallel(
                            executor, func, args, func_kwargs, job_config, loop_info
                        )

                # Execute normally on cluster
                return _execute_single(executor, func, args, func_kwargs, job_config)

        # Store cluster config for access outside execution
        wrapper._cluster_config = {
            "cores": cores,
            "memory": memory,
            "time": time,
            "partition": partition,
            "queue": queue,
            "parallel": parallel,
            "environment": environment,
        }
        wrapper._cluster_config.update(kwargs)

        return wrapper

    # Handle both @cluster and @cluster() usage
    if _func is None:
        # Called as @cluster() or @cluster(args...)
        return decorator
    else:
        # Called as @cluster (without parentheses)
        return decorator(_func)


def _execute_single(
    executor: ClusterExecutor,
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
) -> Any:
    """Execute function once on cluster."""

    # Serialize function and dependencies
    func_data = serialize_function(func, args, kwargs)

    # Submit job
    job_id = executor.submit_job(func_data, job_config)

    # Wait for completion and get result
    result = executor.wait_for_result(job_id)

    return result


def _execute_parallel(
    executor: ClusterExecutor,
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
    loop_info: Dict[str, Any],
) -> Any:
    """Execute function with parallelized loops."""

    config = get_config()

    # Split work based on loop information
    work_chunks = _create_work_chunks(
        func, args, kwargs, loop_info, config.max_parallel_jobs
    )

    # Submit parallel jobs
    job_ids = []
    for chunk in work_chunks:
        func_data = serialize_function(func, chunk["args"], chunk["kwargs"])
        job_id = executor.submit_job(func_data, job_config)
        job_ids.append((job_id, chunk))

    # Collect results
    results = []
    for job_id, chunk in job_ids:
        result = executor.wait_for_result(job_id)
        results.append((chunk["index"], result))

    # Combine results
    return _combine_results(results, loop_info)


def _create_work_chunks(
    func: Callable, args: tuple, kwargs: dict, loop_info: Dict, max_jobs: int
) -> List[Dict]:
    """Create chunks of work for parallel execution."""

    # This is a simplified implementation
    # In practice, you'd need sophisticated analysis of the function
    # to determine how to split loops and iterations

    chunks = []
    loop_var = loop_info.get("variable")
    loop_range = loop_info.get("range", range(10))  # Default range

    chunk_size = max(1, len(loop_range) // max_jobs)

    for i in range(0, len(loop_range), chunk_size):
        chunk_range = loop_range[i : i + chunk_size]

        # Create modified kwargs for this chunk
        chunk_kwargs = kwargs.copy()
        chunk_kwargs[f"_chunk_range_{loop_var}"] = chunk_range
        chunk_kwargs["_chunk_index"] = i // chunk_size

        chunks.append(
            {
                "args": args,
                "kwargs": chunk_kwargs,
                "index": i // chunk_size,
                "range": chunk_range,
            }
        )

    return chunks


def _combine_results(results: List[tuple], loop_info: Dict) -> Any:
    """Combine results from parallel execution."""

    # Sort by index
    results.sort(key=lambda x: x[0])

    # For now, just return the list of results
    # In practice, you'd need to intelligently combine based on the original function
    return [result[1] for result in results]


def _choose_execution_mode(config, func: Callable, args: tuple, kwargs: dict) -> str:
    """
    Choose between local and remote execution.

    Args:
        config: Cluster configuration
        func: Function to execute
        args: Function arguments
        kwargs: Function keyword arguments

    Returns:
        'local' or 'remote'
    """
    # If no cluster is configured, use local execution
    if not config.cluster_host:
        return "local"

    # Check if there's a preference for local parallel execution
    if hasattr(config, "prefer_local_parallel") and config.prefer_local_parallel:
        return "local"

    # Default to remote execution when cluster is available
    return "remote"


def _execute_local_parallel(
    func: Callable, args: tuple, kwargs: dict, job_config: dict
) -> Any:
    """
    Execute function locally with parallelization.

    Args:
        func: Function to execute
        args: Function arguments
        kwargs: Function keyword arguments
        job_config: Job configuration

    Returns:
        Function result
    """
    # Find parallelizable loops
    parallelizable_loops = find_parallelizable_loops(func, args, kwargs)

    if not parallelizable_loops:
        # No parallelizable loops found, execute normally
        return func(*args, **kwargs)

    # Use the first parallelizable loop
    loop_info = parallelizable_loops[0]

    # Create local executor
    max_workers = job_config.get("cores", 4)
    local_executor = create_local_executor(
        max_workers=max_workers, func=func, args=args, kwargs=kwargs
    )

    try:
        with local_executor:
            # Create work chunks for the loop
            work_chunks = _create_local_work_chunks(func, args, kwargs, loop_info)

            if not work_chunks:
                # Fallback to normal execution
                return func(*args, **kwargs)

            # Execute in parallel
            results = local_executor.execute_parallel(func, work_chunks)

            # Combine results
            return _combine_local_results(results, loop_info)

    except Exception as e:
        # Fallback to normal execution on error
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            f"Local parallel execution failed, falling back to sequential: {e}"
        )
        return func(*args, **kwargs)


def _create_local_work_chunks(
    func: Callable, args: tuple, kwargs: dict, loop_info
) -> List[Dict]:
    """
    Create work chunks for local parallel execution.

    Args:
        func: Function to execute
        args: Function arguments
        kwargs: Function keyword arguments
        loop_info: Information about the loop to parallelize

    Returns:
        List of work chunks
    """
    chunks = []

    # Get range information
    if hasattr(loop_info, "range_info") and loop_info.range_info:
        range_info = loop_info.range_info
        start = range_info["start"]
        stop = range_info["stop"]
        step = range_info["step"]

        # Create range object
        loop_range = range(start, stop, step)
        variable = loop_info.variable

    elif hasattr(loop_info, "to_dict"):
        # New loop info format
        loop_dict = loop_info.to_dict()
        range_info = loop_dict.get("range_info")
        if range_info:
            loop_range = range(
                range_info["start"], range_info["stop"], range_info["step"]
            )
            variable = loop_dict["variable"]
        else:
            return []  # Can't parallelize without range info
    else:
        # Legacy format
        loop_range = loop_info.get("range", range(10))
        variable = loop_info.get("variable", "i")

    if not variable or len(loop_range) == 0:
        return []

    # Determine chunk size (aim for reasonable number of chunks)
    import os

    max_chunks = os.cpu_count() * 2  # Allow some oversubscription
    chunk_size = max(1, len(loop_range) // max_chunks)

    # Create chunks
    for i in range(0, len(loop_range), chunk_size):
        chunk_range = list(loop_range[i : i + chunk_size])

        # Create modified kwargs for this chunk
        chunk_kwargs = kwargs.copy()
        chunk_kwargs[f"_parallel_{variable}"] = chunk_range

        chunks.append({"args": args, "kwargs": chunk_kwargs})

    return chunks


def _combine_local_results(results: List[Any], loop_info) -> Any:
    """
    Combine results from local parallel execution.

    Args:
        results: List of results from parallel execution
        loop_info: Information about the parallelized loop

    Returns:
        Combined result
    """
    # For now, flatten list results or return as-is
    if not results:
        return None

    if len(results) == 1:
        return results[0]

    # If all results are lists, concatenate them
    if all(isinstance(r, list) for r in results):
        combined = []
        for result in results:
            combined.extend(result)
        return combined

    # Otherwise return the list of results
    return results
