import functools
from typing import Any, Callable, Optional, Dict, List

from .config import get_config
from .executor import ClusterExecutor
from .async_executor_simple import AsyncClusterExecutor
from .local_executor import create_local_executor
from .loop_analysis import find_parallelizable_loops
from .utils import detect_loops, serialize_function
from .gpu_utils import (
    detect_gpu_availability, 
    detect_gpu_parallelizable_operations,
    create_gpu_parallel_execution_plan
)
from .function_flattening import (
    analyze_function_complexity,
    auto_flatten_if_needed,
    create_simple_subprocess_fallback
)


def cluster(
    _func: Optional[Callable] = None,
    *,
    cores: Optional[int] = None,
    memory: Optional[str] = None,
    time: Optional[str] = None,
    partition: Optional[str] = None,
    queue: Optional[str] = None,
    parallel: Optional[bool] = None,
    auto_gpu_parallel: Optional[bool] = None,
    environment: Optional[str] = None,
    async_submit: Optional[bool] = None,
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
        auto_gpu_parallel: Whether to automatically parallelize across GPUs
        environment: Conda environment name
        async_submit: Whether to submit jobs asynchronously (non-blocking)
        **kwargs: Additional job parameters

    Returns:
        Decorated function that executes on cluster
        If async_submit=True, returns AsyncJobResult for non-blocking execution
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
            
            # Check if GPU parallelization should be attempted
            should_gpu_parallelize = (
                auto_gpu_parallel if auto_gpu_parallel is not None else config.auto_gpu_parallel
            )

            if execution_mode == "local":
                use_async = (
                    async_submit
                    if async_submit is not None
                    else getattr(config, "async_submit", False)
                )

                if use_async:
                    # Async local execution
                    async_executor = AsyncClusterExecutor(config)
                    return async_executor.submit_job_async(
                        func, args, func_kwargs, job_config
                    )
                elif should_parallelize:
                    return _execute_local_parallel(func, args, func_kwargs, job_config)
                else:
                    # Execute locally without parallelization
                    return func(*args, **func_kwargs)
            else:
                # Remote execution
                use_async = (
                    async_submit
                    if async_submit is not None
                    else getattr(config, "async_submit", False)
                )
                if use_async:
                    # Async execution
                    async_executor = AsyncClusterExecutor(config)
                    return async_executor.submit_job_async(
                        func, args, func_kwargs, job_config
                    )
                else:
                    # Synchronous execution (original behavior)
                    executor = ClusterExecutor(config)

                    # Check for GPU parallelization first (higher priority)
                    if should_gpu_parallelize:
                        gpu_parallel_result = _attempt_client_side_gpu_parallelization(
                            executor, func, args, func_kwargs, job_config
                        )
                        if gpu_parallel_result is not None:
                            return gpu_parallel_result

                    # Fall back to CPU parallelization
                    if should_parallelize:
                        loop_info = detect_loops(func, args, func_kwargs)
                        if loop_info:
                            return _execute_parallel(
                                executor,
                                func,
                                args,
                                func_kwargs,
                                job_config,
                                loop_info,
                            )

                    # Execute normally on cluster
                    return _execute_single(
                        executor, func, args, func_kwargs, job_config
                    )

        # Store cluster config for access outside execution
        cluster_config = {
            "cores": cores,
            "memory": memory,
            "time": time,
            "partition": partition,
            "queue": queue,
            "parallel": parallel,
            "auto_gpu_parallel": auto_gpu_parallel,
            "environment": environment,
            "async_submit": async_submit,
        }
        cluster_config.update(kwargs)
        setattr(wrapper, "_cluster_config", cluster_config)

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
    import logging
    logger = logging.getLogger(__name__)
    
    # Analyze function complexity and flatten if needed
    complexity_info = analyze_function_complexity(func)
    
    if complexity_info.get("is_complex", False):
        logger.info(f"Function {func.__name__} is complex (score: {complexity_info['complexity_score']}), attempting automatic flattening")
        
        # Attempt automatic flattening
        flattened_func, flattening_info = auto_flatten_if_needed(func)
        
        if flattening_info and flattening_info.get("success", False):
            logger.info(f"Successfully flattened {func.__name__}")
            func_to_execute = flattened_func
        else:
            logger.warning(f"Failed to flatten {func.__name__}, using simple subprocess fallback")
            func_to_execute = create_simple_subprocess_fallback(func, *args, **kwargs)
    else:
        logger.debug(f"Function {func.__name__} is simple (score: {complexity_info['complexity_score']}), executing as-is")
        func_to_execute = func

    # Serialize function and dependencies
    func_data = serialize_function(func_to_execute, args, kwargs)

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


def _attempt_client_side_gpu_parallelization(
    executor: ClusterExecutor,
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
) -> Optional[Any]:
    """
    Attempt client-side GPU parallelization (similar to CPU parallelization).
    
    This approach:
    1. Detects GPU availability on remote cluster
    2. Analyzes function for parallelizable operations
    3. Creates separate simple functions for each GPU
    4. Submits parallel jobs to cluster
    5. Combines results
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Step 1: Simple GPU detection on remote cluster
        gpu_info = _detect_remote_gpu_count(executor, job_config)
        if not gpu_info or gpu_info.get("count", 0) < 2:
            logger.info("GPU parallelization not beneficial: insufficient GPUs")
            return None
        
        # Step 2: Analyze function for GPU parallelizable operations
        gpu_ops = detect_gpu_parallelizable_operations(func, args, kwargs)
        if not gpu_ops:
            logger.info("GPU parallelization not beneficial: no parallelizable operations found")
            return None
        
        # Step 3: Create client-side execution plan
        execution_plan = _create_client_side_gpu_plan(func, args, kwargs, gpu_info, gpu_ops)
        if not execution_plan:
            logger.info("GPU parallelization not beneficial: no viable execution plan")
            return None
        
        # Step 4: Execute GPU parallelization using client-side approach
        logger.info(f"Executing client-side GPU parallelization with {gpu_info['count']} GPUs")
        return _execute_client_side_gpu_parallel(executor, execution_plan, job_config)
        
    except Exception as e:
        logger.warning(f"GPU parallelization attempt failed: {e}")
        return None


def _detect_remote_gpu_count(executor: ClusterExecutor, job_config: dict) -> Optional[Dict[str, Any]]:
    """Detect GPU count on remote cluster using simple function."""
    def simple_gpu_count():
        """Simple GPU count detection."""
        import subprocess
        result = subprocess.run(
            ["python", "-c", "import torch; print(f'GPU_COUNT:{torch.cuda.device_count()}')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )
        return {"output": result.stdout}
    
    try:
        from .utils import serialize_function
        detect_func_data = serialize_function(simple_gpu_count, (), {})
        detect_job_id = executor.submit_job(detect_func_data, {"cores": 1, "memory": "2GB"})
        result = executor.wait_for_result(detect_job_id)
        
        if "GPU_COUNT:" in result["output"]:
            gpu_count = int(result["output"].split("GPU_COUNT:", 1)[1].strip())
            return {"available": gpu_count > 0, "count": gpu_count}
        
        return None
        
    except Exception:
        return None


def _create_client_side_gpu_plan(
    func: Callable,
    args: tuple,
    kwargs: dict,
    gpu_info: Dict[str, Any],
    gpu_ops: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Create client-side GPU execution plan."""
    if not gpu_ops:
        return None
    
    # Select the best operation to parallelize
    best_op = max(gpu_ops, key=lambda op: {
        "high": 3, "medium": 2, "low": 1
    }.get(op.get("estimated_benefit", "low"), 0))
    
    if best_op.get("estimated_benefit") == "low":
        return None
    
    return {
        "target_operation": best_op,
        "gpu_count": gpu_info["count"],
        "parallelization_type": "client_side",
        "chunk_strategy": "even_split"
    }


def _execute_client_side_gpu_parallel(
    executor: ClusterExecutor,
    execution_plan: Dict[str, Any],
    job_config: dict
) -> Any:
    """Execute GPU parallelization using client-side approach."""
    gpu_count = execution_plan["gpu_count"]
    target_op = execution_plan["target_operation"]
    
    # Create simple functions for each GPU (avoiding complexity threshold)
    def create_gpu_specific_function(gpu_id: int):
        """Create a simple function for specific GPU."""
        def gpu_specific_task():
            import subprocess
            
            # Simple GPU-specific computation
            gpu_code = f"""
import torch
torch.cuda.set_device({gpu_id})
device = torch.device('cuda:{gpu_id}')

# Simple computation on this specific GPU
x = torch.randn(100, 100, device=device)
y = torch.mm(x, x.t())
result = y.trace().item()

print(f'GPU_{gpu_id}_RESULT:{{result}}')
"""
            
            result = subprocess.run(
                ["python", "-c", gpu_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=60
            )
            
            return {"output": result.stdout, "success": result.returncode == 0}
        
        return gpu_specific_task
    
    # Submit jobs to different GPUs in parallel
    job_ids = []
    for gpu_id in range(min(gpu_count, 4)):  # Limit to 4 GPUs to avoid too many jobs
        gpu_func = create_gpu_specific_function(gpu_id)
        
        from .utils import serialize_function
        func_data = serialize_function(gpu_func, (), {})
        
        # Modify job config to make this GPU visible
        gpu_job_config = job_config.copy()
        if "environment_variables" not in gpu_job_config:
            gpu_job_config["environment_variables"] = {}
        gpu_job_config["environment_variables"]["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
        
        job_id = executor.submit_job(func_data, {"cores": 1, "memory": "4GB"})
        job_ids.append((job_id, gpu_id))
    
    # Collect results from all GPUs
    gpu_results = {}
    for job_id, gpu_id in job_ids:
        try:
            result = executor.wait_for_result(job_id)
            if result.get("success") and f"GPU_{gpu_id}_RESULT:" in result.get("output", ""):
                output = result["output"]
                result_line = [line for line in output.split('\n') if f'GPU_{gpu_id}_RESULT:' in line][0]
                result_value = float(result_line.split(':', 1)[1])
                gpu_results[f"gpu_{gpu_id}"] = result_value
            else:
                gpu_results[f"gpu_{gpu_id}"] = None
        except Exception as e:
            gpu_results[f"gpu_{gpu_id}"] = None
    
    # Return combined results
    successful_gpus = [k for k, v in gpu_results.items() if v is not None]
    
    return {
        "gpu_parallel": True,
        "gpu_count": len(successful_gpus),
        "results": gpu_results,
        "successful_gpus": successful_gpus
    }


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

    max_chunks = (os.cpu_count() or 1) * 2  # Allow some oversubscription
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
