"""
Integration tests for the complete packaging system.

These tests verify that the filesystem utilities, dependency analysis, and file packaging
components work together seamlessly.
"""

import os
import sys
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from clustrix.filesystem import (
    ClusterFilesystem,
    cluster_ls,
    cluster_find,
    cluster_stat,
)
from clustrix.dependency_analysis import analyze_function_dependencies
from clustrix.file_packaging import package_function_for_execution
from clustrix.config import ClusterConfig


class TestPackagingIntegration:
    """Test integration between all packaging components."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_end_to_end_filesystem_packaging(self):
        """Test complete end-to-end packaging with filesystem operations."""

        # Create test data files
        data_dir = Path(self.temp_dir) / "data"
        data_dir.mkdir()

        test_files = ["file1.txt", "file2.csv", "file3.json"]
        for filename in test_files:
            (data_dir / filename).write_text(f"Content of {filename}")

        def data_processing_function():
            """A realistic data processing function."""
            from clustrix import cluster_ls, cluster_find, cluster_stat

            # List all files
            all_files = cluster_ls("data/")

            # Find specific file types
            csv_files = cluster_find("*.csv", "data/")
            json_files = cluster_find("*.json", "data/")

            results = {
                "total_files": len(all_files),
                "csv_files": len(csv_files),
                "json_files": len(json_files),
                "file_sizes": {},
            }

            # Get file sizes
            for filename in all_files:
                file_path = f"data/{filename}"
                file_info = cluster_stat(file_path)
                results["file_sizes"][filename] = file_info.size

            return results

        # Test with local config
        local_config = ClusterConfig(cluster_type="local", local_work_dir=self.temp_dir)

        # Package the function
        package_info = package_function_for_execution(
            func=data_processing_function,
            cluster_config=local_config,
            func_args=(),
            func_kwargs={},
        )

        # Verify package was created
        assert os.path.exists(package_info.package_path)
        assert package_info.function_name == "data_processing_function"

        # Verify package contents
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()

            # Should have core files
            assert "metadata.json" in files
            assert "cluster_config.json" in files
            assert "execute.py" in files

            # Should include filesystem utilities
            assert any("filesystem" in f for f in files)

            # Check metadata
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            assert metadata["dependencies"]["requires_cluster_filesystem"]
            assert len(metadata["dependencies"]["filesystem_calls"]) >= 3

            # Verify specific filesystem calls were detected
            fs_functions = [
                call["function"]
                for call in metadata["dependencies"]["filesystem_calls"]
            ]
            assert "cluster_ls" in fs_functions
            assert "cluster_find" in fs_functions
            assert "cluster_stat" in fs_functions

    def test_packaging_with_local_dependencies(self):
        """Test packaging functions with local dependencies."""

        def helper_function(data):
            """Helper function for processing."""
            return [item.upper() for item in data]

        def process_data(data):
            """Process data using helper function."""
            return helper_function(data)

        def main_function():
            """Main function that uses filesystem and local dependencies."""
            from clustrix import cluster_ls

            files = cluster_ls(".")
            processed = process_data(files)
            return processed

        # Add dependencies to global scope
        main_function.__globals__["helper_function"] = helper_function
        main_function.__globals__["process_data"] = process_data

        # Analyze dependencies first
        deps = analyze_function_dependencies(main_function)

        # Should detect filesystem calls
        assert deps.requires_cluster_filesystem
        assert len(deps.filesystem_calls) == 1

        # Should detect local function calls
        assert len(deps.local_function_calls) >= 1
        local_function_names = [
            call.function_name for call in deps.local_function_calls
        ]
        assert "process_data" in local_function_names

        # Package the function
        config = ClusterConfig(cluster_type="local", local_work_dir=self.temp_dir)
        package_info = package_function_for_execution(
            func=main_function, cluster_config=config, func_args=(), func_kwargs={}
        )

        # Verify package includes dependencies
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            assert metadata["dependencies"]["requires_cluster_filesystem"]
            assert len(metadata["dependencies"]["local_functions"]) >= 1

    def test_packaging_with_external_imports(self):
        """Test packaging functions with external package imports."""

        def analysis_function():
            """Function that imports external packages."""
            import json
            import os
            from pathlib import Path
            from clustrix import cluster_find

            # Find JSON config files
            config_files = cluster_find("*.json", "config/")

            results = {}
            for config_file in config_files:
                full_path = Path("config") / config_file
                if full_path.exists():
                    with open(full_path, "r") as f:
                        config_data = json.load(f)
                    results[config_file] = config_data

            return results

        # Analyze dependencies
        deps = analyze_function_dependencies(analysis_function)

        # Should detect standard library imports
        import_modules = [imp.module for imp in deps.imports]
        assert "json" in import_modules
        assert "os" in import_modules
        assert "pathlib" in import_modules
        assert "clustrix" in import_modules

        # Should detect filesystem operations
        assert deps.requires_cluster_filesystem

        # Package the function
        config = ClusterConfig(cluster_type="local")
        package_info = package_function_for_execution(
            func=analysis_function, cluster_config=config, func_args=(), func_kwargs={}
        )

        # Verify packaging succeeded
        assert os.path.exists(package_info.package_path)

        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            # Should include all imports
            imported_modules = [
                imp["module"] for imp in metadata["dependencies"]["imports"]
            ]
            assert "json" in imported_modules
            assert "pathlib" in imported_modules

    def test_cross_platform_packaging(self):
        """Test packaging works across different platform configurations."""

        def cross_platform_function():
            """Function that works on different platforms."""
            import os
            import platform
            from clustrix import cluster_ls, cluster_stat

            # Get platform info
            system_info = {
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "cwd": os.getcwd(),
            }

            # List current directory
            files = cluster_ls(".")

            # Get info about first file (if any)
            if files:
                first_file_info = cluster_stat(files[0])
                system_info["first_file_size"] = first_file_info.size

            return system_info

        configs = [
            ClusterConfig(cluster_type="local"),
            ClusterConfig(cluster_type="slurm", cluster_host="test.cluster.edu"),
            ClusterConfig(cluster_type="ssh", cluster_host="remote.server.com"),
        ]

        for config in configs:
            package_info = package_function_for_execution(
                func=cross_platform_function,
                cluster_config=config,
                func_args=(),
                func_kwargs={},
            )

            # Verify package was created for each config
            assert os.path.exists(package_info.package_path)

            with zipfile.ZipFile(package_info.package_path, "r") as zf:
                # Verify cluster config was preserved
                config_content = zf.read("cluster_config.json").decode()
                config_data = json.loads(config_content)
                assert config_data["cluster_type"] == config.cluster_type


class TestSharedFilesystemIntegration:
    """Test integration with shared filesystem detection."""

    def test_automatic_cluster_detection_in_packaging(self):
        """Test that cluster detection works in packaging context."""

        def shared_fs_function():
            """Function that should work on shared filesystem."""
            from clustrix import cluster_ls, cluster_exists

            # Check if we're on a shared filesystem
            files = cluster_ls(".")

            # Look for a specific file that might exist
            config_exists = cluster_exists("config.yml")

            return {"files_found": len(files), "has_config": config_exists}

        # Test with cluster config that should trigger auto-detection
        cluster_config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="ndoli.dartmouth.edu",
            username="testuser",
        )

        package_info = package_function_for_execution(
            func=shared_fs_function,
            cluster_config=cluster_config,
            func_args=(),
            func_kwargs={},
        )

        # Verify packaging works
        assert os.path.exists(package_info.package_path)

        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            # Check that execution script includes cluster detection logic
            execute_content = zf.read("execute.py").decode()
            assert "setup_cluster_filesystem" in execute_content

    @patch("socket.gethostname")
    def test_packaging_with_mocked_cluster_detection(self, mock_hostname):
        """Test packaging with mocked cluster detection."""

        # Mock hostname to simulate being on cluster
        mock_hostname.return_value = "s17.hpcc.dartmouth.edu"

        def cluster_aware_function():
            """Function that benefits from cluster detection."""
            from clustrix import cluster_ls, cluster_count_files

            # Get directory listing
            files = cluster_ls(".")
            file_count = cluster_count_files(".", "*")

            return {"total_files": file_count, "listed_files": len(files)}

        config = ClusterConfig(cluster_type="slurm", cluster_host="ndoli.dartmouth.edu")

        package_info = package_function_for_execution(
            func=cluster_aware_function,
            cluster_config=config,
            func_args=(),
            func_kwargs={},
        )

        # Verify packaging succeeded
        assert os.path.exists(package_info.package_path)


class TestPackagingErrorHandling:
    """Test error handling in the integrated packaging system."""

    def test_packaging_invalid_filesystem_operations(self):
        """Test packaging functions with invalid filesystem operations."""

        def invalid_fs_function():
            """Function with potentially problematic filesystem calls."""
            from clustrix import cluster_stat

            # This might fail if file doesn't exist
            file_info = cluster_stat("nonexistent_file.txt")
            return file_info.size

        config = ClusterConfig(cluster_type="local")

        # Packaging should succeed even if the function might fail at runtime
        package_info = package_function_for_execution(
            func=invalid_fs_function,
            cluster_config=config,
            func_args=(),
            func_kwargs={},
        )

        assert os.path.exists(package_info.package_path)

    def test_packaging_with_missing_dependencies(self):
        """Test packaging functions that reference undefined dependencies."""

        def function_with_missing_deps():
            """Function that calls undefined functions."""
            from clustrix import cluster_ls

            files = cluster_ls(".")
            # This function doesn't exist in scope  # noqa: F821
            return undefined_function(files)  # noqa: F821

        config = ClusterConfig(cluster_type="local")

        # Should still package successfully (runtime errors handled separately)
        package_info = package_function_for_execution(
            func=function_with_missing_deps,
            cluster_config=config,
            func_args=(),
            func_kwargs={},
        )

        assert os.path.exists(package_info.package_path)


class TestPackagingPerformance:
    """Test performance characteristics of the packaging system."""

    def test_packaging_large_function(self):
        """Test packaging a function with many dependencies."""

        def large_function():
            """Function with many imports and operations."""
            import os
            import sys
            import json
            import pathlib
            import platform
            import tempfile
            import collections
            import itertools
            from clustrix import (
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

            # Use filesystem operations
            files = cluster_ls(".")
            csv_files = cluster_find("*.csv", ".")
            py_files = cluster_glob("*.py", ".")

            results = {}
            for filename in files[:5]:  # Limit to first 5
                if cluster_exists(filename):
                    if cluster_isfile(filename):
                        file_info = cluster_stat(filename)
                        results[filename] = {"size": file_info.size, "is_file": True}
                    elif cluster_isdir(filename):
                        dir_count = cluster_count_files(filename, "*")
                        results[filename] = {"file_count": dir_count, "is_dir": True}

            return {
                "total_files": len(files),
                "csv_files": len(csv_files),
                "py_files": len(py_files),
                "file_details": results,
                "platform": platform.system(),
            }

        config = ClusterConfig(cluster_type="local")

        # Should handle large functions efficiently
        package_info = package_function_for_execution(
            func=large_function, cluster_config=config, func_args=(), func_kwargs={}
        )

        assert os.path.exists(package_info.package_path)

        # Check that all filesystem calls were detected
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            fs_calls = metadata["dependencies"]["filesystem_calls"]
            assert len(fs_calls) >= 5  # Should detect multiple filesystem calls

            # Package should be reasonably sized (< 1MB for this test)
            assert package_info.size_bytes < 1024 * 1024

    def test_package_reuse_and_caching(self):
        """Test that identical functions produce identical packages."""

        def identical_function():
            """Function for testing package ID consistency."""
            from clustrix import cluster_ls

            return len(cluster_ls("."))

        config = ClusterConfig(cluster_type="local")

        # Package the same function multiple times
        package1 = package_function_for_execution(
            func=identical_function, cluster_config=config, func_args=(), func_kwargs={}
        )

        package2 = package_function_for_execution(
            func=identical_function, cluster_config=config, func_args=(), func_kwargs={}
        )

        # Should produce the same package ID
        assert package1.package_id == package2.package_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
