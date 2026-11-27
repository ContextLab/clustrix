"""End-to-end runs against a real sshd and a real SLURM controller.

Nothing here is simulated. ``scripts/setup_local_slurm.sh`` stands up an
OpenSSH server and a single-node SLURM cluster (munge, slurmdbd, slurmctld,
slurmd) on one machine; these tests then drive the shipped ``@cluster`` path
at it: real key authentication, real SFTP, real virtualenv builds, real
``sbatch`` / ``squeue`` / ``sacct`` / ``scancel``.

They are skipped, with the reason, unless the environment names the host::

    sudo scripts/setup_local_slurm.sh
    export CLUSTRIX_TEST_SSH_HOST=127.0.0.1 CLUSTRIX_TEST_SSH_PORT=2222 \\
           CLUSTRIX_TEST_SSH_USER=clx \\
           CLUSTRIX_TEST_SSH_KEY=/tmp/clustrix-local-cluster/id_ed25519
    pytest tests/real_world/test_local_sshd_slurm.py -m real_world

The ``slurm`` half is additionally skipped when the host has no ``sbatch``.
The first test against each backend builds the remote environment, which
needs the host to reach a package index and takes about half a minute.
"""

import os
import subprocess
import sys
import threading
import time
import traceback

import pytest

from clustrix import cluster, configure, get_config
from clustrix.executor import ClusterExecutor
from clustrix.executor_scheduler_status import RemoteTraceback
from clustrix.utils import serialize_function

HOST = os.environ.get("CLUSTRIX_TEST_SSH_HOST")
PORT = int(os.environ.get("CLUSTRIX_TEST_SSH_PORT", "22"))
USER = os.environ.get("CLUSTRIX_TEST_SSH_USER")
KEY = os.environ.get("CLUSTRIX_TEST_SSH_KEY")

REQUIRED = "CLUSTRIX_TEST_SSH_HOST, CLUSTRIX_TEST_SSH_USER and CLUSTRIX_TEST_SSH_KEY"

pytestmark = [
    pytest.mark.skipif(
        not (HOST and USER and KEY),
        reason=(
            f"No local test cluster named: set {REQUIRED} "
            "(scripts/setup_local_slurm.sh creates one)."
        ),
    ),
    pytest.mark.timeout(600),
]

RESOURCES = dict(cores=1, memory="200MB", time="00:05:00")


def _remote(command: str) -> subprocess.CompletedProcess:
    """Run ``command`` on the test host with the system ssh client."""
    return subprocess.run(
        [
            "ssh",
            "-i",
            str(KEY),
            "-p",
            str(PORT),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "BatchMode=yes",
            f"{USER}@{HOST}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture(scope="module")
def host_facts():
    """Whether the host answers, and whether it has SLURM."""
    try:
        probe = _remote("command -v sbatch || true; echo reachable")
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"The test host {HOST}:{PORT} could not be reached: {exc}")
    if "reachable" not in probe.stdout:
        pytest.skip(
            f"The test host {HOST}:{PORT} refused the login: {probe.stderr.strip()}"
        )
    return {"slurm": "sbatch" in probe.stdout}


@pytest.fixture(params=["ssh", "slurm"])
def backend(request, host_facts):
    """Configure clustrix for one backend on the test host."""
    if request.param == "slurm" and not host_facts["slurm"]:
        pytest.skip(f"The test host {HOST} has no sbatch, so SLURM cannot be tested.")
    configure(
        cluster_type=request.param,
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        # Each test gets a fresh HOME, so the host can never already be known.
        ssh_host_key_policy="auto_add",
        remote_work_dir="~/clustrix-realworld-tests",
        job_poll_interval=1,
        job_wait_timeout=300,
    )
    return request.param


def module_level_helper(x):
    return x + 100


class Point:
    def __init__(self, x, y):
        self.x, self.y = x, y


class JobError(Exception):
    pass


def test_a_function_runs_remotely_and_returns_its_value(backend):
    @cluster(**RESOURCES)
    def job(a, b=3):
        import getpass
        import os

        return a * b, os.getpid(), getpass.getuser(), os.environ.get("SLURM_JOB_ID")

    value, pid, user, slurm_job = job(5, b=4)

    assert value == 20
    assert pid != os.getpid(), "the function ran in this process"
    assert user == USER
    assert (slurm_job is not None) == (backend == "slurm")


def test_helpers_closures_and_classes_from_this_file_travel(backend):
    offset = 7

    @cluster(**RESOURCES)
    def job(point):
        square = lambda v: v * v + offset  # noqa: E731
        return Point(module_level_helper(point.x), square(point.y))

    out = job(Point(1, 3))

    assert (out.x, out.y) == (101, 16)


def test_third_party_arguments_and_results_cross_both_ways(backend):
    """A numpy array in and out. The serialization environment has no numpy."""
    np = pytest.importorskip("numpy")

    @cluster(**RESOURCES)
    def job(values):
        return np.cumsum(values)

    out = job(np.arange(4))

    assert isinstance(out, np.ndarray)
    assert out.tolist() == [0, 1, 3, 6]


def test_declared_data_arrives_on_the_worker(backend, tmp_path):
    """A file, a directory and 8 MB of bytes, named with ``data_package``."""
    import hashlib

    import clustrix

    (tmp_path / "tree" / "sub").mkdir(parents=True)
    (tmp_path / "tree" / "a.txt").write_text("one\n")
    (tmp_path / "tree" / "sub" / "b.txt").write_text("two\n")
    (tmp_path / "small.csv").write_text("alpha,1\n")
    blob = os.urandom(8 * 1024 * 1024)
    (tmp_path / "big.bin").write_bytes(blob)

    small = clustrix.data_package(str(tmp_path / "small.csv"))
    tree = clustrix.data_package(str(tmp_path / "tree"))
    # Above the inline threshold a package goes to a private HuggingFace
    # repo, which a worker without clustrix cannot read; force_local carries
    # the bytes with the job instead.
    big = clustrix.data_package(str(tmp_path / "big.bin"), force_local=True)

    @cluster(cores=1, memory="500MB", time="00:05:00")
    def job(small, tree, big):
        import hashlib

        with open(big.path("big.bin"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        return (
            open(small.path("small.csv")).read(),
            sorted(tree.filenames()),
            open(tree.path("sub/b.txt")).read(),
            digest,
            big.path("big.bin"),
        )

    text, names, nested, digest, where = job(small, tree, big)

    assert text == "alpha,1\n"
    assert names == ["a.txt", "sub/b.txt"]
    assert nested == "two\n"
    assert digest == hashlib.sha256(blob).hexdigest()
    assert (
        "/clustrix-realworld-tests/job_" in where
    ), "the data was not unpacked on the worker"


def test_only_what_the_job_imports_is_installed_when_mirroring_is_off(backend):
    """replicate_local_environment=False builds from the detected imports."""
    pytest.importorskip("yaml")
    configure(replicate_local_environment=False)

    @cluster(**RESOURCES)
    def job():
        import importlib.metadata

        import yaml

        installed = {
            d.metadata["Name"].lower() for d in importlib.metadata.distributions()
        }
        return yaml.safe_load("a: 1"), installed

    loaded, installed = job()

    assert loaded == {"a": 1}
    assert "pyyaml" in installed
    assert "pytest" not in installed, "the whole local environment was mirrored"


def test_the_second_job_reuses_the_environment_the_first_built(backend):
    @cluster(**RESOURCES)
    def job():
        import sys

        return sys.executable

    first = job()
    started = time.monotonic()
    second = job()
    elapsed = time.monotonic() - started

    assert first == second
    assert "/envs/" in first and "/job_" not in first
    assert elapsed < 20, f"a job against a built environment took {elapsed:.0f}s"


def test_environment_settings_reach_the_job(backend):
    configure(
        environment_variables={"CLUSTRIX_RW_FLAG": "two words"},
        pre_execution_commands=["export CLUSTRIX_RW_PRE=$(echo ran)"],
    )

    @cluster(**RESOURCES)
    def job():
        import os
        import shutil
        import sys

        return (
            os.environ.get("CLUSTRIX_RW_FLAG"),
            os.environ.get("CLUSTRIX_RW_PRE"),
            shutil.which("python") == sys.executable,
            os.getcwd(),
        )

    flag, pre, activated, cwd = job()

    assert (flag, pre) == ("two words", "ran")
    assert activated, "the execution environment was not activated"
    assert "/clustrix-realworld-tests/job_" in cwd


def test_the_job_directory_is_removed_on_success_unless_asked_not_to(backend):
    def count():
        return int(
            _remote("ls ~/clustrix-realworld-tests | grep -c '^job_'").stdout or 0
        )

    @cluster(**RESOURCES)
    def job():
        return "done"

    before = count()
    assert job() == "done"
    assert count() == before

    configure(cleanup_on_success=False)
    assert job() == "done"
    assert count() == before + 1
    _remote("rm -rf ~/clustrix-realworld-tests/job_*")


def test_an_exception_arrives_as_itself_with_the_remote_traceback(backend):
    @cluster(**RESOURCES)
    def job():
        raise JobError("boom-123")

    with pytest.raises(JobError, match="boom-123") as caught:
        job()

    assert isinstance(caught.value.__cause__, RemoteTraceback)
    text = "".join(traceback.format_exception(caught.value))
    assert "in job" in text and "JobError: boom-123" in text


def test_a_function_that_exits_is_a_failure_that_says_so(backend):
    @cluster(**RESOURCES)
    def job():
        import sys

        sys.exit(3)

    with pytest.raises(RuntimeError) as caught:
        job()

    assert "3" in str(caught.value)
    assert "result_raw.pkl not found" not in str(caught.value)


def test_a_killed_function_is_a_failure_not_a_hang(backend):
    @cluster(**RESOURCES)
    def job():
        import os
        import signal

        os.kill(os.getpid(), signal.SIGKILL)

    started = time.monotonic()
    with pytest.raises(RuntimeError) as caught:
        job()

    assert time.monotonic() - started < 120
    message = str(caught.value)
    assert "SIGKILL" in message or "exit_code=9" in message or "Killed" in message


def test_an_unpicklable_result_is_reported_as_that(backend):
    @cluster(**RESOURCES)
    def job():
        return (i for i in range(3))

    with pytest.raises(TypeError, match="pickle"):
        job()


def test_output_on_stderr_is_not_a_failure(backend):
    @cluster(**RESOURCES)
    def job():
        import sys
        import time
        import warnings

        warnings.warn("only a warning")
        print("noise", file=sys.stderr, flush=True)
        time.sleep(4)
        return "survived"

    assert job() == "survived"


def test_five_jobs_at_once_do_not_collide(backend):
    @cluster(**RESOURCES)
    def job(i):
        import time

        time.sleep(1)
        return i * i

    results, errors = {}, []

    def run(i):
        try:
            results[i] = job(i)
        except Exception as exc:  # collected and asserted on below
            errors.append(repr(exc))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert results == {i: i * i for i in range(5)}


def test_async_submit_returns_a_handle_that_resolves(backend):
    @cluster(async_submit=True, **RESOURCES)
    def job(a):
        return a + 1

    handle = job(41)

    assert handle.get_result(timeout=300) == 42


def test_cancel_stops_the_remote_process(backend):
    def sleeper():
        import time

        time.sleep(300)

    running = "ps -u $(id -u) -o args | grep -c '[v]env2_execution/bin/python'"
    with ClusterExecutor(get_config()) as executor:
        job_id = executor.submit_job(
            serialize_function(sleeper, (), {}), dict(RESOURCES)
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if executor.get_job_status(job_id) == "running" and (
                _remote(running).stdout.strip() == "1"
            ):
                break
            time.sleep(1)
        assert _remote(running).stdout.strip() == "1", "the job never started"

        executor.cancel_job(job_id)

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and _remote(running).stdout.strip() != "0":
            time.sleep(1)
        assert _remote(running).stdout.strip() == "0", "the job outlived its cancel"


def test_configure_after_decorating_changes_where_the_next_call_runs(backend):
    @cluster(**RESOURCES)
    def where():
        import os

        return os.getpid()

    remote_config = {
        name: getattr(get_config(), name)
        for name in ("cluster_type", "cluster_host", "cluster_port")
    }

    assert where() != os.getpid()
    configure(cluster_type="local", cluster_host=None)
    assert where() == os.getpid()
    configure(**remote_config)
    assert where() != os.getpid()


def test_running_past_the_time_limit_is_reported_as_a_timeout(host_facts, backend):
    if backend != "slurm":
        pytest.skip("Only a scheduler enforces a time limit; plain ssh has none.")

    @cluster(cores=1, memory="200MB", time="00:01:00")
    def job():
        import time

        time.sleep(600)

    with pytest.raises(RuntimeError, match="TIMEOUT"):
        job()


def test_a_request_the_scheduler_refuses_is_reported_with_its_reason(backend):
    if backend != "slurm":
        pytest.skip("Only a scheduler can refuse a submission.")

    @cluster(cores=1, memory="200MB", time="00:05:00", partition="no-such-partition")
    def job():
        return 1

    with pytest.raises(RuntimeError, match="(?i)sbatch did not accept.*partition"):
        job()


@pytest.mark.parametrize(
    "override, expected",
    [
        ({"cluster_port": 1}, "connect"),
        ({"key_file": "/nonexistent/clustrix-test-key"}, "No such file"),
        ({"ssh_host_key_policy": "reject"}, "known_hosts"),
    ],
    ids=["wrong-port", "missing-key", "unknown-host-key"],
)
def test_a_connection_that_cannot_be_made_fails_promptly(backend, override, expected):
    configure(**override)

    @cluster(**RESOURCES)
    def job():
        import os

        return os.getpid()

    started = time.monotonic()
    with pytest.raises(Exception) as caught:
        job()

    assert time.monotonic() - started < 60
    assert expected.lower() in f"{type(caught.value).__name__} {caught.value}".lower()
    assert sys.exc_info()[0] is None


# --------------------------------------------------------------------------
# A loop divided into jobs
# --------------------------------------------------------------------------


def _slurm_jobs_that_ran(job_ids):
    """What the accounting database says about ``job_ids``: {id: state}."""
    listing = _remote(
        f"sacct -X -n -P -o JobID,State -j {','.join(sorted(job_ids))}"
    ).stdout
    return dict(line.split("|")[:2] for line in listing.split() if "|" in line)


def _wait_for_accounting(job_ids, seconds=30):
    """slurmdbd records a finished job a moment after the job's result is back."""
    deadline = time.monotonic() + seconds
    while True:
        states = _slurm_jobs_that_ran(job_ids)
        finished = all(states.get(job_id) == "COMPLETED" for job_id in job_ids)
        if finished or time.monotonic() > deadline:
            return states
        time.sleep(1)


def test_a_loop_is_divided_into_jobs_and_returns_the_direct_answer(backend):
    configure(max_parallel_jobs=4)

    def where(values):
        out = []
        for value in values:
            import os

            out.append((value * value, os.getpid(), os.environ.get("SLURM_JOB_ID")))
        return out

    values = list(range(6))
    result = cluster(**RESOURCES)(where)(values)

    assert [v for v, _, _ in result] == [v for v, _, _ in where(values)]
    pids = {pid for _, pid, _ in result}
    assert len(pids) == 4 and os.getpid() not in pids, "four chunks, four processes"

    slurm_ids = {job_id for _, _, job_id in result}
    if backend == "ssh":
        assert slurm_ids == {None}
        return
    assert None not in slurm_ids and len(slurm_ids) == 4, slurm_ids
    states = _wait_for_accounting(slurm_ids)
    assert states == dict.fromkeys(
        slurm_ids, "COMPLETED"
    ), f"sacct -X does not show four completed jobs: {states}"


def test_a_loop_that_cannot_be_divided_is_one_job(backend):
    @cluster(**RESOURCES)
    def running_total(values):
        import os

        total, seen = 0, []
        for value in values:
            total = total + value
            seen.append((total, os.getpid(), os.environ.get("SLURM_JOB_ID")))
        return seen

    result = running_total([1, 2, 3, 4])

    assert [total for total, _, _ in result] == [1, 3, 6, 10]
    assert len({pid for _, pid, _ in result}) == 1
    assert os.getpid() not in {pid for _, pid, _ in result}
    slurm_ids = {job_id for _, _, job_id in result}
    assert len(slurm_ids) == 1 and (slurm_ids == {None}) == (backend == "ssh")


def test_a_split_found_unsafe_on_the_cluster_falls_back_to_the_direct_answer(
    backend, caplog
):
    """Each chunk reports that it changed a value its iterations share; the
    loop is then run again whole, in order, as one job."""
    configure(max_parallel_jobs=2)

    def tallied(values, counter):
        def bump(amount):
            counter["total"] += amount
            return counter["total"]

        out = []
        for value in values:
            out.append(bump(value))
        return out

    expected = tallied([1, 2, 3, 4], {"total": 10})

    with caplog.at_level("WARNING", logger="clustrix.decorator"):
        result = cluster(**RESOURCES)(tallied)([1, 2, 3, 4], {"total": 10})

    assert result == expected == [11, 13, 16, 20]
    assert "tallied: not split -- the loop changed" in caplog.text
