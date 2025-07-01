"""Tests for filesystem utilities."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
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
from clustrix.config import ClusterConfig


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

    @patch("paramiko.SSHClient")
    def test_remote_ls(self, mock_ssh_class):
        """Test remote directory listing."""
        # Mock SSH client
        mock_ssh = MagicMock()
        mock_ssh_class.return_value = mock_ssh

        # Mock command execution
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"file1.txt\nfile2.py\nsubdir/\n"
        mock_ssh.exec_command.return_value = (None, mock_stdout, None)

        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.example.com",
            username="testuser",
            password="testpass",
            remote_work_dir="/home/testuser",
        )
        fs = ClusterFilesystem(config)

        files = fs.ls(".")
        expected = ["file1.txt", "file2.py", "subdir"]
        # Remove trailing slashes for comparison
        cleaned_files = [f.rstrip("/") for f in files]
        assert set(cleaned_files) == set(expected)

    @patch("paramiko.SSHClient")
    def test_remote_exists(self, mock_ssh_class):
        """Test remote file existence check."""
        mock_ssh = MagicMock()
        mock_ssh_class.return_value = mock_ssh

        # Mock successful exists command (file exists)
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"EXISTS"
        mock_ssh.exec_command.return_value = (None, mock_stdout, None)

        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.example.com",
            username="testuser",
            password="testpass",
        )
        fs = ClusterFilesystem(config)

        assert fs.exists("test.txt") is True

        # Mock failed exists command (file doesn't exist)
        mock_stdout.read.return_value = b"NOT_EXISTS"
        assert fs.exists("nonexistent.txt") is False

    @patch("paramiko.SSHClient")
    def test_remote_stat(self, mock_ssh_class):
        """Test remote file stat."""
        mock_ssh = MagicMock()
        mock_ssh_class.return_value = mock_ssh

        # Mock stat output: size mtime mode
        stat_output = "11 1640995200 81a4"  # 11 bytes, timestamp, regular file mode
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = stat_output.encode()
        mock_ssh.exec_command.return_value = (None, mock_stdout, None)

        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.example.com",
            username="testuser",
            password="testpass",
        )
        fs = ClusterFilesystem(config)

        file_info = fs.stat("test.txt")
        assert file_info.size == 11
        assert file_info.modified == 1640995200.0


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

    def test_convenience_functions_use_default_config(self):
        """Test that convenience functions can use default config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.txt").touch()

            # Mock get_config to return our test config
            test_config = ClusterConfig(cluster_type="local", local_work_dir=tmpdir)

            with patch("clustrix.config.get_config", return_value=test_config):
                # Should work without explicitly passing config
                files = cluster_ls(".")
                assert "test.txt" in files

                assert cluster_exists("test.txt") is True

                file_info = cluster_stat("test.txt")
                assert file_info.size >= 0  # Empty files are valid


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

    @patch("paramiko.SSHClient")
    def test_remote_connection_failure(self, mock_ssh_class):
        """Test handling of remote connection failures."""
        mock_ssh_class.side_effect = Exception("Connection failed")

        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="invalid.example.com",
            username="testuser",
        )
        fs = ClusterFilesystem(config)

        with pytest.raises(Exception):
            fs.ls(".")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
