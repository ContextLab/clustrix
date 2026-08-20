#!/usr/bin/env python3
"""``@cluster(cores=N)`` locally: honoured where it can be, reported where it cannot.

Issue #152. ``cores=8`` used to be accepted on every local route and dropped on
almost all of them -- the work ran in the caller's own process, one core, no
message. Only one local route can use the number at all: the parallel path,
which hands ``cores`` to ``LocalExecutor`` as ``max_workers``. There a single
call is split into chunks, so a second worker has something to do.

Everywhere else a local call is one unit of work and there is nothing to split.
That is a limitation; the silence about it was the defect, in the same family as
the fabricated-result bugs this project has spent months removing. So the tests
below come in two kinds:

* the parallel path must *observably* leave this process -- distinct worker
  pids, bounded by ``cores`` -- rather than merely returning an answer;
* every other local route must say, out loud, that the request was discarded.

Nothing here is mocked. Every test drives the real decorator, the real
``LocalExecutor`` pool and the real ``LocalJobManager``.
"""

import inspect
import logging
import os

import pytest

from clustrix import cluster, configure
from clustrix.config import get_config
from clustrix.local_executor import LocalJobManager
from clustrix.utils import serialize_function

N = 64
TOP_MARKER = (N - 1) * (N - 1)


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


def pid_of_whole_call(n):
    """Take no chunk keyword, so this can only ever run as one unit of work."""
    marker = 0
    for i in range(n):
        marker = i * i
    return {"marker": marker, "pid": os.getpid()}


@pytest.fixture(autouse=True)
def local_config():
    """Real global config, restored afterwards."""
    config = get_config()
    saved = (config.cluster_type, config.cluster_host, config.auto_parallel)
    configure(cluster_type="local", cluster_host=None, auto_parallel=False)
    yield
    configure(cluster_type=saved[0], cluster_host=saved[1], auto_parallel=saved[2])


def worker_pids(result):
    """The set of processes that produced a chunked result."""
    assert isinstance(result, list), f"expected per-chunk results, got {result!r}"
    return {pid for chunk in result for _, pid in chunk["pids"]}


def warnings_from(caplog):
    return [record.getMessage() for record in caplog.records]


def test_the_parallel_path_really_runs_outside_this_process():
    """The observed degree of parallelism, not the fact that a value came back."""
    result = cluster(parallel=True, cores=4)(pids_of_slice)(N)

    assert len(result) > 1, f"work was not split: {len(result)} chunk(s)"
    pids = worker_pids(result)

    assert os.getpid() not in pids, (
        "the work ran in the caller's own process -- this is exactly the "
        f"evidence in #152 ({pids})"
    )
    assert len(pids) > 1, f"only one worker did anything: {pids}"

    # Parallelism that loses or duplicates work is not a win.
    indices = sorted(j for chunk in result for j, _ in chunk["pids"])
    assert indices == list(range(N))
    assert [chunk["marker"] for chunk in result] == [TOP_MARKER] * len(result)


def test_cores_bounds_the_worker_pool():
    """``cores`` is what sizes the pool, so changing it changes what is observed.

    ``cores=2`` may never produce three workers. If ``max_workers`` stopped
    being wired through, the pool would fall back to ``os.cpu_count()`` and
    this first assertion fails on any machine with more than two CPUs; the
    second catches the same regression on a two-CPU machine.
    """
    two = worker_pids(cluster(parallel=True, cores=2)(pids_of_slice)(N))
    assert len(two) <= 2, f"cores=2 was not respected: {len(two)} workers {two}"

    if (os.cpu_count() or 1) < 2:
        pytest.skip("a single-CPU machine yields too few chunks to grow a pool")

    eight = worker_pids(cluster(parallel=True, cores=8)(pids_of_slice)(N))
    assert len(eight) > 2, f"cores=8 grew no wider than cores=2: {eight}"


def test_the_sequential_local_path_says_it_is_ignoring_cores(caplog):
    """No loop was split, so the eight workers were never going to exist."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=8)(pid_of_whole_call)(N)

    assert result["pid"] == os.getpid(), "the local path does run here"
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "has no effect here" in message for message in messages
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


def test_nothing_is_reported_when_nothing_was_requested(caplog):
    """A global ``default_cores`` is not a per-call instruction, and cores=1 is no ask."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(pid_of_whole_call)(N)["pid"] == os.getpid()
        assert cluster(cores=1)(pid_of_whole_call)(N)["pid"] == os.getpid()

    assert get_config().default_cores > 1, "default_cores is what would go unnoticed"
    assert not any(
        "has no effect here" in message for message in warnings_from(caplog)
    ), warnings_from(caplog)


def test_local_job_manager_runs_the_job_without_building_a_pool():
    """Its ``LocalExecutor(max_workers=cores, use_threads=True)`` was inert.

    ``execute_single`` is a bare ``func(*args, **kwargs)``: it never calls
    ``_create_executor``, so neither constructor argument was ever read. A pool
    object built and then not used is what made ``cores`` look honoured here.
    """
    source = inspect.getsource(LocalJobManager.submit_job)
    assert "LocalExecutor(" not in source, source

    manager = LocalJobManager(get_config())
    job_id = manager.submit_job(
        serialize_function(pid_of_whole_call, (N,), {}), {"cores": 8}
    )
    assert manager.get_job_status(job_id) == "completed"
    assert manager.wait_for_result(job_id) == {
        "marker": TOP_MARKER,
        "pid": os.getpid(),
    }
