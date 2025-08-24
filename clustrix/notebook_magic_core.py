"""
Core IPython magic functionality for Clustrix.

This module provides the main IPython magic commands and extension loading
functionality for the notebook magic interface.
"""

try:
    from IPython.core.magic import Magics, magics_class, cell_magic
    from IPython import get_ipython

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import Magics, magics_class, cell_magic, get_ipython


def display_config_widget(auto_display: bool = False):
    """Display the configuration widget."""
    from .modern_notebook_widget import display_modern_widget

    return display_modern_widget()


def auto_display_on_import():
    """Automatically display widget when clustrix is imported in a notebook."""
    if not IPYTHON_AVAILABLE:
        return
    ipython = get_ipython()
    if ipython is None:
        return
    # Check if we're in a notebook environment
    if hasattr(ipython, "kernel") and hasattr(ipython, "register_magic_function"):
        # Always display the widget on import
        display_config_widget(auto_display=True)


@magics_class
class ClusterfyMagics(Magics):
    """IPython magic commands for Clustrix."""

    @cell_magic
    def clusterfy(self, line, cell):
        """
        Create an interactive widget for managing Clustrix configurations.

        Usage::

            %%clusterfy

        This creates a widget interface that allows you to:

        - Select and manage cluster configurations
        - Create new configurations with validation
        - Save/load configurations from files
        - Apply configurations to the current session
        """
        if not IPYTHON_AVAILABLE:
            print("❌ This magic command requires IPython and ipywidgets")
            print("Install with: pip install ipywidgets")
            return None
        # Create and display the widget (not auto-display)
        display_config_widget(auto_display=False)
        # Execute any code in the cell (if provided)
        if cell.strip():
            self.shell.run_cell(cell)


def load_ipython_extension(ipython):
    """Load the extension in IPython."""
    if IPYTHON_AVAILABLE:
        ipython.register_magic_function(
            ClusterfyMagics(ipython).clusterfy, "cell", "clusterfy"
        )
        # Note: No print message since widget displays automatically on import
