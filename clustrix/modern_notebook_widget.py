"""The notebook configuration widget: choose where ``@cluster`` runs.

What the widget does lives here; how it looks lives in ``widget_style``.
"""

import html
import logging
import os
import re
import warnings
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple, TYPE_CHECKING

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
    config_source_for_discovered_path,
    configure,
    get_config,
    get_config_dir,
    split_config_kwargs,
)
from .utils import MEMORY_PATTERN
from .profile_manager import ProfileManager, _mkdir_private
from .ssh_security import HostKeyVerificationError, trust_offered_host_key
from .widget_controls import set_choice
from .widget_style import STYLESHEET

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


#: A shell-safe environment variable name.
ENVIRONMENT_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The function the "Run test job" button runs on the cluster. Kept as
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


#: One line per backend saying what choosing it means. Shown under the
#: selector, because the four names alone do not say where the code will run
#: or who pays for it.
BACKEND_DESCRIPTIONS = {
    "local": (
        "Runs in this Python process. CPUs, memory and walltime are recorded "
        "but not enforced here."
    ),
    "ssh": "Runs on one remote machine over SSH, with no scheduler.",
    "slurm": "Submits a batch job to a SLURM scheduler over SSH.",
    "huggingface": (
        "Runs in a HuggingFace Jobs container, billed to the namespace below."
    ),
}

#: How each backend is written on its tile: (name, the few words under it).
#: A backend added to SUPPORTED_CLUSTER_TYPES without an entry here still
#: gets a tile, labelled with its raw name.
BACKEND_TILES = {
    "local": ("Local", "This machine"),
    "ssh": ("SSH", "One remote host"),
    "slurm": ("SLURM", "Batch scheduler"),
    "huggingface": ("Hugging Face", "Cloud containers"),
}

#: Controls whose values make up the configuration on screen. Editing any of
#: them makes the form differ from what ``@cluster`` is using.
FORM_CONTROLS = (
    "cluster_type",
    "cpus",
    "ram",
    "time",
    "host",
    "port",
    "username",
    "ssh_key_file",
    "password",
    "local_env_var",
    "home_dir",
    "hf_namespace",
    "hf_flavor",
    "hf_token",
    "hf_allow_gpu",
    "package_manager",
    "python_executable",
    "clone_env",
    "env_vars",
    "modules",
    "pre_exec_commands",
)

#: Hardware the HuggingFace section offers, cheapest first. Not exhaustive and
#: not validated: a saved flavor that is not listed is added to the menu when
#: it is loaded (see widget_controls.set_choice).
HF_FLAVORS = (
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
)

STATUS_STATES = ("idle", "busy", "ok", "error")


def _parse_environment_variables(text: str) -> Tuple[Dict[str, str], List[str]]:
    """Read ``KEY=value`` lines into a mapping, and name the lines that are not.

    Returned separately rather than raised so the caller can keep the valid
    entries -- a profile being switched away from must not lose three good
    variables to one typo -- while Apply still refuses to proceed.
    """
    variables: Dict[str, str] = {}
    malformed: List[str] = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry:
            continue
        key, separator, value = entry.partition("=")
        if not separator or not ENVIRONMENT_VARIABLE_NAME.match(key.strip()):
            malformed.append(entry)
            continue
        variables[key.strip()] = value
    return variables, malformed


class ModernClustrixWidget:
    """Notebook form for choosing where ``@cluster`` runs.

    ``self.widgets`` maps a name to each control, so code and tests can read
    or set a field without walking the layout; ``get_widget()`` returns the
    one container that holds them all.
    """

    def __init__(self, profile_manager: Optional[ProfileManager] = None):
        if not IPYTHON_AVAILABLE:
            raise ImportError(
                "IPython and ipywidgets are required for the widget interface"
            )

        self.profile_manager = profile_manager or ProfileManager()
        # Set while the widget is writing values into its own controls, so the
        # resulting change events are not mistaken for edits by the user.
        self._suspend_profile_capture = False
        # The refused connection whose host key the user is being asked about.
        self._untrusted_host: Optional[HostKeyVerificationError] = None
        self.widgets: Dict[str, Any] = {}

        self._create_controls()
        self._container = self._create_layout()
        self._setup_observers()
        self._adopt_live_configuration()
        self._update_ui_for_cluster_type()
        self._show_configuration_in_use()
        self._show_whether_applied()

    # -- construction ----------------------------------------------------

    @staticmethod
    def _text(description: str, placeholder: str = "", mono: bool = False):
        control = widgets.Text(
            description=description,
            placeholder=placeholder,
            # Commit on blur or Enter. Per-keystroke events would persist a
            # half-typed hostname to the profile store on every character.
            continuous_update=False,
        )
        control.add_class("cx-field")
        if mono:
            control.add_class("cx-mono")
        return control

    @staticmethod
    def _button(label: str, tooltip: str, primary: bool = False):
        button = widgets.Button(description=label, tooltip=tooltip)
        if primary:
            button.add_class("cx-primary")
        return button

    def _create_controls(self) -> None:
        """Create every control, named, with no layout decisions."""
        w = self.widgets

        # Profile
        w["profile_dropdown"] = widgets.Dropdown(
            description="Saved profile",
            options=self.profile_manager.get_profile_names(),
            value=self.profile_manager.active_profile,
        )
        w["profile_name"] = self._text("Name")
        w["profile_name"].value = self.profile_manager.active_profile or ""
        w["add_profile_btn"] = self._button(
            "Duplicate", "Copy this profile, including the edits on screen"
        )
        w["remove_profile_btn"] = self._button("Delete", "Delete this profile")
        w["remove_profile_btn"].add_class("cx-danger")
        # Typeable, so a file that does not exist yet can be saved to, and a
        # picker over the bundles that do exist, so loading one does not mean
        # remembering where it went.
        w["config_filename"] = widgets.Combobox(
            description="Profiles file",
            value=DEFAULT_PROFILE_STORE,
            options=self._discover_config_files(),
            placeholder=f"{DEFAULT_PROFILE_STORE}, or a path",
            ensure_option=False,
            continuous_update=False,
        )
        w["save_btn"] = self._button(
            "Save all", "Write every profile to this file (credentials are left out)"
        )
        w["load_btn"] = self._button(
            "Load", "Replace every profile with the ones in this file"
        )

        # Backend and resources
        w["cluster_type"] = widgets.ToggleButtons(
            options=[
                (BACKEND_TILES.get(name, (name, ""))[0], name)
                for name in SUPPORTED_CLUSTER_TYPES
            ],
            value="local",
            tooltips=[
                BACKEND_TILES.get(name, (name, ""))[1]
                for name in SUPPORTED_CLUSTER_TYPES
            ],
        )
        w["cluster_type"].add_class("cx-backend")
        w["backend_hint"] = widgets.HTML()
        w["backend_hint"].add_class("cx-hint")
        w["cpus"] = widgets.IntText(description="CPUs", value=1)
        w["ram"] = self._text("Memory", "16GB")
        w["time"] = self._text("Walltime", "HH:MM:SS")

        # SSH connection (ssh and slurm)
        w["host"] = self._text("Host", "login.cluster.example.edu")
        w["port"] = widgets.IntText(description="Port", value=22)
        w["username"] = self._text("Username")
        w["ssh_key_file"] = self._text(
            "SSH key file", "blank: ssh-agent and default keys", mono=True
        )
        w["password"] = widgets.Password(
            description="Password", placeholder="this session only, never saved"
        )
        w["local_env_var"] = self._text(
            "Read password from environment variable", "MY_CLUSTER_PASSWORD", mono=True
        )
        w["home_dir"] = self._text(
            "Remote work directory", ClusterConfig().remote_work_dir, mono=True
        )
        w["auto_setup_ssh"] = self._button(
            "Set up SSH key",
            "Generate a key and install it on the host, using the password "
            "above once",
        )
        w["refresh_keys"] = widgets.Checkbox(
            description="Replace a key that already works", value=False, indent=False
        )

        # HuggingFace Jobs
        w["hf_namespace"] = self._text("Namespace", "user or organisation")
        w["hf_flavor"] = widgets.Dropdown(
            description="Hardware", options=list(HF_FLAVORS), value=HF_FLAVORS[0]
        )
        w["hf_token"] = widgets.Password(
            description="Token", placeholder="blank: HF_TOKEN or `hf auth login`"
        )
        w["hf_allow_gpu"] = widgets.Checkbox(
            description="Allow paid GPU hardware", value=False, indent=False
        )

        # Advanced
        w["package_manager"] = widgets.Dropdown(
            description="Package manager",
            options=["auto", "pip", "uv", "conda"],
            value="auto",
        )
        w["python_executable"] = self._text("Python executable", "python", mono=True)
        w["python_executable"].value = "python"
        w["clone_env"] = widgets.Checkbox(
            description="Replicate my local environment on the worker",
            value=True,
            indent=False,
        )
        w["env_vars"] = widgets.Textarea(
            description="Environment variables",
            placeholder="KEY=value, one per line",
            rows=3,
            continuous_update=False,
        )
        w["modules"] = self._text(
            "Modules to load", "space-separated, e.g. python/3.11 cuda", mono=True
        )
        w["pre_exec_commands"] = widgets.Textarea(
            description="Commands to run before the job",
            placeholder="one shell command per line",
            rows=3,
            continuous_update=False,
        )

        # Actions and feedback
        w["advanced_toggle"] = self._button(
            "Advanced settings", "Show or hide environment and module settings"
        )
        w["test_connect_btn"] = self._button(
            "Test connection", "Connect and authenticate, without running a job"
        )
        w["test_submit_btn"] = self._button(
            "Run test job", "Run one tiny job end to end and show where it ran"
        )
        w["apply_btn"] = self._button(
            "Apply", "Make @cluster use what is shown here", primary=True
        )
        w["trust_host_btn"] = self._button(
            "Trust this host",
            "Record the host key shown below in known_hosts, then connect",
        )
        w["trust_host_btn"].layout.display = "none"
        w["status_pill"] = widgets.HTML()
        w["in_use"] = widgets.HTML()
        w["output"] = widgets.Output()
        w["output"].add_class("cx-output")

        for key in (
            "profile_dropdown",
            "config_filename",
            "cpus",
            "port",
            "password",
            "hf_flavor",
            "hf_token",
            "package_manager",
            "env_vars",
            "pre_exec_commands",
        ):
            w[key].add_class("cx-field")
        for key in ("refresh_keys", "hf_allow_gpu", "clone_env"):
            w[key].add_class("cx-check")

    @staticmethod
    def _row(*cells: Tuple[Any, str]):
        """A wrapping row. Each cell is ``(control, flex)``.

        ``flex`` is a CSS ``grow shrink basis`` triple: the basis is the width
        below which the cell wraps to its own line, so the form reflows in a
        narrow panel instead of clipping.
        """
        for control, flex in cells:
            control.layout.flex = flex
        row = widgets.Box([control for control, _ in cells])
        row.add_class("cx-row")
        return row

    @staticmethod
    def _section(heading: str, *children: Any):
        title = widgets.HTML(f"<div class='cx-heading'>{html.escape(heading)}</div>")
        section = widgets.VBox([title, *children])
        section.add_class("cx-section")
        return section

    def _create_layout(self):
        """Arrange the controls. The only place that knows what goes where."""
        w = self.widgets
        fixed = "0 0 auto"

        brand = widgets.Box(
            [widgets.HTML("<span class='cx-title'>Clustrix</span>"), w["in_use"]]
        )
        brand.add_class("cx-brand")
        header = widgets.Box([brand, w["status_pill"]])
        header.add_class("cx-header")

        profile = self._section(
            "Profile",
            self._row(
                (w["profile_dropdown"], "2 1 200px"),
                (w["profile_name"], "2 1 200px"),
                (w["add_profile_btn"], fixed),
                (w["remove_profile_btn"], fixed),
            ),
        )

        resources = self._section(
            "Where to run",
            self._row((w["cluster_type"], "1 1 100%")),
            w["backend_hint"],
            self._row(
                (w["cpus"], "1 1 90px"),
                (w["ram"], "1 1 110px"),
                (w["time"], "1 1 110px"),
            ),
        )

        w["remote_section"] = self._section(
            "SSH connection",
            self._row(
                (w["host"], "3 1 240px"),
                (w["port"], "0 1 80px"),
                (w["username"], "2 1 140px"),
            ),
            self._row(
                (w["ssh_key_file"], "1 1 240px"),
                (w["password"], "1 1 240px"),
            ),
            self._row(
                (w["home_dir"], "1 1 240px"),
                (w["local_env_var"], "1 1 240px"),
            ),
            self._row((w["auto_setup_ssh"], fixed), (w["refresh_keys"], fixed)),
        )

        w["hf_section"] = self._section(
            "HuggingFace Jobs",
            self._row(
                (w["hf_namespace"], "2 1 200px"),
                (w["hf_flavor"], "1 1 160px"),
            ),
            self._row((w["hf_token"], "1 1 240px")),
            self._row((w["hf_allow_gpu"], fixed)),
        )

        w["advanced_section"] = self._section(
            "Advanced",
            self._row(
                (w["package_manager"], "1 1 160px"),
                (w["python_executable"], "2 1 200px"),
            ),
            self._row((w["clone_env"], fixed)),
            self._row((w["modules"], "1 1 240px")),
            self._row(
                (w["env_vars"], "1 1 240px"),
                (w["pre_exec_commands"], "1 1 240px"),
            ),
            # Profiles are kept automatically; a file is for moving them to
            # another machine, which is rare enough to live down here.
            self._row(
                (w["config_filename"], "1 1 240px"),
                (w["save_btn"], fixed),
                (w["load_btn"], fixed),
            ),
        )
        w["advanced_section"].layout.display = "none"

        main_actions = widgets.Box(
            [w["test_connect_btn"], w["test_submit_btn"], w["apply_btn"]]
        )
        main_actions.add_class("cx-row")
        main_actions.add_class("cx-actions-main")
        action_row = widgets.Box([w["advanced_toggle"], main_actions])
        action_row.add_class("cx-row")
        action_row.add_class("cx-actions")
        actions = widgets.VBox([action_row, w["output"], w["trust_host_btn"]])
        w["trust_host_btn"].layout.margin = "10px 0 0 0"
        w["trust_host_btn"].layout.align_self = "flex-start"
        actions.add_class("cx-section")
        w["output"].layout.margin = "10px 0 0 0"

        body = widgets.VBox(
            [
                profile,
                resources,
                w["remote_section"],
                w["hf_section"],
                w["advanced_section"],
                actions,
            ]
        )
        body.add_class("cx-body")

        # The stylesheet travels inside the widget, so it arrives wherever
        # the widget is displayed. It used to be emitted with display() from
        # the constructor, which put it in whichever cell happened to
        # *construct* the widget -- and nowhere at all when that cell's
        # output was cleared.
        container = widgets.VBox([widgets.HTML(STYLESHEET), header, body])
        container.add_class("clustrix-widget")
        return container

    def _setup_observers(self) -> None:
        w = self.widgets
        w["profile_dropdown"].observe(self._on_profile_change, names="value")
        w["profile_name"].observe(self._on_rename_profile, names="value")
        w["add_profile_btn"].on_click(self._on_add_profile)
        w["remove_profile_btn"].on_click(self._on_remove_profile)
        w["save_btn"].on_click(self._on_save_config)
        w["load_btn"].on_click(self._on_load_config)
        w["cluster_type"].observe(self._on_cluster_type_change, names="value")
        w["advanced_toggle"].on_click(self._on_toggle_advanced)
        w["auto_setup_ssh"].on_click(self._on_auto_setup_ssh)
        w["test_connect_btn"].on_click(self._on_test_connect)
        w["test_submit_btn"].on_click(self._on_test_submit)
        w["apply_btn"].on_click(self._on_apply_config)
        w["trust_host_btn"].on_click(self._on_trust_host)
        for key in FORM_CONTROLS:
            w[key].observe(self._on_field_edit, names="value")

    # -- display ---------------------------------------------------------

    def get_widget(self) -> "widgets.Widget":
        """The widget's single container, for ``display()``."""
        return self._container

    def display(self) -> None:
        """Display the widget in the notebook."""
        display(self._container)

    def set_status(self, state: str, text: str) -> None:
        """Update the header pill: ``state`` picks the colour, ``text`` the words."""
        if state not in STATUS_STATES:
            raise ValueError(
                f"Unknown status state {state!r}; expected one of {STATUS_STATES}"
            )
        self.widgets["status_pill"].value = (
            f"<span class='cx-pill cx-pill-{state}'>{html.escape(text)}</span>"
        )
        output = self.widgets["output"]
        for other in STATUS_STATES:
            output.remove_class(f"cx-state-{other}")
        output.add_class(f"cx-state-{state}")

    def _show_configuration_in_use(self) -> None:
        """Say in the header what ``@cluster`` will do right now.

        What is on screen is a draft until Apply is pressed; without this
        line nothing distinguishes the draft from the live configuration.
        """
        live = get_config()
        if live.cluster_type == "huggingface":
            where = f"huggingface ({live.hf_namespace or 'no namespace'})"
        elif live.cluster_type in ("ssh", "slurm") and live.cluster_host:
            where = f"{live.cluster_type} on {live.cluster_host}"
        else:
            # ssh or slurm with no host runs on this machine, exactly as
            # `local` does (see decorator._choose_execution_mode), so say what
            # will happen rather than echo a backend name that will not.
            where = "this machine"
        self.widgets["in_use"].value = (
            "<span class='cx-in-use'>@cluster currently runs on "
            f"<b>{html.escape(where)}</b></span>"
        )

    def _form_matches_live_configuration(self) -> bool:
        """True when pressing Apply would change nothing."""
        settings, _ = split_config_kwargs(
            self._config_data_for_backend(), reset_fields=WIDGET_MANAGED_FIELDS
        )
        live = asdict(get_config())
        return all(live[name] == value for name, value in settings.items())

    def _show_whether_applied(self) -> None:
        """Say whether the form is what ``@cluster`` is using."""
        if self._form_matches_live_configuration():
            self.set_status("ok", "in use")
        else:
            self.set_status("idle", "not applied")

    def _on_field_edit(self, change) -> None:
        if not self._suspend_profile_capture:
            self._show_whether_applied()

    def _update_ui_for_cluster_type(self) -> None:
        """Show the section the chosen backend needs, and say what it does."""
        cluster_type = self.widgets["cluster_type"].value
        self.widgets["remote_section"].layout.display = (
            None if cluster_type in ("ssh", "slurm") else "none"
        )
        self.widgets["hf_section"].layout.display = (
            None if cluster_type == "huggingface" else "none"
        )
        self.widgets["backend_hint"].value = html.escape(
            BACKEND_DESCRIPTIONS.get(cluster_type, "")
        )

    def _on_cluster_type_change(self, change) -> None:
        self._update_ui_for_cluster_type()

    def _on_toggle_advanced(self, button) -> None:
        section = self.widgets["advanced_section"]
        opening = section.layout.display == "none"
        section.layout.display = None if opening else "none"
        button.description = (
            "Hide advanced settings" if opening else "Advanced settings"
        )

    @contextmanager
    def _action(self, key: str, busy_label: str, busy_status: str) -> Iterator[None]:
        """Run the action behind button ``key``: clear, show progress, restore.

        The console is cleared first so the result on screen is always the
        result of the button just pressed. It used to be an append-only log
        in a fixed-height box, and the newest line -- the only one that
        mattered -- was the one scrolled out of sight.
        """
        output = self.widgets["output"]
        output.clear_output()
        self._untrusted_host = None
        self.widgets["trust_host_btn"].layout.display = "none"
        button = self.widgets[key]
        original = button.description
        button.description = busy_label
        button.disabled = True
        self.set_status("busy", busy_status)
        try:
            with output:
                yield
        finally:
            button.description = original
            button.disabled = False

    def _report(self, *lines: str) -> None:
        """Replace the console's contents with ``lines``."""
        output = self.widgets["output"]
        output.clear_output()
        with output:
            print("\n".join(lines))

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
                self._show_profile(name)
            elif self.profile_manager.active_profile:
                self._show_profile(self.profile_manager.active_profile)
        finally:
            self._suspend_profile_capture = False

    # -- profiles ----------------------------------------------------------

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
            with warnings.catch_warnings():
                # ProfileManager warns that secrets are not written to disk.
                # The password and token fields say so themselves, and in a
                # notebook the warning lands as a red block above the form.
                warnings.filterwarnings(
                    "ignore", message="Profiles are not a credential store"
                )
                self.profile_manager.save_profile(
                    profile_name, self._get_config_from_widgets()
                )
        except (ValueError, OSError) as e:
            self._report(f"Could not keep the changes to '{profile_name}': {e}")
            self.set_status("error", "not saved")

    def _update_profile_dropdown(self) -> None:
        """Re-read the profile names; the active profile stays selected.

        Rebuilding the options fires change events that are not edits by the
        user, so capture is suspended for the duration.
        """
        previous = self._suspend_profile_capture
        self._suspend_profile_capture = True
        try:
            self.widgets["profile_dropdown"].options = (
                self.profile_manager.get_profile_names()
            )
            self.widgets["profile_dropdown"].value = self.profile_manager.active_profile
            self.widgets["profile_name"].value = (
                self.profile_manager.active_profile or ""
            )
        finally:
            self._suspend_profile_capture = previous

    def _show_profile(self, name: str) -> None:
        """Put profile `name` on screen, without treating that as an edit."""
        config = self.profile_manager.set_active_profile(name)
        self._update_profile_dropdown()
        self._suspend_profile_capture = True
        try:
            self._load_config_to_widgets(config)
            self._update_ui_for_cluster_type()
        finally:
            self._suspend_profile_capture = False
        self._show_whether_applied()

    def _on_profile_change(self, change) -> None:
        if self._suspend_profile_capture:
            return
        # Keep the outgoing profile's edits before the new one overwrites the
        # controls; `change["old"]` is the profile the user is leaving.
        self._capture_current_profile(change.get("old"))
        if change["new"]:
            self._show_profile(change["new"])

    def _on_rename_profile(self, change) -> None:
        if self._suspend_profile_capture:
            return
        old_name = self.widgets["profile_dropdown"].value
        new_name = (change["new"] or "").strip()
        if not old_name or new_name == old_name:
            return
        try:
            if not new_name:
                raise ValueError("A profile needs a name")
            self._capture_current_profile(old_name)
            self.profile_manager.rename_profile(old_name, new_name)
        except (ValueError, OSError) as e:
            self._show_profile(old_name)
            self._report(f"Could not rename '{old_name}': {e}")
            self.set_status("error", "not renamed")
            return
        self._show_profile(new_name)

    def _on_add_profile(self, button) -> None:
        current = self.widgets["profile_dropdown"].value
        if not current:
            return
        try:
            # Clone what the user is looking at, not the last saved copy.
            self._capture_current_profile(current)
            new_name = self.profile_manager.clone_profile(current)
        except (ValueError, OSError) as e:
            self._report(f"Could not duplicate '{current}': {e}")
            self.set_status("error", "not duplicated")
            return
        self._show_profile(new_name)
        self._report(f"Created profile '{new_name}'. Rename it in the Name field.")

    def _on_remove_profile(self, button) -> None:
        current = self.widgets["profile_dropdown"].value
        if not current:
            return
        try:
            self.profile_manager.remove_profile(current)
        except (ValueError, OSError) as e:
            self._report(f"Could not delete '{current}': {e}")
            self.set_status("error", "not deleted")
            return
        remaining = self.profile_manager.get_profile_names()
        self._show_profile(self.profile_manager.active_profile or remaining[0])
        self._report(f"Deleted profile '{current}'.")

    # -- profile files -----------------------------------------------------

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
        except (OSError, UnicodeDecodeError) as exc:
            # Not the same answer as the one below, and the difference is the
            # whole point. This file was never read, so "it is not a profile
            # bundle" is a guess: it may be exactly the profile store the user
            # is looking for, sitting behind a permission bit or a dead
            # automount. It still cannot be offered -- loading it would fail
            # too -- but the reason has to be audible, because the symptom is
            # a Load menu that is silently missing the entry the user wants.
            logger.warning(
                "Could not read %s, so it is not being offered in the profile "
                "list (%s). This is a failure to read the file, not evidence "
                "that it holds no profiles.",
                path,
                exc,
            )
            return False
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            # Read in full and it is not parseable, so it is genuinely not a
            # profile bundle. A real answer, and a quiet one: a working tree
            # is full of YAML that has nothing to do with clustrix.
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

    def _on_save_config(self, button) -> None:
        with self._action("save_btn", "Saving…", "saving…"):
            try:
                filename = self._resolve_config_path(
                    self.widgets["config_filename"].value
                )
                self._capture_current_profile(self.widgets["profile_dropdown"].value)
                self.profile_manager.save_to_file(filename)
            except (ValueError, OSError, yaml.YAMLError) as e:
                print(f"Could not save: {e}")
                self.set_status("error", "save failed")
                return
            self._refresh_config_files()
            count = len(self.profile_manager.get_profile_names())
            # A bare filename resolves under the clustrix config directory, so
            # the full path is the only way to tell where it went.
            print(f"Saved {count} profile(s) to {filename}")
            print("Passwords and tokens are never written to this file.")
            self.set_status("ok", "saved")

    def _on_load_config(self, button) -> None:
        with self._action("load_btn", "Loading…", "loading…"):
            try:
                filename = self._resolve_config_path(
                    self.widgets["config_filename"].value
                )
                # The picker is populated by globbing the working directory,
                # so a bundle that a cloned repository ships is *offered* by
                # the widget rather than named by the user. Its provenance is
                # therefore where it was found, exactly as
                # ``ProfileManager._restore`` records it -- calling it the
                # user's explicit choice would hand it the cluster password.
                self.profile_manager.load_from_file(
                    filename, source=config_source_for_discovered_path(filename)
                )
            except (ValueError, OSError, yaml.YAMLError, json.JSONDecodeError) as e:
                print(f"Could not load: {e}")
                self.set_status("error", "load failed")
                return
            names = self.profile_manager.get_profile_names()
            active = self.profile_manager.active_profile or names[0]
            self._show_profile(active)
            print(f"Loaded {len(names)} profile(s) from {filename}:")
            print("  " + ", ".join(names))
            self.set_status("ok", "loaded")

    # -- apply and test ----------------------------------------------------

    def _refuse_if_invalid(self, verb: str) -> bool:
        """Print what is wrong with the form; True when something is."""
        problems = self._validate_widget_values()
        if not problems:
            return False
        print(f"Cannot {verb} this configuration:")
        for problem in problems:
            print(f"  - {problem}")
        self.set_status("error", "invalid")
        return True

    def _on_apply_config(self, button) -> None:
        """Make the displayed configuration the one ``@cluster`` uses."""
        with self._action("apply_btn", "Applying…", "applying…"):
            if self._refuse_if_invalid("apply"):
                return
            try:
                # Kept in the profile too, so it survives the session.
                self._capture_current_profile(self.widgets["profile_dropdown"].value)
                # Reset the fields this widget manages, then apply what is on
                # screen. Merging only the non-empty values left a previous
                # Apply's host in place after switching to a local profile;
                # replacing the configuration wholesale discarded settings
                # that have no control here. ``reset_fields`` does neither.
                settings, unrecognised = split_config_kwargs(
                    self._config_data_for_backend(),
                    reset_fields=WIDGET_MANAGED_FIELDS,
                )
                configure(**settings)
            except (ValueError, TypeError, OSError) as e:
                print(f"Not applied: {e}")
                self.set_status("error", "not applied")
                return

            # Read back what @cluster will actually see, rather than echoing
            # the form: fields the chosen backend ignores were just reset.
            live = get_config()
            self._show_configuration_in_use()
            print(f"Applied. @cluster now runs on: {live.cluster_type}")
            if live.cluster_host:
                print(f"  host       {live.username}@{live.cluster_host}")
            if live.cluster_type == "huggingface":
                print(f"  namespace  {live.hf_namespace} ({live.hf_flavor})")
            print(
                f"  resources  {live.default_cores} CPUs, {live.default_memory}, "
                f"{live.default_time}"
            )
            if unrecognised:
                print("Ignored, not a clustrix setting: " + ", ".join(unrecognised))
            self.set_status("ok", "in use")

    @staticmethod
    def _check_connection(config: ClusterConfig) -> List[str]:
        """Reach the configured backend for real; return what was found.

        Raises when the backend cannot be reached or does not accept the
        credentials. Each branch goes through the same object a job would --
        ``ClusterExecutor`` for SSH, ``HFJobsManager`` for HuggingFace -- so a
        pass here means the job's own connection will be made the same way,
        with the same host-key policy and the same credential gate.
        """
        if config.cluster_type == "local":
            return ["Nothing to connect to: local jobs run in this Python process."]

        if config.cluster_type == "huggingface":
            from .hf_jobs import HFJobsManager

            account = HFJobsManager(config).api.whoami()
            name = account.get("name", "unknown")
            lines = [f"Authenticated to HuggingFace as {name}."]
            namespaces = {name} | {org.get("name") for org in account.get("orgs", [])}
            if config.hf_namespace not in namespaces:
                lines.append(
                    f"Note: {config.hf_namespace!r} is not this account or one of "
                    "its organisations; jobs there may be refused."
                )
            return lines

        from .executor import ClusterExecutor

        with ClusterExecutor(config) as executor:
            executor.connect()
            run = executor.connection_manager.execute_remote_command
            hostname, _ = run("hostname", check=True)
            lines = [
                f"Connected to {config.cluster_host} as {config.username} "
                f"(remote hostname: {hostname.strip()})."
            ]
            if config.cluster_type == "slurm":
                version, _ = run("sbatch --version", check=True)
                lines.append(f"Scheduler found: {version.strip()}.")
        return lines

    def _explain_failure(self, error: Exception, what: str, status: str) -> None:
        """Print why a connection or job failed, and offer the way forward.

        A host this machine has never connected to is the one failure every
        new user meets, and the remedy is a decision rather than a fix: the
        fingerprint is shown and a button offers to trust exactly that key.
        """
        if not isinstance(error, HostKeyVerificationError):
            print(f"{what}: {type(error).__name__}: {error}")
            self.set_status("error", status)
            return
        self._untrusted_host = error
        self.widgets["trust_host_btn"].layout.display = None
        print(f"This machine has not connected to {error.hostname} before.")
        print(f"The host identified itself with the key:\n  {error.fingerprint}")
        print(
            "If that matches the fingerprint your cluster publishes, press "
            "'Trust this host'.\nIf it does not, stop: something may be "
            "impersonating the host."
        )
        self.set_status("error", "unknown host")

    def _on_trust_host(self, button) -> None:
        """Record the host key the user was just shown, then connect."""
        error = self._untrusted_host
        if error is None:
            return
        try:
            known_hosts = trust_offered_host_key(error)
        except OSError as e:
            self._report(f"Could not record the host key: {e}")
            self.set_status("error", "not trusted")
            return
        self._on_test_connect(None)
        with self.widgets["output"]:
            print(f"Recorded the key for {error.hostname} in {known_hosts}.")

    def _on_test_connect(self, button) -> None:
        """Connect and authenticate to the backend on screen, without a job."""
        with self._action("test_connect_btn", "Testing…", "testing…"):
            if self._refuse_if_invalid("test"):
                return
            config = self._config_for_backend()
            try:
                lines = self._check_connection(config)
            except Exception as e:  # noqa: BLE001 - reported to the user below
                # Broad on purpose: this button exists to report *whatever*
                # stops a connection -- DNS, a refused host key, a rejected
                # token -- and each of those raises a different type from a
                # different library.
                self._explain_failure(e, "Connection failed", "not connected")
                return
            print("\n".join(lines))
            self.set_status("ok", "connected")

    def _on_test_submit(self, button) -> None:
        """Run one small real job on the backend on screen and show the result.

        The job goes through exactly the path a real ``@cluster`` call takes.
        Earlier versions printed "4 test jobs executed" without calling an
        executor at all; a test that cannot fail tells you nothing.
        """
        with self._action("test_submit_btn", "Running…", "running…"):
            if self._refuse_if_invalid("run a job with"):
                return
            config = self._config_for_backend()

            from .executor import ClusterExecutor
            from .utils import make_portable_function, serialize_function

            # Compiled from source rather than referenced, so the worker does
            # not need clustrix installed to load it.
            test_job = make_portable_function(_TEST_JOB_SOURCE, "test_job")
            try:
                if config.cluster_type == "local":
                    # `local` has no scheduler; running in this process is
                    # what a local @cluster call does.
                    result = test_job()
                else:
                    with ClusterExecutor(config) as executor:
                        job_id = executor.submit_job(
                            serialize_function(test_job, (), {}),
                            {
                                "cores": config.default_cores,
                                "memory": config.default_memory,
                                "time": config.default_time,
                            },
                        )
                        print(f"Submitted job {job_id}; waiting for the result…")
                        result = executor.wait_for_result(job_id)
            except Exception as e:  # noqa: BLE001 - reported to the user below
                # Broad for the same reason as _on_test_connect.
                self._explain_failure(e, "Test job failed", "job failed")
                return

            print(f"Test job succeeded on {config.cluster_type}.")
            print(f"  ran on    {result.get('host')}")
            print(f"  python    {result.get('python')}")
            print(f"  returned  {result.get('answer')}  (expected 5050)")
            self.set_status("ok", "job completed")

    def _on_auto_setup_ssh(self, button) -> None:
        """Generate an SSH key and install it on the host on screen."""
        with self._action("auto_setup_ssh", "Setting up…", "setting up key…"):
            if self._refuse_if_invalid("set up a key for"):
                return
            password = self.widgets["password"].value
            if not password:
                print(
                    "Enter the account password above first: it is used once, "
                    "to install the new key on the host."
                )
                self.set_status("error", "password needed")
                return

            from .ssh_utils import setup_ssh_keys

            config = self._config_for_backend()
            result = setup_ssh_keys(
                config, password, force_refresh=self.widgets["refresh_keys"].value
            )
            if not result["success"]:
                print(f"SSH key setup failed: {result['error']}")
                self.set_status("error", "key not set up")
                return

            # The key now works, so use it: leaving the password in place
            # would keep authenticating with the thing the key replaces.
            self.widgets["ssh_key_file"].value = result["key_path"]
            self.widgets["password"].value = ""
            if result["key_already_existed"]:
                print(f"A key that already works was found: {result['key_path']}")
            else:
                print(f"Installed a new key on the host: {result['key_path']}")
            print("The SSH key file field now points at it; press Apply to use it.")
            self.set_status("ok", "key ready")

    # -- form <-> configuration --------------------------------------------

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

        _, malformed = _parse_environment_variables(self.widgets["env_vars"].value)
        for entry in malformed:
            problems.append(
                f"Environment variable line {entry!r} is not KEY=value with a "
                "name made of letters, digits and underscores"
            )

        return problems

    #: Fields that only mean something for particular backends. A profile
    #: keeps all of them so nothing is lost while editing, but the applied
    #: configuration must not carry them into a backend that ignores them --
    #: _choose_execution_mode routes on cluster_host, so a leftover host would
    #: send a "local" job to a cluster.
    #:
    #: The line is drawn by asking *what the value names*. Every field here
    #: names **this cluster**: the compute (cluster_host, cluster_port,
    #: remote_work_dir, hf_namespace, hf_flavor), who the job runs as there
    #: (username), the secret that opens that particular door (password,
    #: key_file, hf_token), and what it is allowed to spend there
    #: (hf_allow_gpu_flavors -- GPU flavors bill by the second, so the
    #: permission has to fail safe the moment the target changes). Point the
    #: widget at another backend and every one of them describes somewhere
    #: the job is no longer going.
    #:
    #: ``password_env_var``/``use_env_password`` name a *channel*: which
    #: environment variable a password is read from. Switching backend says
    #: nothing about that variable, so clearing it destroys a setting the
    #: switch had no opinion about -- which is exactly what Apply on a
    #: ``local`` profile used to do. TestACredentialChannelIsNotABackendSetting
    #: holds that in place.
    #:
    #: Stated as a rule that can be applied to the next field: **does the
    #: value stop being correct when the target changes?** Not "where does it
    #: live" -- ``key_file`` is a path on the machine clustrix runs on
    #: exactly as ``password_env_var`` is a variable name on it, so locality
    #: separates nothing and this comment must not be read as claiming it
    #: does. What separates them is what each is *bound* to, and that is a
    #: convention worth naming rather than assuming: one key per host. An SSH
    #: key authenticates you to one particular host -- ``~/.ssh/config`` binds
    #: ``IdentityFile`` inside a ``Host`` stanza for that reason -- so the key
    #: that opens one cluster is the wrong key for the next, and carrying it
    #: over is carrying a credential that cannot work. ``password_env_var`` is
    #: per *install*, not per host: clustrix reads exactly one variable name,
    #: it is the only way to supply a password without writing it to disk, and
    #: it is the variable's *contents* that differ per target, not its name.
    #: Change the target and the key file is wrong; change the target and the
    #: variable name is still right.
    #:
    #: Two tempting distinctions do *not* work, and neither may be used to
    #: move this line again. "It holds no secret" separates nothing:
    #: ``_NOT_ACTUALLY_SECRET`` deliberately keeps ``password_env_var`` and
    #: ``use_env_password`` out of ``SECRET_FIELDS``, so ``save_to_file``
    #: writes both in plaintext -- and ``key_file``, which stays backend-only,
    #: is likewise a name rather than a credential and is likewise written.
    #: "It cannot be recovered from disk" separates nothing either: no member
    #: of this set is unrecoverable, because the reset only clears the
    #: *setting* and every control still shows its value afterwards.
    BACKEND_ONLY_FIELDS = {
        ("ssh", "slurm"): (
            "cluster_host",
            "cluster_port",
            "username",
            "password",
            "key_file",
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
        reset to their defaults.

        The field's *real* default, from ``ClusterConfig()`` -- not ``None``.
        ``cluster_port`` is typed ``int`` and ``remote_work_dir`` is typed
        ``str``; handing either a ``None`` reads as "cleared" while leaving a
        configuration that fails at the first connection or path join.
        """
        data = self._config_data_from_widgets()
        defaults = asdict(ClusterConfig())
        cluster_type = data["cluster_type"]
        for applies_to, field_names in self.BACKEND_ONLY_FIELDS.items():
            if cluster_type not in applies_to:
                for name in field_names:
                    data[name] = defaults[name]
        return data

    def _config_for_backend(self) -> ClusterConfig:
        """The configuration a test or a job started from this form uses."""
        return ClusterConfig(**self._config_data_for_backend())

    def _get_config_from_widgets(self) -> ClusterConfig:
        """The configuration currently shown, as a ClusterConfig."""
        return ClusterConfig(**self._config_data_from_widgets())

    def _config_data_from_widgets(self) -> Dict[str, Any]:
        """The configuration currently shown, as the fields the widget set.

        Apply needs the *keys*, not just the values: a field this widget
        manages but did not set for the current backend has to be reset, while
        a field it does not manage at all must be left alone.
        """
        # Malformed lines are reported by _validate_widget_values; the valid
        # ones are still recorded so a typo does not cost the rest.
        env_vars, _ = _parse_environment_variables(self.widgets["env_vars"].value)
        modules = self.widgets["modules"].value.replace(",", " ").split()

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
            "pre_execution_commands": [
                line.strip()
                for line in self.widgets["pre_exec_commands"].value.splitlines()
                if line.strip()
            ],
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
        # A bare assignment, not set_choice, and deliberately: cluster_type is
        # the one field with an enforced domain, so this menu is authoritative
        # and the value is what can be wrong. It cannot be wrong here -- the
        # argument is a ClusterConfig, whose __post_init__ already rejected
        # anything outside SUPPORTED_CLUSTER_TYPES.
        self.widgets["cluster_type"].value = config.cluster_type

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
        # set_choice, not a bare assignment: ClusterConfig validates neither
        # hf_flavor nor package_manager, so a config naming a flavor newer
        # than this dropdown made the widget impossible to open.
        set_choice(self.widgets["hf_flavor"], config.hf_flavor or "cpu-basic")
        self.widgets["hf_token"].value = config.hf_token or ""
        self.widgets["hf_allow_gpu"].value = bool(config.hf_allow_gpu_flavors)

        # Advanced
        set_choice(self.widgets["package_manager"], config.package_manager or "auto")
        self.widgets["python_executable"].value = config.python_executable or "python"
        self.widgets["clone_env"].value = bool(
            getattr(config, "replicate_local_environment", True)
        )

        # Assigned unconditionally: leaving the previous profile's entries in
        # place is how they migrated between profiles.
        self.widgets["env_vars"].value = "\n".join(
            f"{k}={v}" for k, v in (config.environment_variables or {}).items()
        )
        self.widgets["modules"].value = " ".join(config.module_loads or [])
        self.widgets["pre_exec_commands"].value = "\n".join(
            config.pre_execution_commands or []
        )


def create_modern_cluster_widget(
    profile_manager: Optional[ProfileManager] = None,
) -> "widgets.Widget":
    """Build the configuration widget and return its container, undisplayed."""
    return ModernClustrixWidget(profile_manager).get_widget()


def display_modern_widget() -> None:
    """Display the configuration widget.

    Returns nothing, deliberately: it used to return the ipywidgets
    container, so a cell ending in this call showed the widget twice -- once
    from ``display()`` and once more as the cell's result. Construct
    :class:`ModernClustrixWidget` directly to keep a handle on the controls.
    """
    if not IPYTHON_AVAILABLE:
        print("The configuration widget needs IPython and ipywidgets:")
        print("  pip install 'clustrix[widget]'")
        return

    ModernClustrixWidget().display()


#: Short public name for :func:`display_modern_widget`.
show_widget = display_modern_widget
