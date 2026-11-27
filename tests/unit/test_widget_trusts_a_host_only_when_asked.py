"""The widget's answer to a host this machine has never connected to.

Every new user meets this on their first ``Test connection``: clustrix
refuses a host whose key is not in ``known_hosts``. The refusal used to be a
wall of text ending in a shell command, which a notebook user cannot run
where they are. The widget now shows the fingerprint and offers a button --
and the button must do exactly one thing, only when pressed.

Everything here runs against the real in-process SSH server: a real socket,
a real handshake, a real ``known_hosts`` file under the test's own ``$HOME``.
"""

import pytest

pytest.importorskip("ipywidgets")

from clustrix import cluster, get_config  # noqa: E402
from clustrix.config import ClusterConfig, configure  # noqa: E402
from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402
from clustrix.ssh_security import user_known_hosts_path  # noqa: E402
from dataclasses import asdict  # noqa: E402
from tests.ssh_server import LocalSSHServer  # noqa: E402

import os  # noqa: E402
import sys  # noqa: E402


@pytest.fixture
def server(tmp_path):
    with LocalSSHServer(root=str(tmp_path / "remote"), password="s3cret") as running:
        yield running


@pytest.fixture
def widget(server, tmp_path):
    """A widget filled in for ``server``, starting from a default config."""
    (tmp_path / "remote").mkdir(exist_ok=True)
    configure(**asdict(ClusterConfig()))
    w = ModernClustrixWidget()
    w.widgets["cluster_type"].value = "ssh"
    w.widgets["host"].value = server.host
    w.widgets["port"].value = server.port
    w.widgets["username"].value = "tester"
    w.widgets["password"].value = "s3cret"
    w.widgets["home_dir"].value = str(tmp_path / "remote" / "jobs")
    # The job runs with this interpreter, so no environment is built.
    w.widgets["clone_env"].value = False
    w.widgets["python_executable"].value = sys.executable
    yield w
    configure(**asdict(ClusterConfig()))


def _pill(w) -> str:
    return w.widgets["status_pill"].value


def _trust_button_is_shown(w) -> bool:
    return w.widgets["trust_host_btn"].layout.display != "none"


def test_an_unknown_host_is_refused_and_the_fingerprint_is_shown(
    widget, server, capsys
):
    assert not _trust_button_is_shown(widget)

    widget._on_test_connect(None)

    printed = capsys.readouterr().out
    offered = server.host_keys()[0]
    assert "unknown host" in _pill(widget)
    assert f"[{server.host}]:{server.port}" in printed
    assert f"{offered.get_name()} SHA256:" in printed
    assert _trust_button_is_shown(widget)
    # Refusing is not trusting: nothing was written.
    assert not user_known_hosts_path().exists()


def test_nothing_is_trusted_until_the_button_is_pressed(widget, server):
    """Asking again -- or running a job -- must not quietly accept the key."""
    widget._on_test_connect(None)
    widget._on_test_connect(None)
    widget._on_test_submit(None)

    assert "unknown host" in _pill(widget)
    assert not user_known_hosts_path().exists()


def test_pressing_trust_records_the_key_and_connects(widget, server, capsys):
    widget._on_test_connect(None)
    widget._on_trust_host(None)

    printed = capsys.readouterr().out
    assert "connected" in _pill(widget) and "not connected" not in _pill(widget)
    assert "Connected to" in printed
    assert not _trust_button_is_shown(widget)
    lines = user_known_hosts_path().read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"[{server.host}]:{server.port} ")


def test_the_trust_button_does_nothing_without_a_refusal(widget):
    """The handler is reachable even while the button is hidden."""
    widget._on_trust_host(None)

    assert not user_known_hosts_path().exists()


def test_a_wrong_password_is_reported_as_such_not_as_an_unknown_host(
    widget, server, capsys
):
    widget._on_test_connect(None)
    widget._on_trust_host(None)
    widget.widgets["password"].value = "wrong"
    capsys.readouterr()

    widget._on_test_connect(None)

    assert "not connected" in _pill(widget)
    assert "Authentication" in capsys.readouterr().out
    assert not _trust_button_is_shown(widget)


def test_after_trust_a_real_job_runs_on_the_host_and_apply_goes_live(
    widget, server, tmp_path, capsys
):
    """The whole first-run path: refuse, trust, run a job, apply, call."""

    @cluster(cores=1)
    def where_am_i():
        import os

        return os.getcwd(), os.getpid()

    # Decorated before Apply, and nothing is configured yet: runs here.
    assert where_am_i()[1] == os.getpid()

    widget._on_test_connect(None)
    widget._on_trust_host(None)
    widget._on_test_submit(None)
    assert "job completed" in _pill(widget)
    assert "returned  5050" in capsys.readouterr().out

    widget._on_apply_config(None)
    assert "in use" in _pill(widget)
    assert get_config().cluster_host == server.host

    # The same function object, called again, now runs on the host.
    remote_cwd, remote_pid = where_am_i()
    assert remote_pid != os.getpid()
    assert remote_cwd.startswith(str(tmp_path / "remote" / "jobs"))

    # And pointing the form back at this machine brings it home again.
    widget.widgets["cluster_type"].value = "local"
    assert "not applied" in _pill(widget)
    widget._on_apply_config(None)
    assert where_am_i()[1] == os.getpid()
