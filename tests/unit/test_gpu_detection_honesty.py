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

Two further claims each method is *not* entitled to, both found by adversarial
review of the fix (RT5-6) and both pinned below:

* ``lspci`` was matched on the vendor string, and NVIDIA's vendor id is on far
  more than GPUs -- every consumer card's HDMI audio function, and the whole
  nForce southbridge family. So a machine with an NVIDIA-branded SMBus
  controller and ASPEED graphics reported a GPU. The match is on the PCI
  device *class* now, and the listings here are real ``lspci -nn`` output for
  hardware that really does have one and hardware that really does not.
* ``setup_gpu_enabled_venv2`` gated a CUDA install on ``gpu_available``, which
  ``lspci`` alone can set. A card in a slot is not a driver, so the install is
  gated on ``nvidia_driver_present`` -- set only by nvidia-smi and
  ``/proc/driver/nvidia``, the two methods that observe the driver.
"""

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from clustrix.utils import (
    detect_gpu_capabilities,
    gpu_detection_summary,
    setup_gpu_enabled_venv2,
)
from tests.ssh_server import LocalSSHServer

PASSWORD = "hunter2"

# Two devices, exactly as `nvidia-smi --format=csv,noheader,nounits` prints
# them for the five fields clustrix asks for.
WELL_FORMED = "0, NVIDIA A100-SXM4-40GB, 40536, 40122, 8.0\n1, NVIDIA A100-SXM4-40GB, 40536, 39980, 8.0"


# Real `lspci -nn` listings. Every line below is the shape pciutils prints:
# `<slot> <class name> [<class code>]: <vendor> <device> [<vendor id>:<device
# id>] (rev ..)`. The shipped code passes `-nn` because both halves of the
# match it needs -- the class code and NVIDIA's vendor id 10de -- are numeric
# there, and numeric ids are printed even when the host's pci.ids is too old
# to name the device.

# One consumer NVIDIA card. It is a single GPU but two PCI functions -- the
# display controller and the HD Audio device on the same die -- so counting
# matching lines counts functions, not GPUs.
LSPCI_ONE_CONSUMER_GPU = """00:00.0 Host bridge [0600]: Intel Corporation Xeon E3-1200 v6/7th Gen Core Processor Host Bridge/DRAM Registers [8086:5918] (rev 05)
00:1f.3 Audio device [0403]: Intel Corporation 200 Series PCH HD Audio [8086:a2f0]
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA102 [GeForce RTX 3090] [10de:2204] (rev a1)
01:00.1 Audio device [0403]: NVIDIA Corporation GA102 High Definition Audio Controller [10de:1aef] (rev a1)
"""

# A datacenter part. It drives no display, so it enumerates in class 0302,
# "3D controller", and never as a VGA controller: a class match that only
# looks for 0300 misses every A100 and H100 in existence.
LSPCI_DATACENTER_GPU = """00:00.0 Host bridge [0600]: Intel Corporation Sky Lake-E DMI3 Registers [8086:2020] (rev 04)
07:00.0 VGA compatible controller [0300]: ASPEED Technology, Inc. ASPEED Graphics Family [1a03:2000] (rev 41)
17:00.0 3D controller [0302]: NVIDIA Corporation GA100 [A100 SXM4 40GB] [10de:20b0] (rev a1)
"""

# NVIDIA silicon that is not a GPU. Both of these listings really do occur,
# and `lspci | grep -i nvidia` matched both.
#
# An NVIDIA HD Audio function with no NVIDIA display controller beside it:
# the card's display function has been unbound and handed to a guest VM by
# vfio-pci, leaving the audio function on the host.
LSPCI_NVIDIA_AUDIO_FUNCTION_ONLY = """00:00.0 Host bridge [0600]: Intel Corporation Xeon E3-1200 v6/7th Gen Core Processor Host Bridge/DRAM Registers [8086:5918] (rev 05)
00:02.0 VGA compatible controller [0300]: Intel Corporation HD Graphics 630 [8086:5912] (rev 04)
01:00.1 Audio device [0403]: NVIDIA Corporation GA102 High Definition Audio Controller [10de:1aef] (rev a1)
"""

# An nForce motherboard chipset. NVIDIA made southbridges: the SMBus, LPC,
# SATA and Ethernet controllers of this machine are all NVIDIA-branded, and
# the only graphics in it is the ASPEED BMC.
LSPCI_NFORCE_CHIPSET = """00:00.0 Host bridge [0600]: NVIDIA Corporation MCP61 Host Bridge [10de:03e2] (rev a1)
00:01.0 ISA bridge [0601]: NVIDIA Corporation MCP61 LPC Bridge [10de:03e0] (rev a2)
00:01.1 SMBus [0c05]: NVIDIA Corporation MCP61 SMBus [10de:03eb] (rev a2)
00:07.0 Bridge [0680]: NVIDIA Corporation MCP61 Ethernet [10de:03ef] (rev a2)
00:08.0 IDE interface [0101]: NVIDIA Corporation MCP61 SATA Controller [10de:03f6] (rev a2)
07:00.0 VGA compatible controller [0300]: ASPEED Technology, Inc. ASPEED Graphics Family [1a03:2000] (rev 41)
"""

# No NVIDIA anything.
LSPCI_NO_NVIDIA = """00:00.0 Host bridge [0600]: Intel Corporation Xeon E3-1200 v6/7th Gen Core Processor Host Bridge/DRAM Registers [8086:5918] (rev 05)
00:02.0 VGA compatible controller [0300]: Intel Corporation HD Graphics 630 [8086:5912] (rev 04)
"""


def _write_executable(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _install_proc_gpus_shim(bindir, base, pci_addresses, forced_flags=""):
    """Make ``/proc/driver/nvidia/gpus/`` real, for whatever ``ls`` is asked.

    The NVIDIA driver puts one directory per GPU under that path, named after
    the device's PCI address. macOS has no ``/proc`` and the path cannot be
    created, so a real directory is built elsewhere and ``ls`` is shadowed by
    a shim that rewrites that one argument and hands everything else --
    including the flags the shipped code chose -- to the real ``/bin/ls``.
    Nothing about the listing is hand-written: the bytes clustrix parses are
    the bytes ``ls`` produces for a directory that really has that shape.

    ``forced_flags`` models the other thing a site ``ls`` can be: a wrapper,
    alias or shell function that prepends flags of its own, which is how
    ``--color`` and ``-C`` usually get turned on. The flags really are passed
    to the real ``ls``, ahead of the shipped command's own.
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
        f'exec /bin/ls {forced_flags} "$@"\n',
    )


class Host:
    """A live connection to the test host, and what it was set up with.

    Handed out by :func:`gpu_host` so a test can run detection *and* then
    exercise what the shipped code does with the answer -- the CUDA install
    in particular -- against the same real machine.
    """

    def __init__(self, ssh_client, config, work_dir, pip_log):
        self.ssh_client = ssh_client
        self.config = config
        self.work_dir = work_dir
        self._pip_log = pip_log

    def detect(self):
        return detect_gpu_capabilities(self.ssh_client, self.config)

    def pip_invocations(self):
        """Every argument list a real ``pip`` on the host's PATH was run with."""
        if not self._pip_log.exists():
            return []
        return [line for line in self._pip_log.read_text().splitlines() if line]


@contextmanager
def gpu_host(
    tmp_path,
    smi_stdout,
    smi_exit=0,
    lspci_stdout=None,
    proc_gpus=None,
    forced_ls_flags="",
):
    """A real host whose nvidia-smi, lspci and /proc listing say all this.

    ``nvcc`` and ``conda`` are shadowed with silent stubs; ``pip`` is a real
    executable that records how it was called and succeeds, so an install the
    shipped code decides to perform really happens and is visible. ``lspci``
    and the ``/proc/driver/nvidia/gpus/`` listing answer whatever the caller
    asks for -- by default, nothing -- so the answer comes from the responses
    under test rather than from whatever hardware runs the suite.
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
    _write_executable(bindir / "conda", "#!/bin/sh\nexit 1\n")
    pip_log = base / "pip_invocations"
    _write_executable(
        bindir / "pip",
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{pip_log}"\nexit 0\n',
    )
    if lspci_stdout is None:
        _write_executable(bindir / "lspci", "#!/bin/sh\nexit 1\n")
    else:
        # The listing is `lspci -nn` output and is printed for any argument
        # list; the shipped code passes `-nn`, and a command that dropped it
        # would still see these lines and still have to decide correctly
        # about the NVIDIA-branded non-GPU functions in them.
        lspci_payload = bindir / "lspci_payload"
        lspci_payload.write_text(lspci_stdout)
        _write_executable(bindir / "lspci", f'#!/bin/sh\ncat "{lspci_payload}"\n')
    if proc_gpus is not None:
        _install_proc_gpus_shim(bindir, base, proc_gpus, forced_ls_flags)

    root = base / "served"
    root.mkdir()
    # A real VENV2 to install into. `activate` defines `deactivate` because
    # the shipped install script calls it, exactly as a real venv's does.
    work_dir = root / "job"
    venv2_bin = work_dir / "venv2_execution" / "bin"
    venv2_bin.mkdir(parents=True)
    (venv2_bin / "activate").write_text("deactivate() { :; }\n")
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
            yield Host(connection.ssh_client, config, str(work_dir), pip_log)
        finally:
            connection.disconnect()


@contextmanager
def gpu_info(tmp_path, smi_stdout, **kwargs):
    """Just the detection result, for tests that need nothing else."""
    with gpu_host(tmp_path, smi_stdout, **kwargs) as host:
        yield host.detect()


def test_well_formed_output_is_still_parsed(tmp_path):
    """The honest yes must survive: two real rows, two real devices."""
    with gpu_info(tmp_path, WELL_FORMED) as info:
        assert info["gpu_available"] is True
        assert info["gpu_detection_inconclusive"] is False
        assert info["nvidia_driver_present"] is True
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


UNREADABLE_SMI = "0, NVIDIA A100-SXM4-40GB, 40536 MiB, 40122 MiB, 8.0"


@pytest.mark.parametrize(
    "description,listing",
    [
        ("a consumer card", LSPCI_ONE_CONSUMER_GPU),
        ("a datacenter card in class 0302", LSPCI_DATACENTER_GPU),
    ],
    ids=["consumer-card", "datacenter-card"],
)
def test_lspci_reports_presence_without_inventing_a_count(
    tmp_path, description, listing
):
    """One card, two PCI functions: "2" is not a number of GPUs.

    `lspci | grep -i nvidia | wc -l` counts PCI functions. A single consumer
    card presents the display controller and its companion HD Audio device,
    so the count is 2 on a one-GPU host -- and it was 2 that got assigned to
    ``gpu_count`` and printed as "GPU detected (2 devices)". lspci cannot
    count GPUs, so the count must be reported as unknown.

    The datacenter case is here because it enumerates as ``3D controller``
    and never as a VGA controller: a class match that forgot 0302 would see
    no GPU on any A100 host.
    """
    with gpu_info(tmp_path, UNREADABLE_SMI, lspci_stdout=listing) as info:
        # lspci really does prove a graphics device made by NVIDIA is fitted.
        assert info["gpu_available"] is True, description
        assert info["detection_method"] == "lspci", description
        # ...and really cannot say how many GPUs that is.
        assert info["gpu_count"] is None, description
        assert info["gpu_count"] != 2, description
        # No device list was read off any device.
        assert info["gpu_devices"] == [], description
        # It saw the bus, not the driver.
        assert info["nvidia_driver_present"] is False, description
        # And "could not tell" does not survive a fallback that told. This
        # is the assertion the mutant `inconclusive = smi_unreadable` needs:
        # without it a run can report available *and* inconclusive at once.
        assert info["gpu_detection_inconclusive"] is False, description


def test_lspci_message_states_no_number_it_does_not_have(tmp_path):
    """The sentence the user reads must not contain a fabricated count."""
    with gpu_info(
        tmp_path, UNREADABLE_SMI, lspci_stdout=LSPCI_ONE_CONSUMER_GPU
    ) as info:
        message = gpu_detection_summary(info)
        assert "2 devices" not in message
        assert "devices)" not in message
        assert "unknown" in message
        assert "lspci" in message
        # It must not promise a GPU VENV2 it is not going to build.
        assert "Setting up GPU-enabled VENV2" not in message
        assert "standard VENV2" in message


# NVIDIA sells more than GPUs (#172, adversarial review RT5-6).
#
# `lspci | grep -i nvidia` matched the vendor string, so any NVIDIA-branded
# PCI function said "GPU". Both listings below are real hardware with no
# usable NVIDIA GPU on the bus, and both used to set `gpu_available` -- which
# was, on its own, enough to make `setup_gpu_enabled_venv2` install a cu118
# PyTorch build.


@pytest.mark.parametrize(
    "description,listing",
    [
        (
            "an HD Audio function whose display sibling is gone",
            LSPCI_NVIDIA_AUDIO_FUNCTION_ONLY,
        ),
        ("an nForce chipset with ASPEED graphics", LSPCI_NFORCE_CHIPSET),
    ],
    ids=["nvidia-audio-function", "nforce-chipset"],
)
def test_nvidia_branded_non_gpu_hardware_is_not_a_gpu(tmp_path, description, listing):
    """The vendor is not the device class. Neither of these is a GPU."""
    with gpu_info(tmp_path, "", smi_exit=9, lspci_stdout=listing) as info:
        assert info["gpu_available"] is False, description
        assert info["detection_method"] != "lspci", description
        assert info["nvidia_driver_present"] is False, description
        assert gpu_detection_summary(info) == (
            "No GPUs detected, using standard VENV2 setup..."
        ), description


def test_a_cuda_install_needs_the_driver_not_a_card_on_the_bus(tmp_path):
    """lspci evidence must not buy a cu118 wheel. The driver's evidence must.

    Nothing is stubbed at the Python level: ``setup_gpu_enabled_venv2``
    really runs its install script over SSH against a real ``pip`` on the
    host's PATH, and what that ``pip`` was really called with is what is
    asserted. The requirement is a plain CPU ``torch``.
    """
    requirements = {"torch": "2.0.1"}

    # A card is fitted, but nothing here has ever spoken to a driver.
    with gpu_host(
        tmp_path, UNREADABLE_SMI, lspci_stdout=LSPCI_ONE_CONSUMER_GPU
    ) as host:
        info = host.detect()
        assert info["gpu_available"] is True
        result = setup_gpu_enabled_venv2(
            host.ssh_client, host.work_dir, requirements, info, host.config
        )
        assert host.pip_invocations() == [], (
            "an lspci match installed CUDA packages: lspci reads the PCI bus, "
            "so it cannot know the driver is loaded, the card is new enough "
            "for this CUDA build, or that the device is not passed through to "
            "a guest"
        )
        assert result["gpu_packages_installed"] is False
        assert result["pytorch_gpu_installed"] is False

    # The driver's own directory, which only the driver creates.
    with gpu_host(tmp_path, "", smi_exit=9, proc_gpus=["0000:01:00.0"]) as host:
        info = host.detect()
        assert info["nvidia_driver_present"] is True
        result = setup_gpu_enabled_venv2(
            host.ssh_client, host.work_dir, requirements, info, host.config
        )
        assert host.pip_invocations() == [
            "install torch torchvision torchaudio --index-url "
            "https://download.pytorch.org/whl/cu118 --timeout=600"
        ]
        assert result["pytorch_gpu_installed"] is True


def test_the_inconclusive_state_survives_when_nothing_else_can_tell(tmp_path):
    """No NVIDIA on the bus and no driver directory: "could not tell" stands.

    This is the state RT-1 found unreachable: every earlier test reached it
    only because `lspci` was stubbed to fail, so it was never shown to
    survive a fallback that actually ran and found nothing.
    """
    with gpu_info(
        tmp_path, UNREADABLE_SMI, lspci_stdout=LSPCI_NO_NVIDIA, proc_gpus=[]
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
        # Only the driver creates that tree, so this is driver evidence.
        assert info["nvidia_driver_present"] is True
        assert info["gpu_count"] == expected
        # A count, but still no device detail -- and the message says so.
        assert info["gpu_devices"] == []
        message = gpu_detection_summary(info)
        assert f"GPU detected ({expected} devices)" in message
        assert "no per-device details" in message


@pytest.mark.parametrize(
    "forced_flags",
    [
        # A site `ls` that columnises. `-C` is the usual companion of a
        # forced `--color`, and it survives the pipe: `ls` only defaults to
        # one entry per line when nobody asked for anything else.
        "-C",
        "-C --color=always",
    ],
)
def test_proc_driver_count_survives_an_ls_that_forces_columns(tmp_path, forced_flags):
    """Four GPUs are four GPUs however the site's `ls` likes to print.

    The parse counts lines, so the command has to ask for one entry per line
    rather than inherit it. A wrapper prepending `-C` packs four directory
    names onto one line and the host was reported as having a single GPU.
    """
    addresses = ["0000:07:00.0", "0000:0a:00.0", "0000:47:00.0", "0000:4d:00.0"]
    with gpu_info(
        tmp_path,
        "",
        smi_exit=9,
        proc_gpus=addresses,
        forced_ls_flags=forced_flags,
    ) as info:
        assert info["gpu_available"] is True
        assert info["detection_method"] == "/proc/driver/nvidia"
        assert info["gpu_count"] == 4, forced_flags


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
