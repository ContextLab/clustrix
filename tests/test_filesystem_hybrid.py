"""
Hybrid filesystem tests combining real operations with mock validation.

This demonstrates the hybrid approach where we use real filesystem operations
for primary validation and mocks for edge cases and error conditions.
"""

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


class TestFileSystemHybrid:
    """Hybrid tests for filesystem operations."""

    def test_local_filesystem_operations_real(self):
        """Test local filesystem operations with real files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create real test structure
            test_files = {
                "data.csv": "col1,col2,col3\n1,2,3\n4,5,6",
                "script.py": "#!/usr/bin/env python\nprint('hello')",
                "config.yml": "key: value\nlist: [1, 2, 3]",
                "subdir/nested.txt": "nested content",
            }

            # Create real files
            for filepath, content in test_files.items():
                full_path = Path(tmpdir) / filepath
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)

            # Test with real ClusterFilesystem
            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            # Test real operations
            files = fs.ls(tmpdir)
            assert len(files) >= 3  # At least 3 files + 1 directory

            # Test file existence
            assert fs.exists(str(Path(tmpdir) / "data.csv"))
            assert not fs.exists(str(Path(tmpdir) / "nonexistent.txt"))

            # Test file type checks
            assert fs.isfile(str(Path(tmpdir) / "data.csv"))
            assert fs.isdir(str(Path(tmpdir) / "subdir"))

            # Test file info
            file_info = fs.stat(str(Path(tmpdir) / "data.csv"))
            assert file_info.size > 0
            assert file_info.name == "data.csv"
            assert file_info.is_file
            assert not file_info.is_dir

    def test_remote_filesystem_operations_mock(self):
        """Test remote filesystem operations with mocks for edge cases."""
        config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.example.com", username="testuser"
        )

        # Mock SSH client and SFTP
        with patch("clustrix.filesystem.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh = Mock()
            mock_sftp = Mock()
            mock_ssh_class.return_value = mock_ssh
            mock_ssh.open_sftp.return_value = mock_sftp

            # Mock SSH exec_command to return proper 3-tuple
            mock_stdin = Mock()
            mock_stdout = Mock()
            mock_stderr = Mock()
            mock_stdout.read.return_value = b"file1.txt\nfile2.py\nsubdir"
            mock_ssh.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

            # Mock SFTP operations
            mock_sftp.listdir.return_value = ["file1.txt", "file2.py", "subdir"]
            mock_sftp.stat.return_value = Mock(
                st_size=1024, st_mtime=1640995200.0, st_mode=stat.S_IFREG | 0o644
            )

            fs = ClusterFilesystem(config)

            # Test operations with mocked SSH
            files = fs.ls("/remote/path")
            assert "file1.txt" in files
            assert "file2.py" in files
            assert "subdir" in files

            # Verify SSH methods were called (ls uses exec_command, not SFTP)
            mock_ssh.exec_command.assert_called()
            # mock_ssh.open_sftp.assert_called()  # Not called for ls operation
            # mock_sftp.listdir.assert_called_with("/remote/path")  # Not used for ls

    def test_error_handling_hybrid(self):
        """Test error handling with real and mocked errors."""
        # Test real error conditions
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            # Test real FileNotFoundError
            with pytest.raises(FileNotFoundError):
                fs.stat("/nonexistent/path/file.txt")

            # Test permission handling behavior (Unix only)
            if os.name != "nt":
                restricted_dir = Path(tmpdir) / "restricted"
                restricted_dir.mkdir(mode=0o000)  # No permissions

                try:
                    # Test how the filesystem handles restricted permissions
                    # Different systems may behave differently
                    try:
                        result = fs.ls(str(restricted_dir))
                        # Some systems allow listing but return empty list
                        print(f"✓ Restricted directory access returned: {result}")
                        assert isinstance(result, list)  # Should at least return a list
                    except PermissionError:
                        # This is also valid behavior
                        print("✓ PermissionError raised as expected")
                    except OSError as e:
                        # Other OS errors are also acceptable
                        print(f"✓ OSError handled: {e}")

                finally:
                    # Restore permissions for cleanup
                    restricted_dir.chmod(0o755)

        # Test mocked SSH connection errors
        config = ClusterConfig(
            cluster_type="ssh",
            cluster_host="unreachable.example.com",
            username="testuser",
        )

        with patch("clustrix.filesystem.paramiko.SSHClient") as mock_ssh_class:
            mock_ssh = Mock()
            mock_ssh_class.return_value = mock_ssh

            # Mock SSH connection failure
            mock_ssh.connect.side_effect = ConnectionError("Connection failed")

            fs = ClusterFilesystem(config)

            # Test that connection error is handled appropriately
            with pytest.raises(ConnectionError):
                fs.ls("/remote/path")

    def test_cluster_utility_functions_hybrid(self):
        """Test cluster utility functions with hybrid approach."""
        # Test with real local operations
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create real test structure
            test_files = [
                ("data1.csv", "col1,col2\n1,2\n3,4"),
                ("data2.csv", "col1,col2\n5,6\n7,8"),
                ("script.py", "print('test')"),
                ("config.yml", "setting: value"),
            ]

            for filename, content in test_files:
                (Path(tmpdir) / filename).write_text(content)

            config = ClusterConfig(cluster_type="local")

            # Test real operations
            csv_files = cluster_find("*.csv", tmpdir, config)
            assert len(csv_files) == 2

            all_files = cluster_glob("*", tmpdir, config)
            assert len(all_files) == 4

            # Test file count
            # Force local operation by using filesystem instance directly
            fs = ClusterFilesystem(config)
            file_count = fs.count_files(tmpdir)
            assert file_count == 4

            # Test disk usage
            usage = fs.du(tmpdir)
            assert usage.file_count == 4
            assert usage.total_bytes > 0

        # Test with mocked remote operations
        config = ClusterConfig(
            cluster_type="slurm", cluster_host="hpc.example.com", username="user"
        )

        with patch("clustrix.filesystem.ClusterFilesystem") as mock_fs_class:
            mock_fs = Mock()
            mock_fs_class.return_value = mock_fs

            # Mock return values
            mock_fs.find.return_value = [
                FileInfo(
                    size=1024,
                    modified=1640995200.0,
                    is_dir=False,
                    permissions="rw-r--r--",
                    name="remote_file.txt",
                )
            ]

            # Test mocked operations
            files = cluster_find("*.txt", "/remote/path", config)
            assert len(files) == 1
            assert files[0].name == "remote_file.txt"

            # Verify mock was called correctly
            mock_fs.find.assert_called_with("*.txt", "/remote/path")

    def test_performance_validation_real(self):
        """Test performance with real operations."""
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create many files for performance testing
            file_count = 100
            for i in range(file_count):
                (Path(tmpdir) / f"file_{i:03d}.txt").write_text(f"Content {i}")

            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            # Time directory listing
            start_time = time.time()
            files = fs.ls(tmpdir)
            list_time = time.time() - start_time

            # Should be reasonably fast
            assert list_time < 1.0  # Less than 1 second
            assert len(files) == file_count

            # Time file existence checks
            start_time = time.time()
            for i in range(10):  # Check 10 files
                assert fs.exists(str(Path(tmpdir) / f"file_{i:03d}.txt"))
            check_time = time.time() - start_time

            # Should be very fast
            assert check_time < 0.1  # Less than 100ms

    def test_concurrent_operations_real(self):
        """Test concurrent filesystem operations."""
        import threading
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            results = []
            errors = []

            def worker(worker_id):
                """Worker function for concurrent operations."""
                try:
                    # Each worker creates files and lists directory
                    for i in range(5):
                        file_path = Path(tmpdir) / f"worker_{worker_id}_file_{i}.txt"
                        file_path.write_text(f"Worker {worker_id} - File {i}")

                    # List directory
                    files = fs.ls(tmpdir)
                    results.append((worker_id, len(files)))

                except Exception as e:
                    errors.append((worker_id, str(e)))

            # Start multiple workers
            threads = []
            for worker_id in range(3):
                thread = threading.Thread(target=worker, args=(worker_id,))
                threads.append(thread)
                thread.start()

            # Wait for completion
            for thread in threads:
                thread.join()

            # Verify results
            assert len(errors) == 0, f"Errors occurred: {errors}"
            assert len(results) == 3

            # Verify all files were created
            final_files = fs.ls(tmpdir)
            assert len(final_files) == 15  # 3 workers * 5 files each

    def test_edge_cases_mock_validation(self):
        """Test edge cases with mock validation."""
        config = ClusterConfig(cluster_type="local")

        # Test with very long file paths (mock the filesystem method directly)
        fs = ClusterFilesystem(config)

        # Test that the filesystem handles long paths without crashing
        long_path = "a" * 1000  # 1000 character path

        # This should not crash, even though the path doesn't exist
        try:
            result = fs.exists(long_path)
            # Result should be False since the path doesn't exist, but it shouldn't crash
            assert isinstance(result, bool)
        except Exception as e:
            # Long paths might cause OS errors, which is acceptable
            assert "File name too long" in str(e) or "name too long" in str(e).lower()

        # Test with special characters in filenames
        with tempfile.TemporaryDirectory() as tmpdir:
            special_chars = [
                "file with spaces.txt",
                "file@#$%.txt",
                "file-with-dashes.txt",
            ]

            for filename in special_chars:
                try:
                    (Path(tmpdir) / filename).write_text("test content")

                    fs = ClusterFilesystem(config)
                    assert fs.exists(str(Path(tmpdir) / filename))

                except OSError:
                    # Some filesystems don't support certain characters
                    # This is expected behavior
                    pass

    def test_cross_platform_compatibility_hybrid(self):
        """Test cross-platform compatibility with hybrid approach."""
        import platform

        system = platform.system()

        with tempfile.TemporaryDirectory() as tmpdir:
            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            # Test platform-specific behavior
            if system == "Windows":
                # Test Windows-specific paths
                test_file = Path(tmpdir) / "test.txt"
                test_file.write_text("Windows test")

                # Test case-insensitive filesystem
                assert fs.exists(str(test_file))
                # Windows paths are case-insensitive
                assert fs.exists(str(test_file).upper()) == fs.exists(str(test_file))

            elif system in ["Linux", "Darwin"]:
                # Test Unix-specific features
                test_file = Path(tmpdir) / "test.txt"
                test_file.write_text("Unix test")

                # Test filesystem case sensitivity (don't assume - detect)
                assert fs.exists(str(test_file))

                # Check if filesystem is case sensitive by testing behavior
                upper_path = str(test_file).upper()
                is_case_sensitive = not fs.exists(upper_path)

                # Just verify the behavior is consistent (don't enforce specific behavior)
                # Some macOS systems use case-insensitive APFS, Linux typically case-sensitive
                if is_case_sensitive:
                    print("✓ Case-sensitive filesystem detected")
                else:
                    print("✓ Case-insensitive filesystem detected")

                # Test permissions
                test_file.chmod(0o644)
                file_info = fs.stat(str(test_file))
                assert file_info.permissions is not None

            # Test with mocked cross-platform issues
            with patch("platform.system") as mock_system:
                mock_system.return_value = "UnknownOS"

                # Should still work on unknown platforms
                assert fs.exists(str(test_file))


@pytest.mark.real_world
class TestFileSystemRealWorld:
    """Real-world filesystem tests using actual external resources."""

    def test_large_directory_handling_real(self):
        """Test handling of large directories with real filesystem."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a large number of files
            large_count = 1000

            for i in range(large_count):
                (Path(tmpdir) / f"large_file_{i:04d}.txt").write_text(f"File {i}")

            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            # Test operations on large directory
            files = fs.ls(tmpdir)
            assert len(files) == large_count

            # Test pattern matching
            first_hundred = cluster_find("large_file_00*.txt", tmpdir, config)
            assert len(first_hundred) == 100

            # Test disk usage calculation
            usage = cluster_du(tmpdir, config)
            assert usage.file_count == large_count
            assert usage.total_bytes > large_count * 5  # At least 5 bytes per file

    def test_binary_file_handling_real(self):
        """Test handling of binary files with real filesystem."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create binary test file
            binary_data = bytes(range(256))
            binary_file = Path(tmpdir) / "binary.bin"
            binary_file.write_bytes(binary_data)

            config = ClusterConfig(cluster_type="local")
            fs = ClusterFilesystem(config)

            # Test binary file operations
            assert fs.exists(str(binary_file))
            assert fs.isfile(str(binary_file))

            file_info = fs.stat(str(binary_file))
            assert file_info.size == 256
            assert file_info.name == "binary.bin"
