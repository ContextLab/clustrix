"""Teardown has to be deterministic, and it has to be observable.

Nothing here is mocked: a real in-process SSH server, the shipped
``ClusterExecutor``/``ConnectionManager``, and paramiko's own transport and
channel tables as the measurement. Every assertion is about a resource
actually being released -- ``Transport.is_active()``, ``Channel.closed``, the
transport's channel map -- never about a method having returned.

Before this, cleanup ran only from ``ClusterExecutor.__del__``. A finaliser
runs at an interpreter-defined time or not at all, so an SSH transport and its
SFTP channels stayed open for an unbounded stretch after the last use, and on
the exception path there was no guarantee at all.
"""

import pathlib
import threading
import time

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from clustrix.executor_core import ClusterExecutor
from tests.ssh_server import LocalSSHServer

PASSWORD = "wrong_password"


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=PASSWORD) as running:
        yield running


def _root(server) -> pathlib.Path:
    return pathlib.Path(server.root)


def _config(server) -> ClusterConfig:
    return ClusterConfig(
        cluster_type="ssh",
        cluster_host=server.host,
        cluster_port=server.port,
        username="tester",
        password=PASSWORD,
        ssh_host_key_policy="auto_add",
        remote_work_dir=server.root,
    )


def _open_channels(transport) -> int:
    """Channels paramiko still has on a **live** transport.

    Only meaningful while the transport is up. ``Transport.close()`` unlinks
    its channels through ``Channel._unlink()``, which returns early
    ``if self.closed`` -- so a channel the caller closed itself keeps its map
    entry until the peer's close confirmation arrives (~50ms here), and if the
    transport is torn down inside that window the entry is never removed. That
    entry holds no file descriptor; it is bookkeeping on a dead object. Use
    :func:`_socket_fd` to assert on teardown, not this.
    """
    return len(transport._channels._map)


def _socket_fd(transport) -> int:
    """The transport's real OS file descriptor, or ``-1`` once it is closed.

    This is the assertion that means something after teardown: a closed
    ``socket`` reports ``fileno() == -1``, so this is the kernel's own answer
    about whether the resource was released, not paramiko's.
    """
    return transport.sock.fileno()


# ---------------------------------------------------------------------------
# ClusterExecutor
# ---------------------------------------------------------------------------


def test_cluster_executor_with_block_closes_the_transport(server):
    """The transport is dead the instant the ``with`` block ends."""
    with ClusterExecutor(_config(server)) as executor:
        executor.connect()
        transport = executor.ssh_client.get_transport()
        assert transport.is_active()

    assert (
        not transport.is_active()
    ), "the with block exited but the SSH transport is still up"
    assert (
        _socket_fd(transport) == -1
    ), "the with block exited but the socket is still open"
    assert executor.ssh_client is None
    assert executor.sftp_client is None


def test_cluster_executor_with_block_closes_the_transport_when_the_body_raises(server):
    """The exception path releases exactly what the happy path releases."""
    executor = ClusterExecutor(_config(server))
    executor.connect()
    transport = executor.ssh_client.get_transport()
    sftp = executor.sftp_client  # a real, open SFTP channel
    assert transport.is_active()
    assert _open_channels(transport) == 1

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with executor:
            raise Boom("something went wrong mid-job")

    assert (
        not transport.is_active()
    ), "an exception in the body leaked the SSH transport"
    assert _socket_fd(transport) == -1, "an exception in the body leaked the socket"
    assert sftp.get_channel().closed, "an exception in the body leaked the SFTP channel"
    assert executor.ssh_client is None
    assert executor.sftp_client is None


def test_cluster_executor_with_block_works_for_a_backend_with_no_host():
    """``local`` has nothing to dial, and must still be usable under ``with``."""
    with ClusterExecutor(ClusterConfig(cluster_type="local")) as executor:
        assert executor.ssh_client is None
        assert executor.sftp_client is None


def test_cluster_executor_with_block_returns_the_executor(server):
    executor = ClusterExecutor(_config(server))
    with executor as bound:
        assert bound is executor


def test_cluster_executor_exit_does_not_swallow_the_body_exception(server):
    """``__exit__`` must not return a truthy value."""
    with pytest.raises(ValueError, match="propagate me"):
        with ClusterExecutor(_config(server)):
            raise ValueError("propagate me")


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------


def test_connection_manager_with_block_connects_and_closes(server):
    with ConnectionManager(_config(server)) as manager:
        assert manager.ssh_client is not None
        transport = manager.ssh_client.get_transport()
        assert transport.is_active()

    assert not transport.is_active()
    assert _socket_fd(transport) == -1
    assert manager.ssh_client is None
    assert manager.sftp_client is None


def test_connection_manager_with_block_closes_on_the_exception_path(server):
    manager = ConnectionManager(_config(server))
    with pytest.raises(RuntimeError):
        with manager:
            transport = manager.ssh_client.get_transport()
            manager.sftp_client.listdir(".")  # force a channel open
            assert _open_channels(transport) == 1
            raise RuntimeError("boom")

    assert not transport.is_active()
    assert _socket_fd(transport) == -1, "the transport's socket outlived the with block"
    assert manager.ssh_client is None


def test_disconnect_is_idempotent(server):
    """A second disconnect must not re-close a half-closed object."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    manager.sftp_client.listdir(".")

    manager.disconnect()
    manager.disconnect()

    assert manager.ssh_client is None
    assert manager.sftp_client is None


def test_disconnect_clears_the_cached_remote_home(server):
    """A home cached from a previous account must not survive a reconnect."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    manager.resolve_remote_path("~/work")
    assert manager._remote_home is not None

    manager.disconnect()

    assert manager._remote_home is None


# ---------------------------------------------------------------------------
# The lazily opened sftp_client is a resource too
# ---------------------------------------------------------------------------


def test_connecting_opens_no_sftp_channel(server):
    """``setup_ssh_connection`` used to open a channel nothing ever read."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    try:
        transport = manager.ssh_client.get_transport()
        assert (
            _open_channels(transport) == 0
        ), "connecting opened an SFTP channel before anyone asked for one"
    finally:
        manager.disconnect()


def test_sftp_client_opens_once_on_first_access_and_is_cached(server):
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    try:
        transport = manager.ssh_client.get_transport()

        first = manager.sftp_client
        assert first is not None
        assert _open_channels(transport) == 1

        second = manager.sftp_client
        assert second is first
        assert (
            _open_channels(transport) == 1
        ), "reading the property twice opened two channels"

        # It is a real, working SFTP client, not a placeholder.
        (_root(server) / "marker.txt").write_text("hello")
        assert "marker.txt" in first.listdir(".")
    finally:
        manager.disconnect()


def test_sftp_client_is_none_when_there_is_no_connection(server):
    """Reading an attribute must not dial out."""
    manager = ConnectionManager(_config(server))
    assert manager.sftp_client is None


def test_disconnect_closes_a_lazily_opened_sftp_channel(server):
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    transport = manager.ssh_client.get_transport()
    sftp = manager.sftp_client
    assert _open_channels(transport) == 1

    manager.disconnect()

    assert sftp.get_channel().closed
    assert _socket_fd(transport) == -1


def test_sftp_client_can_still_be_assigned(server):
    """The setter is part of the public surface; assignment must stick."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    try:
        replacement = manager.ssh_client.open_sftp()
        manager.sftp_client = replacement
        assert manager.sftp_client is replacement
    finally:
        manager.disconnect()


def test_executor_sftp_client_property_tracks_the_connection_manager(server):
    executor = ClusterExecutor(_config(server))
    executor.connect()
    try:
        assert executor.sftp_client is executor.connection_manager.sftp_client
        replacement = executor.ssh_client.open_sftp()
        executor.sftp_client = replacement
        assert executor.connection_manager.sftp_client is replacement
    finally:
        executor.disconnect()


def test_disconnect_releases_the_transport_when_sftp_was_never_opened(server):
    """The teardown path must not depend on the lazy channel having been used."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    transport = manager.ssh_client.get_transport()

    manager.disconnect()

    assert _socket_fd(transport) == -1
    assert manager.sftp_client is None


def test_disconnect_closes_an_sftp_channel_the_caller_left_open(server):
    """Whether or not the channel was opened, disconnect owns closing it."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    transport = manager.ssh_client.get_transport()
    channel = manager.sftp_client.get_channel()
    assert not channel.closed

    manager.disconnect()

    assert channel.closed
    assert _socket_fd(transport) == -1


# ---------------------------------------------------------------------------
# Reconnecting, and racing for the first channel
# ---------------------------------------------------------------------------


def test_reconnecting_hands_back_a_channel_that_actually_works(server):
    """``setup_ssh_connection`` twice must not leave a dead channel cached.

    It is the method every "SSH client not connected" error tells the caller
    to run, so it is the reconnect path whether or not it was designed as one.
    Before the fix the lazy cache survived it: the manager reported itself
    connected and handed back the channel from the *previous*, dead transport,
    which answered ``OSError: Socket is closed``. The proof here is a round
    trip to the real server, not object identity.
    """
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    (_root(server) / "before.txt").write_text("x")
    assert "before.txt" in manager.sftp_client.listdir(".")

    # The connection drops the way a real one does: from underneath the
    # caller, with no chance to call disconnect().
    manager.ssh_client.get_transport().close()

    manager.setup_ssh_connection()
    try:
        (_root(server) / "after.txt").write_text("y")
        assert "after.txt" in manager.sftp_client.listdir(
            "."
        ), "reconnecting handed back the channel from the dead transport"
    finally:
        manager.disconnect()


def test_reconnecting_closes_the_previous_channel_rather_than_dropping_it(server):
    """The stale channel is released, not merely forgotten."""
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    stale = manager.sftp_client
    old_transport = manager.ssh_client.get_transport()
    assert not stale.get_channel().closed

    manager.setup_ssh_connection()
    try:
        assert (
            stale.get_channel().closed
        ), "the channel from the previous transport was dropped, not closed"
        assert _socket_fd(old_transport) == -1, "the previous transport was leaked"

        fresh = manager.sftp_client
        assert fresh is not stale
        assert _open_channels(manager.ssh_client.get_transport()) == 1
        assert isinstance(fresh.listdir("."), list)
    finally:
        manager.disconnect()


def test_concurrent_first_access_opens_exactly_one_channel(server):
    """Four threads reaching the unopened property together open one channel.

    Deterministic by construction rather than by luck: the first thread into
    ``open_sftp`` is held there until the others have had every opportunity to
    enter it too, so the check-then-set window is forced wide open instead of
    being hoped for. Against the unguarded property this produced four
    channels every time, three of them orphaned and still open.

    The wrapper delays the real ``open_sftp`` and then calls it -- the channels
    counted below are real channels on the real server.
    """
    manager = ConnectionManager(_config(server))
    manager.setup_ssh_connection()
    try:
        transport = manager.ssh_client.get_transport()
        real_open_sftp = manager.ssh_client.open_sftp
        entered = []
        entered_lock = threading.Lock()
        release = threading.Event()

        def open_sftp_held_open():
            with entered_lock:
                entered.append(threading.current_thread().name)
            release.wait(30)
            return real_open_sftp()

        manager.ssh_client.open_sftp = open_sftp_held_open

        start = threading.Barrier(4)
        results = []

        def read_the_property():
            start.wait(30)
            results.append(manager.sftp_client)

        threads = [threading.Thread(target=read_the_property) for _ in range(4)]
        for thread in threads:
            thread.start()

        # Give the unguarded behaviour every chance to show itself: with no
        # lock all four threads reach open_sftp, because the first one cannot
        # return until `release` is set. Break as soon as that happens so the
        # failing case is fast; the passing case waits out the window.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with entered_lock:
                if len(entered) == 4:
                    break
            time.sleep(0.01)
        release.set()
        for thread in threads:
            thread.join(30)

        assert (
            len(entered) == 1
        ), f"{len(entered)} threads opened a channel; the property raced"
        assert len(results) == 4
        assert all(result is results[0] for result in results)
        assert (
            _open_channels(transport) == 1
        ), "concurrent first access left orphaned channels open on the transport"
        assert isinstance(results[0].listdir("."), list)
    finally:
        manager.disconnect()
