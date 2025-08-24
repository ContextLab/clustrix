"""
IPython magic command and widget for Clustrix configuration management.

This module provides a %%clusterfy magic command that creates an interactive
widget for managing cluster configurations in Jupyter notebooks. The widget
also displays automatically when clustrix is imported in a notebook environment.

This module has been refactored for better maintainability. The implementation
is now split across several focused modules:

- notebook_magic_config: Default configurations and config utilities
- notebook_magic_widget: The main EnhancedClusterConfigWidget class
- notebook_magic_core: Core magic functionality and IPython extension
- notebook_magic_mocks: Mock classes for non-IPython environments
"""

# Import all functionality from the refactored modules to maintain backward compatibility

# Core IPython functionality
from .notebook_magic_core import (
    display_config_widget,
    auto_display_on_import,
    ClusterfyMagics,
    load_ipython_extension,
)

# Configuration management
from .notebook_magic_config import (
    DEFAULT_CONFIGS,
    detect_config_files,
    load_config_from_file,
    validate_ip_address,
    validate_hostname,
)

# Widget functionality
from .notebook_magic_widget import EnhancedClusterConfigWidget

# IPython availability check and imports
try:
    from IPython.core.magic import Magics, magics_class, cell_magic
    from IPython.display import display as _display, HTML as _HTML
    import ipywidgets as _widgets  # type: ignore
    from IPython import get_ipython

    IPYTHON_AVAILABLE = True
    # Make functions available at module level for testing
    display = _display
    HTML = _HTML
    widgets = _widgets
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import (
        Magics,
        magics_class,
        cell_magic,
        display,
        get_ipython,
        HTML,
        widgets,
    )


# For backward compatibility, ensure all original functions and classes
# are available at the module level
__all__ = [
    # Core functions
    "display_config_widget",
    "auto_display_on_import",
    "load_ipython_extension",
    # Configuration
    "DEFAULT_CONFIGS",
    "detect_config_files",
    "load_config_from_file",
    "validate_ip_address",
    "validate_hostname",
    # Widget class
    "EnhancedClusterConfigWidget",
    # Magic class
    "ClusterfyMagics",
    # IPython components (may be mocks)
    "Magics",
    "magics_class",
    "cell_magic",
    "display",
    "HTML",
    "widgets",
    "get_ipython",
    "IPYTHON_AVAILABLE",
]
