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
