"""SFTP channels must not accumulate over the life of a connection.

Nothing here is mocked: a real in-process SSH server, the shipped
``ConnectionManager``, and paramiko's own channel table as the measurement.

The bug this pins was not on an error path. ``remote_file_exists`` answers
"no" by letting ``sftp.stat`` raise, and its ``sftp.close()`` sat inside the
``try`` -- so the *expected* answer leaked a channel every time, and the
exception that caused it was swallowed. A submitter polling for a result
file that is not there yet does exactly that, in a loop.
"""

import time

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from tests.ssh_server import LocalSSHServer


def _open_channels(manager) -> int:
    """How many channels are still open on this transport, once settled.

    ``SFTPClient.close`` returns before paramiko has reaped the channel, so a
    reading taken immediately after a call can show one extra that is on its
    way out. Poll briefly for the count to stop moving rather than sleeping a
    fixed amount: a genuine leak never settles, so this cannot hide one -- it
    only stops the test flaking on the last close in a loop.
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


@pytest.fixture
def connected(tmp_path):
    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(root=str(root), password="wrong_password") as server:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password="wrong_password",
            ssh_host_key_policy="auto_add",
            remote_work_dir=str(root),
        )
        manager = ConnectionManager(config)
        manager.setup_ssh_connection()
        try:
            yield manager, root
        finally:
            manager.disconnect()


def test_asking_about_a_missing_file_does_not_leak_a_channel(connected):
    manager, _ = connected
    baseline = _open_channels(manager)

    for _ in range(25):
        assert manager.remote_file_exists("not-there.pkl") is False

    assert _open_channels(manager) == baseline, (
        "remote_file_exists leaked a channel per call; a submitter polling "
        "for a result file would exhaust the transport"
    )


def test_asking_about_a_present_file_does_not_leak_a_channel(connected):
    manager, root = connected
    (root / "present.txt").write_text("here")
    baseline = _open_channels(manager)

    for _ in range(25):
        assert manager.remote_file_exists("present.txt") is True

    assert _open_channels(manager) == baseline


def test_upload_and_download_do_not_leak_channels(connected):
    manager, root = connected
    source = root / "source.bin"
    source.write_bytes(b"payload")
    baseline = _open_channels(manager)

    for i in range(10):
        manager.upload_file(str(source), f"{root}/copy-{i}.bin")
        manager.download_file(f"{root}/copy-{i}.bin", str(root / f"back-{i}.bin"))

    assert _open_channels(manager) == baseline
    assert (root / "back-9.bin").read_bytes() == b"payload"


def test_a_failed_upload_still_closes_its_channel(connected):
    manager, root = connected
    baseline = _open_channels(manager)

    for _ in range(10):
        with pytest.raises(Exception):
            manager.upload_file(str(root / "no-such-source"), f"{root}/dest.bin")

    assert (
        _open_channels(manager) == baseline
    ), "an upload that raised left its channel open"
