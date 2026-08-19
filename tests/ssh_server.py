"""A real SSH server, in process, for tests that must not fake SSH.

Nothing in this module is a mock. It is paramiko's *server* side: a real
socket on ``127.0.0.1``, a real host key, a real SSH handshake, real
public-key and password authentication, real ``exec`` channels whose commands
are run by a real shell against real files on disk, and a real SFTP subsystem
backed by a real directory.

It exists because the tests it replaces did not test SSH at all. They patched
``paramiko.SSHClient``, told the resulting ``Mock`` what to return, and then
asserted it returned that. With this server the *shipped* client code runs
unmodified -- ``clustrix`` cannot tell the difference between this and sshd,
which is the whole point -- and an assertion about ``ls`` output is an
assertion about files that really exist.

Usage::

    with LocalSSHServer(root=tmp_path, password="hunter2") as server:
        config = ClusterConfig(
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password="hunter2",
            # This server's key is generated per-run, so it can never be in
            # a known_hosts file. Verifying it is covered separately by
            # tests/unit/test_host_key_policy.py.
            ssh_host_key_policy="auto_add",
        )

Commands run with ``root`` as the working directory, so a test can create
files with ``tmp_path`` and then list them over SSH.
"""

import base64
import os
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import List, Optional, Union

import paramiko

# Generating an RSA key takes a noticeable fraction of a second, and every
# test in a run can safely share one host key -- it is the server's identity,
# not a per-connection secret.
_HOST_KEY_LOCK = threading.Lock()
_HOST_KEYS: List[paramiko.PKey] = []


def _host_keys() -> List[paramiko.PKey]:
    """Both an ECDSA and an RSA host key.

    OpenSSH's own tools (``ssh-keyscan``, which clustrix runs before deploying
    a key) will not negotiate with a server that offers only ``ssh-rsa``, so
    offering a modern key as well is what makes this server usable by real
    clients rather than only by paramiko.
    """
    global _HOST_KEYS
    with _HOST_KEY_LOCK:
        if not _HOST_KEYS:
            scratch = tempfile.mkdtemp(prefix="clustrix-test-hostkey-")
            ed25519_path = generate_keypair(scratch, "ssh_host_ed25519_key")
            _HOST_KEYS = [
                paramiko.Ed25519Key.from_private_key_file(str(ed25519_path)),
                paramiko.ECDSAKey.generate(),
                paramiko.RSAKey.generate(2048),
            ]
        return _HOST_KEYS


class _SFTPHandle(paramiko.SFTPHandle):
    """A real open file, on the real filesystem."""

    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def chattr(self, attr):
        try:
            paramiko.SFTPServer.set_file_attr(self.filename, attr)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


class _RootedSFTPServer(paramiko.SFTPServerInterface):
    """SFTP over a real directory tree.

    Paths are resolved beneath ``root`` so a test cannot accidentally have the
    server write outside its temporary directory.
    """

    def __init__(self, server, *largs, root: str = "/", **kwargs):
        super().__init__(server, *largs, **kwargs)
        self.root = os.path.realpath(root)

    def _realpath(self, path: str) -> str:
        # canonicalize() gives us an absolute, normalized path in the client's
        # view of the world; joining it onto root maps that view onto disk.
        resolved = os.path.normpath(self.canonicalize(path))
        return os.path.join(self.root, resolved.lstrip("/"))

    def list_folder(self, path):
        real = self._realpath(path)
        try:
            entries = []
            for name in os.listdir(real):
                attr = paramiko.SFTPAttributes.from_stat(
                    os.stat(os.path.join(real, name))
                )
                attr.filename = name
                entries.append(attr)
            return entries
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._realpath(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def lstat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(self._realpath(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def open(self, path, flags, attr):
        real = self._realpath(path)
        try:
            binary_flags = getattr(os, "O_BINARY", 0)
            fd = os.open(real, flags | binary_flags, 0o666)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"

        try:
            handle_file = os.fdopen(fd, mode)
        except OSError as exc:
            os.close(fd)
            return paramiko.SFTPServer.convert_errno(exc.errno)

        handle = _SFTPHandle(flags)
        handle.filename = real
        handle.readfile = handle_file
        handle.writefile = handle_file
        return handle

    def remove(self, path):
        try:
            os.remove(self._realpath(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rename(self, oldpath, newpath):
        try:
            os.rename(self._realpath(oldpath), self._realpath(newpath))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._realpath(path))
            if attr is not None:
                paramiko.SFTPServer.set_file_attr(self._realpath(path), attr)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rmdir(self, path):
        try:
            os.rmdir(self._realpath(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def chattr(self, path, attr):
        try:
            paramiko.SFTPServer.set_file_attr(self._realpath(path), attr)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK


class _SessionServer(paramiko.ServerInterface):
    """Authentication and channel policy for one connection."""

    def __init__(self, owner: "LocalSSHServer"):
        self.owner = owner

    # -- authentication ---------------------------------------------------
    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        if self.owner.password is not None and password == self.owner.password:
            self.owner.record_auth(username, "password")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        # Real comparison of the real key the client proved possession of.
        # The account's own authorized_keys file is consulted on every
        # attempt, not cached, so a key deployed during a test really takes
        # effect the way it would on a real host.
        for authorized in self.owner.current_authorized_keys():
            if key.asbytes() == authorized.asbytes():
                self.owner.record_auth(username, "publickey")
                return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    # -- channels ---------------------------------------------------------
    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        text = command.decode()
        self.owner.commands.append(text)
        threading.Thread(
            target=self.owner.run_command, args=(channel, text), daemon=True
        ).start()
        return True


class LocalSSHServer:
    """A real SSH server bound to a loopback port.

    Args:
        root: directory commands run in and SFTP is rooted at.
        password: accepted password, or ``None`` to refuse password auth.
        authorized_keys: paths to public keys accepted for publickey auth.
    """

    host = "127.0.0.1"

    def __init__(
        self,
        root: Union[str, Path],
        password: Optional[str] = None,
        authorized_keys: Optional[List[Union[str, Path]]] = None,
    ):
        self.root = str(root)
        self.password = password
        self.authorized_keys = [
            _load_public_key(path) for path in (authorized_keys or [])
        ]
        self.commands: List[str] = []
        self.authentications: List[tuple] = []
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, 0))
        # A timeout on the listening socket, so the accept loop wakes up and
        # notices `_stop` even if no client ever connects. Without it a
        # shutdown would block until something happened to arrive.
        self._sock.settimeout(0.25)
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._transports: List[paramiko.Transport] = []
        self._workers: List[threading.Thread] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> "LocalSSHServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """Shut down deterministically, including mid-connection.

        Called from the fixture's teardown, which runs whether the test passed
        or blew up in the middle of a session, so it must be safe to call on a
        server with live transports and with none.
        """
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        for transport in list(self._transports):
            try:
                transport.close()
            except Exception:
                pass
        self._thread.join(timeout=5)
        for worker in list(self._workers):
            worker.join(timeout=5)

    # -- bookkeeping ------------------------------------------------------
    def record_auth(self, username: str, method: str) -> None:
        with self._lock:
            self.authentications.append((username, method))

    def current_authorized_keys(self) -> List[paramiko.PKey]:
        """The keys accepted right now: constructor list plus the real file.

        ``<root>/.ssh/authorized_keys`` is read fresh each time, because that
        is what sshd does and because it lets a test deploy a key and then
        really log in with it.
        """
        keys = list(self.authorized_keys)
        authorized_file = Path(self.root) / ".ssh" / "authorized_keys"
        if authorized_file.exists():
            for line in authorized_file.read_text().splitlines():
                fields = line.split()
                if len(fields) >= 2:
                    try:
                        keys.append(
                            paramiko.PKey.from_type_string(
                                fields[0], base64.b64decode(fields[1])
                            )
                        )
                    except Exception:
                        continue
        return keys

    def run_command(self, channel, command: str) -> None:
        """Really run ``command``, in a real shell, in ``root``."""
        try:
            # ``root`` is this account's home as well as its working
            # directory. Without this ``~`` in a remote command would expand
            # to the home directory of whoever is running the tests, and a
            # test that deploys a key would write into their real ~/.ssh.
            env = dict(os.environ, HOME=self.root)
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if completed.stdout:
                channel.sendall(completed.stdout)
            if completed.stderr:
                channel.sendall_stderr(completed.stderr)
            channel.send_exit_status(completed.returncode)
        except Exception:  # pragma: no cover - only on a torn-down channel
            try:
                channel.send_exit_status(1)
            except Exception:
                pass
        finally:
            try:
                channel.close()
            except Exception:
                pass

    # -- accept loop ------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                # Nothing connected in this window; re-check `_stop`.
                continue
            except OSError:
                # The listening socket was closed by close(): we are done.
                return
            worker = threading.Thread(target=self._handle, args=(client,), daemon=True)
            self._workers.append(worker)
            worker.start()

    def _handle(self, client: socket.socket) -> None:
        # Blocking with no deadline, but only after the handshake deadlines
        # below have been satisfied; a client that connects and then says
        # nothing is dropped rather than pinning a thread forever.
        client.settimeout(None)
        transport = paramiko.Transport(client)
        transport.banner_timeout = 15
        transport.handshake_timeout = 15
        transport.auth_timeout = 15
        for host_key in _host_keys():
            transport.add_server_key(host_key)
        transport.set_subsystem_handler(
            "sftp", paramiko.SFTPServer, _RootedSFTPServer, root=self.root
        )
        self._transports.append(transport)
        try:
            transport.start_server(server=_SessionServer(self))
        except Exception:
            transport.close()
            return
        # Hold the connection open until the client hangs up or we shut down.
        while transport.is_active() and not self._stop.is_set():
            self._stop.wait(0.05)
        transport.close()


def _load_public_key(path: Union[str, Path]) -> paramiko.PKey:
    """Read an OpenSSH ``.pub`` file into a real paramiko key object."""
    text = Path(path).read_text().split()
    if len(text) < 2:
        raise ValueError(f"{path} is not an OpenSSH public key")
    return paramiko.PKey.from_type_string(text[0], base64.b64decode(text[1]))


def generate_keypair(directory: Union[str, Path], name: str = "id_ed25519") -> Path:
    """Generate a real SSH keypair with ``ssh-keygen`` and return the private key path.

    ``ssh-keygen`` is used rather than a library so the key on disk is exactly
    the artifact a user would have, in exactly the format clustrix has to read.
    """
    private = Path(directory) / name
    completed = subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "clustrix-test",
            "-f",
            str(private),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "ssh-keygen failed; a real key is required and there is no "
            f"substitute:\n{completed.stdout.decode()}"
        )
    return private
