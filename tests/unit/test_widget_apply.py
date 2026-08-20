"""Pressing Apply must actually change the configuration (issue #165).

Both widgets built a dict and splatted it into ``configure()``. A saved
profile carries its own ``name`` -- the label in the dropdown, not a setting
-- and ``configure()`` rejects any keyword that is not a ``ClusterConfig``
field, on purpose: a silently ignored setting is worse than a rejected one.
So the widget's main button raised ``ValueError: Unknown configuration
parameter: name`` before applying anything, caught it, and printed a red
cross. The primary interface for anyone not writing scripts did nothing.

It survived because no test ever pressed the button. The widget tests build
widgets and read their attributes back, which cannot see this: the defect is
entirely in what happens between the controls and the live configuration.
Every test here drives the real handler on a real widget and then asserts on
``get_config()``.
"""

import pytest

pytest.importorskip("ipywidgets")

from dataclasses import fields as dataclass_fields  # noqa: E402

from clustrix.config import (  # noqa: E402
    ClusterConfig,
    SECRET_FIELDS,
    SUPPORTED_CLUSTER_TYPES,
    config_field_names,
    configure,
    get_config,
    split_config_kwargs,
)
from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402
from clustrix.notebook_magic_widget import (  # noqa: E402
    EnhancedClusterConfigWidget,
    PROFILE_BOOKKEEPING_KEYS,
    WIDGET_MANAGED_FIELDS,
)
from clustrix.profile_manager import ProfileManager  # noqa: E402
from clustrix.widget_controls import set_choice  # noqa: E402

#: What the widget's own Save button writes: settings plus the profile's
#: label. Nothing here is contrived -- ``name`` is what _on_add_config puts in.
SAVED_PROFILE = {
    "name": "Research SLURM",
    "cluster_type": "slurm",
    "cluster_host": "hpc.example.edu",
    "cluster_port": 22,
    "username": "researcher",
    "default_cores": 12,
    "default_memory": "64GB",
    "default_time": "04:00:00",
    "remote_work_dir": "/scratch/researcher/clustrix",
    "package_manager": "conda",
    "default_partition": "gpu",
    "key_file": "~/.ssh/id_ed25519",
}


@pytest.fixture(autouse=True)
def isolated_profile_store(tmp_path, monkeypatch):
    """The widgets read and write a profile store on disk. Point it at a
    throwaway: a previous run of these tests polluted a real ~/.clustrix.

    ``HOME`` and ``CLUSTRIX_CONFIG_DIR`` are set as well as the constructor
    patched, because the two paths are reached by different code: the widget
    builds a ProfileManager, while ``get_config_dir`` and every ``~``
    expansion read the environment. Leaving either unset means the developer's
    own home directory is one forgotten argument away.
    """
    home = tmp_path / "home"
    # Created, not just named: HOME pointing at a non-existent directory is a
    # different environment from the one a user has, and hides any code that
    # reads ~ rather than writing to it.
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(tmp_path / "clustrix-config"))
    original = ProfileManager.__init__

    def patched(self, config_dir=None):
        original(self, config_dir=config_dir or str(tmp_path / "profiles"))

    monkeypatch.setattr(ProfileManager, "__init__", patched)


def _press(action, output_widget, capsys):
    """Run a button handler and return everything it told the user.

    The widgets print inside an ``ipywidgets.Output`` context. With a live
    kernel that lands in ``output.outputs``; without one it falls through to
    stdout. Both are read so the assertions do not depend on which.
    """
    already = len(output_widget.outputs)
    action()
    streamed = capsys.readouterr().out
    captured = "".join(
        entry.get("text", "")
        for entry in output_widget.outputs[already:]
        if isinstance(entry, dict)
    )
    return (streamed + captured).replace("\x1b[2K\r", "")


class TestApplyReachesTheLiveConfiguration:
    """The definition of done: press the button, read get_config()."""

    def test_legacy_widget_apply_makes_the_saved_profile_live(self, capsys):
        widget = EnhancedClusterConfigWidget()
        widget.configs["Research SLURM"] = dict(SAVED_PROFILE)
        widget._load_config_to_widgets("Research SLURM")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.cluster_type == "slurm"
        assert live.cluster_host == "hpc.example.edu"
        assert live.username == "researcher"
        assert live.default_cores == 12
        assert live.default_memory == "64GB"
        assert live.default_time == "04:00:00"
        assert live.remote_work_dir == "/scratch/researcher/clustrix"
        assert live.package_manager == "conda"
        assert live.default_partition == "gpu"
        assert live.key_file == "~/.ssh/id_ed25519"

    def test_legacy_widget_apply_says_nothing_about_the_profile_label(self, capsys):
        """``name`` is bookkeeping, so it is dropped without a warning --
        but only ``name``, and only because there is no setting it could
        possibly mean."""
        widget = EnhancedClusterConfigWidget()
        widget.configs["Research SLURM"] = dict(SAVED_PROFILE)
        widget._load_config_to_widgets("Research SLURM")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "Ignored" not in told, told

    def test_modern_widget_apply_makes_the_displayed_settings_live(self, capsys):
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "slurm"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "hpc.example.edu"
        widget.widgets["username"].value = "researcher"
        widget.widgets["cpus"].value = 12
        widget.widgets["ram"].value = "64GB"
        widget.widgets["time"].value = "04:00:00"
        widget.widgets["home_dir"].value = "/scratch/researcher/clustrix"

        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.cluster_type == "slurm"
        assert live.cluster_host == "hpc.example.edu"
        assert live.username == "researcher"
        assert live.default_cores == 12
        assert live.default_memory == "64GB"
        assert live.default_time == "04:00:00"
        assert live.remote_work_dir == "/scratch/researcher/clustrix"


class TestAKeyThatIsNeitherSettingNorBookkeepingIsNamed:
    """Dropping the unknown key quietly would be the same bug wearing the
    other hat: the user asked for something and did not get it."""

    def test_legacy_widget_names_a_control_wired_to_a_dead_key(self, capsys):
        class WidgetWithADeadControl(EnhancedClusterConfigWidget):
            """``queue`` is exactly what this widget collected before #165 --
            not a ClusterConfig field, and read by nothing since #158."""

            def _save_config_from_widgets(self):
                data = super()._save_config_from_widgets()
                data["queue"] = "batch"
                return data

        widget = WidgetWithADeadControl()
        widget.configs["Research SLURM"] = dict(SAVED_PROFILE)
        widget._load_config_to_widgets("Research SLURM")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "queue" in told
        assert "Ignored, not a clustrix setting" in told
        # And the rest of the profile still landed.
        assert get_config().cluster_host == "hpc.example.edu"

    def test_modern_widget_names_a_managed_field_that_has_drifted(
        self, capsys, monkeypatch
    ):
        import clustrix.modern_notebook_widget as modern

        monkeypatch.setattr(
            modern,
            "WIDGET_MANAGED_FIELDS",
            frozenset(modern.WIDGET_MANAGED_FIELDS | {"queue"}),
        )

        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "slurm"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "hpc.example.edu"
        widget.widgets["username"].value = "researcher"

        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        assert "queue" in told
        assert "Ignored, not a clustrix setting" in told
        assert get_config().cluster_host == "hpc.example.edu"


class TestTheForwardedKeySetIsDerived:
    def test_it_is_exactly_the_dataclass_fields(self):
        """Not a list in a module somewhere: a list is correct until the next
        field is added and nothing fails loudly when it stops being."""
        assert config_field_names() == {
            field.name for field in dataclass_fields(ClusterConfig)
        }

    def test_a_field_no_widget_control_knows_about_is_still_forwarded(self):
        """A hand-written "keys the widget sets" list would drop this one."""
        settings, unrecognised = split_config_kwargs(
            {"stage_warn_bytes": 4096, "name": "whatever"}, ("name",)
        )
        assert settings == {"stage_warn_bytes": 4096}
        assert unrecognised == []

    def test_a_reset_field_is_seeded_with_its_real_default(self):
        """``reset_fields`` means "put this back to the ClusterConfig
        default", not "blank it". The container-valued fields are where the
        difference bites: ``None`` is not an empty list."""
        settings, unrecognised = split_config_kwargs(
            {},
            (),
            reset_fields=(
                "module_loads",
                "environment_variables",
                "pre_execution_commands",
                "cluster_port",
                "package_manager",
            ),
        )
        assert unrecognised == []
        defaults = ClusterConfig()
        assert settings == {
            "module_loads": defaults.module_loads,
            "environment_variables": defaults.environment_variables,
            "pre_execution_commands": defaults.pre_execution_commands,
            "cluster_port": defaults.cluster_port,
            "package_manager": defaults.package_manager,
        }
        # Spelled out, because "equals the default" would still hold if every
        # default were None.
        assert settings["module_loads"] == []
        assert settings["environment_variables"] == {}
        assert settings["pre_execution_commands"] == []
        assert settings["cluster_port"] == 22

    def test_bookkeeping_is_dropped_and_everything_else_is_reported(self):
        settings, unrecognised = split_config_kwargs(
            {"cluster_type": "local", "name": "mine", "queue": "batch"},
            ("name",),
        )
        assert settings == {"cluster_type": "local"}
        assert unrecognised == ["queue"]

    def test_configure_is_still_strict(self):
        """The fix belongs in the caller. If this ever passes silently, the
        whole point has been given away."""
        with pytest.raises(ValueError, match="Unknown configuration parameter"):
            configure(name="Research SLURM")


class TestClearingAControlClearsTheSetting:
    """A field the user emptied has to reach ``configure()`` as "unset".

    ``_save_config_from_widgets`` drops empty values, so a blank box cannot
    overwrite a real setting with ``""`` -- which is right, and which also
    meant a box the user *cleared* said nothing at all and the previously
    applied profile's value stayed live. Switching from a cluster profile to
    a local one applied ``cluster_type="local"`` while leaving the cluster's
    host and username in the configuration ``@cluster`` reads.
    """

    def test_switching_to_a_local_profile_drops_the_previous_host(self, capsys):
        widget = EnhancedClusterConfigWidget()
        widget.configs["Slurm HPC"] = {
            "name": "Slurm HPC",
            "cluster_type": "slurm",
            "cluster_host": "hpc.example.edu",
            "username": "researcher",
            "default_cores": 12,
        }
        widget.configs["Just Local"] = {
            "name": "Just Local",
            "cluster_type": "local",
            "default_cores": 4,
        }

        widget._load_config_to_widgets("Slurm HPC")
        _press(lambda: widget._on_apply_config(None), widget.status_output, capsys)
        assert get_config().cluster_host == "hpc.example.edu"

        widget._load_config_to_widgets("Just Local")
        # The control is right; only the applying was wrong.
        assert widget.host_field.value == ""
        assert widget.username_field.value == ""
        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.cluster_type == "local"
        assert live.cluster_host is None
        assert live.username is None
        assert live.default_cores == 4

    def test_emptying_a_box_in_the_profile_being_edited_clears_it_too(self, capsys):
        """Not only across a profile switch. The stored profile still holds
        the host the user just deleted from the box, and the box is what the
        user is looking at."""
        widget = EnhancedClusterConfigWidget()
        widget.configs["Slurm HPC"] = {
            "name": "Slurm HPC",
            "cluster_type": "slurm",
            "cluster_host": "hpc.example.edu",
            "username": "researcher",
        }
        widget._load_config_to_widgets("Slurm HPC")
        _press(lambda: widget._on_apply_config(None), widget.status_output, capsys)
        assert get_config().cluster_host == "hpc.example.edu"

        widget.host_field.value = ""
        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        assert get_config().cluster_host is None
        assert get_config().username == "researcher"

    def test_a_setting_with_no_control_here_survives_apply(self, capsys):
        """The reset is confined to the fields this widget owns. Resetting
        the whole configuration instead would throw away everything a user
        can only set from code."""
        configure(stage_warn_bytes=4096)
        widget = EnhancedClusterConfigWidget()
        widget.configs["Just Local"] = {"name": "Just Local", "cluster_type": "local"}
        widget._load_config_to_widgets("Just Local")

        _press(lambda: widget._on_apply_config(None), widget.status_output, capsys)

        assert get_config().stage_warn_bytes == 4096

    def test_a_cleared_list_box_clears_to_an_empty_list_not_to_none(self, capsys):
        """The seed has to be the field's real default, not ``None``.

        ``module_loads``, ``environment_variables`` and
        ``pre_execution_commands`` are the three controls whose empty state is
        dropped rather than sent, so they are the ones the reset actually has
        to supply a value for -- and every consumer iterates them. Seeding
        ``None`` looks like a clear and passes every assertion phrased as "not
        the old value", while leaving a configuration that raises
        ``TypeError: 'NoneType' object is not iterable`` the first time a job
        script is built.
        """
        widget = EnhancedClusterConfigWidget()
        widget.configs["Modules"] = {
            "name": "Modules",
            "cluster_type": "slurm",
            "cluster_host": "hpc.example.edu",
            "username": "researcher",
            "module_loads": ["python/3.11", "cuda/12.1"],
            "environment_variables": {"OMP_NUM_THREADS": "4"},
            "pre_execution_commands": ["source activate env"],
        }
        widget._load_config_to_widgets("Modules")
        _press(lambda: widget._on_apply_config(None), widget.status_output, capsys)
        assert get_config().module_loads == ["python/3.11", "cuda/12.1"]

        widget.module_loads_field.value = ""
        widget.env_vars_field.value = ""
        widget.pre_exec_commands_field.value = ""
        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.module_loads == []
        assert live.environment_variables == {}
        assert live.pre_execution_commands == []
        # Said the way the code that breaks says it.
        assert [f"module load {name}" for name in live.module_loads] == []
        assert sorted(live.environment_variables.items()) == []
        assert list(live.pre_execution_commands) == []

    def test_managed_fields_are_exactly_what_the_widget_writes(self):
        """The seeded set is the widget's own key list, so a control added
        without updating it -- or a key left in it after its control went --
        fails here rather than quietly going back to unclearable."""
        widget = EnhancedClusterConfigWidget()
        # Every optional key too: password, the HuggingFace pair, and the
        # three list-valued boxes only appear when they hold something.
        widget.cluster_type.value = "huggingface"
        widget.password_field.value = "hunter2"
        widget.hf_token_field.value = "hf_xxx"
        widget.env_vars_field.value = '{"OMP_NUM_THREADS": "4"}'
        widget.module_loads_field.value = "python/3.11"
        widget.pre_exec_commands_field.value = "source activate env"
        widget.host_field.value = "jobs.example.edu"
        widget.username_field.value = "researcher"
        widget.partition_field.value = "gpu"
        widget.ssh_key_field.value = "~/.ssh/id_ed25519"

        written = set(widget._save_config_from_widgets())

        assert written == set(WIDGET_MANAGED_FIELDS) | set(PROFILE_BOOKKEEPING_KEYS)


class TestAStaleProfileKeyIsNamedOnTheRealPath:
    """The warning has to fire for a profile someone actually has on disk,
    not only for a control fabricated by a test. A profile written by an
    older clustrix, or hand-edited, is the case it exists for."""

    def test_a_key_the_widget_will_not_carry_is_named(self, capsys):
        widget = EnhancedClusterConfigWidget()
        widget.configs["Stale"] = {
            "name": "Stale",
            "cluster_type": "local",
            # Written by an older clustrix, and hand-edited since.
            "description": "the group's shared cluster",
            "cluster_hostt": "typo.example.edu",
        }
        widget._load_config_to_widgets("Stale")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "Ignored, not a clustrix setting" in told
        assert "description" in told
        assert "cluster_hostt" in told
        # ``name`` is the one key dropped without a word.
        assert "name" not in told.split("Ignored, not a clustrix setting")[1]

    def test_the_stale_keys_are_still_there_to_be_named_next_time(self, capsys):
        """Rebuilding the profile from the controls erased them, so the
        warning -- had it ever fired -- would have fired once and then gone
        quiet with the user's setting already gone."""
        widget = EnhancedClusterConfigWidget()
        widget.configs["Stale"] = {
            "name": "Stale",
            "cluster_type": "local",
            "cluster_hostt": "typo.example.edu",
        }
        widget._load_config_to_widgets("Stale")

        _press(lambda: widget._on_apply_config(None), widget.status_output, capsys)
        told_again = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert widget.configs["Stale"]["cluster_hostt"] == "typo.example.edu"
        assert "cluster_hostt" in told_again

    def test_a_field_with_no_control_is_carried_rather_than_erased(self, capsys):
        """``stage_warn_bytes`` is a real setting with no control here. It is
        not "unrecognised" -- it is carried into the live configuration and
        kept in the profile."""
        widget = EnhancedClusterConfigWidget()
        widget.configs["Big Data"] = {
            "name": "Big Data",
            "cluster_type": "local",
            "stage_warn_bytes": 4096,
        }
        widget._load_config_to_widgets("Big Data")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "Ignored" not in told, told
        assert get_config().stage_warn_bytes == 4096
        assert widget.configs["Big Data"]["stage_warn_bytes"] == 4096

    def test_the_carry_over_survives_renaming_the_profile_in_the_box(self, capsys):
        """The stored profile is found by the name the widget is *tracking*,
        not by whatever the name box happens to hold.

        ``_on_config_name_change`` strips the typed value before it becomes
        the key, so the moment a user types a name with a space at either end
        the box and the dictionary key stop matching. Keying the lookup off
        the box then misses silently: the stale key stops being named, the
        field with no control stops being carried, and the entire carry-over
        is gone with no error anywhere.
        """
        widget = EnhancedClusterConfigWidget()
        widget.configs["Big Data"] = {
            "name": "Big Data",
            "cluster_type": "local",
            "stage_warn_bytes": 4096,
            "cluster_hostt": "typo.example.edu",
        }
        widget._load_config_to_widgets("Big Data")

        # Renaming it, the way the box is actually typed into.
        widget.config_name.value = "Big Data (staging) "
        assert widget.current_config_name == "Big Data (staging)"
        assert widget.config_name.value != widget.current_config_name

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        assert "Ignored, not a clustrix setting" in told
        assert "cluster_hostt" in told
        assert get_config().stage_warn_bytes == 4096
        assert widget.configs["Big Data (staging)"]["stage_warn_bytes"] == 4096

    def test_the_carry_over_survives_clearing_the_name_box(self, capsys):
        """The other way the box and the tracked name come apart, and the
        cheaper one to reach: empty the name field.

        ``_on_config_name_change`` returns early on a blank name -- deliberate,
        since a half-typed rename must not destroy the key -- so the box holds
        ``''`` while the widget is still tracking ``Big Data``. A lookup keyed
        off ``self.config_name.value.strip()`` then finds nothing at all, and
        the whole unmanaged carry-over disappears without a word: no stale key
        named, no ``stage_warn_bytes``. Nothing raises, which is why only an
        assertion on the carried values catches it.
        """
        widget = EnhancedClusterConfigWidget()
        widget.configs["Big Data"] = {
            "name": "Big Data",
            "cluster_type": "local",
            "stage_warn_bytes": 4096,
            "cluster_hostt": "typo.example.edu",
        }
        widget._load_config_to_widgets("Big Data")

        widget.config_name.value = ""
        assert widget.current_config_name == "Big Data"
        assert widget.config_name.value != widget.current_config_name

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        assert "cluster_hostt" in told
        assert get_config().stage_warn_bytes == 4096
        assert widget.configs["Big Data"]["stage_warn_bytes"] == 4096


class TestProfilesWrittenBeforeTheKeysWereRenamed:
    """``queue`` and ``ssh_key_path`` are what this widget wrote until #165.
    Neither has ever been a ClusterConfig field, so both are read when
    loading and re-emitted under the live name. Profiles already on disk are
    migrated rather than blanked, and the migrated setting reaches
    ``@cluster`` -- which is the whole reason for repointing the controls
    instead of deleting them."""

    OLD_PROFILE = {
        "name": "Written by 0.1.1",
        "cluster_type": "slurm",
        "cluster_host": "hpc.example.edu",
        "username": "researcher",
        "queue": "gpu-long",
        "ssh_key_path": "~/.ssh/id_rsa_hpc",
    }

    def test_the_old_keys_load_into_the_live_controls(self):
        widget = EnhancedClusterConfigWidget()
        widget.configs["Old"] = dict(self.OLD_PROFILE)

        widget._load_config_to_widgets("Old")

        assert widget.partition_field.value == "gpu-long"
        assert widget.ssh_key_field.value == "~/.ssh/id_rsa_hpc"

    def test_the_old_keys_reach_the_live_configuration(self, capsys):
        widget = EnhancedClusterConfigWidget()
        widget.configs["Old"] = dict(self.OLD_PROFILE)
        widget._load_config_to_widgets("Old")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.default_partition == "gpu-long"
        assert live.key_file == "~/.ssh/id_rsa_hpc"

    def test_a_migrated_key_is_not_also_reported_as_unrecognised(self, capsys):
        """It is carried, under its live name. Naming it would be a warning
        about a setting the user did get."""
        widget = EnhancedClusterConfigWidget()
        widget.configs["Old"] = dict(self.OLD_PROFILE)
        widget._load_config_to_widgets("Old")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "Ignored" not in told, told
        # And the profile stops carrying the dead spelling once applied.
        # Loading renames the entry to the profile's own label, so the key to
        # look under is the one the widget now considers current.
        migrated = widget.configs[widget.current_config_name]
        assert "queue" not in migrated
        assert "ssh_key_path" not in migrated
        assert migrated["default_partition"] == "gpu-long"
        assert migrated["key_file"] == "~/.ssh/id_rsa_hpc"


class TestTheSummarySaysWhatWasApplied:
    def test_modern_widget_does_not_print_a_host_it_did_not_apply(self, capsys):
        """_config_data_for_backend resets the fields the chosen backend
        ignores, so a profile switched to ``local`` applies no host. The
        summary printed the on-screen ClusterConfig instead, and announced
        the cluster host that had just been discarded."""
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "slurm"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "hpc.example.edu"
        widget.widgets["username"].value = "researcher"

        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        assert get_config().cluster_host is None
        assert "hpc.example.edu" not in told
        assert "Host:" not in told


class TestACredentialChannelIsNotABackendSetting:
    """Where the backend-only line is drawn, and why it is drawn there.

    ``BACKEND_ONLY_FIELDS`` exists so a value belonging to one backend cannot
    act on another: ``_choose_execution_mode`` routes on ``cluster_host``, so
    a leftover host would send a job the user configured as ``local`` to a
    cluster. The line that follows from that is about *what the value names*.
    Every backend-only field names **this cluster** -- the compute, who the
    job runs as there, the secret that opens that particular door, and what it
    may spend there. ``password_env_var`` and ``use_env_password`` name a
    *channel*: which environment variable a password is read from. Switching
    backend says nothing about that variable, and a modern-widget Apply on a
    ``local`` profile used to wipe it anyway -- silently, and with nothing on
    screen to suggest it had.

    Put as a rule the next field can be tested against: **does the value stop
    being correct when the target changes?** Deliberately *not* "is it local
    to this machine", because that separates nothing -- ``key_file`` is a path
    on the machine clustrix runs on exactly as ``password_env_var`` is a
    variable name on it, and both are machine-local pointers to a credential.
    What separates them is a convention, and it is the convention rather than
    the value's location that keeps ``key_file`` on the backend-only side: one
    key per host. An SSH key authenticates you to one particular host --
    ``~/.ssh/config`` binds ``IdentityFile`` inside a ``Host`` stanza for that
    reason -- so the key that opens one cluster is the wrong key for the next.
    ``password_env_var`` is per *install*: clustrix reads exactly one variable
    name, it is the only channel for supplying a password without writing it
    to disk, and what differs per target is the variable's contents, not its
    name. Change the target and the key file is wrong; change the target and
    the variable name is still right. ``test_a_key_file_is_bound_to_a_host``
    is that rule executed.

    Two other distinctions were tried and are false, so they are recorded here
    rather than left to be re-derived. "It holds no secret" separates nothing:
    ``_NOT_ACTUALLY_SECRET`` keeps this pair out of ``SECRET_FIELDS`` on
    purpose, so ``save_to_file`` writes both in plaintext -- and ``key_file``,
    which stays backend-only, is equally a name rather than a credential and
    is equally written. "It cannot be recovered from disk" separates nothing
    either: no member of the set is unrecoverable, since the reset clears only
    the setting and every control still shows its value afterwards. Both are
    asserted below, so neither can be quoted as a justification again.
    """

    def test_the_env_var_pair_is_written_to_disk_like_key_file(self, tmp_path):
        """The "only unrecoverable setting" argument, refuted.

        ``save_to_file`` omits ``SECRET_FIELDS``, and this pair is deliberately
        not in it -- the flag and the variable *name* are not the password.
        So the file keeps them, exactly as it keeps ``key_file``, which stays
        on the backend-only side of the line. Secrecy and recoverability
        therefore cannot be what separates the two groups.
        """
        assert "password_env_var" not in SECRET_FIELDS
        assert "use_env_password" not in SECRET_FIELDS
        assert "key_file" not in SECRET_FIELDS

        destination = tmp_path / "written.yml"
        ClusterConfig(
            password_env_var="EXAMPLE_PW_VAR",
            use_env_password=True,
            key_file="~/.ssh/id_ed25519",
        ).save_to_file(str(destination))
        written = destination.read_text(encoding="utf-8")

        assert "password_env_var" in written
        assert "use_env_password" in written
        assert "key_file" in written

    def test_a_key_file_is_bound_to_a_host_and_the_env_var_is_not(self, capsys):
        """The rule above, executed on the one pair a careful reader will
        push on: both values are machine-local pointers to a credential, so
        locality cannot be why one is dropped and the other kept.

        Switch from a cluster reached with an SSH key to HuggingFace Jobs.
        The key file names a key that authenticates to *that* host, so it is
        wrong for the new target and goes; the environment variable names the
        channel a password is read from on this install, is equally right for
        the new target, and stays. Moving ``key_file`` out of
        ``BACKEND_ONLY_FIELDS`` on the strength of "but it is local too" turns
        the first assertion red.
        """
        configure(password_env_var="EXAMPLE_PW_VAR", use_env_password=True)
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "ssh"
        widget.widgets["host"].value = "hpc.example.edu"
        widget.widgets["username"].value = "researcher"
        widget.widgets["ssh_key_file"].value = "~/.ssh/id_ed25519"
        widget._update_ui_for_cluster_type()

        widget.widgets["cluster_type"].value = "huggingface"
        widget.widgets["hf_namespace"].value = "contextlab"
        widget._update_ui_for_cluster_type()
        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.key_file == ClusterConfig().key_file
        assert live.cluster_host is None
        assert live.password_env_var == "EXAMPLE_PW_VAR"
        assert live.use_env_password is True

    def test_a_reset_backend_field_is_still_on_screen(self, capsys):
        """The "unrecoverable" argument again, from the other side: the reset
        clears the *setting*, not the control. Every backend-only field the
        widget just dropped is still sitting in its box, so no member of the
        set is any harder to get back than any other."""
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "ssh"
        widget.widgets["host"].value = "hpc.example.edu"
        widget.widgets["username"].value = "researcher"
        widget.widgets["ssh_key_file"].value = "~/.ssh/id_ed25519"
        widget._update_ui_for_cluster_type()

        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        assert get_config().cluster_host is None
        assert widget.widgets["host"].value == "hpc.example.edu"
        assert widget.widgets["username"].value == "researcher"
        assert widget.widgets["ssh_key_file"].value == "~/.ssh/id_ed25519"

    def test_modern_widget_local_apply_keeps_the_password_env_var(self, capsys):
        configure(password_env_var="MY_CLUSTER_PW", use_env_password=True)
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()

        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.cluster_type == "local"
        assert live.password_env_var == "MY_CLUSTER_PW"
        assert live.use_env_password is True
        # The name of the variable is not a secret, but nothing about this
        # change may start printing values either.
        assert "MY_CLUSTER_PW" not in told

    def test_legacy_widget_agrees(self, capsys):
        """The two widgets must not disagree about which settings a backend
        switch owns. The legacy one manages neither field, so it already
        leaves both alone -- asserted here so a later edit that adds them to
        its managed set is caught rather than shipped."""
        configure(password_env_var="MY_CLUSTER_PW", use_env_password=True)
        widget = EnhancedClusterConfigWidget()
        widget.configs["Just Local"] = {"name": "Just Local", "cluster_type": "local"}
        widget._load_config_to_widgets("Just Local")

        told = _press(
            lambda: widget._on_apply_config(None), widget.status_output, capsys
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.cluster_type == "local"
        assert live.password_env_var == "MY_CLUSTER_PW"
        assert live.use_env_password is True
        assert "MY_CLUSTER_PW" not in told

    def test_a_local_apply_still_drops_the_target_and_its_credentials(self, capsys):
        """The other half of the line: everything that does name a target,
        or unlock one, is still cleared. Without this the fix above could be
        "delete BACKEND_ONLY_FIELDS" and nothing would complain."""
        configure(
            cluster_type="huggingface",
            hf_namespace="contextlab",
            hf_flavor="a10g-small",
            hf_token="hf_SECRETTOKEN",
            cluster_host="hpc.example.edu",
            username="researcher",
            password="hunter2-example",
        )
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()

        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        live = get_config()
        assert live.cluster_host is None
        assert live.username is None
        assert live.password is None
        assert live.hf_namespace is None
        assert live.hf_flavor is None
        assert live.hf_token is None
        assert "hf_SECRETTOKEN" not in told
        assert "hunter2-example" not in told

    def test_a_local_apply_revokes_the_permission_to_spend_money(self, capsys):
        """``hf_allow_gpu_flavors`` is not a preference, it is consent to be
        billed by the second, and it is consent for *one* target.

        ``hf_jobs._flavor`` refuses a GPU flavor unless this is True, so
        leaving it standing across a backend switch carries a permission the
        user granted for a HuggingFace namespace into whatever they point at
        next -- and then back to HuggingFace, under a different namespace,
        still granted. It has to fail safe, meaning it must land back on the
        dataclass default rather than merely "not the previous value".
        """
        assert ClusterConfig().hf_allow_gpu_flavors is False
        configure(
            cluster_type="huggingface",
            hf_namespace="contextlab",
            hf_allow_gpu_flavors=True,
        )
        widget = ModernClustrixWidget()
        assert widget.widgets["hf_allow_gpu"].value is True

        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        assert get_config().hf_allow_gpu_flavors is False

    def test_a_reset_backend_field_lands_on_its_real_default_not_none(self, capsys):
        """The same defect D10 fixed one layer down, in
        ``_config_data_for_backend``: the reset has to write
        ``ClusterConfig()``'s value for the field, not ``None``.

        Eight of the ten backend-only fields default to ``None`` anyway, so
        ``None`` passes every assertion phrased as "the host is gone" while
        leaving ``cluster_port`` -- typed ``int`` -- and ``remote_work_dir``
        -- typed ``str`` -- holding a value their consumers cannot use. Said
        the way the code that breaks says it.
        """
        configure(
            cluster_type="ssh",
            cluster_host="hpc.example.edu",
            username="researcher",
            cluster_port=2222,
            remote_work_dir="/scratch/researcher/clustrix",
        )
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()

        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "❌" not in told, told
        live = get_config()
        defaults = ClusterConfig()
        assert live.cluster_port == defaults.cluster_port
        assert live.remote_work_dir == defaults.remote_work_dir
        # Said the way the code that breaks says it.
        assert 1 <= int(live.cluster_port) <= 65535
        assert live.remote_work_dir.rstrip("/").endswith("jobs")


class TestASavedFlavorCanStillOpenTheWidget:
    """A list baked into the UI must not be able to veto a saved
    configuration.

    ``ClusterConfig`` validates neither ``hf_flavor`` nor ``package_manager``
    -- any string is accepted -- while the modern widget offers ten flavors
    and four package managers in ``Dropdown``s. Assigning an unlisted value to
    a ``Dropdown`` raises ``TraitError: Invalid selection``, and both
    assignments happen in ``_load_config_to_widgets``, which the constructor
    calls. So a user who configured a flavor this build has not heard of could
    not open the widget at all -- not a degraded panel, an exception.

    The legacy widget hit this first and fixed it by widening the options
    instead of discarding the value; ``set_choice`` is now that one
    implementation, used by both.
    """

    def test_a_flavor_the_dropdown_never_heard_of_opens_and_survives(self, capsys):
        configure(cluster_type="huggingface", hf_namespace="contextlab")
        configure(hf_flavor="a10g-large")

        widget = ModernClustrixWidget()

        assert widget.widgets["hf_flavor"].value == "a10g-large"
        assert "a10g-large" in widget.widgets["hf_flavor"].options
        # Still there afterwards: widening the options is only useful if the
        # value then reaches the configuration rather than being replaced by
        # the first entry in the list.
        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )
        assert "❌" not in told, told
        assert get_config().hf_flavor == "a10g-large"

    def test_a_package_manager_the_dropdown_never_heard_of_opens(self, capsys):
        configure(cluster_type="local", package_manager="mamba")

        widget = ModernClustrixWidget()

        assert widget.widgets["package_manager"].value == "mamba"
        told = _press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )
        assert "❌" not in told, told
        assert get_config().package_manager == "mamba"

    def test_the_legacy_widget_loads_a_package_manager_it_does_not_offer(self):
        """Found while fixing the modern widget, and reachable between the
        two: the legacy menu offers only pip and conda, while the modern one
        writes ``auto`` and ``uv``. Selecting such a profile here used to
        raise ``TraitError`` out of the dropdown observer."""
        widget = EnhancedClusterConfigWidget()
        widget.configs["From The Modern Widget"] = {
            "name": "From The Modern Widget",
            "cluster_type": "local",
            "package_manager": "uv",
        }

        widget._load_config_to_widgets("From The Modern Widget")

        assert widget.package_manager.value == "uv"
        assert widget._save_config_from_widgets()["package_manager"] == "uv"

    def test_the_listed_values_are_still_the_only_ones_offered(self):
        """Widening happens for the value actually saved, not for everything:
        a config that names nothing unusual must not grow the menu."""
        configure(cluster_type="huggingface", hf_namespace="contextlab")
        widget = ModernClustrixWidget()
        assert "a10g-large" not in widget.widgets["hf_flavor"].options
        assert "mamba" not in widget.widgets["package_manager"].options


class TestHowTheMenuIsWidened:
    """Widening is not just "the value ends up selected".

    ``set_choice`` exists because a list baked into the UI must not veto a
    saved configuration. *How* it adds the value is separately load-bearing,
    and each property below survived round three with nothing pinning it --
    every one of these tests was written by mutating the shipped function and
    watching the suite stay green.
    """

    def _menu(self, options, value="pip"):
        import ipywidgets as widgets

        return widgets.Dropdown(options=list(options), value=value)

    def test_loading_one_profile_three_times_adds_one_entry(self):
        """R3, the dedupe guard. The config dropdown's observer reloads a
        profile every time it is selected, so this is the ordinary path, not
        an edge case. Without the guard the menu grew a fresh copy on every
        load -- and every *listed* value grew one too, because the widened
        list was rebuilt from itself."""
        widget = EnhancedClusterConfigWidget()
        baseline = list(widget.package_manager.options)
        widget.configs["Mamba Box"] = {
            "name": "Mamba Box",
            "cluster_type": "local",
            "package_manager": "mamba",
        }

        for _ in range(3):
            widget._load_config_to_widgets("Mamba Box")

        offered = list(widget.package_manager.options)
        assert offered.count("mamba") == 1, offered
        assert offered == baseline + ["mamba"], offered
        assert widget.package_manager.value == "mamba"

    def test_the_widened_value_is_appended_not_prepended(self):
        """R4. Order is the menu's design -- the modern flavor list runs
        cheapest first -- and entry zero is what a fresh widget shows, so
        putting the saved value at the front both scrambles the design and
        changes the default for every configuration afterwards."""
        field = self._menu(["pip", "conda"])

        set_choice(field, "mamba")

        assert list(field.options) == ["pip", "conda", "mamba"]
        assert list(field.options)[0] == "pip"
        assert list(field.options)[-1] == "mamba"

    def test_a_blank_value_adds_no_blank_entry(self):
        """R5. Guarding on ``is None`` alone leaves ``package_manager: ""``
        -- which a widget that wrote an empty box produces -- putting an
        entry with nothing in it at the end of the menu and *selecting* it,
        so the user is looking at a chosen setting they cannot read."""
        field = self._menu(["pip", "conda"])

        set_choice(field, "")

        assert list(field.options) == ["pip", "conda"]
        assert field.value == "pip"
        assert "" not in field.options

    def test_a_blank_package_manager_reaches_this_through_a_profile(self):
        """The same defect where a user meets it: a saved profile whose
        package manager was cleared."""
        widget = EnhancedClusterConfigWidget()
        baseline = list(widget.package_manager.options)
        widget.configs["Cleared"] = {
            "name": "Cleared",
            "cluster_type": "local",
            "package_manager": "",
        }

        widget._load_config_to_widgets("Cleared")

        assert list(widget.package_manager.options) == baseline
        assert widget.package_manager.value in baseline

    def test_whitespace_only_is_blank_and_padding_is_stripped(self):
        """``(None, "")`` was guarded and ``" "`` was not, so a hand-edited
        YAML with a trailing space produced a menu entry that looks empty and
        a setting that is not the one it names. Stripping rather than merely
        rejecting matches what the widget does with every other text it
        reads."""
        blank = self._menu(["pip", "conda"])
        set_choice(blank, "   ")
        assert list(blank.options) == ["pip", "conda"]
        assert blank.value == "pip"

        padded = self._menu(["pip", "conda"])
        set_choice(padded, "  mamba  ")
        assert list(padded.options) == ["pip", "conda", "mamba"]
        assert padded.value == "mamba"

        # And a padded value that is already offered selects it rather than
        # growing a near-duplicate beside it.
        listed = self._menu(["pip", "conda"])
        set_choice(listed, " conda ")
        assert list(listed.options) == ["pip", "conda"]
        assert listed.value == "conda"

    def test_a_paired_options_menu_is_refused_rather_than_corrupted(self):
        """ipywidgets also accepts ``(label, value)`` pairs. Appending a bare
        string to those adds a second entry for a value that is already there
        *and* relabels every existing one, since ipywidgets then reads each
        pair's members as separate labels. No caller does this today, so this
        fails loudly for whoever does it first instead of silently producing a
        menu that lies."""
        import ipywidgets as widgets

        field = widgets.Dropdown(options=[("Pip", "pip"), ("Conda", "conda")])
        before = list(field.options)

        with pytest.raises(TypeError) as raised:
            set_choice(field, "mamba")

        assert "flat list of strings" in str(raised.value)
        assert list(field.options) == before
        assert field.value == "pip"

    def test_a_non_string_becomes_a_label_rather_than_a_locked_widget(self):
        """A ``Dropdown``'s options are labels. A hand-edited
        ``package_manager: 3`` is nonsense either way, but refusing it would
        be the widget failing to open -- which is the defect this function
        exists to fix -- and putting a bare ``int`` in the list makes the menu
        heterogeneous."""
        field = self._menu(["pip", "conda"])

        set_choice(field, 3)

        assert list(field.options) == ["pip", "conda", "3"]
        assert field.value == "3"

    def test_widening_accumulates_across_profile_switches_on_purpose(self):
        """Recorded as a decision, not left to be rediscovered.

        Loading three profiles with three unlisted package managers leaves all
        three in the menu for the rest of the session. That is wanted: the
        alternative -- rebuilding the menu from the hardcoded list on each
        load -- means switching back to the first profile raises the very
        ``TraitError`` ``set_choice`` exists to prevent. The accumulation is
        per widget instance and is never written anywhere, which
        ``test_the_widened_options_are_not_persisted`` already holds.
        """
        widget = EnhancedClusterConfigWidget()
        baseline = list(widget.package_manager.options)
        for name, manager in (("A", "mamba"), ("B", "uv"), ("C", "poetry")):
            widget.configs[name] = {
                "name": name,
                "cluster_type": "local",
                "package_manager": manager,
            }
            widget._load_config_to_widgets(name)

        assert list(widget.package_manager.options) == baseline + [
            "mamba",
            "uv",
            "poetry",
        ]

        # Going back is the point of keeping them.
        widget._load_config_to_widgets("A")
        assert widget.package_manager.value == "mamba"
        assert widget._save_config_from_widgets()["package_manager"] == "mamba"

    def test_the_widened_options_are_not_persisted(self):
        """The accumulation above is only acceptable because it dies with the
        widget. What Save writes is the *value*; the widened list is not a
        setting and must not become one, or every session would inherit the
        last one's typos."""
        widget = EnhancedClusterConfigWidget()
        baseline = list(widget.package_manager.options)
        widget.configs["Mamba Box"] = {
            "name": "Mamba Box",
            "cluster_type": "local",
            "package_manager": "mamba",
        }
        widget._load_config_to_widgets("Mamba Box")
        assert "mamba" in widget.package_manager.options

        saved = widget._save_config_from_widgets()
        assert saved["package_manager"] == "mamba"
        assert not any(
            isinstance(value, (list, tuple)) and "mamba" in value
            for value in saved.values()
        ), saved

        # A fresh widget offers the hardcoded list again.
        assert list(EnhancedClusterConfigWidget().package_manager.options) == baseline


class TestAProfileNamingARemovedBackend:
    """``cluster_type`` is exempt from ``set_choice``, and the reason is the
    inverse of the one that applies to every other dropdown.

    ``set_choice`` widens a menu because the saved configuration is
    authoritative: ``ClusterConfig`` accepts any string for ``hf_flavor`` or
    ``package_manager``, so a list baked into the UI has no standing to veto
    one. ``cluster_type`` is the single field with an *enforced domain* --
    ``ClusterConfig(cluster_type="pbs")`` and ``load_config()`` both raise
    ``ValueError`` naming issue #140 -- so here the menu is authoritative and
    the saved value is the thing that can be wrong. Widening would offer a
    backend the executor cannot dispatch and defer the failure to submission.

    The value can still reach the widget: ``load_config_from_file`` collects
    what is on disk rather than validating it, so ``cluster_type: pbs`` lands
    in ``self.configs`` intact. Selecting it raised a bare ``TraitError:
    Invalid selection`` out of the dropdown observer, naming neither the
    backend nor why it is gone.
    """

    def _widget_with_a_pbs_profile(self):
        widget = EnhancedClusterConfigWidget()
        widget.configs["Old PBS Cluster"] = {
            "name": "Old PBS Cluster",
            "cluster_type": "pbs",
            "cluster_host": "hpc.example.edu",
        }
        return widget

    def test_selecting_it_names_the_backend_and_its_tracking_issue(self, capsys):
        widget = self._widget_with_a_pbs_profile()

        told = _press(
            lambda: widget._on_config_select(
                {"new": "Old PBS Cluster", "old": None, "name": "value"}
            ),
            widget.status_output,
            capsys,
        )

        assert "pbs" in told
        assert "#140" in told
        assert "Old PBS Cluster" in told
        assert "local, ssh, slurm, huggingface" in told

    def test_it_does_not_widen_the_menu_or_half_load_the_profile(self, capsys):
        widget = self._widget_with_a_pbs_profile()
        was_selected = widget.cluster_type.value
        was_named = widget.current_config_name

        _press(
            lambda: widget._on_config_select(
                {"new": "Old PBS Cluster", "old": None, "name": "value"}
            ),
            widget.status_output,
            capsys,
        )

        assert "pbs" not in widget.cluster_type.options
        assert list(widget.cluster_type.options) == list(SUPPORTED_CLUSTER_TYPES)
        assert widget.cluster_type.value == was_selected
        # Nothing else from the refused profile got in either, and the widget
        # still believes it is showing what it was showing.
        assert widget.host_field.value != "hpc.example.edu"
        assert widget.current_config_name == was_named

    def test_both_menus_are_the_supported_tuple_itself(self):
        """Two tests already asserted these options and both compared against
        a hardcoded copy of the four names, so the drift they were meant to
        catch was pinned in place: ``notebook_magic_widget`` spelled the list
        out rather than reading ``SUPPORTED_CLUSTER_TYPES``, and no test could
        tell."""
        legacy = EnhancedClusterConfigWidget()
        modern = ModernClustrixWidget()

        assert list(legacy.cluster_type.options) == list(SUPPORTED_CLUSTER_TYPES)
        assert list(modern.widgets["cluster_type"].options) == list(
            SUPPORTED_CLUSTER_TYPES
        )
