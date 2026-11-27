"""The name a real SLURM controller records for a clustrix job.

Every job used to be ``clustrix``. The job reads ``SLURM_JOB_NAME`` -- the
scheduler's statement to the job about its own name -- and ``sacct`` is
asked afterwards, so this is what a user looking at the queue sees.

Run as ``tests/real_world/test_local_sshd_slurm.py`` describes.
"""

import time

import pytest

from clustrix import cluster, configure
from tests.real_world.test_local_sshd_slurm import (  # noqa: F401
    HOST,
    KEY,
    PORT,
    RESOURCES,
    USER,
    _remote,
    _wait_for_accounting,
    host_facts,
    pytestmark,
)


@pytest.fixture
def slurm(host_facts):
    if not host_facts["slurm"]:
        pytest.skip(f"The test host {HOST} has no sbatch.")
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
    )


def _accounted_names(job_ids) -> dict:
    """``sacct``'s name for each job, once slurmdbd has the whole record.

    A job that finishes within a second can reach the accounting database
    completion-first; until its start record follows, ``sacct`` reports the
    placeholder name ``allocation`` for it. That is slurmdbd's ordering,
    not the job's name, so it is waited out.
    """
    _wait_for_accounting(set(job_ids))
    deadline = time.monotonic() + 30
    while True:
        listing = _remote(f"sacct -X -n -P -o JobID,JobName%60 -j {','.join(job_ids)}")
        assert listing.returncode == 0, listing.stderr
        names = dict(line.split("|") for line in listing.stdout.split())
        if "allocation" not in names.values() or time.monotonic() > deadline:
            print("sacct -X -n -P -o JobID,JobName:\n" + listing.stdout)
            return names
        time.sleep(1)


def test_a_job_is_listed_under_its_functions_name(slurm):
    @cluster(**RESOURCES)
    def estimate_drift():
        import os

        return os.environ["SLURM_JOB_ID"], os.environ["SLURM_JOB_NAME"]

    job_id, name = estimate_drift()

    assert name == "estimate_drift"
    assert _accounted_names([job_id]) == {job_id: "estimate_drift"}


def test_each_chunk_of_a_divided_loop_is_listed_under_the_functions_name(slurm):
    configure(max_parallel_jobs=3)

    @cluster(**RESOURCES)
    def sweep_thresholds(values):
        out = []
        for value in values:
            import os

            out.append(
                (value, os.environ["SLURM_JOB_ID"], os.environ["SLURM_JOB_NAME"])
            )
        return out

    result = sweep_thresholds([1, 2, 3])

    job_ids = sorted({job_id for _, job_id, _ in result})
    assert len(job_ids) == 3, "the loop was not divided into three jobs"
    assert {name for _, _, name in result} == {"sweep_thresholds.body"}
    assert _accounted_names(job_ids) == dict.fromkeys(job_ids, "sweep_thresholds.body")
