"""The widget's profile row, which used to look inert.

Every symptom below was reported as "the dropdown doesn't do anything", and
they share one cause: the widget only ever *read* profiles. Whatever was typed
lived nowhere but the controls themselves, so switching away discarded it and
switching back redisplayed the stored values -- indistinguishable from a
dropdown that ignores you.

These drive the real handlers on the real widgets.
"""

import os

import pytest

pytest.importorskip("ipywidgets")

from dataclasses import asdict  # noqa: E402

from clustrix.config import ClusterConfig, configure  # noqa: E402
from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402
from clustrix.profile_manager import ProfileManager  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_profile_store(tmp_path, monkeypatch):
    """Profiles persist to ~/.clustrix now, so tests must not read the
    developer's own store -- or write to it."""
    monkeypatch.setattr(
        ProfileManager, "__init__", _profile_manager_init(tmp_path / "profiles")
    )
    # get_config(), not `from clustrix.config import _config`. Binding the
    # singleton by name is the one thing that would make deferring the
    # standard-location search to first use unsafe: a by-name importer can
    # hold the object as it stood *before* the search ran. Nothing in the
    # package does it, and this fixture was the only place in the tests that
    # did, which made the claim in clustrix/config.py true only when scoped to
    # the package. Now it is true everywhere.
    from clustrix.config import get_config

    configure(**asdict(ClusterConfig()))


def _profile_manager_init(directory):
    """Default to a throwaway directory, but honour an explicit one.

    Tests that construct a store deliberately -- to corrupt it, or to check it
    survives a restart -- must reach their own directory, not this default.
    """
    original = ProfileManager.__init__

    def patched(self, config_dir=None):
        original(self, config_dir=config_dir or str(directory))

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

    def test_huggingface_and_ssh_settings_are_not_cross_contaminated(self):
        widget = self._widget_with(
            {
                "MyHF": ClusterConfig(
                    cluster_type="huggingface",
                    hf_namespace="contextlab",
                    hf_flavor="cpu-upgrade",
                ),
                "MySSH": ClusterConfig(
                    cluster_type="ssh",
                    cluster_host="gpu.example.edu",
                    username="researcher",
                ),
            }
        )
        dropdown = widget.widgets["profile_dropdown"]
        for name in ["MyHF", "MySSH", "MyHF", "MySSH"]:
            dropdown.value = name

        hf = widget.profile_manager.load_profile("MyHF")
        ssh = widget.profile_manager.load_profile("MySSH")
        assert (hf.hf_namespace, hf.hf_flavor) == ("contextlab", "cpu-upgrade")
        assert (ssh.cluster_host, ssh.username) == ("gpu.example.edu", "researcher")

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


class TestApplyTouchesOnlyWhatItOwns:
    """Apply has been wrong in both directions."""

    UNMANAGED = {
        "cluster_packages": ["networkx"],
        "excluded_packages": ["appnope"],
        "venv_setup_timeout": 1800,
        "job_poll_interval": 5,
    }

    def _configured_widget(self):
        import clustrix

        clustrix.configure(cluster_type="local", **self.UNMANAGED)
        return ModernClustrixWidget()

    def test_settings_with_no_control_survive(self):
        """Replacing the config wholesale discarded everything the widget has
        no field for -- all of it only settable from code."""
        import clustrix

        widget = self._configured_widget()
        widget._on_apply_config(widget.widgets["apply_btn"])

        live = clustrix.get_config()
        for field, value in self.UNMANAGED.items():
            assert getattr(live, field) == value, field

    def test_fields_from_a_previous_apply_do_not_linger(self):
        """Merging non-None values left the old cluster_host behind."""
        import clustrix

        widget = self._configured_widget()
        widget.widgets["cluster_type"].value = "ssh"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "login.example.edu"
        widget.widgets["username"].value = "alice"
        widget._on_apply_config(widget.widgets["apply_btn"])

        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        widget._on_apply_config(widget.widgets["apply_btn"])

        assert not clustrix.get_config().cluster_host
        assert not clustrix.get_config().username

    def test_the_managed_list_matches_what_the_widget_actually_sets(self):
        """The two must not drift: a field collected but not listed would
        never be reset, and a field listed but not collected would be wiped."""
        from clustrix.modern_notebook_widget import WIDGET_MANAGED_FIELDS

        widget = ModernClustrixWidget()
        produced = set()
        for cluster_type in widget.widgets["cluster_type"].options:
            widget.widgets["cluster_type"].value = cluster_type
            widget._update_ui_for_cluster_type()
            produced |= set(widget._config_data_from_widgets())

        assert produced == set(WIDGET_MANAGED_FIELDS)

    def test_every_managed_field_is_a_real_config_field(self):
        from dataclasses import fields as dataclass_fields

        from clustrix.modern_notebook_widget import WIDGET_MANAGED_FIELDS

        known = {f.name for f in dataclass_fields(ClusterConfig)}
        assert set(WIDGET_MANAGED_FIELDS) <= known


class TestOpeningTheWidgetIsSafe:
    """Constructing the widget scans directories and touches the store."""

    def test_a_broken_symlink_in_the_working_directory(self, tmp_path, monkeypatch):
        """Sorting by mtime outside the try raised before the widget opened."""
        (tmp_path / "broken.yml").symlink_to(tmp_path / "nonexistent")
        monkeypatch.chdir(tmp_path)

        assert ModernClustrixWidget() is not None

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX-only")
    def test_a_fifo_named_like_a_config_file(self, tmp_path, monkeypatch):
        """The size check passed and open() then blocked forever, with no
        writer, so the constructor never returned."""
        os.mkfifo(tmp_path / "pipe.yml")
        monkeypatch.chdir(tmp_path)

        assert ModernClustrixWidget() is not None

    def test_it_does_not_overwrite_a_saved_current_configuration(self, tmp_path):
        """That profile holds settings the live config does not, and losing
        them to merely opening the widget is the worst kind of surprise.

        The marker used to be ``hf_token``. It cannot be any more:
        ``ProfileManager.save_to_file`` now drops credential-bearing fields
        on the way to disk, deliberately and with no opt-in, because
        ``_persist()`` fires from seven mutators and nobody asked for a
        password to be written out world-readable in plaintext. A token
        therefore no longer survives a restart *by design*, which is
        asserted directly below rather than left implicit here.
        ``hf_namespace`` is the same shape of setting without being a
        secret, so it still pins what this test is actually about: opening
        the widget must not clobber a saved profile.
        """
        import clustrix
        from clustrix.profile_manager import ProfileManager as PM

        store = str(tmp_path / "store")
        first = PM(config_dir=store)
        first.save_profile(
            "Current configuration",
            ClusterConfig(cluster_type="huggingface", hf_namespace="my-org"),
        )

        clustrix.configure(cluster_type="local", default_cores=4)
        ModernClustrixWidget(profile_manager=PM(config_dir=store))

        reopened = PM(config_dir=store).load_profile("Current configuration")
        assert reopened.hf_namespace == "my-org"
        assert reopened.cluster_type == "huggingface"

    def test_a_token_lives_for_the_session_but_never_reaches_disk(self, tmp_path):
        """The deliberate consequence of the rule above, pinned both ways.

        Dropping the secret would be a silent surprise if the token stopped
        working immediately, so this checks the trade is what was intended:
        usable for the whole session, absent from the file.
        """
        from clustrix.profile_manager import ProfileManager as PM

        store = tmp_path / "store"
        manager = PM(config_dir=str(store))
        manager.save_profile(
            "Current configuration",
            ClusterConfig(cluster_type="huggingface", hf_token="MYTOKEN"),
        )

        assert manager.load_profile("Current configuration").hf_token == "MYTOKEN"

        on_disk = (store / PM.STORE_FILENAME).read_text(encoding="utf-8")
        assert "MYTOKEN" not in on_disk
        assert (
            PM(config_dir=str(store)).load_profile("Current configuration").hf_token
            is None
        )


class TestTheStoreSurvivesBadInput:
    def test_a_corrupt_profile_does_not_wipe_the_built_ins(self, tmp_path):
        """Clearing then repopulating meant one bad entry left a partial set
        with the templates gone and active_profile naming nothing."""
        import warnings

        from clustrix.profile_manager import ProfileManager as PM

        store = tmp_path / "store"
        store.mkdir()
        (store / "profiles.yml").write_text(
            "active_profile: A\nprofiles:\n  A: {cluster_type: ssh}\n  B: {nope: 1}\n"
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            manager = PM(config_dir=str(store))

        assert set(manager.get_profile_names()) >= set(PM.BUILTIN_PROFILES)
        assert manager.active_profile in manager.get_profile_names()
        assert caught, "a store that could not be read should say so"

    def test_active_profile_always_names_something_that_exists(self, tmp_path):
        from clustrix.profile_manager import ProfileManager as PM

        store = tmp_path / "store"
        store.mkdir()
        (store / "profiles.yml").write_text(
            "active_profile: Gone\nprofiles:\n  A: {cluster_type: ssh}\n"
        )

        manager = PM(config_dir=str(store))
        assert manager.get_active_profile() is not None

    def test_an_unwritable_config_directory_does_not_stop_the_widget(self, tmp_path):
        import warnings

        from clustrix.profile_manager import ProfileManager as PM

        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory")

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            manager = PM(config_dir=str(blocked / "profiles"))

        assert manager.get_profile_names()

    def test_a_cloned_profile_survives_a_restart(self, tmp_path):
        """The + button's output was held in memory only."""
        from clustrix.profile_manager import ProfileManager as PM

        store = str(tmp_path / "store")
        widget = ModernClustrixWidget(profile_manager=PM(config_dir=store))
        widget._on_add_profile(widget.widgets["add_profile_btn"])
        clone = widget.widgets["profile_dropdown"].value

        assert clone in PM(config_dir=store).get_profile_names()


class TestPasswordAuthenticationIsReachable:
    def test_no_placeholder_key_file_is_written(self):
        """`~/.ssh/id_rsa` was pre-filled as a real value, and the connection
        code tries key_file first, so a typed password was never used."""
        import clustrix

        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "ssh"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "a.edu"
        widget.widgets["username"].value = "alice"
        widget.widgets["password"].value = "s3cret"

        widget._on_apply_config(widget.widgets["apply_btn"])

        assert clustrix.get_config().key_file is None
        assert clustrix.get_config().password == "s3cret"


class TestGlancingAtAnotherBackend:
    def test_it_does_not_erase_the_profile_being_left(self, tmp_path):
        """Collection was gated on the current cluster type, so flipping the
        menu before switching profiles dropped the remote settings."""
        from clustrix.profile_manager import ProfileManager as PM

        manager = PM(config_dir=str(tmp_path / "store"))
        manager.save_profile(
            "myssh",
            ClusterConfig(
                cluster_type="ssh",
                cluster_host="h.edu",
                username="bob",
                remote_work_dir="/scratch/bob",
            ),
        )
        manager.save_profile("other", ClusterConfig(cluster_type="local"))
        widget = ModernClustrixWidget(profile_manager=manager)

        widget.widgets["profile_dropdown"].value = "myssh"
        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        widget.widgets["profile_dropdown"].value = "other"

        kept = manager.load_profile("myssh")
        assert (kept.cluster_host, kept.username) == ("h.edu", "bob")
        assert kept.remote_work_dir == "/scratch/bob"

    def test_apply_does_not_send_a_local_job_to_a_cluster(self):
        """_choose_execution_mode routes on cluster_host, so a leftover host
        would make a 'local' configuration run remotely."""
        import clustrix

        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "ssh"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "login.example.edu"
        widget.widgets["username"].value = "alice"
        widget._on_apply_config(widget.widgets["apply_btn"])

        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()
        widget._on_apply_config(widget.widgets["apply_btn"])

        assert not clustrix.get_config().cluster_host
