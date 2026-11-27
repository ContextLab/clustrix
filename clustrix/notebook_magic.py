"""IPython integration for Clustrix: ``%clustrix``, ``%%remote`` and the
extension hook behind ``%load_ext clustrix``.

The implementation is in :mod:`clustrix.notebook_magic_core`; this module is
the documented import path for it.
"""

from .notebook_magic_core import (
    IPYTHON_AVAILABLE,
    ClusterfyMagics,
    auto_display_on_import,
    display_config_widget,
    load_ipython_extension,
)

__all__ = [
    "IPYTHON_AVAILABLE",
    "ClusterfyMagics",
    "auto_display_on_import",
    "display_config_widget",
    "load_ipython_extension",
]
