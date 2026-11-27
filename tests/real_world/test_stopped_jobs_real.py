"""What the user is told when a running job is stopped from outside.

A real job is started on a real host and then really stopped: ``scancel``
for SLURM, ``kill -9`` of the function's own process for both backends
(which is what an out-of-memory kill looks like). The error used to be the
whole of the job's stderr -- and when a process is killed, bash prints the
command it was running, which is the eighty-line program clustrix
generated.

Run as ``tests/real_world/test_local_sshd_slurm.py`` describes.
"""

import time

import pytest

from clustrix import get_config
from clustrix.executor import ClusterExecutor
from clustrix.executor_scheduler_status import JobFailedError
from clustrix.utils import serialize_function
from tests.real_world.test_local_sshd_slurm import (  # noqa: F401
    HOST,
    RESOURCES,
    _remote,
    backend,
    host_facts,
    pytestmark,
)


def announce_then_sleep():
    import sys
    import time

    print("starting the long part", file=sys.stderr, flush=True)
    time.sleep(300)


def _function_pids(remote_dir: str) -> list:
    """Pids of the function's interpreter for *this* job, found by its
    working directory -- other jobs on the host run the same interpreter."""
    found = _remote(
        "for p in $(pgrep -u $(id -u) -f venv2_execution/bin/python); do "
        f'[ "$(readlink /proc/$p/cwd)" = {remote_dir} ] && echo $p; done; true'
    )
    return found.stdout.split()


def _start_and_stop(stop) -> tuple:
    """Run the sleeper, call ``stop(job_id, pid)`` once it is running, and
    return ``(job_id, remote_dir, the exception waiting for it raised)``."""
    with ClusterExecutor(get_config()) as executor:
        job_id = executor.submit_job(
            serialize_function(announce_then_sleep, (), {}), dict(RESOURCES)
        )
        remote_dir = executor.scheduler_manager.active_jobs[job_id]["remote_dir"]
        deadline = time.monotonic() + 90
        while not _function_pids(remote_dir) and time.monotonic() < deadline:
            time.sleep(1)
        pids = _function_pids(remote_dir)
        assert len(pids) == 1, f"the job's function never started: {pids}"

        stop(job_id, pids[0])

        with pytest.raises(JobFailedError) as caught:
            executor.wait_for_result(job_id)
    print(f"\n----- what the user sees -----\n{caught.value}\n-----")
    return job_id, remote_dir, caught.value


def _assert_short_and_complete(error, job_id, remote_dir):
    message = str(error)
    assert isinstance(error, RuntimeError)
    assert len(message.splitlines()) <= 12, message
    assert f"Job {job_id} " in message.splitlines()[0]
    assert remote_dir in message and HOST in message
    assert "starting the long part" in message, "the job's own stderr is gone"
    assert "import dill" not in message, "the generated program is in the message"
    # Nothing is thrown away: the whole log is on the exception.
    assert (error.job_id, error.remote_dir) == (job_id, remote_dir)
    assert "starting the long part" in error.error_log


def test_a_killed_function_is_reported_in_a_few_lines(backend):
    job_id, remote_dir, error = _start_and_stop(
        lambda _job_id, pid: _remote(f"kill -9 {pid}")
    )

    _assert_short_and_complete(error, job_id, remote_dir)
    message = str(error)
    assert "SIGKILL" in message or "exit_code=9" in message or "Killed" in message
    # bash echoed the program into the log; the message folds it to a line.
    assert "import dill" in error.error_log
    assert len(error.error_log.splitlines()) > 50
    assert '-c "<the job program, ' in message


def test_a_cancelled_slurm_job_says_it_was_cancelled(backend):
    if backend != "slurm":
        pytest.skip("scancel is SLURM's; a killed ssh job is the test above.")

    job_id, remote_dir, error = _start_and_stop(
        lambda job_id, _pid: _remote(f"scancel {job_id}")
    )

    _assert_short_and_complete(error, job_id, remote_dir)
    assert str(error).startswith(f"Job {job_id} was cancelled (")
    assert "CANCELLED" in str(error)
