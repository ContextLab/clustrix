"""``False`` from ``remote_file_exists`` must mean "the server said no".

Nothing here is mocked: a real in-process SSH server, real files, real
permissions, and a really-killed transport.

The old body caught every exception and answered ``False``, so "the transport
is dead", "you may not read that directory" and "I could not open a channel"
were all reported as "the file is not there". The polling loops in
``executor_scheduler_status`` read that answer as "the job has not finished
yet", so a connection that broke mid-run presented to the user as a job that
simply never completed -- with nothing in the log to say otherwise. That is
the difference between an error and a wrong answer, and it is why the decision
at this site is *raise* rather than *log and continue*.
"""

import os
import time

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from tests.ssh_server import LocalSSHServer

PASSWORD = "wrong_password"


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=PASSWORD) as running:
        yield running


@pytest.fixture
def manager(server):
    config = ClusterConfig(
        cluster_type="ssh",
        cluster_host=server.host,
        cluster_port=server.port,
        username="tester",
        password=PASSWORD,
        ssh_host_key_policy="auto_add",
        remote_work_dir=server.root,
    )
    connection = ConnectionManager(config)
    connection.setup_ssh_connection()
    try:
        yield connection
    finally:
        connection.disconnect()


def _settled_channels(manager) -> int:
    """Open channels on a live transport, once paramiko has stopped reaping.

    ``SFTPClient.close`` returns before the peer's close confirmation lands, so
    an immediate reading can show one channel on its way out. A genuine leak
    never settles, so polling cannot hide one.
    """
    transport = manager.ssh_client.get_transport()
    previous = -1
    for _ in range(50):
        current = len(transport._channels._map)
        if current == previous:
            return current
        previous = current
        time.sleep(0.02)
    return previous


# ---------------------------------------------------------------------------
# The two answers that really are answers
# ---------------------------------------------------------------------------


def test_a_present_file_is_true(manager, server):
    with open(os.path.join(server.root, "present.pkl"), "w") as handle:
        handle.write("x")
    assert manager.remote_file_exists("present.pkl") is True


def test_a_missing_file_is_false(manager):
    """The one exception that is an answer: the server said no such file."""
    assert manager.remote_file_exists("not-there.pkl") is False


# ---------------------------------------------------------------------------
# Everything else must raise rather than be mistaken for "not there"
# ---------------------------------------------------------------------------


def test_no_connection_raises_instead_of_answering_no(server):
    """With nothing connected there is no evidence about the file at all."""
    config = ClusterConfig(
        cluster_type="ssh",
        cluster_host=server.host,
        cluster_port=server.port,
        username="tester",
        password=PASSWORD,
        ssh_host_key_policy="auto_add",
        remote_work_dir=server.root,
    )
    connection = ConnectionManager(config)
    assert connection.ssh_client is None

    with pytest.raises(RuntimeError, match="not connected"):
        connection.remote_file_exists("anything.pkl")


def test_a_dead_transport_raises_instead_of_answering_no(manager):
    """A broken connection used to look exactly like a job still running."""
    manager.ssh_client.get_transport().close()

    with pytest.raises(Exception) as caught:
        manager.remote_file_exists("result.pkl")

    # Specifically not a bool, and specifically not FileNotFoundError.
    assert not isinstance(caught.value, FileNotFoundError)
    assert "not active" in str(caught.value).lower()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root can read a 0o000 directory, so there is nothing to deny",
)
def test_permission_denied_raises_instead_of_answering_no(manager, server):
    """ "You may not look" is not "it is not there"."""
    locked = os.path.join(server.root, "locked")
    os.mkdir(locked)
    with open(os.path.join(locked, "result.pkl"), "w") as handle:
        handle.write("x")
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(PermissionError):
            manager.remote_file_exists("locked/result.pkl")
    finally:
        os.chmod(locked, 0o755)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root can read a 0o000 directory, so there is nothing to deny",
)
def test_a_probe_that_raises_still_closes_its_channel(manager, server):
    """The new raise must not reintroduce the channel leak it replaced."""
    locked = os.path.join(server.root, "locked")
    os.mkdir(locked)
    with open(os.path.join(locked, "result.pkl"), "w") as handle:
        handle.write("x")
    os.chmod(locked, 0o000)
    try:
        baseline = _settled_channels(manager)
        for _ in range(10):
            with pytest.raises(PermissionError):
                manager.remote_file_exists("locked/result.pkl")
        assert (
            _settled_channels(manager) == baseline
        ), "a probe that raised left its SFTP channel open"
    finally:
        os.chmod(locked, 0o755)
