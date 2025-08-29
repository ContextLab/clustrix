"""
Real-world filesystem tests for Clustrix.

These tests use actual filesystem operations to verify that our
file handling code works correctly with real files and directories.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path
import pytest
import yaml

from clustrix.filesystem import (
    ClusterFilesystem,
    FileInfo,
    DiskUsage,
    cluster_ls,
    cluster_find,
    cluster_stat,
    cluster_exists,
    cluster_isdir,
    cluster_isfile,
    cluster_glob,
    cluster_du,
    cluster_count_files,
)
from clustrix.config import ClusterConfig
from tests.real_world import TempResourceManager, test_manager


@pytest.mark.real_world
class TestRealFilesystemOperations:
    """Test filesystem operations with real files and directories."""

    def test_create_and_read_file_real(self):
        """Test creating and reading a real file."""
        with TempResourceManager() as temp_mgr:
            # Create a test file with content
            test_content = f"Test content {uuid.uuid4()}"
            temp_file = temp_mgr.create_temp_file(test_content, ".txt")

            # Verify file exists
            assert temp_file.exists()

            # Read content back
            with open(temp_file, "r") as f:
                read_content = f.read()

            assert read_content == test_content

    def test_json_file_operations_real(self):
        """Test JSON file operations with real files."""
        with TempResourceManager() as temp_mgr:
            # Create test data
            test_data = {
                "name": "test_config",
                "version": "1.0",
                "settings": {"debug": True, "timeout": 30},
                "items": [1, 2, 3, 4, 5],
            }

            # Write JSON file
            temp_file = temp_mgr.create_temp_file(suffix=".json")
            with open(temp_file, "w") as f:
                json.dump(test_data, f, indent=2)

            # Read JSON file
            with open(temp_file, "r") as f:
                loaded_data = json.load(f)

            assert loaded_data == test_data

    def test_yaml_file_operations_real(self):
        """Test YAML file operations with real files."""
        with TempResourceManager() as temp_mgr:
            # Create test data
            test_data = {
                "cluster_type": "slurm",
                "cluster_host": "hpc.example.edu",
                "default_cores": 4,
                "default_memory": "8GB",
                "modules": ["python/3.9", "gcc/9.0"],
                "env_vars": {"SCRATCH": "/scratch/user", "TMPDIR": "/tmp"},
            }

            # Write YAML file
            temp_file = temp_mgr.create_temp_file(suffix=".yml")
            with open(temp_file, "w") as f:
                yaml.dump(test_data, f, default_flow_style=False)

            # Read YAML file
            with open(temp_file, "r") as f:
                loaded_data = yaml.safe_load(f)

            assert loaded_data == test_data

    def test_directory_operations_real(self):
        """Test directory operations with real directories."""
        with TempResourceManager() as temp_mgr:
            # Create test directory structure
            base_dir = temp_mgr.create_temp_dir()
            sub_dir1 = base_dir / "subdir1"
            sub_dir2 = base_dir / "subdir2"

            sub_dir1.mkdir()
            sub_dir2.mkdir()

            # Create test files
            file1 = sub_dir1 / "file1.txt"
            file2 = sub_dir1 / "file2.py"
            file3 = sub_dir2 / "file3.json"

            file1.write_text("Content 1")
            file2.write_text("print('Hello')")
            file3.write_text('{"key": "value"}')

            # Test directory listing
            contents = list(base_dir.iterdir())
            assert len(contents) == 2
            assert sub_dir1 in contents
            assert sub_dir2 in contents

            # Test file discovery
            all_files = list(base_dir.rglob("*"))
            txt_files = list(base_dir.rglob("*.txt"))
            py_files = list(base_dir.rglob("*.py"))

            assert len(all_files) >= 5  # 2 dirs + 3 files
            assert len(txt_files) == 1
            assert len(py_files) == 1

    def test_cluster_filesystem_local_real(self):
        """Test ClusterFilesystem with real local operations."""
        with TempResourceManager() as temp_mgr:
            # Create local cluster config
            config = ClusterConfig(cluster_type="local")
            filesystem = ClusterFilesystem(config)

            # Create test structure
            base_dir = temp_mgr.create_temp_dir()
            test_file = base_dir / "test.txt"
            test_file.write_text("Test content for cluster filesystem")

            # Test file operations
            assert filesystem.exists(str(test_file))
            assert filesystem.isfile(str(test_file))
            assert filesystem.isdir(str(base_dir))

            # Test file info
            file_info = filesystem.stat(str(test_file))
            assert file_info.size > 0
            assert not file_info.is_dir

    def test_cluster_utility_functions_real(self):
        """Test cluster utility functions with real files."""
        with TempResourceManager() as temp_mgr:
            # Create local cluster config
            config = ClusterConfig(cluster_type="local")

            # Create test structure
            base_dir = temp_mgr.create_temp_dir()

            # Create various test files
            files_to_create = [
                ("data.csv", "col1,col2,col3\n1,2,3\n4,5,6"),
                ("script.py", "#!/usr/bin/env python\nprint('hello')"),
                ("config.yml", "key: value\nlist: [1, 2, 3]"),
                ("README.md", "# Test Project\n\nThis is a test."),
            ]

            for filename, content in files_to_create:
                file_path = base_dir / filename
                file_path.write_text(content)

            # Test cluster_ls
            files = cluster_ls(str(base_dir), config)
            assert len(files) == 4
            assert "data.csv" in files
            assert "script.py" in files

            # Test cluster_find
            csv_files = cluster_find("*.csv", str(base_dir), config)
            assert len(csv_files) == 1
            assert "data.csv" in csv_files[0]  # Should contain the filename

            py_files = cluster_find("*.py", str(base_dir), config)
            assert len(py_files) == 1
            assert "script.py" in py_files[0]  # Should contain the filename

            # Test cluster_exists
            assert cluster_exists(str(base_dir / "data.csv"), config)
            assert not cluster_exists(str(base_dir / "nonexistent.txt"), config)

            # Test cluster_isfile and cluster_isdir
            assert cluster_isfile(str(base_dir / "data.csv"), config)
            assert cluster_isdir(str(base_dir), config)
            assert not cluster_isdir(str(base_dir / "data.csv"), config)

            # Test cluster_stat
            file_info = cluster_stat(str(base_dir / "data.csv"), config)
            assert file_info.size > 0
            assert not file_info.is_dir

            # Test cluster_glob
            all_files = cluster_glob("*", str(base_dir), config)
            assert len(all_files) == 4

            csv_files_glob = cluster_glob("*.csv", str(base_dir), config)
            py_files_glob = cluster_glob("*.py", str(base_dir), config)
            md_files_glob = cluster_glob("*.md", str(base_dir), config)
            assert len(csv_files_glob) == 1  # csv file
            assert len(py_files_glob) == 1  # py file
            assert len(md_files_glob) == 1  # md file

            # Test cluster_du
            dir_usage = cluster_du(str(base_dir), config)
            assert dir_usage.total_bytes > 0
            assert dir_usage.file_count == 4

            # Test cluster_count_files
            file_count = cluster_count_files(str(base_dir), config=config)
            assert file_count == 4

    def test_file_permissions_real(self):
        """Test file permissions with real files."""
        with TempResourceManager() as temp_mgr:
            # Create test file
            test_file = temp_mgr.create_temp_file("test content", ".txt")

            # Test initial permissions
            initial_mode = test_file.stat().st_mode
            assert initial_mode is not None

            # Change permissions
            test_file.chmod(0o644)

            # Verify permissions changed
            new_mode = test_file.stat().st_mode
            assert new_mode != initial_mode

    def test_large_file_operations_real(self):
        """Test operations with larger files."""
        with TempResourceManager() as temp_mgr:
            # Create a larger test file (1MB)
            large_content = "x" * (1024 * 1024)  # 1MB of 'x' characters
            temp_file = temp_mgr.create_temp_file(large_content, ".txt")

            # Verify file size
            file_size = temp_file.stat().st_size
            assert file_size >= 1024 * 1024

            # Test reading large file
            with open(temp_file, "r") as f:
                read_content = f.read()

            assert len(read_content) == len(large_content)
            assert read_content == large_content

    def test_binary_file_operations_real(self):
        """Test binary file operations."""
        with TempResourceManager() as temp_mgr:
            # Create binary test data
            binary_data = bytes(range(256))  # 0-255 byte values

            # Write binary file
            temp_file = temp_mgr.create_temp_file(suffix=".bin")
            with open(temp_file, "wb") as f:
                f.write(binary_data)

            # Read binary file
            with open(temp_file, "rb") as f:
                read_data = f.read()

            assert read_data == binary_data

    def test_concurrent_file_operations_real(self):
        """Test concurrent file operations."""
        import threading
        import time

        with TempResourceManager() as temp_mgr:
            base_dir = temp_mgr.create_temp_dir()
            results = []

            def write_file(file_id):
                """Write a file with specific content."""
                file_path = base_dir / f"concurrent_{file_id}.txt"
                content = f"Content from thread {file_id}"
                file_path.write_text(content)
                results.append(file_id)

            # Create multiple threads to write files concurrently
            threads = []
            for i in range(5):
                thread = threading.Thread(target=write_file, args=(i,))
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

            # Verify all files were created
            assert len(results) == 5
            created_files = list(base_dir.glob("concurrent_*.txt"))
            assert len(created_files) == 5

            # Verify content of each file
            for i in range(5):
                file_path = base_dir / f"concurrent_{i}.txt"
                content = file_path.read_text()
                assert content == f"Content from thread {i}"

    @pytest.mark.skipif(os.name == "nt", reason="Unix-specific test")
    def test_unix_file_operations_real(self):
        """Test Unix-specific file operations."""
        with TempResourceManager() as temp_mgr:
            # Create test file
            test_file = temp_mgr.create_temp_file("#!/bin/bash\necho 'test'", ".sh")

            # Make file executable
            test_file.chmod(0o755)

            # Check if file is executable
            stat_info = test_file.stat()
            assert stat_info.st_mode & 0o111  # Check execute bits

    def test_error_handling_real(self):
        """Test error handling with real filesystem errors."""
        with TempResourceManager() as temp_mgr:
            # Test reading non-existent file
            non_existent = temp_mgr.create_temp_dir() / "does_not_exist.txt"

            with pytest.raises(FileNotFoundError):
                with open(non_existent, "r") as f:
                    f.read()

            # Test writing to read-only directory (Unix only)
            if os.name != "nt":
                ro_dir = temp_mgr.create_temp_dir()
                ro_dir.chmod(0o444)  # Read-only

                with pytest.raises(PermissionError):
                    forbidden_file = ro_dir / "forbidden.txt"
                    forbidden_file.write_text("This should fail")

                # Restore permissions for cleanup
                ro_dir.chmod(0o755)


# Test comment in real_world
