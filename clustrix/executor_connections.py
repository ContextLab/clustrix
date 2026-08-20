"""SSH connection management for cluster execution.

This module handles establishing and managing SSH connections to the cluster
types clustrix supports: ``ssh`` and ``slurm``. (``local`` needs no
connection and ``huggingface`` talks to an HTTP API.)
"""

import os
import logging
from typing import Optional

import paramiko

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
        self.ssh_client = None
        self.sftp_client = None
        self._remote_home = None  # cache for resolve_remote_path()

    def setup_ssh_connection(self):
        """Setup SSH connection to cluster."""
        if not self.config.cluster_host:
            raise ValueError("cluster_host must be specified for SSH-based clusters")

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

        # Use key file for authentication (recommended)
        if self.config.key_file:
            connect_kwargs["key_filename"] = self.config.key_file
        elif self.config.password:
            # Fallback to password authentication (not recommended)
            connect_kwargs["password"] = self.config.password
        else:
            # Try to get SSH credentials from credential manager
            # This ensures we check .env, environment variables, and GitHub Actions
            try:
                from .credential_manager import FlexibleCredentialManager

                credential_manager = FlexibleCredentialManager()
                ssh_credentials = credential_manager.ensure_credential("ssh")

                if ssh_credentials:
                    if "password" in ssh_credentials:
                        connect_kwargs["password"] = ssh_credentials["password"]
                        logger.info("Using SSH password from credential manager")
                    elif "key_file" in ssh_credentials:
                        connect_kwargs["key_filename"] = ssh_credentials["key_file"]
                        logger.info("Using SSH key from credential manager")
            except Exception as e:
                logger.debug(f"Could not load SSH credentials from manager: {e}")
                # Fall back to SSH agent or default keys

        self.ssh_client.connect(**connect_kwargs)
        self.sftp_client = self.ssh_client.open_sftp()

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
        """Check if file exists on remote cluster."""
        if self.ssh_client is None:
            return False
        # The close has to be in a finally, and this method is the reason:
        # a missing file is its *expected* answer, not an error, and
        # sftp.stat raises for it. With the close inside the try, every
        # "no, that file is not there" leaked an SFTP channel for the life
        # of the connection, and the exception that caused it was swallowed
        # -- so a submitter polling for a result file ran out of channels
        # with nothing in the log to say why.
        try:
            sftp = self.ssh_client.open_sftp()
        except Exception:
            return False
        try:
            sftp.stat(remote_path)
            return True
        except Exception:
            return False
        finally:
            sftp.close()

    def connect(self):
        """Establish connection to cluster (for manual connection)."""
        if self.config.cluster_type in ["slurm", "ssh"]:
            if not self.ssh_client:
                self.setup_ssh_connection()

    def disconnect(self):
        """Disconnect from cluster."""
        # A later connect() may use a different username, and a home directory
        # cached from the previous account would be silently wrong.
        self._remote_home = None
        if self.sftp_client:
            self.sftp_client.close()
            self.sftp_client = None
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None
