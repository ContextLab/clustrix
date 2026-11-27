"""The resources a ``@cluster`` call asks for are the ones SLURM grants.

``tests/test_integration.py`` used to "test" this by decorating a function
with ``cores=16`` and asserting ``_cluster_config["cores"] == 16`` -- a
number read back from where the test had just put it. Whether it ever
reached a scheduler was not looked at.

Here the job itself reports what it was given: SLURM exports the allocation
into the job's environment, and ``scontrol`` reports its time limit. Run
against the real SLURM controller ``scripts/setup_local_slurm.sh`` sets up,
and skipped, with the reason, when the environment names no such host.
"""

import os
import subprocess

import pytest

from clustrix import cluster, configure

HOST = os.environ.get("CLUSTRIX_TEST_SSH_HOST")
PORT = int(os.environ.get("CLUSTRIX_TEST_SSH_PORT", "22"))
USER = os.environ.get("CLUSTRIX_TEST_SSH_USER")
KEY = os.environ.get("CLUSTRIX_TEST_SSH_KEY")

pytestmark = [
    pytest.mark.skipif(
        not (HOST and USER and KEY),
        reason=(
            "No local test cluster named: set CLUSTRIX_TEST_SSH_HOST, "
            "CLUSTRIX_TEST_SSH_USER and CLUSTRIX_TEST_SSH_KEY "
            "(scripts/setup_local_slurm.sh creates one)."
        ),
    ),
    pytest.mark.timeout(600),
]


@pytest.fixture
def slurm():
    try:
        probe = subprocess.run(
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
                "command -v sbatch || true; echo reachable",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"The test host {HOST}:{PORT} could not be reached: {exc}")
    if "reachable" not in probe.stdout:
        pytest.skip(f"The test host {HOST}:{PORT} refused the login: {probe.stderr}")
    if "sbatch" not in probe.stdout:
        pytest.skip(f"The test host {HOST} has no sbatch, so SLURM cannot be tested.")
    configure(
        cluster_type="slurm",
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        ssh_host_key_policy="auto_add",
        remote_work_dir="~/clustrix-realworld-tests",
        job_poll_interval=1,
        job_wait_timeout=300,
        default_cores=1,
        default_memory="200MB",
        default_time="00:05:00",
    )


def _allocation():
    """What SLURM says this job was given. Runs inside the job."""
    import os
    import subprocess

    described = subprocess.run(
        ["scontrol", "show", "job", os.environ["SLURM_JOB_ID"]],
        capture_output=True,
        text=True,
    ).stdout
    time_limit = described.split("TimeLimit=", 1)[1].split()[0]
    return {
        "cpus": os.environ.get("SLURM_CPUS_PER_TASK"),
        "memory_mb": os.environ.get("SLURM_MEM_PER_NODE"),
        "time_limit": time_limit,
    }


def test_the_configured_defaults_are_what_an_undecorated_request_gets(slurm):
    @cluster
    def report():
        return _allocation()

    granted = report()

    print(granted)
    assert granted == {"cpus": "1", "memory_mb": "200", "time_limit": "00:05:00"}


def test_the_decorator_overrides_each_default_and_slurm_grants_it(slurm):
    @cluster(cores=2, memory="300MB", time="00:07:00")
    def report():
        return _allocation()

    granted = report()

    print(granted)
    assert granted == {"cpus": "2", "memory_mb": "300", "time_limit": "00:07:00"}
