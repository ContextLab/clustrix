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
from clustrix.profile_manager import ProfileManager  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_profile_store(tmp_path, monkeypatch):
    """Profiles persist to ~/.clustrix now, so tests must not read the
    developer's own store -- or write to it."""
    monkeypatch.setattr(
        ProfileManager, "__init__", _profile_manager_init(tmp_path / "profiles")
    )
    from clustrix.config import _config

    _config.__dict__.update(ClusterConfig().__dict__)


def _profile_manager_init(directory):
    original = ProfileManager.__init__

    def patched(self, config_dir=None):
        original(self, config_dir=str(directory))

    return patched


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
        before = len(widget.profile_manager.get_profile_names())

        widget._on_add_profile(widget.widgets["add_profile_btn"])

        assert widget.profile_manager.load_profile("A").default_cores == 99
        assert len(widget.profile_manager.get_profile_names()) == before + 1


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


class TestTheWidgetAgreesWithTheLibrary:
    """The widget used to only ever *write*, never read."""

    def test_it_opens_showing_the_live_configuration(self):
        """A session that already called configure() saw the widget contradict
        it, and Apply would then overwrite the real config with the defaults
        on screen."""
        import clustrix

        clustrix.configure(
            cluster_type="huggingface",
            hf_namespace="contextlab",
            hf_flavor="cpu-upgrade",
        )
        widget = ModernClustrixWidget()

        assert widget.widgets["cluster_type"].value == "huggingface"
        assert widget.widgets["hf_namespace"].value == "contextlab"
        assert widget.widgets["hf_flavor"].value == "cpu-upgrade"

    def test_the_live_configuration_appears_as_its_own_profile(self):
        """It must not silently overwrite one of the templates."""
        import clustrix

        clustrix.configure(cluster_type="slurm", cluster_host="live.example.edu")
        widget = ModernClustrixWidget()

        assert widget.widgets["profile_dropdown"].value == "Current configuration"
        assert "Local single-core" in widget.profile_manager.get_profile_names()

    def test_the_displayed_values_match_the_selected_profile(self):
        """Fresh widgets showed 16GB while the profile held 16.25GB."""
        widget = ModernClustrixWidget()
        name = widget.widgets["profile_dropdown"].value
        stored = widget.profile_manager.load_profile(name)

        assert widget.widgets["ram"].value == stored.default_memory
        assert widget.widgets["cpus"].value == stored.default_cores

    def test_apply_replaces_rather_than_merges(self):
        """Applying ssh then local left cluster_host pointing at the old host."""
        import clustrix

        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "ssh"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "login.example.edu"
        widget.widgets["username"].value = "alice"
        widget._on_apply_config(widget.widgets["apply_btn"])
        assert clustrix.get_config().cluster_host == "login.example.edu"

        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        widget._on_apply_config(widget.widgets["apply_btn"])

        assert clustrix.get_config().cluster_type == "local"
        assert not clustrix.get_config().cluster_host
        assert not clustrix.get_config().username


class TestBackendSettingsSurviveSwitching:
    """Switching profiles used to overwrite each one with the previous screen."""

    def _widget_with(self, extra):
        widget = ModernClustrixWidget()
        for name, config in extra.items():
            widget.profile_manager.save_profile(name, config)
        widget._update_profile_dropdown()
        return widget

    def test_huggingface_and_kubernetes_settings_are_not_cross_contaminated(self):
        widget = self._widget_with(
            {
                "MyHF": ClusterConfig(
                    cluster_type="huggingface",
                    hf_namespace="contextlab",
                    hf_flavor="cpu-upgrade",
                ),
                "MyK8s": ClusterConfig(
                    cluster_type="kubernetes",
                    k8s_namespace="research",
                    k8s_image="python:3.10",
                ),
            }
        )
        dropdown = widget.widgets["profile_dropdown"]
        for name in ["MyHF", "MyK8s", "MyHF", "MyK8s"]:
            dropdown.value = name

        hf = widget.profile_manager.load_profile("MyHF")
        k8s = widget.profile_manager.load_profile("MyK8s")
        assert (hf.hf_namespace, hf.hf_flavor) == ("contextlab", "cpu-upgrade")
        assert (k8s.k8s_namespace, k8s.k8s_image) == ("research", "python:3.10")

    def test_the_remote_work_directory_survives(self):
        widget = self._widget_with(
            {
                "Scratch": ClusterConfig(
                    cluster_type="slurm",
                    cluster_host="a.edu",
                    remote_work_dir="/scratch/alice/clustrix",
                ),
                "Other": ClusterConfig(cluster_type="local"),
            }
        )
        dropdown = widget.widgets["profile_dropdown"]
        dropdown.value = "Scratch"
        dropdown.value = "Other"
        dropdown.value = "Scratch"

        assert widget.widgets["home_dir"].value == "/scratch/alice/clustrix"

    def test_a_password_reaches_the_configuration(self):
        """Test connect passed the password explicitly and succeeded, while the
        job itself authenticated from config.password and failed."""
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "slurm"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "a.edu"
        widget.widgets["username"].value = "alice"
        widget.widgets["password"].value = "s3cret"

        assert widget._get_config_from_widgets().password == "s3cret"

    def test_unticking_clone_environment_has_an_effect(self):
        widget = ModernClustrixWidget()
        widget.widgets["clone_env"].value = False

        assert widget._get_config_from_widgets().replicate_local_environment is False


class TestProfilesSurviveARestart:
    def test_a_saved_profile_is_there_next_session(self, tmp_path):
        """Profiles were re-seeded from the built-ins on every kernel start."""
        from clustrix.profile_manager import ProfileManager as PM

        first = PM(config_dir=str(tmp_path / "store"))
        first.save_profile(
            "Mine", ClusterConfig(cluster_type="slurm", default_cores=64)
        )
        first.set_active_profile("Mine")

        second = PM(config_dir=str(tmp_path / "store"))
        assert second.active_profile == "Mine"
        assert second.load_profile("Mine").default_cores == 64


class TestTheConfigFilePicker:
    def test_it_offers_files_rather_than_requiring_a_guess(self, tmp_path):
        widget = ModernClustrixWidget()
        assert isinstance(widget.widgets["config_filename"].options, (list, tuple))
        assert widget.widgets["config_filename"].value == "profiles.yml"

    def test_it_does_not_offer_unrelated_yaml(self, tmp_path, monkeypatch):
        """A working tree is full of YAML that has nothing to do with clustrix."""
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        (tmp_path / "profiles.yml").write_text(
            "active_profile: A\nprofiles:\n  A:\n    cluster_type: local\n"
        )
        monkeypatch.chdir(tmp_path)

        widget = ModernClustrixWidget()
        offered = " ".join(widget._discover_config_files())

        assert "pre-commit" not in offered
