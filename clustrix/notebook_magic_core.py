"""
Core IPython magic functionality for Clustrix.

This module provides the main IPython magic commands and extension loading
functionality for the notebook magic interface.
"""

import os
import warnings
from dataclasses import asdict

try:
    from IPython.core.magic import Magics, magics_class, cell_magic, line_magic
    from IPython import get_ipython

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import (
        Magics,
        magics_class,
        cell_magic,
        line_magic,
        get_ipython,
    )


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

    @line_magic
    def clustrix(self, line):
        """Inspect and change the active configuration from a notebook.

        The cell magic opens the widget; this is the keyboard equivalent, for
        the things that do not need one::

            %clustrix status            what @cluster will do right now
            %clustrix config            the same, in full
            %clustrix config <profile>  make a saved profile active
            %clustrix load <file>       load configuration from a YAML/JSON file

        Tests have expected this since the notebook magic was written; it was
        never implemented, so `%clustrix` raised "Line magic function not
        found".
        """
        from .config import configure, get_config, load_config

        parts = line.split()
        command = parts[0] if parts else "status"
        argument = " ".join(parts[1:]) if len(parts) > 1 else ""

        if command in ("status", "config") and not argument:
            config = get_config()
            print(f"cluster_type : {config.cluster_type}")
            if config.cluster_host:
                print(f"host         : {config.cluster_host}")
            if config.username:
                print(f"username     : {config.username}")
            print(
                f"resources    : {config.default_cores} cores, "
                f"{config.default_memory}, {config.default_time}"
            )
            print(f"work dir     : {config.remote_work_dir}")
            return config

        if command == "load":
            if not argument:
                print("❌ Usage: %clustrix load <path-to-config-file>")
                return None
            load_config(argument)
            print(f"✅ Loaded configuration from {argument}")
            return get_config()

        if command == "config" and argument:
            from .profile_manager import ProfileManager

            manager = ProfileManager()
            profiles = manager.get_profile_names()
            if argument not in profiles:
                print(
                    f"❌ No profile named {argument!r}. "
                    f"Available: {', '.join(sorted(profiles)) or 'none'}"
                )
                return None
            profile = manager.get_profile(argument)
            configure(**{k: v for k, v in asdict(profile).items() if v is not None})
            print(f"✅ Applied profile {argument!r}")
            return get_config()

        print(
            f"❌ Unknown command {command!r}. "
            "Try: status | config | config <profile> | load <file>"
        )
        return None

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
        # Loading the extension should leave the notebook able to *use*
        # clustrix, not merely able to type its magics. Without this a user who
        # ran `%load_ext clustrix` still had to `from clustrix import cluster`
        # before anything worked.
        from . import cluster, configure, get_config

        ipython.user_ns.setdefault("cluster", cluster)
        ipython.user_ns.setdefault("configure", configure)
        ipython.user_ns.setdefault("get_config", get_config)

        magics = ClusterfyMagics(ipython)
        ipython.register_magic_function(magics.clustrix, "line", "clustrix")
        ipython.register_magic_function(magics.remote, "cell", "remote")
        # Deprecated alias -- see ClusterfyMagics.clusterfy
        ipython.register_magic_function(magics.clusterfy, "cell", "clusterfy")
        # No print here: the widget is shown by %%remote or by
        # display_config_widget(), not as a side effect of importing.
