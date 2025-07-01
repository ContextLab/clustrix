"""
Tests for the file packaging system.
"""

import os
import sys
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from clustrix.file_packaging import (
    FilePackager,
    PackageInfo,
    ExecutionContext,
    create_execution_context,
    package_function_for_execution,
)
from clustrix.config import ClusterConfig
from clustrix.dependency_analysis import DependencyGraph


class TestFilePackager:
    """Test the FilePackager class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.packager = FilePackager()
        self.temp_dir = tempfile.mkdtemp()

        # Create a test cluster config
        self.config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.cluster.edu",
            username="testuser",
            remote_work_dir="/scratch/test",
        )

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_package_simple_function(self):
        """Test packaging a simple function."""

        def simple_func():
            return 42

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(simple_func, context)

        assert isinstance(package_info, PackageInfo)
        assert package_info.function_name == "simple_func"
        assert os.path.exists(package_info.package_path)
        assert package_info.size_bytes > 0

        # Verify package contents
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()
            assert "metadata.json" in files
            assert "cluster_config.json" in files
            assert "execute.py" in files
            assert "environment.json" in files

    def test_package_function_with_dependencies(self):
        """Test packaging a function with dependencies."""

        # Create a helper function
        def helper_func(x):
            return x * 2

        def main_func():
            import os

            return helper_func(21) + len(os.getcwd())

        # Add helper to main's globals
        main_func.__globals__["helper_func"] = helper_func

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(main_func, context)

        # Verify package was created
        assert os.path.exists(package_info.package_path)

        # Check package contents
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()

            # Should have basic files
            assert "metadata.json" in files
            assert "execute.py" in files

            # Check metadata content
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            assert metadata["function_info"]["name"] == "main_func"
            assert len(metadata["dependencies"]["imports"]) > 0
            assert len(metadata["dependencies"]["local_functions"]) > 0

    def test_package_function_with_filesystem_calls(self):
        """Test packaging a function that uses cluster filesystem."""

        def fs_func():
            from clustrix import cluster_ls, cluster_find

            files = cluster_ls(".")
            csv_files = cluster_find("*.csv", "data/")
            return files + csv_files

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(fs_func, context)

        # Check that filesystem utilities were included
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()

            # Should include filesystem utilities
            assert any(
                "filesystem" in f for f in files
            ), f"Filesystem utilities not found in {files}"

            # Check metadata
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            assert metadata["dependencies"]["requires_cluster_filesystem"]
            assert len(metadata["dependencies"]["filesystem_calls"]) > 0

    def test_package_id_generation(self):
        """Test that package IDs are generated consistently."""

        def test_func():
            return 42

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        # Package the same function twice
        package1 = self.packager.package_function(test_func, context)
        package2 = self.packager.package_function(test_func, context)

        # Should have the same package ID
        assert package1.package_id == package2.package_id

    def test_package_with_data_files(self):
        """Test packaging a function that references data files."""
        # Create a temporary data file
        data_file = os.path.join(self.temp_dir, "test_data.txt")
        with open(data_file, "w") as f:
            f.write("test data")

        def func_with_data():
            with open("test_data.txt", "r") as f:
                return f.read()

        context = ExecutionContext(
            working_directory=self.temp_dir,
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(func_with_data, context)

        # Check that data file was included
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()
            assert any("test_data.txt" in f for f in files)

    def test_execution_script_generation(self):
        """Test that the execution script is generated correctly."""

        def test_func(x, y=10):
            return x + y

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(5,),
            function_kwargs={"y": 20},
        )

        package_info = self.packager.package_function(test_func, context)

        # Extract and examine the execution script
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            script_content = zf.read("execute.py").decode()

            # Should contain key elements
            assert "def setup_environment():" in script_content
            assert "def load_and_execute():" in script_content
            assert 'if __name__ == "__main__":' in script_content

            # Should handle filesystem setup if needed
            assert "setup_cluster_filesystem" in script_content


class TestExecutionContext:
    """Test ExecutionContext functionality."""

    def test_create_execution_context(self):
        """Test creation of execution context."""
        config = ClusterConfig(cluster_type="local")

        context = create_execution_context(
            cluster_config=config, func_args=(1, 2, 3), func_kwargs={"key": "value"}
        )

        assert isinstance(context, ExecutionContext)
        assert context.cluster_config == config
        assert context.function_args == (1, 2, 3)
        assert context.function_kwargs == {"key": "value"}
        assert context.working_directory == os.getcwd()
        assert context.python_version.count(".") == 2  # Format: x.y.z

    def test_execution_context_defaults(self):
        """Test execution context with default values."""
        config = ClusterConfig(cluster_type="local")

        context = create_execution_context(cluster_config=config)

        assert context.function_args == ()
        assert context.function_kwargs == {}


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_package_function_for_execution(self):
        """Test the convenience function for packaging."""

        def test_func():
            return "hello world"

        config = ClusterConfig(cluster_type="local")

        package_info = package_function_for_execution(
            func=test_func, cluster_config=config, func_args=(), func_kwargs={}
        )

        assert isinstance(package_info, PackageInfo)
        assert package_info.function_name == "test_func"
        assert os.path.exists(package_info.package_path)


class TestPackageContents:
    """Test the contents of created packages."""

    def setup_method(self):
        """Set up test fixtures."""
        self.packager = FilePackager()
        self.config = ClusterConfig(
            cluster_type="slurm", cluster_host="test.cluster.edu"
        )

    def test_metadata_content(self):
        """Test the content of metadata.json."""

        def test_func(x, y=5):
            import os

            return x + y

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={"TEST_VAR": "test_value"},
            cluster_config=self.config,
            function_args=(10,),
            function_kwargs={"y": 15},
        )

        package_info = self.packager.package_function(test_func, context)

        # Extract and verify metadata
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            # Check function info
            assert metadata["function_info"]["name"] == "test_func"
            assert "def test_func" in metadata["function_info"]["source"]

            # Check execution info
            assert metadata["execution_info"]["args"] == [10]
            assert metadata["execution_info"]["kwargs"] == {"y": 15}
            assert metadata["execution_info"]["python_version"] == "3.9.0"

            # Check dependencies
            assert "dependencies" in metadata
            assert "imports" in metadata["dependencies"]
            assert "local_functions" in metadata["dependencies"]

    def test_cluster_config_content(self):
        """Test the content of cluster_config.json."""

        def test_func():
            return 42

        config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="test.cluster.edu",
            username="testuser",
            remote_work_dir="/scratch/test",
        )

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(test_func, context)

        # Extract and verify cluster config
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            config_content = zf.read("cluster_config.json").decode()
            config_data = json.loads(config_content)

            assert config_data["cluster_type"] == "slurm"
            assert config_data["cluster_host"] == "test.cluster.edu"
            assert config_data["username"] == "testuser"
            assert config_data["remote_work_dir"] == "/scratch/test"

    def test_environment_content(self):
        """Test the content of environment.json."""

        def test_func():
            return 42

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={"PATH": "/usr/bin", "HOME": "/home/test"},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(test_func, context)

        # Extract and verify environment info
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            env_content = zf.read("environment.json").decode()
            env_data = json.loads(env_content)

            assert env_data["python_version"] == "3.9.0"
            assert env_data["platform"] == sys.platform
            assert "PATH" in env_data["environment_variables"]
            assert "HOME" in env_data["environment_variables"]


class TestErrorHandling:
    """Test error handling in packaging."""

    def setup_method(self):
        """Set up test fixtures."""
        self.packager = FilePackager()
        self.config = ClusterConfig(cluster_type="local")

    def test_package_builtin_function(self):
        """Test packaging a built-in function (should fail)."""
        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        # Should raise an error because we can't get source for built-in functions
        with pytest.raises(ValueError, match="Cannot get source"):
            self.packager.package_function(len, context)

    def test_package_lambda_function(self):
        """Test packaging a lambda function."""
        lambda_func = lambda x: x + 1

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(5,),
            function_kwargs={},
        )

        # Should work but might have limitations
        package_info = self.packager.package_function(lambda_func, context)
        assert package_info.function_name == "<lambda>"


class TestRealWorldScenarios:
    """Test with realistic packaging scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.packager = FilePackager()
        self.config = ClusterConfig(
            cluster_type="slurm", cluster_host="cluster.edu", username="researcher"
        )

    def test_package_data_processing_function(self):
        """Test packaging a realistic data processing function."""

        def process_dataset(data_dir="data", output_dir="results"):
            import pandas as pd
            from clustrix import cluster_find, cluster_stat

            # Find all CSV files
            csv_files = cluster_find("*.csv", data_dir)

            results = []
            for filename in csv_files:
                # Get file info
                file_info = cluster_stat(filename)

                # Process based on file size
                if file_info.size > 1000000:  # > 1MB
                    result = {
                        "file": filename,
                        "size": "large",
                        "bytes": file_info.size,
                    }
                else:
                    result = {
                        "file": filename,
                        "size": "small",
                        "bytes": file_info.size,
                    }

                results.append(result)

            return results

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={"data_dir": "input", "output_dir": "output"},
        )

        package_info = self.packager.package_function(process_dataset, context)

        # Verify the package was created successfully
        assert os.path.exists(package_info.package_path)
        assert package_info.metadata["has_filesystem_ops"]

        # Check that filesystem utilities are included
        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()
            assert any("filesystem" in f for f in files)

            # Check metadata for filesystem calls
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)

            fs_calls = metadata["dependencies"]["filesystem_calls"]
            fs_functions = [call["function"] for call in fs_calls]
            assert "cluster_find" in fs_functions
            assert "cluster_stat" in fs_functions

    def test_package_function_with_local_modules(self):
        """Test packaging a function that uses local modules."""

        # This would require actually creating local module files
        # For now, we'll test the structure
        def func_with_local_import():
            # This would normally import a local module
            # import my_local_module
            return "using local module"

        context = ExecutionContext(
            working_directory=os.getcwd(),
            python_version="3.9.0",
            environment_variables={},
            cluster_config=self.config,
            function_args=(),
            function_kwargs={},
        )

        package_info = self.packager.package_function(func_with_local_import, context)

        # Verify basic structure
        assert os.path.exists(package_info.package_path)

        with zipfile.ZipFile(package_info.package_path, "r") as zf:
            files = zf.namelist()
            assert "metadata.json" in files
            assert "execute.py" in files
