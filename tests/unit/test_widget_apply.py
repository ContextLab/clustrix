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
    throwaway: a previous run of these tests polluted a real ~/.clustrix."""
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
