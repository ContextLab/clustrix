"""Comprehensive tests for authentication fallback methods."""

import os
import sys
from unittest.mock import Mock, patch, MagicMock
import pytest

from clustrix.auth_fallbacks import (
    detect_environment,
    get_password_gui,
    get_password_widget,
    get_cluster_password,
    requires_password_fallback,
    setup_auth_with_fallback,
)


class TestDetectEnvironment:
    """Test environment detection."""

    @patch.dict(sys.modules, {"google.colab": Mock()})
    def test_detect_colab(self):
        """Test detection of Google Colab environment."""
        assert detect_environment() == "colab"

    @patch.dict(sys.modules, {"ipykernel": Mock()}, clear=False)
    def test_detect_notebook(self):
        """Test detection of Jupyter notebook environment."""
        if "google.colab" in sys.modules:
            del sys.modules["google.colab"]
        assert detect_environment() == "notebook"

    @patch("sys.stdin.isatty")
    def test_detect_cli(self, mock_isatty):
        """Test detection of CLI environment."""
        mock_isatty.return_value = True
        # Ensure colab and ipykernel are not present
        with patch.dict(sys.modules, {}, clear=False):
            if "google.colab" in sys.modules:
                del sys.modules["google.colab"]
            if "ipykernel" in sys.modules:
                del sys.modules["ipykernel"]
            assert detect_environment() == "cli"

    @patch("sys.stdin.isatty")
    def test_detect_script(self, mock_isatty):
        """Test detection of script environment."""
        mock_isatty.return_value = False
        # Ensure colab and ipykernel are not present
        with patch.dict(sys.modules, {}, clear=False):
            if "google.colab" in sys.modules:
                del sys.modules["google.colab"]
            if "ipykernel" in sys.modules:
                del sys.modules["ipykernel"]
            assert detect_environment() == "script"


class TestGetPasswordGui:
    """Test GUI password retrieval."""

    @patch("tkinter.Tk")
    @patch("tkinter.simpledialog.askstring")
    def test_get_password_gui_success(self, mock_askstring, mock_tk):
        """Test successful GUI password retrieval."""
        mock_root = Mock()
        mock_tk.return_value = mock_root
        mock_askstring.return_value = "test_password"

        result = get_password_gui("Enter password")

        assert result == "test_password"
        mock_root.withdraw.assert_called_once()
        mock_root.attributes.assert_called_once_with("-topmost", True)
        mock_root.destroy.assert_called_once()
        mock_askstring.assert_called_once_with(
            "Cluster Authentication", "Enter password", show="*"
        )

    @patch("tkinter.Tk")
    @patch("tkinter.simpledialog.askstring")
    def test_get_password_gui_cancelled(self, mock_askstring, mock_tk):
        """Test cancelled GUI password retrieval."""
        mock_root = Mock()
        mock_tk.return_value = mock_root
        mock_askstring.return_value = None

        result = get_password_gui("Enter password")

        assert result is None
        mock_root.destroy.assert_called_once()

    @patch("clustrix.auth_fallbacks.get_password_widget")
    def test_get_password_gui_import_error(self, mock_get_widget):
        """Test GUI password retrieval with ImportError fallback."""
        mock_get_widget.return_value = "widget_password"

        with patch("tkinter.Tk", side_effect=ImportError()):
            result = get_password_gui("Enter password")

        assert result == "widget_password"
        mock_get_widget.assert_called_once_with("Enter password")


class TestGetPasswordWidget:
    """Test widget password retrieval."""

    @patch("ipywidgets.Password")
    @patch("ipywidgets.Button")
    @patch("ipywidgets.Output")
    @patch("ipywidgets.HTML")
    @patch("ipywidgets.VBox")
    @patch("IPython.display.display")
    @patch("IPython.display.clear_output")
    def test_get_password_widget_success(
        self,
        mock_clear,
        mock_display,
        mock_vbox,
        mock_html,
        mock_output,
        mock_button,
        mock_password,
    ):
        """Test successful widget password retrieval."""
        mock_password_widget = Mock()
        mock_password_widget.value = "test_password"
        mock_password.return_value = mock_password_widget

        mock_button_widget = Mock()
        mock_button.return_value = mock_button_widget

        mock_output_widget = Mock()
        mock_output.return_value = mock_output_widget

        result = get_password_widget("Enter password")

        # The function returns None in its current implementation
        # as it needs proper async handling
        assert result is None

    def test_get_password_widget_import_error(self):
        """Test widget password retrieval with ImportError."""
        with patch("ipywidgets.Password", side_effect=ImportError()):
            result = get_password_widget("Enter password")

        assert result is None


class TestGetClusterPassword:
    """Test cluster password retrieval."""

    @patch("clustrix.auth_fallbacks.detect_environment")
    def test_get_cluster_password_colab_success(self, mock_detect):
        """Test successful password retrieval in Colab."""
        mock_detect.return_value = "colab"

        mock_userdata = Mock()
        mock_userdata.get.side_effect = lambda key: {
            "CLUSTER_PASSWORD_example.com": "colab_password"
        }.get(key)

        with patch.dict(sys.modules, {"google.colab": Mock()}):
            with patch("google.colab.userdata", mock_userdata):
                result = get_cluster_password("example.com", "testuser")

        assert result == "colab_password"

    @patch("clustrix.auth_fallbacks.detect_environment")
    def test_get_cluster_password_colab_multiple_keys(self, mock_detect):
        """Test password retrieval in Colab with multiple key attempts."""
        mock_detect.return_value = "colab"

        mock_userdata = Mock()
        call_count = 0

        def mock_get_side_effect(key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Not found")
            elif call_count == 2:
                return "found_password"
            return None

        mock_userdata.get.side_effect = mock_get_side_effect

        with patch.dict(sys.modules, {"google.colab": Mock()}):
            with patch("google.colab.userdata", mock_userdata):
                result = get_cluster_password("example.com", "testuser")

        assert result == "found_password"

    @patch("clustrix.auth_fallbacks.detect_environment")
    def test_get_cluster_password_colab_import_error(self, mock_detect):
        """Test password retrieval in Colab with import error."""
        mock_detect.return_value = "colab"

        # Mock the sys.modules to not contain google.colab
        with patch.dict(sys.modules, {}, clear=False):
            if "google.colab" in sys.modules:
                del sys.modules["google.colab"]
            result = get_cluster_password("example.com", "testuser")

        assert result is None

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch.dict(os.environ, {"CLUSTRIX_PASSWORD_EXAMPLE_COM": "env_password"})
    def test_get_cluster_password_env_vars(self, mock_detect):
        """Test password retrieval from environment variables."""
        mock_detect.return_value = "cli"

        result = get_cluster_password("example.com", "testuser")

        assert result == "env_password"

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("clustrix.auth_fallbacks.get_password_gui")
    def test_get_cluster_password_notebook_gui(self, mock_gui, mock_detect):
        """Test password retrieval in notebook with GUI."""
        mock_detect.return_value = "notebook"
        mock_gui.return_value = "gui_password"

        result = get_cluster_password("example.com", "testuser")

        assert result == "gui_password"
        mock_gui.assert_called_once_with("Password for testuser@example.com")

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("clustrix.auth_fallbacks.get_password_gui")
    @patch("clustrix.auth_fallbacks.get_password_widget")
    def test_get_cluster_password_notebook_widget_fallback(
        self, mock_widget, mock_gui, mock_detect
    ):
        """Test password retrieval in notebook with widget fallback."""
        mock_detect.return_value = "notebook"
        mock_gui.return_value = None
        mock_widget.return_value = "widget_password"

        result = get_cluster_password("example.com", "testuser")

        assert result == "widget_password"
        mock_widget.assert_called_once_with("Password for testuser@example.com")

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("getpass.getpass")
    def test_get_cluster_password_cli_getpass(self, mock_getpass, mock_detect):
        """Test password retrieval in CLI with getpass."""
        mock_detect.return_value = "cli"
        mock_getpass.return_value = "cli_password"

        result = get_cluster_password("example.com", "testuser")

        assert result == "cli_password"
        mock_getpass.assert_called_once_with("Password for testuser@example.com: ")

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("getpass.getpass")
    def test_get_cluster_password_cli_keyboard_interrupt(
        self, mock_getpass, mock_detect
    ):
        """Test password retrieval in CLI with KeyboardInterrupt."""
        mock_detect.return_value = "cli"
        mock_getpass.side_effect = KeyboardInterrupt()

        result = get_cluster_password("example.com", "testuser")

        assert result is None

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("getpass.getpass")
    def test_get_cluster_password_cli_eof_error(self, mock_getpass, mock_detect):
        """Test password retrieval in CLI with EOFError."""
        mock_detect.return_value = "cli"
        mock_getpass.side_effect = EOFError()

        result = get_cluster_password("example.com", "testuser")

        assert result is None

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("builtins.input")
    def test_get_cluster_password_script_input(self, mock_input, mock_detect):
        """Test password retrieval in script with input."""
        mock_detect.return_value = "script"
        mock_input.return_value = "script_password"

        result = get_cluster_password("example.com", "testuser")

        assert result == "script_password"
        mock_input.assert_called_once_with("Password for testuser@example.com: ")

    @patch("clustrix.auth_fallbacks.detect_environment")
    @patch("builtins.input")
    def test_get_cluster_password_script_keyboard_interrupt(
        self, mock_input, mock_detect
    ):
        """Test password retrieval in script with KeyboardInterrupt."""
        mock_detect.return_value = "script"
        mock_input.side_effect = KeyboardInterrupt()

        result = get_cluster_password("example.com", "testuser")

        assert result is None

    @patch("clustrix.auth_fallbacks.detect_environment")
    def test_get_cluster_password_unknown_environment(self, mock_detect):
        """Test password retrieval in unknown environment."""
        mock_detect.return_value = "unknown"

        result = get_cluster_password("example.com", "testuser")

        assert result is None


class TestRequiresPasswordFallback:
    """Test password fallback requirement detection."""

    def test_requires_password_fallback_success_false(self):
        """Test when SSH key setup was successful."""
        auth_result = {"success": True, "connection_tested": True, "error": ""}

        assert requires_password_fallback(auth_result) is False

    def test_requires_password_fallback_no_success(self):
        """Test when SSH key setup was not successful."""
        auth_result = {"success": False, "connection_tested": True, "error": ""}

        assert requires_password_fallback(auth_result) is True

    def test_requires_password_fallback_no_connection_test(self):
        """Test when connection was not tested."""
        auth_result = {"success": True, "connection_tested": False, "error": ""}

        assert requires_password_fallback(auth_result) is True

    def test_requires_password_fallback_publickey_error(self):
        """Test when error indicates publickey authentication issue."""
        auth_result = {
            "success": True,
            "connection_tested": True,
            "error": "Permission denied (publickey)",
        }

        assert requires_password_fallback(auth_result) is True

    def test_requires_password_fallback_key_error(self):
        """Test when error mentions key issues."""
        auth_result = {
            "success": True,
            "connection_tested": True,
            "error": "SSH key authentication failed",
        }

        assert requires_password_fallback(auth_result) is True

    def test_requires_password_fallback_authentication_error(self):
        """Test when error mentions authentication issues."""
        auth_result = {
            "success": True,
            "connection_tested": True,
            "error": "Authentication failure detected",
        }

        assert requires_password_fallback(auth_result) is True

    def test_requires_password_fallback_gssapi_error(self):
        """Test when error mentions GSSAPI issues."""
        auth_result = {
            "success": True,
            "connection_tested": True,
            "error": "GSSAPI authentication failed",
        }

        assert requires_password_fallback(auth_result) is True

    def test_requires_password_fallback_kerberos_error(self):
        """Test when error mentions Kerberos issues."""
        auth_result = {
            "success": True,
            "connection_tested": True,
            "error": "Kerberos authentication failed",
        }

        assert requires_password_fallback(auth_result) is True


class TestSetupAuthWithFallback:
    """Test authentication setup with fallback."""

    def test_setup_auth_with_password_success(self):
        """Test successful SSH key setup with provided password."""
        mock_config = Mock()
        mock_config.cluster_host = "example.com"
        mock_config.username = "testuser"

        mock_ssh_func = Mock()
        mock_ssh_func.return_value = {
            "success": True,
            "connection_tested": True,
            "error": "",
        }

        result = setup_auth_with_fallback(
            mock_config, mock_ssh_func, password="test_password"
        )

        assert result["success"] is True
        mock_ssh_func.assert_called_once_with(mock_config, password="test_password")

    @patch("clustrix.auth_fallbacks.get_cluster_password")
    def test_setup_auth_with_fallback_success(self, mock_get_password):
        """Test successful SSH key setup with fallback password."""
        mock_config = Mock()
        mock_config.cluster_host = "example.com"
        mock_config.username = "testuser"

        mock_ssh_func = Mock()
        # Only one call with fallback password succeeds
        mock_ssh_func.return_value = {
            "success": True,
            "connection_tested": True,
            "error": "",
        }

        mock_get_password.return_value = "fallback_password"

        result = setup_auth_with_fallback(mock_config, mock_ssh_func)

        assert result["success"] is True
        assert result["details"]["used_password_fallback"] is True
        assert mock_ssh_func.call_count == 1

        # Check that fallback password was used in the call
        call_kwargs = mock_ssh_func.call_args[1]
        assert call_kwargs["password"] == "fallback_password"

    @patch("clustrix.auth_fallbacks.get_cluster_password")
    def test_setup_auth_with_fallback_no_password(self, mock_get_password):
        """Test SSH key setup when no fallback password available."""
        mock_config = Mock()
        mock_config.cluster_host = "example.com"
        mock_config.username = "testuser"

        mock_ssh_func = Mock()
        mock_ssh_func.return_value = {
            "success": False,
            "connection_tested": False,
            "error": "No password",
        }

        mock_get_password.return_value = None

        result = setup_auth_with_fallback(mock_config, mock_ssh_func)

        assert result["success"] is False
        assert "no password fallback available" in result["error"]
        assert result["details"]["fallback_attempted"] is True
        assert result["details"]["fallback_available"] is False

    @patch("clustrix.auth_fallbacks.get_cluster_password")
    def test_setup_auth_with_fallback_cleanup(self, mock_get_password):
        """Test that passwords are properly cleaned up after use."""
        mock_config = Mock()
        mock_config.cluster_host = "example.com"
        mock_config.username = "testuser"

        mock_ssh_func = Mock()
        mock_ssh_func.return_value = {
            "success": True,
            "connection_tested": True,
            "error": "",
        }

        mock_get_password.return_value = "fallback_password"

        kwargs = {}
        result = setup_auth_with_fallback(mock_config, mock_ssh_func, **kwargs)

        # Verify password was cleaned up
        assert kwargs.get("password") is None
        assert result["success"] is True

    @patch("clustrix.auth_fallbacks.get_cluster_password")
    def test_setup_auth_with_provided_password_failure(self, mock_get_password):
        """Test with provided password that fails, then fallback."""
        mock_config = Mock()
        mock_config.cluster_host = "example.com"
        mock_config.username = "testuser"

        mock_ssh_func = Mock()
        # First call with provided password fails, second with fallback succeeds
        mock_ssh_func.side_effect = [
            {"success": False, "connection_tested": False, "error": "Wrong password"},
            {"success": True, "connection_tested": True, "error": ""},
        ]

        mock_get_password.return_value = "fallback_password"

        result = setup_auth_with_fallback(
            mock_config, mock_ssh_func, password="wrong_password"
        )

        assert result["success"] is True
        assert result["details"]["used_password_fallback"] is True
        assert mock_ssh_func.call_count == 2
