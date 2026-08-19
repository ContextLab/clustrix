"""
Unified filesystem operations for local and remote clusters.

This module provides a consistent interface for filesystem operations that work
both locally and on remote clusters based on the ClusterConfig object.
"""

import fnmatch
import logging
import os
import posixpath
import shlex
import stat as stat_module
import glob as glob_module
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

import paramiko

from .config import ClusterConfig
from .ssh_security import configure_host_key_policy

logger = logging.getLogger(__name__)


class FileInfo:
    """File information structure."""

    def __init__(
        self, size: int, modified: float, is_dir: bool, permissions: str, name: str = ""
    ):
        """Initialize FileInfo with file metadata."""
        self.size = size
        self.modified = modified  # Unix timestamp
        self.is_dir = is_dir
        self.permissions = permissions
        self.name = name

    @property
    def is_file(self):
        """Check if this is a file (not a directory)."""
        return not self.is_dir

    @property
    def modified_datetime(self):
        """Get modified time as datetime object."""
        from datetime import datetime

        return datetime.fromtimestamp(self.modified)

    def __repr__(self):
        """String representation of FileInfo."""
        return (
            f"FileInfo(name='{self.name}', size={self.size}, modified={self.modified}, "
            f"is_dir={self.is_dir}, permissions='{self.permissions}')"
        )

    def __eq__(self, other):
        """Check equality with another FileInfo object."""
        if not isinstance(other, FileInfo):
            return False
        return (
            self.name == other.name
            and self.size == other.size
            and self.modified == other.modified
            and self.is_dir == other.is_dir
            and self.permissions == other.permissions
        )


class DiskUsage:
    """Disk usage information."""

    def __init__(self, total_bytes: int, file_count: int):
        """Initialize DiskUsage with usage statistics."""
        self.total_bytes = total_bytes
        self.file_count = file_count

    @property
    def total_mb(self) -> float:
        """Total size in megabytes."""
        return self.total_bytes / (1024 * 1024)

    @property
    def total_gb(self) -> float:
        """Total size in gigabytes."""
        return self.total_bytes / (1024 * 1024 * 1024)

    def __repr__(self):
        """String representation of DiskUsage."""
        return (
            f"DiskUsage(total_bytes={self.total_bytes}, file_count={self.file_count})"
        )

    def __eq__(self, other):
        """Check equality with another DiskUsage object."""
        if not isinstance(other, DiskUsage):
            return False
        return (
            self.total_bytes == other.total_bytes
            and self.file_count == other.file_count
        )


class ClusterFilesystem:
    """Unified filesystem operations for local and remote clusters."""

    def __init__(self, config: ClusterConfig):
        """Initialize filesystem with cluster configuration."""
        self.config = config
        self._ssh_client: Optional[paramiko.SSHClient] = None
        self._sftp_client: Optional[paramiko.SFTPClient] = None

        # Auto-detect if we're running on the target cluster (for shared filesystems)
        self._auto_detect_cluster_location()

    def _auto_detect_cluster_location(self):
        """
        Auto-detect if we're already running on the target cluster.

        If we're running on the same cluster as the target, we should use local
        filesystem operations instead of SSH, since most HPC clusters have shared
        filesystems (NFS/Lustre) across head and compute nodes.
        """
        # Only attempt detection if cluster_type is not already 'local'
        if self.config.cluster_type == "local":
            return

        # Skip detection if no cluster_host is configured
        if not hasattr(self.config, "cluster_host") or not self.config.cluster_host:
            return

        try:
            import socket

            current_hostname = socket.gethostname()
            target_host = self.config.cluster_host

            # Whether we can use local filesystem operations is a question
            # about the FILESYSTEM, not about names. The previous test asked
            # whether the two hostnames looked related -- substring matches
            # plus "same institution domain" -- so a laptop on the VPN, whose
            # hostname was a VPN-assigned name in the same domain as the
            # cluster, it was judged to BE the cluster. Clustrix then looked
            # for the job's result file on the laptop, found an empty
            # directory, and reported the job's status as unknown.
            #
            # Two things are actually sufficient, and both are checkable:
            #   * this host IS the target host, by name; or
            #   * the remote working directory is visible here, which is what
            #     "shared filesystem" means and what the code needs to be true.
            fqdn = socket.getfqdn()
            same_host = target_host in (current_hostname, fqdn) or fqdn.startswith(
                target_host.split(".")[0] + "."
            )

            work_dir = os.path.expanduser(
                getattr(self.config, "remote_work_dir", "") or ""
            )
            shared_filesystem = bool(work_dir) and os.path.isdir(work_dir)

            if same_host and shared_filesystem:
                original_cluster_type = self.config.cluster_type
                self.config.cluster_type = "local"
                logger.info(
                    "Running on %s with %s visible locally; using local "
                    "filesystem operations instead of %s.",
                    target_host,
                    work_dir,
                    original_cluster_type,
                )
            elif same_host:
                logger.debug(
                    "Hostname matches %s but %s is not present locally; "
                    "keeping remote filesystem operations.",
                    target_host,
                    work_dir,
                )

        except Exception as e:
            # If detection fails, continue with original cluster_type
            print(f"Warning: Cluster detection failed: {e}")
            pass

    def __del__(self):
        """Clean up SSH connections."""
        self._close_connections()

    def _close_connections(self):
        """Close SSH and SFTP connections."""
        if self._sftp_client:
            self._sftp_client.close()
            self._sftp_client = None
        if self._ssh_client:
            self._ssh_client.close()
            self._ssh_client = None

    def _get_ssh_client(self) -> paramiko.SSHClient:
        """Get or create SSH client connection."""
        if self._ssh_client is None:
            self._ssh_client = paramiko.SSHClient()
            configure_host_key_policy(self._ssh_client, self.config)

            # Connect based on authentication method
            connect_kwargs: Dict[str, Any] = {
                "hostname": self.config.cluster_host,
                "port": self.config.cluster_port,
                "username": self.config.username,
                # Without this paramiko waits on the OS default, which on an
                # unreachable or non-answering host is minutes. A filesystem
                # call that cannot connect should say so quickly.
                "timeout": getattr(self.config, "ssh_connect_timeout", 30),
                "auth_timeout": getattr(self.config, "ssh_connect_timeout", 30),
                "banner_timeout": getattr(self.config, "ssh_connect_timeout", 30),
            }

            if self.config.key_file:
                connect_kwargs["key_filename"] = self.config.key_file
            elif self.config.password:
                connect_kwargs["password"] = self.config.password
            else:
                # Try default SSH key locations
                connect_kwargs["look_for_keys"] = True

            self._ssh_client.connect(**connect_kwargs)

        return self._ssh_client

    def _get_sftp_client(self) -> paramiko.SFTPClient:
        """Get or create SFTP client."""
        if self._sftp_client is None:
            ssh = self._get_ssh_client()
            self._sftp_client = ssh.open_sftp()
        return self._sftp_client

    def _get_full_path(self, path: str) -> str:
        """Get full path based on working directory."""
        if self.config.cluster_type == "local":
            base_dir = self.config.local_work_dir or os.getcwd()
        else:
            base_dir = self.config.remote_work_dir

        # Handle absolute paths
        if os.path.isabs(path):
            return path

        return os.path.join(base_dir, path)

    # ===== Core Operations =====

    def ls(self, path: str = ".") -> List[str]:
        """List directory contents."""
        if self.config.cluster_type == "local":
            return self._local_ls(path)
        else:
            return self._remote_ls(path)

    def find(self, pattern: str, path: str = ".") -> List[str]:
        """Find files matching pattern."""
        if self.config.cluster_type == "local":
            return self._local_find(pattern, path)
        else:
            return self._remote_find(pattern, path)

    def stat(self, path: str) -> FileInfo:
        """Get file/directory information."""
        if self.config.cluster_type == "local":
            return self._local_stat(path)
        else:
            return self._remote_stat(path)

    def exists(self, path: str) -> bool:
        """Check if file/directory exists."""
        if self.config.cluster_type == "local":
            return self._local_exists(path)
        else:
            return self._remote_exists(path)

    def isdir(self, path: str) -> bool:
        """Check if path is a directory."""
        if self.config.cluster_type == "local":
            return self._local_isdir(path)
        else:
            return self._remote_isdir(path)

    def isfile(self, path: str) -> bool:
        """Check if path is a file."""
        if self.config.cluster_type == "local":
            return self._local_isfile(path)
        else:
            return self._remote_isfile(path)

    def glob(self, pattern: str, path: str = ".") -> List[str]:
        """Pattern matching for files."""
        if self.config.cluster_type == "local":
            return self._local_glob(pattern, path)
        else:
            return self._remote_glob(pattern, path)

    def du(self, path: str = ".") -> DiskUsage:
        """Get directory usage information."""
        if self.config.cluster_type == "local":
            return self._local_du(path)
        else:
            return self._remote_du(path)

    def count_files(self, path: str = ".", pattern: str = "*") -> int:
        """Count files in directory matching pattern."""
        if self.config.cluster_type == "local":
            return self._local_count_files(path, pattern)
        else:
            return self._remote_count_files(path, pattern)

    # ===== Local Implementations =====

    def _local_ls(self, path: str) -> List[str]:
        """Local directory listing."""
        full_path = self._get_full_path(path)
        try:
            return sorted(os.listdir(full_path))
        except (OSError, IOError):
            return []

    def _local_find(self, pattern: str, path: str) -> List[str]:
        """Local file finding."""
        full_path = self._get_full_path(path)
        base_path = Path(full_path)

        results = []
        for item in base_path.rglob(pattern):
            # Return relative paths from the search directory
            try:
                rel_path = item.relative_to(base_path)
                # Normalize path separators to forward slashes for consistency
                normalized_path = str(rel_path).replace(os.sep, "/")
                results.append(normalized_path)
            except ValueError:
                # If relative_to fails, use absolute path
                normalized_path = str(item).replace(os.sep, "/")
                results.append(normalized_path)

        return sorted(results)

    def _local_stat(self, path: str) -> FileInfo:
        """Local file stat."""
        full_path = self._get_full_path(path)
        stat = os.stat(full_path)

        return FileInfo(
            size=stat.st_size,
            modified=stat.st_mtime,
            is_dir=os.path.isdir(full_path),
            permissions=oct(stat.st_mode)[-3:],
            name=os.path.basename(path),
        )

    def _local_exists(self, path: str) -> bool:
        """Check if local path exists."""
        full_path = self._get_full_path(path)
        return os.path.exists(full_path)

    def _local_isdir(self, path: str) -> bool:
        """Check if local path is directory."""
        full_path = self._get_full_path(path)
        return os.path.isdir(full_path)

    def _local_isfile(self, path: str) -> bool:
        """Check if local path is file."""
        full_path = self._get_full_path(path)
        return os.path.isfile(full_path)

    def _local_glob(self, pattern: str, path: str) -> List[str]:
        """Local glob pattern matching."""
        full_path = self._get_full_path(path)
        search_pattern = os.path.join(full_path, pattern)

        results = []
        for match in glob_module.glob(search_pattern):
            # Return relative paths from the search directory
            try:
                rel_path = os.path.relpath(match, full_path)
                results.append(rel_path)
            except ValueError:
                results.append(match)

        return sorted(results)

    def _local_du(self, path: str) -> DiskUsage:
        """Local disk usage."""
        full_path = self._get_full_path(path)
        total_size = 0
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(full_path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(filepath)
                    file_count += 1
                except (OSError, IOError):
                    # Skip files we can't access
                    pass

        return DiskUsage(total_bytes=total_size, file_count=file_count)

    def _local_count_files(self, path: str, pattern: str) -> int:
        """Count local files matching pattern."""
        if pattern == "*":
            # Optimize for counting all files
            full_path = self._get_full_path(path)
            count = 0
            for _, _, filenames in os.walk(full_path):
                count += len(filenames)
            return count
        else:
            # Use find for pattern matching
            return len(self._local_find(pattern, path))

    # ===== Remote Implementations =====
    #
    # Two rules hold for everything below, and tests/unit/test_filesystem_injection.py
    # enforces both:
    #
    #   1. Prefer SFTP. ``listdir``, ``stat`` and friends travel as protocol
    #      messages, so a path is a path -- there is no shell to quote for, no
    #      leading ``-`` to be read as a flag, and no GNU-vs-BSD difference in
    #      how a command spells its options.
    #   2. Where a shell is genuinely needed (only ``find``, for a recursive
    #      search whose pattern must stay a pattern), every caller-supplied
    #      value is passed through ``shlex.quote``. This is the same treatment
    #      ``clustrix/utils.py`` gives job-script values.

    def _run_remote(self, cmd: str) -> str:
        """Run a shell command on the cluster and return its stdout.

        Every caller-supplied value in ``cmd`` must already be quoted with
        ``shlex.quote`` before it gets here.

        A non-zero exit status is logged together with the command's stderr.
        These commands used to end in ``2>/dev/null``, which turned every
        failure mode -- a missing directory, a permission error, an option
        the remote host's binary does not support -- into empty output that
        the caller read as "there is nothing there".
        """
        ssh_client = self._get_ssh_client()
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode()
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            logger.warning(
                "Remote command failed (exit %s): %s: %s",
                exit_status,
                cmd,
                stderr.read().decode().strip(),
            )
        return output

    def _remote_attrs(self, full_path: str) -> Optional[Any]:
        """SFTP attributes for ``full_path``, or None if it does not exist.

        Anything that is not an absence -- a permission error, a dead
        connection -- propagates as ``OSError`` rather than being reported as
        "not found".
        """
        sftp = self._get_sftp_client()
        try:
            return sftp.stat(full_path)
        except FileNotFoundError:
            return None

    def _remote_attrs_for_predicate(self, path: str) -> Optional[Any]:
        """Attributes for a boolean question: absent or unreadable is None.

        ``exists``/``isdir``/``isfile`` return a bool, so an unreadable path
        has to come back as False the way ``os.path.exists`` does -- but the
        reason is logged instead of being silently discarded.
        """
        full_path = self._get_full_path(path)
        try:
            return self._remote_attrs(full_path)
        except OSError as exc:
            logger.warning("Cannot stat remote path %s: %s", full_path, exc)
            return None

    def _remote_ls(self, path: str) -> List[str]:
        """Remote directory listing over SFTP."""
        sftp = self._get_sftp_client()
        full_path = self._get_full_path(path)

        try:
            return sorted(sftp.listdir(full_path))
        except OSError as exc:
            # Matches _local_ls, which returns [] rather than raising.
            logger.debug("Cannot list remote directory %s: %s", full_path, exc)
            return []

    def _remote_find(self, pattern: str, path: str) -> List[str]:
        """Remote recursive file search via ``find``.

        This is the one operation with no SFTP equivalent: walking the tree
        over SFTP would cost a round trip per directory. So the shell stays,
        and both caller-supplied values are quoted.

        Quoting ``pattern`` does not stop it being a pattern: ``find``
        expands ``-name`` itself, and quoting is precisely what stops the
        *shell* from expanding (or executing) it first.

        ``-print0`` rather than ``-print`` because a filename may legally
        contain a newline, and splitting such output on newlines would report
        one real file as two imaginary ones.
        """
        full_path = self._get_full_path(path)

        cmd = (
            f"cd -- {shlex.quote(full_path)} && "
            f"find . -name {shlex.quote(pattern)} -type f -print0"
        )
        output = self._run_remote(cmd)

        results = []
        for entry in output.split("\0"):
            if not entry:
                continue
            results.append(entry[2:] if entry.startswith("./") else entry)
        return sorted(results)

    def _remote_stat(self, path: str) -> FileInfo:
        """Remote file stat over SFTP.

        This used to run ``stat -c '%s %Y %f'``, whose ``-c`` is GNU
        coreutils only -- a BSD or macOS host rejects it, ``2>/dev/null`` ate
        the error, and the caller was told a file that plainly exists is not
        there. SFTP returns size, mtime and mode as protocol fields, so there
        is no remote binary whose options can differ.
        """
        full_path = self._get_full_path(path)

        attrs = self._remote_attrs(full_path)
        if attrs is None:
            raise FileNotFoundError(f"File not found: {path}")

        mode = attrs.st_mode or 0

        return FileInfo(
            size=int(attrs.st_size or 0),
            modified=float(attrs.st_mtime or 0),
            is_dir=stat_module.S_ISDIR(mode),
            # ``oct(mode & 0o777)[-3:]`` is only three digits when the value
            # needs three: 0o000 renders "0o0", 0o007 "0o7", 0o077 "o77".
            # ``_local_stat`` never showed this because it slices an
            # unmasked ``st_mode``, whose file-type bits guarantee enough
            # digits. Formatting to a fixed width says what was meant.
            permissions=format(mode & 0o777, "03o"),
            name=os.path.basename(path),
        )

    def _remote_exists(self, path: str) -> bool:
        """Check if remote path exists, over SFTP."""
        return self._remote_attrs_for_predicate(path) is not None

    def _remote_isdir(self, path: str) -> bool:
        """Check if remote path is a directory, over SFTP."""
        attrs = self._remote_attrs_for_predicate(path)
        return attrs is not None and stat_module.S_ISDIR(attrs.st_mode or 0)

    def _remote_isfile(self, path: str) -> bool:
        """Check if remote path is a regular file, over SFTP."""
        attrs = self._remote_attrs_for_predicate(path)
        return attrs is not None and stat_module.S_ISREG(attrs.st_mode or 0)

    # ``glob.glob`` is the contract for pattern matching, and ``_local_glob``
    # is a thin wrapper around it. The only way the two sides can agree is for
    # the remote side to run the same algorithm over remote directory
    # entries, so the helpers below mirror ``glob._iglob``, ``_glob0``,
    # ``_glob1`` and ``_iterdir`` one for one, with SFTP where the stdlib uses
    # ``os``. A cheaper hand-rolled component split is what caused the last
    # divergence: it discarded the empty trailing component of ``*/``, so a
    # pattern that means "directories only" started matching files as well.

    @staticmethod
    def _has_magic(text: str) -> bool:
        """``glob.has_magic``: does this string need expanding at all?"""
        return any(char in text for char in "*?[")

    def _remote_lexists(self, full_path: str) -> bool:
        """``os.path.lexists`` over SFTP -- a broken symlink still exists."""
        sftp = self._get_sftp_client()
        try:
            sftp.lstat(full_path)
        except OSError:
            return False
        return True

    def _remote_path_isdir(self, full_path: str) -> bool:
        """``os.path.isdir`` over SFTP, following symlinks as it does."""
        sftp = self._get_sftp_client()
        try:
            attrs = sftp.stat(full_path)
        except OSError:
            return False
        return stat_module.S_ISDIR(attrs.st_mode or 0)

    def _remote_is_symlink(self, full_path: str) -> bool:
        """Is this path a symlink itself, whatever it points at?"""
        sftp = self._get_sftp_client()
        try:
            attrs = sftp.lstat(full_path)
        except OSError as exc:
            logger.debug("Cannot lstat remote path %s: %s", full_path, exc)
            return False
        return stat_module.S_ISLNK(attrs.st_mode or 0)

    def _remote_entry_is_dir(self, directory: str, entry: Any) -> bool:
        """``os.DirEntry.is_dir()``: a symlink is judged by its target.

        OpenSSH answers a readdir with ``lstat`` attributes, so a symlink
        arrives here as one and its target has to be looked up. A server
        that answers with ``stat`` attributes has already resolved it.
        """
        mode = entry.st_mode or 0
        if stat_module.S_ISLNK(mode):
            return self._remote_path_isdir(
                posixpath.join(directory or ".", entry.filename)
            )
        return stat_module.S_ISDIR(mode)

    def _remote_iterdir(self, directory: str, dironly: bool) -> List[str]:
        """``glob._iterdir``: entry names, or only the directory ones."""
        sftp = self._get_sftp_client()
        try:
            entries = sftp.listdir_attr(directory or ".")
        except OSError as exc:
            logger.debug("Cannot list remote directory %s: %s", directory, exc)
            return []

        names = []
        for entry in entries:
            if dironly and not self._remote_entry_is_dir(directory, entry):
                continue
            names.append(entry.filename)
        return names

    def _remote_glob1(self, directory: str, pattern: str, dironly: bool) -> List[str]:
        """``glob._glob1``: expand a wildcard component in one directory."""
        names = self._remote_iterdir(directory, dironly)
        if not pattern.startswith("."):
            names = [name for name in names if not name.startswith(".")]
        return fnmatch.filter(names, pattern)

    def _remote_glob0(self, directory: str, basename: str, dironly: bool) -> List[str]:
        """``glob._glob0``: a literal component only has to exist.

        ``dironly`` is unused here, exactly as it is in the stdlib, and the
        parameter stays so this and ``_remote_glob1`` remain interchangeable.
        """
        del dironly
        if not basename:
            # ``posixpath.split`` gives an empty basename for a pattern that
            # ends in a separator, and "a*/" must match only directories.
            if self._remote_path_isdir(directory):
                return [basename]
        elif self._remote_lexists(posixpath.join(directory, basename)):
            return [basename]
        return []

    def _remote_iglob(self, pattern: str, dironly: bool) -> Iterator[str]:
        """``glob._iglob``: the recursive component-by-component expansion."""
        directory, basename = posixpath.split(pattern)
        if not self._has_magic(pattern):
            if basename:
                if self._remote_lexists(pattern):
                    yield pattern
            elif self._remote_path_isdir(directory):
                yield pattern
            return
        if not directory:
            yield from self._remote_glob1(directory, basename, dironly)
            return
        directories: Iterable[str]
        if directory != pattern and self._has_magic(directory):
            directories = self._remote_iglob(directory, True)
        else:
            directories = [directory]
        expand = self._remote_glob1 if self._has_magic(basename) else self._remote_glob0
        for parent in directories:
            for name in expand(parent, basename, dironly):
                yield posixpath.join(parent, name)

    def _remote_glob(self, pattern: str, path: str) -> List[str]:
        """Remote pattern matching, expanded here rather than by a shell.

        The old implementation ran ``ls -d {pattern}`` and relied on the
        remote shell to expand it, which is why the pattern could not simply
        be quoted: quoting it would have stopped it being a pattern at all.
        Expanding it here against real directory entries removes the
        dilemma -- globbing still works, and nothing reaches a shell.

        The expansion is ``glob.glob``'s, so every rule ``_local_glob`` obeys
        holds here too: a trailing slash matches directories only, a leading
        dot is matched only by a pattern that has one, an absolute pattern
        ignores the working directory, and the returned paths are normalised
        by ``relpath`` the same way.
        """
        full_path = self._get_full_path(path)
        search_pattern = posixpath.join(full_path, pattern)

        results = []
        for match in self._remote_iglob(search_pattern, False):
            try:
                results.append(posixpath.relpath(match, full_path))
            except ValueError:
                results.append(match)
        return sorted(results)

    def _remote_du(self, path: str) -> DiskUsage:
        """Remote disk usage, walked over SFTP.

        This used to run ``du -sb``, and ``-b`` is GNU coreutils only: on a
        BSD or macOS host the command failed, ``2>/dev/null`` hid it, and the
        directory was reported as holding zero bytes. It also measured
        something different from ``_local_du``, which sums the sizes of the
        regular files underneath ``path``. Walking over SFTP costs a round
        trip per directory but is portable, needs no quoting, and counts
        exactly what the local implementation counts.

        Symlinks are counted the way ``_local_du`` counts them, which is the
        way ``os.walk(followlinks=False)`` plus ``os.path.getsize`` do: a link
        to a file contributes its *target's* size, once, and a link to a
        directory contributes nothing and is not descended into. That last
        rule is also why this loop terminates. The only way to build a cycle
        out of POSIX directories is a symlink, and no symlink is followed, so
        no directory can be reached twice -- which is exactly why ``os.walk``
        needs no visited set either. A link back to an ancestor used to make
        this an endless walk.
        """
        sftp = self._get_sftp_client()
        full_path = self._get_full_path(path)

        total_size = 0
        file_count = 0
        pending = [full_path]
        while pending:
            directory = pending.pop()
            try:
                entries = sftp.listdir_attr(directory)
            except OSError as exc:
                logger.warning("Cannot read remote directory %s: %s", directory, exc)
                continue
            for entry in entries:
                mode = entry.st_mode or 0
                child = posixpath.join(directory, entry.filename)
                if stat_module.S_ISLNK(mode):
                    # readdir answered with lstat attributes, so this is
                    # known to be a link and only its target matters.
                    size = self._remote_link_target_size(child)
                    if size is not None:
                        total_size += size
                        file_count += 1
                elif stat_module.S_ISDIR(mode):
                    # readdir may have answered with stat attributes, in
                    # which case a link to a directory is indistinguishable
                    # from the directory here, and descending into it is the
                    # endless walk. One lstat settles it.
                    if not self._remote_is_symlink(child):
                        pending.append(child)
                elif stat_module.S_ISREG(mode):
                    total_size += int(entry.st_size or 0)
                    file_count += 1

        return DiskUsage(total_bytes=total_size, file_count=file_count)

    def _remote_link_target_size(self, full_path: str) -> Optional[int]:
        """What ``os.path.getsize`` would report for a symlink, or None.

        ``getsize`` follows the link, so a link to a regular file counts at
        the target's size. A link to a directory is not a file and a broken
        link raises -- ``_local_du`` catches that ``OSError`` and skips the
        entry, so both come back as None.
        """
        sftp = self._get_sftp_client()
        try:
            attrs = sftp.stat(full_path)
        except OSError as exc:
            logger.debug("Cannot stat remote symlink %s: %s", full_path, exc)
            return None
        if stat_module.S_ISREG(attrs.st_mode or 0):
            return int(attrs.st_size or 0)
        return None

    def _remote_count_files(self, path: str, pattern: str) -> int:
        """Count remote files matching ``pattern``.

        The same ``find`` that ``_remote_find`` runs, counted here rather
        than piped into ``wc -l`` -- ``wc -l`` counts newlines, and a
        filename may contain one.
        """
        return len(self._remote_find(pattern, path))


# ===== Convenience Functions =====


def cluster_ls(path: str = ".", config: Optional[ClusterConfig] = None) -> List[str]:
    """List directory contents locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.ls(path)


def cluster_find(
    pattern: str, path: str = ".", config: Optional[ClusterConfig] = None
) -> List[str]:
    """Find files matching pattern locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.find(pattern, path)


def cluster_stat(path: str, config: Optional[ClusterConfig] = None) -> FileInfo:
    """Get file information locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.stat(path)


def cluster_exists(path: str, config: Optional[ClusterConfig] = None) -> bool:
    """Check if file/directory exists locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.exists(path)


def cluster_isdir(path: str, config: Optional[ClusterConfig] = None) -> bool:
    """Check if path is directory locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.isdir(path)


def cluster_isfile(path: str, config: Optional[ClusterConfig] = None) -> bool:
    """Check if path is file locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.isfile(path)


def cluster_glob(
    pattern: str, path: str = ".", config: Optional[ClusterConfig] = None
) -> List[str]:
    """Pattern matching for files locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.glob(pattern, path)


def cluster_du(path: str = ".", config: Optional[ClusterConfig] = None) -> DiskUsage:
    """Get directory usage locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.du(path)


def cluster_count_files(
    path: str = ".", pattern: str = "*", config: Optional[ClusterConfig] = None
) -> int:
    """Count files matching pattern locally or remotely based on config."""
    if config is None:
        from .config import get_config

        config = get_config()
    fs = ClusterFilesystem(config)
    return fs.count_files(path, pattern)
