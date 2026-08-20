import functools
import inspect
import logging
import os
import threading
from dataclasses import fields
from typing import Any, Callable, NamedTuple, Optional, Dict, List

from .config import ClusterConfig, get_config
from .executor import ClusterExecutor
from .async_executor_simple import AsyncClusterExecutor
from .local_executor import create_local_executor
from .loop_analysis import find_parallelizable_loops
from .utils import detect_loops, serialize_function

logger = logging.getLogger(__name__)

#: Cluster types that submit work over an API instead of SSH, and therefore
#: never have a ``cluster_host``.
HOSTLESS_CLUSTER_TYPES = frozenset({"huggingface"})

#: ``ClusterConfig.default_cores`` as shipped. A value the user never touched
#: is a resource default rather than an instruction, so it is not reported when
#: a local route cannot use it; a value they set is (#152). Read off the
#: dataclass so the two cannot drift apart.
SHIPPED_DEFAULT_CORES = next(
    f.default for f in fields(ClusterConfig) if f.name == "default_cores"
)


class _CoreRequest(NamedTuple):
    """A worker count the caller asked for, and where they wrote it."""

    value: int
    where: str


def cluster(
    _func: Optional[Callable] = None,
    *,
    cores: Optional[int] = None,
    memory: Optional[str] = None,
    time: Optional[str] = None,
    partition: Optional[str] = None,
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
        parallel: Whether to parallelize loops automatically
        auto_gpu_parallel: NO EFFECT. The client-side GPU path it selected
            never called the decorated function -- it ran a fixed torch
            program per GPU and returned the traces of random matrices as
            the result -- so it was deleted. Passing this warns.
            Parallelize across GPUs inside your own function instead.
        environment: Name of a conda environment that already exists on
            the cluster. The function is executed in it -- it replaces
            the *execution* environment clustrix would otherwise
            replicate, and takes precedence over that replication;
            clustrix's own serialization environment is unaffected.
            Falls back to ``config.conda_env_name``.
        async_submit: Whether to submit jobs asynchronously (non-blocking)
        **kwargs: Additional job parameters

    Returns:
        Decorated function that executes on cluster
        If async_submit=True, returns AsyncJobResult for non-blocking execution
    """

    # A core count that is not a positive integer is a caller error, and it
    # used to be absorbed rather than reported: ``cores=0`` fell through
    # ``cores or config.default_cores`` and silently became the default, while
    # ``cores=-2`` reached ``ProcessPoolExecutor``, whose "max_workers must be
    # greater than 0" was swallowed by the sequential fallback -- so the job
    # ran on one core, and not even the cores warning fired (#152).
    if cores is not None and (not isinstance(cores, int) or cores < 1):
        raise ValueError(
            f"@cluster(cores={cores!r}) is not a usable worker count: cores "
            "must be a positive integer."
        )

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
                # The per-call value only. `resolve_named_environment` falls
                # back to `config.conda_env_name` itself, and folding the
                # fallback in here erased the difference between "the caller
                # asked for this environment" and "an old configuration file
                # still names it" -- which is exactly the difference the
                # migration notice for that field is about (#164).
                "environment": environment,
            }

            # Per-job overrides the backends read off job_config. hf_jobs.py
            # already reads hf_flavor/hf_timeout, they were simply never put
            # there, so the documented @cluster(hf_flavor=...) was dropped.
            passthrough_params = [
                "hf_token",
                "hf_username",
                "hf_flavor",
                "hf_timeout",
                "hf_namespace",
                "key_file",
            ]

            for param in passthrough_params:
                if param in kwargs:
                    job_config[param] = kwargs[param]

            # A silently ignored option is worse than a rejected one: the job
            # runs with settings the caller believes they changed.
            unknown_kwargs = sorted(set(kwargs) - set(passthrough_params))
            if unknown_kwargs:
                logger.warning(
                    "@cluster received unrecognised option(s) %s; they have no "
                    "effect. Recognised extras: %s",
                    ", ".join(unknown_kwargs),
                    ", ".join(sorted(passthrough_params)),
                )

            # #158: ``default_queue`` outlived its only consumers. ``queue``
            # was the PBS and SGE spelling of what SLURM calls a partition, and
            # both of those backends are gone, so nothing on the execution path
            # has read it since. ``@cluster(queue=...)`` is no longer a
            # parameter at all -- it now lands in ``**kwargs`` and is reported
            # by the warning above -- but a value left behind in a config file
            # or a saved widget profile still has to say that it does nothing.
            stale_queue = getattr(config, "default_queue", None)
            if stale_queue:
                logger.warning(
                    "ClusterConfig.default_queue=%r has no effect: no backend "
                    "reads it. SLURM takes a partition, so set default_partition "
                    "or @cluster(partition=...) instead.",
                    stale_queue,
                )

            # Determine execution mode
            execution_mode = _choose_execution_mode(config, func, args, func_kwargs)

            # Check if function contains loops that can be parallelized
            should_parallelize = (
                parallel if parallel is not None else config.auto_parallel
            )

            use_async = (
                async_submit
                if async_submit is not None
                else getattr(config, "async_submit", False)
            )

            # #152: ``cores`` asks for N workers. Three routes run the function
            # exactly once on this machine, where there is no second unit of
            # work to give a second worker, and the number was simply dropped:
            # the plain local path, the async local path (one job on a thread),
            # and ``cluster_type="local"``, which reaches LocalJobManager.
            requested = _requested_cores(cores, config)
            if execution_mode == "local" and (use_async or not should_parallelize):
                _warn_cores_unused(
                    requested,
                    "the local backend runs the decorated function once, in "
                    "this process" + (", on a worker thread" if use_async else ""),
                )
            elif execution_mode == "remote" and config.cluster_type == "local":
                _warn_cores_unused(
                    requested,
                    'cluster_type "local" runs the submitted function here '
                    "as a single unit of work",
                )

            # ``auto_gpu_parallel`` no longer does anything. The path it
            # switched on returned the traces of random matrices instead of
            # calling the function at all (see the module docstring of
            # tests/unit/test_decorator_no_gpu_fabrication.py), so it was
            # deleted. The parameter is still accepted -- removing it would
            # break every existing @cluster(auto_gpu_parallel=...) call site --
            # but a silently ignored option is worse than a rejected one, so
            # say so when someone sets it deliberately.
            if auto_gpu_parallel is not None:
                logger.warning(
                    "@cluster(auto_gpu_parallel=%r) has no effect: automatic "
                    "GPU parallelization was removed because it never ran the "
                    "decorated function. Parallelize across GPUs inside your "
                    "function instead.",
                    auto_gpu_parallel,
                )

            if execution_mode == "local":
                if use_async:
                    # Async local execution
                    async_executor = _shared_async_executor(config)
                    return async_executor.submit_job_async(
                        func, args, func_kwargs, job_config
                    )
                elif should_parallelize:
                    return _execute_local_parallel(
                        func, args, func_kwargs, job_config, requested=requested
                    )
                else:
                    # Execute locally without parallelization
                    return func(*args, **func_kwargs)
            else:
                # Remote execution
                if use_async:
                    # Async execution
                    async_executor = _shared_async_executor(config)

                    return async_executor.submit_job_async(
                        func, args, func_kwargs, job_config
                    )
                else:
                    # Synchronous execution (original behavior)
                    executor = ClusterExecutor(config)

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


#: One async executor per process, not one per submission.
#:
#: Each SimpleAsyncClusterExecutor owns a four-worker ThreadPoolExecutor and
#: nothing ever called its shutdown(), so building one per @cluster call leaked
#: four threads every time an async job was submitted. The pool cannot be closed
#: at the end of the call -- the submitted work outlives it, which is the point
#: of async submission -- so the lifetime is the process instead, and one pool
#: is shared. Threads are reused across submissions rather than accumulating.
_ASYNC_EXECUTOR_LOCK = threading.Lock()
_ASYNC_EXECUTOR: Optional[Any] = None


def _shared_async_executor(config):
    """Return the process-wide async executor, creating it on first use."""
    global _ASYNC_EXECUTOR
    with _ASYNC_EXECUTOR_LOCK:
        if _ASYNC_EXECUTOR is None:
            _ASYNC_EXECUTOR = AsyncClusterExecutor(config)
        return _ASYNC_EXECUTOR


def _execute_single(
    executor: ClusterExecutor,
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
) -> Any:
    """Execute function once on cluster.

    The function the caller wrote is the function that gets serialized. Nothing
    is substituted for it, ever.

    This used to run the function through ``analyze_function_complexity`` and,
    when that reported "complex", swap in either a source-rewritten
    "flattened" replacement or ``create_simple_subprocess_fallback``. Both
    substitutions could silently return something that was not the user's
    answer -- the fallback ran a hardcoded script whose whole body was
    ``result = "Function execution completed"``, so clustrix handed that string
    back as the result of the user's job with no error anywhere. Worse, the
    complexity analyser reported ``is_complex: True`` from its except branch
    whenever ``inspect.getsource`` failed, so the substitution fired precisely
    for the functions (REPL, notebook, ``exec``-created) whose source no
    rewriter could ever read.

    Rewriting a function to preserve its meaning cannot be verified without
    running it, so no rewrite can be trusted here. It is also unnecessary:
    ``serialize_function`` pickles by value via ``dill(recurse=True)`` /
    cloudpickle, which round-trips nested functions, closures, module-level
    globals and source-less ``exec``-created functions correctly. See
    ``tests/unit/test_execute_single_no_fabrication.py``.

    The rewriting machinery named above has since been deleted outright
    (issues #89 and #90): neither generator ever emitted code that ran, and
    ``serialize_function`` already covers every case they were meant to
    rescue, so there was nothing left to keep.
    """
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

    if not work_chunks:
        # Nothing was split, so there is nothing to combine. Falling through
        # would hand _combine_results an empty list and return [] -- a
        # fabricated answer. Run the function itself instead.
        return _execute_single(executor, func, args, kwargs, job_config)

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


def _accepts_chunk_kwargs(func: Callable, names: List[str]) -> bool:
    """Report whether ``func`` can receive every keyword in ``names``.

    Work chunks are handed to the callee as keyword arguments. A function that
    declares none of them -- and does not collect ``**kwargs`` -- cannot
    receive them, so parallelizing would raise
    ``TypeError: f() got an unexpected keyword argument '...'`` on every chunk.
    The callers decline instead and let the function run whole, which is the
    correct answer.
    """
    params = inspect.signature(func).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return all(name in params for name in names)


def _create_work_chunks(
    func: Callable, args: tuple, kwargs: dict, loop_info: Dict, max_jobs: int
) -> List[Dict]:
    """Create chunks of work for parallel execution."""

    # This is a simplified implementation
    # In practice, you'd need sophisticated analysis of the function
    # to determine how to split loops and iterations

    chunks = []
    loop_var = loop_info.get("variable")
    # No default. Guessing the range is how a loop over range(n) came to be
    # split into ten chunks, returning a tenth of the work with no error.
    # detect_loops now declines rather than inventing one, so an absent range
    # means "not parallelizable" and must be treated as such here too.
    loop_range = loop_info.get("range")
    if loop_range is None:
        logger.info(
            "Not parallelizing %s: the loop's range could not be determined.",
            getattr(func, "__name__", repr(func)),
        )
        return []

    chunk_kwarg_names = [f"_chunk_range_{loop_var}", "_chunk_index"]
    if not _accepts_chunk_kwargs(func, chunk_kwarg_names):
        logger.info(
            "Not parallelizing %s on the cluster: it takes no %s parameter(s).",
            getattr(func, "__name__", repr(func)),
            ", ".join(repr(name) for name in chunk_kwarg_names),
        )
        return []

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
    # Some backends reach their compute over an HTTP API rather than SSH, so
    # they legitimately have no cluster_host. Without this they fall into the
    # "no cluster configured" branch below and run on the caller's machine --
    # silently, while reporting success, which is the worst possible outcome
    # for someone who asked for remote execution.
    if config.cluster_type in HOSTLESS_CLUSTER_TYPES:
        return "remote"

    # If no cluster is configured, use local execution
    if not config.cluster_host:
        return "local"

    # Check if there's a preference for local parallel execution
    if hasattr(config, "prefer_local_parallel") and config.prefer_local_parallel:
        return "local"

    # Default to remote execution when cluster is available
    return "remote"


def _requested_cores(cores: Optional[int], config) -> Optional[_CoreRequest]:
    """The worker count the caller asked for, and where they asked for it.

    Two places count as asking. ``@cluster(cores=N)`` is the obvious one. A
    ``default_cores`` the user set with ``configure()`` is the other: it was
    previously treated as never worth reporting, on the grounds that the
    shipped default of 4 would then fire a warning on every local call. That
    reasoning holds for the shipped value and only for it -- someone who wrote
    ``configure(default_cores=8)`` and got one core in silence has exactly the
    complaint #152 is about. So the shipped value is compared against, not
    assumed: only a changed one is an instruction.
    """
    if cores is not None:
        return _CoreRequest(cores, f"@cluster(cores={cores})")
    default = getattr(config, "default_cores", None)
    if default is not None and default != SHIPPED_DEFAULT_CORES:
        return _CoreRequest(default, f"configure(default_cores={default})")
    return None


def _warn_cores_unused(request: Optional[_CoreRequest], because: str) -> None:
    """Say out loud that a requested worker count is being discarded.

    ``@cluster(cores=8)`` reads as "use eight workers". On every local route
    that runs the function once there is nothing to hand a second worker, and
    the number used to be dropped in silence -- the shape of defect this
    project keeps finding (#152). The caller gets one message naming the
    single condition under which ``cores`` does change local behaviour.

    A request of 1 is not a request for a second worker, so nothing is said
    about it: one worker is what every one of these routes already provides.
    """
    if request is None or request.value <= 1:
        return
    logger.warning(
        "%s has no effect here: %s. Locally, cores bounds the worker pool only "
        "when parallel=True finds a parallelizable loop and the function "
        "accepts the matching _parallel_<var> keyword -- and even there it is "
        "an upper bound, not a promise that many workers will be busy.",
        request.where,
        because,
    )


def _execute_local_parallel(
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
    requested: Optional[_CoreRequest] = None,
) -> Any:
    """
    Execute function locally with parallelization.

    This is the one local route where ``cores`` changes what happens: it sizes
    the worker pool and, through it, the number of work chunks. What it does
    *not* do is create parallelism on its own. The pool is a bound -- the work
    is a queue, and a chunk that costs microseconds can be pulled by the first
    worker to reach it before its siblings have finished starting, so a run
    with ``cores=8`` may still be observed doing its work in fewer than eight
    processes. Wider is available; wider is not guaranteed.

    Args:
        func: Function to execute
        args: Function arguments
        kwargs: Function keyword arguments
        job_config: Job configuration
        requested: What the caller asked for and where, or ``None`` if they
            asked for nothing. ``job_config["cores"]`` cannot answer that -- it
            has already been merged with ``config.default_cores`` -- and every
            route out of this function that declines to split the work
            discards the request (#152).

    Returns:
        Function result
    """
    name = getattr(func, "__name__", repr(func))

    # Find parallelizable loops
    parallelizable_loops = find_parallelizable_loops(func, args, kwargs)

    if not parallelizable_loops:
        # No parallelizable loops found, execute normally
        _warn_cores_unused(requested, f"no parallelizable loop was found in {name}")
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
            work_chunks = _create_local_work_chunks(
                func, args, kwargs, loop_info, local_executor.max_workers
            )

            if not work_chunks:
                # Fallback to normal execution
                _warn_cores_unused(
                    requested, f"the work in {name} was not split into chunks"
                )
                return func(*args, **kwargs)

            # Execute in parallel
            results = local_executor.execute_parallel(func, work_chunks)

            # Combine results
            return _combine_local_results(results, loop_info)

    except TypeError:
        # The callee could not receive the chunk it was handed. Work chunks are
        # only built for functions whose signature accepts them, so reaching
        # here means clustrix built a call the function cannot answer -- a bug
        # in this module, not a runtime condition. Absorbing it is what let
        # local parallelization silently never happen; let it surface.
        raise
    except Exception as e:
        # Fallback to normal execution on error
        logger.warning(
            f"Local parallel execution failed, falling back to sequential: {e}"
        )
        _warn_cores_unused(
            requested, f"parallel execution of {name} fell back to sequential"
        )
        return func(*args, **kwargs)


def _create_local_work_chunks(
    func: Callable,
    args: tuple,
    kwargs: dict,
    loop_info,
    max_workers: Optional[int] = None,
) -> List[Dict]:
    """
    Create work chunks for local parallel execution.

    Args:
        func: Function to execute
        args: Function arguments
        kwargs: Function keyword arguments
        loop_info: Information about the loop to parallelize
        max_workers: How many workers the pool will have. ``None`` means the
            caller does not know, and the machine's width is used instead.

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
        # Legacy format. As above: an unknown range is a refusal, not a ten.
        loop_range = loop_info.get("range")
        variable = loop_info.get("variable", "i")
        if loop_range is None:
            return []

    if not variable or len(loop_range) == 0:
        return []

    name = f"_parallel_{variable}"
    if not _accepts_chunk_kwargs(func, [name]):
        logger.info(
            "Not parallelizing %s locally: it takes no %r parameter.",
            getattr(func, "__name__", repr(func)),
            name,
        )
        return []

    # Aim for two chunks per worker: one each leaves nothing to pick up when
    # they finish at different times. The count follows the pool the caller
    # asked for, not the machine. Deriving it from ``os.cpu_count()`` capped
    # every run at the machine's width, so on a two-core box
    # ``@cluster(cores=16)`` produced four chunks and twelve of the sixteen
    # workers it sized had nothing they could ever pull (#152).
    workers = max_workers or os.cpu_count() or 1
    chunk_size = max(1, len(loop_range) // (workers * 2))

    # Create chunks
    for i in range(0, len(loop_range), chunk_size):
        chunk_range = list(loop_range[i : i + chunk_size])

        # Create modified kwargs for this chunk
        chunk_kwargs = kwargs.copy()
        chunk_kwargs[name] = chunk_range

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
