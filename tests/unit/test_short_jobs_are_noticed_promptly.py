"""A job that takes a second must not take thirty to come back.

``ClusterConfig.job_poll_interval`` defaults to 30 seconds, and the wait for
a result used to sleep exactly that long between status checks -- so every
job, however short, cost at least one full interval, and a loop divided
into chunks paid it per chunk. Polls now start a second apart and spread
out to the configured interval, which becomes their ceiling.

Run against the real in-process SSH server: a real submission, a real job
script, a real result file.
"""

import sys
import time
from dataclasses import asdict

import pytest

from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from clustrix.executor_core import FIRST_POLL_SECONDS, POLL_BACKOFF
from tests.ssh_server import LocalSSHServer


@pytest.fixture
def ssh_backend(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    with LocalSSHServer(root=str(remote), password="s3cret") as server:
        configure(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password="s3cret",
            ssh_host_key_policy="auto_add",
            remote_work_dir=str(remote / "jobs"),
            replicate_local_environment=False,
            python_executable=sys.executable,
        )
        yield server
    configure(**asdict(ClusterConfig()))


def test_the_shipped_interval_is_still_thirty_seconds():
    """The premise of the test below: nothing here shortened the setting."""
    assert ClusterConfig().job_poll_interval == 30
    assert FIRST_POLL_SECONDS <= 1 and POLL_BACKOFF > 1


def test_a_quick_job_returns_well_inside_one_poll_interval(ssh_backend):
    @cluster(cores=1, parallel=False)
    def quick():
        return "done"

    started = time.monotonic()
    assert quick() == "done"
    elapsed = time.monotonic() - started

    assert elapsed < 20, (
        f"a trivial job took {elapsed:.0f}s with job_poll_interval=30: the "
        "wait is sleeping a whole interval before looking"
    )


def test_the_configured_interval_caps_the_wait_between_polls(ssh_backend):
    """Backing off must stop at the user's setting, not run past it."""
    configure(job_poll_interval=2)

    @cluster(cores=1, parallel=False)
    def slow():
        import time

        time.sleep(9)
        return "done"

    assert slow() == "done"
    # The ssh backend's status check is the command that looks for job.exit.
    status_checks = [c for c in ssh_backend.commands if "[ -f job.exit ]" in c]

    # 9 seconds at a ceiling of 2s is at least five polls (1, 1.5, 2, 2, 2,
    # ...); backing off without the ceiling (1, 1.5, 2.25, 3.4, 5.1) makes
    # four before the job is done.
    assert len(status_checks) >= 5, len(status_checks)
