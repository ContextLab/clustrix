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
    from .notebook_magic_fallback import (
        Magics,
        magics_class,
        cell_magic,
        line_magic,
        get_ipython,
    )


def display_config_widget() -> None:
    """Display the configuration widget."""
    from .modern_notebook_widget import display_modern_widget

    display_modern_widget()


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
        display_config_widget()


def _status_summary(config) -> str:
    """A few lines saying where ``@cluster`` runs a function right now.

    The first line is the backend *in effect*, which is not always
    ``cluster_type``: with no ``cluster_host`` an ``ssh`` or ``slurm``
    configuration runs the function in this process, and reporting
    ``slurm`` for it described a cluster nothing would be sent to. The
    decision is the decorator's own, asked for rather than restated; it
    depends on the configuration alone, so the function and arguments it is
    also handed are placeholders.
    """
    from .decorator import _choose_execution_mode

    if config.cluster_type == "local":
        backend = "local"
    elif _choose_execution_mode(config, _status_summary, (), {}) == "remote":
        backend = config.cluster_type
    else:
        why = (
            "prefer_local_parallel is set"
            if config.cluster_host
            else "no cluster host set"
        )
        backend = f"local ({why}; cluster_type is {config.cluster_type!r})"

    lines = [f"backend      : {backend}"]
    remote = backend == config.cluster_type and backend != "local"
    if remote and config.cluster_host:
        lines.append(f"host         : {config.cluster_host}")
    if remote and config.username:
        lines.append(f"username     : {config.username}")
    lines.append(
        f"resources    : {config.default_cores} cores, "
        f"{config.default_memory}, {config.default_time}"
    )
    if remote and config.cluster_host:
        lines.append(f"work dir     : {config.remote_work_dir}")
    return "\n".join(lines)


@magics_class
class ClusterfyMagics(Magics):
    """IPython magic commands for Clustrix."""

    @line_magic
    def clustrix(self, line):
        """Inspect and change the active configuration from a notebook.

        The cell magic opens the widget; this is the keyboard equivalent, for
        the things that do not need one::

            %clustrix status            what @cluster will do right now
            %clustrix config            the same, and the configuration itself
            %clustrix config <profile>  make a saved profile active
            %clustrix load <file>       load configuration from a YAML/JSON file

        ``status`` prints and returns nothing. Every other form returns the
        resulting ``ClusterConfig``, or ``None`` when the command was
        refused.
        """
        from .config import configure, get_config, load_config

        parts = line.split()
        command = parts[0] if parts else "status"
        argument = " ".join(parts[1:]) if len(parts) > 1 else ""

        if command in ("status", "config") and not argument:
            config = get_config()
            print(_status_summary(config))
            # ``status`` answers by printing. Returning the configuration as
            # well made a notebook show it as the cell's ``Out[]``: every
            # field of the dataclass on one line, directly under the five
            # lines that had just said what mattered.
            return None if command == "status" else config

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
            # The profile replaces the configuration rather than being merged
            # into it: dropping its unset fields left the previous profile's
            # cluster_host in place, so applying a local profile after a SLURM
            # one still sent jobs to the cluster.
            configure(**asdict(manager.set_active_profile(argument)))
            print(f"✅ Applied profile {argument!r}")
            return get_config()

        print(
            f"❌ Unknown command {command!r}. "
            "Try: status | config | config <profile> | load <file>"
        )
        return None

    @cell_magic
    def remote(self, line, cell):
        """Show the configuration widget, then run the rest of the cell.

        Usage::

            %%remote

        The widget chooses where ``@cluster`` runs: pick or edit a profile,
        test the connection, and press Apply. Anything else in the cell is
        ordinary code and runs after the widget is displayed.
        """
        # Says how to install the widget when it cannot be shown, so the
        # advice has one wording however the user asked for it.
        display_config_widget()
        if IPYTHON_AVAILABLE and cell.strip():
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
