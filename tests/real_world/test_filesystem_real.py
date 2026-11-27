"""
Real-world filesystem tests for Clustrix.

ClusterFilesystem and the cluster_* helpers against real files and
directories on this machine. The same helpers against a real sshd are in
test_paths_with_a_tilde_real.py.
"""

import pytest

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
)
from clustrix.config import ClusterConfig
from tests.real_world import TempResourceManager


@pytest.mark.real_world
class TestRealFilesystemOperations:
    """Test filesystem operations with real files and directories."""

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
