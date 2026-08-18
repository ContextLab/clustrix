"""
Core IPython magic functionality for Clustrix.

This module provides the main IPython magic commands and extension loading
functionality for the notebook magic interface.
"""

import os
import warnings

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


AUTO_WIDGET_ENV_VAR = "CLUSTRIX_AUTO_WIDGET"


def auto_display_on_import():
    """Display the configuration widget on import, if the user asked for it.

    This used to fire unconditionally, so ``import clustrix`` painted a
    configuration UI into the notebook whether or not the user wanted one --
    and a second copy appeared next to any explicit ``%%remote`` or
    ``.display()`` call, which is how it usually got noticed. A library
    should not inject UI as a side effect of being imported.

    The widget is now shown on demand: run ``%%remote`` in a cell, or call
    ``clustrix.notebook_magic.display_config_widget()``. Setting
    ``CLUSTRIX_AUTO_WIDGET=1`` restores the old display-on-import behaviour
    for anyone who relied on it.
    """
    if not IPYTHON_AVAILABLE:
        return
    if os.environ.get(AUTO_WIDGET_ENV_VAR, "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return
    ipython = get_ipython()
    if ipython is None:
        return
    # Check if we're in a notebook environment
    if hasattr(ipython, "kernel") and hasattr(ipython, "register_magic_function"):
        display_config_widget(auto_display=True)


@magics_class
class ClusterfyMagics(Magics):
    """IPython magic commands for Clustrix."""

    @cell_magic
    def remote(self, line, cell):
        """
        Create an interactive widget for managing Clustrix configurations.

        Usage::

            %%remote

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

    @cell_magic
    def clusterfy(self, line, cell):
        """Deprecated alias for ``%%remote``.

        Kept so that notebooks written against the old name keep running --
        a magic rename that silently breaks every published notebook is worse
        than carrying an alias. It warns once per call and then delegates.
        """
        warnings.warn(
            "%%clusterfy is deprecated; use %%remote instead. The old name "
            "still works but will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.remote(line, cell)


def load_ipython_extension(ipython):
    """Load the extension in IPython."""
    if IPYTHON_AVAILABLE:
        magics = ClusterfyMagics(ipython)
        ipython.register_magic_function(magics.remote, "cell", "remote")
        # Deprecated alias -- see ClusterfyMagics.clusterfy
        ipython.register_magic_function(magics.clusterfy, "cell", "clusterfy")
        # Note: No print message since widget displays automatically on import
