import functools
import inspect
import pickle
import asyncio
from typing import Any, Callable, Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import get_config
from .executor import ClusterExecutor
from .utils import detect_loops, setup_environment, serialize_function


def cluster(
    cores: Optional[int] = None,
    memory: Optional[str] = None,
    time: Optional[str] = None,
    partition: Optional[str] = None,
    queue: Optional[str] = None,
    parallel: Optional[bool] = None,
    environment: Optional[str] = None,
    **kwargs
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
        def wrapper(*args, **kwargs):
            config = get_config()
            
            # Use provided parameters or fall back to config defaults
            job_config = {
                'cores': cores or config.default_cores,
                'memory': memory or config.default_memory,
                'time': time or config.default_time,
                'partition': partition or config.default_partition,
                'queue': queue or config.default_queue,
                'environment': environment or config.conda_env_name,
            }
            job_config.update(kwargs)
            
            # Create executor
            executor = ClusterExecutor(config)
            
            # Check if function contains loops that can be parallelized
            should_parallelize = parallel if parallel is not None else config.auto_parallel
            
            if should_parallelize:
                loop_info = detect_loops(func, args, kwargs)
                if loop_info:
                    return _execute_parallel(executor, func, args, kwargs, job_config, loop_info)
            
            # Execute normally on cluster
            return _execute_single(executor, func, args, kwargs, job_config)
        
        return wrapper
    return decorator


def _execute_single(executor: ClusterExecutor, func: Callable, args: tuple, kwargs: dict, job_config: dict) -> Any:
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
    loop_info: Dict[str, Any]
) -> Any:
    """Execute function with parallelized loops."""
    
    config = get_config()
    
    # Split work based on loop information
    work_chunks = _create_work_chunks(func, args, kwargs, loop_info, config.max_parallel_jobs)
    
    # Submit parallel jobs
    job_ids = []
    for chunk in work_chunks:
        func_data = serialize_function(func, chunk['args'], chunk['kwargs'])
        job_id = executor.submit_job(func_data, job_config)
        job_ids.append((job_id, chunk))
    
    # Collect results
    results = []
    for job_id, chunk in job_ids:
        result = executor.wait_for_result(job_id)
        results.append((chunk['index'], result))
    
    # Combine results
    return _combine_results(results, loop_info)


def _create_work_chunks(func: Callable, args: tuple, kwargs: dict, loop_info: Dict, max_jobs: int) -> List[Dict]:
    """Create chunks of work for parallel execution."""
    
    # This is a simplified implementation
    # In practice, you'd need sophisticated analysis of the function
    # to determine how to split loops and iterations
    
    chunks = []
    loop_var = loop_info.get('variable')
    loop_range = loop_info.get('range', range(10))  # Default range
    
    chunk_size = max(1, len(loop_range) // max_jobs)
    
    for i in range(0, len(loop_range), chunk_size):
        chunk_range = loop_range[i:i + chunk_size]
        
        # Create modified kwargs for this chunk
        chunk_kwargs = kwargs.copy()
        chunk_kwargs[f'_chunk_range_{loop_var}'] = chunk_range
        chunk_kwargs['_chunk_index'] = i // chunk_size
        
        chunks.append({
            'args': args,
            'kwargs': chunk_kwargs,
            'index': i // chunk_size,
            'range': chunk_range
        })
    
    return chunks


def _combine_results(results: List[tuple], loop_info: Dict) -> Any:
    """Combine results from parallel execution."""
    
    # Sort by index
    results.sort(key=lambda x: x[0])
    
    # For now, just return the list of results
    # In practice, you'd need to intelligently combine based on the original function
    return [result[1] for result in results]