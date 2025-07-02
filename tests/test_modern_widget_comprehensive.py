"""Comprehensive tests for the modern notebook widget implementation."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any

from clustrix.profile_manager import ProfileManager
from clustrix.config import ClusterConfig


class MockWidget:
    """Mock ipywidgets.Widget for testing."""

    def __init__(self, **kwargs):
        self.value = kwargs.get("value", "")
        self.options = kwargs.get("options", [])
        self.description = kwargs.get("description", "")
        self.disabled = kwargs.get("disabled", False)

        # Handle layout parameter properly
        layout_param = kwargs.get("layout", {})
        if hasattr(layout_param, "__dict__"):
            # If it's already a Layout object, use it
            self.layout = layout_param
        else:
            # If it's a dict or kwargs, create new MockLayout
            self.layout = (
                MockLayout(**layout_param)
                if isinstance(layout_param, dict)
                else MockLayout()
            )

        self.style = kwargs.get("style", {})
        self._observers = []
        self._click_handlers = []

    def observe(self, handler, names=None):
        """Mock observe method."""
        self._observers.append((handler, names))

    def on_click(self, handler):
        """Mock on_click method."""
        self._click_handlers.append(handler)

    def trigger_change(self, new_value):
        """Simulate a value change event."""
        old_value = self.value
        self.value = new_value
        change = {"old": old_value, "new": new_value}
        for handler, names in self._observers:
            if names is None or "value" in names:
                handler(change)

    def trigger_click(self):
        """Simulate a button click."""
        for handler in self._click_handlers:
            handler(self)


class MockLayout:
    """Mock ipywidgets.Layout for testing."""

    def __init__(self, **kwargs):
        self.display = kwargs.get("display", "block")
        self.width = kwargs.get("width", "")
        self.height = kwargs.get("height", "")
        self.margin = kwargs.get("margin", "")
        self.padding = kwargs.get("padding", "")


class MockHBox(MockWidget):
    """Mock ipywidgets.HBox for testing."""

    def __init__(self, children=None, **kwargs):
        super().__init__(**kwargs)
        self.children = children or []


class MockVBox(MockWidget):
    """Mock ipywidgets.VBox for testing."""

    def __init__(self, children=None, **kwargs):
        super().__init__(**kwargs)
        self.children = children or []


class MockOutput:
    """Mock ipywidgets.Output for testing."""

    def __init__(self, **kwargs):
        # Handle layout parameter properly
        layout_param = kwargs.get("layout", {})
        if hasattr(layout_param, "__dict__"):
            # If it's already a Layout object, use it
            self.layout = layout_param
        else:
            # If it's a dict or kwargs, create new MockLayout
            self.layout = (
                MockLayout(**layout_param)
                if isinstance(layout_param, dict)
                else MockLayout()
            )

        self.captured_output = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def clear_output(self):
        """Mock clear_output method."""
        self.captured_output.clear()


class MockWidgets:
    """Mock ipywidgets module for testing."""

    Dropdown = MockWidget
    Text = MockWidget
    IntText = MockWidget
    FloatText = MockWidget
    Password = MockWidget
    Checkbox = MockWidget
    Button = MockWidget
    Textarea = MockWidget
    HTML = MockWidget
    HBox = MockHBox
    VBox = MockVBox
    Output = MockOutput
    Layout = MockLayout


@pytest.fixture
def mock_ipython_env():
    """Create a mock IPython environment for testing."""
    mock_widgets = MockWidgets()
    with patch("clustrix.modern_notebook_widget.IPYTHON_AVAILABLE", True):
        with patch("clustrix.modern_notebook_widget.widgets", mock_widgets):
            with patch("clustrix.modern_notebook_widget.display", Mock()):
                yield


@pytest.fixture
def temp_profile_manager():
    """Create a ProfileManager with temporary directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield ProfileManager(config_dir=temp_dir)


class TestModernClustrixWidgetInitialization:
    """Test widget initialization and basic functionality."""

    def test_widget_initialization_without_ipython(self):
        """Test that widget initialization fails gracefully without IPython."""
        with patch("clustrix.modern_notebook_widget.IPYTHON_AVAILABLE", False):
            from clustrix.modern_notebook_widget import ModernClustrixWidget

            with pytest.raises(
                ImportError, match="IPython and ipywidgets are required"
            ):
                ModernClustrixWidget()

    def test_widget_initialization_with_mock_ipython(
        self, mock_ipython_env, temp_profile_manager
    ):
        """Test widget initialization with mocked IPython environment."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        assert widget.profile_manager is temp_profile_manager
        assert isinstance(widget.widgets, dict)
        assert widget.advanced_settings_visible is False
        assert widget.current_cluster_type == "local"

    def test_widget_with_default_profile_manager(self, mock_ipython_env):
        """Test widget initialization with default ProfileManager."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget()

        assert widget.profile_manager is not None
        assert len(widget.profile_manager.get_profile_names()) == 1
        assert "Local single-core" in widget.profile_manager.get_profile_names()

    def test_widget_creation_methods(self, mock_ipython_env, temp_profile_manager):
        """Test widget creation factory methods."""
        from clustrix.modern_notebook_widget import (
            create_modern_cluster_widget,
            display_modern_widget,
        )

        # Test create_modern_cluster_widget
        widget = create_modern_cluster_widget(temp_profile_manager)
        assert widget is not None

        # Test display_modern_widget (should not raise error)
        display_modern_widget()


class TestWidgetComponents:
    """Test widget component creation and properties."""

    def test_profile_row_components(self, mock_ipython_env, temp_profile_manager):
        """Test profile management row components."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Check profile dropdown
        profile_dropdown = widget.widgets["profile_dropdown"]
        assert profile_dropdown.value == "Local single-core"
        assert "Local single-core" in profile_dropdown.options

        # Check add/remove buttons
        assert "add_profile_btn" in widget.widgets
        assert "remove_profile_btn" in widget.widgets

    def test_config_row_components(self, mock_ipython_env, temp_profile_manager):
        """Test configuration file management row components."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Check config filename field
        config_filename = widget.widgets["config_filename"]
        assert config_filename.value == "clustrix.yml"

        # Check file management buttons
        assert "save_btn" in widget.widgets
        assert "load_btn" in widget.widgets

        # Check action buttons
        assert "apply_btn" in widget.widgets
        assert "test_connect_btn" in widget.widgets
        assert "test_submit_btn" in widget.widgets

    def test_cluster_row_components(self, mock_ipython_env, temp_profile_manager):
        """Test cluster configuration row components."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Check cluster type dropdown
        cluster_type = widget.widgets["cluster_type"]
        assert cluster_type.value == "local"
        assert "slurm" in cluster_type.options
        assert "kubernetes" in cluster_type.options

        # Check resource fields
        assert widget.widgets["cpus"].value == 1
        assert widget.widgets["ram"].value == 16.25
        assert widget.widgets["time"].value == "01:00:00"

        # Check advanced toggle
        assert "advanced_toggle" in widget.widgets

    def test_advanced_section_components(self, mock_ipython_env, temp_profile_manager):
        """Test advanced settings section components."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Check package manager
        package_manager = widget.widgets["package_manager"]
        assert package_manager.value == "auto"
        assert "pip" in package_manager.options
        assert "conda" in package_manager.options

        # Check python executable
        python_exec = widget.widgets["python_executable"]
        assert python_exec.value == "python"

        # Check clone environment
        clone_env = widget.widgets["clone_env"]
        assert clone_env.value is True

        # Check dynamic lists
        assert "env_vars" in widget.widgets
        assert "modules" in widget.widgets
        assert "env_vars_add" in widget.widgets
        assert "env_vars_remove" in widget.widgets
        assert "modules_add" in widget.widgets
        assert "modules_remove" in widget.widgets

        # Check pre-exec commands
        assert "pre_exec_commands" in widget.widgets

    def test_remote_section_components(self, mock_ipython_env, temp_profile_manager):
        """Test remote cluster section components."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Check remote connection fields
        assert "host" in widget.widgets
        assert widget.widgets["port"].value == 22
        assert "username" in widget.widgets

        # Check SSH fields
        ssh_key_file = widget.widgets["ssh_key_file"]
        assert ssh_key_file.value == "~/.ssh/id_rsa"
        assert "refresh_keys" in widget.widgets
        assert "password" in widget.widgets

        # Check authentication options
        assert "local_env_var" in widget.widgets
        assert "use_1password" in widget.widgets
        assert "home_dir" in widget.widgets

        # Check SSH setup button
        assert "auto_setup_ssh" in widget.widgets


class TestEventHandlers:
    """Test widget event handlers and interactions."""

    def test_profile_change_handler(self, mock_ipython_env, temp_profile_manager):
        """Test profile dropdown change handler."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        # Add another profile
        config = ClusterConfig(cluster_type="ssh", default_cores=4)
        temp_profile_manager.create_profile("SSH Cluster", config)

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Simulate profile change
        profile_dropdown = widget.widgets["profile_dropdown"]
        profile_dropdown.trigger_change("SSH Cluster")

        # Verify widget updated
        assert widget.widgets["cluster_type"].value == "ssh"
        assert widget.widgets["cpus"].value == 4

    def test_add_profile_handler(self, mock_ipython_env, temp_profile_manager):
        """Test add profile button handler."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Get initial profile count
        initial_count = len(temp_profile_manager.get_profile_names())

        # Simulate add profile button click
        add_button = widget.widgets["add_profile_btn"]
        add_button.trigger_click()

        # Verify new profile created
        assert len(temp_profile_manager.get_profile_names()) == initial_count + 1
        assert "Local single-core (copy)" in temp_profile_manager.get_profile_names()

    def test_remove_profile_handler(self, mock_ipython_env, temp_profile_manager):
        """Test remove profile button handler."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        # Add another profile first
        config = ClusterConfig(cluster_type="ssh")
        temp_profile_manager.create_profile("SSH Cluster", config)

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Switch to the new profile
        widget.widgets["profile_dropdown"].value = "SSH Cluster"

        # Simulate remove profile button click
        remove_button = widget.widgets["remove_profile_btn"]
        remove_button.trigger_click()

        # Verify profile removed
        assert "SSH Cluster" not in temp_profile_manager.get_profile_names()
        assert len(temp_profile_manager.get_profile_names()) == 1

    def test_cannot_remove_last_profile(self, mock_ipython_env, temp_profile_manager):
        """Test that last profile cannot be removed."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Simulate remove profile button click on last profile
        remove_button = widget.widgets["remove_profile_btn"]
        remove_button.trigger_click()

        # Verify profile still exists
        assert len(temp_profile_manager.get_profile_names()) == 1
        assert "Local single-core" in temp_profile_manager.get_profile_names()

    def test_cluster_type_change_handler(self, mock_ipython_env, temp_profile_manager):
        """Test cluster type dropdown change handler."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Initially should be local with remote section hidden
        assert widget.current_cluster_type == "local"
        assert widget.widgets["remote_section"].layout.display == "none"

        # Change to SLURM
        cluster_type = widget.widgets["cluster_type"]
        cluster_type.trigger_change("slurm")

        # Verify remote section now visible
        assert widget.current_cluster_type == "slurm"
        assert widget.widgets["remote_section"].layout.display == "block"

    def test_advanced_settings_toggle(self, mock_ipython_env, temp_profile_manager):
        """Test advanced settings toggle functionality."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Initially should be hidden
        assert widget.advanced_settings_visible is False
        assert widget.widgets["advanced_section"].layout.display == "none"

        # Click toggle button
        toggle_button = widget.widgets["advanced_toggle"]
        toggle_button.trigger_click()

        # Verify now visible
        assert widget.advanced_settings_visible is True
        assert widget.widgets["advanced_section"].layout.display == "block"

        # Click again to hide
        toggle_button.trigger_click()

        # Verify hidden again
        assert widget.advanced_settings_visible is False
        assert widget.widgets["advanced_section"].layout.display == "none"


class TestConfigurationSynchronization:
    """Test synchronization between widgets and ClusterConfig objects."""

    def test_get_config_from_widgets(self, mock_ipython_env, temp_profile_manager):
        """Test extracting configuration from widget values."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Set some widget values
        widget.widgets["cluster_type"].value = "slurm"
        widget.widgets["cpus"].value = 8
        widget.widgets["ram"].value = 32.0
        widget.widgets["time"].value = "02:00:00"
        widget.widgets["host"].value = "cluster.edu"
        widget.widgets["username"].value = "researcher"

        # Trigger cluster type change to update UI state
        widget.widgets["cluster_type"].trigger_change("slurm")

        # Extract config
        config = widget._get_config_from_widgets()

        # Verify config values
        assert config.cluster_type == "slurm"
        assert config.default_cores == 8
        assert config.default_memory == "32.0GB"
        assert config.default_time == "02:00:00"
        assert config.cluster_host == "cluster.edu"
        assert config.username == "researcher"

    def test_load_config_to_widgets(self, mock_ipython_env, temp_profile_manager):
        """Test loading configuration values into widgets."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Create a config
        config = ClusterConfig(
            cluster_type="pbs",
            default_cores=16,
            default_memory="64GB",
            default_time="04:00:00",
            cluster_host="hpc.university.edu",
            username="student",
        )

        # Load config into widgets
        widget._load_config_to_widgets(config)

        # Verify widget values
        assert widget.widgets["cluster_type"].value == "pbs"
        assert widget.widgets["cpus"].value == 16
        assert widget.widgets["ram"].value == 64.0
        assert widget.widgets["time"].value == "04:00:00"
        assert widget.widgets["host"].value == "hpc.university.edu"
        assert widget.widgets["username"].value == "student"

    def test_config_roundtrip(self, mock_ipython_env, temp_profile_manager):
        """Test config -> widgets -> config roundtrip."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Original config
        original_config = ClusterConfig(
            cluster_type="sge",
            default_cores=24,
            default_memory="128GB",
            default_time="08:00:00",
        )

        # Load to widgets and extract back
        widget._load_config_to_widgets(original_config)
        extracted_config = widget._get_config_from_widgets()

        # Verify roundtrip
        assert extracted_config.cluster_type == original_config.cluster_type
        assert extracted_config.default_cores == original_config.default_cores
        # Memory format might have slight differences due to float conversion
        assert extracted_config.default_memory in ["128GB", "128.0GB"]
        assert extracted_config.default_time == original_config.default_time


class TestDynamicListManagement:
    """Test dynamic list management for environment variables and modules."""

    def test_add_environment_variable(self, mock_ipython_env, temp_profile_manager):
        """Test adding environment variables."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Initially no env vars
        env_vars = widget.widgets["env_vars"]
        assert len(env_vars.options) == 0

        # Simulate add env var button click
        add_button = widget.widgets["env_vars_add"]
        add_button.trigger_click()

        # Verify env var added
        assert len(env_vars.options) == 1
        assert "NEW_VAR=value" in env_vars.options
        assert env_vars.value == "NEW_VAR=value"

    def test_remove_environment_variable(self, mock_ipython_env, temp_profile_manager):
        """Test removing environment variables."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Add an env var first
        env_vars = widget.widgets["env_vars"]
        env_vars.options = ["TEST_VAR=123"]
        env_vars.value = "TEST_VAR=123"

        # Simulate remove env var button click
        remove_button = widget.widgets["env_vars_remove"]
        remove_button.trigger_click()

        # Verify env var removed
        assert len(env_vars.options) == 0
        assert env_vars.value is None

    def test_add_module(self, mock_ipython_env, temp_profile_manager):
        """Test adding modules."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Initially no modules
        modules = widget.widgets["modules"]
        assert len(modules.options) == 0

        # Simulate add module button click
        add_button = widget.widgets["modules_add"]
        add_button.trigger_click()

        # Verify module added
        assert len(modules.options) == 1
        assert "python" in modules.options
        assert modules.value == "python"

    def test_remove_module(self, mock_ipython_env, temp_profile_manager):
        """Test removing modules."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Add a module first
        modules = widget.widgets["modules"]
        modules.options = ["gcc/9.3.0"]
        modules.value = "gcc/9.3.0"

        # Simulate remove module button click
        remove_button = widget.widgets["modules_remove"]
        remove_button.trigger_click()

        # Verify module removed
        assert len(modules.options) == 0
        assert modules.value is None


class TestFileOperations:
    """Test file save/load operations."""

    def test_save_config_handler(self, mock_ipython_env, temp_profile_manager):
        """Test save configuration button handler."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Set a filename
        widget.widgets["config_filename"].value = "test_config.yml"

        # Simulate save button click
        save_button = widget.widgets["save_btn"]
        save_button.trigger_click()

        # Verify file would be saved (we can't test actual file I/O easily here)
        # The handler should not raise any exceptions
        assert True  # If we get here, no exception was raised

    def test_load_config_handler(self, mock_ipython_env, temp_profile_manager):
        """Test load configuration button handler."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Create a test file first
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(
                """
active_profile: Test Profile
profiles:
  Test Profile:
    cluster_type: slurm
    default_cores: 4
    default_memory: 8GB
    default_time: 01:30:00
"""
            )
            test_file = f.name

        try:
            # Set the filename
            widget.widgets["config_filename"].value = test_file

            # Simulate load button click
            load_button = widget.widgets["load_btn"]
            load_button.trigger_click()

            # Verify profile loaded
            assert "Test Profile" in temp_profile_manager.get_profile_names()
            assert temp_profile_manager.active_profile == "Test Profile"
        finally:
            # Clean up
            os.unlink(test_file)


class TestActionButtons:
    """Test action button functionality."""

    def test_apply_config_button(self, mock_ipython_env, temp_profile_manager):
        """Test apply configuration button."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Set some values
        widget.widgets["cpus"].value = 8
        widget.widgets["ram"].value = 16.0

        # Simulate apply button click
        apply_button = widget.widgets["apply_btn"]
        apply_button.trigger_click()

        # Verify configuration applied to profile manager by checking the current profile
        current_profile_name = temp_profile_manager.active_profile
        current_config = temp_profile_manager.profiles[current_profile_name]
        assert current_config.default_cores == 8
        assert current_config.default_memory == "16.0GB"

    def test_test_connect_button(self, mock_ipython_env, temp_profile_manager):
        """Test test connect button."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Simulate test connect button click
        test_button = widget.widgets["test_connect_btn"]
        original_description = test_button.description

        test_button.trigger_click()

        # Verify button description was temporarily changed and restored
        # (In real implementation, this would be async, but our mock is synchronous)
        assert test_button.description == original_description
        assert test_button.disabled is False

    def test_test_submit_button(self, mock_ipython_env, temp_profile_manager):
        """Test test submit button."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Simulate test submit button click
        test_button = widget.widgets["test_submit_btn"]
        original_description = test_button.description

        test_button.trigger_click()

        # Verify button description was temporarily changed and restored
        assert test_button.description == original_description
        assert test_button.disabled is False


class TestErrorHandling:
    """Test error handling in widget operations."""

    def test_profile_change_with_invalid_profile(
        self, mock_ipython_env, temp_profile_manager
    ):
        """Test profile change with invalid profile name."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Simulate change to non-existent profile
        profile_dropdown = widget.widgets["profile_dropdown"]
        profile_dropdown.trigger_change("Non-existent Profile")

        # Should handle gracefully without crashing
        assert True  # If we get here, no exception was raised

    def test_load_non_existent_file(self, mock_ipython_env, temp_profile_manager):
        """Test loading non-existent configuration file."""
        from clustrix.modern_notebook_widget import ModernClustrixWidget

        widget = ModernClustrixWidget(profile_manager=temp_profile_manager)

        # Set non-existent filename
        widget.widgets["config_filename"].value = "non_existent_file.yml"

        # Simulate load button click
        load_button = widget.widgets["load_btn"]
        load_button.trigger_click()

        # Should handle gracefully without crashing
        assert True  # If we get here, no exception was raised


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
