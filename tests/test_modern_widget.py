"""The notebook configuration widget, driven the way a user drives it.

Nothing here is mocked. The widget is a real ``ModernClustrixWidget`` built
from real ipywidgets controls (which need no browser to construct), its
buttons are pressed by calling the handlers they are wired to, profiles live
in a real temporary ``~/.clustrix``, local jobs really run, and everything
that says "connected" connected to a real SSH server on loopback
(``tests/ssh_server.py``).

What is *not* here, because it already has a home:

* profile switching, the Load menu's discovery, persistence across a restart
  -- ``tests/unit/test_widget_profiles.py``
* every validation rule -- ``tests/unit/test_widget_validation.py``
* what Apply resets and what it leaves alone -- ``tests/unit/test_widget_apply.py``
* rename refusals -- ``tests/unit/test_widget_rename_never_destroys_a_profile.py``
* secrets and saved files -- ``tests/unit/test_widget_save_is_not_a_credential_store.py``
* an unknown host key -- ``tests/unit/test_widget_trusts_a_host_only_when_asked.py``
"""

import os
import pathlib
import platform
import shutil
import socket
from dataclasses import asdict

import pytest

pytest.importorskip("ipywidgets")

import ipywidgets  # noqa: E402

import clustrix  # noqa: E402
from clustrix import cluster, configure, get_config  # noqa: E402
from clustrix.config import SUPPORTED_CLUSTER_TYPES, ClusterConfig  # noqa: E402
from clustrix.modern_notebook_widget import (  # noqa: E402
    FORM_CONTROLS,
    WIDGET_MANAGED_FIELDS,
    ModernClustrixWidget,
    _parse_environment_variables,
    create_modern_cluster_widget,
    display_modern_widget,
    show_widget,
)
from clustrix.widget_style import STYLESHEET  # noqa: E402
from tests.ssh_server import LocalSSHServer  # noqa: E402
from tests.widget_console import pill, press  # noqa: E402

PASSWORD = "the-right-password"

BUTTONS = {
    "add_profile_btn": "_on_add_profile",
    "remove_profile_btn": "_on_remove_profile",
    "save_btn": "_on_save_config",
    "load_btn": "_on_load_config",
    "advanced_toggle": "_on_toggle_advanced",
    "auto_setup_ssh": "_on_auto_setup_ssh",
    "test_connect_btn": "_on_test_connect",
    "test_submit_btn": "_on_test_submit",
    "apply_btn": "_on_apply_config",
    "trust_host_btn": "_on_trust_host",
}


def _click(widget, key, capsys):
    """Press button ``key``; return what the console then showed."""
    handler = getattr(widget, BUTTONS[key])
    return press(lambda: handler(widget.widgets[key]), widget.widgets["output"], capsys)


def _in_layout(container):
    """Every widget reachable from ``container``, by identity."""
    found = {id(container)}
    for child in getattr(container, "children", ()):
        found |= _in_layout(child)
    return found


# --------------------------------------------------------------------------
# The form
# --------------------------------------------------------------------------


class TestTheForm:
    def test_every_named_control_is_on_the_page(self):
        """A control that is created but never laid out is a setting the user
        cannot reach, and nothing else would notice."""
        widget = ModernClustrixWidget()
        on_page = _in_layout(widget.get_widget())

        expected = set(FORM_CONTROLS) | set(BUTTONS)
        expected |= {"profile_dropdown", "profile_name", "config_filename"}
        expected |= {"refresh_keys", "status_pill", "in_use", "output", "backend_hint"}
        expected |= {"remote_section", "hf_section", "advanced_section"}

        assert expected <= set(widget.widgets), expected - set(widget.widgets)
        missing = sorted(
            k for k in widget.widgets if id(widget.widgets[k]) not in on_page
        )
        assert missing == [], f"created but not in the layout: {missing}"

    def test_every_button_is_wired_to_its_handler(self):
        """Pressing the real button must reach the real handler. ``click()``
        is ipywidgets' own entry point for a press."""
        widget = ModernClustrixWidget()
        section = widget.widgets["advanced_section"]
        assert section.layout.display == "none"

        widget.widgets["advanced_toggle"].click()

        assert section.layout.display is None
        assert widget.widgets["advanced_toggle"].description == "Hide advanced settings"
        widget.widgets["advanced_toggle"].click()
        assert section.layout.display == "none"
        assert widget.widgets["advanced_toggle"].description == "Advanced settings"

        widget.widgets["apply_btn"].click()
        assert pill(widget) == "in use"
        for key in BUTTONS:
            assert widget.widgets[key]._click_handlers.callbacks, f"{key} does nothing"

    def test_the_backend_selector_offers_exactly_the_supported_backends(self):
        widget = ModernClustrixWidget()
        selector = widget.widgets["cluster_type"]

        assert [value for _, value in selector.options] == list(SUPPORTED_CLUSTER_TYPES)
        # A (label, value) pair per backend: the value is what is configured.
        selector.label = "Hugging Face"
        assert selector.value == "huggingface"

    @pytest.mark.parametrize("backend", SUPPORTED_CLUSTER_TYPES)
    def test_choosing_a_backend_says_what_it_means(self, backend):
        widget = ModernClustrixWidget()
        hints = set()
        for other in SUPPORTED_CLUSTER_TYPES:
            widget.widgets["cluster_type"].value = other
            hints.add(widget.widgets["backend_hint"].value)

        widget.widgets["cluster_type"].value = backend

        assert widget.widgets["backend_hint"].value
        assert len(hints) == len(SUPPORTED_CLUSTER_TYPES), "two backends share a hint"

    def test_the_container_carries_its_own_stylesheet(self):
        """The CSS travels inside the widget, so it arrives wherever the
        widget is displayed -- not in whichever cell constructed it."""
        container = ModernClustrixWidget().get_widget()

        assert "clustrix-widget" in container._dom_classes
        first = container.children[0]
        assert isinstance(first, ipywidgets.HTML)
        assert first.value == STYLESHEET
        assert "<style>" in STYLESHEET and ".clustrix-widget" in STYLESHEET

    def test_get_widget_returns_the_same_single_container(self):
        widget = ModernClustrixWidget()

        assert widget.get_widget() is widget.get_widget()
        assert isinstance(create_modern_cluster_widget(), ipywidgets.VBox)

    def test_displaying_returns_nothing(self):
        """Returning the container made a cell ending in this call show the
        widget twice: once from display(), once as the cell's result."""
        assert display_modern_widget() is None
        assert show_widget is display_modern_widget
        assert ModernClustrixWidget().display() is None

    def test_an_unknown_status_state_is_refused(self):
        widget = ModernClustrixWidget()

        with pytest.raises(ValueError, match="Unknown status state"):
            widget.set_status("purple", "anything")

    def test_status_text_is_escaped(self):
        """Status text can carry a hostname or an error from elsewhere."""
        widget = ModernClustrixWidget()

        widget.set_status("error", "<script>alert(1)</script>")

        assert "<script>" not in widget.widgets["status_pill"].value
        assert pill(widget) == "&lt;script&gt;alert(1)&lt;/script&gt;"


# --------------------------------------------------------------------------
# Form <-> configuration
# --------------------------------------------------------------------------

#: One fully populated configuration per backend. Every field the widget
#: manages is set to something that is not its default, so a control that is
#: read but never written (or the reverse) changes the round trip.
EVERYTHING_SET = dict(
    default_cores=12,
    default_memory="48GB",
    default_time="03:30:00",
    cluster_host="login.example.edu",
    cluster_port=2222,
    username="researcher",
    password="typed-this-session",
    key_file="~/.ssh/id_ed25519_hpc",
    password_env_var="HPC_PASSWORD",
    use_env_password=True,
    remote_work_dir="/scratch/researcher/clustrix",
    hf_namespace="contextlab",
    hf_flavor="t4-small",
    hf_token="typed-token",
    hf_allow_gpu_flavors=True,
    package_manager="uv",
    python_executable="python3.11",
    replicate_local_environment=False,
    environment_variables={"OMP_NUM_THREADS": "4", "URL": "a=b=c"},
    module_loads=["python/3.11", "cuda/12.1"],
    pre_execution_commands=["source /etc/profile", "ulimit -n 4096"],
)


def _managed(config):
    return {name: getattr(config, name) for name in sorted(WIDGET_MANAGED_FIELDS)}


class TestFormAndConfigurationAgree:
    def test_the_fixture_sets_every_managed_field_away_from_its_default(self):
        """Or the round trip below would pass on fields it never exercised."""
        assert set(EVERYTHING_SET) | {"cluster_type"} == set(WIDGET_MANAGED_FIELDS)
        defaults = ClusterConfig()
        unchanged = [k for k, v in EVERYTHING_SET.items() if getattr(defaults, k) == v]
        assert unchanged == []

    @pytest.mark.parametrize("backend", SUPPORTED_CLUSTER_TYPES)
    def test_a_configuration_round_trips_through_the_controls(self, backend):
        """Shown, then read back: nothing lost, whichever backend is chosen.

        Every backend keeps every field, on purpose: the form holds the
        selected *profile*, and glancing at another backend must not erase
        the host from the one being edited.
        """
        config = ClusterConfig(cluster_type=backend, **EVERYTHING_SET)
        widget = ModernClustrixWidget()

        widget._load_config_to_widgets(config)

        assert widget.widgets["cluster_type"].value == backend
        assert _managed(widget._get_config_from_widgets()) == _managed(config)
        assert set(widget._config_data_from_widgets()) == set(WIDGET_MANAGED_FIELDS)

    @pytest.mark.parametrize("backend", SUPPORTED_CLUSTER_TYPES)
    def test_what_a_job_would_use_drops_the_other_backends_settings(self, backend):
        """The profile keeps everything; the configuration a test or a job
        starts from carries only what the chosen backend uses."""
        widget = ModernClustrixWidget()
        widget._load_config_to_widgets(
            ClusterConfig(cluster_type=backend, **EVERYTHING_SET)
        )

        used = widget._config_for_backend()

        uses_ssh = backend in ("ssh", "slurm")
        assert (used.cluster_host == "login.example.edu") is uses_ssh
        assert (used.password == "typed-this-session") is uses_ssh
        assert (used.key_file == "~/.ssh/id_ed25519_hpc") is uses_ssh
        assert (used.hf_token == "typed-token") is (backend == "huggingface")
        assert (used.hf_namespace == "contextlab") is (backend == "huggingface")
        # Settings every backend uses are never dropped.
        assert used.default_cores == 12
        assert used.module_loads == ["python/3.11", "cuda/12.1"]

    def test_showing_a_default_configuration_blanks_what_the_last_one_set(self):
        """Loading must reset a control the incoming configuration leaves
        unset, or one profile's values migrate into the next."""
        widget = ModernClustrixWidget()
        widget._load_config_to_widgets(
            ClusterConfig(cluster_type="slurm", **EVERYTHING_SET)
        )

        widget._load_config_to_widgets(ClusterConfig())

        shown = _managed(widget._get_config_from_widgets())
        expected = _managed(ClusterConfig())
        # A dropdown has to show something: an unset flavor is displayed, and
        # read back, as the cheapest one rather than as nothing.
        assert expected.pop("hf_flavor") is None
        assert shown.pop("hf_flavor") == "cpu-basic"
        assert shown == expected
        for key in ("host", "username", "password", "hf_token", "env_vars", "modules"):
            assert widget.widgets[key].value == "", key


class TestEnvironmentVariables:
    def test_one_per_line_and_only_the_first_equals_splits(self):
        variables, malformed = _parse_environment_variables(
            "OMP_NUM_THREADS=4\n\n  PADDED = kept as typed \nURL=postgres://h/db?a=b\n"
            "EMPTY=\n_UNDERSCORE1=x"
        )

        assert malformed == []
        assert variables == {
            "OMP_NUM_THREADS": "4",
            "PADDED": " kept as typed",
            "URL": "postgres://h/db?a=b",
            "EMPTY": "",
            "_UNDERSCORE1": "x",
        }

    @pytest.mark.parametrize(
        "line",
        ["NO_EQUALS", "=value", "1STARTS_WITH_DIGIT=x", "HAS-DASH=x", "HAS SPACE=x"],
    )
    def test_a_line_that_is_not_key_equals_value_is_named(self, line):
        variables, malformed = _parse_environment_variables(f"GOOD=1\n{line}\nALSO=2")

        assert malformed == [line]
        assert variables == {"GOOD": "1", "ALSO": "2"}, "a typo cost the valid lines"

    def test_a_malformed_line_is_reported_and_blocks_apply(self, capsys):
        widget = ModernClustrixWidget()
        widget.widgets["env_vars"].value = "GOOD=1\nexport BAD=2"
        before = dict(get_config().environment_variables)

        told = _click(widget, "apply_btn", capsys)

        assert pill(widget) == "invalid"
        assert "Cannot apply this configuration" in told
        assert "'export BAD=2'" in told
        assert "KEY=value" in told
        assert (
            get_config().environment_variables == before
        ), "an invalid form was applied anyway"

    def test_a_malformed_line_blocks_the_test_buttons_too(self, capsys):
        widget = ModernClustrixWidget()
        widget.widgets["env_vars"].value = "not a variable"

        told = _click(widget, "test_submit_btn", capsys)

        assert pill(widget) == "invalid"
        assert "Cannot run a job with this configuration" in told
        assert "Test job succeeded" not in told

    def test_fixing_the_line_unblocks_apply(self, capsys):
        widget = ModernClustrixWidget()
        widget.widgets["env_vars"].value = "export BAD=2"
        _click(widget, "apply_btn", capsys)

        widget.widgets["env_vars"].value = "GOOD=2\nPATH_EXTRA=/opt/bin"
        told = _click(widget, "apply_btn", capsys)

        assert pill(widget) == "in use"
        assert "Applied." in told
        assert get_config().environment_variables == {
            "GOOD": "2",
            "PATH_EXTRA": "/opt/bin",
        }


class TestModulesAndCommands:
    @pytest.mark.parametrize(
        "typed",
        [
            "python/3.11 cuda/12.1",
            "  python/3.11    cuda/12.1  ",
            "python/3.11,cuda/12.1",
            "python/3.11, cuda/12.1",
        ],
    )
    def test_modules_are_split_on_spaces_or_commas(self, typed):
        widget = ModernClustrixWidget()

        widget.widgets["modules"].value = typed

        assert widget._get_config_from_widgets().module_loads == [
            "python/3.11",
            "cuda/12.1",
        ]

    def test_no_modules_is_an_empty_list(self):
        widget = ModernClustrixWidget()

        widget.widgets["modules"].value = "   "

        assert widget._get_config_from_widgets().module_loads == []

    def test_commands_are_one_per_line_with_blank_lines_dropped(self):
        widget = ModernClustrixWidget()

        widget.widgets["pre_exec_commands"].value = (
            "source /etc/profile\n\n   ulimit -n 4096   \n"
        )

        assert widget._get_config_from_widgets().pre_execution_commands == [
            "source /etc/profile",
            "ulimit -n 4096",
        ]

    def test_a_password_variable_name_also_switches_its_use_on(self):
        """Naming the variable did nothing unless clustrix was also told to
        look it up."""
        widget = ModernClustrixWidget()
        assert widget._get_config_from_widgets().use_env_password is False

        widget.widgets["local_env_var"].value = "HPC_PASSWORD"

        config = widget._get_config_from_widgets()
        assert config.password_env_var == "HPC_PASSWORD"
        assert config.use_env_password is True

    def test_a_blank_work_directory_means_the_default_not_the_empty_string(self):
        widget = ModernClustrixWidget()

        widget.widgets["home_dir"].value = "   "

        assert widget._get_config_from_widgets().remote_work_dir == (
            ClusterConfig().remote_work_dir
        )


# --------------------------------------------------------------------------
# Apply, and what @cluster then does
# --------------------------------------------------------------------------


@cluster(cores=1)
def _which_process():
    """Decorated at import, long before any widget exists or is applied."""
    return os.getpid()


class TestApply:
    def test_a_function_decorated_earlier_follows_what_is_applied(self, capsys):
        """The point of the widget: it changes what an existing ``@cluster``
        function does, without redecorating anything.

        Before Apply the live configuration names a host that cannot exist,
        and calling the function really tries to reach it. After applying a
        local profile the same function object runs in this process.
        """
        configure(
            cluster_type="ssh", cluster_host="no-such-host.invalid", username="nobody"
        )
        with pytest.raises(socket.gaierror):
            _which_process()

        widget = ModernClustrixWidget()
        assert "ssh on no-such-host.invalid" in widget.widgets["in_use"].value
        widget.widgets["profile_dropdown"].value = "Local single-core"
        told = _click(widget, "apply_btn", capsys)

        assert "Applied. @cluster now runs on: local" in told
        assert clustrix.get_config().cluster_type == "local"
        assert clustrix.get_config().cluster_host is None
        assert clustrix.get_config().username is None
        assert _which_process() == os.getpid()
        assert "this machine" in widget.widgets["in_use"].value

    def test_switching_the_backend_to_local_resets_the_host_in_the_live_config(
        self, capsys
    ):
        """Same profile, backend changed on the form: the host stays on
        screen for when the user switches back, and leaves the live
        configuration, because a leftover host would send a local job to it."""
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "slurm"
        controls["host"].value = "hpc.example.edu"
        controls["port"].value = 2222
        controls["username"].value = "researcher"
        controls["ssh_key_file"].value = "~/.ssh/id_hpc"
        controls["home_dir"].value = "/scratch/researcher"
        _click(widget, "apply_btn", capsys)
        live = get_config()
        assert (live.cluster_type, live.cluster_host, live.cluster_port) == (
            "slurm",
            "hpc.example.edu",
            2222,
        )

        controls["cluster_type"].value = "local"
        told = _click(widget, "apply_btn", capsys)

        defaults = ClusterConfig()
        live = get_config()
        assert live.cluster_type == "local"
        assert live.cluster_host is None
        assert live.username is None
        assert live.key_file is None
        assert live.cluster_port == defaults.cluster_port
        assert live.remote_work_dir == defaults.remote_work_dir
        assert "hpc.example.edu" not in told
        assert controls["host"].value == "hpc.example.edu"
        assert _which_process() == os.getpid()

    def test_the_summary_reads_back_the_live_configuration(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "huggingface"
        controls["hf_namespace"].value = "contextlab"
        controls["hf_flavor"].value = "cpu-upgrade"
        controls["cpus"].value = 2
        controls["ram"].value = "8GB"
        controls["time"].value = "00:20:00"

        told = _click(widget, "apply_btn", capsys)

        assert "Applied. @cluster now runs on: huggingface" in told
        assert "namespace  contextlab (cpu-upgrade)" in told
        assert "resources  2 CPUs, 8GB, 00:20:00" in told
        assert "huggingface (contextlab)" in widget.widgets["in_use"].value

    def test_apply_restores_its_button(self, capsys):
        """The button is relabelled and disabled while it works; a handler
        that returned early must not leave it that way."""
        widget = ModernClustrixWidget()
        button = widget.widgets["apply_btn"]

        widget.widgets["cpus"].value = 0
        _click(widget, "apply_btn", capsys)
        assert (button.description, button.disabled) == ("Apply", False)

        widget.widgets["cpus"].value = 2
        _click(widget, "apply_btn", capsys)
        assert (button.description, button.disabled) == ("Apply", False)

    def test_each_action_replaces_the_console_instead_of_appending(self, capsys):
        widget = ModernClustrixWidget()
        _click(widget, "apply_btn", capsys)

        told = _click(widget, "test_connect_btn", capsys)

        assert "Applied." not in told
        assert "Nothing to connect to" in told


class TestThePillSaysWhetherTheFormIsInUse:
    @pytest.fixture
    def applied(self, capsys):
        """A widget whose form has just been applied, so it starts in use."""
        widget = ModernClustrixWidget()
        _click(widget, "apply_btn", capsys)
        assert pill(widget) == "in use"
        return widget

    def test_on_a_fresh_install_the_selected_template_is_not_what_is_in_use(self):
        """Nothing has been applied, and the template on screen differs from
        clustrix's defaults, so saying "in use" would be untrue."""
        widget = ModernClustrixWidget()

        assert widget.widgets["profile_dropdown"].value == "Local single-core"
        assert get_config().default_cores != widget.widgets["cpus"].value
        assert pill(widget) == "not applied"

    def test_a_widget_opened_on_a_configured_session_is_in_use(self):
        """It opens showing what clustrix is already doing."""
        configure(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            default_cores=7,
        )

        widget = ModernClustrixWidget()

        assert widget.widgets["host"].value == "hpc.example.edu"
        assert widget.widgets["cpus"].value == 7
        assert pill(widget) == "in use"
        assert "slurm on hpc.example.edu" in widget.widgets["in_use"].value

    def test_an_edit_makes_it_not_applied_and_apply_makes_it_in_use(
        self, applied, capsys
    ):
        applied.widgets["cpus"].value = 3
        assert pill(applied) == "not applied"

        _click(applied, "apply_btn", capsys)
        assert pill(applied) == "in use"
        assert get_config().default_cores == 3

    def test_undoing_the_edit_by_hand_is_in_use_again(self, applied):
        """It compares the form with the live configuration; it does not
        merely remember that something was touched."""
        original = applied.widgets["ram"].value

        applied.widgets["ram"].value = "128GB"
        assert pill(applied) == "not applied"
        applied.widgets["ram"].value = original
        assert pill(applied) == "in use"

    @pytest.mark.parametrize(
        "control, value",
        [
            ("cluster_type", "huggingface"),
            ("time", "09:00:00"),
            ("package_manager", "conda"),
            ("python_executable", "python3.12"),
            ("clone_env", False),
            ("env_vars", "A=1"),
            ("modules", "gcc"),
            ("pre_exec_commands", "true"),
            ("local_env_var", "PW"),
        ],
    )
    def test_every_kind_of_control_is_watched(self, control, value, applied):
        applied.widgets[control].value = value

        assert pill(applied) == "not applied", f"editing {control} went unnoticed"

    def test_a_field_the_chosen_backend_ignores_does_not_count(self, applied):
        """Typing a HuggingFace namespace while on ``local`` changes nothing
        Apply would send, so the form is still what is in use."""
        assert applied.widgets["cluster_type"].value == "local"

        applied.widgets["hf_namespace"].value = "contextlab"
        applied.widgets["host"].value = "unused.example.edu"

        assert pill(applied) == "in use"

    def test_selecting_another_profile_is_not_applied_until_it_is(
        self, applied, capsys
    ):
        applied.widgets["profile_dropdown"].value = "Local all cores"
        assert pill(applied) == "not applied"

        _click(applied, "apply_btn", capsys)
        assert pill(applied) == "in use"
        assert get_config().default_cores == -1

    def test_a_change_made_from_code_shows_up_when_the_form_is_next_touched(
        self, applied
    ):
        """``configure()`` behind the widget's back makes the form stale."""
        configure(default_cores=31)
        applied.widgets["ram"].value = applied.widgets["ram"].value + " "

        assert pill(applied) == "not applied"


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


class TestProfileButtons:
    def test_duplicate_copies_what_is_on_screen_and_selects_the_copy(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["profile_dropdown"].value = "SSH remote machine"
        controls["host"].value = "typed-not-saved.example.edu"
        before = widget.profile_manager.get_profile_names()

        told = _click(widget, "add_profile_btn", capsys)

        names = widget.profile_manager.get_profile_names()
        (copy_name,) = set(names) - set(before)
        assert controls["profile_dropdown"].value == copy_name
        assert controls["profile_name"].value == copy_name
        assert copy_name in told and "Rename it in the Name field" in told
        copied = widget.profile_manager.load_profile(copy_name)
        original = widget.profile_manager.load_profile("SSH remote machine")
        assert copied.cluster_host == "typed-not-saved.example.edu"
        assert original.cluster_host == "typed-not-saved.example.edu"
        assert copied is not original

    def test_editing_the_copy_leaves_the_original_alone(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["profile_dropdown"].value = "SLURM cluster"
        original_host = controls["host"].value
        _click(widget, "add_profile_btn", capsys)

        controls["host"].value = "only-the-copy.example.edu"
        controls["profile_dropdown"].value = "SLURM cluster"

        assert controls["host"].value == original_host

    def test_duplicating_twice_gives_two_distinct_names(self, capsys):
        widget = ModernClustrixWidget()
        before = len(widget.profile_manager.get_profile_names())

        widget.widgets["profile_dropdown"].value = "Local single-core"
        _click(widget, "add_profile_btn", capsys)
        widget.widgets["profile_dropdown"].value = "Local single-core"
        _click(widget, "add_profile_btn", capsys)

        names = widget.profile_manager.get_profile_names()
        assert len(names) == len(set(names)) == before + 2

    def test_renaming_moves_the_profile_and_keeps_it_selected(self):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["profile_dropdown"].value = "SLURM cluster"
        position = list(controls["profile_dropdown"].options).index("SLURM cluster")

        controls["profile_name"].value = "Discovery"

        options = list(controls["profile_dropdown"].options)
        assert "SLURM cluster" not in options
        assert "Discovery" in options
        assert len(options) == len(set(options))
        assert controls["profile_dropdown"].value == "Discovery"
        assert widget.profile_manager.active_profile == "Discovery"
        assert widget.profile_manager.load_profile("Discovery").cluster_type == "slurm"
        assert position >= 0

    def test_the_name_box_follows_the_selected_profile(self):
        widget = ModernClustrixWidget()

        for name in widget.profile_manager.get_profile_names():
            widget.widgets["profile_dropdown"].value = name
            assert widget.widgets["profile_name"].value == name

        # ...and following it is not mistaken for a rename.
        assert set(widget.profile_manager.get_profile_names()) == set(
            widget.profile_manager.BUILTIN_PROFILES
        )

    def test_delete_removes_the_profile_and_shows_another(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["profile_dropdown"].value = "SLURM cluster"

        told = _click(widget, "remove_profile_btn", capsys)

        names = widget.profile_manager.get_profile_names()
        assert "SLURM cluster" not in names
        assert "Deleted profile 'SLURM cluster'" in told
        assert controls["profile_dropdown"].value in names
        assert controls["profile_name"].value == controls["profile_dropdown"].value
        shown = widget.profile_manager.load_profile(controls["profile_dropdown"].value)
        assert controls["cluster_type"].value == shown.cluster_type

    def test_the_last_profile_cannot_be_deleted(self, capsys):
        widget = ModernClustrixWidget()
        while len(widget.profile_manager.get_profile_names()) > 1:
            _click(widget, "remove_profile_btn", capsys)
        (last,) = widget.profile_manager.get_profile_names()

        told = _click(widget, "remove_profile_btn", capsys)

        assert widget.profile_manager.get_profile_names() == [last]
        assert pill(widget) == "not deleted"
        assert f"Could not delete '{last}'" in told
        assert widget.widgets["profile_dropdown"].value == last


class TestSavingAndLoadingAFile:
    def _edited(self):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["profile_dropdown"].value = "SLURM cluster"
        controls["host"].value = "hpc.example.edu"
        controls["username"].value = "researcher"
        controls["password"].value = "never-written"
        controls["modules"].value = "python/3.11 cuda"
        controls["pre_exec_commands"].value = "ulimit -n 4096"
        controls["time"].value = "06:00:00"
        controls["profile_dropdown"].value = "HuggingFace Jobs (CPU)"
        controls["hf_namespace"].value = "contextlab"
        controls["hf_token"].value = "never-written-either"
        return widget

    @pytest.mark.parametrize("extension", ["yml", "yaml", "json"])
    def test_a_saved_file_loads_back_with_everything_but_the_secrets(
        self, extension, tmp_path, capsys
    ):
        target = tmp_path / f"shared.{extension}"
        first = self._edited()
        first.widgets["config_filename"].value = str(target)
        told = _click(first, "save_btn", capsys)
        assert (
            f"Saved {len(first.profile_manager.profiles)} profile(s) to {target}"
            in (told)
        )

        second = ModernClustrixWidget()
        second.widgets["profile_dropdown"].value = "Local all cores"
        second.widgets["config_filename"].value = str(target)
        told = _click(second, "load_btn", capsys)

        assert pill(second) == "loaded"
        assert "Loaded" in told and "SLURM cluster" in told
        assert sorted(second.profile_manager.get_profile_names()) == sorted(
            first.profile_manager.get_profile_names()
        )
        # The selection travels with the file.
        assert second.widgets["profile_dropdown"].value == "HuggingFace Jobs (CPU)"
        assert second.widgets["hf_namespace"].value == "contextlab"
        assert second.widgets["hf_token"].value == ""
        second.widgets["profile_dropdown"].value = "SLURM cluster"
        controls = second.widgets
        assert controls["host"].value == "hpc.example.edu"
        assert controls["username"].value == "researcher"
        assert controls["modules"].value == "python/3.11 cuda"
        assert controls["pre_exec_commands"].value == "ulimit -n 4096"
        assert controls["time"].value == "06:00:00"
        assert controls["password"].value == ""
        text = target.read_text(encoding="utf-8")
        assert "never-written" not in text

    def test_a_bare_filename_lands_in_the_clustrix_directory_not_the_cwd(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        widget = self._edited()
        widget.widgets["config_filename"].value = "team.yml"

        told = _click(widget, "save_btn", capsys)

        expected = pathlib.Path(os.environ["CLUSTRIX_CONFIG_DIR"]) / "team.yml"
        assert expected.exists()
        assert not (tmp_path / "team.yml").exists()
        assert str(expected) in told, "the user is not told where the file went"
        assert "team.yml" in widget.widgets["config_filename"].options
        assert widget.widgets["config_filename"].value == "team.yml"

    def test_saving_somewhere_unwritable_says_so(self, tmp_path, capsys):
        blocker = tmp_path / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        widget = ModernClustrixWidget()
        widget.widgets["config_filename"].value = str(blocker / "profiles.yml")

        told = _click(widget, "save_btn", capsys)

        assert pill(widget) == "save failed"
        assert "Could not save" in told
        assert widget.widgets["save_btn"].description == "Save all"
        assert widget.widgets["save_btn"].disabled is False


# --------------------------------------------------------------------------
# The two test buttons, locally
# --------------------------------------------------------------------------


class TestTheTestButtonsOnThisMachine:
    def test_the_local_test_job_really_runs_and_returns_5050(self, capsys):
        widget = ModernClustrixWidget()
        assert widget.widgets["cluster_type"].value == "local"

        told = _click(widget, "test_submit_btn", capsys)

        assert pill(widget) == "job completed"
        assert "Test job succeeded on local." in told
        assert "returned  5050  (expected 5050)" in told
        assert f"ran on    {platform.node()}" in told
        assert f"python    {platform.python_version()}" in told
        button = widget.widgets["test_submit_btn"]
        assert (button.description, button.disabled) == ("Run test job", False)

    def test_local_has_nothing_to_connect_to_and_says_so(self, capsys):
        widget = ModernClustrixWidget()

        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "connected"
        assert "Nothing to connect to: local jobs run in this Python process." in told

    def test_a_huggingface_token_that_is_not_one_is_not_reported_connected(
        self, monkeypatch, capsys
    ):
        """A real call to the Hub with a token it will not accept. Offline
        the call fails differently and the answer is the same: not connected."""
        monkeypatch.delenv("HF_TOKEN", raising=False)
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "huggingface"
        widget.widgets["hf_namespace"].value = "contextlab"
        widget.widgets["hf_token"].value = "hf_this-is-not-a-token"

        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "not connected"
        assert "Connection failed:" in told
        assert "Authenticated to HuggingFace" not in told


# --------------------------------------------------------------------------
# The same buttons against a real SSH server
# --------------------------------------------------------------------------


@pytest.fixture
def ssh_host(tmp_path):
    """A real SSH server on loopback that this user has connected to before.

    "Before" is a ``known_hosts`` entry in the isolated ``$HOME``: the form
    has no control for the host-key policy, so without one every connection
    stops at the host-key question -- which has its own module -- and none of
    the assertions below would be about passwords or jobs.
    """
    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=PASSWORD) as server:
        _known(server)
        yield server


def _known(server):
    """Record ``server``'s host key the way a first ``ssh`` to it would."""
    known_hosts = pathlib.Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(known_hosts, "a", encoding="utf-8") as handle:
        for key in server.host_keys():
            handle.write(
                f"[{server.host}]:{server.port} {key.get_name()} {key.get_base64()}\n"
            )
    known_hosts.chmod(0o600)


def _pointed_at(server, password=PASSWORD, backend="ssh"):
    widget = ModernClustrixWidget()
    controls = widget.widgets
    controls["cluster_type"].value = backend
    controls["host"].value = server.host
    controls["port"].value = server.port
    controls["username"].value = "tester"
    controls["password"].value = password
    controls["home_dir"].value = str(pathlib.Path(server.root) / "clustrix-work")
    # Not replicating this interpreter's packages keeps the remote job to
    # what it needs; the path through the executor is the same.
    controls["clone_env"].value = False
    return widget


def _a_port_nothing_listens_on():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TestTestConnectionOverSSH:
    def test_it_connects_authenticates_and_reports_the_remote_hostname(
        self, ssh_host, capsys
    ):
        widget = _pointed_at(ssh_host)

        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "connected"
        assert (
            f"Connected to {ssh_host.host} as tester "
            f"(remote hostname: {platform.node()})." in told
        )
        assert ssh_host.authentications == [("tester", "password")]
        assert "hostname" in ssh_host.commands
        button = widget.widgets["test_connect_btn"]
        assert (button.description, button.disabled) == ("Test connection", False)

    def test_it_does_not_apply_anything(self, ssh_host, capsys):
        """Testing a connection is a question, not a decision."""
        before = asdict(get_config())
        widget = _pointed_at(ssh_host)

        _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "connected"
        assert asdict(get_config()) == before
        assert get_config().cluster_host is None

    def test_a_wrong_password_is_not_connected(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host, password="the-wrong-password")

        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "not connected"
        assert "Connection failed: AuthenticationException" in told
        assert "Connected to" not in told
        assert ssh_host.authentications == []
        assert ssh_host.commands == []
        assert "the-wrong-password" not in told

    def test_a_port_nothing_listens_on_is_not_connected(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host)
        closed = _a_port_nothing_listens_on()
        widget.widgets["port"].value = closed

        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "not connected"
        assert f"Unable to connect to port {closed}" in told
        assert ssh_host.authentications == []

    def test_slurm_on_a_host_with_no_scheduler_is_not_connected(self, tmp_path, capsys):
        """SSH works and ``sbatch`` does not exist there. Reporting that as
        connected would send the first real job to a host that cannot run it.

        The host is a second real server whose ``PATH`` holds ``hostname``
        and nothing else, so this does not depend on whether the machine
        running the tests happens to have SLURM installed.
        """
        tools = tmp_path / "bin"
        tools.mkdir()
        (tools / "hostname").symlink_to(shutil.which("hostname"))
        root = tmp_path / "no-scheduler"
        root.mkdir()
        with LocalSSHServer(
            root=str(root), password=PASSWORD, env={"PATH": str(tools)}
        ) as bare_host:
            _known(bare_host)
            widget = _pointed_at(bare_host, backend="slurm")

            told = _click(widget, "test_connect_btn", capsys)

            assert bare_host.authentications == [("tester", "password")]
            assert "sbatch --version" in bare_host.commands
        assert pill(widget) == "not connected"
        assert "Connection failed" in told
        assert "Scheduler found" not in told

    @pytest.mark.skipif(
        shutil.which("sbatch") is None,
        reason="needs a real SLURM client on this machine to answer sbatch --version",
    )
    def test_slurm_on_a_host_with_a_scheduler_names_its_version(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host, backend="slurm")

        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "connected", told
        assert "sbatch --version" in ssh_host.commands
        assert "Scheduler found: slurm" in told

    def test_a_failure_then_a_success_shows_only_the_success(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host, password="the-wrong-password")
        _click(widget, "test_connect_btn", capsys)

        widget.widgets["password"].value = PASSWORD
        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "connected"
        assert "Connection failed" not in told


class TestRunTestJobOverSSH:
    @pytest.mark.slow
    def test_the_job_runs_on_the_host_and_its_result_comes_back(self, ssh_host, capsys):
        """End to end through ``ClusterExecutor``: upload, environment setup,
        remote execution, signed result download. Slow because the remote
        side builds real virtual environments."""
        widget = _pointed_at(ssh_host)

        told = _click(widget, "test_submit_btn", capsys)

        assert pill(widget) == "job completed", told
        assert "Submitted job ssh_" in told
        assert "Test job succeeded on ssh." in told
        assert "returned  5050  (expected 5050)" in told
        assert f"ran on    {platform.node()}" in told
        assert ssh_host.authentications[0] == ("tester", "password")
        # It ran there: the work directory the form named exists on the host.
        assert (pathlib.Path(ssh_host.root) / "clustrix-work").is_dir()
        # And running a test job applied nothing.
        assert get_config().cluster_host is None

    def test_a_wrong_password_fails_the_job_and_says_why(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host, password="the-wrong-password")

        told = _click(widget, "test_submit_btn", capsys)

        assert pill(widget) == "job failed"
        assert "Test job failed: AuthenticationException" in told
        assert "Test job succeeded" not in told
        assert ssh_host.authentications == []
        assert not (pathlib.Path(ssh_host.root) / "clustrix-work").exists()
        button = widget.widgets["test_submit_btn"]
        assert (button.description, button.disabled) == ("Run test job", False)

    def test_a_port_nothing_listens_on_fails_the_job(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host)
        closed = _a_port_nothing_listens_on()
        widget.widgets["port"].value = closed

        told = _click(widget, "test_submit_btn", capsys)

        assert pill(widget) == "job failed"
        assert f"Unable to connect to port {closed}" in told


class TestSetUpSSHKey:
    def test_it_installs_a_key_that_then_authenticates_without_the_password(
        self, ssh_host, capsys
    ):
        widget = _pointed_at(ssh_host)
        controls = widget.widgets
        assert controls["ssh_key_file"].value == ""

        told = _click(widget, "auto_setup_ssh", capsys)

        assert pill(widget) == "key ready", told
        key_path = pathlib.Path(controls["ssh_key_file"].value)
        assert f"Installed a new key on the host: {key_path}" in told
        assert "press Apply to use it" in told
        # A real private key, inside the (isolated) home, private.
        assert key_path.is_file()
        assert pathlib.Path.home() / ".ssh" in key_path.parents
        assert key_path.stat().st_mode & 0o077 == 0
        public = pathlib.Path(str(key_path) + ".pub").read_text().split()[1]
        # Really installed on the host.
        authorized = pathlib.Path(ssh_host.root) / ".ssh" / "authorized_keys"
        assert public in authorized.read_text()
        # The password it replaces is gone from the form.
        assert controls["password"].value == ""

        ssh_host.authentications.clear()
        told = _click(widget, "test_connect_btn", capsys)

        assert pill(widget) == "connected", told
        assert ssh_host.authentications == [("tester", "publickey")]

    def test_it_asks_for_the_password_instead_of_connecting_without_one(
        self, ssh_host, capsys
    ):
        widget = _pointed_at(ssh_host, password="")

        told = _click(widget, "auto_setup_ssh", capsys)

        assert pill(widget) == "password needed"
        assert "Enter the account password above first" in told
        assert widget.widgets["ssh_key_file"].value == ""
        assert ssh_host.authentications == []

    def test_a_wrong_password_sets_up_nothing(self, ssh_host, capsys):
        widget = _pointed_at(ssh_host, password="the-wrong-password")

        told = _click(widget, "auto_setup_ssh", capsys)

        assert pill(widget) == "key not set up"
        assert "SSH key setup failed" in told
        assert widget.widgets["ssh_key_file"].value == ""
        assert not (pathlib.Path(ssh_host.root) / ".ssh" / "authorized_keys").exists()
        # The password is kept so the user can correct it.
        assert widget.widgets["password"].value == "the-wrong-password"
        button = widget.widgets["auto_setup_ssh"]
        assert (button.description, button.disabled) == ("Set up SSH key", False)

    def test_an_incomplete_form_is_refused_before_anything_is_generated(self, capsys):
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "ssh"
        widget.widgets["password"].value = PASSWORD

        told = _click(widget, "auto_setup_ssh", capsys)

        assert pill(widget) == "invalid"
        assert "Cannot set up a key for this configuration" in told
        assert "A host is required" in told
        assert list((pathlib.Path.home() / ".ssh").glob("id_*")) == []

    def test_a_key_that_already_works_is_reused_unless_replacement_is_ticked(
        self, ssh_host, capsys
    ):
        widget = _pointed_at(ssh_host)
        controls = widget.widgets
        _click(widget, "auto_setup_ssh", capsys)
        first_key = controls["ssh_key_file"].value
        first_public = pathlib.Path(first_key + ".pub").read_text()

        controls["password"].value = PASSWORD
        told = _click(widget, "auto_setup_ssh", capsys)

        assert pill(widget) == "key ready"
        assert f"A key that already works was found: {first_key}" in told
        assert pathlib.Path(first_key + ".pub").read_text() == first_public

        controls["password"].value = PASSWORD
        controls["refresh_keys"].value = True
        told = _click(widget, "auto_setup_ssh", capsys)

        assert pill(widget) == "key ready", told
        assert "Installed a new key on the host" in told
        replaced = pathlib.Path(controls["ssh_key_file"].value + ".pub").read_text()
        assert replaced != first_public
        ssh_host.authentications.clear()
        _click(widget, "test_connect_btn", capsys)
        assert ssh_host.authentications == [("tester", "publickey")]
