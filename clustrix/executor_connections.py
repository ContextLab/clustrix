"""SSH connection management for cluster execution.

This module handles establishing and managing SSH connections to the cluster
types clustrix supports: ``ssh`` and ``slurm``. (``local`` needs no
connection and ``huggingface`` talks to an HTTP API.)
"""

import os
import logging
import threading
from typing import Optional

import paramiko

from clustrix.credential_release import CredentialTarget, release_credential
from clustrix.ssh_security import configure_host_key_policy

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages SSH connections for cluster execution."""

    def __init__(self, config):
        """Initialize connection manager.

        Args:
            config: ClusterConfig instance with connection settings
        """
        self.config = config
        self.ssh_client: Optional[paramiko.SSHClient] = None
        # Opened on first read of the `sftp_client` property, not at connect
        # time -- see that property for why.
        self._sftp_client: Optional[paramiko.SFTPClient] = None
        # Guards the cached channel and the client it is opened against, so
        # that "is it cached yet?" and "open one" cannot be interleaved by a
        # second thread across the network round trip in between.
        self._sftp_lock = threading.Lock()
        self._remote_home = None  # cache for resolve_remote_path()

    @property
    def sftp_client(self) -> Optional[paramiko.SFTPClient]:
        """A long-lived SFTP channel, opened on first use.

        ``setup_ssh_connection`` used to call ``open_sftp()`` eagerly and hold
        the result for the life of the connection, while every operation in
        this class opened its own channel -- so the eager one cost a channel
        on every connection and was never read by shipped code. It is still
        part of the public surface (``ClusterExecutor.sftp_client`` exposes
        it, and tests use it to inspect the far end), so it is kept, but
        deferred: a connection that nobody asks for SFTP on now opens no
        channel at all.

        The per-call sites deliberately do *not* reuse this. ``SFTPClient``
        multiplexes requests over one channel keyed by request id and is not
        thread-safe; a channel per call is what makes two concurrent uploads
        on one connection safe, and sharing this one would trade a real
        correctness property for one saved channel.

        Returns ``None`` when there is no SSH connection, rather than opening
        one: reading an attribute must not dial out.

        The check and the assignment are held under ``_sftp_lock``. Without it
        this is a check-then-set around a network round trip: four threads
        reaching an unopened property together each saw ``None``, each called
        ``open_sftp()``, and three of the four channels were then dropped on
        the floor still open -- a leak of exactly the kind the rest of this
        class exists to prevent. The lock only serialises *opening* the
        convenience channel; it does not make ``SFTPClient`` shareable, which
        is why the per-call sites above still open their own.
        """
        with self._sftp_lock:
            if self._sftp_client is None and self.ssh_client is not None:
                self._sftp_client = self.ssh_client.open_sftp()
            return self._sftp_client

    @sftp_client.setter
    def sftp_client(self, value: Optional[paramiko.SFTPClient]) -> None:
        with self._sftp_lock:
            self._sftp_client = value

    def __enter__(self) -> "ConnectionManager":
        """Connect, and guarantee the transport is closed on the way out.

        ``disconnect()`` previously ran only from ``ClusterExecutor.__del__``,
        which the interpreter may call late or not at all.
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def setup_ssh_connection(self):
        """Setup SSH connection to cluster.

        Anything cached against the *previous* transport is released first.
        This method is what the "SSH client not connected" errors tell a caller
        to run, so it has to be usable as a reconnect -- and while it used to
        reassign ``sftp_client`` unconditionally, making the cache lazy turned
        that into a stale-cache bug: the second call left the property bound to
        a channel on the old, dead transport, so a manager that reported itself
        connected handed out a channel that answered "Socket is closed".
        ``disconnect()`` clears the channel, the cached remote home and the old
        transport, and closes each of them rather than dropping it.
        """
        if not self.config.cluster_host:
            raise ValueError("cluster_host must be specified for SSH-based clusters")

        self.disconnect()

        self.ssh_client = paramiko.SSHClient()
        configure_host_key_policy(self.ssh_client, self.config)

        # Connect using provided credentials
        connect_kwargs = {
            "hostname": self.config.cluster_host,
            "port": self.config.cluster_port,
        }

        # Always include username if available
        if self.config.username:
            connect_kwargs["username"] = self.config.username
        else:
            connect_kwargs["username"] = os.getenv("USER")

        # Ask the one gate -- for every credential, including this config's
        # own ``key_file`` and ``password``.
        #
        # A stored credential belongs to one host, and applying it to
        # whatever ``config.cluster_host`` says was an exfiltration path
        # rather than a convenience: the search of the standard
        # configuration locations includes ``./clustrix.yml``, so a cloned
        # repository can name the host that receives the user's cluster
        # password. ``key_file`` is an ordinary field of that same file, so
        # testing it *before* the gate -- which is what this did -- offered
        # the victim's private key to the same chosen host with no decision
        # taken at all. Naming ``config-field`` first keeps the precedence
        # this path has always had, and puts the branch behind the rule.
        try:
            target = CredentialTarget.for_config(self.config)
        except ValueError as exc:
            logger.warning("No credential can be released: %s", exc)
        else:
            release = release_credential(
                target,
                provider="ssh",
                config=self.config,
                sources=("config-field", "stored-credential", "environment"),
            )
            if release.refusal is not None:
                logger.warning(
                    "Not using a stored SSH credential for %s: %s.",
                    self.config.cluster_host,
                    release.refusal,
                )
            elif release.password:
                connect_kwargs["password"] = release.password
                logger.info("Using SSH password from %s", release.method)
            elif release.key_path:
                # ``private_key_path`` is the name
                # ``resolve_provider_credentials`` actually emits (it is
                # the field name for ``SSH_PRIVATE_KEY_PATH``). This
                # tested for ``key_file``, which nothing has ever
                # produced, so a user whose .env named a key rather than
                # a password silently fell through to the agent and the
                # default key files.
                connect_kwargs["key_filename"] = release.key_path
                logger.info("Using SSH key from %s", release.method)

        self.ssh_client.connect(**connect_kwargs)

    def execute_remote_command(self, command: str, check: bool = False) -> tuple:
        """Execute command on remote cluster.

        ``check`` raises when the command exits non-zero. It is off by default
        because most callers here inspect the output themselves and tolerate
        failure, but anything whose failure would be *unsafe* rather than
        merely unhelpful must opt in -- a `chmod 700` that quietly does nothing
        leaves a secret readable.
        """
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        out = stdout.read().decode()
        err = stderr.read().decode()
        if check:
            status = stdout.channel.recv_exit_status()
            if status != 0:
                raise RuntimeError(
                    f"Remote command failed (exit {status}): {command}\n{err.strip()}"
                )
        return out, err

    def resolve_remote_path(self, path: str) -> str:
        """Expand a leading ``~/`` against the remote account's home directory.

        Shell commands expand ``~`` themselves, but SFTP does not: it treats
        ``~/.clustrix/jobs`` as a *relative* directory literally named ``~``.
        A work directory that is created correctly by ``mkdir -p`` and then
        uploaded into the wrong place is a confusing failure, so every path
        derived from ``remote_work_dir`` goes through here first.

        Only ``~`` and ``~/...`` are expanded. ``~otheruser/...`` refers to a
        different account's home and cannot be derived from ``$HOME``;
        rewriting it by string concatenation would silently produce
        ``/home/alicebob/...``, so it is left alone for the shell to resolve.

        The remote home directory is resolved once and cached per connection.
        """
        if path != "~" and not path.startswith("~/"):
            return path

        if self._remote_home is None:
            stdout, _ = self.execute_remote_command("echo $HOME")
            # Take the LAST line: an interactive-style profile can print a
            # login banner ahead of the value, and prepending "*** Welcome ***"
            # to a path produces a directory nobody can explain.
            candidates = [
                ln.strip() for ln in (stdout or "").splitlines() if ln.strip()
            ]
            home = candidates[-1] if candidates else ""
            if not home.startswith("/"):
                raise RuntimeError(
                    "Could not determine the remote home directory (got "
                    f"{home!r}), so remote_work_dir={path!r} cannot be "
                    "resolved. Set remote_work_dir to an absolute path."
                )
            self._remote_home = home.rstrip("/")

        if path == "~":
            return self._remote_home
        return self._remote_home + path[1:]

    def upload_file(self, local_path: str, remote_path: str):
        """Upload file to remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def download_file(self, remote_path: str, local_path: str):
        """Download file from remote cluster."""
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def create_remote_file(
        self, remote_path: str, content: str, mode: Optional[int] = None
    ):
        """Create file with content on remote cluster.

        ``mode`` sets the file's permissions before anything is written, so a
        secret never exists on disk world-readable even briefly.
        """
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        sftp = self.ssh_client.open_sftp()
        try:
            with sftp.open(remote_path, "w") as f:
                if mode is not None:
                    sftp.chmod(remote_path, mode)
                f.write(content)
        finally:
            sftp.close()

    def remote_file_exists(self, remote_path: str) -> bool:
        """Check if file exists on remote cluster.

        ``False`` means one thing only: the server answered, and said there is
        no such file. Everything else raises.

        This method is the cautionary example for the whole class. Its old
        body answered ``False`` for *any* exception, so "the transport is
        dead", "you may not read that directory" and "I could not open a
        channel" were all reported as "the file is not there" -- and the
        polling loops in ``executor_scheduler_status`` read that as "the job
        has not finished yet", so a broken connection presented as a job that
        ran forever. Swallowing also hid the channel leak below.
        """
        # Raise rather than answer ``False``: with no connection there is no
        # evidence about the file at all, and the callers here poll in a loop
        # on the answer.
        if self.ssh_client is None:
            raise RuntimeError(
                "SSH client not connected. Call setup_ssh_connection() first."
            )
        # Opened outside the try: a channel that failed to open is not one to
        # close, and failing to open one says nothing about the file, so it
        # propagates.
        sftp = self.ssh_client.open_sftp()
        # The close has to be in a finally, and this method is the reason:
        # a missing file is its *expected* answer, not an error, and
        # sftp.stat raises for it. With the close inside the try, every
        # "no, that file is not there" leaked an SFTP channel for the life
        # of the connection, and the exception that caused it was swallowed
        # -- so a submitter polling for a result file ran out of channels
        # with nothing in the log to say why.
        try:
            sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            # The one exception that *is* an answer. paramiko maps the
            # server's SFTP_NO_SUCH_FILE onto errno ENOENT, which Python
            # raises as FileNotFoundError; PermissionError and friends are
            # deliberately not caught here.
            return False
        finally:
            sftp.close()

    def connect(self):
        """Establish connection to cluster (for manual connection)."""
        if self.config.cluster_type in ["slurm", "ssh"]:
            if not self.ssh_client:
                self.setup_ssh_connection()

    def disconnect(self):
        """Release every OS resource this manager holds.

        The attributes are cleared *before* anything is closed, and the
        transport close sits in a ``finally``: a previous version closed the
        SFTP channel first and left ``ssh_client`` set, so an SFTP channel
        that refused to close leaked the whole transport, and a retry
        re-closed a half-closed object.
        """
        # A later connect() may use a different username, and a home directory
        # cached from the previous account would be silently wrong.
        self._remote_home = None
        # Under the lock, and clearing the client with it: a thread part-way
        # through the lazy property must not open a channel against a
        # transport this call is about to close, and then cache it where
        # nothing will ever close it.
        with self._sftp_lock:
            sftp, self._sftp_client = self._sftp_client, None
            ssh, self.ssh_client = self.ssh_client, None
        try:
            if sftp is not None:
                sftp.close()
        except Exception:
            # Log and continue: closing the transport below reclaims this
            # channel's descriptor anyway, so the caller's answer -- "this
            # connection is now closed" -- stays true. Raising here would
            # skip the transport close and make the leak worse.
            logger.warning("Closing the SFTP channel failed", exc_info=True)
        finally:
            if ssh is not None:
                ssh.close()
