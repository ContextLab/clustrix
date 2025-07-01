"""
Unified filesystem operations for local and remote clusters.

This module provides a consistent interface for filesystem operations that work
both locally and on remote clusters based on the ClusterConfig object.
"""

import os
import glob as glob_module
from pathlib import Path
from typing import List, Optional, Dict, Any

import paramiko

from .config import ClusterConfig


class FileInfo:
    """File information structure."""

    def __init__(self, size: int, modified: float, is_dir: bool, permissions: str):
        """Initialize FileInfo with file metadata."""
        self.size = size
        self.modified = modified  # Unix timestamp
        self.is_dir = is_dir
        self.permissions = permissions

    @property
    def modified_datetime(self):
        """Get modified time as datetime object."""
        from datetime import datetime

        return datetime.fromtimestamp(self.modified)

    def __repr__(self):
        """String representation of FileInfo."""
        return (
            f"FileInfo(size={self.size}, modified={self.modified}, "
            f"is_dir={self.is_dir}, permissions='{self.permissions}')"
        )

    def __eq__(self, other):
        """Check equality with another FileInfo object."""
        if not isinstance(other, FileInfo):
            return False
        return (
            self.size == other.size
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

            # Check various hostname matching scenarios
            is_on_target_cluster = (
                # Exact match
                current_hostname == target_host
                or
                # Current host contains target (e.g., s17.hpcc.dartmouth.edu contains ndoli.dartmouth.edu)
                target_host in current_hostname
                or
                # Target contains current (e.g., compute node s17 part of ndoli.dartmouth.edu)
                current_hostname in target_host
                or
                # Domain matching (e.g., s04.hpcc.dartmouth.edu and ndoli.dartmouth.edu)
                self._same_domain(current_hostname, target_host)
                or
                # HPC cluster specific: check if both are in same institution domain
                self._same_institution_domain(current_hostname, target_host)
            )

            if is_on_target_cluster:
                # We're on the target cluster - use local filesystem operations
                original_cluster_type = self.config.cluster_type
                self.config.cluster_type = "local"

                # Log the detection for debugging
                print(
                    f"Cluster detection: Running on target cluster (hostname: {current_hostname})"
                )
                print(
                    f"Switched from {original_cluster_type} to local filesystem operations"
                )

        except Exception as e:
            # If detection fails, continue with original cluster_type
            print(f"Warning: Cluster detection failed: {e}")
            pass

    def _same_domain(self, host1: str, host2: str) -> bool:
        """Check if two hostnames are in the same domain."""
        try:
            # Extract domain parts (ignore first part which might be different)
            domain1_parts = host1.split(".")[1:]  # Skip hostname, get domain
            domain2_parts = host2.split(".")[1:]  # Skip hostname, get domain

            # Check if domains match (at least 2 parts)
            if len(domain1_parts) >= 2 and len(domain2_parts) >= 2:
                return domain1_parts == domain2_parts

        except (IndexError, AttributeError):
            pass

        return False

    def _same_institution_domain(self, host1: str, host2: str) -> bool:
        """
        Check if two hostnames are from the same institution.

        This handles cases like:
        - s04.hpcc.dartmouth.edu (compute node)
        - ndoli.dartmouth.edu (head node)

        Both should be considered the same cluster.
        """
        try:
            # Split hostnames into parts
            parts1 = host1.split(".")
            parts2 = host2.split(".")

            # For HPC clusters, check if they share the institution domain
            # e.g., both end in "dartmouth.edu"
            if len(parts1) >= 2 and len(parts2) >= 2:
                # Get the last 2 parts (institution.tld)
                institution1 = ".".join(parts1[-2:])
                institution2 = ".".join(parts2[-2:])

                if institution1 == institution2:
                    # Same institution - likely same cluster
                    return True

            # Also check for common HPC patterns
            # e.g., login.cluster.edu and compute01.cluster.edu
            if len(parts1) >= 3 and len(parts2) >= 3:
                # Check if middle part matches (cluster name)
                cluster1_parts = parts1[-3:]  # Get last 3 parts
                cluster2_parts = parts2[-3:]  # Get last 3 parts

                # If the cluster and institution parts match
                if (
                    cluster1_parts[1:] == cluster2_parts[1:]
                ):  # Same cluster.institution.edu
                    return True

        except (IndexError, AttributeError):
            pass

        return False

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
            self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Connect based on authentication method
            connect_kwargs: Dict[str, Any] = {
                "hostname": self.config.cluster_host,
                "port": self.config.cluster_port,
                "username": self.config.username,
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

    def _remote_ls(self, path: str) -> List[str]:
        """Remote directory listing via SSH."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        # Use ls -1 for one file per line
        cmd = f"ls -1 {full_path} 2>/dev/null || true"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        if output:
            return sorted(output.split("\n"))
        return []

    def _remote_find(self, pattern: str, path: str) -> List[str]:
        """Remote file finding via SSH."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        # Use find command with name pattern
        cmd = f"cd {full_path} && find . -name '{pattern}' -type f | sed 's|^\\./||' | sort"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        if output:
            return output.split("\n")
        return []

    def _remote_stat(self, path: str) -> FileInfo:
        """Remote file stat via SSH."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        # Use stat command with portable format
        # %s = size, %Y = modification time, %f = file type/mode in hex
        cmd = f"stat -c '%s %Y %f' {full_path} 2>/dev/null"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        if not output:
            raise FileNotFoundError(f"File not found: {path}")

        parts = output.split()
        size = int(parts[0])
        mtime = int(parts[1])
        mode_hex = int(parts[2], 16)

        # Check if directory (S_IFDIR = 0x4000)
        is_dir = bool(mode_hex & 0x4000)

        # Extract permissions (last 3 octal digits)
        permissions = oct(mode_hex & 0o777)[-3:]

        return FileInfo(
            size=size, modified=mtime, is_dir=is_dir, permissions=permissions
        )

    def _remote_exists(self, path: str) -> bool:
        """Check if remote path exists."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        cmd = f"test -e {full_path} && echo 'EXISTS' || echo 'NOT_EXISTS'"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        return output == "EXISTS"

    def _remote_isdir(self, path: str) -> bool:
        """Check if remote path is directory."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        cmd = f"test -d {full_path} && echo 'DIR' || echo 'NOT_DIR'"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        return output == "DIR"

    def _remote_isfile(self, path: str) -> bool:
        """Check if remote path is file."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        cmd = f"test -f {full_path} && echo 'FILE' || echo 'NOT_FILE'"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        return output == "FILE"

    def _remote_glob(self, pattern: str, path: str) -> List[str]:
        """Remote glob pattern matching via SSH."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        # Use shell glob expansion with ls
        # The 2>/dev/null suppresses errors for no matches
        cmd = f"cd {full_path} && ls -d {pattern} 2>/dev/null | sort || true"
        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        if output:
            return output.split("\n")
        return []

    def _remote_du(self, path: str) -> DiskUsage:
        """Remote disk usage via SSH."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        # Get total size in bytes
        cmd1 = f"du -sb {full_path} 2>/dev/null | cut -f1"
        stdin, stdout, stderr = ssh_client.exec_command(cmd1)
        size_output = stdout.read().decode().strip()

        # Count files
        cmd2 = f"find {full_path} -type f 2>/dev/null | wc -l"
        stdin, stdout, stderr = ssh_client.exec_command(cmd2)
        count_output = stdout.read().decode().strip()

        total_bytes = int(size_output) if size_output else 0
        file_count = int(count_output) if count_output else 0

        return DiskUsage(total_bytes=total_bytes, file_count=file_count)

    def _remote_count_files(self, path: str, pattern: str) -> int:
        """Remote file counting via SSH."""
        ssh_client = self._get_ssh_client()
        full_path = self._get_full_path(path)

        if pattern == "*":
            # Count all files
            cmd = f"find {full_path} -type f 2>/dev/null | wc -l"
        else:
            # Count files matching pattern
            cmd = f"find {full_path} -name '{pattern}' -type f 2>/dev/null | wc -l"

        stdin, stdout, stderr = ssh_client.exec_command(cmd)
        output = stdout.read().decode().strip()

        return int(output) if output else 0


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
