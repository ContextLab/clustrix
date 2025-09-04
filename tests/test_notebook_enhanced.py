"""Comprehensive tests for enhanced notebook magic components."""

import pytest
import tempfile
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open, call
from datetime import datetime

# Test enhanced UI components
from clustrix.notebook_magic_enhanced import (
    ThemeManager,
    ProgressIndicator,
    NotificationManager,
    render_modern_styling,
    implement_dark_mode,
    add_progress_indicators,
    create_enhanced_ui_components,
    create_interactive_dashboard,
    validate_enhanced_features,
)

# Test SSH key management
from clustrix.notebook_magic_ssh import (
    SSHKeyInfo,
    SSHKeyManager,
    SSHConfigManager,
    generate_ssh_keys,
    manage_ssh_keychain,
    validate_ssh_connectivity,
    setup_ssh_config,
    list_ssh_keys,
)

from clustrix.config import ClusterConfig


class TestThemeManager:
    """Test theme management functionality."""

    def test_theme_manager_initialization(self):
        """Test ThemeManager initialization."""
        theme_manager = ThemeManager()
        assert theme_manager.current_theme == "light"
        assert "light" in theme_manager.themes
        assert "dark" in theme_manager.themes

    def test_get_current_theme(self):
        """Test getting current theme colors."""
        theme_manager = ThemeManager()
        colors = theme_manager.get_current_theme()
        assert isinstance(colors, dict)
        assert "background" in colors
        assert "primary" in colors
        assert "text" in colors

    def test_set_theme_valid(self):
        """Test setting valid theme."""
        theme_manager = ThemeManager()
        result = theme_manager.set_theme("dark")
        assert result is True
        assert theme_manager.current_theme == "dark"

    def test_set_theme_invalid(self):
        """Test setting invalid theme."""
        theme_manager = ThemeManager()
        result = theme_manager.set_theme("invalid_theme")
        assert result is False
        assert theme_manager.current_theme == "light"  # Should remain unchanged

    def test_get_theme_css(self):
        """Test CSS generation for themes."""
        theme_manager = ThemeManager()
        css = theme_manager.get_theme_css()
        assert isinstance(css, str)
        assert "clustrix-enhanced" in css
        assert "font-family" in css
        assert "color:" in css

    def test_dark_theme_colors(self):
        """Test dark theme has appropriate colors."""
        theme_manager = ThemeManager()
        theme_manager.set_theme("dark")
        colors = theme_manager.get_current_theme()
        assert colors["background"] == "#121212"
        assert colors["text"] == "#ffffff"

    def test_light_theme_colors(self):
        """Test light theme has appropriate colors."""
        theme_manager = ThemeManager()
        theme_manager.set_theme("light")
        colors = theme_manager.get_current_theme()
        assert colors["background"] == "#ffffff"
        assert colors["text"] == "#212529"


class TestProgressIndicator:
    """Test progress indicator functionality."""

    @pytest.fixture
    def mock_output_widget(self):
        """Mock IPython output widget."""
        mock_widget = Mock()
        mock_widget.clear_output = Mock()
        # Make it support context manager protocol
        mock_widget.__enter__ = Mock(return_value=mock_widget)
        mock_widget.__exit__ = Mock(return_value=None)
        return mock_widget

    def test_progress_indicator_initialization(self, mock_output_widget):
        """Test ProgressIndicator initialization."""
        progress = ProgressIndicator(mock_output_widget)
        assert progress.output == mock_output_widget
        assert progress.current_progress == 0
        assert progress.total_steps == 0

    @patch("builtins.print")
    def test_start_operation(self, mock_print, mock_output_widget):
        """Test starting an operation."""
        progress = ProgressIndicator(mock_output_widget)
        progress.start_operation("Test Operation", 10)

        assert progress.current_operation == "Test Operation"
        assert progress.total_steps == 10
        assert progress.current_progress == 0
        assert progress.start_time is not None
        mock_print.assert_called()

    @patch("builtins.print")
    def test_update_progress(self, mock_print, mock_output_widget):
        """Test updating progress."""
        progress = ProgressIndicator(mock_output_widget)
        progress.start_operation("Test Operation", 10)
        progress.update_progress(5, "Step 5")

        assert progress.current_progress == 5
        mock_print.assert_called()

    @patch("builtins.print")
    def test_update_progress_exceeds_total(self, mock_print, mock_output_widget):
        """Test updating progress beyond total steps."""
        progress = ProgressIndicator(mock_output_widget)
        progress.start_operation("Test Operation", 10)
        progress.update_progress(15, "Step 15")

        assert progress.current_progress == 10  # Should be capped at total

    @patch("builtins.print")
    def test_complete_operation_success(self, mock_print, mock_output_widget):
        """Test completing operation successfully."""
        progress = ProgressIndicator(mock_output_widget)
        progress.start_operation("Test Operation", 10)
        progress.complete_operation(True, "Success message")

        mock_print.assert_called()
        # Check that success emoji is used
        print_calls = [call.args[0] for call in mock_print.call_args_list]
        assert any("✅" in call for call in print_calls)

    @patch("builtins.print")
    def test_complete_operation_failure(self, mock_print, mock_output_widget):
        """Test completing operation with failure."""
        progress = ProgressIndicator(mock_output_widget)
        progress.start_operation("Test Operation", 10)
        progress.complete_operation(False, "Error message")

        mock_print.assert_called()
        # Check that error emoji is used
        print_calls = [call.args[0] for call in mock_print.call_args_list]
        assert any("❌" in call for call in print_calls)

    @patch("builtins.print")
    def test_render_progress_bar_zero_steps(self, mock_print, mock_output_widget):
        """Test rendering progress bar with zero total steps."""
        progress = ProgressIndicator(mock_output_widget)
        progress.total_steps = 0
        progress._render_progress_bar()
        # Should not print anything for zero steps
        mock_print.assert_not_called()


class TestNotificationManager:
    """Test notification management."""

    def test_notification_manager_initialization(self):
        """Test NotificationManager initialization."""
        manager = NotificationManager()
        assert manager.notifications == []

    @patch("builtins.print")
    def test_show_notification(self, mock_print):
        """Test showing a notification."""
        manager = NotificationManager()
        manager.show_notification("Test message", "info", 3000)

        assert len(manager.notifications) == 1
        notification = manager.notifications[0]
        assert notification["message"] == "Test message"
        assert notification["type"] == "info"
        assert notification["duration"] == 3000
        assert isinstance(notification["timestamp"], datetime)
        mock_print.assert_called_once()

    def test_get_recent_notifications(self):
        """Test getting recent notifications."""
        manager = NotificationManager()
        # Add more notifications than the limit
        for i in range(10):
            manager.show_notification(f"Message {i}", "info")

        recent = manager.get_recent_notifications(5)
        assert len(recent) == 5
        assert recent[-1]["message"] == "Message 9"  # Most recent

    def test_clear_notifications(self):
        """Test clearing notifications."""
        manager = NotificationManager()
        manager.show_notification("Test message", "info")
        assert len(manager.notifications) == 1

        manager.clear_notifications()
        assert len(manager.notifications) == 0


class TestEnhancedUIFunctions:
    """Test enhanced UI utility functions."""

    def test_render_modern_styling(self):
        """Test modern styling CSS generation."""
        css = render_modern_styling()
        assert isinstance(css, str)
        assert len(css) > 100
        assert "clustrix-enhanced" in css
        assert "font-family" in css

    def test_implement_dark_mode_enable(self):
        """Test enabling dark mode."""
        theme_manager = implement_dark_mode(True)
        assert isinstance(theme_manager, ThemeManager)
        assert theme_manager.current_theme == "dark"

    def test_implement_dark_mode_disable(self):
        """Test disabling dark mode."""
        theme_manager = implement_dark_mode(False)
        assert isinstance(theme_manager, ThemeManager)
        assert theme_manager.current_theme == "light"

    @patch("clustrix.notebook_magic_enhanced.IPYTHON_AVAILABLE", True)
    def test_add_progress_indicators_with_ipython(self):
        """Test adding progress indicators with IPython available."""
        mock_widget = Mock()
        progress = add_progress_indicators(mock_widget)
        assert isinstance(progress, ProgressIndicator)
        assert progress.output == mock_widget

    @patch("clustrix.notebook_magic_enhanced.IPYTHON_AVAILABLE", False)
    def test_add_progress_indicators_without_ipython(self):
        """Test adding progress indicators without IPython."""
        mock_widget = Mock()
        with pytest.raises(ImportError):
            add_progress_indicators(mock_widget)

    @patch("clustrix.notebook_magic_enhanced.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_enhanced.widgets")
    def test_create_enhanced_ui_components(self, mock_widgets):
        """Test creating enhanced UI components."""
        # Mock widget classes
        mock_widgets.ToggleButton = Mock()
        mock_widgets.HTML = Mock()
        mock_widgets.Output = Mock()
        mock_widgets.Button = Mock()
        mock_widgets.VBox = Mock()
        mock_widgets.Layout = Mock()

        components = create_enhanced_ui_components("light")

        assert isinstance(components, dict)
        assert "theme_manager" in components
        assert "notification_manager" in components
        assert "theme_toggle" in components
        assert "status_indicator" in components
        assert "progress_output" in components

    @patch("clustrix.notebook_magic_enhanced.IPYTHON_AVAILABLE", False)
    def test_create_enhanced_ui_components_no_ipython(self):
        """Test creating enhanced UI components without IPython."""
        with pytest.raises(ImportError):
            create_enhanced_ui_components()

    @patch("clustrix.notebook_magic_enhanced.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_enhanced.widgets")
    @patch("clustrix.notebook_magic_enhanced.display")
    @patch("clustrix.notebook_magic_enhanced.HTML")
    def test_create_interactive_dashboard(self, mock_html, mock_display, mock_widgets):
        """Test creating interactive dashboard."""
        # Mock widget classes and methods
        mock_widgets.ToggleButton = Mock()
        mock_widgets.HTML = Mock()
        mock_widgets.Output = Mock()
        mock_widgets.Button = Mock()
        mock_widgets.VBox = Mock()
        mock_widgets.HBox = Mock()
        mock_widgets.Layout = Mock()

        # Mock widget instances
        mock_widget_instance = Mock()
        mock_widget_instance.observe = Mock()
        mock_widget_instance.on_click = Mock()
        mock_widget_instance.add_class = Mock()

        for widget_class in [
            mock_widgets.ToggleButton,
            mock_widgets.HTML,
            mock_widgets.Output,
            mock_widgets.Button,
            mock_widgets.VBox,
            mock_widgets.HBox,
        ]:
            widget_class.return_value = mock_widget_instance

        dashboard = create_interactive_dashboard()

        # Verify dashboard was created
        assert dashboard is not None
        mock_display.assert_called()  # CSS should be injected

    def test_validate_enhanced_features(self):
        """Test validating enhanced features."""
        results = validate_enhanced_features()

        assert isinstance(results, dict)
        assert "theme_management" in results
        assert "notification_system" in results
        assert "css_generation" in results
        assert "component_creation" in results

        # All results should be boolean
        for key, value in results.items():
            assert isinstance(value, bool)


class TestSSHKeyInfo:
    """Test SSH key information dataclass."""

    def test_ssh_key_info_creation(self):
        """Test creating SSH key info."""
        key_info = SSHKeyInfo(
            public_key_path="/test/key.pub",
            private_key_path="/test/key",
            key_type="ed25519",
            fingerprint="SHA256:test",
            comment="test@host",
            created_at="123456789",
            exists=True,
        )

        assert key_info.public_key_path == "/test/key.pub"
        assert key_info.private_key_path == "/test/key"
        assert key_info.key_type == "ed25519"
        assert key_info.fingerprint == "SHA256:test"
        assert key_info.comment == "test@host"
        assert key_info.created_at == "123456789"
        assert key_info.exists is True

    def test_ssh_key_info_defaults(self):
        """Test SSH key info with default values."""
        key_info = SSHKeyInfo(
            public_key_path="/test/key.pub",
            private_key_path="/test/key",
            key_type="ed25519",
        )

        assert key_info.fingerprint is None
        assert key_info.comment is None
        assert key_info.created_at is None
        assert key_info.exists is False


class TestSSHKeyManager:
    """Test SSH key management functionality."""

    @pytest.fixture
    def mock_config(self):
        """Mock cluster configuration."""
        return ClusterConfig(
            cluster_type="ssh", cluster_host="test.example.com", username="testuser"
        )

    @pytest.fixture
    def ssh_key_manager(self, mock_config):
        """SSH key manager instance."""
        return SSHKeyManager(mock_config)

    def test_ssh_key_manager_initialization(self, ssh_key_manager):
        """Test SSH key manager initialization."""
        assert ssh_key_manager.config is not None
        assert ssh_key_manager.ssh_dir.name == ".ssh"

    def test_get_default_key_path_ed25519(self, ssh_key_manager):
        """Test getting default key path for ed25519."""
        path = ssh_key_manager.get_default_key_path("ed25519")
        assert "id_ed25519" in path

    def test_get_default_key_path_rsa(self, ssh_key_manager):
        """Test getting default key path for RSA."""
        path = ssh_key_manager.get_default_key_path("rsa")
        assert "id_rsa" in path

    def test_get_default_key_path_custom(self, ssh_key_manager):
        """Test getting default key path for custom key type."""
        path = ssh_key_manager.get_default_key_path("custom")
        assert "id_custom" in path

    @patch("pathlib.Path.exists")
    def test_get_key_info_nonexistent(self, mock_exists, ssh_key_manager):
        """Test getting info for non-existent key."""
        mock_exists.return_value = False

        key_info = ssh_key_manager.get_key_info("/test/id_ed25519")

        assert key_info.key_type == "ed25519"
        assert key_info.exists is False
        assert key_info.fingerprint is None

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch.object(SSHKeyManager, "_get_key_fingerprint")
    @patch.object(SSHKeyManager, "_get_key_comment")
    def test_get_key_info_existing(
        self, mock_comment, mock_fingerprint, mock_stat, mock_exists, ssh_key_manager
    ):
        """Test getting info for existing key."""
        mock_exists.return_value = True
        mock_stat.return_value.st_mtime = 123456789
        mock_fingerprint.return_value = "SHA256:test"
        mock_comment.return_value = "test@host"

        key_info = ssh_key_manager.get_key_info("/test/id_ed25519")

        assert key_info.exists is True
        assert key_info.fingerprint == "SHA256:test"
        assert key_info.comment == "test@host"

    @patch("subprocess.run")
    @patch("pathlib.Path.chmod")
    def test_generate_ssh_keys_success(self, mock_chmod, mock_run, ssh_key_manager):
        """Test successful SSH key generation."""
        mock_run.return_value.returncode = 0

        with patch.object(ssh_key_manager, "get_key_info") as mock_get_info:
            mock_get_info.return_value = SSHKeyInfo(
                public_key_path="/test/key.pub",
                private_key_path="/test/key",
                key_type="ed25519",
                exists=True,
            )

            key_info = ssh_key_manager.generate_ssh_keys("ed25519")

            assert key_info.exists is True
            mock_run.assert_called_once()
            mock_chmod.assert_called()

    @patch("subprocess.run")
    def test_generate_ssh_keys_failure(self, mock_run, ssh_key_manager):
        """Test SSH key generation failure."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "ssh-keygen", stderr="Error"
        )

        with pytest.raises(RuntimeError, match="SSH key generation failed"):
            ssh_key_manager.generate_ssh_keys("ed25519")

    @patch("pathlib.Path.exists")
    def test_generate_ssh_keys_exists_no_force(self, mock_exists, ssh_key_manager):
        """Test SSH key generation when key exists and force=False."""
        mock_exists.return_value = True

        with pytest.raises(FileExistsError):
            ssh_key_manager.generate_ssh_keys("ed25519", force_overwrite=False)

    @patch("clustrix.notebook_magic_ssh.PARAMIKO_AVAILABLE", False)
    def test_deploy_public_key_no_paramiko(self, ssh_key_manager):
        """Test deploying public key without paramiko."""
        with pytest.raises(ImportError):
            ssh_key_manager.deploy_public_key("host", "user", "/key.pub", "pass")

    @patch("clustrix.notebook_magic_ssh.PARAMIKO_AVAILABLE", True)
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="ssh-ed25519 AAAAC3... test@host",
    )
    @patch("clustrix.notebook_magic_ssh.paramiko")
    def test_deploy_public_key_success(self, mock_paramiko, mock_file, ssh_key_manager):
        """Test successful public key deployment."""
        mock_client = Mock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_client.exec_command.return_value = (None, Mock(), Mock())
        mock_client.exec_command.return_value[
            1
        ].channel.recv_exit_status.return_value = 0

        result = ssh_key_manager.deploy_public_key("host", "user", "/key.pub", "pass")

        assert result is True
        mock_client.connect.assert_called_once()
        mock_client.close.assert_called_once()

    @patch("clustrix.notebook_magic_ssh.PARAMIKO_AVAILABLE", True)
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_deploy_public_key_missing_file(self, mock_file, ssh_key_manager):
        """Test deploying public key with missing file."""
        with pytest.raises(FileNotFoundError):
            ssh_key_manager.deploy_public_key(
                "host", "user", "/nonexistent.pub", "pass"
            )

    @patch("subprocess.run")
    def test_get_key_fingerprint_success(self, mock_run, ssh_key_manager):
        """Test getting key fingerprint successfully."""
        mock_run.return_value.stdout = "2048 SHA256:test /path/to/key.pub (ED25519)\n"

        fingerprint = ssh_key_manager._get_key_fingerprint("/key.pub")

        assert fingerprint == "SHA256:test"

    @patch("subprocess.run")
    def test_get_key_fingerprint_failure(self, mock_run, ssh_key_manager):
        """Test getting key fingerprint failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ssh-keygen")

        fingerprint = ssh_key_manager._get_key_fingerprint("/key.pub")

        assert fingerprint == "Unknown"

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="ssh-ed25519 AAAAC3... test@host\n",
    )
    def test_get_key_comment_success(self, mock_file, ssh_key_manager):
        """Test getting key comment successfully."""
        comment = ssh_key_manager._get_key_comment("/key.pub")
        assert comment == "test@host"

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_get_key_comment_missing_file(self, mock_file, ssh_key_manager):
        """Test getting key comment with missing file."""
        comment = ssh_key_manager._get_key_comment("/nonexistent.pub")
        assert comment == ""


class TestSSHConfigManager:
    """Test SSH configuration management."""

    @pytest.fixture
    def ssh_config_manager(self):
        """SSH config manager instance."""
        return SSHConfigManager()

    @patch("pathlib.Path.exists")
    def test_read_config_no_file(self, mock_exists, ssh_config_manager):
        """Test reading config when file doesn't exist."""
        mock_exists.return_value = False

        config = ssh_config_manager.read_config()

        assert config == {}

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="""Host example
    HostName example.com
    User testuser
    Port 22

Host another
    HostName another.com
    User otheruser
""",
    )
    @patch("pathlib.Path.exists")
    def test_read_config_success(self, mock_exists, mock_file, ssh_config_manager):
        """Test reading config successfully."""
        mock_exists.return_value = True

        config = ssh_config_manager.read_config()

        assert "example" in config
        assert config["example"]["hostname"] == "example.com"
        assert config["example"]["user"] == "testuser"
        assert config["example"]["port"] == "22"
        assert "another" in config

    @patch.object(SSHConfigManager, "read_config")
    @patch.object(SSHConfigManager, "_write_config")
    def test_add_host_config_new(self, mock_write, mock_read, ssh_config_manager):
        """Test adding new host configuration."""
        mock_read.return_value = {}
        mock_write.return_value = None

        options = {"hostname": "example.com", "user": "testuser"}
        result = ssh_config_manager.add_host_config("example", options)

        assert result is True
        mock_write.assert_called_once()

    @patch.object(SSHConfigManager, "read_config")
    def test_add_host_config_exists_no_overwrite(self, mock_read, ssh_config_manager):
        """Test adding host config when it exists and overwrite=False."""
        mock_read.return_value = {"example": {"hostname": "existing.com"}}

        options = {"hostname": "example.com", "user": "testuser"}
        result = ssh_config_manager.add_host_config("example", options, overwrite=False)

        assert result is False

    @patch.object(SSHConfigManager, "read_config")
    @patch.object(SSHConfigManager, "_write_config")
    def test_remove_host_config_success(
        self, mock_write, mock_read, ssh_config_manager
    ):
        """Test removing host configuration successfully."""
        mock_read.return_value = {"example": {"hostname": "example.com"}}
        mock_write.return_value = None

        result = ssh_config_manager.remove_host_config("example")

        assert result is True
        mock_write.assert_called_once()

    @patch.object(SSHConfigManager, "read_config")
    def test_remove_host_config_not_exists(self, mock_read, ssh_config_manager):
        """Test removing non-existent host configuration."""
        mock_read.return_value = {}

        result = ssh_config_manager.remove_host_config("nonexistent")

        assert result is False

    @patch("tempfile.NamedTemporaryFile")
    @patch("shutil.move")
    @patch("pathlib.Path.chmod")
    def test_write_config(
        self, mock_chmod, mock_move, mock_tempfile, ssh_config_manager
    ):
        """Test writing SSH config file."""
        mock_file = Mock()
        mock_tempfile.return_value.__enter__.return_value = mock_file
        mock_file.name = "/tmp/test"

        config = {
            "example": {"hostname": "example.com", "user": "testuser"},
            "another": {"hostname": "another.com"},
        }

        ssh_config_manager._write_config(config)

        mock_file.write.assert_called()
        mock_move.assert_called_once()
        mock_chmod.assert_called_once()


class TestSSHUtilityFunctions:
    """Test SSH utility functions."""

    @patch("clustrix.notebook_magic_ssh.SSHKeyManager")
    def test_generate_ssh_keys_function(self, mock_manager_class):
        """Test generate_ssh_keys utility function."""
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager
        mock_key_info = SSHKeyInfo(
            public_key_path="/test/key.pub",
            private_key_path="/test/key",
            key_type="ed25519",
            exists=True,
        )
        mock_manager.generate_ssh_keys.return_value = mock_key_info

        result = generate_ssh_keys("ed25519", "/test/key")

        assert result == mock_key_info
        mock_manager.generate_ssh_keys.assert_called_once_with(
            key_type="ed25519",
            key_path="/test/key",
            comment=None,
            force_overwrite=False,
        )

    @patch("clustrix.notebook_magic_ssh.SSHKeyManager")
    @patch("clustrix.notebook_magic_ssh.SSHConfigManager")
    def test_manage_ssh_keychain_generate(self, mock_config_manager, mock_key_manager):
        """Test SSH keychain management - generate action."""
        mock_manager = Mock()
        mock_key_manager.return_value = mock_manager
        mock_key_info = SSHKeyInfo(
            public_key_path="/test/key.pub",
            private_key_path="/test/key",
            key_type="ed25519",
            exists=True,
        )
        mock_manager.generate_ssh_keys.return_value = mock_key_info

        result = manage_ssh_keychain("generate", "example.com", key_type="ed25519")

        assert result["success"] is True
        assert "key_info" in result["data"]

    @patch("clustrix.notebook_magic_ssh.SSHKeyManager")
    def test_manage_ssh_keychain_unknown_action(self, mock_key_manager):
        """Test SSH keychain management - unknown action."""
        result = manage_ssh_keychain("unknown", "example.com")

        assert result["success"] is False
        assert "Unknown action" in result["message"]

    @patch("clustrix.notebook_magic_ssh.PARAMIKO_AVAILABLE", False)
    def test_validate_ssh_connectivity_no_paramiko(self):
        """Test SSH connectivity validation without paramiko."""
        result = validate_ssh_connectivity("example.com", "user")

        assert result["connection_test"] is False
        assert "paramiko not available" in result["error_messages"]

    @patch("clustrix.notebook_magic_ssh.PARAMIKO_AVAILABLE", True)
    @patch("pathlib.Path.exists")
    def test_validate_ssh_connectivity_no_keys(self, mock_exists):
        """Test SSH connectivity validation with no SSH keys."""
        mock_exists.return_value = False

        result = validate_ssh_connectivity("example.com", "user")

        assert result["connection_test"] is False
        assert "No SSH private key found" in result["error_messages"]

    @patch("clustrix.notebook_magic_ssh.SSHConfigManager")
    def test_setup_ssh_config(self, mock_config_manager):
        """Test SSH config setup."""
        mock_manager = Mock()
        mock_config_manager.return_value = mock_manager
        mock_manager.add_host_config.return_value = True

        options = {"hostname": "example.com", "user": "testuser"}
        result = setup_ssh_config("example", options)

        assert result is True
        mock_manager.add_host_config.assert_called_once_with(
            "example", options, overwrite=True
        )

    @patch("pathlib.Path.exists")
    def test_list_ssh_keys_no_dir(self, mock_exists):
        """Test listing SSH keys when directory doesn't exist."""
        mock_exists.return_value = False

        keys = list_ssh_keys("/nonexistent")

        assert keys == []

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    @patch("pathlib.Path.is_file")
    @patch("clustrix.notebook_magic_ssh.SSHKeyManager")
    def test_list_ssh_keys_success(
        self, mock_manager_class, mock_is_file, mock_glob, mock_exists
    ):
        """Test listing SSH keys successfully."""
        mock_exists.return_value = True

        # Mock key files
        mock_key_file = Mock()
        mock_key_file.name = "id_ed25519"
        mock_is_file.return_value = True
        mock_glob.return_value = [mock_key_file]

        # Mock key manager
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager
        mock_key_info = SSHKeyInfo(
            public_key_path="/test/id_ed25519.pub",
            private_key_path="/test/id_ed25519",
            key_type="ed25519",
            exists=True,
        )
        mock_manager.get_key_info.return_value = mock_key_info

        keys = list_ssh_keys("/test/.ssh")

        # The mock returns twice due to glob pattern matching
        assert len(keys) >= 1
        assert all(key.key_type == "ed25519" for key in keys)
        assert all(key.exists for key in keys)


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple components."""

    @patch("clustrix.notebook_magic_enhanced.IPYTHON_AVAILABLE", True)
    def test_enhanced_ui_theme_switching(self):
        """Test complete theme switching workflow."""
        # Create theme manager
        theme_manager = ThemeManager()

        # Test initial state
        assert theme_manager.current_theme == "light"
        light_colors = theme_manager.get_current_theme()

        # Switch to dark theme
        success = theme_manager.set_theme("dark")
        assert success is True

        dark_colors = theme_manager.get_current_theme()
        assert dark_colors != light_colors
        assert dark_colors["background"] == "#121212"

        # Generate CSS for dark theme
        css = theme_manager.get_theme_css()
        assert "#121212" in css

    def test_ssh_key_lifecycle(self):
        """Test complete SSH key lifecycle."""
        config = ClusterConfig(
            cluster_type="ssh", cluster_host="test.com", username="user"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            ssh_dir = Path(temp_dir) / ".ssh"
            ssh_dir.mkdir()

            key_manager = SSHKeyManager(config)
            key_manager.ssh_dir = ssh_dir

            # Test key info for non-existent key
            key_path = str(ssh_dir / "test_key")
            key_info = key_manager.get_key_info(key_path)
            assert key_info.exists is False

            # Test SSH config management
            config_manager = SSHConfigManager()
            config_manager.config_path = ssh_dir / "config"

            # Add host config
            options = {"hostname": "test.com", "user": "testuser", "port": "22"}
            result = config_manager.add_host_config("test", options)
            assert result is True

            # Read back config
            read_config = config_manager.read_config()
            assert "test" in read_config
            assert read_config["test"]["hostname"] == "test.com"

    @patch("builtins.print")
    def test_progress_indicator_complete_workflow(self, mock_print):
        """Test complete progress indicator workflow."""
        mock_output = Mock()
        mock_output.clear_output = Mock()
        # Make it support context manager protocol
        mock_output.__enter__ = Mock(return_value=mock_output)
        mock_output.__exit__ = Mock(return_value=None)
        progress = ProgressIndicator(mock_output)

        # Start operation
        progress.start_operation("Test Workflow", 3)
        assert progress.current_operation == "Test Workflow"
        assert progress.total_steps == 3

        # Update progress
        progress.update_progress(1, "Step 1 complete")
        assert progress.current_progress == 1

        progress.update_progress(2, "Step 2 complete")
        assert progress.current_progress == 2

        # Complete operation
        progress.complete_operation(True, "All steps completed")

        # Verify print calls were made
        assert mock_print.called
        print_calls = [call.args[0] for call in mock_print.call_args_list]
        assert any("🚀" in call for call in print_calls)
        assert any("✅" in call for call in print_calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
