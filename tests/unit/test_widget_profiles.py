"""The widget's profile row, which used to look inert.

Every symptom below was reported as "the dropdown doesn't do anything", and
they share one cause: the widget only ever *read* profiles. Whatever was typed
lived nowhere but the controls themselves, so switching away discarded it and
switching back redisplayed the stored values -- indistinguishable from a
dropdown that ignores you.

These drive the real handlers on the real widgets.
"""

import pytest

pytest.importorskip("ipywidgets")

from clustrix.config import ClusterConfig  # noqa: E402
from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402


@pytest.fixture
def widget():
    w = ModernClustrixWidget()
    w.profile_manager.save_profile(
        "A", ClusterConfig(cluster_type="slurm", cluster_host="a.edu", username="u1")
    )
    w.profile_manager.save_profile(
        "B", ClusterConfig(cluster_type="ssh", cluster_host="b.edu", username="u2")
    )
    w._update_profile_dropdown()
    return w


class TestSwitchingProfiles:
    def test_selecting_a_profile_shows_that_profile(self, widget):
        widget.widgets["profile_dropdown"].value = "B"
        assert widget.widgets["cluster_type"].value == "ssh"
        assert widget.widgets["host"].value == "b.edu"

        widget.widgets["profile_dropdown"].value = "A"
        assert widget.widgets["cluster_type"].value == "slurm"
        assert widget.widgets["host"].value == "a.edu"

    def test_edits_survive_a_round_trip(self, widget):
        """The reported symptom: type something, switch away, come back, gone."""
        widget.widgets["profile_dropdown"].value = "A"
        widget.widgets["host"].value = "edited.edu"
        widget.widgets["cpus"].value = 64

        widget.widgets["profile_dropdown"].value = "B"
        widget.widgets["profile_dropdown"].value = "A"

        assert widget.widgets["host"].value == "edited.edu"
        assert widget.widgets["cpus"].value == 64

    def test_edits_do_not_leak_into_the_other_profile(self, widget):
        widget.widgets["profile_dropdown"].value = "A"
        widget.widgets["host"].value = "edited.edu"
        widget.widgets["profile_dropdown"].value = "B"

        assert widget.widgets["host"].value == "b.edu"

    def test_switching_updates_the_active_profile(self, widget):
        widget.widgets["profile_dropdown"].value = "B"
        assert widget.profile_manager.active_profile == "B"


class TestAddingAndRemoving:
    def test_add_clones_what_is_on_screen(self, widget):
        """Cloning the *stored* profile silently threw away every edit."""
        widget.widgets["profile_dropdown"].value = "A"
        widget.widgets["cpus"].value = 99
        widget.widgets["host"].value = "onscreen.edu"

        widget._on_add_profile(widget.widgets["add_profile_btn"])

        assert widget.widgets["cpus"].value == 99
        assert widget.widgets["host"].value == "onscreen.edu"

    def test_add_leaves_the_original_intact(self, widget):
        widget.widgets["profile_dropdown"].value = "A"
        widget.widgets["cpus"].value = 99
        widget._on_add_profile(widget.widgets["add_profile_btn"])

        assert widget.profile_manager.load_profile("A").default_cores == 99
        assert len(widget.profile_manager.get_profile_names()) == 4


class TestSaveAndLoad:
    def test_saving_reports_where_the_file_went(self, widget, tmp_path):
        """A bare filename resolves under ~/.clustrix, so silence reads as
        'nothing happened'."""
        target = tmp_path / "profiles.yml"
        widget.widgets["config_filename"].value = str(target)

        widget._on_save_config(widget.widgets["save_btn"])

        assert target.exists() and target.stat().st_size > 0
        assert "saved" in widget.widgets["status_pill"].value

    def test_a_saved_file_reloads_into_a_fresh_widget(self, widget, tmp_path):
        target = tmp_path / "profiles.yml"
        widget.widgets["config_filename"].value = str(target)
        widget.widgets["profile_dropdown"].value = "A"
        widget.widgets["host"].value = "roundtrip.edu"
        widget._on_save_config(widget.widgets["save_btn"])

        fresh = ModernClustrixWidget()
        fresh.widgets["config_filename"].value = str(target)
        fresh._on_load_config(fresh.widgets["load_btn"])

        assert {"A", "B"} <= set(fresh.profile_manager.get_profile_names())
        assert fresh.profile_manager.load_profile("A").cluster_host == "roundtrip.edu"
        assert "loaded" in fresh.widgets["status_pill"].value

    def test_a_failed_load_says_so(self, widget, tmp_path):
        widget.widgets["config_filename"].value = str(tmp_path / "not-here.yml")
        widget._on_load_config(widget.widgets["load_btn"])
        assert "failed" in widget.widgets["status_pill"].value


class TestTestJobButton:
    def test_it_works_for_the_default_cluster_type(self):
        """`local` is the default, and ClusterExecutor rejects it outright, so
        this button failed for every new user before they configured anything.
        """
        widget = ModernClustrixWidget()
        assert widget.widgets["cluster_type"].value == "local"

        widget._on_test_submit(widget.widgets["test_submit_btn"])

        assert "completed" in widget.widgets["status_pill"].value

    def test_every_offered_cluster_type_is_either_runnable_or_validated(self):
        """A type the button cannot handle must fail validation, not crash."""
        widget = ModernClustrixWidget()
        for cluster_type in widget.widgets["cluster_type"].options:
            widget.widgets["cluster_type"].value = cluster_type
            widget._update_ui_for_cluster_type()
            widget._on_test_submit(widget.widgets["test_submit_btn"])
            pill = widget.widgets["status_pill"].value
            assert any(
                word in pill for word in ("completed", "invalid", "failed")
            ), f"{cluster_type} left the pill saying {pill!r}"
