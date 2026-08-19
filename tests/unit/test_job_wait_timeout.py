"""The scheduler wait loop must be bounded.

``_wait_for_scheduler_result`` used to poll under a bare ``while True``. A job
that never reached a terminal state -- held by the scheduler, sitting behind a
queue that never cleared -- hung the caller forever, with no deadline, no
diagnostic and no way out but Ctrl-C.

Nothing here is mocked. ``StuckSchedulerManager`` is a real subclass of the
real ``SchedulerManager``: it overrides one method to report the one thing a
stuck scheduler reports, which is the situation under test. The executor is a
real ``ClusterExecutor`` and the loop it runs is the production loop.
"""

import time

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_core import ClusterExecutor
from clustrix.executor_schedulers import SchedulerManager


class StuckSchedulerManager(SchedulerManager):
    """A scheduler whose job never leaves the queue."""

    def check_job_status(self, job_id: str) -> str:
        return "running"


def _stuck_executor(**config_kwargs) -> ClusterExecutor:
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="hpc.example.invalid",
        username="someone",
        job_poll_interval=1,
        **config_kwargs,
    )
    executor = ClusterExecutor(config)
    executor.scheduler_manager = StuckSchedulerManager(
        config, executor.connection_manager
    )
    executor.scheduler_manager.active_jobs["job_1"] = {
        "remote_dir": "/scratch/someone/.clustrix/jobs/job_1",
    }
    return executor


def test_a_job_that_never_finishes_raises_instead_of_hanging():
    executor = _stuck_executor(job_wait_timeout=2)

    started = time.monotonic()
    with pytest.raises(TimeoutError) as excinfo:
        executor._wait_for_scheduler_result("job_1")
    elapsed = time.monotonic() - started

    # It gave up on its own, near the deadline rather than at some multiple
    # of it, and long before pytest's own timeout would have caught it.
    assert 2 <= elapsed < 10, f"gave up after {elapsed:.1f}s"

    message = str(excinfo.value)
    # Everything the reader needs to act: which job, how long it waited,
    # what it last saw, where the files are, and which knob to turn.
    assert "job_1" in message
    assert "job_wait_timeout" in message
    assert "running" in message
    assert "/scratch/someone/.clustrix/jobs/job_1" in message
    assert "NOT been cancelled" in message


def test_the_default_is_finite():
    """A default of None would leave every existing caller hanging."""
    assert ClusterConfig().job_wait_timeout == 86400


def test_none_restores_the_unbounded_wait():
    """The escape hatch has to actually not have a deadline.

    Verified by observing that it is still polling well past a timeout that
    would have fired -- not by waiting out 24 hours.
    """
    executor = _stuck_executor(job_wait_timeout=None)

    import threading

    done = threading.Event()

    def run():
        try:
            executor._wait_for_scheduler_result("job_1")
        except BaseException:
            pass
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    # Comfortably longer than the 2s deadline used above; if a deadline were
    # applied here, the call would have returned by now.
    assert not done.wait(timeout=5), "an unbounded wait should still be polling"


def test_an_unknown_job_id_is_rejected_before_any_polling():
    executor = _stuck_executor(job_wait_timeout=2)

    started = time.monotonic()
    with pytest.raises(ValueError, match="Unknown job ID"):
        executor._wait_for_scheduler_result("no-such-job")

    assert time.monotonic() - started < 1, "should not have polled at all"
