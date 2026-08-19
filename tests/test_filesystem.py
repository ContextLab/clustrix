"""Tests for filesystem utilities."""

import pytest
import socket
import tempfile
import os
from pathlib import Path
import stat

from clustrix.filesystem import (
    ClusterFilesystem,
    cluster_ls,
    cluster_find,
    cluster_stat,
    cluster_exists,
    cluster_isdir,
    cluster_isfile,
    cluster_glob,
    cluster_du,
    cluster_count_files,
    FileInfo,
    DiskUsage,
)
from clustrix.config import ClusterConfig, configure
from tests.ssh_server import LocalSSHServer

#: A real password for a real server that lives for one test.
SSH_PASSWORD = "clustrix-test-password"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A real ``$HOME`` for the duration of one test.

    ``ssh_host_key_policy="auto_add"`` makes paramiko *write* the server's
    key into ``~/.ssh/known_hosts``. This server's key is generated per run
    and its port is new for every test, so without an isolated home every run
    appends junk to the developer's real known_hosts -- and two runs at once
    interleave their writes and corrupt it, after which unrelated tests fail
    with ``InvalidHostKey``.
    """
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    os.chmod(home / ".ssh", 0o700)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def ssh_server(tmp_path, isolated_home):
    """A real SSH server on a loopback port.

    The remote filesystem operations are all ``exec_command`` against a real
    shell, so pointing them at this server means ``ls``, ``test -e`` and
    ``stat`` really run, against files that really exist.
    """
    root = tmp_path / "remote"
    root.mkdir()
    with LocalSSHServer(root=root, password=SSH_PASSWORD) as server:
        server.root_path = root
        yield server


def _remote_filesystem(server) -> ClusterFilesystem:
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=server.host,
        cluster_port=server.port,
        username="testuser",
        password=SSH_PASSWORD,
        remote_work_dir=str(server.root_path),
        # The server's host key is generated per run, so it can never be in a
        # known_hosts file; verification is tested separately.
        ssh_host_key_policy="auto_add",
    )
    fs = ClusterFilesystem(config)
    # ClusterFilesystem silently switches to local operations when it decides
    # it is already running on the target host. If that ever fired here the
    # tests below would quietly stop testing SSH, so it is checked.
    assert fs.config.cluster_type == "slurm"
    return fs


class TestFileInfo:
    """Test FileInfo data class."""

    def test_fileinfo_creation(self):
        """Test FileInfo object creation."""
        file_info = FileInfo(
            size=1024, modified=1640995200.0, is_dir=False, permissions="rw-r--r--"
        )

        assert file_info.size == 1024
        assert file_info.is_dir is False
        assert file_info.permissions == "rw-r--r--"
        assert file_info.modified == 1640995200.0

        # Test datetime property
        import datetime

        expected_dt = datetime.datetime.fromtimestamp(1640995200.0)
        assert file_info.modified_datetime == expected_dt


class TestDiskUsage:
    """Test DiskUsage data class."""

    def test_diskusage_creation(self):
        """Test DiskUsage object creation."""
        usage = DiskUsage(total_bytes=2048000, file_count=15)

        assert usage.total_bytes == 2048000
        assert usage.file_count == 15
        assert usage.total_mb == pytest.approx(1.95, rel=1e-2)
        assert usage.total_gb == pytest.approx(0.0019, rel=1e-2)


class TestClusterFilesystem:
    """Test ClusterFilesystem class."""

    def test_init_local_config(self):
        """Test initialization with local config."""
        config = ClusterConfig(cluster_type="local")
        fs = ClusterFilesystem(config)

        assert fs.config == config
        assert config.cluster_type == "local"

    def test_init_remote_config(self):
        """Test initialization with remote config."""
        config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.example.com", username="testuser"
        )
        fs = ClusterFilesystem(config)

        assert fs.config == config
        assert config.cluster_type == "slurm"

    def test_local_ls(self):
        """Test local directory listing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            test_files = ["file1.txt", "file2.py", "subdir"]
            for name in test_files[:2]:
                Path(tmpdir, name).touch()
            Path(tmpdir, test_files[2]).mkdir()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            files = fs.ls(".")
            assert set(files) == set(test_files)

    def test_local_exists(self):
        """Test local file existence check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir, "test.txt")
            test_file.touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            assert fs.exists("test.txt") is True
            assert fs.exists("nonexistent.txt") is False

    def test_local_stat(self):
        """Test local file stat."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir, "test.txt")
            test_file.write_text("Hello World")

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            file_info = fs.stat("test.txt")
            assert file_info.size == 11  # "Hello World"
            assert file_info.is_dir is False
            assert file_info.modified > 0

    def test_local_isdir_isfile(self):
        """Test local directory and file type checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir, "test.txt")
            test_file.touch()
            test_dir = Path(tmpdir, "subdir")
            test_dir.mkdir()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            assert fs.isfile("test.txt") is True
            assert fs.isdir("test.txt") is False
            assert fs.isfile("subdir") is False
            assert fs.isdir("subdir") is True

    def test_local_glob(self):
        """Test local glob pattern matching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            files = ["file1.txt", "file2.txt", "script.py", "data.csv"]
            for name in files:
                Path(tmpdir, name).touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            txt_files = fs.glob("*.txt", ".")
            assert set(txt_files) == {"file1.txt", "file2.txt"}

            py_files = fs.glob("*.py", ".")
            assert py_files == ["script.py"]

    def test_local_find(self):
        """Test local file finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create nested structure
            Path(tmpdir, "file1.txt").touch()
            subdir = Path(tmpdir, "subdir")
            subdir.mkdir()
            Path(subdir, "file2.txt").touch()
            Path(subdir, "script.py").touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            txt_files = fs.find("*.txt", ".")
            # Results should be relative paths
            expected = {"file1.txt", "subdir/file2.txt"}
            assert set(txt_files) == expected

    def test_local_count_files(self):
        """Test local file counting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            files = ["file1.txt", "file2.txt", "script.py"]
            for name in files:
                Path(tmpdir, name).touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            total_count = fs.count_files(".", "*")
            assert total_count == 3

            txt_count = fs.count_files(".", "*.txt")
            assert txt_count == 2

    def test_local_du(self):
        """Test local disk usage calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with known sizes
            Path(tmpdir, "small.txt").write_text("a" * 100)  # 100 bytes
            Path(tmpdir, "large.txt").write_text("b" * 1000)  # 1000 bytes

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            usage = fs.du(".")
            assert usage.file_count == 2
            assert usage.total_bytes >= 1100  # At least 1100 bytes

    def test_remote_ls(self, ssh_server):
        """Named in issue #117: this used to test ``str.split``.

        The old version fed a ``MagicMock`` the bytes
        ``b"file1.txt\\nfile2.py\\nsubdir/\\n"`` and asserted those three
        names came back. Every name in the assertion was written by the test
        four lines earlier.

        Here the files really exist, the listing really runs ``ls -1`` in a
        real shell at the far end of a real SSH connection, and the assertion
        is on what is really in the directory -- including the fact that a
        file created after the connection opened shows up, which a canned
        byte string cannot express.
        """
        (ssh_server.root_path / "file1.txt").write_text("one")
        (ssh_server.root_path / "file2.py").write_text("two")
        (ssh_server.root_path / "subdir").mkdir()
        fs = _remote_filesystem(ssh_server)

        assert fs.ls(".") == ["file1.txt", "file2.py", "subdir"]

        # A subdirectory of the real tree, listed by real path resolution.
        (ssh_server.root_path / "subdir" / "nested.dat").write_text("three")
        assert fs.ls("subdir") == ["nested.dat"]

        # A directory that is not there lists as empty rather than raising.
        assert fs.ls("no_such_directory") == []

    def test_remote_exists(self, ssh_server):
        """Existence decided by a real ``test -e`` on a real file."""
        (ssh_server.root_path / "test.txt").write_text("real contents")
        (ssh_server.root_path / "a_directory").mkdir()
        fs = _remote_filesystem(ssh_server)

        assert fs.exists("test.txt") is True
        assert fs.exists("a_directory") is True
        assert fs.exists("nonexistent.txt") is False

        # And it tracks reality: delete the file, and it stops existing.
        (ssh_server.root_path / "test.txt").unlink()
        assert fs.exists("test.txt") is False

    def test_remote_stat(self, ssh_server):
        """Size, mtime and mode read off a real file over a real connection.

        This test used to branch: on a host whose ``stat`` was GNU coreutils
        it asserted the real values, and on a BSD or macOS host it asserted
        ``FileNotFoundError`` for a file that was plainly there. That second
        branch pinned issue #154's second defect -- ``_remote_stat`` ran
        ``stat -c``, which only GNU accepts, and ``2>/dev/null`` turned the
        rejection into a false "not found".

        The branch is gone because the defect is: ``_remote_stat`` reads the
        attributes over SFTP, where size, mtime and mode are protocol fields
        and no remote binary's option spelling is involved. The assertion is
        now the same one on every platform, which is the point.
        """
        target = ssh_server.root_path / "test.txt"
        target.write_text("hello world")  # exactly 11 bytes
        os.chmod(target, 0o640)
        (ssh_server.root_path / "a_directory").mkdir()
        local_stat = target.stat()
        fs = _remote_filesystem(ssh_server)

        file_info = fs.stat("test.txt")
        assert file_info.size == 11 == local_stat.st_size
        assert file_info.modified == pytest.approx(local_stat.st_mtime, abs=1)
        assert file_info.is_dir is False
        assert file_info.is_file is True
        assert file_info.permissions == "640"
        assert file_info.name == "test.txt"

        directory_info = fs.stat("a_directory")
        assert directory_info.is_dir is True

        # A genuine absence is still an absence...
        with pytest.raises(FileNotFoundError):
            fs.stat("no_such_file.txt")

        # ...and the file really is found again once it is really there.
        (ssh_server.root_path / "no_such_file.txt").write_text("now it exists")
        assert fs.stat("no_such_file.txt").size == 13


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_cluster_ls_local(self):
        """Test cluster_ls with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.txt").touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            files = cluster_ls(".", config)
            assert "test.txt" in files

    def test_cluster_exists_local(self):
        """Test cluster_exists with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir, "test.txt")
            test_file.touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            assert cluster_exists("test.txt", config) is True
            assert cluster_exists("nonexistent.txt", config) is False

    def test_cluster_stat_local(self):
        """Test cluster_stat with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir, "test.txt")
            test_file.write_text("Hello")

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            file_info = cluster_stat("test.txt", config)
            assert file_info.size == 5

    def test_cluster_isdir_isfile_local(self):
        """Test cluster_isdir and cluster_isfile with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir, "test.txt")
            test_file.touch()
            test_dir = Path(tmpdir, "subdir")
            test_dir.mkdir()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)

            assert cluster_isfile("test.txt", config) is True
            assert cluster_isdir("test.txt", config) is False
            assert cluster_isfile("subdir", config) is False
            assert cluster_isdir("subdir", config) is True

    def test_cluster_glob_local(self):
        """Test cluster_glob with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            files = ["file1.txt", "file2.txt", "script.py"]
            for name in files:
                Path(tmpdir, name).touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            txt_files = cluster_glob("*.txt", ".", config)
            assert set(txt_files) == {"file1.txt", "file2.txt"}

    def test_cluster_find_local(self):
        """Test cluster_find with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "file1.txt").touch()
            subdir = Path(tmpdir, "subdir")
            subdir.mkdir()
            Path(subdir, "file2.txt").touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            txt_files = cluster_find("*.txt", ".", config)
            expected = {"file1.txt", "subdir/file2.txt"}
            assert set(txt_files) == expected

    def test_cluster_count_files_local(self):
        """Test cluster_count_files with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            files = ["file1.txt", "file2.txt", "script.py"]
            for name in files:
                Path(tmpdir, name).touch()

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)

            total_count = cluster_count_files(".", "*", config)
            assert total_count == 3

            txt_count = cluster_count_files(".", "*.txt", config)
            assert txt_count == 2

    def test_cluster_du_local(self):
        """Test cluster_du with local config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "small.txt").write_text("a" * 100)
            Path(tmpdir, "large.txt").write_text("b" * 1000)

            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            usage = cluster_du(".", config)

            assert usage.file_count == 2
            assert usage.total_bytes >= 1100
            assert usage.total_mb > 0

    def test_convenience_functions_use_default_config(self, tmp_path):
        """The real global configuration is what the convenience functions read.

        The old version patched ``clustrix.config.get_config``. ``configure()``
        is the shipped way to set that configuration, so it is used instead --
        which also means this test would notice if ``configure`` and
        ``get_config`` ever stopped agreeing. The autouse ``reset_config``
        fixture restores the singleton afterwards.
        """
        (tmp_path / "test.txt").write_text("four bytes\n")

        configure(cluster_type="local", local_work_dir=str(tmp_path))

        files = cluster_ls(".")
        assert "test.txt" in files
        assert cluster_exists("test.txt") is True
        assert cluster_stat("test.txt").size == len("four bytes\n")


class TestErrorHandling:
    """Test error handling in filesystem operations."""

    def test_local_stat_nonexistent_file(self):
        """Test stat on nonexistent file raises appropriate error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)
            fs = ClusterFilesystem(config)

            with pytest.raises(FileNotFoundError):
                fs.stat("nonexistent.txt")

    def test_local_operations_invalid_path(self):
        """Test operations on invalid paths."""
        config = ClusterConfig(cluster_type="local", local_work_dir="/nonexistent/path")
        fs = ClusterFilesystem(config)

        # Should handle invalid paths gracefully
        assert fs.exists("test.txt") is False

        # ls on nonexistent directory should return empty list, not raise
        files = fs.ls(".")
        assert files == []

    def test_remote_connection_failure(self):
        """A host that is not listening really fails, and fails quickly.

        The old version made a patched ``paramiko.SSHClient`` raise, which
        proved only that the exception propagated. This connects to a real
        port on the loopback interface with nothing behind it, so the failure
        is produced by the network stack.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]

        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="127.0.0.1",
            cluster_port=closed_port,
            username="testuser",
            password="invalid-password",
            remote_work_dir="/tmp",
            ssh_connect_timeout=5,
        )
        fs = ClusterFilesystem(config)

        with pytest.raises(OSError):
            fs.ls(".")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
