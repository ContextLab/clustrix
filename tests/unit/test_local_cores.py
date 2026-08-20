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
* every other local route must say, out loud, that the request was discarded
  -- once per decorated function per distinct request, not on every call;
* a core count that cannot mean anything must be refused, not absorbed.

Nothing here is mocked. Every test drives the real decorator, the real
``LocalExecutor`` pool and the real ``LocalJobManager``.
"""

import io
import logging
import multiprocessing
import os
import threading
from contextlib import contextmanager
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


def indices_of_slice(n, _parallel_i=None):
    """Return this worker's indices as a list, so the combined answer is ordered.

    ``_combine_local_results`` concatenates per-chunk lists, so a run of this
    over ``range(N)`` must come back as ``list(range(N))`` however it was cut
    up. A dict-returning helper cannot see that: the combiner does not
    concatenate those, so the chunk order is only visible through a list.
    """
    indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
    marker = 0
    for i in range(n):
        marker = i * i
    del marker
    return list(indices)


def countdown_of_slice(n, _parallel_i=None):
    """Like ``indices_of_slice``, but its natural answer is not already sorted.

    ``indices_of_slice`` answers ``[0, 1, 2, ...]``, which is its own sorted
    order, so a combiner that returned ``sorted(combined)`` would agree with it
    on every input and go unnoticed. Counting down means sorted order and
    produced order are different sequences, and only one of them is the answer
    the undecorated call gives.
    """
    indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
    marker = 0
    for i in range(n):
        marker = i * i
    del marker
    return [n - 1 - j for j in indices]


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


@pytest.mark.parametrize("cores", [2, 4, 8])
def test_cores_is_exactly_how_wide_the_pool_gets(cores, machine_with_two_cpus):
    """``cores`` workers can all be busy at once, and a further one never appears.

    Both halves matter and neither is a race. Each worker holds its chunk until
    ``cores`` distinct pids have checked in, so a pool narrower than ``cores``
    reports too few pids and a wider one reports too many.

    The machine is pinned to two CPUs for the duration, which is what gives
    ``cores=8`` its power: with ``max_workers`` not wired through to
    ``_create_local_work_chunks`` the chunk count falls back to
    ``os.cpu_count() * 2`` -- four chunks -- and only four of the eight sized
    workers can ever be handed anything, so this reports four. Parametrising
    over 2 and 4 alone could not see that, because on any machine with two or
    more CPUs the ``cpu_count``-driven chunking still supplies enough chunks to
    fill a pool that small. A test whose power depends on the reviewer's CPU
    count is not a test.
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


@pytest.fixture
def machine_with_two_cpus(monkeypatch):
    """Pin ``os.cpu_count()`` to 2 for the duration of a test.

    Not a mock of anything clustrix owns: it is the machine, and pinning it is
    the only way to write a test about "the pool follows what you asked for,
    not what you are running on" whose result does not depend on what the
    reviewer is running on. Every count asserted under this fixture is
    therefore a fact about the code.
    """
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    return 2


@pytest.mark.parametrize("cores", [2, 4, 8, 16])
def test_the_call_site_hands_the_pool_size_to_the_chunker(cores, machine_with_two_cpus):
    """The wiring itself, exercised through the decorator rather than by hand.

    ``_execute_local_parallel`` passes ``local_executor.max_workers`` into
    ``_create_local_work_chunks``. Delete that argument -- it reads like a
    signature cleanup -- and the chunk count silently reverts to
    ``os.cpu_count() * 2``, which is the #152 symptom itself: on this
    two-CPU machine every pool size would be cut into four pieces, so
    ``cores=16`` would size sixteen workers and offer them four chunks.
    ``test_the_chunk_count_follows_the_pool_not_the_machine`` calls the chunker
    directly and so cannot see the call site at all.

    One result comes back per chunk, so the chunk count is readable from here
    without reaching inside anything. Two contracts are asserted, and both are
    machine-independent because the machine is pinned:

    * every worker is offered exactly two chunks (see
      ``test_a_worker_is_offered_more_than_one_chunk`` for why two). The
      equality holds because ``N`` is divisible by ``2 * cores`` for every
      count parametrised here, and only for that reason: ``chunk_size`` is a
      floor, so a remainder becomes a further chunk. Adding ``cores=3`` to the
      list would cut ``N == 64`` into seven pieces against ``2 * 3 == 6``.
      A new pool size has to satisfy ``N % (2 * cores) == 0``, or this
      assertion has to loosen with it;
    * the count moves when ``cores`` moves, while ``os.cpu_count()`` does not.
    """
    result = cluster(parallel=True, cores=cores)(pids_of_slice)(N)
    assert isinstance(result, list), f"expected per-chunk results, got {result!r}"

    assert len(result) == 2 * cores, (
        f"cores={cores} sized a {cores}-worker pool and the work was cut into "
        f"{len(result)} chunk(s) on a machine reporting "
        f"{os.cpu_count()} CPUs -- expected exactly {2 * cores}"
    )

    # Splitting further must not lose, duplicate or corrupt the work.
    indices = sorted(j for chunk in result for j, _ in chunk["pids"])
    assert indices == list(range(N)), "parallelism that loses work is not a win"
    assert [chunk["marker"] for chunk in result] == [TOP_MARKER] * len(result)


def test_a_configured_default_cores_sizes_the_chunks_too(machine_with_two_cpus):
    """The other route into the pool: ``configure(default_cores=N)``, no keyword.

    Every other test here drives ``@cluster(cores=N)``, and the two routes meet
    only at ``job_config["cores"] = cores or config.default_cores``. Replace
    that fallback with the shipped constant -- it reads like a tidy-up, since
    4 *is* what ``default_cores`` ships as -- and a user who wrote
    ``configure(default_cores=16)`` gets a 4-worker pool cut into 8 chunks
    while the whole suite stays green.

    It is worse than an untested branch. ``_requested_cores`` still reads
    ``default_cores`` when it decides whether to warn, so under that change the
    caller would be told about a number that no longer sizes anything: the
    message and the behaviour would disagree in silence, which is the shape of
    #152 itself.

    The machine is pinned to two CPUs, so 32 chunks cannot have come from
    ``os.cpu_count()``; the only place 16 exists is the configured default.
    """
    configure(default_cores=16, auto_parallel=True)
    assert get_config().default_cores == 16

    # No ``cores`` keyword anywhere: the only worker count in play is the
    # configured one. One result comes back per chunk.
    result = cluster(pids_of_slice)(N)
    assert isinstance(result, list), f"expected per-chunk results, got {result!r}"

    assert len(result) == 2 * 16, (
        f"configure(default_cores=16) cut {N} iterations into {len(result)} "
        f"chunk(s) on a machine reporting {os.cpu_count()} CPUs -- expected "
        f"exactly {2 * 16}, two per configured worker"
    )

    indices = sorted(j for chunk in result for j, _ in chunk["pids"])
    assert indices == list(range(N)), "parallelism that loses work is not a win"
    assert os.getpid() not in worker_pids(result)


def test_a_configured_default_cores_is_how_wide_the_pool_gets():
    """The same route, measured as a pool width rather than a chunk count.

    ``configure(default_cores=8)`` with a bare ``@cluster()`` must put eight
    workers on the work. Each one holds its chunk until eight distinct pids
    have checked in, so a narrower pool reports too few and a wider one too
    many -- not a race either way. A fallback pinned to the shipped 4 answers
    four here.
    """
    configure(default_cores=8, auto_parallel=True)

    with multiprocessing.Manager() as manager:
        arrivals = manager.dict()
        result = cluster(pids_once_every_worker_has_arrived)(
            N, arrivals=arrivals, expected=8
        )
        pids = worker_pids(result)

    assert os.getpid() not in pids, f"the work ran in the caller's process: {pids}"
    assert len(pids) == 8, (
        f"configure(default_cores=8) sized a pool {len(pids)} worker(s) wide "
        f"({pids}); every one was held until the others arrived, so this is "
        "the pool's width and not a timing artefact"
    )

    indices = sorted(j for chunk in result for j, _ in chunk["pids"])
    assert indices == list(range(N)), "parallelism that loses work is not a win"


def test_a_parallel_run_returns_its_results_in_order():
    """Chunk order is user-visible output order, and nothing else pins it.

    ``_combine_local_results`` concatenates the per-chunk lists in the order
    the chunks were built, so reversing that order reverses the caller's
    answer: ``[56, 57, ..., 48, 49]`` instead of ``[0, 1, 2, ...]``. Every
    other assertion in this file sorts the indices before comparing -- which is
    right for "no work was lost" and blind to "the work came back shuffled".

    A parallel run that answers in a different order from a sequential one is a
    correctness defect, so the decorated and undecorated results are compared
    as sequences, at two pool sizes so the comparison is not accidentally
    reading a single chunk.

    Reversal is not the only way to lose the order, and the obvious other way
    hid here for a while: a combiner ending in ``sorted(combined)`` reads like
    a determinism fix -- results do arrive from a pool in completion order --
    and ``indices_of_slice`` cannot see it, because ``[0, 1, 2, ...]`` is its
    own sorted order. ``countdown_of_slice`` is the same shape of function with
    a descending answer, so sorted order and produced order are different
    sequences and only the produced one matches the undecorated call. Sorting
    is not a fix in any case: order comes from the chunk list, which is built
    in range order, and ``execute_parallel`` already returns per chunk.
    """
    for func, expected in (
        (indices_of_slice, list(range(N))),
        (countdown_of_slice, list(range(N - 1, -1, -1))),
    ):
        sequential = func(N)
        assert sequential == expected, f"{func.__name__} answers {sequential!r}"

        for cores in (2, 4):
            parallel = cluster(parallel=True, cores=cores)(func)(N)
            assert parallel == sequential, (
                f"{func.__name__} at cores={cores} returned the work in a "
                "different order from the undecorated call"
            )


def sum_of_slice(n, _parallel_i=None):
    """A scalar-returning callee: whole, it answers ``sum(range(n))``.

    Handed a slice it answers the sum *of that slice*, which is a partial
    answer and not a smaller version of the whole one. That is the difference
    between this and ``indices_of_slice``, and it is the difference that makes
    the combined shape visible -- see
    ``test_the_answers_shape_depends_on_cores_and_on_how_long_the_loop_is``.
    """
    indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
    marker = 0
    for i in range(n):
        marker = i * i
    del marker
    return sum(indices)


def test_the_answers_shape_depends_on_cores_and_on_how_long_the_loop_is(
    machine_with_two_cpus,
):
    """Pinning a live hazard exactly as it behaves, not as it ought to.

    ``_combine_local_results`` concatenates when every chunk answered with a
    list and otherwise hands back the list of per-chunk answers. For a callee
    whose answer is a list, that makes a parallel run match a sequential one.
    For a callee whose answer is a scalar it cannot: each chunk answers about
    its own slice, and there is no way to add them up without knowing what the
    caller meant by the loop.

    So two things vary that a caller would not expect to vary, and both are
    asserted here as facts about today's behaviour:

    * **``cores`` changes the answer.** The same call at three pool sizes comes
      back as three different lists, because the pool size sets the chunk count
      and the chunk count sets how the partial sums are cut. None of the three
      is the undecorated answer.
    * **The loop's length changes the *type*.** A range shorter than three
      iterations is not parallelized at all -- ``LoopInfo`` requires three
      before it considers the work worth splitting -- so the caller gets the
      scalar the undecorated function returns. One more iteration and the same
      decorated function returns a list.

    This test is deliberately not a fix. Changing what comes back is a
    breaking change to user-visible behaviour and belongs in its own release
    note; issue #170 carries the design question of what ``parallel=True``
    should promise a scalar-returning callee. What must not happen in the
    meantime is the shape changing again by accident, so the numbers below are
    exact. They are machine-independent: ``LocalExecutor`` takes the worker
    count it is given, and the machine is pinned to two CPUs to prove the
    counts are not coming from it.
    """
    assert sum_of_slice(8) == 28, "the undecorated answer, for reference"

    # Below the three-iteration threshold: no loop is split, so the caller
    # gets the scalar back whatever they asked for.
    for cores in (1, 2, 4):
        short = cluster(parallel=True, cores=cores)(sum_of_slice)(2)
        assert short == 1 and isinstance(short, int), (
            f"a 2-iteration loop at cores={cores} came back as {short!r}: "
            "too short to split, so this is the sequential answer"
        )

    # Long enough to split: a list of partial sums, cut differently at every
    # pool size, and equal to the sequential answer at none of them.
    expected = {
        1: [6, 22],  # two chunks of four
        2: [1, 5, 9, 13],  # four chunks of two
        4: [0, 1, 2, 3, 4, 5, 6, 7],  # eight chunks of one
    }
    for cores, answer in expected.items():
        got = cluster(parallel=True, cores=cores)(sum_of_slice)(8)
        assert got == answer, (
            f"cores={cores} on an 8-iteration loop answered {got!r}, not "
            f"{answer!r}: the partial sums are cut two per worker"
        )
        assert got != sum_of_slice(8), (
            "a parallel run of a scalar-returning callee does not reproduce "
            "the sequential answer, and pretending otherwise here would hide "
            "that from the next reader"
        )

    assert len({tuple(v) for v in expected.values()}) == 3, (
        "the whole point: three pool sizes, three different answers to the " "same call"
    )


def test_the_chunk_count_moves_with_cores_on_a_fixed_machine(machine_with_two_cpus):
    """Two pool sizes, one machine: the counts must differ, through the decorator.

    The companion to the per-size contract above. If the chunk count came from
    ``os.cpu_count()`` these two runs would be cut identically, whatever was
    asked for.
    """
    narrow = cluster(parallel=True, cores=2)(pids_of_slice)(N)
    wide = cluster(parallel=True, cores=8)(pids_of_slice)(N)

    assert len(wide) > len(narrow), (
        f"cores=8 and cores=2 were both cut into {len(narrow)} chunk(s): the "
        "chunk count is being taken from the machine, not from the request"
    )


def test_a_worker_is_offered_more_than_one_chunk():
    """The ``* 2`` in the chunk size is a load-balancing decision, and is tested.

    ``chunk_size = len(loop_range) // (workers * 2)`` aims at two chunks per
    worker. Dropping the factor -- ``// workers`` -- still fills the pool once
    and survived the entire suite, which is why this test exists. It is not a
    cosmetic constant: with exactly one chunk each, a worker that draws the
    expensive chunk keeps it to the end while the others sit idle, because
    there is nothing left in the queue for them to take. Slack in the queue is
    the only rebalancing a ``ProcessPoolExecutor`` has.

    Asserting the timing consequence would be asserting the weather. The
    granularity is the part that is a fact, so that is what is checked, at
    several pool sizes and with a range long enough for the division to have
    room.

    The count is asserted **exactly**, and that was a deliberate change from
    "at least two each". Two per worker is not a floor the chunker is free to
    exceed: over-chunking is not free either, because every extra chunk is a
    pickle of the arguments, a queue round trip and a result to reassemble, and
    a chunker that answered ``workers * 4`` -- half the slice size, twice the
    dispatch -- passed the lower-bound form of this assertion while quietly
    doubling the overhead the factor of two was chosen to trade against. ``N``
    is a multiple of ``2 * workers`` at every size listed, so the arithmetic is
    exact and the equality is a statement about the rule rather than about the
    rounding.

    ``workers=1`` is in the list deliberately, and it was a judgement call.
    "One worker, one chunk, no rebalancing possible" is a defensible reading,
    and a special case returning a single chunk there survived the rest of this
    file. It is rejected: the rule is one rule, and at one worker it still has
    an observable consequence, because ``_combine_local_results`` returns
    ``results[0]`` unchanged when there is exactly one of them. A single chunk
    at ``cores=1`` would therefore make the *shape* of the returned value
    depend on the worker count -- a bare chunk result at 1, a combined list at
    2 -- for a saving of one dispatch round trip on a pool that is not
    contending for anything. The uniform rule costs nothing and keeps
    ``cores=1`` and ``cores=2`` answering the same kind of thing.
    """
    loops = find_parallelizable_loops(pids_of_slice, (N,), {})
    assert loops, "pids_of_slice has a loop clustrix considers parallelizable"

    for workers in (1, 2, 4, 8, 16):
        chunks = _create_local_work_chunks(pids_of_slice, (N,), {}, loops[0], workers)
        assert len(chunks) == 2 * workers, (
            f"a {workers}-worker pool was offered {len(chunks)} chunk(s) of "
            f"{N} iterations, not {2 * workers}: with fewer than two each, a "
            "worker that draws a slow chunk cannot be relieved by its idle "
            "siblings; with more, the extra dispatches are pure overhead"
        )


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


def test_the_same_request_is_reported_once_per_decorated_function(caplog):
    """A warning repeated on every call is a warning the user learns to skip.

    The message is worth saying: it tells the caller their eight workers are
    not going to exist. It is not worth saying five times, and the local path
    is precisely where a decorated function gets called in a loop. It is
    throttled per decorated function and per ``(request, reason)`` pair, so a
    second function -- a genuinely separate thing the user asked for -- still
    gets told.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        once = cluster(cores=8)(pid_of_whole_call)
        for _ in range(5):
            assert once(N)["pid"] == os.getpid()

        said = [m for m in warnings_from(caplog) if "cores=8" in m]
        assert len(said) == 1, f"five calls produced {len(said)} warnings: {said}"

        again = cluster(cores=8)(pid_of_whole_call)
        assert again(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 2, (
        "a separately decorated function has its own record and must be told "
        f"too: {said}"
    )


def test_a_changed_request_is_reported_again(caplog):
    """Throttling may not swallow a *different* configuration.

    The same decorated function, called after ``configure(default_cores=...)``
    changed underneath it, is a new request and has never been answered. A
    throttle keyed on the function alone would report the first value and go
    quiet on the second, which is the #152 silence again in a smaller box.
    """
    bare = cluster(pid_of_whole_call)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        configure(default_cores=8)
        assert bare(N)["pid"] == os.getpid()
        assert bare(N)["pid"] == os.getpid()

        configure(default_cores=16)
        assert bare(N)["pid"] == os.getpid()
        assert bare(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "has no effect here" in m]
    assert len(said) == 2, f"expected one warning per distinct request: {said}"
    assert sum("default_cores=8" in m for m in said) == 1, said
    assert sum("default_cores=16" in m for m in said) == 1, said


def test_a_different_decline_reason_is_reported_again(caplog):
    """One request, two reasons to drop it: the caller hears about both.

    ``_warn_cores_unused`` keys its record on ``(where, because)``, and
    ``test_a_changed_request_is_reported_again`` only ever varies ``where``.
    Drop ``because`` from the key -- it looks redundant, the message already
    names the request -- and the same decorated function that has been told
    "the local backend runs this once" goes quiet when it later declines for a
    completely different reason. The two facts are not interchangeable: the
    first says *this route* cannot use eight workers, the second says the
    parallel route looked and found no loop to split.

    Both halves are asserted, because a throttle that has stopped throttling
    would pass a bare count of two.
    """
    once = cluster(cores=8)(has_no_loop_to_split)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        configure(auto_parallel=False)
        assert once(N) == N * N  # plain local path: run here, once
        assert once(N) == N * N  # ... and throttled

        configure(auto_parallel=True)
        assert once(N) == N * N  # the parallel path: no loop to split
        assert once(N) == N * N  # ... and throttled

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 2, f"expected one warning per distinct reason: {said}"
    assert sum("runs the decorated function once" in m for m in said) == 1, said
    assert sum("no parallelizable loop was found" in m for m in said) == 1, said


@pytest.mark.parametrize("silenced_at", [logging.ERROR, logging.CRITICAL])
def test_a_warning_nobody_could_hear_does_not_spend_the_budget(silenced_at, caplog):
    """The one message is spent on delivery, not on the attempt.

    The throttle is right -- a warning repeated on every iteration is a warning
    that gets filtered out -- but it used to record the key before asking
    whether anything was listening. A library that raises clustrix's log level,
    or a script that calls the decorated function before
    ``logging.basicConfig()``, therefore spent the single message on a record
    that went nowhere, and every later call was silent: zero warnings
    delivered. That is #152's own silence, rebuilt inside the fix for it.

    Both levels above ``WARNING`` are exercised, and that is the point of the
    parametrisation rather than tidiness. Asking the gate about ``ERROR``
    instead of ``WARNING`` -- an easy slip, since the two read alike -- is
    invisible at ``CRITICAL``, where both answers are "off". At ``ERROR`` they
    part company: the real question answers "nobody can hear this", the wrong
    one answers "somebody can", and the budget is spent on a record that went
    nowhere. The level the gate asks about must be the level the message is
    logged at, and only the notch immediately above it can show that.
    """
    decorator_logger = logging.getLogger("clustrix.decorator")
    saved = decorator_logger.level
    said_once = cluster(cores=8)(pid_of_whole_call)

    try:
        decorator_logger.setLevel(silenced_at)
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()
        heard = [m for m in warnings_from(caplog) if "cores=8" in m]
        assert not heard, f"the logger was off and something got through: {heard}"
    finally:
        decorator_logger.setLevel(saved)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller turned warnings on and was told exactly once; got "
        f"{len(said)}: {said}"
    )


def test_a_null_handler_does_not_spend_the_budget_either(caplog):
    """The level was never the whole question: a handler has to want it too.

    ``logging.getLogger("clustrix").addHandler(logging.NullHandler())`` is the
    documented way to keep a library quiet, and under it ``isEnabledFor`` still
    answers True -- the level is untouched. What changes is delivery:
    ``Logger.callHandlers`` finds the null handler, does nothing with the
    record, and *because it found a handler* declines to fall back to
    ``logging.lastResort``. Zero warnings are emitted, and a gate that asked
    only about the level would have marked the reason as reported. The caller
    who later wires up a real handler -- which is the whole reason to start
    with a null one -- then hears nothing, ever.

    The root handlers are lifted for the silent phase because pytest installs
    its own there, and leaving them would mean the record *was* delivered,
    which is a different situation from the one under test.
    """
    decorator_logger = logging.getLogger("clustrix.decorator")
    null_handler = logging.NullHandler()
    saved_root = logging.root.handlers[:]
    said_once = cluster(cores=8)(pid_of_whole_call)

    decorator_logger.addHandler(null_handler)
    try:
        logging.root.handlers = []
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()
    finally:
        logging.root.handlers = saved_root
        decorator_logger.removeHandler(null_handler)

    assert not [m for m in warnings_from(caplog) if "cores=8" in m], (
        "a NullHandler was the only handler in the chain, so nothing was "
        "emitted; caplog should not have seen anything either"
    )

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller replaced the NullHandler with one that emits and was told "
        f"exactly once; got {len(said)}: {said}"
    )


DECORATOR_LOGGER = logging.getLogger("clustrix.decorator")


@contextmanager
def a_bare_logging_chain():
    """Run the block with the ``clustrix.decorator`` logging chain emptied.

    A test about what happens when nothing is listening cannot leave pytest's
    own root handlers in place: the record would genuinely be delivered, which
    is a different situation from the one under test. Every logger from
    ``clustrix.decorator`` up to the root is stripped of handlers and filters
    and set to propagate, ``clustrix.decorator`` is pinned at ``WARNING`` so
    that no inherited level can decide the answer instead of the thing being
    tested, and ``logging.lastResort`` -- a module global, and therefore
    everybody's -- is saved along with them. The block attaches whatever the
    scenario needs; everything is put back afterwards.
    """
    chain = [DECORATOR_LOGGER, logging.getLogger("clustrix"), logging.root]
    saved = [(lg, lg.handlers, lg.filters, lg.level, lg.propagate) for lg in chain]
    saved_last_resort = logging.lastResort
    saved_last_resort_level = (
        None if saved_last_resort is None else saved_last_resort.level
    )
    for one in chain:
        one.handlers = []
        one.filters = []
        one.propagate = True
    DECORATOR_LOGGER.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logging.lastResort = saved_last_resort
        if saved_last_resort is not None:
            saved_last_resort.level = saved_last_resort_level
        for one, handlers, filters, level, propagate in saved:
            one.handlers = handlers
            one.filters = filters
            one.level = level
            one.propagate = propagate


def collecting_handler(level=logging.WARNING):
    """A real handler that writes somewhere the test can read back."""
    handler = logging.StreamHandler(io.StringIO())
    handler.setLevel(level)
    return handler


def test_a_handler_that_is_too_high_to_emit_does_not_spend_the_budget(caplog):
    """The handler's level is a second gate, and it is not the logger's level.

    A ``clustrix`` logger carrying a single ``ERROR``-level file handler is an
    ordinary production setup -- an application that wants library errors on
    disk and nothing else. ``isEnabledFor(WARNING)`` says yes, because the
    *logger* level is untouched; ``callHandlers`` then finds the handler,
    declines to emit because ``WARNING < ERROR``, and *because it found one*
    does not fall back to ``logging.lastResort``. Nothing is emitted.

    This is the case where asking the handler gate about ``ERROR`` rather than
    ``WARNING`` -- the same one-notch slip that
    ``test_a_warning_nobody_could_hear_does_not_spend_the_budget`` pins for the
    logger level -- reads as "somebody can hear this", spends the single
    message on a record nothing received, and hands the caller permanent
    silence the moment they add the handler that would have shown it. That is
    #152's own defect rebuilt inside the fix for it.
    """
    said_once = cluster(cores=8)(pid_of_whole_call)
    too_high = collecting_handler(logging.ERROR)

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(too_high)
        assert DECORATOR_LOGGER.isEnabledFor(logging.WARNING), (
            "the level must be on, or this test would pass because the record "
            "was never made rather than because the handler refused it"
        )
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    written = too_high.stream.getvalue()
    assert (
        "cores=8" not in written
    ), f"an ERROR-level handler emitted a WARNING record: {written!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller added a handler that emits and was told exactly once; got "
        f"{len(said)}: {said}"
    )


def test_an_ancestor_handler_behind_propagate_false_does_not_spend_the_budget(
    caplog,
):
    """``propagate = False`` ends the walk, and the walk must end with it.

    ``logging.getLogger("clustrix.decorator").propagate = False`` is how an
    application says "this logger's records stop here". ``callHandlers`` obeys
    it literally: it visits ``clustrix.decorator``'s own handlers and then
    stops, never reaching the emitting handler an ancestor carries. So with a
    ``NullHandler`` here and a real handler on ``clustrix``, nothing is
    emitted -- a handler was found, so ``lastResort`` stays out of it too.

    A gate that walked to the root regardless would see the ancestor's real
    handler, answer "somebody can hear this", and spend the one message on a
    record that stopped one logger short of it.
    """
    said_once = cluster(cores=8)(pid_of_whole_call)
    unreachable = collecting_handler(logging.WARNING)

    with a_bare_logging_chain():
        DECORATOR_LOGGER.addHandler(logging.NullHandler())
        DECORATOR_LOGGER.propagate = False
        logging.getLogger("clustrix").addHandler(unreachable)
        assert DECORATOR_LOGGER.isEnabledFor(
            logging.WARNING
        ), "the level must be on, or this test would pass for the wrong reason"
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    written = unreachable.stream.getvalue()
    assert (
        "cores=8" not in written
    ), f"propagate=False was set and a record crossed it anyway: {written!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller let records propagate again and was told exactly once; got "
        f"{len(said)}: {said}"
    )


@pytest.mark.parametrize("last_resort_state", ["removed", "raised"])
def test_a_last_resort_that_cannot_emit_does_not_spend_the_budget(
    last_resort_state, caplog, capsys
):
    """With no handlers at all, ``lastResort`` decides -- and it can say no.

    When ``callHandlers`` finds no handler anywhere in the chain it falls back
    to ``logging.lastResort``, a module-level ``_StderrHandler`` that ships at
    ``WARNING``. That fallback is the reason an unconfigured script still sees
    this message, so the gate has to account for it. But it is a global anyone
    may change, and both of the ways it can be silenced are exercised here
    because they are separate halves of one expression: ``logging.lastResort =
    None`` is the documented way to turn the fallback off entirely, and raising
    its level is what an application does when it wants stderr quieter. A gate
    that answered "yes" for a bare chain without asking these questions would
    spend the message into a void on exactly the setup -- no handlers
    configured -- where the caller is least likely to have another way of
    finding out.
    """
    said_once = cluster(cores=8)(pid_of_whole_call)

    with a_bare_logging_chain():
        if last_resort_state == "removed":
            logging.lastResort = None
        else:
            logging.lastResort.setLevel(logging.ERROR)
        assert DECORATOR_LOGGER.isEnabledFor(
            logging.WARNING
        ), "the level must be on, or this test would pass for the wrong reason"
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    stderr = capsys.readouterr().err
    assert (
        "cores=8" not in stderr
    ), f"lastResort was silenced and still wrote the message: {stderr!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "handlers came back and the caller was told exactly once; got "
        f"{len(said)}: {said}"
    )


@pytest.mark.parametrize("attach_to", ["handler", "logger"])
def test_a_filter_that_drops_the_record_does_not_spend_the_budget(attach_to, caplog):
    """A filter can throw the record away after every level test has passed.

    Two of them can, at two different points, and both are pinned because the
    code has to know about both: ``Logger.handle`` runs *this logger's* filters
    before ``callHandlers`` gets the record at all, and ``Handler.handle`` runs
    each handler's filters after the level test and before ``emit``. Level
    checks alone see neither, so a gate built only from levels answers
    "somebody can hear this" and spends the single message on a record that was
    discarded -- #152's silence, again, rebuilt inside the fix for it.

    ``_warning_reaches_someone`` does not run the filter to find out, and says
    so: a filter is arbitrary caller code, and asking it twice per message
    would corrupt any filter that counts or rate-limits. It treats a filter as
    an obstruction instead, which is exact when the filter drops the record --
    the case here -- and pessimistic when the filter passes it, costing a
    repeated message rather than a lost one.
    """

    class DropEverything(logging.Filter):
        def filter(self, record):
            return False

    said_once = cluster(cores=8)(pid_of_whole_call)
    filtered = collecting_handler(logging.WARNING)
    if attach_to == "handler":
        filtered.addFilter(DropEverything())

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(filtered)
        if attach_to == "logger":
            DECORATOR_LOGGER.addFilter(DropEverything())
        assert DECORATOR_LOGGER.isEnabledFor(
            logging.WARNING
        ), "the level must be on, or this test would pass for the wrong reason"
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    written = filtered.stream.getvalue()
    assert (
        "cores=8" not in written
    ), f"a filter said no and the record was emitted anyway: {written!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the filter is gone and the caller was told exactly once; got "
        f"{len(said)}: {said}"
    )


@pytest.mark.parametrize("cores", [True, False])
def test_a_bool_is_not_a_core_count(cores):
    """``bool`` subclasses ``int``, and that is not the caller's problem.

    ``isinstance(True, int)`` is true, so ``@cluster(cores=True)`` passed the
    "is it an integer" check and was read as a request for one worker, and
    ``LocalExecutor(max_workers=True)`` stored ``True`` as its worker count --
    while ``cores=False`` was refused with "must be a positive integer", a
    message that says nothing useful about ``True``, which is not a positive
    integer in any sense the caller means. Both are refused now, and the
    message names the actual reason.
    """
    for construct in (
        lambda: cluster(cores=cores)(pid_of_whole_call),
        lambda: cluster(parallel=True, cores=cores)(pids_of_slice),
        lambda: LocalExecutor(max_workers=cores),
    ):
        with pytest.raises(ValueError, match="bool is not one"):
            construct()


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
