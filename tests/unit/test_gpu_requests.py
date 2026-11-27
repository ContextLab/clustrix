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
from clustrix.utils import (
    create_job_script,
    gpu_device_groups,
    local_gpu_devices,
    read_gpu_probe,
    remote_gpu_devices,
    usable_gpu_devices,
)


@pytest.mark.parametrize(
    "devices,per_job,at_most,expected",
    [
        (["0", "1", "2", "3"], 1, 8, ["0", "1", "2", "3"]),
        (["0", "1", "2", "3"], 1, 2, ["0", "1"]),
        (["0", "1", "2", "3"], 2, 8, ["0,1", "2,3"]),
        (["0", "1", "2", "3", "4"], 2, 8, ["0,1", "2,3"]),
        (["0"], 1, 4, ["0"]),
        # #176 G2: the ids handed out are the ids given, not 0..n-1.
        (["3"], 1, 4, ["3"]),
        (["4", "5", "6", "7"], 2, 8, ["4,5", "6,7"]),
        (["GPU-aaaa", "GPU-bbbb"], 1, 8, ["GPU-aaaa", "GPU-bbbb"]),
    ],
)
def test_each_job_gets_its_own_devices(devices, per_job, at_most, expected):
    assert gpu_device_groups(devices, per_job, at_most) == expected


def test_no_two_jobs_are_given_the_same_device():
    groups = gpu_device_groups([str(n) for n in range(8)], 3, 10)

    devices = [device for group in groups for device in group.split(",")]
    assert len(devices) == len(set(devices)) == 6


@pytest.mark.parametrize("devices,per_job", [([], 1), (["0"], 2)])
def test_fewer_devices_than_one_job_needs_is_an_error(devices, per_job):
    with pytest.raises(ValueError, match=f"gpus={per_job} was requested"):
        gpu_device_groups(devices, per_job, 4)


#: What ``nvidia-smi --query-gpu=index,uuid --format=csv,noheader`` prints
#: for four devices. Text, for the function that reads it; no test here runs
#: a stand-in ``nvidia-smi``.
FOUR_DEVICES = "0, GPU-aaaa1111\n1, GPU-bbbb2222\n2, GPU-cccc3333\n3, GPU-dddd4444\n"


@pytest.mark.parametrize(
    "visible,expected",
    [
        (None, ["0", "1", "2", "3"]),
        # #176 G2: a caller restricted to device 3 had workers handed 0-3.
        ("3", ["3"]),
        ("2,0", ["2", "0"]),
        (" 1 , 3 ", ["1", "3"]),
        ("", []),
        ("-1", []),
        ("GPU-cccc3333", ["GPU-cccc3333"]),
        ("GPU-cccc,GPU-aaaa", ["GPU-cccc", "GPU-aaaa"]),
        # CUDA reads up to the first entry it cannot use.
        ("1,9,2", ["1"]),
        ("1,,2", ["1"]),
        ("1,1", ["1"]),
        ("GPU-zzzz,0", []),
    ],
)
def test_the_devices_a_process_may_use_are_the_ones_it_was_given(visible, expected):
    assert usable_gpu_devices(FOUR_DEVICES, visible) == expected


@pytest.mark.parametrize("visible", [None, "0", "0,1", "GPU-aaaa1111"])
def test_a_variable_cannot_conjure_a_device_the_driver_does_not_list(visible):
    """``CUDA_VISIBLE_DEVICES=0`` left in a shell profile on a machine with
    no GPU is not a GPU."""
    assert usable_gpu_devices("", visible) == []
    assert usable_gpu_devices("No devices were found\n", visible) == []


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


no_gpu_here = pytest.mark.skipif(
    shutil.which("nvidia-smi") is not None,
    reason="This machine has nvidia-smi; the test is about a machine without GPUs.",
)


@no_gpu_here
def test_dividing_a_loop_across_gpus_on_a_machine_without_any_says_so():
    if available_cpus() < 2:
        pytest.skip("With one CPU the loop is not divided, so no GPU is looked for.")
    assert local_gpu_devices() == []
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


def no_loop(n):
    return os.environ.get("CUDA_VISIBLE_DEVICES", "ran without a GPU")


def has_a_loop(n):
    out = []
    for i in range(n):
        out.append(i)
    return out


@no_gpu_here
@pytest.mark.parametrize(
    "function,options,setting",
    [
        (no_loop, dict(cores=4), {}),
        (has_a_loop, dict(cores=1), {}),
        (has_a_loop, dict(cores=4, parallel=False), {}),
        (has_a_loop, dict(cores=4, async_submit=True), {}),
        (no_loop, dict(), dict(cluster_type="local", cluster_host="localhost")),
    ],
    ids=["no loop", "cores=1", "parallel=False", "async_submit", "cluster_type=local"],
)
def test_every_local_route_refuses_gpus_this_machine_does_not_have(
    function, options, setting
):
    """#176 G1. Only the divided path checked. Each of these ran the
    function without a GPU and said nothing."""
    configure(**{"cluster_type": "local", "cluster_host": None, **setting})
    ran = []

    def recorded(n):
        ran.append(n)
        return function(n)

    recorded.__name__ = function.__name__

    with pytest.raises(ValueError) as raised:
        cluster(gpus=1, **options)(recorded)(4)

    assert "gpus=1 was requested and 0 GPU(s) were found on this machine" in str(
        raised.value
    )
    assert ran == [], "the function must not have run"


@no_gpu_here
def test_the_refusal_says_when_the_environment_restricts_the_devices(monkeypatch):
    configure(cluster_type="local", cluster_host=None)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")

    with pytest.raises(ValueError) as raised:
        cluster(gpus=1)(no_loop)(1)

    assert "CUDA_VISIBLE_DEVICES='3' is set in this process" in str(raised.value)


def test_asking_for_no_gpus_looks_for_none():
    configure(cluster_type="local", cluster_host=None)

    assert cluster(gpus=0)(no_loop)(1) == "ran without a GPU"
    assert cluster()(no_loop)(1) == "ran without a GPU"


@pytest.mark.parametrize("exported", [None, "3", ""])
def test_an_ssh_host_is_asked_in_one_command_and_its_environment_is_read(
    tmp_path, exported
):
    """A real SSH server and a real remote shell, with and without a
    ``CUDA_VISIBLE_DEVICES`` in the account's environment. There is no GPU
    behind it, so what this shows is that the probe runs, that the variable
    reaches :func:`usable_gpu_devices` as it is, and that a variable alone
    finds no device. The same probe on a host that has GPUs has not been
    run."""
    from clustrix.executor import ClusterExecutor
    from clustrix.utils import GPU_PROBE_COMMAND
    from tests.ssh_server import LocalSSHServer

    if shutil.which("nvidia-smi") is not None:
        pytest.skip("This machine has nvidia-smi; the server would list its GPUs.")
    env = {} if exported is None else {"CUDA_VISIBLE_DEVICES": exported}
    with LocalSSHServer(root=str(tmp_path), password="s3cret", env=env) as server:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password="s3cret",
            ssh_host_key_policy="auto_add",
        )
        with ClusterExecutor(config) as executor:
            executor.connect()
            connection = executor.connection_manager
            printed, _ = connection.execute_remote_command(GPU_PROBE_COMMAND)
            before = len(server.commands)
            devices = remote_gpu_devices(connection)
            asked = len(server.commands) - before

    assert read_gpu_probe(printed) == ("", exported)
    assert devices == [] and asked == 1


def test_a_probe_s_answer_is_read_back_as_a_listing_and_a_variable():
    answer = "CLUSTRIX-CUDA-VISIBLE-DEVICES set 1,3\n" + FOUR_DEVICES

    listing, visible = read_gpu_probe(answer)

    assert visible == "1,3" and listing.splitlines() == FOUR_DEVICES.splitlines()
    assert usable_gpu_devices(listing, visible) == ["1", "3"]
    assert read_gpu_probe("CLUSTRIX-CUDA-VISIBLE-DEVICES  \n" + FOUR_DEVICES)[1] is None
    assert read_gpu_probe("CLUSTRIX-CUDA-VISIBLE-DEVICES set \n")[1] == ""
