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


# What `lspci` really prints on a host with one consumer NVIDIA card. The
# card is a single GPU but two PCI functions -- the display controller and
# the HD Audio device that ships on the same die -- so `grep -i nvidia | wc
# -l` says 2. Counting these lines is counting functions, not GPUs.
LSPCI_ONE_CONSUMER_GPU = """00:00.0 Host bridge: Intel Corporation Xeon E3-1200 v6/7th Gen Core Processor Host Bridge/DRAM Registers (rev 05)
00:1f.3 Audio device: Intel Corporation 200 Series PCH HD Audio
01:00.0 VGA compatible controller: NVIDIA Corporation GA102 [GeForce RTX 3090] (rev a1)
01:00.1 Audio device: NVIDIA Corporation GA102 High Definition Audio Controller (rev a1)
"""


def _write_executable(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _install_proc_gpus_shim(bindir, base, pci_addresses):
    """Make ``/proc/driver/nvidia/gpus/`` real, for whatever ``ls`` is asked.

    The NVIDIA driver puts one directory per GPU under that path, named after
    the device's PCI address. macOS has no ``/proc`` and the path cannot be
    created, so a real directory is built elsewhere and ``ls`` is shadowed by
    a shim that rewrites that one argument and hands everything else --
    including the flags the shipped code chose -- to the real ``/bin/ls``.
    Nothing about the listing is hand-written: the bytes clustrix parses are
    the bytes ``ls`` produces for a directory that really has that shape.
    """
    gpus = base / "proc_nvidia_gpus"
    gpus.mkdir()
    for address in pci_addresses:
        (gpus / address).mkdir()
    _write_executable(
        bindir / "ls",
        "#!/bin/sh\n"
        "n=$#\n"
        "i=0\n"
        "while [ $i -lt $n ]; do\n"
        '  a="$1"; shift\n'
        '  case "$a" in\n'
        f'    /proc/driver/nvidia/gpus*) a="{gpus}" ;;\n'
        "  esac\n"
        '  set -- "$@" "$a"\n'
        "  i=$((i+1))\n"
        "done\n"
        'exec /bin/ls "$@"\n',
    )


@contextmanager
def gpu_info(tmp_path, smi_stdout, smi_exit=0, lspci_stdout=None, proc_gpus=None):
    """Run the real detection against a real host whose nvidia-smi says this.

    ``nvcc`` is shadowed with a silent stub, and ``lspci`` and the
    ``/proc/driver/nvidia/gpus/`` listing answer whatever the caller asks for
    -- by default, nothing -- so the answer comes from the responses under
    test rather than from whatever hardware happens to be running the suite.
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
    if lspci_stdout is None:
        _write_executable(bindir / "lspci", "#!/bin/sh\nexit 1\n")
    else:
        lspci_payload = bindir / "lspci_payload"
        lspci_payload.write_text(lspci_stdout)
        _write_executable(bindir / "lspci", f'#!/bin/sh\ncat "{lspci_payload}"\n')
    if proc_gpus is not None:
        _install_proc_gpus_shim(bindir, base, proc_gpus)

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


# The fallback methods (#172, adversarial review RT-1/RT-2).
#
# The tests above shadow `lspci` with `exit 1` and leave
# `/proc/driver/nvidia/gpus/` absent, which is what a host with no GPU looks
# like. On a host that *does* have one, the fallbacks run after an unreadable
# nvidia-smi and answer with their own evidence -- and the two of them can
# establish different things. Nothing below is stubbed at the Python level:
# a real `lspci` on the server's PATH prints a real listing, and the
# `/proc/driver/nvidia/gpus/` listing comes from the real `ls` reading a real
# directory that really has one entry per GPU.


def test_lspci_reports_presence_without_inventing_a_count(tmp_path):
    """One card, two PCI functions: "2" is not a number of GPUs.

    `lspci | grep -i nvidia | wc -l` counts PCI functions. A single consumer
    card presents the display controller and its companion HD Audio device,
    so the count is 2 on a one-GPU host -- and it was 2 that got assigned to
    ``gpu_count`` and printed as "GPU detected (2 devices)". lspci cannot
    count GPUs, so the count must be reported as unknown.
    """
    unreadable = "0, NVIDIA A100-SXM4-40GB, 40536 MiB, 40122 MiB, 8.0"
    with gpu_info(tmp_path, unreadable, lspci_stdout=LSPCI_ONE_CONSUMER_GPU) as info:
        # lspci really does prove NVIDIA hardware is attached.
        assert info["gpu_available"] is True
        assert info["detection_method"] == "lspci"
        # ...and really cannot say how many GPUs that is.
        assert info["gpu_count"] is None
        assert info["gpu_count"] != 2
        # No device list was read off any device.
        assert info["gpu_devices"] == []


def test_lspci_message_states_no_number_it_does_not_have(tmp_path):
    """The sentence the user reads must not contain a fabricated count."""
    unreadable = "0, NVIDIA A100-SXM4-40GB, 40536 MiB, 40122 MiB, 8.0"
    with gpu_info(tmp_path, unreadable, lspci_stdout=LSPCI_ONE_CONSUMER_GPU) as info:
        message = gpu_detection_summary(info)
        assert "2 devices" not in message
        assert "devices)" not in message
        assert "unknown" in message
        assert "lspci" in message


def test_the_inconclusive_state_survives_when_nothing_else_can_tell(tmp_path):
    """No NVIDIA on the bus and no driver directory: "could not tell" stands.

    This is the state RT-1 found unreachable: every earlier test reached it
    only because `lspci` was stubbed to fail, so it was never shown to
    survive a fallback that actually ran and found nothing.
    """
    unreadable = "0, NVIDIA A100-SXM4-40GB, 40536 MiB, 40122 MiB, 8.0"
    lspci_no_nvidia = (
        "00:00.0 Host bridge: Intel Corporation Xeon E3-1200 v6/7th Gen Core "
        "Processor Host Bridge/DRAM Registers (rev 05)\n"
        "00:02.0 VGA compatible controller: Intel Corporation HD Graphics 630 (rev 04)\n"
    )
    with gpu_info(
        tmp_path, unreadable, lspci_stdout=lspci_no_nvidia, proc_gpus=[]
    ) as info:
        assert info["gpu_available"] is False
        assert info["gpu_detection_inconclusive"] is True
        assert info["gpu_count"] == 0
        assert gpu_detection_summary(info).startswith(
            "Could not determine whether this cluster has GPUs"
        )


@pytest.mark.parametrize(
    "pci_addresses,expected",
    [
        (["0000:01:00.0"], 1),
        (["0000:07:00.0", "0000:0a:00.0"], 2),
        (
            [
                "0000:07:00.0",
                "0000:0a:00.0",
                "0000:47:00.0",
                "0000:4d:00.0",
            ],
            4,
        ),
    ],
)
def test_proc_driver_counts_gpus_not_listing_decorations(
    tmp_path, pci_addresses, expected
):
    """One directory per GPU means one line per GPU -- and nothing else.

    This ran `ls -la` and subtracted 2 for `.` and `..`, which forgot the
    ``total`` line that ``-l`` prints, so a machine with one GPU was reported
    as having two. The listing here is produced by the real ``ls`` against a
    real directory with exactly ``len(pci_addresses)`` entries, so whatever
    the shipped command asks for is what gets parsed.
    """
    with gpu_info(tmp_path, "", smi_exit=9, proc_gpus=pci_addresses) as info:
        assert info["gpu_available"] is True
        assert info["detection_method"] == "/proc/driver/nvidia"
        assert info["gpu_count"] == expected
        # A count, but still no device detail -- and the message says so.
        assert info["gpu_devices"] == []
        message = gpu_detection_summary(info)
        assert f"GPU detected ({expected} devices)" in message
        assert "no per-device details" in message


def test_proc_driver_wins_over_lspci_because_it_can_count(tmp_path):
    """Both fallbacks available: the one that can count is the one that does."""
    with gpu_info(
        tmp_path,
        "",
        smi_exit=9,
        lspci_stdout=LSPCI_ONE_CONSUMER_GPU,
        proc_gpus=["0000:01:00.0"],
    ) as info:
        assert info["detection_method"] == "/proc/driver/nvidia"
        assert info["gpu_count"] == 1


def test_nvidia_smi_still_owns_the_device_list(tmp_path):
    """A readable nvidia-smi is not overridden by a fallback's coarser answer."""
    with gpu_info(
        tmp_path,
        WELL_FORMED,
        lspci_stdout=LSPCI_ONE_CONSUMER_GPU,
        proc_gpus=["0000:01:00.0"],
    ) as info:
        assert info["detection_method"] == "nvidia-smi"
        assert info["gpu_count"] == 2
        assert len(info["gpu_devices"]) == 2
        assert gpu_detection_summary(info) == (
            "GPU detected (2 devices), setting up GPU-enabled VENV2..."
        )
