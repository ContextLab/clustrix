"""``detect_gpu_capabilities`` must not report a GPU it could not read about.

Nothing here is mocked. A real in-process SSH server runs a real ``nvidia-smi``
-- a small executable on the server's ``PATH`` that prints exactly the bytes a
driver would print -- and the shipped detection code connects to it over a real
paramiko client and parses what really comes back.

The defect these tests pin (#172): ``gpu_available`` was set to ``True`` as
soon as ``nvidia-smi`` exited 0 with *any* output, before a single line had
been parsed. Nothing downstream looked at the parse result, so output the code
could not read -- an added column, a warning line, a unit suffix, a comma in a
device name -- was reported as "yes, there is a GPU" with an empty device list.
``enhanced_setup_two_venv_environment`` then printed "GPU detected (0 devices)"
and took the GPU branch.

The contract now: ``gpu_available`` means a GPU was positively identified, and
output that could not be parsed sets ``gpu_detection_inconclusive`` instead,
which is neither a yes nor a no.
"""

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from clustrix.utils import detect_gpu_capabilities, gpu_detection_summary
from tests.ssh_server import LocalSSHServer

PASSWORD = "hunter2"

# Two devices, exactly as `nvidia-smi --format=csv,noheader,nounits` prints
# them for the five fields clustrix asks for.
WELL_FORMED = "0, NVIDIA A100-SXM4-40GB, 40536, 40122, 8.0\n1, NVIDIA A100-SXM4-40GB, 40536, 39980, 8.0"


def _write_executable(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@contextmanager
def gpu_info(tmp_path, smi_stdout, smi_exit=0):
    """Run the real detection against a real host whose nvidia-smi says this.

    ``nvcc`` and ``lspci`` are shadowed with silent stubs so the answer comes
    from the response under test rather than from whatever hardware happens to
    be running the suite.
    """
    # A fresh directory per call, so one test can stand up several hosts.
    base = Path(tempfile.mkdtemp(dir=str(tmp_path)))
    bindir = base / "bin"
    bindir.mkdir()
    payload = bindir / "smi_payload"
    payload.write_text(smi_stdout)
    _write_executable(
        bindir / "nvidia-smi",
        f'#!/bin/sh\ncat "{payload}"\nexit {smi_exit}\n',
    )
    _write_executable(bindir / "nvcc", "#!/bin/sh\nexit 1\n")
    _write_executable(bindir / "lspci", "#!/bin/sh\nexit 1\n")

    root = base / "served"
    root.mkdir()
    path = f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    with LocalSSHServer(root=str(root), password=PASSWORD, env={"PATH": path}) as srv:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=srv.host,
            cluster_port=srv.port,
            username="tester",
            password=PASSWORD,
            ssh_host_key_policy="auto_add",
            remote_work_dir=srv.root,
        )
        connection = ConnectionManager(config)
        connection.setup_ssh_connection()
        try:
            yield detect_gpu_capabilities(connection.ssh_client, config)
        finally:
            connection.disconnect()


def test_well_formed_output_is_still_parsed(tmp_path):
    """The honest yes must survive: two real rows, two real devices."""
    with gpu_info(tmp_path, WELL_FORMED) as info:
        assert info["gpu_available"] is True
        assert info["gpu_detection_inconclusive"] is False
        assert info["gpu_count"] == 2
        assert info["detection_method"] == "nvidia-smi"
        assert [d["index"] for d in info["gpu_devices"]] == [0, 1]
        assert info["gpu_devices"][0]["name"] == "NVIDIA A100-SXM4-40GB"
        assert info["gpu_devices"][0]["memory_total_mb"] == 40536
        assert info["gpu_devices"][1]["memory_free_mb"] == 39980
        assert info["gpu_devices"][0]["compute_capability"] == "8.0"
        assert info["detection_errors"] == []


@pytest.mark.parametrize(
    "description,smi_stdout",
    [
        (
            # A driver release adds a column; the five fields asked for are
            # no longer the five fields returned.
            "an extra column",
            "0, NVIDIA A100-SXM4-40GB, 40536, 40122, 8.0, 350.00\n"
            "1, NVIDIA A100-SXM4-40GB, 40536, 39980, 8.0, 348.12",
        ),
        (
            # nounits was asked for and ignored.
            "a changed unit",
            "0, NVIDIA A100-SXM4-40GB, 40536 MiB, 40122 MiB, 8.0",
        ),
        (
            # nvidia-smi prints diagnostics on stdout and still exits 0.
            "a leading warning line",
            "Warning: persistence mode is disabled on device 0\n"
            "0, NVIDIA A100-SXM4-40GB, 40536, 40122, 8.0",
        ),
        (
            # A device name with a comma in it splits into six fields.
            "a comma inside a device name",
            "0, NVIDIA RTX A6000, Ada Generation, 49140, 48800, 8.9",
        ),
        (
            # Index is not a number.
            "a non-numeric index",
            "GPU-1a2b3c, NVIDIA A100-SXM4-40GB, 40536, 40122, 8.0",
        ),
    ],
)
def test_unparseable_output_is_not_reported_as_a_gpu(tmp_path, description, smi_stdout):
    """Output nobody could read is not evidence of a GPU."""
    with gpu_info(tmp_path, smi_stdout) as info:
        assert info["gpu_available"] is False, description
        assert info["gpu_detection_inconclusive"] is True, description
        assert info["gpu_count"] == 0, description
        assert info["gpu_devices"] == [], description
        assert info["detection_method"] != "nvidia-smi", description
        # The response itself is reported, so a user can see what moved.
        assert any(
            "could not be parsed" in err for err in info["detection_errors"]
        ), description


def test_partially_parseable_output_does_not_undercount(tmp_path):
    """One readable row out of two is not "this machine has one GPU"."""
    smi_stdout = (
        "0, NVIDIA A100-SXM4-40GB, 40536, 40122, 8.0\n"
        "1, NVIDIA A100-SXM4-40GB, 40536 MiB, 39980 MiB, 8.0"
    )
    with gpu_info(tmp_path, smi_stdout) as info:
        assert info["gpu_count"] != 1
        assert info["gpu_count"] == 0
        assert info["gpu_available"] is False
        assert info["gpu_detection_inconclusive"] is True


def test_no_gpu_is_a_definite_no_not_an_inconclusive_one(tmp_path):
    """nvidia-smi present, exits 0, lists nothing: that is a real answer."""
    with gpu_info(tmp_path, "") as info:
        assert info["gpu_available"] is False
        assert info["gpu_detection_inconclusive"] is False
        assert info["gpu_count"] == 0
        assert info["detection_errors"] == []


def test_nvidia_smi_failing_is_a_definite_no(tmp_path):
    """No driver at all: still a definite answer, not an unreadable one."""
    with gpu_info(tmp_path, "", smi_exit=9) as info:
        assert info["gpu_available"] is False
        assert info["gpu_detection_inconclusive"] is False
        assert info["gpu_count"] == 0


def test_the_setup_message_distinguishes_no_from_could_not_tell(tmp_path):
    """The sentence a user reads must not turn "could not tell" into "no".

    ``enhanced_setup_two_venv_environment`` prints this line before choosing
    between the GPU and the standard VENV2 path. It used to have two branches
    for three outcomes, so an unreadable nvidia-smi response was announced as
    "No GPUs detected".
    """
    with gpu_info(tmp_path, WELL_FORMED) as info:
        assert gpu_detection_summary(info) == (
            "GPU detected (2 devices), setting up GPU-enabled VENV2..."
        )

    with gpu_info(tmp_path, "") as info:
        assert gpu_detection_summary(info) == (
            "No GPUs detected, using standard VENV2 setup..."
        )

    unreadable = "0, NVIDIA A100-SXM4-40GB, 40536 MiB, 40122 MiB, 8.0"
    with gpu_info(tmp_path, unreadable) as info:
        message = gpu_detection_summary(info)
        assert message.startswith("Could not determine whether this cluster has GPUs")
        assert "No GPUs detected" not in message
        # The response that could not be read is quoted, not summarised away.
        assert "40536 MiB" in message
