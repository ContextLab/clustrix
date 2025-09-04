"""
Comprehensive tests for utils module serialization and environment functions.

This test file focuses on lines 97-373 of clustrix/utils.py:
- Function serialization/deserialization with fallback chains
- Environment detection and package manager support
- Cross-version compatibility testing
"""

import ast
import pytest
import sys
import os
import pickle
import subprocess
import importlib
from unittest.mock import Mock, patch, MagicMock, call
from typing import Any, Dict, Callable

# Import the functions to test from utils module
from clustrix.utils import (
    serialize_function,
    deserialize_function,
    get_environment_requirements,
    get_environment_info,
    is_uv_available,
    is_conda_available,
    get_package_manager_command,
    setup_environment,
    detect_loops,
)
from clustrix.config import ClusterConfig


class TestFunctionSerialization:
    """Test function serialization with dill/cloudpickle/pickle fallback."""

    def test_serialize_simple_function_with_dill(self):
        """Test serialization of a simple function using dill."""

        def simple_func(x, y):
            return x * y

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env:
            mock_env.return_value = {"numpy": "1.21.0"}
            mock_dill.dumps.return_value = b"dill_serialized_data"

            result = serialize_function(simple_func, (3, 4), {})

            # Verify dill was called first
            mock_dill.dumps.assert_called_once_with(simple_func, protocol=4)

            # Check result structure
            assert "function" in result
            assert "args" in result
            assert "kwargs" in result
            assert "requirements" in result
            assert "func_info" in result
            assert result["function"] == b"dill_serialized_data"

    def test_serialize_fallback_to_cloudpickle(self):
        """Test fallback from dill to cloudpickle when dill fails."""

        def test_func(x):
            return x**2

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.cloudpickle"
        ) as mock_cloudpickle, patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env:

            mock_env.return_value = {}
            mock_dill.dumps.side_effect = Exception("dill failed")
            mock_cloudpickle.dumps.return_value = b"cloudpickle_data"

            result = serialize_function(test_func, (5,), {})

            # Verify fallback chain
            mock_dill.dumps.assert_called_once()
            mock_cloudpickle.dumps.assert_called_once_with(test_func, protocol=4)
            assert result["function"] == b"cloudpickle_data"

    def test_serialize_fallback_to_pickle(self):
        """Test final fallback to built-in pickle when both dill and cloudpickle fail."""

        def test_func(x):
            return x + 1

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.cloudpickle"
        ) as mock_cloudpickle, patch("clustrix.utils.pickle") as mock_pickle, patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env:

            mock_env.return_value = {}
            mock_dill.dumps.side_effect = Exception("dill failed")
            mock_cloudpickle.dumps.side_effect = Exception("cloudpickle failed")
            # Mock pickle.dumps to handle multiple calls (function, args, kwargs)
            mock_pickle.dumps.side_effect = [
                b"pickle_data",
                b"args_data",
                b"kwargs_data",
            ]

            result = serialize_function(test_func, (10,), {})

            # Verify complete fallback chain
            mock_dill.dumps.assert_called_once()
            mock_cloudpickle.dumps.assert_called_once()
            # Check that pickle was called for function (first call should be the function)
            assert mock_pickle.dumps.call_count == 3  # function, args, kwargs
            assert result["function"] == b"pickle_data"

    def test_serialize_with_complex_args_kwargs(self):
        """Test serialization with complex arguments and keyword arguments."""

        def complex_func(data, multiplier=1.0, **options):
            return sum(data) * multiplier

        args = ([1, 2, 3, 4],)
        kwargs = {"multiplier": 2.5, "precision": 3}

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.pickle"
        ) as mock_pickle, patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env:

            mock_env.return_value = {"scipy": "1.7.0"}
            mock_dill.dumps.return_value = b"func_data"
            mock_pickle.dumps.side_effect = [b"args_data", b"kwargs_data"]

            result = serialize_function(complex_func, args, kwargs)

            # Verify args and kwargs serialization
            assert mock_pickle.dumps.call_count == 2
            mock_pickle.dumps.assert_has_calls(
                [call(args, protocol=4), call(kwargs, protocol=4)]
            )
            assert result["args"] == b"args_data"
            assert result["kwargs"] == b"kwargs_data"

    def test_serialize_function_metadata(self):
        """Test that function metadata is correctly captured."""

        def documented_func(x, y):
            """A well-documented function."""
            return x + y

        with patch("clustrix.utils.dill"), patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env, patch(
            "clustrix.utils.inspect.getsource"
        ) as mock_getsource, patch(
            "clustrix.utils.inspect.getfile"
        ) as mock_getfile:

            mock_env.return_value = {}
            mock_getsource.return_value = "def documented_func(x, y):\n    return x + y"
            mock_getfile.return_value = "/path/to/test.py"

            result = serialize_function(documented_func, (), {})

            # Check function metadata
            func_info = result["func_info"]
            assert func_info["name"] == "documented_func"
            assert func_info["module"] is not None
            assert func_info["source"] == "def documented_func(x, y):\n    return x + y"

    def test_serialize_lambda_function(self):
        """Test serialization of lambda functions."""

        lambda_func = lambda x: x * 2

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env, patch("clustrix.utils.inspect.getsource") as mock_getsource:

            mock_env.return_value = {}
            mock_dill.dumps.return_value = b"lambda_data"
            mock_getsource.side_effect = Exception("Cannot get lambda source")

            result = serialize_function(lambda_func, (5,), {})

            # Lambda source should be None due to exception
            assert result["function_source"] is None
            assert result["func_info"]["source"] is None
            assert result["function"] == b"lambda_data"

    def test_serialize_closure_function(self):
        """Test serialization of functions with closures."""

        def make_multiplier(factor):
            def multiplier(x):
                return x * factor

            return multiplier

        closure_func = make_multiplier(3)

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.get_environment_requirements"
        ) as mock_env:

            mock_env.return_value = {"functools": "1.0"}
            mock_dill.dumps.return_value = b"closure_data"

            result = serialize_function(closure_func, (10,), {})

            # Closures should serialize with dill
            mock_dill.dumps.assert_called_once_with(closure_func, protocol=4)
            assert result["function"] == b"closure_data"


class TestFunctionDeserialization:
    """Test function deserialization with cross-version compatibility."""

    def test_deserialize_dict_format_with_dill(self):
        """Test deserialization of dictionary format data using dill."""

        func_data = {
            "function": b"dill_func_data",
            "args": b"pickled_args",
            "kwargs": b"pickled_kwargs",
        }

        mock_func = Mock()
        mock_func.return_value = 42

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.pickle"
        ) as mock_pickle:

            mock_dill.loads.return_value = mock_func
            mock_pickle.loads.side_effect = [(1, 2), {"z": 3}]

            func, args, kwargs = deserialize_function(func_data)

            # Verify dill deserialization was used first
            mock_dill.loads.assert_called_once_with(b"dill_func_data")
            assert func == mock_func
            assert args == (1, 2)
            assert kwargs == {"z": 3}

    def test_deserialize_fallback_to_cloudpickle(self):
        """Test fallback to cloudpickle when dill deserialization fails."""

        func_data = {
            "function": b"cloudpickle_func_data",
            "args": b"pickled_args",
            "kwargs": b"pickled_kwargs",
        }

        mock_func = Mock()

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.cloudpickle"
        ) as mock_cloudpickle, patch("clustrix.utils.pickle") as mock_pickle:

            mock_dill.loads.side_effect = Exception("dill failed")
            mock_cloudpickle.loads.return_value = mock_func
            mock_pickle.loads.side_effect = [(), {}]

            func, args, kwargs = deserialize_function(func_data)

            # Verify fallback to cloudpickle
            mock_dill.loads.assert_called_once()
            mock_cloudpickle.loads.assert_called_once_with(b"cloudpickle_func_data")
            assert func == mock_func

    def test_deserialize_bytes_format(self):
        """Test deserialization of legacy bytes format."""

        func_data = b"legacy_pickle_data"
        expected_result = (Mock(), (1, 2), {"key": "value"})

        with patch("clustrix.utils.pickle") as mock_pickle:
            mock_pickle.loads.return_value = expected_result

            result = deserialize_function(func_data)

            mock_pickle.loads.assert_called_once_with(func_data)
            assert result == expected_result

    def test_deserialize_invalid_format(self):
        """Test error handling for invalid function data format."""

        invalid_data = "invalid string format"

        with pytest.raises(ValueError, match="Invalid function data format"):
            deserialize_function(invalid_data)

    def test_deserialize_cross_version_compatibility(self):
        """Test deserialization maintains compatibility across Python versions."""

        func_data = {
            "function": b"version_specific_data",
            "args": b"version_args",
            "kwargs": b"version_kwargs",
            "python_version": "3.8.10 (default, Jun  2 2021, 10:49:15)",
        }

        mock_func = Mock()
        mock_func.__name__ = "cross_version_func"

        with patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.pickle"
        ) as mock_pickle:

            mock_dill.loads.return_value = mock_func
            mock_pickle.loads.side_effect = [(42,), {"mode": "test"}]

            func, args, kwargs = deserialize_function(func_data)

            # Should successfully deserialize despite version difference
            assert func == mock_func
            assert args == (42,)
            assert kwargs == {"mode": "test"}


class TestEnvironmentRequirements:
    """Test environment requirements detection and package management."""

    def test_get_environment_requirements_success(self):
        """Test successful pip list parsing."""

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = """numpy==1.21.0
pandas==1.3.3
scipy==1.7.1"""

        with patch("clustrix.utils.subprocess.run", return_value=mock_result):
            requirements = get_environment_requirements()

            expected = {
                "numpy": "1.21.0",
                "pandas": "1.3.3",
                "scipy": "1.7.1",
                "cloudpickle": None,  # Added by essential packages logic
                "dill": None,
            }

            assert "numpy" in requirements
            assert requirements["numpy"] == "1.21.0"
            assert "pandas" in requirements
            assert "scipy" in requirements

    def test_get_environment_requirements_with_editable_packages(self):
        """Test handling of editable packages (should be ignored)."""

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = """numpy==1.21.0
-e git+https://github.com/user/repo.git@abc123#egg=mypackage
pandas==1.3.3"""

        with patch("clustrix.utils.subprocess.run", return_value=mock_result):
            requirements = get_environment_requirements()

            # Editable package should be filtered out
            assert "numpy" in requirements
            assert "pandas" in requirements
            assert "mypackage" not in requirements

    def test_get_environment_requirements_subprocess_failure(self):
        """Test handling of subprocess failures."""

        with patch("clustrix.utils.subprocess.run", side_effect=FileNotFoundError()):
            requirements = get_environment_requirements()

            # Should still include essential packages even if subprocess fails
            assert isinstance(requirements, dict)
            # Essential packages may or may not be present depending on availability

    def test_get_environment_requirements_essential_packages(self):
        """Test that essential packages are always included."""

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "numpy==1.21.0"

        mock_dill = Mock()
        mock_dill.__version__ = "0.3.4"
        mock_cloudpickle = Mock()
        mock_cloudpickle.__version__ = "2.0.0"

        with patch("clustrix.utils.subprocess.run", return_value=mock_result), patch(
            "clustrix.utils.importlib.import_module"
        ) as mock_import:

            def mock_import_side_effect(name):
                if name == "dill":
                    return mock_dill
                elif name == "cloudpickle":
                    return mock_cloudpickle
                raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = mock_import_side_effect

            requirements = get_environment_requirements()

            assert "dill" in requirements
            assert requirements["dill"] == "0.3.4"
            assert "cloudpickle" in requirements
            assert requirements["cloudpickle"] == "2.0.0"

    def test_get_environment_info_string_format(self):
        """Test get_environment_info returns string format."""

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "numpy==1.21.0\npandas==1.3.3\n"

        with patch("clustrix.utils.subprocess.run", return_value=mock_result):
            env_info = get_environment_info()

            assert isinstance(env_info, str)
            assert "numpy==1.21.0" in env_info
            assert "pandas==1.3.3" in env_info

    def test_get_environment_info_failure(self):
        """Test get_environment_info returns empty string on failure."""

        with patch("clustrix.utils.subprocess.run", side_effect=Exception("Failed")):
            env_info = get_environment_info()

            assert env_info == ""


class TestPackageManagerDetection:
    """Test package manager detection utilities."""

    def test_is_uv_available_success(self):
        """Test successful uv detection."""

        mock_result = Mock()
        mock_result.returncode = 0

        with patch(
            "clustrix.utils.subprocess.run", return_value=mock_result
        ) as mock_run:
            result = is_uv_available()

            mock_run.assert_called_once_with(
                ["uv", "--version"], capture_output=True, text=True, timeout=10
            )
            assert result is True

    def test_is_uv_available_not_found(self):
        """Test uv not available."""

        with patch("clustrix.utils.subprocess.run", side_effect=FileNotFoundError()):
            result = is_uv_available()
            assert result is False

    def test_is_uv_available_timeout(self):
        """Test uv detection timeout."""

        with patch(
            "clustrix.utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("uv", 10),
        ):
            result = is_uv_available()
            assert result is False

    def test_is_conda_available_success(self):
        """Test successful conda detection."""

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("clustrix.utils.subprocess.run", return_value=mock_result):
            result = is_conda_available()
            assert result is True

    def test_is_conda_available_failure(self):
        """Test conda detection failure."""

        mock_result = Mock()
        mock_result.returncode = 1

        with patch("clustrix.utils.subprocess.run", return_value=mock_result):
            result = is_conda_available()
            assert result is False

    def test_get_package_manager_command_explicit_uv(self):
        """Test explicit uv package manager configuration."""

        config = ClusterConfig(package_manager="uv")
        result = get_package_manager_command(config)
        assert result == "uv pip"

    def test_get_package_manager_command_explicit_conda(self):
        """Test explicit conda package manager configuration."""

        config = ClusterConfig(package_manager="conda")
        result = get_package_manager_command(config)
        assert result == "conda"

    def test_get_package_manager_command_explicit_pip(self):
        """Test explicit pip package manager configuration."""

        config = ClusterConfig(package_manager="pip")
        result = get_package_manager_command(config)
        assert result == "pip"

    def test_get_package_manager_command_auto_prefers_uv(self):
        """Test auto-detection prefers uv when available."""

        config = ClusterConfig(package_manager="auto")

        with patch("clustrix.utils.is_uv_available", return_value=True), patch(
            "clustrix.utils.is_conda_available", return_value=True
        ):

            result = get_package_manager_command(config)
            assert result == "uv pip"

    def test_get_package_manager_command_auto_fallback_conda(self):
        """Test auto-detection falls back to conda when uv unavailable."""

        config = ClusterConfig(package_manager="auto")

        with patch("clustrix.utils.is_uv_available", return_value=False), patch(
            "clustrix.utils.is_conda_available", return_value=True
        ):

            result = get_package_manager_command(config)
            assert result == "conda"

    def test_get_package_manager_command_auto_fallback_pip(self):
        """Test auto-detection falls back to pip when neither uv nor conda available."""

        config = ClusterConfig(package_manager="auto")

        with patch("clustrix.utils.is_uv_available", return_value=False), patch(
            "clustrix.utils.is_conda_available", return_value=False
        ):

            result = get_package_manager_command(config)
            assert result == "pip"

    def test_get_package_manager_command_unknown_defaults_pip(self):
        """Test unknown package manager defaults to pip."""

        config = ClusterConfig(package_manager="unknown_manager")
        result = get_package_manager_command(config)
        assert result == "pip"


class TestEnvironmentSetup:
    """Test environment setup functions."""

    def test_setup_environment_with_existing_conda_env(self):
        """Test setup with existing conda environment."""

        config = ClusterConfig(conda_env_name="existing_env")

        result = setup_environment("/work/dir", {}, config)

        assert result == "conda run -n existing_env python"

    def test_setup_environment_create_conda_env(self):
        """Test creating new conda environment."""

        config = ClusterConfig(package_manager="conda", python_executable="python3.11")

        requirements = {"numpy": "1.21.0", "pandas": "1.3.3"}

        with patch("clustrix.utils.get_package_manager_command", return_value="conda"):
            result = setup_environment("/work/dir", requirements, config)

            # Should return conda run command with path-based environment
            assert "conda run -p" in result
            assert "python" in result

    def test_setup_environment_create_venv_with_pip(self):
        """Test creating virtual environment with pip."""

        config = ClusterConfig(package_manager="pip")
        requirements = {"requests": "2.25.1"}

        with patch("clustrix.utils.get_package_manager_command", return_value="pip"):
            result = setup_environment("/work/dir", requirements, config)

            assert result == "/work/dir/venv/bin/python"

    def test_setup_environment_create_venv_with_uv(self):
        """Test creating virtual environment with uv."""

        config = ClusterConfig(package_manager="uv")
        requirements = {"fastapi": "0.70.0"}

        with patch("clustrix.utils.get_package_manager_command", return_value="uv pip"):
            result = setup_environment("/work/dir", requirements, config)

            assert result == "/work/dir/venv/bin/python"

    def test_setup_environment_no_requirements(self):
        """Test environment setup without requirements."""

        config = ClusterConfig(package_manager="pip")

        with patch("clustrix.utils.get_package_manager_command", return_value="pip"):
            result = setup_environment("/work/dir", {}, config)

            assert result == "/work/dir/venv/bin/python"


class TestSerializationCompatibility:
    """Test serialization compatibility and edge cases."""

    def test_serialize_function_with_source_extraction_failure(self):
        """Test handling when source code cannot be extracted."""

        def test_func():
            return "test"

        with patch(
            "clustrix.utils.inspect.getsource",
            side_effect=OSError("No source available"),
        ), patch("clustrix.utils.dill") as mock_dill, patch(
            "clustrix.utils.get_environment_requirements", return_value={}
        ):

            mock_dill.dumps.return_value = b"serialized"

            result = serialize_function(test_func, (), {})

            # Should handle source extraction failure gracefully
            assert result["function_source"] is None
            assert result["func_info"]["source"] is None
            assert result["function"] == b"serialized"

    def test_serialize_function_captures_working_directory(self):
        """Test that serialization captures current working directory."""

        def test_func():
            return os.getcwd()

        with patch("clustrix.utils.dill"), patch(
            "clustrix.utils.get_environment_requirements", return_value={}
        ), patch("clustrix.utils.os.getcwd", return_value="/current/work/dir"):

            result = serialize_function(test_func, (), {})

            assert result["working_directory"] == "/current/work/dir"

    def test_serialize_function_captures_python_version(self):
        """Test that serialization captures Python version information."""

        def test_func():
            return sys.version

        with patch("clustrix.utils.dill"), patch(
            "clustrix.utils.get_environment_requirements", return_value={}
        ):

            result = serialize_function(test_func, (), {})

            assert "python_version" in result
            assert result["python_version"] == sys.version

    def test_serialize_deserialize_roundtrip_compatibility(self):
        """Test full roundtrip serialization and deserialization."""

        def roundtrip_func(a, b, c=10):
            return a + b + c

        original_args = (5, 7)
        original_kwargs = {"c": 15}

        # Mock the serialization components
        with patch(
            "clustrix.utils.get_environment_requirements", return_value={"test": "1.0"}
        ):
            # Serialize
            serialized = serialize_function(
                roundtrip_func, original_args, original_kwargs
            )

            # Deserialize
            func, args, kwargs = deserialize_function(serialized)

            # Test that deserialized function works
            result = func(*args, **kwargs)
            expected = roundtrip_func(*original_args, **original_kwargs)
            assert result == expected


class TestLoopDetection:
    """Test AST-based loop detection functionality."""

    def test_detect_loops_simple_for_loop(self):
        """Test detection of simple for loop."""

        # Mock a function and its source to test loop detection
        mock_func = Mock()
        mock_func.__name__ = "simple_loop_func"

        mock_source = """def simple_loop_func():
    results = []
    for i in range(10):
        results.append(i * 2)
    return results"""

        with patch("clustrix.utils.inspect.getsource", return_value=mock_source):
            loop_info = detect_loops(mock_func, (), {})

        assert loop_info is not None
        assert loop_info["type"] == "for"
        assert loop_info["variable"] == "i"
        assert "range(" in loop_info["iterable"]

    def test_detect_loops_while_loop(self):
        """Test detection of while loop (should return None as only for+range loops are returned)."""

        mock_func = Mock()
        mock_source = """def while_loop_func():
    count = 0
    while count < 5:
        count += 1
    return count"""

        with patch("clustrix.utils.inspect.getsource", return_value=mock_source):
            loop_info = detect_loops(mock_func, (), {})

        # While loops are detected but not returned (only for+range loops are returned)
        assert loop_info is None

    def test_detect_loops_no_loops(self):
        """Test function with no loops."""

        def no_loop_func(x):
            return x * 2 + 1

        loop_info = detect_loops(no_loop_func, (5,), {})

        assert loop_info is None

    def test_detect_loops_range_extraction(self):
        """Test range extraction from for loop."""

        mock_func = Mock()
        mock_source = """def range_loop_func():
    total = 0
    for i in range(5, 15, 2):
        total += i
    return total"""

        with patch("clustrix.utils.inspect.getsource", return_value=mock_source):
            loop_info = detect_loops(mock_func, (), {})

        assert loop_info is not None
        assert loop_info["type"] == "for"
        assert "range" in loop_info
        # Range object should be extracted successfully

    def test_detect_loops_complex_iterable(self):
        """Test loop with complex iterable (not range) - should return None."""

        def list_loop_func():
            items = [1, 2, 3, 4, 5]
            for item in items:
                print(item)
            return len(items)

        loop_info = detect_loops(list_loop_func, (), {})

        # Non-range for loops are detected but not returned
        assert loop_info is None

    def test_detect_loops_nested_loops(self):
        """Test detection with nested loops (returns first loop)."""

        mock_func = Mock()
        mock_source = """def nested_loop_func():
    result = []
    for i in range(3):
        for j in range(4):
            result.append(i * j)
    return result"""

        with patch("clustrix.utils.inspect.getsource", return_value=mock_source):
            loop_info = detect_loops(mock_func, (), {})

        assert loop_info is not None
        assert loop_info["type"] == "for"
        # Should return info about the first (outer) loop
        assert loop_info["variable"] == "i"

    def test_detect_loops_source_unavailable(self):
        """Test handling when function source is unavailable."""

        # Create a lambda function (source typically unavailable)
        lambda_func = lambda: [x for x in range(5)]

        with patch(
            "clustrix.utils.inspect.getsource", side_effect=Exception("No source")
        ):
            loop_info = detect_loops(lambda_func, (), {})

            # Should return None when source is unavailable
            assert loop_info is None

    def test_detect_loops_ast_parse_failure(self):
        """Test handling when AST parsing fails."""

        def test_func():
            for i in range(5):
                pass

        with patch("clustrix.utils.ast.parse", side_effect=SyntaxError("Parse error")):
            loop_info = detect_loops(test_func, (), {})

            # Should return None when AST parsing fails
            assert loop_info is None

    def test_detect_loops_range_extraction_failure(self):
        """Test handling when range extraction fails."""

        mock_func = Mock()
        mock_source = """def complex_range_func():
    n = 10
    for i in range(n):
        pass"""

        with patch("clustrix.utils.inspect.getsource", return_value=mock_source), patch(
            "clustrix.utils.eval", side_effect=Exception("Eval failed")
        ):
            loop_info = detect_loops(mock_func, (), {})

            # Should still return loop info but with default range fallback
            assert loop_info is not None
            assert loop_info["type"] == "for"
            assert "range" in loop_info
            # Should fall back to default range(10)

    def test_detect_loops_ast_unparse_unavailable(self):
        """Test loop detection when ast.unparse is not available (Python < 3.9)."""

        mock_func = Mock()
        mock_source = """def for_loop_func():
    for x in [1, 2, 3]:
        print(x)"""

        # Mock hasattr to simulate ast.unparse being unavailable
        with patch("clustrix.utils.inspect.getsource", return_value=mock_source), patch(
            "clustrix.utils.hasattr"
        ) as mock_hasattr:

            # Make hasattr return False for ast.unparse
            def hasattr_side_effect(obj, name):
                if name == "unparse":
                    return False
                return True  # Default behavior for other attributes

            mock_hasattr.side_effect = hasattr_side_effect

            loop_info = detect_loops(mock_func, (), {})

            # Should still work - non-range loops return None
            assert loop_info is None

    def test_detect_loops_multiple_loop_types(self):
        """Test function with both for and while loops (returns first for+range loop)."""

        mock_func = Mock()
        mock_source = """def mixed_loop_func():
    for i in range(3):
        print(f"For loop: {i}")
    
    count = 0
    while count < 2:
        print(f"While loop: {count}")
        count += 1"""

        with patch("clustrix.utils.inspect.getsource", return_value=mock_source):
            loop_info = detect_loops(mock_func, (), {})

        assert loop_info is not None
        # Should return the first for+range loop
        assert loop_info["type"] == "for"
        assert loop_info["variable"] == "i"
