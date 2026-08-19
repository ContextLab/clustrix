"""Tests for clustrix.ssh_security -- the shared SSH host-key verification
policy (#121 item 2).

Every one of clustrix's ~12 paramiko.SSHClient call sites used to call
``set_missing_host_key_policy(paramiko.AutoAddPolicy())`` unconditionally,
which trusts *any* host key on first connection with no verification -- a
textbook machine-in-the-middle hole. These tests exercise the real
paramiko machinery (a real local SSH server over a real TCP socket, real
generated host keys, a real known_hosts file on disk) rather than mocking
paramiko, because the entire point of this fix is that paramiko's actual
handshake behaves correctly -- a mock could not catch a regression here.
"""

import os
import socket
import threading
import time

import paramiko
import pytest

from clustrix.config import ClusterConfig
from clustrix.ssh_security import (
    HostKeyVerificationError,
    RejectUnknownHostKeyPolicy,
    VALID_HOST_KEY_POLICIES,
    configure_host_key_policy,
)


class _AuthRejectingServer(paramiko.ServerInterface):
    """Minimal real SSH server: completes the handshake, rejects all auth.

    We don't need real authentication to succeed -- we only need a genuine
    SSH transport-level handshake (key exchange, host key exchange) to
    happen over a real socket, since that's the exact point at which
    paramiko invokes the client's missing_host_key policy.
    """

    def check_auth_password(self, username, password):
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"


class _RealLocalSSHServer:
    """A real SSH server bound to 127.0.0.1 on an ephemeral port.

    Used to prove host-key verification against a genuine SSH handshake,
    not a mocked one.
    """

    def __init__(self):
        self.host_key = paramiko.RSAKey.generate(2048)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_once, daemon=True)

    def start(self):
        self._thread.start()

    def _serve_once(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            transport = paramiko.Transport(conn)
            transport.add_server_key(self.host_key)
            transport.start_server(server=_AuthRejectingServer())
            # Give the client time to attempt auth (and fail) before the
            # transport is torn down.
            time.sleep(2)
            transport.close()
        except Exception:
            pass

    def stop(self):
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def real_ssh_server():
    server = _RealLocalSSHServer()
    server.start()
    time.sleep(0.1)  # let the accept() loop actually reach listening state
    yield server
    server.stop()


def test_reject_policy_blocks_connection_to_real_unknown_host(real_ssh_server):
    """End-to-end: connecting to a real SSH server whose host key is not in
    any known_hosts file must fail with HostKeyVerificationError, not
    silently succeed.
    """
    client = paramiko.SSHClient()
    config = ClusterConfig(ssh_host_key_policy="reject")
    configure_host_key_policy(client, config)

    with pytest.raises(HostKeyVerificationError) as exc_info:
        client.connect(
            hostname="127.0.0.1",
            port=real_ssh_server.port,
            username="nobody",
            password="fake-unused-password",
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
            look_for_keys=False,
            allow_agent=False,
        )

    message = str(exc_info.value)
    assert "127.0.0.1" in message, message
    assert "ssh-keyscan" in message, message
    assert "known_hosts" in message, message
    client.close()


def test_reject_policy_is_the_clusterconfig_default():
    assert ClusterConfig().ssh_host_key_policy == "reject"


def test_auto_add_policy_gets_past_host_key_check_to_real_auth(real_ssh_server):
    """With the explicit opt-out, the same real unknown host must NOT be
    rejected at the host-key stage -- it should reach (and fail at) real
    authentication instead, proving the host-key gate was bypassed as
    requested rather than the connection just failing for some other reason.
    """
    client = paramiko.SSHClient()
    config = ClusterConfig(ssh_host_key_policy="auto_add")
    configure_host_key_policy(client, config)

    with pytest.raises(paramiko.AuthenticationException):
        client.connect(
            hostname="127.0.0.1",
            port=real_ssh_server.port,
            username="nobody",
            password="fake-unused-password",
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
            look_for_keys=False,
            allow_agent=False,
        )
    client.close()


def test_missing_config_defaults_to_reject(real_ssh_server):
    """Sites that can't easily plumb a ClusterConfig through (config=None)
    must still be secure by default -- there is no code path that becomes
    silently insecure for lack of a config object.
    """
    client = paramiko.SSHClient()
    configure_host_key_policy(client, None)

    with pytest.raises(HostKeyVerificationError):
        client.connect(
            hostname="127.0.0.1",
            port=real_ssh_server.port,
            username="nobody",
            password="fake-unused-password",
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
            look_for_keys=False,
            allow_agent=False,
        )
    client.close()


def test_invalid_policy_value_raises_at_config_construction():
    """A typo in ssh_host_key_policy must fail loudly and immediately, not
    silently fall back to something insecure.
    """
    with pytest.raises(ValueError, match="ssh_host_key_policy"):
        ClusterConfig(ssh_host_key_policy="yolo")


def test_invalid_policy_value_raises_in_configure_host_key_policy():
    """Belt-and-suspenders: even if an object other than ClusterConfig (one
    that skips __post_init__ validation) is passed in with a bad value,
    configure_host_key_policy itself must still refuse it.
    """

    class _FakeConfig:
        ssh_host_key_policy = "yolo"

    client = paramiko.SSHClient()
    with pytest.raises(ValueError, match="Invalid ssh_host_key_policy"):
        configure_host_key_policy(client, _FakeConfig())


def test_reject_unknown_host_key_policy_message_names_the_real_key():
    """Directly exercise RejectUnknownHostKeyPolicy.missing_host_key with a
    real generated paramiko key (no mocking) to verify the error names the
    actual key type and a fingerprint, which is what a user needs to
    manually verify the key via `ssh-keyscan` output before trusting it.
    """
    real_key = paramiko.RSAKey.generate(2048)
    client = paramiko.SSHClient()
    policy = RejectUnknownHostKeyPolicy()

    with pytest.raises(HostKeyVerificationError) as exc_info:
        policy.missing_host_key(client, "suspicious-host.example.com", real_key)

    message = str(exc_info.value)
    assert "suspicious-host.example.com" in message
    assert "ssh-rsa" in message
    assert "SHA256:" in message


def test_valid_host_key_policies_are_exactly_reject_and_auto_add():
    assert set(VALID_HOST_KEY_POLICIES) == {"reject", "auto_add"}


def test_user_known_hosts_file_is_actually_loaded(tmp_path, monkeypatch):
    """configure_host_key_policy must load ~/.ssh/known_hosts for real, so a
    host the user has already verified out-of-band (e.g. via ssh-keyscan)
    is recognized and does NOT trigger the reject policy.
    """
    fake_home = tmp_path / "home"
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True)

    trusted_key = paramiko.RSAKey.generate(2048)
    known_hosts_path = ssh_dir / "known_hosts"
    known_hosts_path.write_text(
        f"127.0.0.1 {trusted_key.get_name()} {trusted_key.get_base64()}\n"
    )

    monkeypatch.setenv("HOME", str(fake_home))
    if os.name == "nt":
        # Path.home() expands ``~`` from USERPROFILE (falling back to
        # HOMEDRIVE+HOMEPATH) on Windows and ignores HOME, so redirecting
        # HOME alone would leave clustrix reading the real user's
        # ~/.ssh/known_hosts.
        drive, tail = os.path.splitdrive(str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))
        monkeypatch.setenv("HOMEDRIVE", drive)
        monkeypatch.setenv("HOMEPATH", tail)

    client = paramiko.SSHClient()
    configure_host_key_policy(client, None)

    loaded = client.get_host_keys()
    assert loaded.lookup("127.0.0.1") is not None, (
        "known_hosts entry for 127.0.0.1 was not loaded from the real "
        f"file at {known_hosts_path}"
    )
    found_key = loaded.lookup("127.0.0.1")[trusted_key.get_name()]
    assert found_key.get_base64() == trusted_key.get_base64()
