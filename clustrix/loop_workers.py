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

Output
------

Whatever a task prints is captured in the worker and returned with its
result, and :meth:`WorkerPool.map` replays it in task order once the tasks
are done. Printing from a loop body therefore still appears where the user
is looking -- a notebook cell included -- and in the order a sequential run
would have produced it, though not until the chunk it belongs to finishes.
"""

import atexit
import contextlib
import io
import os
import struct
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import IO, Any, Callable, Dict, List, Optional, Sequence, Tuple

import cloudpickle

#: Each message on the pipe is this header (payload length) and the payload.
_HEADER = struct.Struct("<Q")

#: Set in the environment of every worker. A decorated function that is
#: called from inside a loop that is already divided among workers must not
#: start a pool of its own: each worker would start ``cores`` more, and a
#: machine with four CPUs would be running sixteen busy processes -- or
#: sixty-four, one level further down. See :func:`in_worker`.
IN_WORKER_VARIABLE = "CLUSTRIX_LOOP_WORKER"


class WorkerDied(RuntimeError):
    """A worker process ended without answering -- killed, or crashed."""


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


def worker_main() -> None:
    """Serve tasks from the parent until its end of the pipe closes."""
    requests = os.fdopen(os.dup(sys.stdin.fileno()), "rb")
    replies = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    # The protocol owns the real stdin and stdout from here on. Anything
    # that writes to file descriptor 1 directly -- a C library, a child
    # process -- goes to stderr instead of corrupting a reply.
    os.dup2(os.open(os.devnull, os.O_RDONLY), sys.stdin.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

    while True:
        request = _receive(requests)
        if request is None:
            return
        printed = io.StringIO()
        try:
            with (
                contextlib.redirect_stdout(printed),
                contextlib.redirect_stderr(printed),
            ):
                function, arguments = cloudpickle.loads(request)
                outcome: Tuple[str, Any] = ("ok", function(*arguments))
            reply = cloudpickle.dumps((*outcome, printed.getvalue()))
        except BaseException as error:  # noqa: BLE001 - shipped to the parent
            # BaseException on purpose: SystemExit and KeyboardInterrupt
            # raised by the task are the task's outcome, to be re-raised
            # where the user called it, not a reason to drop the connection.
            reply = _failure_reply(error, traceback.format_exc(), printed.getvalue())
        _send(replies, reply)


def _failure_reply(error: BaseException, trace: str, printed: str) -> bytes:
    try:
        return cloudpickle.dumps(("error", (error, trace), printed))
    except Exception:  # noqa: BLE001 - an unpicklable exception, or result
        # The exception object itself could not be sent (or, on the success
        # path, the result could not). Say what it was in words.
        described = RuntimeError(f"{type(error).__name__}: {error}")
        return cloudpickle.dumps(("error", (described, trace), printed))


# --------------------------------------------------------------------------
# The parent side
# --------------------------------------------------------------------------


class _Worker:
    """One worker process and the pipe to it."""

    def __init__(self, extra_environment: Dict[str, str]):
        environment = dict(os.environ)
        # Modules the task names rather than carries are imported in the
        # worker; it must look for them where this process would.
        environment["PYTHONPATH"] = os.pathsep.join(
            entry or os.getcwd() for entry in sys.path
        )
        environment[IN_WORKER_VARIABLE] = "1"
        environment.update(extra_environment)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from clustrix.loop_workers import worker_main; worker_main()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=environment,
        )

    def run(self, payload: bytes) -> Tuple[str, Any, str]:
        assert self.process.stdin is not None and self.process.stdout is not None
        try:
            _send(self.process.stdin, payload)
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

    def _start_worker(self) -> _Worker:
        """Start one more worker. The caller holds the lock."""
        extra = (
            self._environments[len(self._all) % len(self._environments)]
            if self._environments
            else {}
        )
        worker = _Worker(extra)
        self._all.append(worker)
        return worker

    def _acquire(self) -> _Worker:
        with self._available:
            while True:
                if self._closed:
                    raise RuntimeError("the worker pool has been closed")
                while self._idle:
                    worker = self._idle.pop()
                    if worker.alive():
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

    def grow(self, workers: int) -> None:
        """Allow up to ``workers`` processes; never fewer than there are.

        Tasks already running are not disturbed, which replacing the pool
        with a larger one would do to every other thread using it.
        """
        with self._available:
            if workers > self.size:
                self.size = workers
                self._available.notify_all()

    def _release(self, worker: _Worker) -> None:
        with self._available:
            if worker.alive() and not self._closed:
                self._idle.append(worker)
            else:
                if worker in self._all:
                    self._all.remove(worker)
                worker.close()
            # Either a worker is idle, or there is room to start one.
            self._available.notify()

    def _run_one(self, payload: bytes) -> Tuple[str, Any, str]:
        worker = self._acquire()
        try:
            return worker.run(payload)
        finally:
            self._release(worker)

    def map(
        self,
        function: Callable,
        argument_lists: Sequence[Sequence[Any]],
        emit: Optional[Callable[[str], object]] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Call ``function(*arguments)`` for each list, in parallel.

        At most ``limit`` run at once (the pool's size when omitted): the
        shared pool may be larger than one caller asked for.

        Results come back in the order of ``argument_lists``. What the tasks
        printed is passed to ``emit`` in that same order (``sys.stdout``'s
        ``write`` as it is when the tasks finish, if not given). If a task raised,
        the exception of the *earliest* such task is re-raised here once
        every task has finished -- the one a sequential run would have hit.
        """
        payloads = [
            cloudpickle.dumps((function, tuple(arguments)))
            for arguments in argument_lists
        ]
        if not payloads:
            return []
        width = max(1, min(limit or self.size, self.size, len(payloads)))
        with ThreadPoolExecutor(max_workers=width) as threads:
            futures = [threads.submit(self._run_one, payload) for payload in payloads]
            outcomes: List[Any] = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except WorkerDied as died:
                    outcomes.append(("died", died, ""))

        results = []
        for status, value, printed in outcomes:
            if printed:
                (emit or sys.stdout.write)(printed)
            if status == "ok":
                results.append(value)
            elif status == "died":
                raise value
            else:
                error, trace = value
                raise error from RemoteTraceback(trace)
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


def shared_pool(workers: int) -> WorkerPool:
    """The process-wide pool, grown if ``workers`` is more than it has.

    Kept between calls: starting an interpreter and importing a scientific
    stack costs a large fraction of a second per worker, which would be paid
    on every call of a decorated function otherwise.
    """
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = WorkerPool(workers)
            _SHARED.prestart()
        elif _SHARED.size < workers:
            # Grown in place: another thread may be in the middle of a loop
            # on this pool, and closing it would kill that loop's workers.
            _SHARED.grow(workers)
            _SHARED.prestart()
        return _SHARED


def _close_shared_pool() -> None:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is not None:
            _SHARED.close()
            _SHARED = None


atexit.register(_close_shared_pool)
