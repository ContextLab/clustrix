"""``@cluster(gpus=N)``: the parts that need no cluster.

The scheduler and ssh paths are exercised for real in
``tests/real_world/test_gpu_requests_real.py``. Nothing here or there runs a
computation on a GPU.
"""

import os
import shutil

import pytest

from clustrix import cluster, configure
from clustrix.config import ClusterConfig
from clustrix.loop_workers import available_cpus, gpu_pool
from clustrix.utils import create_job_script, gpu_device_groups, local_gpu_count


@pytest.mark.parametrize(
    "available,per_job,at_most,expected",
    [
        (4, 1, 8, ["0", "1", "2", "3"]),
        (4, 1, 2, ["0", "1"]),
        (4, 2, 8, ["0,1", "2,3"]),
        (5, 2, 8, ["0,1", "2,3"]),
        (1, 1, 4, ["0"]),
    ],
)
def test_each_job_gets_its_own_devices(available, per_job, at_most, expected):
    assert gpu_device_groups(available, per_job, at_most) == expected


def test_no_two_jobs_are_given_the_same_device():
    groups = gpu_device_groups(8, 3, 10)

    devices = [device for group in groups for device in group.split(",")]
    assert len(devices) == len(set(devices)) == 6


@pytest.mark.parametrize("available,per_job", [(0, 1), (1, 2)])
def test_fewer_devices_than_one_job_needs_is_an_error(available, per_job):
    with pytest.raises(ValueError, match=f"gpus={per_job} was requested"):
        gpu_device_groups(available, per_job, 4)


@pytest.mark.parametrize("bad", [-1, 1.5, "2", True])
def test_gpus_must_be_a_whole_number(bad):
    configure(cluster_type="local", cluster_host=None)

    @cluster(gpus=bad)
    def job():
        return 1

    with pytest.raises(ValueError, match="gpus is the number of GPUs each job"):
        job()


def test_a_slurm_script_asks_for_the_gpus_and_only_when_asked():
    config = ClusterConfig(cluster_type="slurm", cluster_host="login.example.edu")
    resources = {"cores": 2, "memory": "4GB", "time": "00:10:00"}

    with_gpus = create_job_script("slurm", {**resources, "gpus": 2}, "/w/job", config)
    without = create_job_script("slurm", {**resources, "gpus": 0}, "/w/job", config)

    assert "#SBATCH --gres=gpu:2" in with_gpus.splitlines()
    assert "--gres" not in without


def test_an_ssh_script_exports_the_devices_before_anything_else_runs():
    config = ClusterConfig(cluster_type="ssh", cluster_host="gpu.example.edu")
    resources = {"cores": 2, "memory": "4GB", "time": "00:10:00"}

    script = create_job_script(
        "ssh", {**resources, "cuda_visible_devices": "2,3"}, "/w/job", config
    ).splitlines()
    plain = create_job_script("ssh", resources, "/w/job", config)

    assert script.index("export CUDA_VISIBLE_DEVICES=2,3") == 3
    assert "CUDA_VISIBLE_DEVICES" not in plain


def test_devices_that_are_not_a_list_of_numbers_cannot_reach_the_shell():
    config = ClusterConfig(cluster_type="ssh", cluster_host="gpu.example.edu")
    resources = {"cores": 2, "memory": "4GB", "time": "00:10:00"}

    with pytest.raises(ValueError, match="cuda_visible_devices"):
        create_job_script(
            "ssh", {**resources, "cuda_visible_devices": "0; rm -rf ~"}, "/w", config
        )


def reads_its_devices():
    return os.environ.get("CUDA_VISIBLE_DEVICES"), os.getpid()


def test_each_worker_of_a_gpu_pool_sees_only_its_own_devices():
    """Real worker processes; the variable is what a framework reads."""
    pool = gpu_pool(["0", "1,2"])

    seen = pool.map(reads_its_devices, [(), (), (), ()])

    by_process = {}
    for devices, pid in seen:
        by_process.setdefault(pid, set()).add(devices)
    assert os.getpid() not in by_process
    assert all(len(devices) == 1 for devices in by_process.values())
    assert {next(iter(devices)) for devices in by_process.values()} <= {"0", "1,2"}
    assert gpu_pool(["0", "1,2"]) is pool, "kept between calls"


@pytest.mark.skipif(
    shutil.which("nvidia-smi") is not None,
    reason="This machine has nvidia-smi; the test is about a machine without GPUs.",
)
def test_dividing_a_loop_across_gpus_on_a_machine_without_any_says_so():
    if available_cpus() < 2:
        pytest.skip("With one CPU the loop is not divided, so no GPU is looked for.")
    assert local_gpu_count() == 0
    configure(cluster_type="local", cluster_host=None)

    @cluster(cores=2, gpus=1)
    def train_each(settings):
        out = []
        for setting in settings:
            out.append(setting)
        return out

    with pytest.raises(ValueError) as raised:
        train_each(["a", "b", "c", "d"])

    assert "gpus=1 was requested and 0 GPU(s) were found on this machine" in str(
        raised.value
    )
