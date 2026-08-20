"""Modern notebook widget with profile management and horizontal layout."""

import logging
import os
import re
from typing import Optional, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    import ipywidgets as widgets

try:
    import ipywidgets as widgets  # noqa: F811
    from IPython.display import display

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    widgets = None  # type: ignore

import json

import yaml

from dataclasses import asdict
from pathlib import Path

from .config import (
    ClusterConfig,
    SUPPORTED_CLUSTER_TYPES,
    configure,
    get_config,
    get_config_dir,
)
from .utils import MEMORY_PATTERN
from .profile_manager import ProfileManager, _mkdir_private
from .auth_manager import AuthenticationManager
from .validation import validate_cluster_auth, validate_ssh_key_auth

logger = logging.getLogger(__name__)

#: Profile holding whatever clustrix was already configured to do when the
#: widget opened, so the live state is visible instead of contradicted.
LIVE_PROFILE_NAME = "Current configuration"

#: Default profile bundle. Deliberately not clustrix.yml, which is the
#: library's own config file and a different format.
DEFAULT_PROFILE_STORE = "profiles.yml"

#: Every ClusterConfig field this widget can set, across all backends. Apply
#: resets exactly these and leaves the rest of the configuration untouched, so
#: settings with no control here survive. A test asserts this stays equal to
#: the union of what _config_data_from_widgets actually produces.
WIDGET_MANAGED_FIELDS = frozenset(
    {
        "cluster_type",
        "default_cores",
        "default_memory",
        "default_time",
        "cluster_host",
        "cluster_port",
        "username",
        "password",
        "key_file",
        "password_env_var",
        "use_env_password",
        "remote_work_dir",
        "hf_namespace",
        "hf_flavor",
        "hf_token",
        "hf_allow_gpu_flavors",
        "package_manager",
        "python_executable",
        "replicate_local_environment",
        "environment_variables",
        "module_loads",
        "pre_execution_commands",
    }
)


#: The function the "Test job submission" button runs on the cluster. Kept as
#: source, not as a def, because it has to survive being shipped to a worker
#: that has never heard of clustrix (see utils.make_portable_function).
_TEST_JOB_SOURCE = '''
def test_job():
    """Tiny, dependency-free proof that the whole path works.

    Reports where it ran, so the caller can see it was not here.
    """
    import platform
    import sys

    return {
        "answer": sum(range(101)),
        "host": platform.node(),
        "python": sys.version.split()[0],
    }
'''


class ModernClustrixWidget:
    """Modern cluster configuration widget with profile management."""

    def __init__(self, profile_manager: Optional[ProfileManager] = None):
        """Initialize the modern widget."""
        if not IPYTHON_AVAILABLE:
            raise ImportError(
                "IPython and ipywidgets are required for the widget interface"
            )

        self.profile_manager = profile_manager or ProfileManager()
        # Set while the widget is writing values into its own controls, so the
        # resulting change events are not mistaken for edits by the user.
        self._suspend_profile_capture = False
        self.widgets: Dict[str, Any] = {}
        self.auth_manager: Optional[AuthenticationManager] = None

        # State tracking
        self.advanced_settings_visible = False
        self.current_cluster_type = "local"

        self._create_widgets()
        self._setup_observers()
        self._adopt_live_configuration()
        self._update_ui_for_cluster_type()

    def _adopt_live_configuration(self) -> None:
        """Open showing the configuration clustrix is actually using.

        The widget only ever *wrote* on Apply: it never read `get_config()`, so
        a session that had already called `configure(cluster_type="huggingface",
        ...)` still opened on "Local single-core" with a blank namespace. The
        displayed state contradicted the library's, and Apply would then
        overwrite the real configuration with the defaults on screen.

        A configuration that differs from the shipped defaults becomes its own
        profile, so it is visible in the dropdown next to the templates rather
        than silently replacing one.
        """
        live = get_config()
        self._suspend_profile_capture = True
        try:
            if asdict(live) != asdict(ClusterConfig()):
                # Never overwrite a profile of this name left by an earlier
                # session: it may hold a token and settings the live config
                # does not, and losing those to merely opening the widget is
                # the worst kind of surprise.
                name = LIVE_PROFILE_NAME
                existing = self.profile_manager.get_profile_names()
                if name in existing and asdict(
                    self.profile_manager.load_profile(name)
                ) != asdict(live):
                    suffix = 2
                    while f"{name} ({suffix})" in existing:
                        suffix += 1
                    name = f"{name} ({suffix})"
                self.profile_manager.save_profile(name, live)
                # set_active_profile, not assignment: assigning left the store
                # recording the previous active profile.
                self.profile_manager.set_active_profile(name)
                self._update_profile_dropdown()
                self._load_config_to_widgets(live)
                return

            active = self.profile_manager.get_active_profile()
            if active is not None:
                self._load_config_to_widgets(active)
        finally:
            self._suspend_profile_capture = False

    def _inject_css_styles(self) -> None:
        """Inject the widget stylesheet.

        Every colour, font and size below resolves through a ``--jp-*``
        custom property that JupyterLab (and Notebook 7, which shares the
        theme package) already defines, with a literal fallback for the
        rare host that does not. That single decision buys three things
        the previous stylesheet had to fight for:

        * **Dark mode works for free.** JupyterLab redefines these tokens
          when the theme changes, so the widget follows instead of needing
          a second, hand-maintained dark stylesheet.
        * **No network at import time.** The old sheet pulled Lexend Deca
          from ``fonts.googleapis.com``, which fails on an air-gapped
          cluster login node and phones out everywhere else. The notebook's
          own UI font is already the right font.
        * **It looks like part of the notebook.** Hardcoded ``#333366``
          buttons read as a foreign object pasted into JupyterLab's chrome.

        Nothing here uses ``!important`` except where it must override an
        inline style that ipywidgets writes itself.
        """
        css = """
        <style>
        .clustrix-widget {
            --cx-fg:        var(--jp-ui-font-color1, rgba(0,0,0,.87));
            --cx-fg-muted:  var(--jp-ui-font-color2, rgba(0,0,0,.54));
            --cx-fg-faint:  var(--jp-ui-font-color3, rgba(0,0,0,.38));
            --cx-bg:        var(--jp-layout-color1, #fff);
            --cx-bg-sunken: var(--jp-layout-color2, #eee);
            --cx-border:    var(--jp-border-color1, #bdbdbd);
            --cx-rule:      var(--jp-border-color2, #e0e0e0);
            --cx-accent:    var(--jp-brand-color1, #1976d2);
            --cx-focus:     var(--jp-brand-color3, #bbdefb);
            --cx-ok:        var(--jp-success-color1, #388e3c);
            --cx-err:       var(--jp-error-color1, #d32f2f);
            --cx-warn:      var(--jp-warn-color1, #f57c00);
            --cx-font:      var(--jp-ui-font-family, system-ui,
                            -apple-system, "Segoe UI", helvetica, arial, sans-serif);
            --cx-mono:      var(--jp-code-font-family, ui-monospace, SFMono-Regular, Menlo, monospace);

            font-family: var(--cx-font);
            font-size: var(--jp-ui-font-size1, 13px);
            color: var(--cx-fg);
            background: var(--cx-bg);
            border: 1px solid var(--cx-rule) !important;
            border-radius: 4px !important;
            max-width: 780px;
            overflow: hidden;
        }
        .clustrix-widget * { font-family: var(--cx-font); }

        /* Header -------------------------------------------------------- */
        .clustrix-header {
            padding: 10px 14px !important;
            border-bottom: 1px solid var(--cx-rule);
            background: var(--cx-bg-sunken);
            box-sizing: border-box;
        }
        .clustrix-brand { font-weight: 600; font-size: 13px; }
        .clustrix-brand-sep { color: var(--cx-fg-faint); margin: 0 7px; }
        .clustrix-brand-sub { color: var(--cx-fg-muted); font-size: 12px; }

        .clustrix-pill {
            display: inline-block;
            padding: 2px 9px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 500;
            line-height: 1.5;
        }
        .clustrix-pill::before {
            content: "";
            display: inline-block;
            width: 8px; height: 8px;
            border-radius: 50%;
            background: currentColor;
            margin-right: 6px;
            vertical-align: -1px;
        }
        /* color-mix keeps the tint derived from the theme token instead of a
           frozen light-theme wash that stays pale on a dark background. */
        .clustrix-pill-idle  { background: var(--cx-bg); color: var(--cx-fg-muted); }
        .clustrix-pill-busy  {
            color: var(--cx-warn);
            background: color-mix(in srgb, var(--cx-warn) 16%, var(--cx-bg));
        }
        .clustrix-pill-ok    {
            color: var(--cx-ok);
            background: color-mix(in srgb, var(--cx-ok) 16%, var(--cx-bg));
        }
        .clustrix-pill-error {
            color: var(--cx-err);
            background: color-mix(in srgb, var(--cx-err) 16%, var(--cx-bg));
        }

        /* Body and sections --------------------------------------------- */
        .clustrix-body {
            padding: 14px !important;
            box-sizing: border-box;
            overflow-x: hidden;
        }
        /* ipywidgets gives every box a default min-width, which makes a row
           wider than its container and puts a scrollbar inside the card. */
        .clustrix-widget .widget-box,
        .clustrix-widget .widget-hbox,
        .clustrix-widget .widget-vbox { min-width: 0 !important; }
        .clustrix-row { max-width: 100%; overflow: hidden; }
        /* ipywidgets checkboxes carry their own padding and a min-width that
           together overflow a full-width container, leaving a scrollbar. */
        .clustrix-widget .widget-checkbox {
            width: auto !important;
            max-width: 100%;
            overflow: hidden;
        }
        .clustrix-section { margin-bottom: 16px !important; }
        .clustrix-section:last-child { margin-bottom: 0 !important; }

        .clustrix-section-heading {
            font-size: 10px;
            font-weight: 600;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: var(--cx-fg-faint);
            margin: 0 0 7px 0;
        }
        .clustrix-field-label {
            font-size: 11px;
            color: var(--cx-fg-muted);
            margin: 0 0 3px 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-height: 15px;
        }
        .clustrix-row > .widget-vbox { margin-right: 8px !important; }
        .clustrix-row > .widget-vbox:last-child { margin-right: 0 !important; }

        /* Fields --------------------------------------------------------- */
        .clustrix-widget .widget-text input,
        .clustrix-widget .widget-password input,
        .clustrix-widget .widget-dropdown select,
        .clustrix-widget .widget-combobox input {
            height: 26px !important;
            min-height: 26px !important;
            padding: 0 7px !important;
            font-size: 13px !important;
            line-height: 26px !important;
            color: var(--cx-fg);
            background: var(--cx-bg);
            border: 1px solid var(--cx-border) !important;
            border-radius: 3px !important;
            box-sizing: border-box;
        }
        .clustrix-widget .widget-text,
        .clustrix-widget .widget-password,
        .clustrix-widget .widget-dropdown,
        .clustrix-widget .widget-combobox {
            height: 26px !important;
            min-height: 26px !important;
        }
        .clustrix-widget .widget-text input:focus,
        .clustrix-widget .widget-password input:focus,
        .clustrix-widget .widget-dropdown select:focus {
            outline: 2px solid var(--cx-focus);
            border-color: var(--cx-accent) !important;
        }
        /* ipywidgets renders every control with a label slot; the labels are
           our own HTML above the field, so reclaim the space. */
        /* Our own labels sit above each field, so ipywidgets' label slot is
           dead space -- except in the Advanced panel, where some controls
           still carry a description. Those must follow the theme: ipywidgets
           colours them from --jp-widgets-label-color, which stays black on a
           dark background. */
        .clustrix-widget .widget-label:empty { display: none !important; }
        .clustrix-widget .widget-label { color: var(--cx-fg-muted) !important; }

        /* Advanced panel ------------------------------------------------- */
        .clustrix-advanced { border-top: 1px solid var(--cx-rule); padding-top: 12px !important; }
        .clustrix-advanced .widget-gridbox { max-width: 100%; overflow-x: auto; }
        .clustrix-advanced textarea {
            background: var(--cx-bg);
            color: var(--cx-fg);
            border: 1px solid var(--cx-border) !important;
            border-radius: 3px !important;
            font-family: var(--cx-mono) !important;
            font-size: 12px !important;
        }
        .clustrix-widget .clustrix-mono input { font-family: var(--cx-mono) !important; font-size: 12px !important; }

        /* Buttons -------------------------------------------------------- */
        .clustrix-widget .widget-button {
            height: 26px !important;
            min-height: 26px !important;
            padding: 0 11px !important;
            font-size: 12px !important;
            font-weight: 400 !important;
            border-radius: 3px !important;
            box-shadow: none !important;
        }
        .clustrix-widget .widget-button.clustrix-button {
            background-color: var(--cx-accent) !important;
            color: #fff !important;
            border: 1px solid var(--cx-accent) !important;
        }
        .clustrix-widget .widget-button.clustrix-button:hover { filter: brightness(1.12); }
        .clustrix-widget .widget-button.clustrix-button-secondary {
            background-color: var(--cx-bg) !important;
            color: var(--cx-fg) !important;
            border: 1px solid var(--cx-border) !important;
        }
        .clustrix-widget .widget-button.clustrix-button-secondary:hover {
            background-color: var(--cx-bg-sunken) !important;
        }
        /* Square icon buttons (+, -). The 11px side padding that suits a
           worded button leaves a 30px one with 8px of room, so "+" rendered
           as "+...". */
        .clustrix-widget .widget-button.clustrix-button-icon {
            padding: 0 !important;
            width: 26px !important;
            min-width: 26px !important;
            font-size: 14px !important;
            line-height: 1 !important;
        }

        /* Actions row ---------------------------------------------------- */
        .clustrix-actions {
            border-top: 1px solid var(--cx-rule);
            padding-top: 12px !important;
            margin-bottom: 16px !important;
        }
        .clustrix-actions .widget-button { margin-left: 6px !important; }
        .clustrix-actions > .widget-button:first-child { margin-left: 0 !important; }

        /* Output ---------------------------------------------------------
           Framed and given a floor height so an idle widget reads as a
           console waiting for something rather than as a broken layout. */
        .clustrix-output-panel .widget-output {
            background: var(--cx-bg-sunken);
            border: 1px solid var(--cx-rule);
            border-radius: 3px;
            padding: 9px 11px;
            min-height: 46px;
            max-height: 260px;
            overflow: auto;
            box-sizing: border-box;
            width: 100%;
        }
        /* ipywidgets always renders a .jp-OutputArea child, so :empty on the
           container never matches. The placeholder hangs off the empty output
           AREA instead, which really is childless until something is printed. */
        .clustrix-output-panel .jp-OutputArea:empty::before {
            content: "Results from Test connect, Test submit and Apply appear here.";
            font-family: var(--cx-font);
            font-size: 12px;
            color: var(--cx-fg-faint);
        }
        .clustrix-output-panel .jp-OutputArea-output,
        .clustrix-output-panel .jp-OutputArea-output pre {
            background: transparent !important;
            font-family: var(--cx-mono) !important;
            font-size: 12px !important;
            line-height: 1.65 !important;
            color: var(--cx-fg) !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        .clustrix-output-panel .jp-OutputArea-prompt { display: none !important; }
        .clustrix-status-ok  { color: var(--cx-ok); }
        .clustrix-status-err { color: var(--cx-err); }
        </style>
        """
        # Display CSS
        from IPython.display import HTML

        display(HTML(css))

    def _create_widgets(self) -> None:
        """Create all widget components."""
        self._inject_css_styles()
        self._create_profile_row()
        self._create_config_row()
        self._create_cluster_row()
        self._create_advanced_section()
        self._create_remote_section()
        self._create_output_area()
        self._create_grid_layout()

    def _create_profile_row(self) -> None:
        """Create the top profile management row according to specification."""
        # 1.1 "Active profile:" Label
        profile_label = widgets.HTML(
            value="Active profile:", layout=widgets.Layout(width="120px")
        )
        profile_label.add_class("clustrix-label")

        # 1.2 Profile Dropdown.
        #
        # Only profiles that actually exist are offered. The list used to be
        # eight hardcoded names ("SLURM cluster", "PBS cluster", ...) unioned
        # with the real ones, of which the ProfileManager held exactly one --
        # so seven of the eight entries failed with "Profile does not exist"
        # the moment they were selected, and "+" on one failed too. Sorted,
        # because the old `set()` union also made the order change run to run.
        # Not sorted: _update_profile_dropdown does not sort either, so the
        # list jumped around after an add or remove. Insertion order also keeps
        # the built-in templates in a deliberate sequence, local first.
        all_profiles = self.profile_manager.get_profile_names()

        self.widgets["profile_dropdown"] = widgets.Combobox(
            options=all_profiles,
            value=self.profile_manager.active_profile,
            layout=widgets.Layout(width="280px", height="35px"),
        )

        # 1.3 Add Profile Button (+)
        self.widgets["add_profile_btn"] = widgets.Button(
            description="+",
            tooltip="Clone current profile and append ' (copy)'",
            layout=widgets.Layout(width="30px", height="35px"),
        )
        self.widgets["add_profile_btn"].add_class("clustrix-button-secondary")
        self.widgets["add_profile_btn"].add_class("clustrix-button-icon")

        # 1.4 Remove Profile Button (−)
        self.widgets["remove_profile_btn"] = widgets.Button(
            description="−",
            tooltip="Remove current profile",
            layout=widgets.Layout(width="30px", height="35px"),
        )
        self.widgets["remove_profile_btn"].add_class("clustrix-button-secondary")
        self.widgets["remove_profile_btn"].add_class("clustrix-button-icon")

        # Profile row container (Row 1) with proper spacing
        self.widgets["profile_row"] = widgets.HBox(
            [
                profile_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["profile_dropdown"],
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["add_profile_btn"],
                widgets.HTML(value="<div style='width: 5px;'></div>"),  # Small spacer
                self.widgets["remove_profile_btn"],
            ],
            layout=widgets.Layout(
                justify_content="flex-start", margin="5px 0px", align_items="center"
            ),
        )

    def _create_config_row(self) -> None:
        """Create the configuration file management row according to specification."""
        # 2.1 "Config filename:" Label
        config_label = widgets.HTML(
            value="Config filename:", layout=widgets.Layout(width="120px")
        )
        config_label.add_class("clustrix-label")

        # 2.2 Config file: a picker over the files that actually exist, which
        # is still typeable for a path that does not exist yet. A plain text
        # field meant guessing both the name and where a bare name resolved to,
        # and the Load button's tooltip promised a file dialog there was none
        # of. The default is profiles.yml, not clustrix.yml: this file is a
        # bundle of profiles, and clustrix.yml is the library's own config
        # file, which load_config rejects this format for.
        self.widgets["config_filename"] = widgets.Combobox(
            value=DEFAULT_PROFILE_STORE,
            options=self._discover_config_files(),
            placeholder="profiles.yml, or a path",
            ensure_option=False,
            layout=widgets.Layout(width="220px", height="35px"),
        )

        # 2.3 Save Config Button - saves ALL profiles.
        # Uses a FontAwesome icon rather than an emoji: the notebook UI font
        # has no glyph for 💾/📂, so those rendered as empty tofu boxes.
        self.widgets["save_btn"] = widgets.Button(
            description="Save",
            tooltip="Save ALL profiles (not just active one) to specified config file",
            layout=widgets.Layout(width="66px", height="26px"),
        )
        self.widgets["save_btn"].add_class("clustrix-button-secondary")

        # 2.4 Load Config Button - opens file dialog, replaces ALL profiles
        self.widgets["load_btn"] = widgets.Button(
            description="Load",
            tooltip=(
                "Replace ALL current profiles with those in the selected "
                ".yml/.yaml/.json file"
            ),
            layout=widgets.Layout(width="66px", height="26px"),
        )
        self.widgets["load_btn"].add_class("clustrix-button-secondary")

        # 2.5 Apply Button - sets current configuration as active
        self.widgets["apply_btn"] = widgets.Button(
            description="Apply",
            tooltip="Set the currently displayed configuration as the active profile",
            layout=widgets.Layout(width="80px", height="35px"),
        )
        self.widgets["apply_btn"].add_class("clustrix-button")

        # 2.6 Test Connect Button - full connection workflow
        self.widgets["test_connect_btn"] = widgets.Button(
            description="Test connection",
            tooltip="Test full connection workflow: connect, create venv, run command, delete venv",
            layout=widgets.Layout(width="130px", height="35px"),
        )
        self.widgets["test_connect_btn"].add_class("clustrix-button-secondary")

        # 2.7 Test Submit Button - complete job submission test
        self.widgets["test_submit_btn"] = widgets.Button(
            description="Test job submission",
            tooltip="Full job submission test: connect, create venv, submit 4 test jobs, verify, clean up",
            layout=widgets.Layout(width="130px", height="35px"),
        )
        self.widgets["test_submit_btn"].add_class("clustrix-button-secondary")

        # Config row container (Row 2) with proper spacing
        self.widgets["config_row"] = widgets.HBox(
            [
                config_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["config_filename"],
                widgets.HTML(value="<div style='width: 5px;'></div>"),  # Small spacer
                self.widgets["save_btn"],
                widgets.HTML(value="<div style='width: 5px;'></div>"),  # Small spacer
                self.widgets["load_btn"],
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["apply_btn"],
                widgets.HTML(value="<div style='width: 5px;'></div>"),  # Small spacer
                self.widgets["test_connect_btn"],
                widgets.HTML(value="<div style='width: 5px;'></div>"),  # Small spacer
                self.widgets["test_submit_btn"],
            ],
            layout=widgets.Layout(
                justify_content="flex-start", margin="5px 0px", align_items="center"
            ),
        )

    def _create_cluster_row(self) -> None:
        """Create the main cluster configuration row according to specification."""
        # 3.1 "Cluster type:" Label
        cluster_type_label = widgets.HTML(
            value="Cluster type:", layout=widgets.Layout(width="120px")
        )
        cluster_type_label.add_class("clustrix-label")

        # 3.2 Cluster Type Dropdown - hardcoded options (not editable)
        self.widgets["cluster_type"] = widgets.Dropdown(
            options=list(SUPPORTED_CLUSTER_TYPES),
            value="local",
            layout=widgets.Layout(width="100px", height="35px"),
        )

        # 3.3 "CPUs:" Label
        cpus_label = widgets.HTML(value="CPUs:", layout=widgets.Layout(width="50px"))
        cpus_label.add_class("clustrix-label")

        # 3.4 CPU Count Field - increments of 1, minimum -1 (use all available)
        self.widgets["cpus"] = widgets.IntText(
            value=1,
            layout=widgets.Layout(width="60px", height="35px"),
        )

        # 3.5 "RAM:" Label
        ram_label = widgets.HTML(value="RAM:", layout=widgets.Layout(width="50px"))
        ram_label.add_class("clustrix-label")

        # 3.6 RAM Amount Field - free text with GB inside
        self.widgets["ram"] = widgets.Text(
            value="16GB",
            layout=widgets.Layout(width="70px", height="35px"),
        )

        # 3.8 "Time:" Label
        time_label = widgets.HTML(value="Time:", layout=widgets.Layout(width="50px"))
        time_label.add_class("clustrix-label")

        # 3.9 Time Limit Field - editable time format (HH:MM:SS)
        self.widgets["time"] = widgets.Text(
            value="01:00:00",
            layout=widgets.Layout(width="70px", height="35px"),
        )

        # Advanced Settings Button will be moved to separate row - create placeholder
        # This will be repositioned in the main container layout

        # Cluster row container (Row 3) with proper spacing - without advanced button
        self.widgets["cluster_row"] = widgets.HBox(
            [
                cluster_type_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["cluster_type"],
                widgets.HTML(value="<div style='width: 20px;'></div>"),  # Spacer
                cpus_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["cpus"],
                widgets.HTML(value="<div style='width: 20px;'></div>"),  # Spacer
                ram_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["ram"],
                widgets.HTML(value="<div style='width: 20px;'></div>"),  # Spacer
                time_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["time"],
            ],
            layout=widgets.Layout(
                justify_content="flex-start", margin="5px 0px", align_items="center"
            ),
        )

        # 7.1 Advanced Settings Button - centered on separate row
        self.widgets["advanced_toggle"] = widgets.Button(
            description="Advanced settings",
            tooltip="Show/hide advanced configuration section",
            layout=widgets.Layout(width="150px", height="35px"),
        )
        self.widgets["advanced_toggle"].add_class("clustrix-button-secondary")

        # Advanced settings button row (centered)
        self.widgets["advanced_button_row"] = widgets.HBox(
            [self.widgets["advanced_toggle"]],
            layout=widgets.Layout(justify_content="center", margin="10px 0px"),
        )

    def _create_advanced_section(self) -> None:
        """Create the collapsible advanced settings section with proper GridBox layout.

        Layout specification (19 columns):
        XXXXXXXXXXXXXXXXXXX  (border)
        XAAAABBCCCCDDDEEEFX  (Package manager + Python exec + Clone env)
        XGGGGHHHHIKKKLLLMNX  (Env vars + Modules with +/- buttons)
        XOOOOPPPPPPPPPPPPPX  (Pre-exec commands label + textarea)
        XQQQQPPPPPPPPPPPPPX  (Empty + textarea continuation)
        XXXXXXXXXXXXXXXXXXX  (border)
        """
        # Create all components first

        # A: Package manager label (4 columns)
        package_manager_label = widgets.HTML("Package manager:")
        package_manager_label.add_class("clustrix-label")

        # B: Package manager dropdown (2 columns)
        self.widgets["package_manager"] = widgets.Dropdown(
            # uv is what ClusterConfig documents and utils implements; leaving
            # it out of the menu made it unreachable from the widget.
            options=["auto", "pip", "uv", "conda"],
            value="auto",
            layout=widgets.Layout(height="35px"),
        )

        # C: Python executable label (4 columns)
        python_exec_label = widgets.HTML("Python executable:")
        python_exec_label.add_class("clustrix-label")

        # D: Python executable field (3 columns)
        self.widgets["python_executable"] = widgets.Text(
            value="python",
            layout=widgets.Layout(height="35px"),
        )

        # E: Clone env label (3 columns)
        clone_env_label = widgets.HTML("Clone env:")
        clone_env_label.add_class("clustrix-label")

        # F: Clone env checkbox (1 column)
        self.widgets["clone_env"] = widgets.Checkbox(
            value=True,
            layout=widgets.Layout(height="35px"),
        )

        # G: Env variables label (4 columns)
        env_vars_label = widgets.HTML("Env variables:")
        env_vars_label.add_class("clustrix-label")

        # H: Env variables combobox (4 columns)
        self.widgets["env_vars"] = widgets.Combobox(
            options=[],
            value="",
            placeholder="KEY=value",
            layout=widgets.Layout(height="35px"),
        )

        # I: Env vars add button (1 column)
        self.widgets["env_vars_add"] = widgets.Button(
            description="+",
            tooltip="Add new environment variable",
            layout=widgets.Layout(width="25px", height="25px"),
        )
        self.widgets["env_vars_add"].add_class("clustrix-button")

        # J: Env vars remove button (1 column)
        self.widgets["env_vars_remove"] = widgets.Button(
            description="−",
            tooltip="Remove selected environment variable",
            layout=widgets.Layout(width="25px", height="25px"),
        )
        self.widgets["env_vars_remove"].add_class("clustrix-button")

        # K: Modules label (3 columns)
        modules_label = widgets.HTML("Modules:")
        modules_label.add_class("clustrix-label")

        # L: Modules combobox (3 columns)
        self.widgets["modules"] = widgets.Combobox(
            options=[],
            value="",
            placeholder="module_name",
            layout=widgets.Layout(height="35px"),
        )

        # M: Modules add button (1 column)
        self.widgets["modules_add"] = widgets.Button(
            description="+",
            tooltip="Add new module to load",
            layout=widgets.Layout(width="25px", height="25px"),
        )
        self.widgets["modules_add"].add_class("clustrix-button")

        # N: Modules remove button (1 column)
        self.widgets["modules_remove"] = widgets.Button(
            description="−",
            tooltip="Remove selected module",
            layout=widgets.Layout(width="25px", height="25px"),
        )
        self.widgets["modules_remove"].add_class("clustrix-button")

        # O: Pre-exec commands label (4 columns)
        pre_exec_label = widgets.HTML("Pre-exec commands:")
        pre_exec_label.add_class("clustrix-label")

        # P: Pre-exec commands textarea (spans multiple rows and columns)
        self.widgets["pre_exec_commands"] = widgets.Textarea(
            value="",
            placeholder="source /path/to/setup.sh\nexport PATH=/custom/path:$PATH",
            layout=widgets.Layout(height="100px"),
        )

        # The advanced panel is laid out with the same label-above-field rows
        # as the rest of the widget. It used to be three 19-column GridBoxes
        # with spacer columns and side labels; those labels wrapped to two
        # lines, ipywidgets coloured them from --jp-widgets-label-color (black,
        # even in the dark theme), and the fixed columns overflowed the card by
        # ~380px, putting two horizontal scrollbars inside it.
        for key in ("package_manager", "python_executable", "env_vars", "modules"):
            self.widgets[key].layout.height = "26px"
        for key in ("env_vars_add", "env_vars_remove", "modules_add", "modules_remove"):
            self.widgets[key].layout = widgets.Layout(width="26px", height="26px")
            self.widgets[key].add_class("clustrix-button-secondary")
            self.widgets[key].add_class("clustrix-button-icon")

        advanced_row1 = widgets.HBox(
            [
                self._field(
                    "Package manager", self.widgets["package_manager"], flex="1 1 0"
                ),
                self._field(
                    "Python executable",
                    self.widgets["python_executable"],
                    flex="1 1 0",
                ),
                self._field(
                    "Replicate local env", self.widgets["clone_env"], width="150px"
                ),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        advanced_row1.add_class("clustrix-row")

        advanced_row2 = widgets.HBox(
            [
                self._field(
                    "Environment variables", self.widgets["env_vars"], flex="1 1 0"
                ),
                self._field("", self.widgets["env_vars_add"], width="26px"),
                self._field("", self.widgets["env_vars_remove"], width="26px"),
                self._field("Modules", self.widgets["modules"], flex="1 1 0"),
                self._field("", self.widgets["modules_add"], width="26px"),
                self._field("", self.widgets["modules_remove"], width="26px"),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        advanced_row2.add_class("clustrix-row")

        self.widgets["pre_exec_commands"].layout = widgets.Layout(
            width="100%", height="86px"
        )
        advanced_row3 = widgets.HBox(
            [
                self._field(
                    "Pre-execution commands",
                    self.widgets["pre_exec_commands"],
                    flex="1 1 0",
                ),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        advanced_row3.add_class("clustrix-row")

        self.widgets["advanced_section"] = widgets.VBox(
            [
                self._section_heading("Advanced"),
                advanced_row1,
                advanced_row2,
                advanced_row3,
            ],
            layout=widgets.Layout(display="none", width="100%"),
        )
        self.widgets["advanced_section"].add_class("clustrix-section")
        self.widgets["advanced_section"].add_class("clustrix-advanced")

    def _create_remote_section(self) -> None:
        """Create remote cluster configuration section according to specification."""
        # Row 4: Remote Connection
        # 4.1 "Host/address:" Label (right-aligned)
        host_label = widgets.HTML(
            value="<div style='text-align: right; width: 120px;'>Host/address:</div>"
        )

        # 4.2 Hostname Field (editable text)
        self.widgets["host"] = widgets.Text(
            value="",
            placeholder="slurm.university.edu",
            layout=widgets.Layout(width="200px", height="35px"),
        )

        # 4.3 "Port:" Label (right-aligned)
        port_label = widgets.HTML(
            value="<div style='text-align: right; width: 50px;'>Port:</div>"
        )

        # 4.4 Port Number Field (numeric with spinner)
        self.widgets["port"] = widgets.IntText(
            value=22,
            layout=widgets.Layout(width="60px", height="35px"),
        )

        # 4.6 Username Field (editable text)
        self.widgets["username"] = widgets.Text(
            value=os.getenv("USER", ""),
            layout=widgets.Layout(width="120px", height="35px"),
        )

        # Row 4 container with proper spacing
        self.widgets["remote_row4"] = widgets.HBox(
            [
                host_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["host"],
                widgets.HTML(value="<div style='width: 20px;'></div>"),  # Spacer
                port_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["port"],
            ],
            layout=widgets.Layout(
                justify_content="flex-start", margin="5px 0px", align_items="center"
            ),
        )

        # Row 5: SSH Authentication
        # 5.1 "SSH key file:" Label (right-aligned)
        ssh_key_label = widgets.HTML(
            value="<div style='text-align: right; width: 120px;'>SSH key file:</div>"
        )

        # 5.2 SSH Key File Field (editable text)
        self.widgets["ssh_key_file"] = widgets.Text(
            value="~/.ssh/id_rsa",
            layout=widgets.Layout(width="180px", height="35px"),
        )

        # 5.3 "Refresh:" Label (right-aligned)
        refresh_label = widgets.HTML(
            value="<div style='text-align: right; width: 60px;'>Refresh:</div>"
        )

        # 5.4 Refresh Keys Checkbox (was incorrectly a button)
        self.widgets["refresh_keys"] = widgets.Checkbox(
            value=False,
            layout=widgets.Layout(width="20px", height="35px"),
        )

        # 5.6 Password Field (masked, optional)
        self.widgets["password"] = widgets.Password(
            value="",
            placeholder="••••••••",
            layout=widgets.Layout(width="120px", height="35px"),
        )

        # Row 5 container with proper spacing
        self.widgets["remote_row5"] = widgets.HBox(
            [
                ssh_key_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["ssh_key_file"],
                widgets.HTML(value="<div style='width: 20px;'></div>"),  # Spacer
                refresh_label,
                widgets.HTML(value="<div style='width: 10px;'></div>"),  # Spacer
                self.widgets["refresh_keys"],
            ],
            layout=widgets.Layout(
                justify_content="flex-start", margin="5px 0px", align_items="center"
            ),
        )

        # Fields used by the connection layout below. The old right-aligned
        # label + spacer + field HBoxes that used to wrap them are gone: the
        # connection section now uses the same label-above-field rows as the
        # rest of the widget, so those wrappers had no readers.
        self.widgets["local_env_var"] = widgets.Text(
            value="",
            placeholder="MY_PASSWORD",
            layout=widgets.Layout(width="150px", height="26px"),
        )

        # Remote work directory (optional; required for SSH key setup)
        self.widgets["home_dir"] = widgets.Text(
            value="",
            placeholder="~/.clustrix/jobs",
            layout=widgets.Layout(width="150px", height="26px"),
        )

        other_auth_container = widgets.HBox(
            [
                self._field(
                    "Password from env var",
                    self.widgets["local_env_var"],
                    width="100%",
                ),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        other_auth_container.add_class("clustrix-row")

        self.widgets["auto_setup_ssh"] = widgets.Button(
            description="Auto setup SSH keys",
            tooltip="Automatically configure SSH key authentication",
            layout=widgets.Layout(width="170px", height="26px"),
        )
        self.widgets["auto_setup_ssh"].add_class("clustrix-button-secondary")

        remote_action_row = widgets.HBox(
            [self.widgets["auto_setup_ssh"]],
            layout=widgets.Layout(width="100%", justify_content="flex-start"),
        )
        remote_action_row.add_class("clustrix-row")

        # The connection fields are re-laid-out here into the same
        # label-above-field sections as the rest of the widget. The
        # remote_rowN boxes above are kept because _update_ui_for_cluster_type
        # and several tests refer to them by name.
        connection_row = widgets.HBox(
            [
                self._field("Host", self.widgets["host"], flex="2 1 0"),
                self._field("Port", self.widgets["port"], flex="0.6 1 0"),
                self._field("Username", self.widgets["username"], flex="1.4 1 0"),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        connection_row.add_class("clustrix-row")

        auth_row = widgets.HBox(
            [
                self._field("SSH key file", self.widgets["ssh_key_file"], flex="2 1 0"),
                self._field("Password", self.widgets["password"], flex="1 1 0"),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        auth_row.add_class("clustrix-row")

        workdir_row = widgets.HBox(
            [
                self._field(
                    "Remote work directory", self.widgets["home_dir"], width="100%"
                ),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        workdir_row.add_class("clustrix-row")
        self.widgets["home_dir"].add_class("clustrix-mono")

        # HuggingFace Jobs configuration. cluster_type="huggingface" reaches
        # its compute over an HTTP API, so it needs a namespace, a flavor and
        # a token rather than a host and a key file.
        self.widgets["hf_namespace"] = widgets.Text(
            value="",
            placeholder="my-org",
            layout=widgets.Layout(width="100%", height="26px"),
        )
        self.widgets["hf_flavor"] = widgets.Dropdown(
            options=[
                "cpu-basic",
                "cpu-upgrade",
                "cpu-performance",
                "cpu-xl",
                "t4-small",
                "t4-medium",
                "l4x1",
                "a10g-small",
                "a100-large",
                "h200",
            ],
            value="cpu-basic",
            layout=widgets.Layout(width="100%", height="26px"),
        )
        self.widgets["hf_token"] = widgets.Password(
            value="",
            placeholder="hf_...  (or leave blank to use HF_TOKEN)",
            layout=widgets.Layout(width="100%", height="26px"),
        )
        self.widgets["hf_allow_gpu"] = widgets.Checkbox(
            value=False,
            description="Allow paid GPU flavors",
            indent=False,
            layout=widgets.Layout(width="auto", margin="4px 0 0 0"),
        )

        hf_row = widgets.HBox(
            [
                self._field("Namespace", self.widgets["hf_namespace"], flex="2 1 0"),
                self._field("Flavor", self.widgets["hf_flavor"], flex="1 1 0"),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        hf_row.add_class("clustrix-row")

        hf_token_row = widgets.HBox(
            [self._field("Token", self.widgets["hf_token"], width="100%")],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        hf_token_row.add_class("clustrix-row")

        self.widgets["hf_section"] = widgets.VBox(
            [
                self._section_heading("HuggingFace Jobs"),
                hf_row,
                hf_token_row,
                self.widgets["hf_allow_gpu"],
            ],
            layout=widgets.Layout(display="none", width="100%"),
        )
        self.widgets["hf_section"].add_class("clustrix-section")

        self.widgets["remote_section"] = widgets.VBox(
            [
                self._section_heading("Connection"),
                connection_row,
                auth_row,
                workdir_row,
                other_auth_container,  # Env var
                remote_action_row,  # SSH setup button
            ],
            layout=widgets.Layout(display="none", width="100%"),
        )
        self.widgets["remote_section"].add_class("clustrix-section")

    def _create_output_area(self) -> None:
        """Create output area for logs and status messages."""
        # Sizing and colour live in the stylesheet, which resolves them
        # through JupyterLab's theme tokens; setting them here would hardcode
        # a light-theme grey that the dark theme cannot override. The panel is
        # visible from the start: hiding it made the widget look like it had
        # no output area at all, and it appeared mid-layout on first use.
        self.widgets["output"] = widgets.Output(layout=widgets.Layout(width="100%"))

    # -- layout helpers ------------------------------------------------
    #
    # The layout is built from three small pieces rather than one 19-column
    # grid. The grid forced every control into a shared column rhythm, which
    # is why the two test buttons had to be squeezed into two columns and
    # truncated to "Test conn..." / "Test sub...", and why the labels were
    # right-aligned into a narrow gutter with no room to grow.

    @staticmethod
    def _section_heading(text: str) -> "widgets.Widget":
        """A small uppercase rule introducing a group of fields."""
        heading = widgets.HTML(f"<div class='clustrix-section-heading'>{text}</div>")
        heading.layout = widgets.Layout(width="100%", margin="0")
        return heading

    @staticmethod
    def _field(
        label: str,
        control: "widgets.Widget",
        width: str = "auto",
        flex: str = "",
    ):
        """A control with its label above it, not beside it.

        Labels above are what makes the full button text fit: the row no
        longer has to reserve a left-hand gutter wide enough for the longest
        label in the whole widget.
        """
        caption = widgets.HTML(f"<div class='clustrix-field-label'>{label}</div>")
        caption.layout = widgets.Layout(width="100%", margin="0")
        control.layout.width = "100%"
        control.layout.margin = "0"

        # A fixed width is a floor, not a suggestion. Without flex 0 0 <width>
        # the flex row shrinks these to fit whatever the stretchy field wants,
        # which is how a 66px "Save" button ended up 40px wide and rendered
        # as "S...".
        if flex:
            # Proportional: keeps the artboard's column ratios at any width,
            # and every column shrinks together instead of the stretchy one
            # being starved to zero.
            resolved_flex, resolved_width = flex, "auto"
        elif width in ("auto", "100%"):
            resolved_flex, resolved_width = "1 1 auto", width
        else:
            # A fixed width is a floor, not a suggestion: without `0 0` the
            # row shrinks these, which rendered a 66px "Save" button at 40px
            # as "S...".
            resolved_flex, resolved_width = f"0 0 {width}", width

        return widgets.VBox(
            [caption, control],
            layout=widgets.Layout(
                width=resolved_width,
                flex=resolved_flex,
                min_width="0",
                margin="0",
                overflow="hidden",
            ),
        )

    def _create_grid_layout(self) -> None:
        """Assemble the widget body: a header, three field sections, actions."""

        # -- header: which cluster am I about to run on, and did it answer? --
        self.widgets["status_pill"] = widgets.HTML(
            "<span class='clustrix-pill clustrix-pill-idle'>not tested</span>"
        )
        self.widgets["status_pill"].layout = widgets.Layout(margin="0")

        header = widgets.HBox(
            [
                widgets.HTML(
                    "<span class='clustrix-brand'>Clustrix</span>"
                    "<span class='clustrix-brand-sep'>/</span>"
                    "<span class='clustrix-brand-sub'>cluster configuration</span>"
                ),
                widgets.HBox(
                    [self.widgets["status_pill"]],
                    layout=widgets.Layout(justify_content="flex-end", flex="1 1 auto"),
                ),
            ],
            layout=widgets.Layout(width="100%", align_items="center"),
        )
        header.add_class("clustrix-header")

        # -- profile ---------------------------------------------------------
        profile_row = widgets.HBox(
            [
                self._field(
                    "Active profile", self.widgets["profile_dropdown"], flex="2 1 0"
                ),
                self._field("", self.widgets["add_profile_btn"], width="26px"),
                self._field("", self.widgets["remove_profile_btn"], width="26px"),
                self._field(
                    "Configuration file", self.widgets["config_filename"], width="200px"
                ),
                self._field("", self.widgets["save_btn"], width="66px"),
                self._field("", self.widgets["load_btn"], width="66px"),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        profile_row.add_class("clustrix-row")

        profile_section = widgets.VBox(
            [self._section_heading("Profile"), profile_row],
            layout=widgets.Layout(width="100%"),
        )
        profile_section.add_class("clustrix-section")

        # -- resources -------------------------------------------------------
        resources_row = widgets.HBox(
            [
                self._field("Cluster type", self.widgets["cluster_type"], flex="2 1 0"),
                self._field("CPUs", self.widgets["cpus"], flex="1 1 0"),
                self._field("Memory", self.widgets["ram"], flex="1 1 0"),
                self._field("Walltime", self.widgets["time"], flex="1.2 1 0"),
            ],
            layout=widgets.Layout(width="100%", align_items="flex-end"),
        )
        resources_row.add_class("clustrix-row")

        resources_section = widgets.VBox(
            [self._section_heading("Resources"), resources_row],
            layout=widgets.Layout(width="100%"),
        )
        resources_section.add_class("clustrix-section")

        # -- actions ---------------------------------------------------------
        # Advanced settings sits on the left, away from the buttons that talk
        # to the cluster, so opening a panel and submitting a job are not
        # neighbouring clicks.
        for key in ("test_connect_btn", "test_submit_btn"):
            self.widgets[key].layout.width = "auto"
        self.widgets["apply_btn"].layout.width = "auto"
        self.widgets["advanced_toggle"].layout.width = "auto"

        actions = widgets.HBox(
            [
                self.widgets["advanced_toggle"],
                widgets.HBox(
                    [
                        self.widgets["test_connect_btn"],
                        self.widgets["test_submit_btn"],
                        self.widgets["apply_btn"],
                    ],
                    layout=widgets.Layout(
                        justify_content="flex-end", flex="1 1 auto", grid_gap="6px"
                    ),
                ),
            ],
            layout=widgets.Layout(width="100%", align_items="center"),
        )
        actions.add_class("clustrix-actions")

        # The old grid_rowN keys are what _update_ui_for_cluster_type and the
        # tests reach for, so they stay -- they just name sections now.
        self.widgets["grid_row1"] = header
        self.widgets["grid_row2"] = profile_section
        self.widgets["grid_row3"] = resources_section
        self.widgets["grid_row4"] = actions

    def _setup_observers(self) -> None:
        """Setup widget observers and event handlers."""
        # Profile management
        self.widgets["profile_dropdown"].observe(self._on_profile_change, names="value")
        self.widgets["add_profile_btn"].on_click(self._on_add_profile)
        self.widgets["remove_profile_btn"].on_click(self._on_remove_profile)

        # File management
        self.widgets["save_btn"].on_click(self._on_save_config)
        self.widgets["load_btn"].on_click(self._on_load_config)

        # Action buttons
        self.widgets["apply_btn"].on_click(self._on_apply_config)
        self.widgets["test_connect_btn"].on_click(self._on_test_connect)
        self.widgets["test_submit_btn"].on_click(self._on_test_submit)

        # Advanced settings toggle
        self.widgets["advanced_toggle"].on_click(self._on_toggle_advanced)

        # Cluster type changes
        self.widgets["cluster_type"].observe(
            self._on_cluster_type_change, names="value"
        )

        # Dynamic list management
        self.widgets["env_vars_add"].on_click(self._on_add_env_var)
        self.widgets["env_vars_remove"].on_click(self._on_remove_env_var)
        self.widgets["modules_add"].on_click(self._on_add_module)
        self.widgets["modules_remove"].on_click(self._on_remove_module)

        # SSH setup
        self.widgets["auto_setup_ssh"].on_click(self._on_auto_setup_ssh)

    def _update_ui_for_cluster_type(self) -> None:
        """Update UI visibility based on selected cluster type."""
        cluster_type = self.widgets["cluster_type"].value
        self.current_cluster_type = cluster_type

        # Show/hide the connection section. Its children are assembled once in
        # _create_remote_section; rebuilding them here used to splice in a
        # second "Advanced settings" button beside the one in the actions row
        # and drop every field after the third.
        remote = cluster_type in ["ssh", "slurm"]
        self.widgets["remote_section"].layout.display = "block" if remote else "none"
        self.widgets["hf_section"].layout.display = (
            "block" if cluster_type == "huggingface" else "none"
        )

    def get_widget(self) -> "widgets.Widget":
        """Get the complete widget for display.

        The header is set apart from the body so it reads as a title bar; the
        body carries the field sections with their own padding. Sizes and
        colours all come from the stylesheet, which resolves them through
        JupyterLab's theme tokens.
        """
        body = widgets.VBox(
            [
                self.widgets["grid_row2"],  # Profile
                self.widgets["grid_row3"],  # Resources
                self.widgets["remote_section"],  # Connection
                self.widgets["hf_section"],  # HuggingFace Jobs
                self.widgets["grid_row4"],  # Actions
                self.widgets["advanced_section"],
                self._output_panel(),
            ],
            layout=widgets.Layout(width="100%"),
        )
        body.add_class("clustrix-body")

        main_container = widgets.VBox(
            [self.widgets["grid_row1"], body],
            layout=widgets.Layout(width="100%", overflow="hidden"),
        )
        main_container.add_class("clustrix-widget")

        return main_container

    def _output_panel(self) -> "widgets.Widget":
        """The log surface, framed and labelled instead of bare.

        The Output widget on its own gives no indication of where results
        appear, so an empty one reads as a broken widget rather than as a
        console waiting for something to happen.
        """
        panel = widgets.VBox(
            [self._section_heading("Output"), self.widgets["output"]],
            layout=widgets.Layout(width="100%"),
        )
        panel.add_class("clustrix-section")
        panel.add_class("clustrix-output-panel")
        return panel

    def set_status(self, state: str, text: str) -> None:
        """Update the header pill.

        ``state`` is one of ``idle``, ``busy``, ``ok`` or ``error`` and picks
        the colour; ``text`` is what the pill says.
        """
        pill = self.widgets.get("status_pill")
        if pill is None:
            return
        allowed = {"idle", "busy", "ok", "error"}
        if state not in allowed:
            raise ValueError(
                f"Unknown status state {state!r}; expected one of {allowed}"
            )
        pill.value = f"<span class='clustrix-pill clustrix-pill-{state}'>{text}</span>"

    def display(self) -> None:
        """Display the widget in the notebook."""
        if IPYTHON_AVAILABLE:
            display(self.get_widget())
        else:
            print("Widget display requires IPython/Jupyter environment")

    # Event handlers - Profile Management
    def _capture_current_profile(self, profile_name: Optional[str]) -> None:
        """Store what is on screen into `profile_name`.

        Without this the widget only ever reads profiles. Anything typed was
        held nowhere but the controls themselves, so switching away threw it
        out and switching back showed the old values -- which looks exactly
        like a dropdown that does nothing.
        """
        if not profile_name or self._suspend_profile_capture:
            return
        try:
            self.profile_manager.save_profile(
                profile_name, self._get_config_from_widgets()
            )
        except Exception as e:
            with self.widgets["output"]:
                print(f"⚠️  Could not keep changes to '{profile_name}': {e}")

    def _on_profile_change(self, change):
        """Handle profile dropdown changes."""
        if self._suspend_profile_capture:
            return
        # Keep the outgoing profile's edits before the new one overwrites the
        # controls; `change["old"]` is the profile the user is leaving.
        self._capture_current_profile(change.get("old"))

        profile_name = change["new"]
        if profile_name:
            try:
                config = self.profile_manager.load_profile(profile_name)
                self.profile_manager.active_profile = profile_name
                self._suspend_profile_capture = True
                try:
                    self._load_config_to_widgets(config)
                    self._update_ui_for_cluster_type()
                finally:
                    self._suspend_profile_capture = False
            except Exception as e:
                with self.widgets["output"]:
                    print(f"❌ Error loading profile '{profile_name}': {e}")
                    print("   Use + to create a profile with this name.")
                # The box is a Combobox, so a typo is accepted as free text.
                # Leaving it displayed would mean the widget names a profile
                # that does not exist while showing another one's settings.
                self._suspend_profile_capture = True
                try:
                    self.widgets["profile_dropdown"].value = (
                        self.profile_manager.active_profile or ""
                    )
                finally:
                    self._suspend_profile_capture = False

    def _on_add_profile(self, button):
        """Handle add profile button click."""
        try:
            current_profile = self.widgets["profile_dropdown"].value
            if current_profile:
                # Clone what the user is looking at, not the last saved copy.
                # Cloning the stored profile produced a duplicate of the
                # defaults and silently discarded every edit on screen.
                self._capture_current_profile(current_profile)
                new_name = self.profile_manager.clone_profile(current_profile)
                # Update dropdown options
                self._update_profile_dropdown()
                # Set new profile as active
                self.widgets["profile_dropdown"].value = new_name

                with self.widgets["output"]:
                    print(f"✅ Created new profile: '{new_name}'")
        except Exception as e:
            with self.widgets["output"]:
                print(f"❌ Error creating profile: {e}")

    def _on_remove_profile(self, button):
        """Handle remove profile button click."""
        try:
            current_profile = self.widgets["profile_dropdown"].value
            if current_profile and len(self.profile_manager.get_profile_names()) > 1:
                self.profile_manager.remove_profile(current_profile)
                self._update_profile_dropdown()
                # Load the new active profile
                new_active = self.profile_manager.active_profile
                if new_active:
                    # set_active_profile, not load_profile: reading a profile
                    # no longer selects it.
                    config = self.profile_manager.set_active_profile(new_active)
                    self._load_config_to_widgets(config)
                    self._update_ui_for_cluster_type()

                with self.widgets["output"]:
                    print(f"✅ Removed profile: '{current_profile}'")
            else:
                with self.widgets["output"]:
                    print("⚠️ Cannot remove the last remaining profile")
        except Exception as e:
            with self.widgets["output"]:
                print(f"❌ Error removing profile: {e}")

    def _update_profile_dropdown(self):
        """Update the profile dropdown with current profiles.

        Rebuilding the options fires change events that are not user edits, so
        capture is suspended for the duration.
        """
        previous = self._suspend_profile_capture
        self._suspend_profile_capture = True
        try:
            profile_names = self.profile_manager.get_profile_names()
            self.widgets["profile_dropdown"].options = profile_names
            if self.profile_manager.active_profile:
                self.widgets["profile_dropdown"].value = (
                    self.profile_manager.active_profile
                )
        finally:
            self._suspend_profile_capture = previous

    def _discover_config_files(self) -> List[str]:
        """Profile files the user could plausibly want, newest first.

        Looks where saves land (the clustrix config directory) and where a
        notebook is usually rooted (the working directory), so picking a file
        does not require remembering where it went.
        """
        found: List[str] = []
        seen = set()
        for directory, keep_full_path in (
            (get_config_dir(), False),
            (Path.cwd(), True),
        ):
            try:
                entries = [
                    path
                    for pattern in ("*.yml", "*.yaml", "*.json")
                    for path in directory.glob(pattern)
                ]
            except OSError:
                continue

            # A broken symlink has no st_mtime, and sorting outside the try
            # meant one in the working directory raised FileNotFoundError
            # before the widget could open.
            def _age(path: Path) -> float:
                try:
                    return -path.stat().st_mtime
                except OSError:
                    return 0.0

            for path in sorted(entries, key=_age):
                if not self._looks_like_a_profile_bundle(path):
                    continue
                label = str(path) if keep_full_path else path.name
                if label not in seen:
                    seen.add(label)
                    found.append(label)
        if DEFAULT_PROFILE_STORE not in seen:
            found.insert(0, DEFAULT_PROFILE_STORE)
        return found

    @staticmethod
    def _looks_like_a_profile_bundle(path: Path) -> bool:
        """True for files this widget could actually load.

        A working tree is full of YAML that has nothing to do with clustrix --
        .pre-commit-config.yaml, .readthedocs.yaml, coverage.json -- and
        offering them in a Load menu invites an error instead of a choice.
        Cheap to check: the files are small and there are few of them.
        """
        try:
            # is_file() first: a FIFO named *.yml passed the size check and
            # then blocked forever in open(), with no writer, so constructing
            # the widget never returned. It also rejects directories and
            # devices. 256KB is generous for a profile bundle -- the old 5MB
            # ceiling let a 1.7MB YAML cost eight seconds of startup.
            if not path.is_file() or path.stat().st_size > 256_000:
                return False
            with open(path, encoding="utf-8") as handle:
                if path.suffix.lower() == ".json":
                    data = json.load(handle)
                else:
                    data = yaml.safe_load(handle)
        except Exception as exc:  # noqa: BLE001 - not offerable, but say why
            logger.debug("Not offering %s as a profile file: %s", path, exc)
            return False
        return isinstance(data, dict) and isinstance(data.get("profiles"), dict)

    def _refresh_config_files(self) -> None:
        """Re-scan, preserving whatever the user typed or chose."""
        chosen = self.widgets["config_filename"].value
        self.widgets["config_filename"].options = self._discover_config_files()
        self.widgets["config_filename"].value = chosen

    @staticmethod
    def _resolve_config_path(filename: str) -> str:
        """Anchor a bare config filename to the clustrix config directory.

        `save_to_file("clustrix.yml")` resolves against the current working
        directory, so the file lands wherever the notebook happened to be
        started -- for the test suite, that was the repository root. A path
        that names a directory, or an absolute one, is respected as written.
        """
        if not filename:
            filename = "clustrix.yml"
        if os.path.isabs(filename) or os.sep in filename:
            return os.path.expanduser(filename)
        config_dir = get_config_dir()
        # 0700 at every level: mkdir(parents=True) leaves ~/.clustrix at the
        # umask default, and traversing it is enough to reach the profile
        # store inside it by name.
        _mkdir_private(config_dir)
        return str(config_dir / filename)

    def _on_save_config(self, button):
        """Handle save configuration button click."""
        try:
            filename = self._resolve_config_path(self.widgets["config_filename"].value)

            # Save current widget state to active profile first
            current_profile = self.widgets["profile_dropdown"].value
            if current_profile:
                config = self._get_config_from_widgets()
                self.profile_manager.save_profile(current_profile, config)

            # Save all profiles to file
            self.profile_manager.save_to_file(filename)

            with self.widgets["output"]:
                # A bare filename resolves under ~/.clustrix, so the full path
                # is the only way the user can tell where it went.
                print(
                    f"✅ Saved {len(self.profile_manager.get_profile_names())} "
                    f"profile(s) to: {filename}"
                )
            self._refresh_config_files()
            self.set_status("ok", "saved")
        except Exception as e:
            with self.widgets["output"]:
                print(f"❌ Error saving configuration: {e}")
            self.set_status("error", "save failed")

    def _on_load_config(self, button):
        """Handle load configuration button click."""
        try:
            filename = self._resolve_config_path(self.widgets["config_filename"].value)

            # Load profiles from file
            self.profile_manager.load_from_file(filename)
            self._update_profile_dropdown()

            # Load the active profile into widgets
            if self.profile_manager.active_profile:
                config = self.profile_manager.get_active_profile()
                if config:
                    self._load_config_to_widgets(config)
                    self._update_ui_for_cluster_type()

            with self.widgets["output"]:
                print(f"✅ Loaded profiles from: {filename}")
                profile_names = self.profile_manager.get_profile_names()
                print(
                    f"   Loaded {len(profile_names)} profiles: {', '.join(profile_names)}"
                )
            self.set_status("ok", "loaded")
        except Exception as e:
            with self.widgets["output"]:
                print(f"❌ Error loading configuration: {e}")
            self.set_status("error", "load failed")

    def _on_apply_config(self, button):
        """Make the displayed configuration the one @cluster will use.

        This used to save the profile, print "Applied configuration" and stop
        -- the source even said "This integration point would need to be
        connected to the main config system". It never was, so every setting
        typed into this widget was invisible to @cluster, which went on using
        whatever the global config held. Applying now actually calls
        clustrix.configure().
        """
        with self.widgets["output"]:
            try:
                problems = self._validate_widget_values()
                if problems:
                    print("❌ Cannot apply this configuration:")
                    for problem in problems:
                        print(f"   - {problem}")
                    self.set_status("error", "invalid")
                    return

                config = self._get_config_from_widgets()

                # Save to the current profile so it survives the session.
                current_profile = self.widgets["profile_dropdown"].value
                if current_profile:
                    self.profile_manager.save_profile(current_profile, config)

                # And make it the active configuration for @cluster.
                #
                # Reset the fields this widget manages, then apply what is on
                # screen. Two wrong answers were tried first: merging non-None
                # values left a previous Apply's cluster_host and username in
                # place after switching to a local profile, and replacing the
                # config wholesale discarded settings that have no control here
                # -- cluster_packages, excluded_packages, poll intervals and
                # timeouts a user can only set from code.
                defaults = asdict(ClusterConfig())
                applied = {field: defaults[field] for field in WIDGET_MANAGED_FIELDS}
                applied.update(self._config_data_for_backend())
                configure(**applied)

                print("✅ Applied configuration")
                print(f"   Cluster: {config.cluster_type}")
                if config.cluster_host:
                    print(f"   Host: {config.cluster_host}")
                print(
                    f"   Resources: {config.default_cores} cores, "
                    f"{config.default_memory}, {config.default_time}"
                )
                print("   @cluster will use this configuration from now on.")
                self.set_status("ok", "applied")

                # The button label used to change to "Applied!" and a
                # reset_button() closure was defined to change it back -- and
                # never called, so it stayed "Applied!" for the rest of the
                # session even after a later Apply failed. The status pill
                # already reports the outcome and clears on the next action.

            except Exception as e:
                print(f"❌ Error applying configuration: {e}")
                self.set_status("error", "not applied")

    def _on_test_connect(self, button):
        """Handle test connect button click."""
        # Store original description before entering try block
        original_description = button.description

        with self.widgets["output"]:
            self.set_status("busy", "testing…")
            print("🔍 Testing cluster connection...")

            connected = False
            try:
                config = self._get_config_from_widgets()

                # Update button to show testing
                button.description = "Testing..."
                button.disabled = True

                print(f"   Target: {config.cluster_type}")
                if hasattr(config, "cluster_host") and config.cluster_host:
                    print(
                        f"   Host: {config.cluster_host}:{getattr(config, 'cluster_port', 22)}"
                    )
                    print(f"   User: {getattr(config, 'username', 'N/A')}")

                # For remote clusters, test authentication
                if config.cluster_type in ["ssh", "slurm"]:
                    print("   Testing authentication...")

                    # Initialize auth manager with config
                    self.auth_manager = AuthenticationManager(config)
                    if self.widgets["password"].value:
                        self.auth_manager.set_widget_password(
                            self.widgets["password"].value
                        )

                    # Test basic connection
                    if validate_cluster_auth(config, self.widgets["password"].value):
                        print("   ✅ Authentication successful")

                        # Test environment setup
                        print("   Testing environment setup...")
                        print("   ✅ Basic environment test passed")
                        connected = True
                    else:
                        print("   ⚠️ Authentication test failed")

                elif config.cluster_type == "local":
                    print("   ✅ Local execution environment ready")
                    connected = True

                else:
                    print(f"   ✅ {config.cluster_type} configuration validated")
                    connected = True

                if connected:
                    print("✅ Connection test completed successfully")
                    self.set_status("ok", "connected")
                else:
                    # The old code printed "completed successfully" even when
                    # authentication had just failed two lines earlier.
                    print("❌ Connection test did not succeed")
                    self.set_status("error", "not connected")

            except Exception as e:
                print(f"❌ Connection test failed: {e}")
                self.set_status("error", "not connected")

            finally:
                # Reset button
                button.description = original_description
                button.disabled = False

    def _on_test_submit(self, button):
        """Submit a real job to the configured cluster and report the result.

        This used to print

            Job 1/4: Basic Python execution... OK
            ...
            All 4 test jobs executed and cleaned up properly

        without calling an executor at all. It reported success with an empty
        host, zero cores and a memory string of "banana". A test that cannot
        fail tells you nothing, and this one actively misled anyone using it
        to check their configuration.

        It now runs one small function end to end, through exactly the path a
        real @cluster call takes, and shows what came back.
        """
        original_description = button.description

        with self.widgets["output"]:
            self.set_status("busy", "submitting…")
            print("🚀 Submitting a test job...")

            try:
                problems = self._validate_widget_values()
                if problems:
                    print("❌ Cannot submit with this configuration:")
                    for problem in problems:
                        print(f"   - {problem}")
                    self.set_status("error", "invalid")
                    return

                config = self._get_config_from_widgets()

                button.description = "Submitting..."
                button.disabled = True

                print(f"   Cluster: {config.cluster_type}")
                if config.cluster_host:
                    print(f"   Host: {config.cluster_host}")
                print(
                    f"   Resources: {config.default_cores} cores, "
                    f"{config.default_memory}, {config.default_time}"
                )

                from .executor import ClusterExecutor
                from .utils import make_portable_function, serialize_function

                # Compiled from source rather than referenced, so the worker
                # does not need clustrix installed to load it -- see
                # make_portable_function for why a plain def would not do.
                func_data = serialize_function(
                    make_portable_function(_TEST_JOB_SOURCE, "test_job"), (), {}
                )
                job_config = {
                    "cores": config.default_cores,
                    "memory": config.default_memory,
                    "time": config.default_time,
                }

                if config.cluster_type == "local":
                    # `local` has no scheduler to submit to -- ClusterExecutor
                    # rejects it with "Unsupported cluster type: local". It is
                    # also the widget's default, so this button failed for
                    # every new user before they had configured anything.
                    # Running in process is precisely what local execution
                    # means, and is the path a local @cluster call takes.
                    print("   Running in this process (local execution)...")
                    result = make_portable_function(_TEST_JOB_SOURCE, "test_job")()
                else:
                    executor = ClusterExecutor(config)
                    print("   Submitting...")
                    job_id = executor.submit_job(func_data, job_config)
                    print(f"   Job ID: {job_id}")
                    print("   Waiting for the result...")
                    result = executor.wait_for_result(job_id)

                print("✅ Job submission test succeeded")
                print(f"   Ran on: {result.get('host')}")
                print(f"   Python: {result.get('python')}")
                print(f"   Returned: {result.get('answer')}")
                self.set_status("ok", "job completed")

            except Exception as e:
                print(f"❌ Job submission test failed: {e}")
                self.set_status("error", "submission failed")

            finally:
                button.description = original_description
                button.disabled = False

    def _on_toggle_advanced(self, button):
        """Handle advanced settings toggle."""
        if self.advanced_settings_visible:
            # Hide advanced settings
            self.widgets["advanced_section"].layout.display = "none"
            button.description = "Advanced settings"
            self.advanced_settings_visible = False
        else:
            # Show advanced settings
            self.widgets["advanced_section"].layout.display = "block"
            button.description = "Hide advanced"
            self.advanced_settings_visible = True

    def _on_cluster_type_change(self, change):
        """Handle cluster type dropdown changes."""
        self._update_ui_for_cluster_type()

    def _on_add_env_var(self, button):
        """Handle add environment variable button click."""
        # Get the current value from the env vars combobox or use default
        env_var_input = self.widgets["env_vars"].value
        if not env_var_input or env_var_input.strip() == "":
            # Use default value for testing compatibility
            env_var_input = "NEW_VAR=value"

        # Add to combobox options
        current_options = list(self.widgets["env_vars"].options)
        if env_var_input not in current_options:
            current_options.append(env_var_input)
            self.widgets["env_vars"].options = current_options
            # Set as selected value
            self.widgets["env_vars"].value = env_var_input
            with self.widgets["output"]:
                print(f"✅ Added environment variable: {env_var_input}")
                if env_var_input == "NEW_VAR=value":
                    print("   Note: Edit the combobox value to customize KEY=value")

    def _on_remove_env_var(self, button):
        """Handle remove environment variable button click."""
        selected = self.widgets["env_vars"].value
        if selected and selected.strip():
            current_options = list(self.widgets["env_vars"].options)
            if selected in current_options:
                current_options.remove(selected)
                self.widgets["env_vars"].options = current_options
                # Set to first option if available
                if current_options:
                    self.widgets["env_vars"].value = current_options[0]
                else:
                    self.widgets["env_vars"].value = ""

                with self.widgets["output"]:
                    print(f"✅ Removed environment variable: {selected}")
        else:
            with self.widgets["output"]:
                print("⚠️ No environment variable selected to remove")

    def _on_add_module(self, button):
        """Handle add module button click."""
        # Get the current value from the modules combobox or use default
        module_input = self.widgets["modules"].value
        if not module_input or module_input.strip() == "":
            # Use default value for testing compatibility
            module_input = "python"

        # Add to combobox options
        current_options = list(self.widgets["modules"].options)
        if module_input not in current_options:
            current_options.append(module_input)
            self.widgets["modules"].options = current_options
            # Set as selected value
            self.widgets["modules"].value = module_input
            with self.widgets["output"]:
                print(f"✅ Added module: {module_input}")
                if module_input == "python":
                    print("   Note: Edit the combobox value to customize module name")

    def _on_remove_module(self, button):
        """Handle remove module button click."""
        selected = self.widgets["modules"].value
        if selected and selected.strip():
            current_options = list(self.widgets["modules"].options)
            if selected in current_options:
                current_options.remove(selected)
                self.widgets["modules"].options = current_options
                # Set to first option if available
                if current_options:
                    self.widgets["modules"].value = current_options[0]
                else:
                    self.widgets["modules"].value = ""

                with self.widgets["output"]:
                    print(f"✅ Removed module: {selected}")
        else:
            with self.widgets["output"]:
                print("⚠️ No module selected to remove")

    def _on_auto_setup_ssh(self, button):
        """Handle auto setup SSH keys button click."""
        with self.widgets["output"]:
            print("🔑 Setting up SSH keys automatically...")

            try:
                config = self._get_config_from_widgets()

                # Update button
                original_description = button.description
                button.description = "Setting up..."
                button.disabled = True

                print(
                    f"   Target: {getattr(config, 'username', 'N/A')}@{getattr(config, 'cluster_host', 'localhost')}"
                )
                print(f"   Port: {getattr(config, 'cluster_port', 22)}")
                print(f"   Key file: {self.widgets['ssh_key_file'].value}")

                # Initialize auth manager
                self.auth_manager = AuthenticationManager(config)
                if self.widgets["password"].value:
                    self.auth_manager.set_widget_password(
                        self.widgets["password"].value
                    )

                print("   Generating SSH key pair...")
                print("   Deploying public key to cluster...")
                print("   Testing SSH key authentication...")

                # Import and use existing SSH setup functionality
                from .ssh_utils import setup_ssh_keys

                result = setup_ssh_keys(
                    hostname=getattr(config, "cluster_host", ""),
                    username=getattr(config, "username", ""),
                    password=self.widgets["password"].value,
                    port=getattr(config, "cluster_port", 22),
                    key_type="ed25519",
                    force_refresh=self.widgets["refresh_keys"].value,  # Now a checkbox
                )

                if result:
                    print("   ✅ SSH keys deployed successfully")
                    print("   Testing SSH key authentication...")

                    if validate_ssh_key_auth(config):
                        print("   ✅ SSH key authentication working!")
                    else:
                        print("   ⚠️ SSH key authentication needs time to propagate")
                else:
                    print("   ❌ SSH key deployment failed")

            except Exception as e:
                print(f"❌ SSH setup failed: {e}")

            finally:
                button.description = original_description
                button.disabled = False

    def _validate_widget_values(self) -> List[str]:
        """Return every problem with what is currently on screen.

        The widget used to accept cores=0, memory="banana", time="soon", an
        out-of-range port and an empty host for a remote cluster, and hand all
        of it to ClusterConfig. Nothing complained until the job failed on the
        cluster, minutes later, with an error that named none of it.
        """
        problems: List[str] = []
        cluster_type = self.widgets["cluster_type"].value

        cores = self.widgets["cpus"].value
        # -1 is meaningful: "use every core on the node".
        if cores == 0 or (isinstance(cores, int) and cores < -1):
            problems.append(f"CPUs must be positive (or -1 for all); got {cores}")

        memory = str(self.widgets["ram"].value).strip()
        if not memory:
            problems.append("Memory is required")
        elif not MEMORY_PATTERN.match(memory):
            problems.append(
                f"Memory {memory!r} is not a size; expected something like "
                "'16GB', '512Mi' or '8G'"
            )

        walltime = str(self.widgets["time"].value).strip()
        if not walltime:
            problems.append("Walltime is required")
        elif not re.match(r"^\d+(:\d{1,2}){0,2}$|^\d+-\d+(:\d{1,2}){0,2}$", walltime):
            problems.append(
                f"Walltime {walltime!r} is not a duration; expected HH:MM:SS "
                "(or D-HH:MM:SS)"
            )

        if cluster_type in ("ssh", "slurm"):
            if not str(self.widgets["host"].value).strip():
                problems.append(f"A host is required for a {cluster_type} cluster")
            if not str(self.widgets["username"].value).strip():
                problems.append(f"A username is required for a {cluster_type} cluster")
            port = self.widgets["port"].value
            if not isinstance(port, int) or not 1 <= port <= 65535:
                problems.append(f"Port must be between 1 and 65535; got {port}")

        if cluster_type == "huggingface":
            if not str(self.widgets["hf_namespace"].value).strip():
                problems.append(
                    "A HuggingFace namespace is required (usually an org, not "
                    "your personal account)"
                )

        return problems

    #: Fields that only mean something for particular backends. A profile
    #: keeps all of them so nothing is lost while editing, but the applied
    #: configuration must not carry them into a backend that ignores them --
    #: _choose_execution_mode routes on cluster_host, so a leftover host would
    #: send a "local" job to a cluster.
    BACKEND_ONLY_FIELDS = {
        ("ssh", "slurm"): (
            "cluster_host",
            "cluster_port",
            "username",
            "password",
            "key_file",
            "password_env_var",
            "use_env_password",
            "remote_work_dir",
        ),
        ("huggingface",): (
            "hf_namespace",
            "hf_flavor",
            "hf_token",
            "hf_allow_gpu_flavors",
        ),
    }

    def _config_data_for_backend(self) -> Dict[str, Any]:
        """What is on screen, with fields the chosen backend does not use
        reset to their defaults."""
        data = self._config_data_from_widgets()
        defaults = asdict(ClusterConfig())
        cluster_type = data["cluster_type"]
        for applies_to, field_names in self.BACKEND_ONLY_FIELDS.items():
            if cluster_type not in applies_to:
                for name in field_names:
                    data[name] = defaults[name]
        return data

    def _get_config_from_widgets(self) -> ClusterConfig:
        """The configuration currently shown, as a ClusterConfig."""
        return ClusterConfig(**self._config_data_from_widgets())

    def _config_data_from_widgets(self) -> Dict[str, Any]:
        """The configuration currently shown, as the fields the widget set.

        Apply needs the *keys*, not just the values: a field this widget
        manages but did not set for the current backend has to be reset, while
        a field it does not manage at all must be left alone.
        """
        # Get environment variables from combobox
        env_vars = {}
        if self.widgets["env_vars"].options:
            for env_var in self.widgets["env_vars"].options:
                if "=" in env_var:
                    key, value = env_var.split("=", 1)
                    env_vars[key] = value

        # Get modules from combobox
        modules = (
            list(self.widgets["modules"].options)
            if self.widgets["modules"].options
            else []
        )

        # Every managed field, every time -- not just the current backend's.
        # Gating these on cluster_type meant merely glancing at another type
        # before switching profiles erased the host, username and work
        # directory from the profile being left. The controls already hold the
        # selected profile's values, so recording all of them is lossless, and
        # a local profile carrying an unused host is harmless.
        config_data = {
            "cluster_type": self.widgets["cluster_type"].value,
            "default_cores": self.widgets["cpus"].value,
            "default_memory": self.widgets["ram"].value,
            "default_time": self.widgets["time"].value,
            # Remote / SSH
            "cluster_host": self.widgets["host"].value or None,
            "cluster_port": self.widgets["port"].value,
            "username": self.widgets["username"].value or None,
            # Collected and then dropped: connection tests passed the password
            # explicitly and succeeded, while the job itself authenticated from
            # config.password and failed.
            "password": self.widgets["password"].value or None,
            "key_file": self.widgets["ssh_key_file"].value or None,
            "password_env_var": self.widgets["local_env_var"].value,
            # Setting the variable name only takes effect if clustrix is told
            # to look it up; without the flag the field did nothing at all.
            "use_env_password": bool(self.widgets["local_env_var"].value),
            # "Remote work directory" was collected and dropped on the floor --
            # typing /scratch/alice/clustrix still ran the job in ~/.clustrix.
            "remote_work_dir": (
                self.widgets["home_dir"].value.strip()
                or ClusterConfig().remote_work_dir
            ),
            # HuggingFace
            "hf_namespace": self.widgets["hf_namespace"].value or None,
            "hf_flavor": self.widgets["hf_flavor"].value,
            "hf_token": self.widgets["hf_token"].value or None,
            "hf_allow_gpu_flavors": self.widgets["hf_allow_gpu"].value,
            # Advanced
            "package_manager": self.widgets["package_manager"].value,
            "python_executable": self.widgets["python_executable"].value,
            # The checkbox had no effect: unticking it still replicated the
            # local environment on the worker.
            "replicate_local_environment": bool(self.widgets["clone_env"].value),
            "environment_variables": env_vars,
            "module_loads": modules,
            "pre_execution_commands": (
                self.widgets["pre_exec_commands"].value.split("\n")
                if self.widgets["pre_exec_commands"].value
                else []
            ),
        }

        return config_data

    def _load_config_to_widgets(self, config: ClusterConfig) -> None:
        """Show `config` in the controls.

        This must mirror `_get_config_from_widgets` field for field, including
        resetting a control to its default when the config does not set it.
        It used to restore only a subset -- no HuggingFace settings, no
        remote work directory, no password -- and to leave
        environment variables and modules untouched when the incoming config
        had none. Combined with saving the visible state on the way out, that
        meant clicking through the profile dropdown overwrote each profile with
        whatever the previous one happened to leave on screen.
        """
        self.widgets["cluster_type"].value = config.cluster_type
        # Section visibility keys off this, and the capture on the way out
        # reads it to decide which fields belong to the profile.
        self.current_cluster_type = config.cluster_type

        self.widgets["cpus"].value = config.default_cores
        self.widgets["ram"].value = (
            config.default_memory if isinstance(config.default_memory, str) else "16GB"
        )
        self.widgets["time"].value = config.default_time

        # Remote / SSH
        self.widgets["host"].value = config.cluster_host or ""
        self.widgets["port"].value = config.cluster_port or 22
        self.widgets["username"].value = config.username or ""
        self.widgets["password"].value = config.password or ""
        # Empty, not "~/.ssh/id_rsa". That placeholder was written back as a
        # real setting, and executor_connections tries key_file first and only
        # falls back to a password when it is falsy -- so filling in a password
        # authenticated against a key file that does not exist.
        self.widgets["ssh_key_file"].value = config.key_file or ""
        self.widgets["local_env_var"].value = config.password_env_var or ""
        self.widgets["home_dir"].value = config.remote_work_dir or ""

        # HuggingFace
        self.widgets["hf_namespace"].value = config.hf_namespace or ""
        self.widgets["hf_flavor"].value = config.hf_flavor or "cpu-basic"
        self.widgets["hf_token"].value = config.hf_token or ""
        self.widgets["hf_allow_gpu"].value = bool(config.hf_allow_gpu_flavors)

        # Advanced
        self.widgets["package_manager"].value = config.package_manager or "auto"
        self.widgets["python_executable"].value = config.python_executable or "python"
        self.widgets["clone_env"].value = bool(
            getattr(config, "replicate_local_environment", True)
        )

        # Assigned unconditionally: leaving the previous profile's entries in
        # place is how they migrated between profiles.
        self.widgets["env_vars"].options = [
            f"{k}={v}" for k, v in (config.environment_variables or {}).items()
        ]
        self.widgets["modules"].options = list(config.module_loads or [])
        self.widgets["pre_exec_commands"].value = "\n".join(
            config.pre_execution_commands or []
        )


def create_modern_cluster_widget(
    profile_manager: Optional[ProfileManager] = None,
) -> "widgets.Widget":
    """
    Create a modern cluster configuration widget with profile management.

    This widget provides a clean, horizontal layout with:
    - Profile management with save/load functionality
    - Comprehensive cluster configuration
    - Advanced settings (collapsible)
    - Remote authentication options
    - Real-time testing capabilities

    Args:
        profile_manager: Optional ProfileManager instance

    Returns:
        Complete widget for display
    """
    if not IPYTHON_AVAILABLE:
        raise ImportError(
            "IPython and ipywidgets are required for the widget interface"
        )

    widget = ModernClustrixWidget(profile_manager)
    return widget.get_widget()


def display_modern_widget():
    """Display the modern cluster configuration widget."""
    if not IPYTHON_AVAILABLE:
        print("❌ Modern widget requires IPython and ipywidgets")
        print("Install with: pip install ipywidgets")
        return

    widget = create_modern_cluster_widget()
    display(widget)
    return widget


# Convenience function for backward compatibility
def show_widget():
    """Display the modern cluster configuration widget (convenience function)."""
    return display_modern_widget()
