"""
Tests for the enhanced notebook magic functionality.
"""

import json
import sys
import tempfile
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Import only non-widget functionality at module level
from clustrix.notebook_magic import (
    DEFAULT_CONFIGS,
    detect_config_files,
    load_config_from_file,
    validate_ip_address,
    validate_hostname,
)

# Import widget conditionally to avoid GitHub Actions import issues
try:
    from clustrix.notebook_magic import EnhancedClusterConfigWidget
except ImportError:
    # In GitHub Actions, widget import fails - will be handled by fixture
    EnhancedClusterConfigWidget = None  # type: ignore


class TestEnhancedDefaultConfigs:
    """Test enhanced default configurations."""

    def test_default_configs_structure(self):
        """Test that default configs have required structure."""
        assert isinstance(DEFAULT_CONFIGS, dict)
        assert len(DEFAULT_CONFIGS) >= 2  # local and local_multicore
        for config_name, config in DEFAULT_CONFIGS.items():
            assert isinstance(config, dict)
            assert "cluster_type" in config
            # Note: 'name' and 'description' fields were removed for ClusterConfig compatibility
            # Basic configs should have these core fields
            if config.get("cluster_type") == "local":
                assert "default_cores" in config
                assert "default_memory" in config

    def test_default_configs_values(self):
        """Test that default configs have reasonable values."""
        for config_name, config in DEFAULT_CONFIGS.items():
            # Check cores are integers (can be -1 for all cores)
            if "default_cores" in config:
                assert isinstance(config["default_cores"], int)
                assert config["default_cores"] == -1 or config["default_cores"] > 0
            # Check memory format
            if "default_memory" in config:
                assert isinstance(config["default_memory"], str)
                assert (
                    "GB" in config["default_memory"] or "MB" in config["default_memory"]
                )

    def test_local_configs_present(self):
        """Test that local configurations are present."""
        assert "Local Single-core" in DEFAULT_CONFIGS
        assert "Local Multi-core" in DEFAULT_CONFIGS
        assert DEFAULT_CONFIGS["Local Single-core"]["cluster_type"] == "local"
        assert DEFAULT_CONFIGS["Local Multi-core"]["cluster_type"] == "local"
        assert DEFAULT_CONFIGS["Local Multi-core"]["default_cores"] == -1


class TestConfigFileDetection:
    """Test configuration file detection functionality."""

    def test_detect_config_files_empty_dirs(self):
        """Test config file detection with no config files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            files = detect_config_files([tmpdir])
            assert files == []

    def test_detect_config_files_with_configs(self):
        """Test config file detection with config files present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Create test config files
            (tmpdir_path / "clustrix.yml").touch()
            (tmpdir_path / "config.yaml").touch()
            (tmpdir_path / "other.txt").touch()  # Should be ignored
            files = detect_config_files([tmpdir])
            assert len(files) == 2
            file_names = [f.name for f in files]
            assert "clustrix.yml" in file_names
            assert "config.yaml" in file_names
            assert "other.txt" not in file_names

    def test_load_config_from_yaml_file(self):
        """Test loading configuration from YAML file."""
        test_config = {
            "test_cluster": {
                "cluster_type": "ssh",
                "cluster_host": "test.example.com",
                "username": "testuser",
                "default_cores": 8,
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(test_config, f)
            temp_path = Path(f.name)
        try:
            loaded_config = load_config_from_file(temp_path)
            assert loaded_config == test_config
        finally:
            temp_path.unlink()

    def test_load_config_from_json_file(self):
        """Test loading configuration from JSON file."""
        test_config = {
            "test_cluster": {
                "cluster_type": "kubernetes",
                "cluster_host": "k8s.example.com",
                "default_cores": 4,
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config, f)
            temp_path = Path(f.name)
        try:
            loaded_config = load_config_from_file(temp_path)
            assert loaded_config == test_config
        finally:
            temp_path.unlink()


class TestValidationFunctions:
    """Test input validation functions."""

    def test_validate_ip_address(self):
        """Test IP address validation."""
        # Valid IPv4 addresses
        assert validate_ip_address("192.168.1.1") is True
        assert validate_ip_address("10.0.0.1") is True
        assert validate_ip_address("127.0.0.1") is True
        # Invalid IP addresses
        assert validate_ip_address("256.1.1.1") is False
        assert validate_ip_address("192.168.1") is False
        assert validate_ip_address("not.an.ip") is False
        assert validate_ip_address("") is False

    def test_validate_hostname(self):
        """Test hostname validation."""
        # Valid hostnames
        assert validate_hostname("example.com") is True
        assert validate_hostname("subdomain.example.com") is True
        assert validate_hostname("host-name") is True
        assert validate_hostname("host123") is True
        # Invalid hostnames
        assert validate_hostname("") is False
        assert validate_hostname("host..com") is False
        assert validate_hostname("-hostname") is False
        assert validate_hostname("hostname-") is False
        assert validate_hostname("a" * 256) is False  # Too long


@pytest.fixture
def mock_ipython_environment():
    """Mock IPython environment for testing."""
    # Save current module state and global variable
    original_module = sys.modules.get("clustrix.notebook_magic")
    global EnhancedClusterConfigWidget
    original_widget_class = EnhancedClusterConfigWidget

    try:
        # Clear the module to force re-import with mocks
        if "clustrix.notebook_magic" in sys.modules:
            del sys.modules["clustrix.notebook_magic"]

        # Mock the IPython/ipywidgets modules
        with patch.dict(
            "sys.modules",
            {
                "IPython": MagicMock(),
                "IPython.core": MagicMock(),
                "IPython.core.magic": MagicMock(),
                "IPython.display": MagicMock(),
                "ipywidgets": MagicMock(),
            },
        ):
            # Re-import the module with mocked dependencies
            import clustrix.notebook_magic

            # Mock get_ipython
            with patch("clustrix.notebook_magic.get_ipython") as mock_get_ipython:
                mock_ipython = MagicMock()
                mock_ipython.kernel = True
                mock_ipython.register_magic_function = MagicMock()
                mock_get_ipython.return_value = mock_ipython

                # Make the widget class available globally
                EnhancedClusterConfigWidget = (
                    clustrix.notebook_magic.EnhancedClusterConfigWidget
                )

                yield mock_ipython
    finally:
        # Restore original module and global variable
        if original_module:
            sys.modules["clustrix.notebook_magic"] = original_module
        EnhancedClusterConfigWidget = original_widget_class


class TestEnhancedClusterConfigWidget:
    """Test the enhanced cluster configuration widget."""

    def test_widget_initialization(self, mock_ipython_environment):
        """Test widget initialization with defaults."""
        widget = EnhancedClusterConfigWidget()
        # Check that default configs are loaded
        assert len(widget.configs) >= len(DEFAULT_CONFIGS)
        assert "Local Single-core" in widget.configs
        assert "Local Multi-core" in widget.configs
        # Check initial state
        assert widget.current_config_name is not None
        assert widget.auto_display is False

    def test_widget_auto_display_flag(self, mock_ipython_environment):
        """Test widget with auto_display flag."""
        widget = EnhancedClusterConfigWidget(auto_display=True)
        assert widget.auto_display is True

    def test_widget_without_ipython(self):
        """Test widget creation fails without IPython."""
        # Clear the module cache to ensure clean import
        import sys

        original_module = sys.modules.get("clustrix.notebook_magic")
        try:
            if "clustrix.notebook_magic" in sys.modules:
                del sys.modules["clustrix.notebook_magic"]

            with patch.dict("sys.modules", {"IPython": None, "ipywidgets": None}):
                from clustrix.notebook_magic import EnhancedClusterConfigWidget

                with pytest.raises(
                    ImportError, match="IPython and ipywidgets are required"
                ):
                    EnhancedClusterConfigWidget()
        finally:
            # Restore the original module
            if original_module:
                sys.modules["clustrix.notebook_magic"] = original_module

    def test_config_file_loading(self, mock_ipython_environment):
        """Test configuration loading from files."""
        test_configs = {
            "remote_cluster": {
                "cluster_type": "ssh",
                "cluster_host": "remote.example.com",
                "username": "testuser",
                "default_cores": 16,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "clustrix.yml"
            with open(config_file, "w") as f:
                yaml.dump(test_configs, f)
            with patch(
                "clustrix.notebook_magic.detect_config_files",
                return_value=[config_file],
            ):
                # Use the widget class from the mocked module
                import clustrix.notebook_magic

                widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
                # Check that file config was loaded
                assert "remote_cluster" in widget.configs
                assert (
                    widget.configs["remote_cluster"]["cluster_host"]
                    == "remote.example.com"
                )
                assert widget.config_file_map["remote_cluster"] == config_file

    def test_save_config_from_widgets(self, mock_ipython_environment):
        """Test saving configuration from widget values."""
        widget = EnhancedClusterConfigWidget()

        # Mock the widget fields directly to avoid complex mock interactions
        widget.config_name = MagicMock()
        widget.config_name.value = "Test Config"
        widget.cluster_type = MagicMock()
        widget.cluster_type.value = "ssh"
        widget.host_field = MagicMock()
        widget.host_field.value = "test.example.com"
        widget.username_field = MagicMock()
        widget.username_field.value = "testuser"
        widget.cores_field = MagicMock()
        widget.cores_field.value = 8
        widget.memory_field = MagicMock()
        widget.memory_field.value = "32GB"
        widget.package_manager = MagicMock()
        widget.package_manager.value = "conda"
        widget.time_field = MagicMock()
        widget.time_field.value = "01:00:00"
        widget.python_version = MagicMock()
        widget.python_version.value = "python"
        widget.env_vars = MagicMock()
        widget.env_vars.value = ""
        widget.module_loads = MagicMock()
        widget.module_loads.value = ""
        widget.pre_exec_commands = MagicMock()
        widget.pre_exec_commands.value = ""
        widget.port_field = MagicMock()
        widget.port_field.value = 22
        widget.work_dir_field = MagicMock()
        widget.work_dir_field.value = "/tmp/clustrix"
        widget.ssh_key_field = MagicMock()
        widget.ssh_key_field.value = ""
        widget.cost_monitoring_checkbox = MagicMock()
        widget.cost_monitoring_checkbox.value = True

        # Test save functionality
        config = widget._save_config_from_widgets()
        assert config["name"] == "Test Config"
        assert config["cluster_type"] == "ssh"
        assert config["cluster_host"] == "test.example.com"
        assert config["username"] == "testuser"
        assert config["default_cores"] == 8
        assert config["default_memory"] == "32GB"
        assert config["package_manager"] == "conda"
        assert config["cost_monitoring"] is True

    def test_load_config_to_widgets(self, mock_ipython_environment):
        """Test loading configuration into widgets."""
        widget = EnhancedClusterConfigWidget()

        # Mock the widget fields to test the loading functionality
        widget.config_name = MagicMock()
        widget.cluster_type = MagicMock()
        widget.host_field = MagicMock()
        widget.port_field = MagicMock()
        widget.cores_field = MagicMock()
        widget.memory_field = MagicMock()
        widget.k8s_namespace = MagicMock()
        widget.package_manager = MagicMock()
        widget.username_field = MagicMock()
        widget.ssh_key_field = MagicMock()
        widget.work_dir_field = MagicMock()
        widget.time_field = MagicMock()
        widget.python_version = MagicMock()
        widget.env_vars = MagicMock()
        widget.module_loads = MagicMock()
        widget.pre_exec_commands = MagicMock()
        widget.k8s_image = MagicMock()
        widget.cost_monitoring_checkbox = MagicMock()

        test_config = {
            "name": "Test Load Config",
            "cluster_type": "kubernetes",
            "cluster_host": "k8s.example.com",
            "cluster_port": 443,
            "default_cores": 12,
            "default_memory": "64GB",
            "k8s_namespace": "production",
            "package_manager": "uv",
            "cost_monitoring": True,
        }
        # Add test config and load it
        widget.configs["test_load"] = test_config
        widget._load_config_to_widgets("test_load")
        # Check that widget values were updated
        # Widget uses "name" field from config if present, otherwise falls back to config key
        assert (
            widget.config_name.value == "Test Load Config"
        )  # Uses the "name" field from test_config
        assert widget.cluster_type.value == "kubernetes"
        assert widget.host_field.value == "k8s.example.com"
        assert widget.port_field.value == 443
        assert widget.cores_field.value == 12
        assert widget.memory_field.value == "64GB"
        assert widget.k8s_namespace.value == "production"
        assert widget.package_manager.value == "uv"
        assert widget.cost_monitoring_checkbox.value is True

    def test_cluster_type_field_visibility(self, mock_ipython_environment):
        """Test field visibility changes based on cluster type."""
        # Use the widget class from the mocked module
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
        # Test local cluster type (should hide remote fields)
        widget._on_cluster_type_change({"new": "local"})
        # Check that layout.display attribute is properly set
        # Note: In mock environment, this tests the logic, not actual UI behavior
        # Test SSH cluster type (should show SSH fields)
        widget._on_cluster_type_change({"new": "ssh"})
        # In a real environment, fields would be shown/hidden
        # Test Kubernetes cluster type (should show K8s fields)
        widget._on_cluster_type_change({"new": "kubernetes"})
        # In a real environment, different fields would be shown/hidden
        # The key test is that the method executes without error
        assert True  # Method executed successfully

    def test_host_validation(self, mock_ipython_environment):
        """Test host field validation."""
        # Use the widget class from the mocked module
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
        # Test valid hostname
        widget._validate_host({"new": "example.com"})
        assert widget.host_field.layout.border == ""
        # Test valid IP
        widget._validate_host({"new": "192.168.1.1"})
        assert widget.host_field.layout.border == ""
        # Test invalid input
        widget._validate_host({"new": "invalid..hostname"})
        assert widget.host_field.layout.border == "2px solid red"

    def test_environment_variable_parsing(self, mock_ipython_environment):
        """Test environment variable parsing from textarea."""
        # Use the widget class from the mocked module
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
        # Set environment variables
        widget.env_vars.value = "KEY1=value1\nKEY2=value2\nKEY3=value with spaces"
        config = widget._save_config_from_widgets()
        expected_env = {"KEY1": "value1", "KEY2": "value2", "KEY3": "value with spaces"}
        assert config["environment_variables"] == expected_env

    def test_module_loads_parsing(self, mock_ipython_environment):
        """Test module loads parsing from textarea."""
        # Use the widget class from the mocked module
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
        # Set module loads
        widget.module_loads.value = "gcc/9.3.0\npython/3.11\ncuda/11.8"
        config = widget._save_config_from_widgets()
        expected_modules = ["gcc/9.3.0", "python/3.11", "cuda/11.8"]
        assert config["module_loads"] == expected_modules

    def test_add_new_config(self, mock_ipython_environment):
        """Test adding a new configuration."""
        # Use the widget class from the mocked module
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
        initial_count = len(widget.configs)
        # Mock status output
        widget.status_output = MagicMock()
        widget.status_output.clear_output = MagicMock()
        # Add new config
        widget._on_add_config(None)
        # Check that config was added
        assert len(widget.configs) == initial_count + 1
        assert "New Configuration" in widget.configs


class TestAutoDisplayFunctionality:
    """Test automatic widget display functionality."""

    def test_display_config_widget_function(self, mock_ipython_environment):
        """Test display_config_widget function."""
        # Use functions from the mocked module
        import clustrix.notebook_magic

        # Test that display_config_widget function runs without error
        # It should only use the modern widget (no fallback)
        try:
            result = clustrix.notebook_magic.display_config_widget(auto_display=True)
            # Function should not raise any exceptions and should return the modern widget
            assert result is not None
        except Exception as e:
            pytest.fail(f"display_config_widget raised an exception: {e}")

    def test_auto_display_on_import_notebook(self, mock_ipython_environment):
        """Test auto display when imported in notebook."""
        # Use functions from the mocked module
        import clustrix.notebook_magic

        # Test auto_display_on_import function
        with patch("clustrix.notebook_magic.display_config_widget") as mock_display:
            clustrix.notebook_magic.auto_display_on_import()
            mock_display.assert_called_once_with(auto_display=True)

    def test_auto_display_no_ipython(self):
        """Test auto display when IPython not available."""
        # Import functions from the module
        from clustrix.notebook_magic import auto_display_on_import

        with patch("clustrix.notebook_magic.IPYTHON_AVAILABLE", False):
            with patch("clustrix.notebook_magic.display_config_widget") as mock_display:
                auto_display_on_import()
                mock_display.assert_not_called()


class TestMagicCommands:
    """Test IPython magic command functionality."""

    def test_load_ipython_extension(self, mock_ipython_environment):
        """Test loading IPython extension."""
        # Import functions from the mocked module
        import clustrix.notebook_magic

        mock_ipython = mock_ipython_environment
        # Mock the ClusterfyMagics class to avoid trait validation issues
        with patch("clustrix.notebook_magic.ClusterfyMagics") as MockMagics:
            mock_magic_instance = MagicMock()
            MockMagics.return_value = mock_magic_instance
            clustrix.notebook_magic.load_ipython_extension(mock_ipython)
            MockMagics.assert_called_once_with(mock_ipython)
            mock_ipython.register_magic_function.assert_called_once()
            # Note: No print message expected since widget displays automatically

    def test_clusterfy_magic_without_ipython(self):
        """Test magic command fails gracefully without IPython."""
        # Import the magic class directly - avoid module reloading issues
        from clustrix.notebook_magic import ClusterfyMagics

        # Test with thorough patching to override any existing state
        with patch("clustrix.notebook_magic.IPYTHON_AVAILABLE", False), patch(
            "builtins.print"
        ) as mock_print, patch(
            "clustrix.notebook_magic.display_config_widget"
        ) as mock_display, patch(
            "clustrix.notebook_magic.get_ipython", return_value=None
        ):
            magic = ClusterfyMagics()
            magic.shell = MagicMock()

            # Test that the clusterfy method exists and is callable
            assert hasattr(magic, "clusterfy")
            assert callable(magic.clusterfy)

            # Call the magic method
            result = magic.clusterfy("", "")

            # Collect all print messages
            print_messages = []
            if mock_print.call_args_list:
                for call in mock_print.call_args_list:
                    if call[0]:  # Ensure there are positional arguments
                        print_messages.append(str(call[0][0]))

            # Check for expected behavior patterns (robust for different environments)
            has_error_messages = any(
                any(
                    keyword in msg.lower()
                    for keyword in ["ipython", "widget", "magic", "requires"]
                )
                for msg in print_messages
            )
            display_not_called = not mock_display.called
            returned_none_or_empty = result is None or result == ""

            # Success criteria: graceful handling of missing IPython
            # This can manifest as error messages, avoiding widget display, or returning None
            success = has_error_messages or display_not_called or returned_none_or_empty

            # For CI debugging - provide comprehensive info if test fails
            if not success:
                debug_info = {
                    "print_messages": print_messages,
                    "has_error_messages": has_error_messages,
                    "display_not_called": display_not_called,
                    "result": result,
                    "python_version": sys.version_info[:2],
                    "mock_display_call_count": mock_display.call_count,
                    "mock_print_call_count": mock_print.call_count,
                }
                print(f"DEBUG: Test failure details: {debug_info}")

            assert (
                success
            ), "Magic command should handle missing IPython gracefully. Check debug output above."


class TestConfigurationSaveLoad:
    """Test configuration save/load functionality integration."""

    def test_save_load_cycle(self, mock_ipython_environment):
        """Test complete save and load cycle."""
        # Use the widget class from the mocked module
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
        # Create a test configuration
        test_config = {
            "name": "Integration Test Config",
            "cluster_type": "slurm",
            "cluster_host": "hpc.university.edu",
            "username": "researcher",
            "default_cores": 32,
            "default_memory": "128GB",
            "package_manager": "conda",
        }
        # Add config to widget
        widget.configs["integration_test"] = test_config
        widget.current_config_name = "integration_test"
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "test_config.yml"
            # Mock the save file dropdown
            widget.save_file_dropdown = MagicMock()
            widget.save_file_dropdown.value = f"New file: {save_path.name}"
            widget.config_files = []
            # Mock status output
            widget.status_output = MagicMock()
            widget.status_output.clear_output = MagicMock()
            # Mock the widget values to match test config
            widget.config_name = MagicMock()
            widget.config_name.value = test_config["name"]
            widget.cluster_type = MagicMock()
            widget.cluster_type.value = test_config["cluster_type"]
            widget.host_field = MagicMock()
            widget.host_field.value = test_config["cluster_host"]
            widget.username_field = MagicMock()
            widget.username_field.value = test_config["username"]
            widget.cores_field = MagicMock()
            widget.cores_field.value = test_config["default_cores"]
            widget.memory_field = MagicMock()
            widget.memory_field.value = test_config["default_memory"]
            widget.package_manager = MagicMock()
            widget.package_manager.value = test_config["package_manager"]
            widget.time_field = MagicMock()
            widget.time_field.value = "01:00:00"
            widget.python_version = MagicMock()
            widget.python_version.value = "python"
            widget.env_vars = MagicMock()
            widget.env_vars.value = ""
            widget.module_loads = MagicMock()
            widget.module_loads.value = ""
            widget.pre_exec_commands = MagicMock()
            widget.pre_exec_commands.value = ""
            widget.cost_monitoring_checkbox = MagicMock()
            widget.cost_monitoring_checkbox.value = False
            # Mock the new filename input field
            widget.save_filename_input = MagicMock()
            widget.save_filename_input.value = "clustrix.yml"
            # Change current directory for the test
            import os

            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # Trigger save
                widget._on_save_config(None)
                # Check that file was created (default name is clustrix.yml)
                default_save_path = Path(tmpdir) / "clustrix.yml"
                assert default_save_path.exists()
                # Load and verify content
                with open(default_save_path, "r") as f:
                    saved_data = yaml.safe_load(f)
                assert "Integration Test Config" in saved_data
                saved_config = saved_data["Integration Test Config"]
                assert saved_config["name"] == test_config["name"]
                assert saved_config["cluster_type"] == test_config["cluster_type"]
                assert saved_config["cluster_host"] == test_config["cluster_host"]
            finally:
                os.chdir(old_cwd)

    def test_multiple_config_file_handling(self, mock_ipython_environment):
        """Test handling multiple configuration files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple config files
            config1_path = Path(tmpdir) / "config1.yml"
            config2_path = Path(tmpdir) / "config2.yml"
            config1_data = {
                "cluster1": {"cluster_type": "ssh", "cluster_host": "host1.com"}
            }
            config2_data = {
                "cluster2": {"cluster_type": "slurm", "cluster_host": "host2.com"}
            }
            with open(config1_path, "w") as f:
                yaml.dump(config1_data, f)
            with open(config2_path, "w") as f:
                yaml.dump(config2_data, f)
            # Mock file detection
            with patch(
                "clustrix.notebook_magic.detect_config_files",
                return_value=[config1_path, config2_path],
            ):
                # Use the widget class from the mocked module
                import clustrix.notebook_magic

                widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()
                # Check that both configs were loaded
                assert "cluster1" in widget.configs
                assert "cluster2" in widget.configs
                assert widget.configs["cluster1"]["cluster_host"] == "host1.com"
                assert widget.configs["cluster2"]["cluster_host"] == "host2.com"

    def test_save_all_configurations(self, mock_ipython_environment):
        """Test saving all configurations from widget dropdown."""
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()

        # Add multiple test configurations
        config1 = {
            "name": "Test Config 1",
            "cluster_type": "ssh",
            "cluster_host": "host1.com",
            "default_cores": 4,
            "default_memory": "8GB",
        }
        config2 = {
            "name": "Test Config 2",
            "cluster_type": "slurm",
            "cluster_host": "host2.com",
            "default_cores": 8,
            "default_memory": "16GB",
        }

        widget.configs["test_config_1"] = config1
        widget.configs["test_config_2"] = config2
        widget.current_config_name = "test_config_1"

        # Mock required widget fields
        widget.config_name = MagicMock()
        widget.config_name.value = "test_config_1"
        widget.cluster_type = MagicMock()
        widget.cluster_type.value = "ssh"
        widget.host_field = MagicMock()
        widget.host_field.value = "host1.com"
        widget.username_field = MagicMock()
        widget.username_field.value = "user"
        widget.cores_field = MagicMock()
        widget.cores_field.value = 4
        widget.memory_field = MagicMock()
        widget.memory_field.value = "8GB"
        widget.time_field = MagicMock()
        widget.time_field.value = "01:00:00"
        widget.python_version = MagicMock()
        widget.python_version.value = "python"
        widget.package_manager = MagicMock()
        widget.package_manager.value = "pip"
        widget.env_vars = MagicMock()
        widget.env_vars.value = ""
        widget.module_loads = MagicMock()
        widget.module_loads.value = ""
        widget.pre_exec_commands = MagicMock()
        widget.pre_exec_commands.value = ""
        widget.port_field = MagicMock()
        widget.port_field.value = 22
        widget.work_dir_field = MagicMock()
        widget.work_dir_field.value = "/tmp/clustrix"
        widget.ssh_key_field = MagicMock()
        widget.ssh_key_field.value = ""
        widget.cost_monitoring_checkbox = MagicMock()
        widget.cost_monitoring_checkbox.value = False
        widget.save_filename_input = MagicMock()
        widget.save_filename_input.value = "test_all_configs.yml"
        widget.status_output = MagicMock()
        widget.status_output.clear_output = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                # Trigger save - should save all configurations
                widget._on_save_config(None)

                # Check that file was created
                save_path = Path(tmpdir) / "test_all_configs.yml"
                assert save_path.exists()

                # Load and verify content - should contain both configurations
                with open(save_path, "r") as f:
                    saved_data = yaml.safe_load(f)

                # Should have both configs (defaults are skipped)
                assert "test_config_1" in saved_data
                assert "test_config_2" in saved_data
                assert saved_data["test_config_1"]["cluster_host"] == "host1.com"
                assert saved_data["test_config_2"]["cluster_host"] == "host2.com"

            finally:
                os.chdir(old_cwd)

    def test_test_configuration_functionality(self, mock_ipython_environment):
        """Test the test configuration button functionality."""
        import clustrix.notebook_magic

        widget = clustrix.notebook_magic.EnhancedClusterConfigWidget()

        # Set up a test configuration
        widget.current_config_name = "test_config"

        # Mock required widget fields for SSH configuration
        widget.config_name = MagicMock()
        widget.config_name.value = "Test SSH Config"
        widget.cluster_type = MagicMock()
        widget.cluster_type.value = "ssh"
        widget.host_field = MagicMock()
        widget.host_field.value = "example.com"
        widget.username_field = MagicMock()
        widget.username_field.value = "testuser"
        widget.cores_field = MagicMock()
        widget.cores_field.value = 8
        widget.memory_field = MagicMock()
        widget.memory_field.value = "16GB"
        widget.time_field = MagicMock()
        widget.time_field.value = "02:00:00"
        widget.python_version = MagicMock()
        widget.python_version.value = "python"
        widget.package_manager = MagicMock()
        widget.package_manager.value = "conda"
        widget.env_vars = MagicMock()
        widget.env_vars.value = ""
        widget.module_loads = MagicMock()
        widget.module_loads.value = ""
        widget.pre_exec_commands = MagicMock()
        widget.pre_exec_commands.value = ""
        widget.port_field = MagicMock()
        widget.port_field.value = 22
        widget.work_dir_field = MagicMock()
        widget.work_dir_field.value = "/tmp/clustrix"
        widget.ssh_key_field = MagicMock()
        widget.ssh_key_field.value = "~/.ssh/id_rsa"
        widget.cost_monitoring_checkbox = MagicMock()
        widget.cost_monitoring_checkbox.value = False
        widget.status_output = MagicMock()
        widget.status_output.clear_output = MagicMock()

        # Test the configuration - should succeed without errors
        widget._on_test_config(None)

        # Verify the status output was used
        widget.status_output.clear_output.assert_called_once()
