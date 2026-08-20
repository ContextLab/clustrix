#!/usr/bin/env python3
"""``@cluster(cores=N)`` locally: honoured where it can be, reported where it cannot.

Issue #152. ``cores=8`` used to be accepted on every local route and dropped on
almost all of them -- the work ran in the caller's own process, one core, no
message. Only one local route can use the number at all: the parallel path,
which hands ``cores`` to ``LocalExecutor`` as ``max_workers`` and sizes the work
chunks to match. Everywhere else a local call is one unit of work and there is
nothing to split.

What ``cores`` buys on that one route is stated carefully here, because the
first version of these tests overstated it. ``cores`` **bounds** the pool; it
does not by itself produce parallelism. The chunks sit in a queue, and a chunk
costing microseconds can be pulled by the first worker to reach it before its
siblings have finished starting, so a run with ``cores=8`` may be observed
doing all its work in one process. Asserting "more than two workers appeared"
therefore measures the reviewer's machine and the weather, not the code -- so
the tests below make every worker genuinely occupied, by having each one wait
until its siblings have arrived. That is an assertion about the pool's width
that holds on a two-CPU runner and on a twelve-CPU laptop alike.

The tests come in three kinds:

* the parallel path must *observably* leave this process, in exactly the number
  of workers ``cores`` asked for;
* every other local route must say, out loud, that the request was discarded;
* a core count that cannot mean anything must be refused, not absorbed.

Nothing here is mocked. Every test drives the real decorator, the real
``LocalExecutor`` pool and the real ``LocalJobManager``.
"""

import logging
import multiprocessing
import os
import threading
from time import monotonic, sleep

import pytest

from clustrix import cluster, configure
from clustrix.config import get_config
from clustrix.decorator import _create_local_work_chunks
from clustrix.local_executor import LocalExecutor, LocalJobManager
from clustrix.loop_analysis import find_parallelizable_loops
from clustrix.utils import serialize_function

N = 64
TOP_MARKER = (N - 1) * (N - 1)

#: Long enough that a slow spawn on a loaded CI runner is not mistaken for a
#: pool that is too narrow, short enough that a genuinely narrow pool fails the
#: test rather than hanging the suite.
BARRIER_TIMEOUT = 30.0


def pids_of_slice(n, _parallel_i=None):
    """Report which process handled each index this worker was handed.

    Shaped like ``squares_of_slice`` in ``test_local_auto_parallel``: the
    ``for`` loop is the one clustrix's analyser detects and splits, so its body
    may read nothing but the loop variable, and the real per-index work happens
    outside it over ``_parallel_i``. Absent that keyword, this worker is the
    only worker and owns the whole range.
    """
    indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
    marker = 0
    for i in range(n):
        marker = i * i
    return {"marker": marker, "pids": [(j, os.getpid()) for j in indices]}


def pids_once_every_worker_has_arrived(n, arrivals=None, expected=1, _parallel_i=None):
    """Like ``pids_of_slice``, but no worker may finish before the pool is full.

    ``arrivals`` is a real shared dictionary served by a manager process; each
    worker records its pid there and then waits for ``expected`` distinct pids
    to appear. That turns "how many workers ran" from a race into a fact: a
    pool of ``expected`` workers cannot answer fewer than ``expected`` of these
    chunks, because the first one to arrive is still holding its chunk when the
    last one arrives. A pool narrower than ``expected`` cannot satisfy the wait
    at all, and the caller sees too few pids after the timeout rather than a
    hang.
    """
    indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
    pid = os.getpid()
    if arrivals is not None:
        arrivals[pid] = True
        deadline = monotonic() + BARRIER_TIMEOUT
        while len(arrivals) < expected and monotonic() < deadline:
            sleep(0.01)
    marker = 0
    for i in range(n):
        marker = i * i
    return {"marker": marker, "pids": [(j, pid) for j in indices]}


def pid_of_whole_call(n):
    """Take no chunk keyword, so this can only ever run as one unit of work."""
    marker = 0
    for i in range(n):
        marker = i * i
    return {"marker": marker, "pid": os.getpid()}


def where_this_ran(n):
    """Report the process *and* thread that ran the call.

    A pool that actually executes something is visible here even when it is
    only one worker wide: a ``ThreadPoolExecutor`` answers on a different
    thread, a ``ProcessPoolExecutor`` in a different process.
    """
    marker = 0
    for i in range(n):
        marker = i * i
    return {
        "marker": marker,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
    }


def has_no_loop_to_split(n):
    """No loop at all, so the auto-parallel route has nothing to work with."""
    return n * n


def fails_only_on_its_chunk(n, _parallel_i=None):
    """Run whole, this works; run on a slice, it raises -- forcing the fallback.

    A worker that raises anything other than ``TypeError`` sends
    ``_execute_local_parallel`` down its "fall back to sequential" path, which
    is a route that silently discarded the caller's ``cores`` (#152).
    """
    if _parallel_i is not None:
        raise ValueError("this callee refuses the slice it was handed")
    marker = 0
    for i in range(n):
        marker = i * i
    return marker


@pytest.fixture(autouse=True)
def local_config():
    """Real global config, restored afterwards."""
    config = get_config()
    saved = (
        config.cluster_type,
        config.cluster_host,
        config.auto_parallel,
        config.default_cores,
    )
    configure(cluster_type="local", cluster_host=None, auto_parallel=False)
    yield
    configure(
        cluster_type=saved[0],
        cluster_host=saved[1],
        auto_parallel=saved[2],
        default_cores=saved[3],
    )


def worker_pids(result):
    """The set of processes that produced a chunked result."""
    assert isinstance(result, list), f"expected per-chunk results, got {result!r}"
    return {pid for chunk in result for _, pid in chunk["pids"]}


def warnings_from(caplog):
    return [record.getMessage() for record in caplog.records]


def test_the_parallel_path_really_runs_outside_this_process():
    """The work leaves this process, and comes back whole.

    How *many* processes it reaches is not asserted here -- that is a race
    unless the workers are held against each other, which is what
    ``test_cores_is_exactly_how_wide_the_pool_gets`` does. What is not a race
    is that the work left the caller: with these chunks nothing can answer them
    except a worker, so seeing this pid at all would be the #152 evidence
    itself.
    """
    result = cluster(parallel=True, cores=4)(pids_of_slice)(N)

    assert len(result) > 1, f"work was not split: {len(result)} chunk(s)"
    pids = worker_pids(result)

    assert os.getpid() not in pids, (
        "the work ran in the caller's own process -- this is exactly the "
        f"evidence in #152 ({pids})"
    )

    # Parallelism that loses or duplicates work is not a win.
    indices = sorted(j for chunk in result for j, _ in chunk["pids"])
    assert indices == list(range(N))
    assert [chunk["marker"] for chunk in result] == [TOP_MARKER] * len(result)


@pytest.mark.parametrize("cores", [2, 4])
def test_cores_is_exactly_how_wide_the_pool_gets(cores):
    """``cores`` workers can all be busy at once, and a third never appears.

    Both halves matter and neither is a race. Each worker holds its chunk until
    ``cores`` distinct pids have checked in, so if ``max_workers`` stopped
    being wired through and the pool fell back to ``os.cpu_count()``, more than
    ``cores`` processes would answer the first ``cores`` chunks and the count
    would come out too high; if ``cores`` were dropped the other way and the
    work ran here, the count would be one and it would be this process. The
    earlier version of this test asserted only "more than two workers for
    cores=8", which is satisfied or defeated by how fast the machine spawns
    processes.
    """
    with multiprocessing.Manager() as manager:
        arrivals = manager.dict()
        result = cluster(parallel=True, cores=cores)(
            pids_once_every_worker_has_arrived
        )(N, arrivals=arrivals, expected=cores)
        pids = worker_pids(result)

    assert os.getpid() not in pids, f"the work ran in the caller's process: {pids}"
    assert len(pids) == cores, (
        f"cores={cores} asked for {cores} workers and {len(pids)} answered "
        f"({pids}); every one of them was held until the others arrived, so "
        "this is the pool's width, not a timing artefact"
    )

    indices = sorted(j for chunk in result for j, _ in chunk["pids"])
    assert indices == list(range(N)), "parallelism that loses work is not a win"


def test_the_chunk_count_follows_the_pool_not_the_machine():
    """A pool of N workers is useless if the work is only cut into fewer pieces.

    The chunk count used to be ``os.cpu_count() * 2`` however many workers had
    been asked for, which capped every run at the machine's width: on a
    two-core box ``cores=16`` produced four chunks, so twelve of the sixteen
    workers it sized had nothing they could ever pull. Reading the counts back
    for several pool sizes is machine-independent -- no CPU count enters into
    it -- which is the point.
    """
    loops = find_parallelizable_loops(pids_of_slice, (N,), {})
    assert loops, "pids_of_slice has a loop clustrix considers parallelizable"

    counts = {}
    for workers in (2, 4, 16):
        chunks = _create_local_work_chunks(pids_of_slice, (N,), {}, loops[0], workers)
        counts[workers] = len(chunks)
        assert len(chunks) >= workers, (
            f"a {workers}-worker pool was offered {len(chunks)} chunk(s): "
            f"{workers - len(chunks)} worker(s) can never be given anything"
        )

    assert counts[16] > counts[4] > counts[2], counts


def test_the_sequential_local_path_says_it_is_ignoring_cores(caplog):
    """No loop was split, so the eight workers were never going to exist."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=8)(pid_of_whole_call)(N)

    assert result["pid"] == os.getpid(), "the local path does run here"
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "has no effect here" in message for message in messages
    ), messages


def test_the_auto_parallel_route_reports_a_function_with_no_loop(caplog):
    """``auto_parallel`` is on by default, so this is the ordinary user's route.

    Nothing else covers it: with ``parallel`` unset and the shipped config, a
    function without a parallelizable loop reaches ``_execute_local_parallel``,
    is run whole, and used to discard ``cores=8`` on the way past.
    """
    configure(auto_parallel=True)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(cores=8)(has_no_loop_to_split)(N) == N * N

    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "no parallelizable loop was found" in message
        for message in messages
    ), messages


def test_parallel_true_that_cannot_split_still_reports_the_request(caplog):
    """``pid_of_whole_call`` has a detectable loop but takes no chunk keyword."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(parallel=True, cores=8)(pid_of_whole_call)(N)

    assert result["pid"] == os.getpid()
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "was not split into chunks" in message
        for message in messages
    ), messages


def test_a_fallback_to_sequential_reports_the_request(caplog):
    """The pool started, a worker raised, and the answer was computed here."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(parallel=True, cores=8)(fails_only_on_its_chunk)(N) == TOP_MARKER

    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "fell back to sequential" in message
        for message in messages
    ), messages


def test_cluster_type_local_reports_the_request_and_still_returns(caplog):
    """The ``LocalJobManager`` route: a serialized job, run here as one unit."""
    configure(cluster_type="local", cluster_host="host-the-local-manager-ignores")

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=8)(pid_of_whole_call)(N)

    assert result == {"marker": TOP_MARKER, "pid": os.getpid()}
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "single unit of work" in message
        for message in messages
    ), messages


def test_a_default_cores_the_user_set_is_reported_too(caplog):
    """Setting the default globally is still asking, and used to say nothing.

    ``configure(default_cores=8)`` followed by a bare ``@cluster()`` gave one
    core in silence, because only the per-call keyword was treated as a
    request. The shipped default is the only value that is not an instruction.
    """
    configure(default_cores=8)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(pid_of_whole_call)(N)["pid"] == os.getpid()

    messages = warnings_from(caplog)
    assert any(
        "default_cores=8" in message and "has no effect here" in message
        for message in messages
    ), messages


def test_nothing_is_reported_when_nothing_was_requested(caplog):
    """The shipped ``default_cores`` is not a per-call instruction, and 1 is no ask.

    ``cores=1`` is checked on the parallel path as well as the plain one: that
    is where the request reaches the warning itself rather than being filtered
    out by the caller, so a warning that stopped distinguishing "one worker"
    from "several" would fire here and nowhere else.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(pid_of_whole_call)(N)["pid"] == os.getpid()
        assert cluster(cores=1)(pid_of_whole_call)(N)["pid"] == os.getpid()
        assert cluster(parallel=True, cores=1)(has_no_loop_to_split)(N) == N * N
        assert (
            cluster(parallel=True, cores=1)(pid_of_whole_call)(N)["pid"] == os.getpid()
        )

    assert get_config().default_cores > 1, "default_cores is what would go unnoticed"
    assert not any(
        "has no effect here" in message for message in warnings_from(caplog)
    ), warnings_from(caplog)


@pytest.mark.parametrize("cores", [0, -2])
def test_a_core_count_below_one_is_refused_not_absorbed(cores):
    """Zero and negative used to be swallowed in two different silent ways.

    ``cores=0`` is falsy, so ``cores or config.default_cores`` quietly replaced
    it with the default. ``cores=-2`` reached ``ProcessPoolExecutor``, whose
    "max_workers must be greater than 0" was caught by the sequential fallback
    -- and because the fallback's own warning only speaks for ``cores > 1``,
    not even that was reported.
    """
    with pytest.raises(ValueError, match="positive integer"):
        cluster(cores=cores)(pid_of_whole_call)

    with pytest.raises(ValueError, match="positive integer"):
        cluster(parallel=True, cores=cores)(pids_of_slice)

    with pytest.raises(ValueError, match="positive integer"):
        LocalExecutor(max_workers=cores)


def test_local_job_manager_runs_the_job_here_and_builds_no_pool():
    """Its ``LocalExecutor(max_workers=cores, use_threads=True)`` was inert.

    ``execute_single`` is a bare ``func(*args, **kwargs)``: it never calls
    ``_create_executor``, so neither constructor argument was ever read. A pool
    object built and then not used is what made ``cores`` look honoured here.

    The check is behavioural rather than a search for ``LocalExecutor(`` in the
    source, which any reintroduction under an alias would pass. A pool that
    *runs* anything is visible: the call would answer from another thread or
    another process, and it would move when ``cores`` moved. So the job is run
    twice, with the two core counts furthest apart, and must land in this exact
    thread of this exact process both times.
    """
    manager = LocalJobManager(get_config())
    here = {"pid": os.getpid(), "thread": threading.get_ident()}

    ran = []
    for cores in (1, 8):
        job_id = manager.submit_job(
            serialize_function(where_this_ran, (N,), {}), {"cores": cores}
        )
        assert manager.get_job_status(job_id) == "completed"
        result = manager.wait_for_result(job_id)
        assert result["marker"] == TOP_MARKER
        ran.append({"pid": result["pid"], "thread": result["thread"]})

    assert ran == [here, here], (
        f"the job did not run on the caller's own thread: {ran} != {here}; "
        "something executed it through a pool"
    )
