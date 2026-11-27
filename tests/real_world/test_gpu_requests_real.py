"""``@cluster(gpus=N)`` against a real scheduler and a real ssh host.

What this can and cannot show. The test SLURM
(``scripts/setup_local_slurm.sh``) is configured with two resources of type
``gpu`` that are names for the null device, not graphics cards. So these tests show the
whole path a GPU *request* takes: the directive clustrix writes, ``sbatch``
accepting it, the scheduler holding back jobs when both devices are busy,
and each job being told which device is its own. They do not show a
computation on a GPU; nothing in this repository's tests does.

    export CLUSTRIX_TEST_SSH_HOST=127.0.0.1 CLUSTRIX_TEST_SSH_PORT=2222 \
           CLUSTRIX_TEST_SSH_USER=clx \
           CLUSTRIX_TEST_SSH_KEY=/tmp/clustrix-local-cluster/id_ed25519
    pytest tests/real_world/test_gpu_requests_real.py -m real_world
"""

import os
import subprocess

import pytest

from clustrix import cluster, configure
from clustrix import decorator as decorator_module
from clustrix.executor import ClusterExecutor
from clustrix.config import get_config
from clustrix.utils import serialize_function

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

RESOURCES = dict(cores=1, memory="200MB", time="00:05:00")


def _on_host(command: str) -> str:
    done = subprocess.run(
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
    return done.stdout


def _configure(cluster_type: str, **more) -> None:
    configure(
        cluster_type=cluster_type,
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        ssh_host_key_policy="auto_add",
        remote_work_dir="~/clustrix-realworld-tests",
        job_poll_interval=1,
        job_wait_timeout=300,
        **more,
    )
    decorator_module._ABANDONED.clear()


@pytest.fixture
def slurm_with_gpus():
    """SLURM on the test host, with the number of ``gpu`` resources it has."""
    listed = _on_host("sinfo -h -o %G 2>/dev/null")
    if "gpu:" not in listed:
        pytest.skip(
            f"The scheduler on {HOST} has no resource of type gpu (sinfo says "
            f"{listed.strip()!r}); scripts/setup_local_slurm.sh configures two."
        )
    _configure("slurm")
    return int(listed.strip().split("gpu:")[1].split("(")[0].split(",")[0])


def test_a_job_that_asks_for_a_gpu_is_given_one_by_the_scheduler(slurm_with_gpus):
    @cluster(gpus=1, **RESOURCES)
    def which_device():
        import os

        return os.environ.get("CUDA_VISIBLE_DEVICES"), os.environ["SLURM_JOB_ID"]

    device, job_id = which_device()

    assert device in {str(n) for n in range(slurm_with_gpus)}
    allocated = _on_host(f"sacct -j {job_id} -X -n -P -o AllocTRES")
    assert "gres/gpu=1" in allocated, allocated


def test_a_job_that_asks_for_none_is_given_none(slurm_with_gpus):
    @cluster(**RESOURCES)
    def which_device():
        import os

        return os.environ.get("CUDA_VISIBLE_DEVICES"), os.environ["SLURM_JOB_ID"]

    device, job_id = which_device()

    assert not device
    assert "gres/gpu" not in _on_host(f"sacct -j {job_id} -X -n -P -o AllocTRES")


def test_pieces_of_a_divided_loop_never_share_a_device(slurm_with_gpus):
    """Four pieces, one GPU each, two GPUs: the scheduler runs two at a time,
    and two that overlap in time were given different devices."""
    configure(max_parallel_jobs=4)

    @cluster(gpus=1, **RESOURCES)
    def train_each(settings):
        import os
        import time

        runs = []
        for setting in settings:
            started = time.time()
            time.sleep(6)
            runs.append(
                (
                    setting,
                    os.environ.get("CUDA_VISIBLE_DEVICES"),
                    os.environ["SLURM_JOB_ID"],
                    started,
                    time.time(),
                )
            )
        return runs

    runs = train_each(["a", "b", "c", "d"])

    assert [run[0] for run in runs] == ["a", "b", "c", "d"]
    assert len({run[2] for run in runs}) == 4, "each piece is its own job"
    assert {run[1] for run in runs} <= {str(n) for n in range(slurm_with_gpus)}
    overlapping = [
        (first, second)
        for index, first in enumerate(runs)
        for second in runs[index + 1 :]
        if first[3] < second[4] and second[3] < first[4]
    ]
    assert overlapping, "with two devices, two pieces should have run together"
    for first, second in overlapping:
        assert first[1] != second[1], (first, second)
    for moment in [run[3] + 0.01 for run in runs]:
        running = [run for run in runs if run[3] <= moment < run[4]]
        assert len(running) <= slurm_with_gpus, running


def test_asking_for_more_gpus_than_exist_is_refused_by_the_scheduler(slurm_with_gpus):
    @cluster(gpus=slurm_with_gpus + 1, **RESOURCES)
    def never_runs():
        return "ran"

    with pytest.raises(Exception) as raised:
        never_runs()

    assert "sbatch" in str(raised.value).lower() or "gres" in str(raised.value).lower()


def test_an_ssh_job_sees_the_devices_it_was_assigned():
    """What a piece of a divided loop is submitted with on a plain host."""
    _configure("ssh")

    def which_device():
        import os

        return os.environ.get("CUDA_VISIBLE_DEVICES")

    with ClusterExecutor(get_config()) as executor:
        job_id = executor.submit_job(
            serialize_function(which_device, (), {}),
            {**RESOURCES, "cuda_visible_devices": "2,3"},
        )
        assert executor.wait_for_result(job_id) == "2,3"


def test_dividing_a_loop_across_gpus_on_a_host_without_any_says_so():
    _configure("ssh", max_parallel_jobs=4)

    @cluster(gpus=1, **RESOURCES)
    def train_each(settings):
        out = []
        for setting in settings:
            out.append(setting)
        return out

    with pytest.raises(ValueError) as raised:
        train_each(["a", "b", "c", "d"])

    assert "gpus=1 was requested and 0 GPU(s) were found" in str(raised.value)
    assert HOST in str(raised.value)
