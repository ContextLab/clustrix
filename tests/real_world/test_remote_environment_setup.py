"""``setup_remote_environment`` building a real virtualenv on a real host.

This is the single-venv path a job takes with ``use_two_venv=False``: create
``venv`` in the job directory and install dill and cloudpickle into it, pinned
to the caller's versions. The tests it replaces handed the function a
``Mock`` for the SSH client and asserted on the command string it built. Here
the command runs: OpenSSH carries it, the host's own Python creates the
virtualenv, and the host's own pip or uv downloads from a package index. What
is asserted is what ended up installed, read back from the virtualenv.

``tests/test_enhanced_features.py`` holds the copy of these that CI can run,
against the in-process SSH server with stand-ins for the installers. Those
stand-ins are only worth anything because the real installers were driven
here first.

Skipped, with the reason, unless the local test cluster is named; see
``tests/real_world/test_local_sshd_slurm.py``. Each test needs the host to
reach a package index.
"""

import uuid

import cloudpickle
import dill
import pytest

from clustrix import configure, get_config
from clustrix.executor import ClusterExecutor
from clustrix.utils import setup_remote_environment

from .test_local_sshd_slurm import (
    HOST,
    KEY,
    PORT,
    USER,
    _remote,
    pytestmark,
)  # noqa: F401

PINNED = {"dill": dill.__version__, "cloudpickle": cloudpickle.__version__}

#: Prints, for each distribution named, its version and the tool that
#: installed it. Installers record themselves in ``INSTALLER`` (PEP 376),
#: which is the one trace that tells a pip install from a uv one afterwards.
REPORT = (
    "import sys; from importlib.metadata import distribution\n"
    "for name in sys.argv[1:]:\n"
    "    found = distribution(name)\n"
    "    print(name, found.version, (found.read_text('INSTALLER') or '').strip())\n"
)


@pytest.fixture
def remote_host():
    """A connected client, the live configuration and an empty directory."""
    home = _remote("echo $HOME")
    if home.returncode != 0:
        pytest.skip(f"The test host {HOST}:{PORT} did not answer: {home.stderr}")
    # Absolute: the function quotes the directory, so a ``~`` would not expand.
    work_dir = f"{home.stdout.strip()}/clustrix-envsetup-tests/{uuid.uuid4().hex}"
    assert _remote(f"mkdir -p {work_dir}").returncode == 0
    configure(
        cluster_type="ssh",
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        # Each test gets a fresh HOME, so the host can never already be known.
        ssh_host_key_policy="auto_add",
        remote_work_dir=work_dir,
    )
    config = get_config()
    try:
        with ClusterExecutor(config) as executor:
            executor.connect()
            yield executor.connection_manager.ssh_client, config, work_dir
    finally:
        _remote(f"rm -rf {work_dir}")


def _installed(work_dir, *names):
    """``{name: (version, installer)}`` as the remote virtualenv reports it."""
    report = _remote(
        f"cd {work_dir} && cat > report.py <<'CLUSTRIX_EOF'\n{REPORT}CLUSTRIX_EOF\n"
        f"venv/bin/python report.py {' '.join(names)}"
    )
    assert report.returncode == 0, report.stderr
    return {
        name: (version, installer)
        for name, version, installer in (
            line.split() for line in report.stdout.splitlines()
        )
    }


def _host_has_uv():
    return bool(_remote("command -v uv").stdout.strip())


def test_pip_installs_the_callers_serializer_versions_into_a_new_virtualenv(
    remote_host,
):
    client, config, work_dir = remote_host
    config.package_manager = "pip"

    outcome = setup_remote_environment(client, work_dir, dict(PINNED), config)

    assert outcome == "Environment setup completed successfully"
    assert _installed(work_dir, "dill", "cloudpickle") == {
        "dill": (dill.__version__, "pip"),
        "cloudpickle": (cloudpickle.__version__, "pip"),
    }


def test_uv_is_the_installer_when_the_configuration_names_it(remote_host):
    if not _host_has_uv():
        pytest.skip(f"The test host {HOST} has no uv, so a uv install cannot run.")
    client, config, work_dir = remote_host
    config.package_manager = "uv"

    setup_remote_environment(client, work_dir, dict(PINNED), config)

    assert _installed(work_dir, "dill", "cloudpickle") == {
        "dill": (dill.__version__, "uv"),
        "cloudpickle": (cloudpickle.__version__, "uv"),
    }


def test_auto_uses_what_the_remote_host_has_not_what_this_machine_has(
    remote_host, monkeypatch, tmp_path
):
    """``package_manager`` describes the remote build, so the remote is asked.

    This machine is made to have no ``uv`` at all for the duration: an empty
    directory is the whole of its ``PATH``. If the decision were taken here,
    as it once was, the installer would be pip whatever the host has.
    """
    client, config, work_dir = remote_host
    config.package_manager = "auto"
    expected = "uv" if _host_has_uv() else "pip"

    with monkeypatch.context() as this_machine:
        this_machine.setenv("PATH", str(tmp_path))
        setup_remote_environment(client, work_dir, dict(PINNED), config)

    assert _installed(work_dir, "dill")["dill"] == (dill.__version__, expected)


def test_a_pin_the_index_cannot_satisfy_falls_back_to_an_unpinned_install(
    remote_host,
):
    """A job without dill cannot start, so some dill beats no dill."""
    client, config, work_dir = remote_host
    config.package_manager = "pip"

    setup_remote_environment(
        client, work_dir, {"dill": "0.0.0.dev987654321", "cloudpickle": "0"}, config
    )

    installed = _installed(work_dir, "dill", "cloudpickle")
    assert installed["dill"][0] != "0.0.0.dev987654321"
    assert installed["cloudpickle"][0] != "0"
