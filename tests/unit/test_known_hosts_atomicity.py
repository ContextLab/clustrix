"""``auto_add`` must never rewrite the user's whole ``~/.ssh/known_hosts``.

Regression tests for issue #157.

``ssh_host_key_policy="auto_add"`` used to install paramiko's own
accept-everything policy, whose ``missing_host_key`` calls
``client.save_host_keys(filename)``. That method reloads the file, then
opens it ``"w"`` -- truncate -- and re-emits *every* entry from its
in-memory model. Three consequences, all of them measured rather than
theorised:

1. **It loses content that paramiko cannot round-trip.** Comments, blank
   lines, a single line naming several hosts, and any key type paramiko's
   parser does not implement (``sk-ssh-ed25519@openssh.com``, which OpenSSH
   itself handles fine) are silently dropped on the way back out. Nothing
   fails at the time; the user finds out the next time they ssh somewhere.
2. **Concurrent writers interleave.** Two clustrix processes -- or two
   threads -- adding a host at once truncate and re-emit the same file, and
   the result is a file with entries interleaved or cut mid-base64.
3. **An interrupted rewrite truncates.** The window between truncate and
   the last line is proportional to the size of the file, so a crash or a
   kill during it loses everything after the cut point.

The reported symptom of (2) and (3) is not subtle: once one line is cut
mid-base64, ``paramiko.HostKeys.load`` raises ``InvalidHostKey`` on it, so
*every subsequent* connection fails -- to hosts that had nothing to do with
clustrix.

``paramiko.HostKeys().load()`` is the oracle throughout: it is the real
parser, the same one every later connection has to get through.

No mocks anywhere. Real generated keys, a real SSH server over a real
socket, real files, real concurrency, real SIGKILL.
"""

import contextlib
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import paramiko
import pytest
from paramiko.hostkeys import HostKeyEntry

from clustrix.config import ClusterConfig
from clustrix.ssh_security import configure_host_key_policy, user_known_hosts_path
from tests.ssh_server import LocalSSHServer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _preexisting_known_hosts_text(bulk=0):
    """A known_hosts file with the shapes a real one actually contains.

    Every line here is valid OpenSSH input. Four of them are things
    paramiko's writer cannot reproduce, which is the point: the fix is not
    "re-emit the file more carefully", it is "never re-emit the file".

    ``bulk`` pads the file with additional real entries. Size matters for
    the concurrency test: the old code's rewrite window is proportional to
    the length of the file, so a realistically sized known_hosts is what
    makes two writers actually collide rather than merely being able to.
    """
    lines = [
        "# hosts I verified by hand -- do not touch\n",
        "\n",
    ]
    for name in ("alpha.example.com", "beta.example.com"):
        key = paramiko.ECDSAKey.generate()
        lines.append(f"{name} {key.get_name()} {key.get_base64()}\n")
    shared = paramiko.ECDSAKey.generate()
    # One line, two hostnames: paramiko re-emits this as two lines.
    lines.append(
        f"gamma.example.com,10.0.0.7 {shared.get_name()} {shared.get_base64()}\n"
    )
    # A key type this paramiko has no parser for. OpenSSH reads it; paramiko
    # drops it on the floor when it re-emits the file.
    lines.append(
        "delta.example.com sk-ssh-ed25519@openssh.com "
        "AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29tAAAAIAABAgMEBQYHCAkKCwwND"
        "g8QERITFBUWFxgZGhscHR4fAAAABHNzaDo=\n"
    )
    padding = [paramiko.ECDSAKey.generate() for _ in range(4)] if bulk else []
    for index in range(bulk):
        key = padding[index % len(padding)]
        lines.append(
            f"real-host-{index}.example.com {key.get_name()} {key.get_base64()}\n"
        )
    return "".join(lines)


def _seed_known_hosts(bulk=0):
    """Write the pre-existing file into this test's isolated ``$HOME``."""
    known_hosts = user_known_hosts_path()
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = _preexisting_known_hosts_text(bulk=bulk)
    known_hosts.write_text(text)
    return known_hosts, text


def _client_with_auto_add():
    client = paramiko.SSHClient()
    configure_host_key_policy(client, ClusterConfig(ssh_host_key_policy="auto_add"))
    return client


def _assert_parses(known_hosts):
    """The file must survive the real parser every later connection uses."""
    try:
        paramiko.HostKeys().load(str(known_hosts))
    except Exception as exc:  # pragma: no cover - only on a real corruption
        pytest.fail(
            f"known_hosts no longer parses ({type(exc).__name__}: {exc}). Every "
            f"subsequent SSH connection, clustrix's and the user's own, now "
            f"fails. File was:\n{known_hosts.read_text()!r}"
        )


def _assert_every_added_line_is_whole(added_text):
    """No line may be cut short: three fields, and real base64 in the third."""
    for lineno, line in enumerate(added_text.splitlines(), start=1):
        if not line.strip():
            continue
        entry = HostKeyEntry.from_line(line, lineno)
        assert entry is not None, (
            f"appended line {lineno} is not a complete known_hosts entry -- a "
            f"write was cut in half: {line!r}"
        )


def test_auto_add_appends_and_leaves_every_pre_existing_byte_alone():
    """End to end, over a real socket, against a real SSH server.

    The assertion is deliberately the strongest one available: the file
    afterwards must *start with* exactly the bytes that were there before.
    Anything that rewrites the file fails this, including a rewrite that
    happens to preserve the same set of keys, because it reorders them and
    drops the comment.
    """
    known_hosts, before = _seed_known_hosts()
    root = Path(str(known_hosts.parent.parent)) / "srv"
    root.mkdir()

    with LocalSSHServer(root=str(root), password="hunter2") as server:
        client = _client_with_auto_add()
        client.connect(
            server.host,
            port=server.port,
            username="tester",
            password="hunter2",
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, _ = client.exec_command("echo trusted-on-first-use")
        assert stdout.read().decode().strip() == "trusted-on-first-use"
        client.close()

        after = known_hosts.read_text()

    assert after.startswith(before), (
        "auto_add rewrote the user's known_hosts instead of appending to it. "
        f"Before:\n{before!r}\nAfter:\n{after!r}"
    )
    added = after[len(before) :]
    assert f"[{server.host}]:{server.port}" in added, (
        "auto_add did not record the key it accepted; the next connection "
        f"would trust it blindly all over again. Appended:\n{added!r}"
    )
    _assert_every_added_line_is_whole(added)
    _assert_parses(known_hosts)

    # And the whole point: the appended entry is usable afterwards.
    learned = paramiko.HostKeys()
    learned.load(str(known_hosts))
    assert learned.lookup(f"[{server.host}]:{server.port}") is not None
    for host in ("alpha.example.com", "beta.example.com", "gamma.example.com"):
        assert learned.lookup(host) is not None, (
            f"{host} was in known_hosts before clustrix ran and is not "
            "recognised any more"
        )


def test_concurrent_connections_never_corrupt_the_file():
    """Eight real SSH connections, opened at once, all learning a new host.

    Eight servers rather than one, because eight clients meeting the *same*
    server produce one new entry: after the first, the host is known and
    the policy is never consulted again. Distinct ephemeral ports are what
    make eight simultaneous writers.

    The pre-existing file is padded to a realistic size on purpose. The old
    code truncated and re-emitted the whole thing per accepted key, so the
    window in which a second writer can land grows with the file; at this
    size a standalone reproduction of the old behaviour corrupted the file
    in 10 runs out of 10, leaving NUL runs and half-written base64 inside
    unrelated entries and making every later connection fail.
    """
    known_hosts, before = _seed_known_hosts(bulk=200)
    home = known_hosts.parent.parent

    workers = 8
    barrier = threading.Barrier(workers)
    failures = []

    with contextlib.ExitStack() as stack:
        servers = []
        for index in range(workers):
            root = home / f"srv{index}"
            root.mkdir()
            servers.append(
                stack.enter_context(LocalSSHServer(root=str(root), password="hunter2"))
            )

        def connect_to(server):
            try:
                barrier.wait(timeout=60)
                client = _client_with_auto_add()
                client.connect(
                    server.host,
                    port=server.port,
                    username="tester",
                    password="hunter2",
                    look_for_keys=False,
                    allow_agent=False,
                )
                client.close()
            except BaseException as exc:  # pragma: no cover - reported below
                failures.append(
                    f"port {server.port}: {type(exc).__name__}: {str(exc)[:300]}"
                )

        threads = [
            threading.Thread(target=connect_to, args=(server,)) for server in servers
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            assert not thread.is_alive(), "a connection never finished"

        ports = [server.port for server in servers]

    assert not failures, "\n".join(failures)

    after = known_hosts.read_text()
    assert after.startswith(before), (
        "concurrent auto_add writers rewrote the pre-existing entries away. "
        f"After:\n{after[:2000]!r}"
    )
    added = after[len(before) :]
    _assert_every_added_line_is_whole(added)
    _assert_parses(known_hosts)

    learned = paramiko.HostKeys()
    learned.load(str(known_hosts))
    missing = [port for port in ports if learned.lookup(f"[127.0.0.1]:{port}") is None]
    assert not missing, (
        f"{len(missing)} of {workers} concurrently accepted host keys were "
        f"lost from known_hosts: ports {missing}"
    )
    for host in (
        "alpha.example.com",
        "real-host-0.example.com",
        "real-host-199." "example.com",
    ):
        assert learned.lookup(host) is not None, (
            f"{host} was in known_hosts before clustrix ran and is not "
            "recognised any more"
        )


def _wait_until_the_writer_has_written(known_hosts, before_size, process):
    """Block until the child has really appended, so the kill lands mid-write.

    This replaces ``process.wait(timeout=random.uniform(1.0, 2.0))``, which
    was a guess about how long a CPython start-up plus a paramiko import plus
    an ECDSA key generation takes -- and the guess is load-dependent. Measured
    on the machine this was written on, the first append lands at ~0.45s on an
    idle box and at up to 3.2s with the machine oversubscribed 32 ways: 152 of
    160 sampled starts exceeded 1.0s under that load. Past 1.0s the child was
    killed before it had written anything and the test failed on its own
    "test is vacuous" guard -- 0 to 6 of the 6 parameters, depending on what
    else happened to be running.

    That is a defect in the test, not in the writer: nothing here was ever an
    assertion about a race in ``ssh_security``. It still matters, because a
    test that fails on a busy machine teaches people to re-run until green,
    which is how a real failure gets ignored.

    Waiting for the observable event is both deterministic and stronger than
    the guess it replaces: the kill is now guaranteed to interrupt a running
    write loop, which is the situation this test exists to cover, instead of
    only doing so when the machine happened to be fast enough.
    """
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        if known_hosts.stat().st_size > before_size:
            return
        if process.poll() is not None:
            raise AssertionError(
                "the writer exited on its own without appending anything; its "
                "stderr was:\n" + process.stderr.read().decode()
            )
        time.sleep(0.005)
    raise AssertionError(
        "the writer appended nothing in 120s -- it is not writing at all, "
        "which is a different failure from the one this test looks for"
    )


#: Adds host keys through the real policy until it is killed. Run as a
#: separate process so the kill is a genuine SIGKILL mid-write, not an
#: exception raised at a point Python chose.
_ADDER = """
import paramiko
from clustrix.config import ClusterConfig
from clustrix.ssh_security import configure_host_key_policy

client = paramiko.SSHClient()
configure_host_key_policy(client, ClusterConfig(ssh_host_key_policy="auto_add"))
key = paramiko.ECDSAKey.generate()
index = 0
while True:
    index += 1
    client._policy.missing_host_key(client, "burst-%d.example.com" % index, key)
"""


@pytest.mark.parametrize("round_number", range(6))
def test_a_killed_writer_never_leaves_a_broken_file(round_number):
    """SIGKILL a real process mid-write and require the file to still parse.

    A truncate-then-rewrite loses everything after the cut. An append of a
    single whole line either landed or did not.
    """
    known_hosts, before = _seed_known_hosts()

    env = dict(os.environ)
    env["HOME"] = str(known_hosts.parent.parent)
    env["USERPROFILE"] = env["HOME"]
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    process = subprocess.Popen(
        [sys.executable, "-c", _ADDER],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_until_the_writer_has_written(known_hosts, len(before), process)
        # Vary where in the write loop the kill lands, so the six rounds are
        # six different interruption points rather than six copies of "just
        # after the first append". The wait above is what makes every one of
        # them an interruption at all.
        time.sleep(random.uniform(0.0, 0.05))
    finally:
        process.kill()
    stderr = process.communicate()[1].decode()

    after = known_hosts.read_text()
    assert after.startswith(before), (
        "a killed writer destroyed the pre-existing entries. "
        f"After:\n{after[:2000]!r}"
    )
    added = after[len(before) :]
    assert added, "the child was killed before it wrote anything -- test is vacuous"
    _assert_every_added_line_is_whole(added)
    _assert_parses(known_hosts)
    assert "Traceback" not in stderr, stderr
