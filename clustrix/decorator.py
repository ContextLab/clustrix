import functools
import logging
import threading
import weakref
from dataclasses import fields
from typing import Any, Callable, NamedTuple, Optional, Dict, List, Sequence

from .config import ClusterConfig, get_config
from .executor import ClusterExecutor
from .async_executor_simple import AsyncClusterExecutor
from . import parallelize
from .local_executor import is_worker_count
from .loop_workers import available_cpus, shared_pool, timed_call
from .utils import serialize_function

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

#: How many times one ``(where, because)`` reason may be spoken while
#: ``_warning_reaches_someone`` is unable to confirm that anybody heard it.
#: The gate answers "no" for a filter it refuses to run (see there), and a
#: reason it cannot confirm is therefore never marked delivered -- which
#: without a cap means one message per call, in exactly the tight local loop
#: the throttle exists to protect. Three is enough to be noticed in a scrolling
#: log and few enough not to become the noise it is warning about.
UNCONFIRMED_REPEAT_LIMIT = 3

#: Recorded against a reason whose message the gate could vouch for. Distinct
#: from any repeat count, which counts up from zero.
_HEARD = -1


class _CoreRequest(NamedTuple):
    """A worker count the caller asked for, and where they wrote it.

    ``reported`` records, per ``(where, because)`` pair this decorated
    function has complained about, how many times it has been said: ``_HEARD``
    once a listener was confirmed, otherwise the number of unconfirmed
    repeats so far. See ``_warn_cores_unused``.
    """

    value: int
    where: str
    reported: Dict[tuple, int]


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
    # ran on one core, and not even the cores warning fired (#152). ``True``
    # was a third silent absorption: ``bool`` subclasses ``int``, so it passed
    # the type check and became a request for one worker while ``False`` was
    # refused -- see ``is_worker_count``.
    if cores is not None and not is_worker_count(cores):
        detail = "cores must be a positive integer."
        if isinstance(cores, bool):
            detail = (
                "cores must be a positive integer, and a bool is not one -- "
                "whatever Python's type hierarchy says."
            )
        raise ValueError(
            f"@cluster(cores={cores!r}) is not a usable worker count: {detail}"
        )

    def decorator(func: Callable) -> Callable:

        # One record per decorated function of the (request, reason) pairs
        # already reported and how often, so a call in a loop does not repeat
        # itself. See ``_warn_cores_unused``.
        cores_reported: Dict[tuple, int] = {}

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
            requested = _requested_cores(cores, config, cores_reported)
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
                    return _run_split_locally(
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
                        loop_plan = _plan_for(func)
                        if loop_plan.parallelizable:
                            return _run_split_remotely(
                                loop_plan,
                                executor,
                                func,
                                args,
                                func_kwargs,
                                job_config,
                                config.max_parallel_jobs,
                            )
                        logger.info(
                            "%s runs as one job.\n%s",
                            getattr(func, "__name__", repr(func)),
                            loop_plan.explain(),
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


#: One plan per function. Planning reads and parses the function's source,
#: which does not change between calls; what the function *reads* is looked
#: up afresh by ``parallelize.bind`` each time.
_PLANS: "weakref.WeakKeyDictionary[Callable, parallelize.ParallelPlan]" = (
    weakref.WeakKeyDictionary()
)


def _plan_for(func: Callable) -> parallelize.ParallelPlan:
    """The split of ``func``'s loop into jobs, or the reasons there is none."""
    plan = _PLANS.get(func)
    if plan is None:
        plan = _PLANS[func] = parallelize.plan(func)
    return plan


def _run_split_remotely(
    plan: parallelize.ParallelPlan,
    executor: ClusterExecutor,
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
    max_jobs: int,
) -> Any:
    """Run ``func`` on the cluster with its loop divided among jobs.

    Each chunk of the loop is one job with the resources the caller asked
    for, so ``@cluster(cores=4, memory="8GB")`` describes *a* job, not the
    total. The code around the loop runs as a job of its own unless it only
    unpacks arguments or returns what was collected.
    """
    name = getattr(func, "__name__", repr(func))

    def invoke(function: Callable, *arguments: Any) -> Any:
        return _execute_single(executor, function, arguments, {}, job_config)

    def map_chunks(body: Callable, env: dict, chunks: List[Sequence[Any]]) -> list:
        logger.info(
            "%s: running its loop as %d job(s) of up to %d iteration(s).",
            name,
            len(chunks),
            max(len(chunk) for chunk in chunks),
        )
        job_ids = [
            executor.submit_job(serialize_function(body, (env, chunk), {}), job_config)
            for chunk in chunks
        ]
        results = []
        try:
            for job_id in job_ids:
                results.append(executor.wait_for_result(job_id))
        except BaseException:
            # One chunk failed, or the caller interrupted: the rest are
            # doing work whose result will never be used.
            for job_id in job_ids[len(results) + 1 :]:
                try:
                    executor.cancel_job(job_id)
                except Exception as error:  # noqa: BLE001 - best effort
                    logger.warning("Could not cancel job %s: %s", job_id, error)
            raise
        return results

    return parallelize.run(
        plan,
        func,
        args,
        kwargs,
        map_chunks,
        jobs=max(1, max_jobs),
        invoke=invoke,
        on_fallback=logger.warning,
    )


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


def _requested_cores(
    cores: Optional[int], config, reported: Dict[tuple, int]
) -> Optional[_CoreRequest]:
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
        return _CoreRequest(cores, f"@cluster(cores={cores})", reported)
    default = getattr(config, "default_cores", None)
    if default is not None and default != SHIPPED_DEFAULT_CORES:
        return _CoreRequest(default, f"configure(default_cores={default})", reported)
    return None


def _warning_reaches_someone() -> bool:
    """Whether a ``logger.warning`` issued right now would actually be emitted.

    ``isEnabledFor`` is only half the question. The other half is whether any
    handler will do anything with the record, and the standard library's own
    idiom for a quiet library makes the two answers disagree: with
    ``logging.getLogger("clustrix").addHandler(logging.NullHandler())`` and no
    handler configured above it, the level test passes, ``callHandlers`` finds
    the null handler, and *because it found one* it does not fall back to
    ``logging.lastResort``. Nothing is emitted. Spending the one-per-reason
    budget there rebuilds exactly the silence the budget was added to prevent
    (#152): zero warnings delivered, and then permanent silence once the caller
    wires up a handler that would have shown them.

    This walks the chain ``Logger.callHandlers`` walks and asks the same
    questions of it, so the two cannot disagree.

    **Filters are the one question this cannot answer, so it declines to
    guess.** ``Logger.handle`` runs this logger's own filters before
    ``callHandlers``, and ``Handler.handle`` runs each handler's filters before
    ``emit``; either can drop the record. A filter is arbitrary caller code
    that takes a ``LogRecord``, so the only way to learn its verdict is to
    build the record and run it -- and running it here would run it twice for
    every message that does get logged. That is not free: a filter that counts,
    rate-limits, de-duplicates or mutates the record would see double, and a
    rate-limiting filter would have its own budget spent by the very check that
    exists to protect a budget. So a filter that stands between this record and
    a handler makes the answer "no": the reason stays unreported and speaks
    again next time. That direction is deliberate. Being wrong towards *False*
    costs a repeated message; being wrong towards *True* spends the one message
    on a record nothing received, which is #152's silence rebuilt inside the
    fix for it (see ``_warn_cores_unused``). Only filters on this logger and on
    the handlers themselves count -- ``callHandlers`` never consults an
    ancestor logger's filters, so neither does this.
    """
    if not logger.isEnabledFor(logging.WARNING):
        return False

    if logger.filters:
        return False

    current: Optional[logging.Logger] = logger
    found_a_handler = False
    while current is not None:
        for handler in current.handlers:
            found_a_handler = True
            if (
                handler.level <= logging.WARNING
                and not handler.filters
                and not isinstance(handler, logging.NullHandler)
            ):
                return True
        if not current.propagate:
            break
        current = current.parent

    if found_a_handler:
        # Handlers exist, none of them will emit this, and their existence is
        # what stops ``lastResort`` from stepping in.
        return False

    last_resort = logging.lastResort
    return (
        last_resort is not None
        and last_resort.level <= logging.WARNING
        and not last_resort.filters
    )


def _warn_cores_unused(request: Optional[_CoreRequest], because: str) -> None:
    """Say out loud that a requested worker count is being discarded.

    ``@cluster(cores=8)`` reads as "use eight workers". On every local route
    that runs the function once there is nothing to hand a second worker, and
    the number used to be dropped in silence -- the shape of defect this
    project keeps finding (#152). The caller gets one message naming the
    single condition under which ``cores`` does change local behaviour.

    A request of 1 is not a request for a second worker, so nothing is said
    about it: one worker is what every one of these routes already provides.

    Each ``(where, because)`` pair is reported **once per decorated function**
    once a listener has been confirmed for it (and at most
    ``UNCONFIRMED_REPEAT_LIMIT`` times before that; see below).
    The point of the message is to tell the caller something they did not know;
    repeating it on every iteration of their loop is how a warning gets
    filtered out mentally, and the local path is exactly where a decorated
    function gets called in a tight loop. A different request -- a
    ``configure(default_cores=...)`` changed between calls, say -- is a
    different pair and speaks again, and every separately decorated function
    starts with its own empty record.

    The budget is spent at **delivery**, not at the attempt. Recording the key
    unconditionally meant that a first call made before the caller had turned
    warnings on burned the single message on a record nothing was listening
    for, and the fifty calls after ``logging.basicConfig()`` were then silent:
    zero warnings delivered, which is #152's silence rebuilt by the fix for it.
    ``_warning_reaches_someone`` asks the whole of that question -- see there
    for why the level test alone is only half of it.

    A reason the gate could not vouch for is spoken again -- but not forever.
    The gate answers "no" to any filter it declines to run, so a filter that
    in fact passes the record leaves the caller hearing the message while the
    budget stays unspent; the reason is then said again on the next call, and
    the next, which is the tight local loop this throttle exists to protect
    with the throttle switched off. So an unconfirmed reason is spoken
    ``UNCONFIRMED_REPEAT_LIMIT`` times and then left alone. The cap can only
    ever remove *repeats*: the first delivery is made before any counting can
    stop it, and a confirmed listener arriving later -- the filter removed,
    ``basicConfig`` called -- takes the ``_warning_reaches_someone`` branch,
    which the cap does not guard. The failure this fix exists to prevent is
    silence, and the cap cannot cause it.

    The number of keys is not bounded, and deliberately. A key is a pair of
    short strings, and a new one only appears when the caller changes what they
    asked for between calls; the pathological case is a loop that calls
    ``configure(default_cores=k)`` with a fresh ``k`` every iteration, which
    retains one small tuple per distinct ``k``. Capping that would mean either
    dropping keys -- and a dropped key speaks again, which is the repetition
    the throttle exists to stop -- or refusing to report a genuinely new
    request. Neither trade is worth a few hundred bytes.
    """
    if request is None or request.value <= 1:
        return
    key = (request.where, because)
    spoken = request.reported.get(key, 0)
    if spoken == _HEARD:
        return
    if _warning_reaches_someone():
        request.reported[key] = _HEARD
    elif spoken >= UNCONFIRMED_REPEAT_LIMIT:
        return
    else:
        request.reported[key] = spoken + 1
    logger.warning(
        "%s has no effect here: %s. Locally, cores is the number of worker "
        "processes a loop in the function is divided among; with no loop that "
        "can be divided, the function runs once, in this process. "
        "clustrix.explain(function) says why a loop was not divided.",
        request.where,
        because,
    )


#: Below this much estimated work, a loop is not divided among workers:
#: handing chunks out and collecting them back would cost more than it saves.
MIN_SECONDS_WORTH_DIVIDING = 0.2


def _run_split_locally(
    func: Callable,
    args: tuple,
    kwargs: dict,
    job_config: dict,
    requested: Optional[_CoreRequest] = None,
) -> Any:
    """Run ``func`` here with its loop divided among worker processes.

    ``cores`` is the number of workers (``-1``: every CPU this process may
    use). The code around the loop runs in this process, as it would have.

    The first iteration is run on its own and timed. If the rest of the loop
    looks too short to be worth dividing, one worker finishes it in order;
    either way the result is what a direct call returns.
    """
    name = getattr(func, "__name__", repr(func))
    plan = _plan_for(func)
    if not plan.parallelizable:
        _warn_cores_unused(requested, f"{name} has no loop that can be split")
        logger.info("%s runs unsplit.\n%s", name, plan.explain())
        return func(*args, **kwargs)

    cores = job_config.get("cores") or 1
    workers = available_cpus() if cores == -1 else min(cores, available_cpus())
    if workers < 2:
        _warn_cores_unused(requested, "only one CPU is available to this process")
        return func(*args, **kwargs)

    pool = shared_pool(workers)

    def map_chunks(body: Callable, env: dict, chunks: List[Sequence[Any]]) -> list:
        total = sum(len(chunk) for chunk in chunks)
        first, others = chunks[0], chunks[1:]
        ((seconds, probe_result),) = pool.map(
            timed_call, [(body, env, first[:1])], limit=1
        )
        remaining = [chunk for chunk in (first[1:], *others) if len(chunk)]
        worth_dividing = seconds * (total - 1) >= MIN_SECONDS_WORTH_DIVIDING
        if not worth_dividing:
            logger.info(
                "%s: its loop is too short to be worth dividing "
                "(about %.3fs in all); one worker runs it.",
                name,
                seconds * total,
            )
        return [
            probe_result,
            *pool.map(
                body,
                [(env, chunk) for chunk in remaining],
                limit=workers if worth_dividing else 1,
            ),
        ]

    return parallelize.run(
        plan,
        func,
        args,
        kwargs,
        map_chunks,
        jobs=workers,
        on_fallback=logger.warning,
    )
