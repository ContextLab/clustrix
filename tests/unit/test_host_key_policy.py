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

import contextlib
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import paramiko
import pytest

from clustrix.config import ClusterConfig
from clustrix.ssh_security import (
    HostKeyVerificationError,
    OPENSSH_STRICT_HOST_KEY_CHECKING,
    RejectUnknownHostKeyPolicy,
    VALID_HOST_KEY_POLICIES,
    configure_host_key_policy,
    host_key_policy_name,
    openssh_strict_host_key_checking,
    user_known_hosts_path,
)
from tests.ssh_server import LocalSSHServer


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


def _redirect_home(monkeypatch, home):
    """Point ``~`` at `home` for both POSIX and Windows expansion rules."""
    monkeypatch.setenv("HOME", str(home))
    if os.name == "nt":
        # Path.home() expands ``~`` from USERPROFILE (falling back to
        # HOMEDRIVE+HOMEPATH) on Windows and ignores HOME, so redirecting
        # HOME alone would leave clustrix reading the real user's
        # ~/.ssh/known_hosts.
        drive, tail = os.path.splitdrive(str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("HOMEDRIVE", drive)
        monkeypatch.setenv("HOMEPATH", tail)


@pytest.fixture
def real_ssh_server(tmp_path, monkeypatch):
    """A real local SSH server, with ``~`` pointed at a per-test directory.

    The HOME redirection is not cosmetic. `configure_host_key_policy` calls
    `load_host_keys(~/.ssh/known_hosts)`, which sets paramiko's
    `_host_keys_filename`; paramiko's AutoAddPolicy then *saves* every key it
    accepts back to that file. Without this fixture the auto_add test below
    appended an `[127.0.0.1]:<ephemeral port>` entry to the developer's real
    known_hosts on every run -- 83 of them had accumulated on the machine
    where this was found. Ephemeral ports are eventually reused, and when one
    came back around against a freshly generated server key, paramiko raised
    BadHostKeyException instead of invoking the missing-host-key policy and
    `test_reject_policy_blocks_connection_to_real_unknown_host` failed. The
    test was not wrong about the behaviour it asserts; it was poisoning its
    own precondition (that the host is unknown) one run at a time.
    """
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    _redirect_home(monkeypatch, home)

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

    _redirect_home(monkeypatch, fake_home)

    client = paramiko.SSHClient()
    configure_host_key_policy(client, None)

    loaded = client.get_host_keys()
    assert loaded.lookup("127.0.0.1") is not None, (
        "known_hosts entry for 127.0.0.1 was not loaded from the real "
        f"file at {known_hosts_path}"
    )
    found_key = loaded.lookup("127.0.0.1")[trusted_key.get_name()]
    assert found_key.get_base64() == trusted_key.get_base64()


def test_auto_add_persists_the_key_it_accepted(real_ssh_server, tmp_path):
    """``auto_add`` must WRITE the key it accepted, not just tolerate it.

    This covers a line that reads like dead code and is not.
    ``_load_known_hosts`` calls ``load_system_host_keys()`` and then
    ``load_host_keys(~/.ssh/known_hosts)``. paramiko puts those in different
    places: the first fills ``_system_host_keys``, consulted when verifying
    and never written back; the second also sets ``_host_keys_filename``, and
    ``AutoAddPolicy.missing_host_key`` saves only when that attribute is set.

    Delete the second call and verification still works, so almost every test
    stays green -- while ``auto_add`` silently stops persisting anything and
    re-accepts the same host on every connection forever. An adversarial
    review reported the line as redundant on the strength of a surviving
    mutant; this is the test that was missing rather than a line that was
    spare.
    """
    known_hosts = Path(os.path.expanduser("~")) / ".ssh" / "known_hosts"
    assert not known_hosts.exists(), "fixture should start with no known_hosts"

    config = ClusterConfig(ssh_host_key_policy="auto_add")
    client = paramiko.SSHClient()
    configure_host_key_policy(client, config)

    with contextlib.suppress(paramiko.AuthenticationException):
        client.connect(
            hostname="127.0.0.1",
            port=real_ssh_server.port,
            username="tester",
            password="wrong_password",  # a form check_for_secrets suppresses
            timeout=5,
            allow_agent=False,
            look_for_keys=False,
        )
    client.close()

    assert known_hosts.exists(), (
        "auto_add accepted the host key but never wrote it: "
        "_host_keys_filename was not set, so AutoAddPolicy's save was skipped"
    )
    recorded = known_hosts.read_text()
    assert f"[127.0.0.1]:{real_ssh_server.port}" in recorded


# ---------------------------------------------------------------------------
# The OpenSSH subprocess obeys the same policy as the paramiko clients.
#
# ``ssh_utils.deploy_public_key`` shells out to ``ssh-copy-id`` *before* it
# reaches paramiko, and that subprocess used to (a) hardcode
# ``StrictHostKeyChecking=accept-new``, silently applying the deliberate
# ``auto_add`` opt-out to every user, and (b) be preceded by an
# unconditional ``add_host_key`` -- an ``ssh-keyscan`` whose output is
# appended to the user's real ``known_hosts``, permanently marking a host
# named by a working-directory ``clustrix.yml`` as verified for every future
# connection this machine makes.
#
# **Stated limit.** These tests say what clustrix does with the policy it is
# given. They do not say where the policy came from: ``ssh_host_key_policy``
# is an ordinary declared field, so a configuration file the user did not
# choose can still set it to ``auto_add`` and get the weak policy for the
# host it names. That is the same shape as route 10 (``key_file`` in an
# untrusted file) and is not addressed here; it is recorded so the gap is
# not silent.
# ---------------------------------------------------------------------------


def _a_key_pair_to_deploy(tmp_path):
    """A real private/public pair, the shape ``setup_ssh_keys`` produces.

    ``ssh-copy-id -i x.pub`` refuses to start unless the matching private
    half is readable, so a ``.pub`` on its own would exercise nothing.
    Synthetic and thrown away with ``tmp_path``.
    """
    key = paramiko.RSAKey.generate(2048)
    private = tmp_path / "id_rsa_clustrix_victim"
    key.write_private_key_file(str(private))
    private.chmod(0o600)
    public = tmp_path / "id_rsa_clustrix_victim.pub"
    public.write_text(f"ssh-rsa {key.get_base64()} clustrix\n", encoding="utf-8")
    return key, public


@pytest.mark.parametrize(
    "policy, expected",
    [
        ("reject", "StrictHostKeyChecking=yes"),
        ("auto_add", "StrictHostKeyChecking=accept-new"),
    ],
)
def test_ssh_copy_id_is_invoked_with_the_configured_host_key_policy(
    policy, expected, tmp_path, monkeypatch
):
    """The real argv, read from the process that was really executed.

    A ``ssh-copy-id`` earlier on ``PATH`` records the arguments it is handed
    and exits non-zero. Nothing in clustrix is patched: it resolves the name
    the way it always does and execs what it finds, so this is the argv
    OpenSSH would have received -- not a restatement of the source.
    """
    from clustrix.ssh_utils import deploy_public_key

    if shutil.which("ssh-copy-id") is None:
        pytest.skip("ssh-copy-id is not installed; there is no argv to observe")

    recording = tmp_path / "argv"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "ssh-copy-id"
    shim.write_text(
        "#!/bin/sh\n" f'printf "%s\\n" "$@" > {recording}\n' "exit 1\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")

    _, public = _a_key_pair_to_deploy(tmp_path)
    config = ClusterConfig(
        cluster_type="ssh",
        cluster_host="127.0.0.1",
        cluster_port=1,
        username="victim",
        ssh_host_key_policy=policy,
    )

    with contextlib.suppress(Exception):
        deploy_public_key("127.0.0.1", "victim", str(public), 1, None, config=config)

    argv = recording.read_text(encoding="utf-8").splitlines()
    assert expected in argv, argv
    # The known_hosts the Python half reads, not the one OpenSSH would pick
    # out of the passwd database.
    assert f"UserKnownHostsFile={user_known_hosts_path()}" in argv, argv


def test_key_deployment_does_not_silently_mark_a_new_host_as_verified(
    tmp_path, monkeypatch
):
    """Defect: ``add_host_key`` ran unconditionally, ``reject`` or not.

    RED before the fix: ``known_hosts`` went from 0 bytes to the server's
    three host keys, so the host counted as verified for every later
    connection -- including the paramiko paths route 13 had just gated.
    """
    from clustrix.ssh_utils import deploy_public_key

    _, public = _a_key_pair_to_deploy(tmp_path)
    known_hosts = user_known_hosts_path()
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    known_hosts.write_text("", encoding="utf-8")
    before = known_hosts.read_bytes()

    root = tmp_path / "server-root"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=None) as server:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="victim",
        )
        assert config.ssh_host_key_policy == "reject"
        with contextlib.suppress(Exception):
            deploy_public_key(
                server.host, "victim", str(public), server.port, None, config=config
            )

    assert known_hosts.read_bytes() == before, (
        "deploying a key auto-accepted the host's key under the default "
        "reject policy, which marks the host verified for every future "
        "connection: " + known_hosts.read_text(encoding="utf-8")
    )


def test_auto_add_still_records_the_host_key_it_was_told_to_trust(
    tmp_path, monkeypatch
):
    """The opt-out keeps working; it is only no longer taken for the user."""
    from clustrix.ssh_utils import deploy_public_key

    _, public = _a_key_pair_to_deploy(tmp_path)
    known_hosts = user_known_hosts_path()
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    known_hosts.write_text("", encoding="utf-8")

    root = tmp_path / "server-root"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=None) as server:
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="victim",
            ssh_host_key_policy="auto_add",
        )
        with contextlib.suppress(Exception):
            deploy_public_key(
                server.host, "victim", str(public), server.port, None, config=config
            )
        recorded = known_hosts.read_text(encoding="utf-8")

    assert recorded.strip(), "auto_add recorded no host key at all"
    assert any(
        key.get_base64() in recorded for key in server.host_keys()
    ), "auto_add recorded something that is not this server's host key"


def test_the_openssh_spelling_of_every_policy_is_defined():
    """A new policy value cannot be added without deciding its ssh spelling."""
    assert set(OPENSSH_STRICT_HOST_KEY_CHECKING) == set(VALID_HOST_KEY_POLICIES)
    assert openssh_strict_host_key_checking(ClusterConfig()) == "yes"
    assert (
        openssh_strict_host_key_checking(ClusterConfig(ssh_host_key_policy="auto_add"))
        == "accept-new"
    )
    assert openssh_strict_host_key_checking(None) == "yes"
    # The widget's dict shape, and the same validation as the paramiko side.
    #
    # This line asserted ``== "auto_add"`` and that assertion was wrong, so
    # it is rewritten deliberately rather than relaxed. A weakening of host
    # key verification may only come from a source the user chose, and a
    # mapping carries no provenance record at all -- it is a bag of values
    # that no loader stamped, so ``config_source_is_trusted`` has nothing to
    # read and the fail-closed answer is the only available one. ``reject``
    # from a mapping still means ``reject``; validation is unchanged, which
    # is what the ``pytest.raises`` below is about.
    assert host_key_policy_name({"ssh_host_key_policy": "auto_add"}) == "reject"
    assert host_key_policy_name({"ssh_host_key_policy": "reject"}) == "reject"
    with pytest.raises(ValueError, match="Invalid ssh_host_key_policy"):
        openssh_strict_host_key_checking({"ssh_host_key_policy": "accept-new"})
