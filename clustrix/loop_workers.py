"""Worker processes on this machine, for the chunks of a split loop.

``clustrix.parallelize`` cuts a loop into chunks and asks a backend to run
them, each in its own process. On a cluster the backend is the scheduler;
here it is this module: a small pool of long-lived Python processes that are
handed ``(function, arguments)`` pairs and hand back results.

Why not ``multiprocessing`` or ``concurrent.futures.ProcessPoolExecutor``
----------------------------------------------------------------------

Both start a worker by re-importing the parent's ``__main__`` module (as
``__mp_main__``) under the ``spawn`` and ``forkserver`` start methods. For a
plain script with no ``if __name__ == "__main__":`` guard -- which is how an
analysis script is normally written -- that re-runs the script inside every
worker, including the very call that asked for the workers. The ``fork``
start method avoids the re-import but is unsafe once the parent has threads
or has initialised CUDA, and is not the default on macOS or, from Python
3.14, anywhere.

The functions sent here are serialized by value with cloudpickle, so a
worker has no need for the parent's ``__main__`` at all. Workers are
therefore started as ordinary subprocesses that import nothing but this
module. Modules the function refers to *by name* are found because each
worker is given the parent's ``sys.path`` and working directory.

Where a task runs
-----------------

Workers are kept between calls, so what they inherited when they started
goes stale: the caller changes directory, or sets an environment variable,
and expects the loop it runs next to see that. Every task therefore carries
the caller's current working directory, and its environment whenever that
differs from what the worker was last given, and the worker adopts both
before running it. The variables a pool gives its own workers
(``environments``, ``PYTHONPATH``, ``CLUSTRIX_LOOP_WORKER``) are the
exception -- see :meth:`_Worker.environment_for`. A variable read once at
import (a thread count, ``CUDA_VISIBLE_DEVICES``) is not re-read by a
library a worker has already imported.

Output
------

Whatever a task prints is captured in the worker and returned with its
result, and :meth:`WorkerPool.map` replays it in task order once the tasks
are done. Printing from a loop body therefore still appears where the user
is looking -- a notebook cell included -- and in the order a sequential run
would have produced it, though not until the chunk it belongs to finishes.

Standard output and standard error are captured separately and each is
replayed on the caller's stream of the same name. The order *between* the
two within one task is not kept: a task's standard output is replayed
first, then its standard error, then its warnings. Warnings are recorded in
the worker and issued again in the caller with ``warnings.warn_explicit``,
so the caller's filters -- ``catch_warnings(record=True)``, ``-W error`` --
apply to them. The same warning from the same line is reported once per
task however many iterations raised it.
"""

import atexit
import contextlib
import io
import os
import pickle
import select
import struct
import subprocess
import sys
import threading
import time
import traceback
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import cloudpickle

#: Each message on the pipe is this header (payload length) and the payload.
_HEADER = struct.Struct("<Q")

#: Set in the environment of every worker. A decorated function that is
#: called from inside a loop that is already divided among workers must not
#: start a pool of its own: each worker would start ``cores`` more, and a
#: machine with four CPUs would be running sixteen busy processes -- or
#: sixty-four, one level further down. See :func:`in_worker`.
IN_WORKER_VARIABLE = "CLUSTRIX_LOOP_WORKER"


#: Read by numerical libraries, when they are imported, to size their own
#: thread pools.
THREAD_COUNT_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


class WorkerDied(RuntimeError):
    """A worker process ended without answering -- killed, or crashed."""


class NotPicklable(TypeError):
    """A task could not be pickled, so it cannot be sent to a worker."""


#: Dedupes re-issued warnings the way a module's ``__warningregistry__``
#: does for warnings raised in this process: under the default filter a
#: warning from one line is shown once, not once per chunk.
_WARNING_REGISTRY: Dict[Any, Any] = {}


@dataclass
class TaskOutput:
    """What one task wrote and warned about, to be shown where it was called."""

    stdout: str = ""
    stderr: str = ""
    #: ``(message, category, filename, lineno)``, in the order first raised.
    warnings: List[Tuple[str, type, str, int]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.stdout or self.stderr or self.warnings)

    def replay(self) -> None:
        """Write to this process's streams and issue the warnings here."""
        if self.stdout:
            sys.stdout.write(self.stdout)
        if self.stderr:
            sys.stderr.write(self.stderr)
        for message, category, filename, lineno in self.warnings:
            warnings.warn_explicit(
                message, category, filename, lineno, registry=_WARNING_REGISTRY
            )


class RemoteTraceback(Exception):
    """The traceback of an exception raised in a worker, as text.

    Attached as ``__cause__`` to the exception re-raised in the parent, so
    the lines the user needs -- the ones inside their loop body -- are shown
    above the parent-side frames.
    """

    def __str__(self) -> str:
        return "\n" + str(self.args[0])


def _send(stream: IO[bytes], payload: bytes) -> None:
    stream.write(_HEADER.pack(len(payload)))
    stream.write(payload)
    stream.flush()


def _receive(stream: IO[bytes]) -> Optional[bytes]:
    header = stream.read(_HEADER.size)
    if len(header) < _HEADER.size:
        return None
    (length,) = _HEADER.unpack(header)
    payload = stream.read(length)
    return payload if len(payload) == length else None


def available_cpus() -> int:
    """How many CPUs this process may actually use.

    A scheduler's allocation comes first: on a SLURM node configured without
    a task plugin, ``sched_getaffinity`` reports every core on the machine to
    a job that was granted one, and sizing a pool from that oversubscribes
    the node. Then the affinity mask, which reflects ``taskset`` and cgroup
    cpusets; then the machine's count.
    """
    allocated = os.environ.get("SLURM_CPUS_PER_TASK", "")
    if allocated.isdigit() and int(allocated) > 0:
        return int(allocated)
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)


def in_worker() -> bool:
    """Whether this process is one of a pool's workers (or was started by one)."""
    return bool(os.environ.get(IN_WORKER_VARIABLE))


def timed_call(function: Callable, *arguments: Any) -> Tuple[float, Any]:
    """Run ``function`` and report how long it took, measured where it ran.

    Timing a task from the parent would include starting the worker and
    shipping the arguments, which says nothing about the work itself.
    """
    started = time.perf_counter()
    result = function(*arguments)
    return time.perf_counter() - started, result


# --------------------------------------------------------------------------
# The worker side
# --------------------------------------------------------------------------


def _survives_pickling(value: Any) -> bool:
    """Whether ``value`` can be sent *and* rebuilt on the other side."""
    try:
        cloudpickle.loads(cloudpickle.dumps(value))
        return True
    except Exception:  # noqa: BLE001 - any failure means it cannot travel
        return False


def _adopt(directory: Optional[str], environment: Optional[Dict[str, str]]) -> None:
    """Make this worker's directory and environment the caller's."""
    if environment is not None and dict(os.environ) != environment:
        for name in set(os.environ) - set(environment):
            del os.environ[name]
        for name, value in environment.items():
            if os.environ.get(name) != value:
                os.environ[name] = value
    if directory is not None:
        os.chdir(directory)


def _output(
    out: io.StringIO, err: io.StringIO, caught: List[warnings.WarningMessage]
) -> TaskOutput:
    recorded: Dict[Tuple[str, type, str, int], None] = {}
    for warning in caught:
        category = warning.category
        if not _survives_pickling(category):
            category = UserWarning
        key = (str(warning.message), category, warning.filename, warning.lineno)
        recorded.setdefault(key)
    return TaskOutput(out.getvalue(), err.getvalue(), list(recorded))


#: How often a worker that cannot be told of a hang-up looks for itself.
_PARENT_CHECK_SECONDS = 1.0


def _exit_when_parent_is_gone(request_fd: int) -> None:
    """Start a thread that ends this worker as soon as its parent is gone.

    A worker only reads the pipe between tasks. One in the middle of a
    chunk when its parent was killed -- SIGTERM, SIGKILL, a restarted
    notebook kernel -- used to finish the chunk first: minutes of work
    for nobody, on CPUs or GPUs the user thinks are free.

    The parent holds the only write end of the request pipe, and the kernel
    closes it however the parent ends, so a hang-up on the read end is the
    signal. It is waited for with ``poll`` and no requested events, which
    reports a hang-up without reading anything the main thread is owed. A
    change of parent process id is checked as well, for a platform where the
    hang-up is not reported.

    ``prctl(PR_SET_PDEATHSIG)`` is deliberately not used: it fires when the
    *thread* that started the process ends, and workers are started from
    short-lived pool threads, so every worker would be killed at the end of
    the call that started it.

    The exit is ``os._exit``: the task is in the middle of user code that
    cannot be interrupted from here. Nothing it was writing is finished or
    removed, and processes it started itself are not stopped.
    """
    parent = os.getppid()

    def watch() -> None:
        hung_up = None
        if hasattr(select, "poll"):
            hung_up = select.poll()
            hung_up.register(request_fd, 0)
        while True:
            if hung_up is not None:
                gone = bool(hung_up.poll(_PARENT_CHECK_SECONDS * 1000))
            else:
                time.sleep(_PARENT_CHECK_SECONDS)
                gone = False
            if gone or os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=watch, name="clustrix-parent-watch", daemon=True).start()


def worker_main() -> None:
    """Serve tasks from the parent until its end of the pipe closes.

    Between tasks that is noticed by the read below, which returns. During
    one it is noticed by :func:`_exit_when_parent_is_gone`.
    """
    requests = os.fdopen(os.dup(sys.stdin.fileno()), "rb")
    _exit_when_parent_is_gone(requests.fileno())
    replies = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    # The protocol owns the real stdin and stdout from here on. Anything
    # that writes to file descriptor 1 directly -- a C library, a child
    # process -- goes to stderr instead of corrupting a reply.
    os.dup2(os.open(os.devnull, os.O_RDONLY), sys.stdin.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

    #: The environment the parent last sent. Enforced before every task, so
    #: a variable one task set is not still there for the next.
    environment: Optional[Dict[str, str]] = None
    while True:
        request = _receive(requests)
        if request is None:
            return
        out, err = io.StringIO(), io.StringIO()
        caught: List[warnings.WarningMessage] = []
        try:
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
                warnings.catch_warnings(record=True) as recording,
            ):
                assert recording is not None
                caught = recording
                warnings.simplefilter("always")
                directory, sent_environment, task = pickle.loads(request)
                if sent_environment is not None:
                    environment = sent_environment
                _adopt(directory, environment)
                function, arguments = cloudpickle.loads(task)
                outcome: Tuple[str, Any] = ("ok", function(*arguments))
            reply = cloudpickle.dumps((*outcome, _output(out, err, caught)))
        except BaseException as error:  # noqa: BLE001 - shipped to the parent
            # BaseException on purpose: SystemExit and KeyboardInterrupt
            # raised by the task are the task's outcome, to be re-raised
            # where the user called it, not a reason to drop the connection.
            reply = _failure_reply(
                error, traceback.format_exc(), _output(out, err, caught)
            )
        _send(replies, reply)


def _failure_reply(error: BaseException, trace: str, output: TaskOutput) -> bytes:
    """The reply for a task that raised (or whose result would not pickle).

    An exception whose ``__init__`` takes more than the message pickles
    here and then fails to *un*pickle in the parent -- pickle rebuilds it
    by calling its class with ``args`` -- which would report a ``TypeError``
    about a missing argument in place of the real failure. So the round
    trip is tried here. An exception that does not survive it is sent in
    parts (class, ``args``, attributes) and put together in the parent
    without calling ``__init__``, so it still arrives as its own type. Only
    if the parts cannot travel either is it described in words. The
    traceback that says where it came from goes with all three.
    """
    if _survives_pickling(error):
        return cloudpickle.dumps(("error", (error, trace), output))
    parts = (type(error), error.args, dict(vars(error)))
    if _survives_pickling(parts):
        return cloudpickle.dumps(("error in parts", (parts, trace), output))
    described = RuntimeError(f"{type(error).__name__}: {error}")
    return cloudpickle.dumps(("error", (described, trace), output))


def _rebuilt(kind: type, arguments: tuple, attributes: Dict[str, Any]) -> BaseException:
    """An exception from its parts, without calling its ``__init__``."""
    error: BaseException = BaseException.__new__(kind)
    error.args = arguments
    vars(error).update(attributes)
    return error


# --------------------------------------------------------------------------
# The parent side
# --------------------------------------------------------------------------


class _Worker:
    """One worker process and the pipe to it."""

    def __init__(self, extra_environment: Dict[str, str], generation: int = 0):
        #: Which set of ``environments`` the pool had when this was started.
        self.generation = generation
        #: What the pool, not the caller, decides in this worker's environment.
        self._owned = {
            # Modules the task names rather than carries are imported in the
            # worker; it must look for them where this process would.
            "PYTHONPATH": os.pathsep.join(entry or os.getcwd() for entry in sys.path),
            IN_WORKER_VARIABLE: "1",
            **extra_environment,
        }
        #: The environment last sent with a task; resent only when it changes.
        self._sent_environment: Optional[Dict[str, str]] = None
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from clustrix.loop_workers import worker_main; worker_main()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, **self._owned},
        )

    def environment_for(self, callers: Dict[str, str]) -> Dict[str, str]:
        """The environment a task from a caller with ``callers`` runs in.

        The caller's, except for what the pool set for this worker: its
        pinned variables (one GPU per worker, say), ``PYTHONPATH`` and the
        worker marker stay as they are. A thread-count variable is the
        pool's choice only until the caller sets it; then it is theirs.
        """
        environment = dict(callers)
        for name, value in self._owned.items():
            if not (name in THREAD_COUNT_VARIABLES and name in callers):
                environment[name] = value
        return environment

    def run(
        self, payload: bytes, directory: Optional[str], callers: Dict[str, str]
    ) -> Tuple[str, Any, TaskOutput]:
        assert self.process.stdin is not None and self.process.stdout is not None
        environment = self.environment_for(callers)
        changed = environment != self._sent_environment
        request = pickle.dumps((directory, environment if changed else None, payload))
        try:
            _send(self.process.stdin, request)
            self._sent_environment = environment
            reply = _receive(self.process.stdout)
        except (BrokenPipeError, OSError):
            reply = None
        if reply is None:
            code = self.process.wait()
            raise WorkerDied(
                "a worker process ended without returning a result "
                f"(exit status {code}); it was killed, ran out of memory, or "
                "crashed outside Python"
            )
        return cloudpickle.loads(reply)

    def alive(self) -> bool:
        return self.process.poll() is None

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            self.process.kill()

    def close(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


@dataclass
class _Batch:
    """The tasks of one :meth:`WorkerPool.map` call, while they run."""

    directory: Optional[str]
    environment: Dict[str, str]
    #: Workers running one of this call's tasks right now.
    busy: Set[_Worker] = field(default_factory=set)
    #: Set when the caller stopped waiting: nothing more is to be started.
    abandoned: bool = False


def _what_cannot_be_pickled(function: Callable, arguments: Sequence[Any]) -> str:
    """Name the part of a task that pickling fails on."""
    if not _survives_pickling(function):
        return f"the function {getattr(function, '__name__', function)!r}"
    for position, argument in enumerate(arguments):
        if _survives_pickling(argument):
            continue
        if isinstance(argument, dict):
            for key, value in argument.items():
                if not _survives_pickling(value):
                    return f"{key!r} (a {type(value).__name__})"
        return f"argument {position} (a {type(argument).__name__})"
    return "the task"


class WorkerPool:
    """At most ``size`` worker processes, started on first use and kept.

    The bound holds however many threads use the pool at once: a task that
    finds every worker busy waits for one, it does not start another.

    ``environments`` gives each worker extra environment variables, by
    position -- this is how one GPU is pinned per worker
    (``CUDA_VISIBLE_DEVICES``) or a thread count set (``OMP_NUM_THREADS``).
    A variable has to be in place before the library that reads it is
    imported, which is why it is set at process start and not per task.
    """

    def __init__(
        self,
        workers: int,
        environments: Optional[Sequence[Dict[str, str]]] = None,
    ):
        if workers < 1:
            raise ValueError(f"a pool needs at least one worker; got {workers}")
        self.size = workers
        self._environments = list(environments or [])
        self._idle: List[_Worker] = []
        self._all: List[_Worker] = []
        self._lock = threading.Lock()
        #: Signalled whenever a worker becomes idle or a new one may be started.
        self._available = threading.Condition(self._lock)
        self._closed = False
        #: Bumped when ``environments`` is replaced: a worker started under
        #: the old one cannot be given the new one, only retired.
        self._generation = 0

    def _start_worker(self) -> _Worker:
        """Start one more worker. The caller holds the lock."""
        extra = (
            self._environments[len(self._all) % len(self._environments)]
            if self._environments
            else {}
        )
        worker = _Worker(extra, self._generation)
        self._all.append(worker)
        return worker

    def _usable(self, worker: _Worker) -> bool:
        return worker.alive() and worker.generation == self._generation

    def _acquire(self) -> _Worker:
        with self._available:
            while True:
                if self._closed:
                    raise RuntimeError("the worker pool has been closed")
                while self._idle:
                    worker = self._idle.pop()
                    if self._usable(worker):
                        return worker
                    self._all.remove(worker)
                    worker.close()
                if len(self._all) < self.size:
                    return self._start_worker()
                # Every worker is busy with another caller's task.
                self._available.wait()

    def prestart(self) -> None:
        """Start every worker now, without waiting for any of them.

        Starting an interpreter takes a noticeable fraction of a second.
        Begun together, the workers are importing while the first task is
        still being timed, instead of one after another as tasks arrive.
        """
        with self._available:
            while len(self._all) < self.size:
                self._idle.append(self._start_worker())
            self._available.notify_all()

    def grow(
        self,
        workers: int,
        environments: Optional[Sequence[Dict[str, str]]] = None,
    ) -> None:
        """Allow up to ``workers`` processes; never fewer than there are.

        Tasks already running are not disturbed, which replacing the pool
        with a larger one would do to every other thread using it.

        ``environments``, when given, replaces the pool's. An environment is
        fixed when a process starts, so workers started under the old one
        are retired as they become idle and are replaced on demand.
        """
        with self._available:
            if environments is not None:
                self._environments = list(environments)
                self._generation += 1
                retired, self._idle = self._idle, []
                for worker in retired:
                    self._all.remove(worker)
                    worker.close()
            if workers > self.size:
                self.size = workers
            self._available.notify_all()

    def _release(self, worker: _Worker) -> None:
        with self._available:
            if self._usable(worker) and not self._closed:
                self._idle.append(worker)
            else:
                if worker in self._all:
                    self._all.remove(worker)
                worker.close()
            # Either a worker is idle, or there is room to start one.
            self._available.notify()

    def _run_one(self, payload: bytes, batch: _Batch) -> Tuple[str, Any, TaskOutput]:
        worker = self._acquire()
        try:
            with self._lock:
                if batch.abandoned:
                    raise WorkerDied("the call this task belonged to was interrupted")
                batch.busy.add(worker)
            return worker.run(payload, batch.directory, batch.environment)
        finally:
            with self._lock:
                batch.busy.discard(worker)
            self._release(worker)

    def _abandon(self, batch: _Batch, futures: Sequence[Future]) -> None:
        """Stop the tasks of a call nobody is waiting for any more.

        A task cannot be interrupted from outside, so the workers running
        this call's tasks are killed; the pool starts others when it next
        needs them. Tasks not yet started are never started.
        """
        for future in futures:
            future.cancel()
        with self._lock:
            batch.abandoned = True
            busy = list(batch.busy)
        for worker in busy:
            worker.kill()

    def map(
        self,
        function: Callable,
        argument_lists: Sequence[Sequence[Any]],
        emit: Optional[Callable[[TaskOutput], object]] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Call ``function(*arguments)`` for each list, in parallel.

        At most ``limit`` run at once (the pool's size when omitted): the
        shared pool may be larger than one caller asked for.

        Each task runs in the working directory and environment this
        process has when ``map`` is called (see the module docstring).

        Results come back in the order of ``argument_lists``. What each task
        printed and warned about is passed to ``emit`` as a
        :class:`TaskOutput`, in that same order (:meth:`TaskOutput.replay`,
        which shows it here, if not given). If a task raised, the exception
        of the *earliest* such task is re-raised here once every task has
        finished -- the one a sequential run would have hit.

        If this call is itself interrupted -- ``KeyboardInterrupt`` in the
        waiting thread -- it does not wait for the tasks: the workers
        running them are killed and the interrupt is raised at once.

        Raises :class:`NotPicklable` if a task cannot be sent at all.
        """
        payloads = []
        for arguments in argument_lists:
            try:
                payloads.append(cloudpickle.dumps((function, tuple(arguments))))
            except Exception as error:  # noqa: BLE001 - whatever pickling raises
                raise NotPicklable(
                    f"{_what_cannot_be_pickled(function, arguments)} cannot be "
                    f"pickled, so it cannot be sent to a worker process: {error}"
                ) from error
        if not payloads:
            return []
        try:
            directory: Optional[str] = os.getcwd()
        except OSError:  # the working directory has been removed
            directory = None
        batch = _Batch(directory, dict(os.environ))
        width = max(1, min(limit or self.size, self.size, len(payloads)))
        with ThreadPoolExecutor(max_workers=width) as threads:
            futures: List[Future] = []
            outcomes: List[Tuple[str, Any, TaskOutput]] = []
            try:
                for payload in payloads:
                    futures.append(threads.submit(self._run_one, payload, batch))
                for future in futures:
                    try:
                        outcomes.append(future.result())
                    except WorkerDied as died:
                        outcomes.append(("died", died, TaskOutput()))
            except BaseException:
                # Leaving the ``with`` waits for the threads, and they are
                # waiting for the workers.
                self._abandon(batch, futures)
                raise

        results = []
        for status, value, output in outcomes:
            if output:
                (emit or TaskOutput.replay)(output)
            if status == "ok":
                results.append(value)
            elif status == "died":
                raise value
            else:
                raised, trace = value
                if status == "error in parts":
                    raised = _rebuilt(*raised)
                raise raised from RemoteTraceback(trace)
        return results

    def close(self) -> None:
        with self._available:
            self._closed = True
            workers, self._all, self._idle = self._all, [], []
            self._available.notify_all()
        for worker in workers:
            worker.close()

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


# --------------------------------------------------------------------------
# One pool per process
# --------------------------------------------------------------------------

_SHARED_LOCK = threading.Lock()
_SHARED: Optional[WorkerPool] = None


def _threads_per_worker(workers: int) -> Dict[str, str]:
    """The thread-count variables for each of ``workers`` workers.

    NumPy's BLAS, scikit-learn's OpenMP and the like each start a thread
    pool as wide as the machine in every process that imports them. Four
    workers on four CPUs are then sixteen threads fighting over four cores,
    and dividing the loop gains nothing. Each worker is told its share.

    A variable the user has set is theirs: it is inherited as it is.
    """
    share = str(max(1, available_cpus() // workers))
    return {name: share for name in THREAD_COUNT_VARIABLES if name not in os.environ}


def shared_pool(workers: int) -> WorkerPool:
    """The process-wide pool, grown if ``workers`` is more than it has.

    Kept between calls: starting an interpreter and importing a scientific
    stack costs a large fraction of a second per worker, which would be paid
    on every call of a decorated function otherwise.

    Its workers are each limited to their share of the CPUs in the threads
    numerical libraries start (see :func:`_threads_per_worker`). A
    :class:`WorkerPool` built directly sets nothing of the kind.
    """
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = WorkerPool(workers, [_threads_per_worker(workers)])
            _SHARED.prestart()
        elif _SHARED.size < workers:
            # Grown in place: another thread may be in the middle of a loop
            # on this pool, and closing it would kill that loop's workers.
            # More workers means a smaller share of threads for each, so
            # those already started are retired as they finish.
            _SHARED.grow(workers, [_threads_per_worker(workers)])
            _SHARED.prestart()
        return _SHARED


#: Pools whose workers each see their own GPUs, by the device groups they
#: were built for. Separate from the shared pool: which devices a process
#: may use is fixed when it starts.
_PINNED: Dict[Tuple[str, ...], WorkerPool] = {}


def gpu_pool(device_groups: Sequence[str]) -> WorkerPool:
    """A pool with one worker per group, each started with
    ``CUDA_VISIBLE_DEVICES`` set to its group (``"0"``, ``"2,3"``).

    Kept between calls like the shared pool, and for the same reason; a
    framework's start-up on a GPU costs more than an interpreter's.
    """
    key = tuple(device_groups)
    with _SHARED_LOCK:
        pool = _PINNED.get(key)
        if pool is None:
            pool = _PINNED[key] = WorkerPool(
                len(key), [{"CUDA_VISIBLE_DEVICES": group} for group in key]
            )
            pool.prestart()
        return pool


def _close_shared_pool() -> None:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is not None:
            _SHARED.close()
            _SHARED = None
        for pool in _PINNED.values():
            pool.close()
        _PINNED.clear()


atexit.register(_close_shared_pool)
