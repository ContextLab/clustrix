"""``@cluster`` dividing a loop into jobs on an SSH host.

The host is the real in-process SSH server of ``tests/ssh_server.py``: a
real handshake, real SFTP uploads, a real shell running real job scripts in
fresh interpreters. What ran where is read from what the jobs themselves
report (their pid and working directory) and from the server's log of the
commands it was asked to run -- one ``mkdir -m 700 .../job_*`` per job
submitted.

Functions are defined inside the tests so that they travel by value: the
"remote" interpreter cannot import this module.
"""

import logging
import os
import sys

import pytest

from clustrix import cluster, configure, explain
from tests.ssh_server import LocalSSHServer

pytestmark = pytest.mark.timeout(300)

PASSWORD = "s3cret"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """One server for the module, so the remote environment is built once."""
    root = tmp_path_factory.mktemp("remote")
    with LocalSSHServer(root=str(root), password=PASSWORD) as running:
        running.jobs_dir = str(root / "jobs")
        yield running


@pytest.fixture
def ssh(server):
    """Point clustrix at the server. ``reset_config`` undoes it afterwards."""
    configure(
        cluster_type="ssh",
        cluster_host=server.host,
        cluster_port=server.port,
        username="tester",
        password=PASSWORD,
        # The server's key is generated per run; verifying host keys is
        # tests/unit/test_host_key_policy.py's subject, not this module's.
        ssh_host_key_policy="auto_add",
        remote_work_dir=server.jobs_dir,
        replicate_local_environment=False,
        python_executable=sys.executable,
        job_poll_interval=1,
    )
    return server


def jobs_submitted(server, since):
    """How many job directories the server was asked to create after ``since``."""
    return sum(
        1 for command in server.commands[since:] if command.startswith("mkdir -m 700 ")
    )


def squares_where(values):
    out = []
    for value in values:
        import os

        out.append((value * value, os.getpid(), os.getcwd()))
    return out


@pytest.mark.parametrize(
    "count, max_jobs, expected_jobs",
    [(7, 3, 3), (2, 5, 2)],
    ids=["more-items-than-jobs", "fewer-items-than-jobs"],
)
def test_a_loop_becomes_one_job_per_chunk_and_returns_the_direct_answer(
    ssh, count, max_jobs, expected_jobs
):
    configure(max_parallel_jobs=max_jobs)
    values = list(range(count))
    before = len(ssh.commands)

    result = cluster(cores=1)(squares_where)(values)

    assert [v for v, _, _ in result] == [v for v, _, _ in squares_where(values)]
    assert jobs_submitted(ssh, before) == expected_jobs == min(count, max_jobs)
    # Each chunk ran in its own process, in its own job directory, on the host.
    assert len({pid for _, pid, _ in result}) == expected_jobs
    directories = {cwd for _, _, cwd in result}
    assert len(directories) == expected_jobs
    assert all(d.startswith(ssh.jobs_dir + "/job_") for d in directories), directories
    assert os.getpid() not in {pid for _, pid, _ in result}
    # Contiguous chunks, in order: the job changes exactly jobs - 1 times.
    in_order = [cwd for _, _, cwd in result]
    changes = sum(1 for a, b in zip(in_order, in_order[1:]) if a != b)
    assert changes == expected_jobs - 1, in_order


def test_a_failing_iteration_raises_its_own_exception_here(ssh):
    configure(max_parallel_jobs=3)

    @cluster(cores=1)
    def checked(values):
        out = []
        for value in values:
            if value == 4:
                raise LookupError(f"nothing is filed under {value}")
            out.append(value)
        return out

    with pytest.raises(LookupError, match="nothing is filed under 4"):
        checked(list(range(6)))


def test_work_before_and_after_the_loop_runs_on_the_host_not_here(ssh):
    """Code around the loop that does real work is a job of its own.

    ``squares_where`` above has nothing around its loop but a list and a
    return, and costs exactly one job per chunk. This function calls into
    ``os`` before and after, so both ends must have run remotely too.
    """
    configure(max_parallel_jobs=2)

    @cluster(cores=1)
    def surveyed(values):
        import os

        started_in = (os.getpid(), os.getcwd())
        out = []
        for value in values:
            out.append((value + 1, os.getpid()))
        finished_in = (os.getpid(), os.getcwd())
        return started_in, out, finished_in

    account = explain(surveyed)
    assert "code before the loop runs as its own job" in account
    assert "code after the loop runs as its own job" in account

    before = len(ssh.commands)
    started_in, out, finished_in = surveyed([1, 2, 3, 4])

    assert [value for value, _ in out] == [2, 3, 4, 5]
    assert jobs_submitted(ssh, before) == 1 + 2 + 1, "before, two chunks, after"
    pids = [started_in[0], *sorted({pid for _, pid in out}), finished_in[0]]
    assert len(set(pids)) == 4 and os.getpid() not in pids, pids
    for _, cwd in (started_in, finished_in):
        assert cwd.startswith(ssh.jobs_dir + "/job_"), cwd
    assert started_in[1] != finished_in[1]


def test_a_loop_that_cannot_be_divided_is_one_job(ssh, caplog):
    @cluster(cores=1)
    def running_total(values):
        import os

        total = 0
        seen = []
        for value in values:
            total = total + value
            seen.append(total)
        return seen, os.getpid()

    before = len(ssh.commands)
    with caplog.at_level(logging.INFO, logger="clustrix.decorator"):
        seen, pid = running_total([1, 2, 3, 4])

    assert seen == [1, 3, 6, 10] and pid != os.getpid()
    assert jobs_submitted(ssh, before) == 1
    assert "running_total runs as one job" in caplog.text
    assert "carries a value from the previous iteration" in caplog.text


def test_a_split_found_unsafe_on_the_host_is_rerun_whole_and_says_so(ssh, caplog):
    """The chunks report that they changed something they share; the loop
    then runs once more, in order, as a single job."""
    configure(max_parallel_jobs=2)

    @cluster(cores=1)
    def tallied(values, counter):
        def bump(amount):
            counter["total"] += amount
            return counter["total"]

        out = []
        for value in values:
            out.append(bump(value))
        return out

    before = len(ssh.commands)
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = tallied([1, 2, 3, 4], {"total": 0})

    assert result == [1, 3, 6, 10]
    assert "tallied: not split -- the loop changed" in caplog.text
    # pre (it defines bump), two chunks, then the whole loop again.
    assert jobs_submitted(ssh, before) == 4
