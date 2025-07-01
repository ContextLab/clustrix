"""
Tests for the dependency analysis system.
"""

import ast
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, mock_open
import pytest

from clustrix.dependency_analysis import (
    DependencyAnalyzer,
    DependencyGraph,
    LoopAnalyzer,
    FilesystemCall,
    ImportInfo,
    LocalFunctionCall,
    FileReference,
    analyze_function_dependencies,
    analyze_function_loops,
)


class TestDependencyAnalyzer:
    """Test the DependencyAnalyzer class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.analyzer = DependencyAnalyzer()

    def test_analyze_simple_function(self):
        """Test analysis of a simple function."""

        def simple_func():
            return 42

        deps = self.analyzer.analyze_function(simple_func)

        assert deps.function_name == "simple_func"
        assert "return 42" in deps.source_code
        assert len(deps.imports) == 0
        assert len(deps.local_function_calls) == 0
        assert len(deps.file_references) == 0
        assert len(deps.filesystem_calls) == 0
        assert not deps.requires_cluster_filesystem

    def test_analyze_function_with_imports(self):
        """Test analysis of function with imports."""

        def func_with_imports():
            import os
            from pathlib import Path
            import numpy as np

            return os.getcwd()

        deps = self.analyzer.analyze_function(func_with_imports)

        assert len(deps.imports) == 3

        # Check import details
        import_modules = [imp.module for imp in deps.imports]
        assert "os" in import_modules
        assert "pathlib" in import_modules
        assert "numpy" in import_modules

        # Check import types
        os_import = next(imp for imp in deps.imports if imp.module == "os")
        assert not os_import.is_from_import

        pathlib_import = next(imp for imp in deps.imports if imp.module == "pathlib")
        assert pathlib_import.is_from_import
        assert "Path" in pathlib_import.names

    def test_analyze_function_with_filesystem_calls(self):
        """Test analysis of function with cluster filesystem calls."""

        def func_with_fs():
            from clustrix import cluster_ls, cluster_find

            files = cluster_ls(".")
            csv_files = cluster_find("*.csv", "data/")
            return files + csv_files

        deps = self.analyzer.analyze_function(func_with_fs)

        assert deps.requires_cluster_filesystem
        assert len(deps.filesystem_calls) == 2

        # Check filesystem call details
        fs_functions = [call.function for call in deps.filesystem_calls]
        assert "cluster_ls" in fs_functions
        assert "cluster_find" in fs_functions

        cluster_ls_call = next(
            call for call in deps.filesystem_calls if call.function == "cluster_ls"
        )
        assert cluster_ls_call.args == ["'.'"]

        cluster_find_call = next(
            call for call in deps.filesystem_calls if call.function == "cluster_find"
        )
        assert "'*.csv'" in cluster_find_call.args
        assert "'data/'" in cluster_find_call.args

    def test_analyze_function_with_file_references(self):
        """Test analysis of function with file references."""

        def func_with_files():
            with open("data.txt", "r") as f:
                content = f.read()

            # String literal that looks like a file path
            log_file = "logs/application.log"
            config_path = "/etc/myapp/config.json"

            return content

        deps = self.analyzer.analyze_function(func_with_files)

        assert len(deps.file_references) >= 2  # At least the ones we can detect

        # Check for file operation
        file_paths = [ref.path for ref in deps.file_references]
        assert "data.txt" in file_paths

        # Check file operations
        operations = [ref.operation for ref in deps.file_references]
        assert "open" in operations

    def test_analyze_function_with_local_calls(self):
        """Test analysis of function with local function calls."""

        def helper_function(x):
            return x * 2

        def main_function():
            return helper_function(21)

        # Add helper to main's globals for testing
        main_function.__globals__["helper_function"] = helper_function

        deps = self.analyzer.analyze_function(main_function)

        assert len(deps.local_function_calls) == 1
        local_call = deps.local_function_calls[0]
        assert local_call.function_name == "helper_function"
        assert local_call.defined_in_scope

    def test_analyze_invalid_function(self):
        """Test analysis of function that can't be analyzed."""
        # Create a built-in function that has no source
        with pytest.raises(ValueError, match="Cannot get source"):
            self.analyzer.analyze_function(len)

    def test_dependency_graph_methods(self):
        """Test DependencyGraph helper methods."""
        deps = DependencyGraph("test_func", "def test_func(): pass")

        # Test adding imports
        imports = [ImportInfo("os", ["os"], None, False, 1)]
        deps.add_imports(imports)
        assert len(deps.imports) == 1

        # Test adding filesystem calls
        fs_calls = [FilesystemCall("cluster_ls", ["'.'"], 2)]
        deps.add_filesystem_calls(fs_calls)
        assert deps.requires_cluster_filesystem
        assert len(deps.filesystem_calls) == 1

        # Test adding file references
        file_refs = [FileReference("data.txt", "open", 3, True)]
        deps.add_file_references(file_refs)
        assert len(deps.file_references) == 1
        assert "data.txt" in deps.data_files


class TestLoopAnalyzer:
    """Test the LoopAnalyzer class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.analyzer = LoopAnalyzer()

    def test_analyze_for_loop(self):
        """Test analysis of for loops."""
        source = """
def func_with_loops():
    for i in range(10):
        print(i)
    
    for item in items:
        process(item)
"""
        tree = ast.parse(source)
        loops = self.analyzer.analyze_loops(tree)

        assert len(loops) == 2

        # Check first loop
        loop1 = loops[0]
        assert loop1["type"] == "for"
        assert "i" in loop1["target"]
        assert "range(10)" in loop1["iter"]

        # Check second loop
        loop2 = loops[1]
        assert loop2["type"] == "for"
        assert "item" in loop2["target"]
        assert "items" in loop2["iter"]

    def test_analyze_while_loop(self):
        """Test analysis of while loops."""
        source = """
def func_with_while():
    while condition:
        do_something()
"""
        tree = ast.parse(source)
        loops = self.analyzer.analyze_loops(tree)

        assert len(loops) == 1
        loop = loops[0]
        assert loop["type"] == "while"
        assert not loop["is_parallelizable"]  # While loops not parallelizable

    def test_loop_parallelizability(self):
        """Test detection of non-parallelizable loops."""
        # Loop with break statement
        source_with_break = """
def func_with_break():
    for i in range(10):
        if i == 5:
            break
        print(i)
"""
        tree = ast.parse(source_with_break)
        loops = self.analyzer.analyze_loops(tree)

        assert len(loops) == 1
        assert not loops[0]["is_parallelizable"]  # Has break statement

        # Simple loop without breaks
        source_simple = """
def func_simple():
    for i in range(10):
        print(i)
"""
        tree = ast.parse(source_simple)
        loops = self.analyzer.analyze_loops(tree)

        assert len(loops) == 1
        assert loops[0]["is_parallelizable"]  # Simple loop


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_analyze_function_dependencies(self):
        """Test the convenience function for dependency analysis."""

        def test_func():
            import os

            return os.getcwd()

        deps = analyze_function_dependencies(test_func)

        assert isinstance(deps, DependencyGraph)
        assert deps.function_name == "test_func"
        assert len(deps.imports) == 1

    def test_analyze_function_loops(self):
        """Test the convenience function for loop analysis."""

        def test_func():
            for i in range(10):
                print(i)

        loops = analyze_function_loops(test_func)

        assert isinstance(loops, list)
        assert len(loops) == 1
        assert loops[0]["type"] == "for"


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_function_with_nested_functions(self):
        """Test function with nested function definitions."""

        def outer_func():
            def inner_func(x):
                return x + 1

            return inner_func(42)

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze_function(outer_func)

        # Should detect the call to inner_func
        assert deps.function_name == "outer_func"
        # Note: inner_func won't be in local_function_calls because it's defined
        # within the function scope, not in the global scope

    def test_function_with_complex_imports(self):
        """Test function with complex import patterns."""

        def complex_imports():
            import os.path
            from collections import defaultdict, Counter
            import numpy as np
            from pathlib import Path as P

            return defaultdict(int)

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze_function(complex_imports)

        assert len(deps.imports) == 4

        # Check specific imports
        import_modules = [imp.module for imp in deps.imports]
        assert "os.path" in import_modules
        assert "collections" in import_modules
        assert "numpy" in import_modules
        assert "pathlib" in import_modules

    def test_function_with_filesystem_method_calls(self):
        """Test function with filesystem operations via method calls."""

        def func_with_methods():
            from clustrix.filesystem import ClusterFilesystem
            from clustrix.config import ClusterConfig

            config = ClusterConfig()
            fs = ClusterFilesystem(config)
            return fs.ls(".")

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze_function(func_with_methods)

        # Should detect the import but not the method call as a filesystem call
        # (since we only detect direct function calls, not method calls)
        assert len(deps.imports) == 1
        # Method calls are harder to detect without more sophisticated analysis

    def test_empty_function(self):
        """Test analysis of empty function."""

        def empty_func():
            pass

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze_function(empty_func)

        assert deps.function_name == "empty_func"
        assert len(deps.imports) == 0
        assert len(deps.local_function_calls) == 0
        assert len(deps.file_references) == 0
        assert len(deps.filesystem_calls) == 0


class TestRealWorldScenarios:
    """Test with realistic function examples."""

    def test_data_processing_function(self):
        """Test analysis of a realistic data processing function."""

        def process_data():
            import pandas as pd
            from clustrix import cluster_find, cluster_stat

            # Find CSV files
            csv_files = cluster_find("*.csv", "data/")

            results = []
            for filename in csv_files:
                # Get file info
                file_info = cluster_stat(filename)

                # Read and process file
                if file_info.size > 1000000:  # > 1MB
                    df = pd.read_csv(filename, chunksize=10000)
                    processed = process_large_file(df)
                else:
                    df = pd.read_csv(filename)
                    processed = process_small_file(df)

                results.append(processed)

            return results

        # Add a mock local function for testing
        def process_large_file(df):
            return "large"

        def process_small_file(df):
            return "small"

        process_data.__globals__["process_large_file"] = process_large_file
        process_data.__globals__["process_small_file"] = process_small_file

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze_function(process_data)

        # Should detect pandas import
        import_modules = [imp.module for imp in deps.imports]
        assert "pandas" in import_modules
        assert "clustrix" in import_modules

        # Should detect filesystem calls
        assert deps.requires_cluster_filesystem
        fs_functions = [call.function for call in deps.filesystem_calls]
        assert "cluster_find" in fs_functions
        assert "cluster_stat" in fs_functions

        # Should detect local function calls
        local_functions = [call.function_name for call in deps.local_function_calls]
        assert "process_large_file" in local_functions
        assert "process_small_file" in local_functions
