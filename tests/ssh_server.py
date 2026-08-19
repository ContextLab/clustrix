"""A real SSH server, in process, for tests that must not fake SSH.

Nothing in this module is a mock. It is paramiko's *server* side: a real
socket on ``127.0.0.1``, a real host key, a real SSH handshake, real
public-key and password authentication, real ``exec`` channels whose commands
are run by a real shell against real files on disk, and a real SFTP subsystem
backed by a real directory.

It exists because the tests it replaces did not test SSH at all. They patched
``paramiko.SSHClient``, told the resulting ``Mock`` what to return, and then
asserted it returned that. With this server the *shipped* client code runs
unmodified and an assertion about ``ls`` output is an assertion about files
that really exist.

Known divergences from OpenSSH sshd
-----------------------------------

This module used to claim that "clustrix cannot tell the difference between
this and sshd". That was false, and the overclaim was itself the defect: a
reader had no way to know what a passing test here does and does not prove.
The list below is the honest version. Anything not listed has been made to
behave as sshd does; anything listed is a deliberate limitation, with the
reason it was not worth closing.

Closed (this server now matches sshd):

* **Command environment.** A command gets a bare, sshd-shaped environment
  (``HOME``, ``PWD``, ``USER``, ``LOGNAME``, ``SHELL``, ``PATH``,
  ``SSH_CONNECTION``) plus whatever the ``env=`` constructor argument adds --
  *not* the environment of the pytest process. This one mattered most:
  clustrix's two-venv execution path exists precisely to control the remote
  environment, so while the parent environment leaked through, every test of
  that path was testing nothing about it.
* **Standard input.** The channel is wired to the command's stdin, so
  ``cat`` really reads what the client writes and really sees EOF when the
  client shuts the channel down for writing.
* **Output streaming.** stdout and stderr are forwarded as they are produced,
  not collected and sent at exit, so ``echo a; sleep 3; echo b`` delivers
  ``a`` immediately.
* **SFTP ``readdir`` uses ``lstat``.** OpenSSH answers a directory listing
  with link attributes. Using ``os.stat`` made one dangling symlink fail the
  *entire* listing, and hid every code path that keys off ``S_ISLNK`` in a
  readdir result.
* **SFTP ``readlink``/``symlink``** are implemented rather than answering
  "operation unsupported", so ``stat`` and ``lstat`` really disagree about a
  symlink the way they do on a real host.

Open (this server still differs; a test relying on these proves nothing):

* **No pty, no shell channel.** ``pty-req`` and ``shell`` requests are
  refused. clustrix only ever opens ``exec`` channels and the SFTP
  subsystem, so accepting them would mean carrying server code that no test
  exercises -- and accepting a ``pty-req`` without actually allocating a pty
  would be a worse lie than refusing it. A test cannot use this server to
  show anything about interactive sessions, terminal echo, job control, or
  signal delivery on ``SIGWINCH``/``SIGHUP``.
* **Absolute paths outside ``root`` disagree between exec and SFTP.** An
  exec channel runs against the real filesystem, so it sees the real
  ``/etc/hosts``; SFTP maps any path outside ``root`` back inside it, so it
  raises ``FileNotFoundError`` for the same string. Real sshd does not
  chroot its SFTP subsystem and both would see the same file. The
  containment is deliberate -- it is what stops a buggy test writing outside
  its ``tmp_path`` over SFTP -- and it is worth more than the fidelity.
  Tests should use paths under ``root``.
* **No login shell, no rc files, no ``AcceptEnv``.** Commands run under
  ``sh -c`` exactly as sshd runs a non-interactive command, but nothing
  sources ``~/.bashrc`` or ``~/.profile``, and client-sent ``env`` requests
  are refused (as a default-configured sshd refuses everything outside
  ``AcceptEnv``). ``PATH`` is inherited from the test process rather than
  being sshd's compiled-in default, so that the interpreter running the
  tests can be found.
* **Authentication is permissive about the account.** Any username is
  accepted with the configured password or an authorized key; there is no
  real account, no ``/etc/passwd`` lookup, and no uid change. Commands run
  as whoever runs pytest.

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
import selectors
import signal
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Union

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
        # ...unless it already points inside root. Real sshd does not chroot
        # its SFTP subsystem, so on a real host an absolute path names the
        # same file over SFTP as it does in an exec channel. Re-joining such
        # a path onto root would make this server's two transports disagree
        # about the same string -- and code that legitimately uses both (the
        # scheduler's completion check reads result.pkl by absolute path)
        # would see a file through one and not the other. Containment is
        # unaffected: a path outside root is still mapped inside it.
        try:
            if os.path.commonpath([resolved, self.root]) == self.root:
                return resolved
        except ValueError:  # pragma: no cover - different drives on Windows
            pass
        return os.path.join(self.root, resolved.lstrip("/"))

    def list_folder(self, path):
        """``readdir``, answered with ``lstat`` attributes as OpenSSH does.

        Not ``os.stat``: that follows the link, so a single dangling symlink
        raised ``FileNotFoundError`` and failed the *whole* listing, which no
        real server does. It also meant no entry ever carried ``S_ISLNK``
        attributes, so every caller branch that keys off a link in a readdir
        result was unreachable through this server.
        """
        real = self._realpath(path)
        try:
            names = os.listdir(real)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

        entries = []
        for name in names:
            try:
                attr = paramiko.SFTPAttributes.from_stat(
                    os.lstat(os.path.join(real, name))
                )
            except OSError:
                # Vanished between listdir and lstat. sshd omits it rather
                # than failing the listing.
                continue
            attr.filename = name
            entries.append(attr)
        return entries

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

    def readlink(self, path):
        """Really read the link target, rather than "operation unsupported".

        The target is returned exactly as it is stored on disk. For a
        relative target that is what a real server returns too; for an
        absolute one inside ``root`` it is also the client's view, because
        ``_realpath`` leaves such paths alone.
        """
        try:
            return os.readlink(self._realpath(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def symlink(self, target_path, path):
        """Really create a symlink, so ``stat`` and ``lstat`` can disagree."""
        try:
            os.symlink(target_path, self._realpath(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK


class _SessionServer(paramiko.ServerInterface):
    """Authentication and channel policy for one connection."""

    def __init__(self, owner: "LocalSSHServer"):
        self.owner = owner
        self.username: Optional[str] = None

    # -- authentication ---------------------------------------------------
    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        if self.owner.password is not None and password == self.owner.password:
            self.username = username
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
                self.username = username
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
        self.owner.spawn_command(channel, text, self.username)
        return True


class LocalSSHServer:
    """A real SSH server bound to a loopback port.

    Args:
        root: directory commands run in and SFTP is rooted at.
        password: accepted password, or ``None`` to refuse password auth.
        authorized_keys: paths to public keys accepted for publickey auth.
        env: extra variables to add to the otherwise bare command
            environment, the way ``environment=`` in an ``authorized_keys``
            entry does on a real host. Nothing from the test process's own
            environment is passed through except ``PATH``.
    """

    host = "127.0.0.1"

    def __init__(
        self,
        root: Union[str, Path],
        password: Optional[str] = None,
        authorized_keys: Optional[List[Union[str, Path]]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        self.root = str(root)
        self.password = password
        self.env = dict(env or {})
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
        self._processes: List[subprocess.Popen] = []
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

        Every thread this server starts is in ``_workers`` and every child
        process is in ``_processes``, and both are drained here. Before that
        was true, a ``run_command`` thread outlived ``close()`` and a
        long-running remote command (``sleep 321`` in the report that found
        this) was left orphaned on the machine after the test that started
        it had finished.
        """
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        # Terminate children first: it is what unblocks the run_command
        # threads waiting on them, so the joins below can actually finish.
        self._signal_processes(signal.SIGTERM)
        for transport in list(self._transports):
            try:
                transport.close()
            except Exception:
                pass
        self._thread.join(timeout=5)
        for worker in list(self._workers):
            worker.join(timeout=5)
        # Anything that ignored SIGTERM, or that was started while we were
        # tearing down, does not get to survive us.
        self._signal_processes(signal.SIGKILL)

    def _signal_processes(self, sig: int) -> None:
        """Signal every live child's whole process group.

        The group, not the process: commands run through ``sh -c``, and a
        shell that has not ``exec``'d its child would otherwise die while
        leaving that child running.
        """
        with self._lock:
            processes = list(self._processes)
        for proc in processes:
            if proc.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except Exception:
                    pass

    def _track(self, thread: threading.Thread) -> None:
        """Remember a thread so ``close()`` can join it."""
        with self._lock:
            self._workers.append(thread)

    # -- bookkeeping ------------------------------------------------------
    def host_keys(self) -> List[paramiko.PKey]:
        """Every host key this server offers, newest algorithm first.

        A test that wants to verify the server rather than blanket-trust it
        needs these to build a known_hosts file, which is what
        ``ssh-keyscan`` would hand a real user.
        """
        return list(_host_keys())

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

    def command_env(self, username: Optional[str] = None) -> Dict[str, str]:
        """The environment a command really gets: sshd-shaped, not inherited.

        Previously this was ``dict(os.environ, HOME=root)``, so every
        variable set in the pytest process was visible to a "remote"
        command. Real sshd hands a non-interactive command a bare
        environment built from the account, and clustrix's two-venv
        execution path exists precisely to control the remote environment --
        so while the parent environment leaked through, no test against this
        server said anything about that path.

        ``PATH`` is the one inherited value. sshd would use its compiled-in
        default; inheriting means the interpreter and tools the test process
        can find are the ones the "remote" command can find, which is what
        makes the server usable at all. It is listed as a divergence in the
        module docstring rather than hidden here.
        """
        account = username or "tester"
        env = {
            # ``root`` is this account's home as well as its working
            # directory. Without this, ``~`` in a remote command would
            # expand to the home directory of whoever is running the tests,
            # and a test that deploys a key would write into their real
            # ~/.ssh.
            "HOME": self.root,
            "PWD": self.root,
            "USER": account,
            "LOGNAME": account,
            "SHELL": "/bin/sh",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "SSH_CONNECTION": f"{self.host} 0 {self.host} {self.port}",
        }
        env.update(self.env)
        return env

    def spawn_command(
        self, channel, command: str, username: Optional[str] = None
    ) -> threading.Thread:
        """Start ``run_command`` on a tracked thread and return it."""
        worker = threading.Thread(
            target=self.run_command,
            args=(channel, command, username),
            daemon=True,
        )
        self._track(worker)
        worker.start()
        return worker

    def run_command(
        self, channel, command: str, username: Optional[str] = None
    ) -> None:
        """Really run ``command``, in a real shell, in ``root``.

        stdin, stdout and stderr are wired to the channel while the command
        runs, rather than the command being run to completion and its output
        sent afterwards. That is not a refinement: with the old
        ``subprocess.run`` the command inherited *pytest's* stdin, so ``cat``
        hung until the test timed out, and nothing streamed -- ``echo a;
        sleep 3; echo b`` delivered both lines at t=3.
        """
        proc = None
        try:
            proc = subprocess.Popen(  # nosec B602 - a shell is the point
                command,
                shell=True,
                cwd=self.root,
                env=self.command_env(username),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                # Its own process group, so close() can kill the whole tree
                # rather than only the shell at the top of it.
                start_new_session=True,
            )
            with self._lock:
                self._processes.append(proc)

            stdin_pump = threading.Thread(
                target=self._pump_stdin, args=(channel, proc), daemon=True
            )
            self._track(stdin_pump)
            stdin_pump.start()

            self._pump_output(channel, proc)
            channel.send_exit_status(proc.wait())
        except Exception:  # pragma: no cover - only on a torn-down channel
            try:
                channel.send_exit_status(1)
            except Exception:
                pass
        finally:
            if proc is not None:
                for stream in (proc.stdout, proc.stderr):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
            try:
                channel.close()
            except Exception:
                pass

    def _pump_stdin(self, channel, proc: subprocess.Popen) -> None:
        """Feed what the client writes into the command's real stdin.

        Closing the command's stdin on EOF is the load-bearing part: it is
        what lets ``cat`` terminate when the client calls
        ``shutdown_write()``, exactly as it does against sshd.
        """
        try:
            while not self._stop.is_set():
                data = channel.recv(65536)
                if not data:
                    break
                if proc.stdin is None:
                    break
                proc.stdin.write(data)
                proc.stdin.flush()
        except Exception:
            pass
        finally:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass

    def _pump_output(self, channel, proc: subprocess.Popen) -> None:
        """Forward stdout and stderr to the channel as they are produced."""
        selector = selectors.DefaultSelector()
        try:
            if proc.stdout is not None:
                selector.register(proc.stdout, selectors.EVENT_READ, channel.sendall)
            if proc.stderr is not None:
                selector.register(
                    proc.stderr, selectors.EVENT_READ, channel.sendall_stderr
                )
            while selector.get_map():
                for key, _ in selector.select(timeout=0.1):
                    try:
                        chunk = key.fileobj.read(65536)  # type: ignore[union-attr]
                    except (OSError, ValueError):
                        chunk = b""
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data(chunk)
                if self._stop.is_set() and proc.poll() is not None:
                    break
        finally:
            selector.close()

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
            self._track(worker)
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
