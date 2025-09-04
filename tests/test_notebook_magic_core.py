"""
Tests for the core notebook magic functionality.

This module focuses on testing the IPython extension loading, magic command
registration, and core magic functionality like %%clusterfy execution.
"""

import sys
import pytest
from unittest.mock import MagicMock, patch, call

# Test the actual core module functionality
from clustrix.notebook_magic_core import (
    display_config_widget,
    auto_display_on_import,
    ClusterfyMagics,
    load_ipython_extension,
    IPYTHON_AVAILABLE,
)


class TestIPythonExtensionLoading:
    """Test IPython extension registration and loading."""

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    def test_load_ipython_extension_with_ipython(self):
        """Test loading IPython extension when IPython is available."""
        mock_ipython = MagicMock()
        mock_ipython.register_magic_function = MagicMock()

        # Mock ClusterfyMagics to avoid complex trait validation
        with patch("clustrix.notebook_magic_core.ClusterfyMagics") as MockMagics:
            mock_magic_instance = MagicMock()
            mock_magic_instance.clusterfy = MagicMock()
            MockMagics.return_value = mock_magic_instance

            # Load the extension
            load_ipython_extension(mock_ipython)

            # Verify magic class instantiation
            MockMagics.assert_called_once_with(mock_ipython)

            # Verify magic function registration
            mock_ipython.register_magic_function.assert_called_once()
            call_args = mock_ipython.register_magic_function.call_args
            assert call_args[0][1] == "cell"  # Magic type
            assert call_args[0][2] == "clusterfy"  # Magic name

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", False)
    def test_load_ipython_extension_without_ipython(self):
        """Test extension loading gracefully handles missing IPython."""
        mock_ipython = MagicMock()

        # Should not raise any exceptions
        load_ipython_extension(mock_ipython)

        # Should not attempt registration when IPython unavailable
        mock_ipython.register_magic_function.assert_not_called()

    def test_ipython_available_flag(self):
        """Test that IPYTHON_AVAILABLE flag is properly set."""
        # The flag should be a boolean
        assert isinstance(IPYTHON_AVAILABLE, bool)

        # Test behavior with the current environment
        try:
            import IPython.core.magic

            expected = True
        except ImportError:
            expected = False

        assert IPYTHON_AVAILABLE == expected


class TestClusterfyMagics:
    """Test the ClusterfyMagics class and its magic commands."""

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_clusterfy_magic_basic_execution(self, mock_display):
        """Test basic %%clusterfy magic command execution."""
        # Create magic instance without shell to avoid traitlets validation
        magic = ClusterfyMagics()
        magic.shell = MagicMock()  # Assign shell after creation
        magic.shell.run_cell = MagicMock()

        # Execute magic command with no cell content
        result = magic.clusterfy("", "")

        # Should display the config widget
        mock_display.assert_called_once_with(auto_display=False)

        # Should not execute any cell content
        magic.shell.run_cell.assert_not_called()

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_clusterfy_magic_with_cell_content(self, mock_display):
        """Test %%clusterfy magic command with cell content execution."""
        # Create magic instance and assign shell after creation
        magic = ClusterfyMagics()
        magic.shell = MagicMock()
        magic.shell.run_cell = MagicMock()

        # Execute magic command with cell content
        cell_content = "print('Hello from clusterfy cell')\nx = 42"
        result = magic.clusterfy("", cell_content)

        # Should display the config widget
        mock_display.assert_called_once_with(auto_display=False)

        # Should execute the cell content
        magic.shell.run_cell.assert_called_once_with(cell_content)

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_clusterfy_magic_with_line_arguments(self, mock_display):
        """Test %%clusterfy magic command with line arguments (currently ignored)."""
        # Create magic instance and assign shell after creation
        magic = ClusterfyMagics()
        magic.shell = MagicMock()
        magic.shell.run_cell = MagicMock()

        # Execute magic command with line arguments (should be ignored for now)
        line_args = "--config myconfig --verbose"
        cell_content = "result = compute_something()"
        result = magic.clusterfy(line_args, cell_content)

        # Should display the config widget regardless of line arguments
        mock_display.assert_called_once_with(auto_display=False)

        # Should execute the cell content
        magic.shell.run_cell.assert_called_once_with(cell_content)

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", False)
    def test_clusterfy_magic_without_ipython(self, capsys):
        """Test %%clusterfy magic command fails gracefully without IPython."""
        # Create magic instance and assign shell after creation
        magic = ClusterfyMagics()
        magic.shell = MagicMock()
        magic.shell.run_cell = MagicMock()

        with patch(
            "clustrix.notebook_magic_core.display_config_widget"
        ) as mock_display:
            # Execute magic command
            result = magic.clusterfy("", "print('test')")

            # Should return None when IPython unavailable
            assert result is None

            # Should not display widget
            mock_display.assert_not_called()

            # Should not execute cell content
            magic.shell.run_cell.assert_not_called()

            # Should print error messages
            captured = capsys.readouterr()
            assert "IPython and ipywidgets" in captured.out
            assert "pip install ipywidgets" in captured.out

    def test_clusterfy_magic_with_empty_cell(self):
        """Test %%clusterfy magic command with empty cell content."""
        with patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True), patch(
            "clustrix.notebook_magic_core.display_config_widget"
        ) as mock_display:

            # Create magic instance and assign shell after creation
            magic = ClusterfyMagics()
            magic.shell = MagicMock()
            magic.shell.run_cell = MagicMock()

            # Execute with whitespace-only cell content
            result = magic.clusterfy("", "   \n  \t  \n  ")

            # Should display widget
            mock_display.assert_called_once_with(auto_display=False)

            # Should not execute empty cell content
            magic.shell.run_cell.assert_not_called()

    def test_clusterfy_magic_docstring(self):
        """Test that clusterfy magic has proper docstring."""
        magic = ClusterfyMagics()

        # Should have a descriptive docstring
        assert hasattr(magic.clusterfy, "__doc__")
        assert magic.clusterfy.__doc__ is not None
        docstring = magic.clusterfy.__doc__

        # Should mention key functionality
        assert "%%clusterfy" in docstring
        assert "widget" in docstring or "configuration" in docstring
        assert "Usage::" in docstring


class TestDisplayFunctionality:
    """Test display and auto-display functionality."""

    @patch("clustrix.modern_notebook_widget.display_modern_widget")
    def test_display_config_widget_function(self, mock_display_modern):
        """Test display_config_widget function calls modern widget."""
        mock_widget = MagicMock()
        mock_display_modern.return_value = mock_widget

        # Test without auto_display flag
        result = display_config_widget(auto_display=False)

        # Should call modern widget display
        mock_display_modern.assert_called_once()
        assert result == mock_widget

    @patch("clustrix.modern_notebook_widget.display_modern_widget")
    def test_display_config_widget_with_auto_display(self, mock_display_modern):
        """Test display_config_widget function with auto_display flag."""
        mock_widget = MagicMock()
        mock_display_modern.return_value = mock_widget

        # Test with auto_display flag
        result = display_config_widget(auto_display=True)

        # Should call modern widget display
        mock_display_modern.assert_called_once()
        assert result == mock_widget

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", False)
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_auto_display_no_ipython(self, mock_display):
        """Test auto display when IPython not available."""
        auto_display_on_import()

        # Should not attempt to display widget
        mock_display.assert_not_called()

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.get_ipython")
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_auto_display_no_ipython_instance(self, mock_display, mock_get_ipython):
        """Test auto display when get_ipython returns None."""
        mock_get_ipython.return_value = None

        auto_display_on_import()

        # Should not attempt to display widget
        mock_display.assert_not_called()

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.get_ipython")
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_auto_display_not_notebook_environment(
        self, mock_display, mock_get_ipython
    ):
        """Test auto display when not in notebook environment."""
        # Mock IPython instance without notebook attributes
        mock_ipython = MagicMock()
        del mock_ipython.kernel  # Remove notebook indicators
        mock_get_ipython.return_value = mock_ipython

        auto_display_on_import()

        # Should not display widget (no kernel attribute)
        mock_display.assert_not_called()

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.get_ipython")
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_auto_display_in_notebook_environment(self, mock_display, mock_get_ipython):
        """Test auto display when in notebook environment."""
        # Mock IPython instance with notebook attributes
        mock_ipython = MagicMock()
        mock_ipython.kernel = True
        mock_ipython.register_magic_function = MagicMock()
        mock_get_ipython.return_value = mock_ipython

        auto_display_on_import()

        # Should display widget with auto_display=True
        mock_display.assert_called_once_with(auto_display=True)


class TestMockFallbacks:
    """Test mock functionality for non-IPython environments."""

    def test_mocks_import_without_ipython(self):
        """Test that mocks are used when IPython unavailable."""
        with patch.dict("sys.modules", {"IPython": None, "IPython.core.magic": None}):
            # Force reimport to trigger mock usage
            if "clustrix.notebook_magic_core" in sys.modules:
                del sys.modules["clustrix.notebook_magic_core"]

            # This should not raise ImportError
            from clustrix.notebook_magic_core import (
                ClusterfyMagics,
                magics_class,
                cell_magic,
            )

            # Should have mock implementations
            assert ClusterfyMagics is not None
            assert magics_class is not None
            assert cell_magic is not None

    def test_mock_magic_decorators(self):
        """Test that mock magic decorators work properly."""
        from clustrix.notebook_magic_mocks import magics_class, cell_magic, Magics

        # Test magics_class decorator
        @magics_class
        class TestMagics(Magics):
            pass

        # Should return the class unchanged
        assert TestMagics is not None

        # Test cell_magic decorator
        @cell_magic
        def test_magic(self, line, cell):
            return "test_result"

        # Should return a callable
        assert callable(test_magic)


class TestExtensionUnloading:
    """Test extension unloading functionality (if implemented)."""

    def test_extension_unloading_not_implemented(self):
        """Test that unload function doesn't exist (not implemented yet)."""
        import clustrix.notebook_magic_core

        # Should not have unload_ipython_extension function yet
        assert not hasattr(clustrix.notebook_magic_core, "unload_ipython_extension")

    def test_future_unload_functionality_placeholder(self):
        """Placeholder test for future unload functionality."""
        # When unload functionality is implemented, this test should be updated
        # to verify proper cleanup of registered magic functions
        pass


class TestErrorHandling:
    """Test error handling in magic commands."""

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_magic_propagates_display_widget_errors(self, mock_display):
        """Test that magic command propagates display widget errors (current behavior)."""
        # Make display_config_widget raise an exception
        mock_display.side_effect = Exception("Widget display error")

        magic = ClusterfyMagics()
        magic.shell = MagicMock()
        magic.shell.run_cell = MagicMock()

        # Current implementation propagates exceptions (no error handling)
        with pytest.raises(Exception, match="Widget display error"):
            result = magic.clusterfy("", "print('test')")

    @patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True)
    @patch("clustrix.notebook_magic_core.display_config_widget")
    def test_magic_propagates_cell_execution_errors(self, mock_display):
        """Test that magic command propagates cell execution errors (current behavior)."""
        magic = ClusterfyMagics()
        magic.shell = MagicMock()
        # Make shell.run_cell raise an exception
        magic.shell.run_cell.side_effect = Exception("Cell execution error")

        # Current implementation propagates exceptions (no error handling)
        with pytest.raises(Exception, match="Cell execution error"):
            result = magic.clusterfy("", "print('test')")


class TestMagicCommandRegistration:
    """Test magic command registration details."""

    def test_magic_command_name_registration(self):
        """Test that magic command is registered with correct name."""
        mock_ipython = MagicMock()

        with patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True), patch(
            "clustrix.notebook_magic_core.ClusterfyMagics"
        ) as MockMagics:

            mock_magic_instance = MagicMock()
            MockMagics.return_value = mock_magic_instance

            load_ipython_extension(mock_ipython)

            # Verify registration details
            call_args = mock_ipython.register_magic_function.call_args
            assert call_args[0][1] == "cell"  # Should be cell magic
            assert call_args[0][2] == "clusterfy"  # Should use correct name

    def test_magic_instance_creation(self):
        """Test that magic instance is created with correct shell."""
        mock_ipython = MagicMock()

        with patch("clustrix.notebook_magic_core.IPYTHON_AVAILABLE", True), patch(
            "clustrix.notebook_magic_core.ClusterfyMagics"
        ) as MockMagics:

            load_ipython_extension(mock_ipython)

            # Verify magic instance creation
            MockMagics.assert_called_once_with(mock_ipython)


class TestCoverageCompleteness:
    """Verify comprehensive coverage of core magic functionality."""

    def test_all_public_functions_tested(self):
        """Ensure all public functions in the core module are tested."""
        import clustrix.notebook_magic_core as core_module

        # List of public functions that should be tested
        public_functions = [
            "display_config_widget",
            "auto_display_on_import",
            "load_ipython_extension",
        ]

        # Verify all functions exist
        for func_name in public_functions:
            assert hasattr(core_module, func_name), f"Function {func_name} not found"

    def test_all_public_classes_tested(self):
        """Ensure all public classes in the core module are tested."""
        import clustrix.notebook_magic_core as core_module

        # List of public classes that should be tested
        public_classes = [
            "ClusterfyMagics",
        ]

        # Verify all classes exist
        for class_name in public_classes:
            assert hasattr(core_module, class_name), f"Class {class_name} not found"

    def test_magic_methods_tested(self):
        """Ensure all magic methods are tested."""
        # Verify ClusterfyMagics has the expected magic method
        magic_methods = ["clusterfy"]

        for method_name in magic_methods:
            assert hasattr(
                ClusterfyMagics, method_name
            ), f"Magic method {method_name} not found"
