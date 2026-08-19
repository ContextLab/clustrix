#!/usr/bin/env python3
"""``tests/ssh_server.py`` must behave the way sshd does, where it claims to.

The in-process server underpins most of the de-mocked SSH suite, so what it
gets *wrong* silently becomes what those tests prove. A review found six
places where it was more forgiving than the thing it stands in for; five are
fixed and pinned here, and the sixth (no pty, no shell channel, and absolute
paths outside ``root`` disagreeing between exec and SFTP) is documented in
that module's docstring instead.

The point of this file is that those fixes cannot rot back. Each test below
fails against the previous implementation:

* the environment leak -- ``dict(os.environ, HOME=root)`` handed every remote
  command the pytest process's whole environment, which made every test of
  clustrix's two-venv environment control vacuous
* no stdin -- ``subprocess.run`` inherited pytest's stdin, so ``cat`` hung
* full buffering -- output was collected and sent at exit, so nothing streamed
* ``readdir`` answered with ``os.stat``, so one dangling symlink failed an
  entire listing and no entry ever carried link attributes
* ``readlink``/``symlink`` answered "operation unsupported"
* ``run_command`` threads were untracked and children were never killed, so
  both outlived ``close()``

Nothing here is mocked: a real socket, a real handshake, real files.
"""

import os
import stat as stat_module
import threading
import time

import paramiko
import pytest

from tests.ssh_server import LocalSSHServer

CANARY = "CLUSTRIX_SSH_SERVER_LEAK_CANARY"


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with LocalSSHServer(root=str(root), password="hunter2") as running:
        yield running


@pytest.fixture
def connection(server):
    """A connected client. The host key is unknown by construction.

    The server mints a fresh key per run, so it can never be in a
    known_hosts file; ``tests/unit/test_no_autoadd_policy.py`` is where the
    default reject policy is proved against this same server.
    """
    client = paramiko.SSHClient()
    client.load_host_keys(os.devnull)
    for host_key in server.host_keys():
        client.get_host_keys().add(
            f"[{server.host}]:{server.port}", host_key.get_name(), host_key
        )
    client.connect(
        server.host,
        port=server.port,
        username="tester",
        password="hunter2",
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        yield client
    finally:
        client.close()


def _run(client, command):
    _, stdout, stderr = client.exec_command(command)
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out, err, stdout.channel.recv_exit_status()


# -- the environment -------------------------------------------------------


def test_parent_environment_does_not_leak_into_a_remote_command(
    monkeypatch, connection
):
    """A remote command must not see variables set in the pytest process.

    This is the divergence that mattered most. clustrix's two-venv execution
    path exists precisely to control what the remote environment contains, so
    while every variable leaked through, no test against this server said
    anything about that path.
    """
    monkeypatch.setenv(CANARY, "i-leaked-from-pytest")
    assert os.environ[CANARY] == "i-leaked-from-pytest"

    out, _, status = _run(connection, f'echo "[${CANARY}]"')
    assert status == 0
    assert out.strip() == "[]", (
        f"{CANARY} was set only in the pytest process and must not be "
        f"visible to a remote command; the command saw {out.strip()!r}"
    )

    # PYTEST_CURRENT_TEST is always present in the parent while a test runs,
    # so it is a canary that cannot be forgotten.
    names, _, _ = _run(connection, "env | cut -d= -f1 | sort")
    exported = set(names.split())
    assert "PYTEST_CURRENT_TEST" not in exported
    assert CANARY not in exported


def test_command_environment_is_the_sshd_shaped_one(server, connection):
    """What a command *does* get is the account, not the caller."""
    out, _, _ = _run(connection, "echo $HOME:$PWD:$USER:$LOGNAME")
    home, pwd, user, logname = out.strip().split(":")
    assert home == server.root
    assert pwd == server.root
    assert user == logname == "tester"

    # ``~`` must expand to the served root, or a test that deploys a key
    # writes into whoever is running the suite.
    tilde, _, _ = _run(connection, "cd ~ && pwd")
    assert os.path.realpath(tilde.strip()) == os.path.realpath(server.root)

    # PATH is the one inherited value, and it is documented as such.
    path, _, _ = _run(connection, "echo $PATH")
    assert path.strip() == os.environ["PATH"]


def test_env_argument_adds_variables_the_way_authorized_keys_would(tmp_path):
    """The escape hatch for a test that really does need a variable set."""
    root = tmp_path / "root"
    root.mkdir()
    with LocalSSHServer(
        root=str(root), password="hunter2", env={"CLUSTRIX_TEST_FLAVOUR": "vanilla"}
    ) as server:
        client = paramiko.SSHClient()
        for host_key in server.host_keys():
            client.get_host_keys().add(
                f"[{server.host}]:{server.port}", host_key.get_name(), host_key
            )
        client.connect(
            server.host,
            port=server.port,
            username="tester",
            password="hunter2",
            look_for_keys=False,
            allow_agent=False,
        )
        try:
            out, _, _ = _run(client, "echo $CLUSTRIX_TEST_FLAVOUR")
            assert out.strip() == "vanilla"
        finally:
            client.close()


# -- stdin and streaming ---------------------------------------------------


def test_stdin_reaches_the_command_and_eof_ends_it(connection):
    """``cat`` must read the channel and terminate on ``shutdown_write``.

    Before, the command inherited pytest's stdin, so this hung until the test
    timed out -- which is why nothing in the suite ever piped anything to a
    remote command.
    """
    stdin, stdout, _ = connection.exec_command("cat")
    stdin.write("piped-through-the-channel\n")
    stdin.flush()
    stdin.channel.shutdown_write()

    stdout.channel.settimeout(15)
    assert stdout.read().decode() == "piped-through-the-channel\n"
    assert stdout.channel.recv_exit_status() == 0


def test_stdin_feeds_a_command_that_consumes_it_incrementally(connection):
    """More than one write, and the command's own view of EOF."""
    stdin, stdout, _ = connection.exec_command("wc -l")
    for i in range(5):
        stdin.write(f"line {i}\n")
    stdin.flush()
    stdin.channel.shutdown_write()

    stdout.channel.settimeout(15)
    assert stdout.read().decode().strip() == "5"


def test_output_streams_instead_of_arriving_all_at_once(connection):
    """The first line must arrive long before the command exits."""
    _, stdout, _ = connection.exec_command("echo first; sleep 3; echo second")
    stdout.channel.settimeout(15)

    started = time.monotonic()
    first = stdout.channel.recv(1024)
    first_at = time.monotonic() - started

    assert first.startswith(b"first")
    assert first_at < 1.5, (
        f"the first line took {first_at:.2f}s to arrive, so output is being "
        "buffered until the command exits rather than streamed"
    )
    assert stdout.read().decode().endswith("second\n")


def test_stderr_is_delivered_separately_from_stdout(connection):
    out, err, status = _run(connection, "echo to-out; echo to-err >&2; exit 3")
    assert out.strip() == "to-out"
    assert err.strip() == "to-err"
    assert status == 3


# -- SFTP link handling ----------------------------------------------------


def test_readdir_uses_lstat_so_a_broken_symlink_does_not_fail_the_listing(
    server, connection
):
    """OpenSSH answers readdir with lstat, and one dead link is not fatal.

    With ``os.stat`` a single dangling symlink raised ``FileNotFoundError``
    and took out the whole directory listing -- behaviour no real cluster
    reproduces.
    """
    os.symlink("/definitely/not/here", os.path.join(server.root, "dangling"))
    with open(os.path.join(server.root, "real.txt"), "w") as handle:
        handle.write("x")

    sftp = connection.open_sftp()
    try:
        assert sorted(sftp.listdir(".")) == ["dangling", "real.txt"]

        by_name = {entry.filename: entry for entry in sftp.listdir_attr(".")}
        assert stat_module.S_ISLNK(by_name["dangling"].st_mode), (
            "readdir must report link attributes; if it follows the link "
            "instead, every caller branch that keys off S_ISLNK is dead code "
            "as far as this server is concerned"
        )
        assert not stat_module.S_ISLNK(by_name["real.txt"].st_mode)
    finally:
        sftp.close()


def test_stat_follows_a_symlink_and_lstat_does_not(server, connection):
    """``stat`` reports the target; ``lstat`` reports the link itself.

    The distinguishing property is the file *type*, not the size: a link's
    ``st_size`` is the length of the target's name, which can coincide with
    the target's own length (it did on the first draft of this test, where
    "target.txt" and the ten bytes of content were both 10). Size is checked
    too, but with a payload long enough that the two cannot collide.
    """
    payload = "x" * 64
    with open(os.path.join(server.root, "target.txt"), "w") as handle:
        handle.write(payload)
    os.symlink("target.txt", os.path.join(server.root, "link.txt"))

    sftp = connection.open_sftp()
    try:
        followed = sftp.stat("link.txt")
        assert stat_module.S_ISREG(followed.st_mode)
        assert not stat_module.S_ISLNK(followed.st_mode)
        assert followed.st_size == len(payload)

        itself = sftp.lstat("link.txt")
        assert stat_module.S_ISLNK(itself.st_mode)
        assert itself.st_size == len("target.txt")

        assert sftp.readlink("link.txt") == "target.txt"
    finally:
        sftp.close()


def test_symlink_over_sftp_really_creates_one(server, connection):
    with open(os.path.join(server.root, "target.txt"), "w") as handle:
        handle.write("x")

    sftp = connection.open_sftp()
    try:
        sftp.symlink("target.txt", "made-over-sftp")
    finally:
        sftp.close()

    made = os.path.join(server.root, "made-over-sftp")
    assert os.path.islink(made)
    assert os.readlink(made) == "target.txt"


# -- shutdown --------------------------------------------------------------


def test_close_leaves_no_thread_or_child_process_running(tmp_path):
    """Every thread joined, every child killed -- including its whole tree.

    ``run_command`` threads used to be untracked, so one survived ``close()``;
    and a long-running remote command was left orphaned on the machine after
    the test that started it had finished.
    """
    root = tmp_path / "root"
    root.mkdir()
    threads_before = set(threading.enumerate())
    server = LocalSSHServer(root=str(root), password="hunter2")
    with server:
        client = paramiko.SSHClient()
        for host_key in server.host_keys():
            client.get_host_keys().add(
                f"[{server.host}]:{server.port}", host_key.get_name(), host_key
            )
        client.connect(
            server.host,
            port=server.port,
            username="tester",
            password="hunter2",
            look_for_keys=False,
            allow_agent=False,
        )
        # Long enough that it cannot have finished on its own.
        client.exec_command("sleep 600")
        deadline = time.monotonic() + 10
        while not server._processes and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server._processes, "the command never started"
        client.close()

    # Every *live* thread, not merely every tracked one. Asserting that no
    # tracked thread survives is vacuous -- the original defect was precisely
    # that ``run_command`` threads were never added to ``_workers``, so a
    # check scoped to that list cannot see the thread it is looking for.
    leaked = sorted(
        thread.name
        for thread in threading.enumerate()
        if thread not in threads_before and thread.is_alive()
    )
    assert not leaked, f"threads outlived close(): {leaked}"
    assert server._workers, "no worker threads were tracked at all"

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(proc.poll() is not None for proc in server._processes):
            break
        time.sleep(0.05)
    alive = [proc.pid for proc in server._processes if proc.poll() is None]
    assert not alive, f"child processes outlived close(): {alive}"
