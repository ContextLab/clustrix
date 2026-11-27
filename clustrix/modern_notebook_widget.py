"""The notebook configuration widget: choose where ``@cluster`` runs.

What the widget does lives here; how it looks lives in ``widget_style``.
"""

import contextlib
import html
import io
import logging
import os
import re
import warnings
from contextlib import contextmanager
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
    TYPE_CHECKING,
)

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
        "default_partition",
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
        "hf_job_timeout",
        "hf_token",
        "hf_allow_gpu_flavors",
        "package_manager",
        "python_executable",
        "conda_env_name",
        "replicate_local_environment",
        "cleanup_on_success",
        "environment_variables",
        "module_loads",
        "pre_execution_commands",
    }
)


#: What an empty secret field says, and what it says once something has been
#: entered and moved out of it.
SECRET_PLACEHOLDERS = {
    "password": "this session only, never saved",
    "hf_token": "blank: HF_TOKEN or `hf auth login`",
}
SECRET_HELD = "entered; kept in memory only"

#: Profile settings Apply never forwards to ``configure()``. Both weaken a
#: check -- host-key verification, and which container image is handed the
#: HuggingFace token -- and clustrix honours them only from a configuration
#: source it trusts. ``configure()`` *is* such a source, so passing a
#: profile's value through it would turn "a file said so" into "the user
#: said so". They stay in the profile; setting them is done in code.
NOT_APPLIED_FROM_A_PROFILE = frozenset({"ssh_host_key_policy", "hf_image"})

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
    "partition",
    "host",
    "port",
    "username",
    "ssh_key_file",
    "password",
    "local_env_var",
    "home_dir",
    "hf_namespace",
    "hf_flavor",
    "hf_timeout",
    "hf_token",
    "hf_allow_gpu",
    "package_manager",
    "python_executable",
    "conda_env",
    "clone_env",
    "cleanup",
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

#: Above this, a core count is a typo rather than a request.
MAX_PLAUSIBLE_CORES = 4096


def _is_walltime(text: str) -> bool:
    """True for ``MM``, ``MM:SS``, ``HH:MM:SS`` or ``D-HH[:MM[:SS]]``."""
    match = re.fullmatch(r"(?:(\d+)-)?(\d+)(?::(\d{1,2}))?(?::(\d{1,2}))?", text)
    if not match:
        return False
    return all(part is None or int(part) < 60 for part in match.groups()[2:])


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

    Three states are kept apart, because confusing them is how a form like
    this misleads:

    * what ``@cluster`` is using -- the live configuration, named in the
      header and compared against by the *applied* pill;
    * what each profile holds on disk;
    * what is on screen, a **draft** of the selected profile. Editing never
      changes a profile; **Apply** does, and makes it live in the same step.
      A draft survives switching to another profile and back, for the life
      of the widget, and **Revert** throws it away.
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
        # Unapplied edits, per profile name, as _config_data_from_widgets
        # produced them. Held in memory only.
        self._drafts: Dict[str, Dict[str, Any]] = {}
        #: What was typed into a secret field. It is moved here, and the
        #: field emptied, as soon as the browser reports it: Jupyter can save
        #: the form's state into the notebook file ("Save Widget State
        #: Automatically"), and a password left in the field would be saved
        #: with it, in plain text.
        self._secrets: Dict[str, str] = {key: "" for key in SECRET_PLACEHOLDERS}
        # What the form read as just after the selected profile was loaded.
        self._baseline: Dict[str, Any] = {}
        # The refused connection whose host key the user is being asked
        # about, and the button to press again once it is trusted.
        self._untrusted_host: Optional[HostKeyVerificationError] = None
        self._retry_after_trust: Optional[str] = None
        # The profile whose deletion is awaiting a second click.
        self._delete_armed_for: Optional[str] = None
        self.widgets: Dict[str, Any] = {}

        self._create_controls()
        self._container = self._create_layout()
        self._setup_observers()
        self._adopt_live_configuration()
        self._update_ui_for_cluster_type()
        self._refresh_state()

    # -- construction ----------------------------------------------------

    @staticmethod
    def _text(description: str, placeholder: str = "", mono: bool = False):
        control = widgets.Text(
            description=description,
            placeholder=placeholder,
            # Commit on blur or Enter rather than on every keystroke: each
            # commit re-evaluates the whole form.
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
        w["profile_name"] = self._text("Rename to")
        w["profile_name"].value = self.profile_manager.active_profile or ""
        w["add_profile_btn"] = self._button(
            "Duplicate", "Save what is on screen as a new profile"
        )
        w["revert_btn"] = self._button(
            "Revert", "Discard the edits on screen and show the profile as saved"
        )
        w["remove_profile_btn"] = self._button("Delete", "Delete this profile")
        w["remove_profile_btn"].add_class("cx-danger")
        w["profile_state"] = widgets.HTML()
        w["profile_state"].add_class("cx-hint")
        # Typeable, so a file that does not exist yet can be saved to, and a
        # picker over the bundles that do exist, so loading one does not mean
        # remembering where it went.
        w["config_filename"] = widgets.Combobox(
            description="Profiles file (to move profiles between machines)",
            value=DEFAULT_PROFILE_STORE,
            options=self._discover_config_files(),
            placeholder=f"{DEFAULT_PROFILE_STORE}, or a path",
            ensure_option=False,
            continuous_update=False,
        )
        w["save_btn"] = self._button(
            "Export", "Write every saved profile to this file (no credentials)"
        )
        w["load_btn"] = self._button(
            "Import", "Replace every profile with the ones in this file"
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
        w["cpus"] = widgets.IntText(description="CPUs per job", value=1)
        w["ram"] = self._text("Memory per job", "e.g. 16GB")
        w["time"] = self._text("Time limit per job", "HH:MM:SS")
        w["partition"] = self._text("Partition", "the scheduler's default")

        # SSH connection (ssh and slurm)
        w["host"] = self._text("Host", "login.cluster.example.edu")
        w["port"] = widgets.IntText(description="Port", value=22)
        w["username"] = self._text("Username")
        w["ssh_key_file"] = self._text(
            "SSH key file", "blank: ssh-agent and default keys", mono=True
        )
        w["password"] = self._secret_field("password", "Password")
        w["local_env_var"] = self._text(
            "…or the environment variable that holds the password",
            "MY_CLUSTER_PASSWORD",
            mono=True,
        )
        w["home_dir"] = self._text(
            "Directory on the host for job files",
            ClusterConfig().remote_work_dir,
            mono=True,
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
        w["hf_namespace"] = self._text("Namespace", "your username, or an organisation")
        w["hf_flavor"] = widgets.Dropdown(
            description="Hardware", options=list(HF_FLAVORS), value=HF_FLAVORS[0]
        )
        w["hf_timeout"] = self._text("Time limit per job", "e.g. 30m, 2h")
        w["hf_token"] = self._secret_field(
            "hf_token", "Token (this session only, never saved)"
        )
        w["hf_allow_gpu"] = widgets.Checkbox(
            description="Allow GPU hardware, which is billed per second",
            value=False,
            indent=False,
        )

        # Advanced
        w["package_manager"] = widgets.Dropdown(
            description="Package manager",
            options=["auto", "pip", "uv", "conda"],
            value="auto",
        )
        w["python_executable"] = self._text("Python executable", "python", mono=True)
        w["python_executable"].value = "python"
        w["conda_env"] = self._text(
            "Existing conda environment", "blank: build one", mono=True
        )
        w["clone_env"] = widgets.Checkbox(
            description="Install my local packages on the worker",
            value=True,
            indent=False,
        )
        w["cleanup"] = widgets.Checkbox(
            description="Delete job files on the host after success",
            value=True,
            indent=False,
        )
        w["env_vars"] = widgets.Textarea(
            description="Environment variables (this session only, never saved)",
            placeholder="KEY=value, one per line",
            rows=3,
            continuous_update=False,
        )
        w["modules"] = self._text(
            "Modules to load", "space-separated, e.g. python/3.11 cuda", mono=True
        )
        w["pre_exec_commands"] = widgets.Textarea(
            description="Shell commands to run before the job",
            placeholder="one per line, e.g. source ~/setup.sh",
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
            "Apply",
            "Save this profile and make @cluster use it from now on",
            primary=True,
        )
        w["trust_host_btn"] = self._button(
            "Trust this host",
            "Record the host key shown above in known_hosts, then try again",
        )
        w["trust_host_btn"].layout.display = "none"
        w["applied_pill"] = widgets.HTML()
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
        for key in ("refresh_keys", "hf_allow_gpu", "clone_env", "cleanup"):
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
    def _group(*buttons: Any):
        """Buttons that stay together when the row around them wraps."""
        group = widgets.Box(list(buttons))
        group.add_class("cx-group")
        return group

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
        header = widgets.Box([brand, w["applied_pill"]])
        header.add_class("cx-header")

        profile = self._section(
            "Profile",
            self._row(
                (w["profile_dropdown"], "2 1 190px"),
                (w["profile_name"], "2 1 190px"),
                (
                    self._group(
                        w["add_profile_btn"], w["revert_btn"], w["remove_profile_btn"]
                    ),
                    fixed,
                ),
            ),
            w["profile_state"],
        )

        w["resources_row"] = self._row(
            (w["cpus"], "1 1 110px"),
            (w["ram"], "1 1 110px"),
            (w["time"], "1 1 110px"),
            (w["partition"], "1 1 110px"),
        )
        resources = self._section(
            "Where to run",
            self._row((w["cluster_type"], "1 1 100%")),
            w["backend_hint"],
            w["resources_row"],
        )

        w["remote_section"] = self._section(
            "SSH connection",
            self._row(
                (w["host"], "3 1 240px"),
                (w["port"], "0 1 80px"),
                (w["username"], "2 1 140px"),
            ),
            self._row(
                (w["password"], "1 1 240px"),
                (w["local_env_var"], "1 1 240px"),
            ),
            self._row((w["ssh_key_file"], "1 1 240px")),
            self._row((w["auto_setup_ssh"], fixed), (w["refresh_keys"], "1 1 240px")),
            self._row((w["home_dir"], "1 1 240px")),
        )

        w["hf_section"] = self._section(
            "HuggingFace Jobs",
            self._row(
                (w["hf_namespace"], "2 1 200px"),
                (w["hf_flavor"], "1 1 140px"),
                (w["hf_timeout"], "1 1 140px"),
            ),
            self._row((w["hf_token"], "1 1 240px")),
            self._row((w["hf_allow_gpu"], "1 1 240px")),
        )

        w["advanced_section"] = self._section(
            "Advanced",
            self._row(
                (w["package_manager"], "1 1 140px"),
                (w["python_executable"], "1 1 160px"),
                (w["conda_env"], "1 1 180px"),
            ),
            self._row((w["clone_env"], "1 1 240px"), (w["cleanup"], "1 1 240px")),
            self._row((w["modules"], "1 1 240px")),
            self._row(
                (w["env_vars"], "1 1 240px"),
                (w["pre_exec_commands"], "1 1 240px"),
            ),
            self._row(
                (w["config_filename"], "1 1 240px"),
                (self._group(w["save_btn"], w["load_btn"]), fixed),
            ),
        )
        w["advanced_section"].layout.display = "none"

        action_row = widgets.Box(
            [
                w["advanced_toggle"],
                self._group(
                    w["test_connect_btn"], w["test_submit_btn"], w["apply_btn"]
                ),
            ]
        )
        action_row.add_class("cx-row")
        action_row.add_class("cx-actions")
        result_bar = widgets.Box(
            [widgets.HTML("<div class='cx-heading'>Result</div>"), w["status_pill"]]
        )
        result_bar.add_class("cx-result-bar")
        actions = widgets.VBox(
            [action_row, result_bar, w["output"], w["trust_host_btn"]]
        )
        actions.add_class("cx-section")
        w["trust_host_btn"].layout.margin = "10px 0 0 0"
        w["trust_host_btn"].layout.align_self = "flex-start"

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
        w["revert_btn"].on_click(self._on_revert_profile)
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

    @staticmethod
    def _pill(state: str, text: str) -> str:
        if state not in STATUS_STATES:
            raise ValueError(
                f"Unknown status state {state!r}; expected one of {STATUS_STATES}"
            )
        return f"<span class='cx-pill cx-pill-{state}'>{html.escape(text)}</span>"

    def set_status(self, state: str, text: str) -> None:
        """Report the outcome of the last action, beside the console.

        This is *not* the header pill. That one answers a different question
        -- is what is on screen what ``@cluster`` is using? -- and a green
        "connected" up there read as "applied" when nothing had been.
        """
        self.widgets["status_pill"].value = self._pill(state, text)
        output = self.widgets["output"]
        for other in STATUS_STATES:
            output.remove_class(f"cx-state-{other}")
        output.add_class(f"cx-state-{state}")

    def _clear_result(self) -> None:
        """Forget the last action's outcome: the form it described has changed."""
        self.widgets["status_pill"].value = ""
        output = self.widgets["output"]
        output.clear_output()
        for other in STATUS_STATES:
            output.remove_class(f"cx-state-{other}")
        self._forget_untrusted_host()

    def _forget_untrusted_host(self) -> None:
        self._untrusted_host = None
        self._retry_after_trust = None
        self.widgets["trust_host_btn"].layout.display = "none"

    def _show_configuration_in_use(self) -> None:
        """Say in the header what ``@cluster`` will do right now."""
        live = get_config()
        if live.cluster_type == "huggingface":
            where = f"Hugging Face ({live.hf_namespace or 'no namespace'})"
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
        """True when pressing Apply would change nothing ``@cluster`` does."""
        settings, _ = split_config_kwargs(
            self._config_data_for_backend(), reset_fields=WIDGET_MANAGED_FIELDS
        )
        live = asdict(get_config())
        return all(live[name] == value for name, value in settings.items())

    def _is_edited(self) -> bool:
        """True when the form differs from the selected profile as saved."""
        return self._config_data_from_widgets() != self._baseline

    def _refresh_state(self) -> None:
        """Bring every indicator in line with the form, the profile and live."""
        w = self.widgets
        self._show_configuration_in_use()
        if self._form_matches_live_configuration():
            w["applied_pill"].value = self._pill("ok", "in use")
        else:
            w["applied_pill"].value = self._pill("idle", "not applied")

        edited = self._is_edited()
        w["revert_btn"].disabled = not edited
        w["profile_state"].value = (
            "Edited. <b>Apply</b> saves these changes to the profile and uses "
            "them; <b>Revert</b> discards them."
            if edited
            else ""
        )
        w["remove_profile_btn"].disabled = (
            len(self.profile_manager.get_profile_names()) <= 1
        )
        self._disarm_delete()

    def _split_user_at_host(self) -> None:
        """Accept ``alice@login.example.edu`` typed or pasted into Host."""
        w = self.widgets
        user, at, host = str(w["host"].value).strip().rpartition("@")
        if not at or not user or not host:
            return
        self._suspend_profile_capture = True
        try:
            w["host"].value = host
            w["username"].value = user
        finally:
            self._suspend_profile_capture = False

    def _on_field_edit(self, change) -> None:
        if self._suspend_profile_capture:
            return
        if change.get("owner") is self.widgets["host"]:
            self._split_user_at_host()
        # Whatever the console says was about the form as it was.
        self._clear_result()
        self._mark_invalid(())
        self._refresh_state()

    def _update_ui_for_cluster_type(self) -> None:
        """Show the controls the chosen backend reads, and say what it does."""
        w = self.widgets
        cluster_type = w["cluster_type"].value

        def show(key: str, visible: bool) -> None:
            w[key].layout.display = None if visible else "none"

        show("remote_section", cluster_type in ("ssh", "slurm"))
        show("hf_section", cluster_type == "huggingface")
        # HuggingFace Jobs takes its size from the hardware choice and its
        # limit from its own timeout; CPUs, memory and walltime are not read.
        show("resources_row", cluster_type != "huggingface")
        show("partition", cluster_type == "slurm")
        # Nothing to connect to, and a job "here" proves nothing.
        show("test_connect_btn", cluster_type != "local")
        w["backend_hint"].value = html.escape(
            BACKEND_DESCRIPTIONS.get(cluster_type, "")
        )

    def _on_cluster_type_change(self, change) -> None:
        self._update_ui_for_cluster_type()

    def _on_toggle_advanced(self, button) -> None:
        section = self.widgets["advanced_section"]
        opening = section.layout.display == "none"
        section.layout.display = None if opening else "none"
        self.widgets["advanced_toggle"].description = (
            "Hide advanced settings" if opening else "Advanced settings"
        )

    #: Buttons that start something. While one runs they are all disabled:
    #: the kernel is busy, and a click then would queue silently behind it.
    ACTION_BUTTONS = (
        "apply_btn",
        "test_connect_btn",
        "test_submit_btn",
        "auto_setup_ssh",
        "save_btn",
        "load_btn",
        "add_profile_btn",
        "remove_profile_btn",
        "revert_btn",
        "trust_host_btn",
    )

    @contextmanager
    def _action(self, key: str, busy_label: str, busy_status: str) -> Iterator[None]:
        """Run the action behind button ``key``: clear, show progress, restore.

        The console is cleared first so the result on screen is always the
        result of the button just pressed.
        """
        w = self.widgets
        self._clear_result()
        button = w[key]
        original = button.description
        was_disabled = {name: w[name].disabled for name in self.ACTION_BUTTONS}
        for name in self.ACTION_BUTTONS:
            w[name].disabled = True
        button.description = busy_label
        self.set_status("busy", busy_status)
        try:
            with w["output"]:
                yield
        finally:
            button.description = original
            for name, disabled in was_disabled.items():
                w[name].disabled = disabled
            self._refresh_state()

    def _report(self, state: str, status: str, *lines: str) -> None:
        """Replace the console's contents with ``lines`` and say how it went."""
        self._clear_result()
        with self.widgets["output"]:
            print("\n".join(lines))
        self.set_status(state, status)

    def _adopt_live_configuration(self) -> None:
        """Open showing the configuration clustrix is actually using.

        A session that had already called ``configure(...)`` must not open on
        an unrelated profile: the form would contradict the library, and
        Apply would overwrite the real configuration with whatever happened
        to be on screen. A live configuration that differs from the shipped
        defaults becomes its own profile, visible in the dropdown next to
        the templates.
        """
        live = get_config()
        if asdict(live) != asdict(ClusterConfig()):
            # Never overwrite a profile of this name left by an earlier
            # session: it may hold settings the live config does not.
            name = LIVE_PROFILE_NAME
            existing = self.profile_manager.get_profile_names()
            if name in existing and asdict(
                self.profile_manager.load_profile(name)
            ) != asdict(live):
                suffix = 2
                while f"{name} ({suffix})" in existing:
                    suffix += 1
                name = f"{name} ({suffix})"
            self._save_profile(name, live)
            self._show_profile(name)
        elif self.profile_manager.active_profile:
            self._show_profile(self.profile_manager.active_profile)

    # -- profiles ----------------------------------------------------------

    def _save_profile(self, name: str, config: ClusterConfig) -> None:
        """Save ``config`` as profile ``name``.

        ProfileManager warns, once, that secrets are not written to disk. In
        a notebook a warning lands as a red block above the form, away from
        where the user is looking, so that one warning is taken out of the
        stream here and said in the console instead -- by
        ``_session_only_note``, on every Apply rather than once. Any other
        warning is passed on untouched.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.profile_manager.save_profile(name, config)
        for warning in caught:
            if "Profiles are not a credential store" not in str(warning.message):
                warnings.warn_explicit(
                    warning.message,
                    warning.category,
                    warning.filename,
                    warning.lineno,
                )

    def _session_only_note(self) -> Optional[str]:
        """Name what Apply did *not* write to disk, if anything.

        Passwords, tokens and environment variables are withheld from the
        profile store: clustrix cannot tell a setting from a credential in a
        variable's value, so it writes none of them.
        """
        w = self.widgets
        held = [
            label
            for label, value in (
                ("the password", self.secret("password")),
                ("the token", self.secret("hf_token")),
                ("environment variables", w["env_vars"].value.strip()),
            )
            if value
        ]
        if not held:
            return None
        return (
            "Kept for this session only, never written to disk: "
            + ", ".join(held)
            + "."
        )

    def _update_profile_dropdown(self) -> None:
        """Re-read the profile names; the active profile stays selected."""
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
        """Put profile ``name`` on screen: as saved, then any draft over it."""
        config = self.profile_manager.set_active_profile(name)
        self._update_profile_dropdown()
        self._suspend_profile_capture = True
        try:
            self._load_config_to_widgets(config)
            self._baseline = self._config_data_from_widgets()
            draft = self._drafts.get(name)
            if draft is not None:
                self._load_data_to_widgets(draft)
            self._update_ui_for_cluster_type()
        finally:
            self._suspend_profile_capture = False
        self._mark_invalid(())
        self._refresh_state()

    def _keep_draft(self, name: Optional[str]) -> None:
        """Remember unapplied edits to ``name`` for when it is shown again."""
        if not name:
            return
        if self._is_edited():
            self._drafts[name] = self._config_data_from_widgets()
        else:
            self._drafts.pop(name, None)

    def _on_profile_change(self, change) -> None:
        if self._suspend_profile_capture:
            return
        # `change["old"]` is the profile being left; its edits are kept as a
        # draft, not written to it.
        self._keep_draft(change.get("old"))
        self._clear_result()
        if change["new"]:
            self._show_profile(change["new"])

    def _on_revert_profile(self, button) -> None:
        name = self.widgets["profile_dropdown"].value
        if not name:
            return
        self._drafts.pop(name, None)
        self._clear_result()
        self._show_profile(name)

    def _on_rename_profile(self, change) -> None:
        if self._suspend_profile_capture:
            return
        old_name = self.widgets["profile_dropdown"].value
        new_name = (change["new"] or "").strip()
        if not old_name:
            return
        if new_name == old_name:
            # Only whitespace differed; show the name as it is.
            self._update_profile_dropdown()
            return
        self._keep_draft(old_name)
        try:
            if not new_name:
                raise ValueError("A profile needs a name")
            self.profile_manager.rename_profile(old_name, new_name)
        except (ValueError, OSError) as e:
            self._show_profile(old_name)
            self._report("error", "not renamed", f"Could not rename '{old_name}': {e}")
            return
        if old_name in self._drafts:
            self._drafts[new_name] = self._drafts.pop(old_name)
        self._clear_result()
        self._show_profile(new_name)

    def _on_add_profile(self, button) -> None:
        current = self.widgets["profile_dropdown"].value
        if not current:
            return
        try:
            # What is on screen becomes the new profile; the one it was
            # copied from keeps what it had.
            config = self._get_config_from_widgets()
            new_name = self.profile_manager.clone_profile(current)
            self._save_profile(new_name, config)
        except (ValueError, TypeError, OSError) as e:
            self._report(
                "error", "not duplicated", f"Could not duplicate '{current}': {e}"
            )
            return
        self._drafts.pop(current, None)
        self._show_profile(new_name)
        self._report(
            "ok",
            "profile created",
            f"Created profile '{new_name}' from what was on screen.",
            "Give it a name of your own in 'Rename to'.",
        )

    def _disarm_delete(self) -> None:
        self._delete_armed_for = None
        button = self.widgets["remove_profile_btn"]
        button.description = "Delete"
        button.remove_class("cx-armed")

    def _on_remove_profile(self, button) -> None:
        """Delete the selected profile -- on the second click.

        There is no undo, and the button sits beside two harmless ones, so
        the first click only arms it. Anything else the user does disarms it.
        """
        current = self.widgets["profile_dropdown"].value
        if not current:
            return
        delete_button = self.widgets["remove_profile_btn"]
        if self._delete_armed_for != current:
            self._delete_armed_for = current
            delete_button.description = "Confirm delete"
            delete_button.add_class("cx-armed")
            return
        try:
            self.profile_manager.remove_profile(current)
        except (ValueError, OSError) as e:
            self._report("error", "not deleted", f"Could not delete '{current}': {e}")
            return
        self._drafts.pop(current, None)
        remaining = self.profile_manager.get_profile_names()
        self._show_profile(self.profile_manager.active_profile or remaining[0])
        self._report("ok", "profile deleted", f"Deleted profile '{current}'.")

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
        if not filename or not filename.strip():
            # Not clustrix.yml: that is the library's own configuration file,
            # in a different format.
            filename = DEFAULT_PROFILE_STORE
        filename = filename.strip()
        if os.path.isabs(filename) or os.sep in filename:
            return os.path.expanduser(filename)
        config_dir = get_config_dir()
        # 0700 at every level: mkdir(parents=True) leaves ~/.clustrix at the
        # umask default, and traversing it is enough to reach the profile
        # store inside it by name.
        _mkdir_private(config_dir)
        return str(config_dir / filename)

    def _on_save_config(self, button) -> None:
        """Export every saved profile to the named file."""
        with self._action("save_btn", "Exporting…", "exporting…"):
            try:
                filename = self._resolve_config_path(
                    self.widgets["config_filename"].value
                )
                self.profile_manager.save_to_file(filename)
            except (ValueError, OSError, yaml.YAMLError) as e:
                print(f"Could not export: {e}")
                self.set_status("error", "export failed")
                return
            self._refresh_config_files()
            count = len(self.profile_manager.get_profile_names())
            # A bare filename resolves under the clustrix config directory, so
            # the full path is the only way to tell where it went.
            print(f"Exported {count} profile(s) to {filename}")
            print("Passwords and tokens are never written to this file.")
            if self._is_edited() or self._drafts:
                print("Edits that have not been applied are not included.")
            self.set_status("ok", "exported")

    def _on_load_config(self, button) -> None:
        """Replace every profile with the ones in the named file."""
        with self._action("load_btn", "Importing…", "importing…"):
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
                print(f"Could not import: {e}")
                self.set_status("error", "import failed")
                return
            self._drafts.clear()
            names = self.profile_manager.get_profile_names()
            self._show_profile(self.profile_manager.active_profile or names[0])
            print(f"Imported {len(names)} profile(s) from {filename}:")
            print("  " + ", ".join(names))
            self.set_status("ok", "imported")

    # -- apply and test ----------------------------------------------------

    def _mark_invalid(self, keys: Iterable[str]) -> None:
        """Outline exactly the controls named in ``keys`` as needing attention."""
        wanted = set(keys)
        for key in FORM_CONTROLS:
            control = self.widgets[key]
            if key in wanted:
                control.add_class("cx-invalid")
            else:
                control.remove_class("cx-invalid")

    def _refuse_if_invalid(self, verb: str) -> bool:
        """Say what is wrong with the form, and where; True when something is."""
        problems = self._validation_problems()
        self._mark_invalid(key for key, _ in problems)
        if not problems:
            return False
        print(f"Cannot {verb} this configuration. The outlined fields need fixing:")
        for _, problem in problems:
            print(f"  - {problem}")
        self.set_status("error", "invalid")
        return True

    def _on_apply_config(self, button) -> None:
        """Save the form to its profile and make ``@cluster`` use it."""
        with self._action("apply_btn", "Applying…", "applying…"):
            if self._refuse_if_invalid("apply"):
                return
            name = self.widgets["profile_dropdown"].value
            try:
                if name:
                    self._save_profile(name, self._get_config_from_widgets())
                # Reset the fields this widget manages, then apply what is on
                # screen. Merging only the non-empty values left a previous
                # Apply's host in place after switching to a local profile;
                # replacing the configuration wholesale discarded settings
                # that have no control here. ``reset_fields`` does neither.
                settings, unrecognised = split_config_kwargs(
                    self._config_data_for_backend(),
                    reset_fields=WIDGET_MANAGED_FIELDS,
                )
                # What the profile sets that has no control here goes live
                # with it, where it differs from the default; a setting made
                # from code that the profile has no opinion on is left alone.
                defaults = asdict(ClusterConfig())
                for field, value in self._settings_without_a_control().items():
                    if (
                        field not in NOT_APPLIED_FROM_A_PROFILE
                        and value != defaults[field]
                    ):
                        settings[field] = value
                configure(**settings)
            except (ValueError, TypeError, OSError) as e:
                print(f"Not applied: {e}")
                self.set_status("error", "not applied")
                return
            if name:
                self._drafts.pop(name, None)
                self._baseline = self._config_data_from_widgets()

            # Read back what @cluster will actually see, rather than echoing
            # the form: fields the chosen backend ignores were just reset.
            live = get_config()
            print(f"Applied. New @cluster calls run on: {live.cluster_type}")
            if live.cluster_host:
                print(f"  host       {live.username}@{live.cluster_host}")
            if live.cluster_type == "huggingface":
                print(f"  namespace  {live.hf_namespace} ({live.hf_flavor})")
            else:
                print(
                    f"  per job    {live.default_cores} CPUs, {live.default_memory}, "
                    f"{live.default_time}"
                )
            if name:
                print(f"Saved to the profile '{name}'.")
            note = self._session_only_note()
            if note:
                print(note)
            if unrecognised:
                print("Ignored, not a clustrix setting: " + ", ".join(unrecognised))
            self.set_status("ok", "applied")

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

        # The connection layers narrate each credential source they try. In
        # a console meant to answer "did it connect?", three paragraphs of
        # that ahead of the answer bury it, so it is collected and dropped;
        # a failure is reported by the exception, which says more.
        with contextlib.redirect_stdout(io.StringIO()):
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

    def _explain_failure(
        self, error: Exception, what: str, status: str, retry: str
    ) -> None:
        """Print why a connection or job failed, and offer the way forward.

        A host this machine has never connected to is the one failure every
        new user meets, and the remedy is a decision rather than a fix: the
        fingerprint is shown and a button offers to trust exactly that key,
        then presses ``retry`` -- the button that was being pressed -- again.
        """
        if not isinstance(error, HostKeyVerificationError):
            print(f"{what}: {type(error).__name__}: {error}")
            self.set_status("error", status)
            return
        self._untrusted_host = error
        self._retry_after_trust = retry
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
        """Record the host key the user was just shown, then try again."""
        error, retry = self._untrusted_host, self._retry_after_trust
        if error is None or retry is None:
            return
        try:
            known_hosts = trust_offered_host_key(error)
        except OSError as e:
            self._report("error", "not trusted", f"Could not record the host key: {e}")
            return
        handlers = {
            "test_connect_btn": self._on_test_connect,
            "test_submit_btn": self._on_test_submit,
            "auto_setup_ssh": self._on_auto_setup_ssh,
        }
        handlers[retry](None)
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
                self._explain_failure(
                    e, "Connection failed", "not connected", "test_connect_btn"
                )
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
                                "partition": config.default_partition,
                            },
                        )
                        print(f"Submitted job {job_id}; waiting for the result…")
                        result = executor.wait_for_result(job_id)
            except Exception as e:  # noqa: BLE001 - reported to the user below
                # Broad for the same reason as _on_test_connect.
                self._explain_failure(
                    e, "Test job failed", "job failed", "test_submit_btn"
                )
                return

            print(f"Test job succeeded on {config.cluster_type}.")
            print(f"  ran on    {result.get('host')}")
            print(f"  python    {result.get('python')}")
            print(f"  returned  {result.get('answer')}  (expected 5050)")
            self.set_status("ok", "job completed")

    def _secret_field(self, key: str, description: str) -> "widgets.Password":
        """A field whose content is held here and not in the form."""
        field = widgets.Password(
            description=description,
            placeholder=SECRET_PLACEHOLDERS[key],
            # One report when the field is left, not one per keystroke: the
            # field is emptied on that report, which must not happen mid-word.
            continuous_update=False,
        )

        def typed(change) -> None:
            if change["new"]:
                self._set_secret(key, change["new"])

        field.observe(typed, names="value")
        return field

    def secret(self, key: str) -> str:
        """The password or token entered for this session, or ``""``."""
        return self._secrets[key]

    def _set_secret(self, key: str, value: str) -> None:
        self._secrets[key] = value
        field = self.widgets.get(key)
        if field is None:  # still being built
            return
        field.placeholder = SECRET_HELD if value else SECRET_PLACEHOLDERS[key]
        if field.value:
            field.value = ""

    def _on_auto_setup_ssh(self, button) -> None:
        """Generate an SSH key and install it on the host on screen."""
        with self._action("auto_setup_ssh", "Setting up…", "setting up key…"):
            if self._refuse_if_invalid("set up a key for"):
                return
            password = self.secret("password")
            if not password:
                self._mark_invalid(("password",))
                print(
                    "Enter the account password first: it is used once, to "
                    "install the new key on the host."
                )
                self.set_status("error", "password needed")
                return

            from .ssh_utils import setup_ssh_keys

            config = self._config_for_backend()
            try:
                # Connect first, the way a job would. Key setup reports an
                # unknown host as a line of text; this reports it as the
                # decision it is, with the fingerprint and the button.
                self._check_connection(config)
            except Exception as e:  # noqa: BLE001 - reported to the user below
                self._explain_failure(
                    e, "Could not reach the host", "not connected", "auto_setup_ssh"
                )
                return

            with contextlib.redirect_stdout(io.StringIO()):
                result = setup_ssh_keys(
                    config, password, force_refresh=self.widgets["refresh_keys"].value
                )
            if not result["success"]:
                print(f"SSH key setup failed: {result['error']}")
                self.set_status("error", "key not set up")
                return

            # The key now works, so use it: leaving the password in place
            # would keep authenticating with the thing the key replaces.
            self._suspend_profile_capture = True
            try:
                self.widgets["ssh_key_file"].value = result["key_path"]
                self._set_secret("password", "")
            finally:
                self._suspend_profile_capture = False
            if result["key_already_existed"]:
                print(f"A key that already works was found:\n  {result['key_path']}")
            else:
                print(f"Installed a new key on the host:\n  {result['key_path']}")
            print("The SSH key file field now points at it. Press Apply to use it.")
            self.set_status("ok", "key ready")

    # -- form <-> configuration --------------------------------------------

    def _validation_problems(self) -> List[Tuple[str, str]]:
        """Every problem with the form, as ``(control, what is wrong)``.

        Checked here, before anything is sent anywhere, because the
        alternative is a job that fails on the cluster minutes later with an
        error that names none of it.
        """
        w = self.widgets
        problems: List[Tuple[str, str]] = []
        cluster_type = w["cluster_type"].value

        if cluster_type != "huggingface":
            cores = w["cpus"].value
            # -1 is meaningful: "use every core on the node".
            if not isinstance(cores, int) or cores == 0 or cores < -1:
                problems.append(
                    ("cpus", f"CPUs must be positive (or -1 for all); got {cores}")
                )
            elif cores > MAX_PLAUSIBLE_CORES:
                problems.append(
                    ("cpus", f"{cores} CPUs is more than any single job can use")
                )

            memory = str(w["ram"].value).strip()
            matched = MEMORY_PATTERN.match(memory)
            if not memory:
                problems.append(("ram", "Memory is required"))
            elif not matched or float(matched.group(1)) <= 0:
                # A bare number is accepted: clustrix reads it as gigabytes
                # everywhere else (utils.normalize_memory), and refusing here
                # what the library accepts would make a valid profile
                # impossible to apply from the form.
                problems.append(
                    (
                        "ram",
                        f"Memory {memory!r} is not a size; expected something "
                        "like '16GB' or '512MB', above zero",
                    )
                )

            walltime = str(w["time"].value).strip()
            if not walltime:
                problems.append(("time", "A time limit is required"))
            elif not _is_walltime(walltime):
                problems.append(
                    (
                        "time",
                        f"Time limit {walltime!r} is not a duration; expected "
                        "HH:MM:SS (or D-HH:MM:SS) with minutes and seconds "
                        "below 60",
                    )
                )

        if cluster_type in ("ssh", "slurm"):
            backend = BACKEND_TILES[cluster_type][0]
            if not str(w["host"].value).strip():
                problems.append(("host", f"{backend} needs a host"))
            if not str(w["username"].value).strip():
                problems.append(("username", f"{backend} needs a username"))
            port = w["port"].value
            if not isinstance(port, int) or not 1 <= port <= 65535:
                problems.append(
                    ("port", f"Port must be between 1 and 65535; got {port}")
                )

        if cluster_type == "huggingface":
            if not str(w["hf_namespace"].value).strip():
                problems.append(
                    (
                        "hf_namespace",
                        "A HuggingFace namespace is required: your username, "
                        "or an organisation you can run jobs in",
                    )
                )
            timeout = str(w["hf_timeout"].value).strip()
            if timeout and not re.fullmatch(r"\d+(\.\d+)?[smhd]?", timeout):
                problems.append(
                    (
                        "hf_timeout",
                        f"Time limit {timeout!r} is not a duration; expected "
                        "something like '30m', '2h' or '1d'",
                    )
                )

        if not str(w["python_executable"].value).strip():
            problems.append(("python_executable", "A Python executable is required"))

        _, malformed = _parse_environment_variables(w["env_vars"].value)
        for entry in malformed:
            problems.append(
                (
                    "env_vars",
                    f"Environment variable line {entry!r} is not KEY=value with "
                    "a name made of letters, digits and underscores",
                )
            )

        return problems

    def _validate_widget_values(self) -> List[str]:
        """Every problem with what is currently on screen, in words."""
        return [problem for _, problem in self._validation_problems()]

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
        ("slurm",): ("default_partition",),
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
            "hf_job_timeout",
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

    def _settings_without_a_control(self) -> Dict[str, Any]:
        """What the selected profile holds that the form cannot show.

        ``ClusterConfig`` has many more fields than there are controls. A
        profile that sets one -- a poll interval, a setup timeout -- must
        keep it through Apply and Duplicate, which rebuild the profile from
        the form.
        """
        name = self.widgets["profile_dropdown"].value
        if name not in self.profile_manager.get_profile_names():
            return {}
        saved = asdict(self.profile_manager.load_profile(name))
        return {
            field: value
            for field, value in saved.items()
            if field not in WIDGET_MANAGED_FIELDS
        }

    def _config_for_backend(self) -> ClusterConfig:
        """The configuration a test or a job started from this form uses."""
        return ClusterConfig(
            **{**self._settings_without_a_control(), **self._config_data_for_backend()}
        )

    def _get_config_from_widgets(self) -> ClusterConfig:
        """The profile as it would be saved: the form over what it cannot show."""
        return ClusterConfig(
            **{**self._settings_without_a_control(), **self._config_data_from_widgets()}
        )

    def _config_data_from_widgets(self) -> Dict[str, Any]:
        """The configuration currently shown, as the fields the widget set.

        Every managed field, every time -- not just the current backend's --
        so that a profile keeps its SSH settings while its Hugging Face ones
        are being looked at. ``_config_data_for_backend`` is what narrows
        this to the backend in use.

        Text is stripped: a host pasted with a trailing space is the same
        host, and the error for the unstripped one ("Name or service not
        known") does not say why.
        """
        w = self.widgets

        def text(key: str) -> str:
            return str(w[key].value).strip()

        # Malformed lines are reported by validation; the valid ones are
        # still recorded so a typo does not cost the rest.
        env_vars, _ = _parse_environment_variables(w["env_vars"].value)
        return {
            "cluster_type": w["cluster_type"].value,
            "default_cores": w["cpus"].value,
            "default_memory": text("ram"),
            "default_time": text("time"),
            "default_partition": text("partition") or None,
            # SSH
            "cluster_host": text("host") or None,
            "cluster_port": w["port"].value,
            "username": text("username") or None,
            "password": self.secret("password") or None,
            "key_file": text("ssh_key_file") or None,
            "password_env_var": text("local_env_var"),
            # Naming the variable only takes effect if clustrix is also told
            # to look it up.
            "use_env_password": bool(text("local_env_var")),
            "remote_work_dir": text("home_dir") or ClusterConfig().remote_work_dir,
            # HuggingFace
            "hf_namespace": text("hf_namespace") or None,
            "hf_flavor": w["hf_flavor"].value,
            "hf_job_timeout": text("hf_timeout") or None,
            "hf_token": self.secret("hf_token") or None,
            "hf_allow_gpu_flavors": bool(w["hf_allow_gpu"].value),
            # Advanced
            "package_manager": w["package_manager"].value,
            "python_executable": text("python_executable"),
            "conda_env_name": text("conda_env") or None,
            "replicate_local_environment": bool(w["clone_env"].value),
            "cleanup_on_success": bool(w["cleanup"].value),
            "environment_variables": env_vars,
            "module_loads": w["modules"].value.replace(",", " ").split(),
            "pre_execution_commands": [
                line.strip()
                for line in w["pre_exec_commands"].value.splitlines()
                if line.strip()
            ],
        }

    def _load_config_to_widgets(self, config: ClusterConfig) -> None:
        """Show ``config`` in the controls."""
        self._load_data_to_widgets(asdict(config))

    def _load_data_to_widgets(self, data: Mapping[str, Any]) -> None:
        """Show configuration ``data`` in the controls.

        The exact inverse of ``_config_data_from_widgets``, field for field,
        including resetting a control when ``data`` does not set its field:
        a control left holding the previous profile's value is how settings
        migrated between profiles. It takes a mapping rather than a
        ``ClusterConfig`` so that a draft -- which may hold a value the
        config class would refuse -- can be put back on screen as typed.
        """
        w = self.widgets
        defaults = ClusterConfig()

        # cluster_type is the one field with an enforced domain, so this menu
        # is authoritative; everything reaching here has passed
        # SUPPORTED_CLUSTER_TYPES already.
        w["cluster_type"].value = data["cluster_type"]

        def number(field: str, fallback: int) -> int:
            # `or` would turn a draft's 0 into the default; only a missing
            # value falls back.
            value = data.get(field)
            return fallback if value is None else value

        w["cpus"].value = number("default_cores", defaults.default_cores)
        w["ram"].value = str(data.get("default_memory") or defaults.default_memory)
        w["time"].value = str(data.get("default_time") or defaults.default_time)
        w["partition"].value = data.get("default_partition") or ""

        # SSH
        w["host"].value = data.get("cluster_host") or ""
        w["port"].value = number("cluster_port", defaults.cluster_port)
        w["username"].value = data.get("username") or ""
        self._set_secret("password", data.get("password") or "")
        # Empty, never a placeholder path: a key file that is set is tried
        # first, and a made-up one shadows the password.
        w["ssh_key_file"].value = data.get("key_file") or ""
        w["local_env_var"].value = data.get("password_env_var") or ""
        w["home_dir"].value = data.get("remote_work_dir") or ""

        # HuggingFace. set_choice, not assignment: neither hf_flavor nor
        # package_manager is validated, so a saved value newer than these
        # menus must widen them rather than make the widget unopenable.
        w["hf_namespace"].value = data.get("hf_namespace") or ""
        set_choice(w["hf_flavor"], data.get("hf_flavor") or HF_FLAVORS[0])
        w["hf_timeout"].value = data.get("hf_job_timeout") or ""
        self._set_secret("hf_token", data.get("hf_token") or "")
        w["hf_allow_gpu"].value = bool(data.get("hf_allow_gpu_flavors"))

        # Advanced
        set_choice(w["package_manager"], data.get("package_manager") or "auto")
        w["python_executable"].value = data.get("python_executable") or "python"
        w["conda_env"].value = data.get("conda_env_name") or ""
        w["clone_env"].value = bool(data.get("replicate_local_environment", True))
        w["cleanup"].value = bool(data.get("cleanup_on_success", True))
        w["env_vars"].value = "\n".join(
            f"{key}={value}"
            for key, value in (data.get("environment_variables") or {}).items()
        )
        w["modules"].value = " ".join(data.get("module_loads") or [])
        w["pre_exec_commands"].value = "\n".join(
            data.get("pre_execution_commands") or []
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
