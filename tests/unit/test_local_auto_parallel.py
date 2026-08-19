#!/usr/bin/env python3
"""Local auto-parallelization must parallelize, or say it is not going to.

Issue #120 item 2. ``_create_local_work_chunks`` hands each worker its slice of
the loop through a keyword argument named ``_parallel_<loop variable>``. It used
to inject that argument unconditionally, so any function that had not declared
it raised ``TypeError: f() got an unexpected keyword argument '_parallel_i'`` on
every chunk. ``_execute_local_parallel`` caught that under a blanket
``except Exception``, logged a warning and re-ran the function sequentially --
so ``auto_parallel`` never parallelized anything locally, and the warning named
a symptom the user could not act on.

The loop analyser only offers a loop whose body reads nothing but the loop
variable (an accumulator such as ``total += i`` is a loop-carried dependency;
see #106/#131). A cooperating function therefore reads ``_parallel_i`` outside
the loop, which is what ``squares_of_slice`` below does.

Nothing here is mocked. Every test runs the real decorator against the real
``LocalExecutor``, which really does dispatch the chunks to a worker pool.
"""

import logging

import pytest

from clustrix import cluster, configure
from clustrix.config import get_config
from clustrix.decorator import _create_local_work_chunks, _execute_local_parallel
from clustrix.loop_analysis import find_parallelizable_loops

N = 64
TOP_SQUARE = (N - 1) * (N - 1)


def squares_of_slice(n, _parallel_i=None):
    """Square exactly the indices this worker was handed.

    ``_parallel_i`` absent means "you are the only worker, do the whole range".
    The ``for`` loop is the one clustrix's analyser detects and splits; its body
    reads nothing but the loop variable, which is what keeps it eligible.
    """
    indices = list(range(n)) if _parallel_i is None else list(_parallel_i)
    top_square = 0
    for i in range(n):
        top_square = i * i
    return {"top_square": top_square, "squares": [(j, j * j) for j in indices]}


def top_square_only(n):
    """Takes no chunk parameter, so it can only ever be run sequentially."""
    top_square = 0
    for i in range(n):
        top_square = i * i
    return top_square


def breaks_only_on_its_chunk(n, _parallel_i=None):
    """Succeeds when run whole, raises ``TypeError`` when handed a slice.

    Stands in for a callee that cannot make sense of the slice it was given.
    The old blanket ``except Exception`` caught that TypeError and re-ran the
    function without the slice, which succeeds -- so the caller got an answer
    and no hint that parallelization had failed. That is the exact shape of the
    bug, so this function is what distinguishes the fix from it.
    """
    top_square = 0
    for i in range(n):
        top_square = i * i
    if _parallel_i is not None:
        raise TypeError("callee cannot use the slice it was given")
    return top_square


@pytest.fixture(autouse=True)
def local_parallel_config():
    """Real global config, restored afterwards."""
    config = get_config()
    saved = (config.cluster_type, config.auto_parallel)
    configure(cluster_type="local", auto_parallel=True)
    yield
    configure(cluster_type=saved[0], auto_parallel=saved[1])


def test_a_chunk_aware_function_is_genuinely_parallelized():
    """The work is split, every slice is distinct, and together they tile it."""
    result = cluster(parallel=True, cores=4)(squares_of_slice)(N)

    # More than one chunk came back: the parallel path ran, not the fallback.
    assert isinstance(result, list), f"expected per-chunk results, got {result!r}"
    assert len(result) > 1, f"work was not split: {len(result)} chunk(s)"

    # Every worker executed the loop clustrix chose to split.
    assert [chunk["top_square"] for chunk in result] == [TOP_SQUARE] * len(result)

    # The slices are disjoint and their union is exactly the whole problem.
    squares = [pair for chunk in result for pair in chunk["squares"]]
    assert len(squares) == N, f"slices overlap or drop work: {len(squares)} != {N}"
    assert sorted(squares) == [(i, i * i) for i in range(N)]

    # And the answer matches the function called directly.
    assert sorted(squares) == sorted(squares_of_slice(N)["squares"])


def test_a_function_without_the_chunk_parameter_is_not_offered_one():
    """No chunks are built, so no unanswerable call is ever made."""
    loops = find_parallelizable_loops(top_square_only, (N,), {})
    assert loops, "top_square_only has a loop clustrix considers parallelizable"

    assert _create_local_work_chunks(top_square_only, (N,), {}, loops[0]) == []


def test_a_function_without_the_chunk_parameter_still_returns_the_right_answer():
    """Declining to parallelize must not change the answer."""
    assert cluster(parallel=True, cores=4)(top_square_only)(N) == TOP_SQUARE
    assert top_square_only(N) == TOP_SQUARE


def test_declining_to_parallelize_is_reported_as_a_decision_not_a_failure(caplog):
    """The old path logged a TypeError warning; there is no error to report."""
    with caplog.at_level(logging.INFO, logger="clustrix.decorator"):
        assert cluster(parallel=True, cores=4)(top_square_only)(N) == TOP_SQUARE

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Not parallelizing top_square_only locally" in message for message in messages
    ), messages
    assert not any(
        "falling back to sequential" in message for message in messages
    ), messages
    assert not any("unexpected keyword argument" in message for message in messages)


def test_a_callee_that_breaks_on_its_chunk_raises_instead_of_being_swallowed():
    """``TypeError`` on the parallel path is a bug, not a runtime condition.

    Run sequentially this function returns ``TOP_SQUARE``, which is what the old
    fallback handed back. Getting that value now would mean the failure was
    absorbed again.
    """
    loops = find_parallelizable_loops(breaks_only_on_its_chunk, (N,), {})
    assert loops, "breaks_only_on_its_chunk has a parallelizable loop"
    chunks = _create_local_work_chunks(breaks_only_on_its_chunk, (N,), {}, loops[0])
    assert len(chunks) > 1, "the chunk-accepting signature must be offered chunks"

    assert breaks_only_on_its_chunk(N) == TOP_SQUARE

    with pytest.raises(TypeError, match="cannot use the slice"):
        _execute_local_parallel(breaks_only_on_its_chunk, (N,), {}, {"cores": 4})
