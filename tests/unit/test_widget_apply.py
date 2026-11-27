"""Pressing Apply must actually change the configuration (issue #165).

The widget built a dict and splatted it into ``configure()``, which rejects
any keyword that is not a ``ClusterConfig`` field, on purpose: a silently
ignored setting is worse than a rejected one. One stray key and the widget's
main button raised ``ValueError: Unknown configuration parameter`` before
applying anything, caught it, and printed a red cross. The primary interface
for anyone not writing scripts did nothing.

It survived because no test ever pressed the button. Widget tests that build
a widget and read its attributes back cannot see this: the defect is entirely
in what happens between the controls and the live configuration. Every test
here drives the real handler on a real widget and then asserts on
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
from clustrix.modern_notebook_widget import (  # noqa: E402
    WIDGET_MANAGED_FIELDS,
    ModernClustrixWidget,
)
from clustrix.profile_manager import ProfileManager  # noqa: E402
from clustrix.widget_controls import set_choice  # noqa: E402
from tests.widget_console import applied, pill, press  # noqa: E402


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


class TestApplyReachesTheLiveConfiguration:
    """The definition of done: press the button, read get_config()."""

    def test_apply_makes_the_displayed_settings_live(self, capsys):
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "slurm"
        widget._update_ui_for_cluster_type()
        widget.widgets["host"].value = "hpc.example.edu"
        widget.widgets["username"].value = "researcher"
        widget.widgets["cpus"].value = 12
        widget.widgets["ram"].value = "64GB"
        widget.widgets["time"].value = "04:00:00"
        widget.widgets["home_dir"].value = "/scratch/researcher/clustrix"

        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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

    def test_a_managed_field_that_has_drifted_is_named(self, capsys, monkeypatch):
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

        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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


class TestTheSummarySaysWhatWasApplied:
    def test_the_summary_does_not_print_a_host_it_did_not_apply(self, capsys):
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
        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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
    backend says nothing about that variable, and an Apply on a
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
        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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
        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
        assert get_config().cluster_host is None
        assert widget.widgets["host"].value == "hpc.example.edu"
        assert widget.widgets["username"].value == "researcher"
        assert widget.widgets["ssh_key_file"].value == "~/.ssh/id_ed25519"

    def test_a_local_apply_keeps_the_password_env_var(self, capsys):
        configure(password_env_var="MY_CLUSTER_PW", use_env_password=True)
        widget = ModernClustrixWidget()
        widget.widgets["cluster_type"].value = "local"
        widget._update_ui_for_cluster_type()

        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
        live = get_config()
        assert live.cluster_type == "local"
        assert live.password_env_var == "MY_CLUSTER_PW"
        assert live.use_env_password is True
        # The name of the variable is not a secret, but nothing about this
        # change may start printing values either.
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

        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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
        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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

        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )

        assert "Applied." in told, told
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
    -- any string is accepted -- while the widget offers ten flavors
    and four package managers in ``Dropdown``s. Assigning an unlisted value to
    a ``Dropdown`` raises ``TraitError: Invalid selection``, and both
    assignments happen in ``_load_config_to_widgets``, which the constructor
    calls. So a user who configured a flavor this build has not heard of could
    not open the widget at all -- not a degraded panel, an exception.

    ``set_choice`` widens the options instead of discarding the value.
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
        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )
        assert "Applied." in told, told
        assert get_config().hf_flavor == "a10g-large"

    def test_a_package_manager_the_dropdown_never_heard_of_opens(self, capsys):
        configure(cluster_type="local", package_manager="mamba")

        widget = ModernClustrixWidget()

        assert widget.widgets["package_manager"].value == "mamba"
        told = press(
            lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
            widget.widgets["output"],
            capsys,
        )
        assert "Applied." in told, told
        assert get_config().package_manager == "mamba"

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


def _apply(widget, capsys):
    return press(
        lambda: widget._on_apply_config(widget.widgets["apply_btn"]),
        widget.widgets["output"],
        capsys,
    )


def _widget_showing(**profile):
    """A widget whose form shows one saved profile, selected the real way."""
    widget = ModernClustrixWidget()
    widget.profile_manager.save_profile("Under test", ClusterConfig(**profile))
    widget._update_profile_dropdown()
    widget.widgets["profile_dropdown"].value = "Under test"
    return widget


class TestClearingAControlClearsTheSetting:
    """A field the user emptied has to reach ``configure()`` as "unset".

    An empty box must not overwrite a real setting with ``""`` -- which is
    right, and which also meant a box the user *cleared* said nothing at all,
    so the previously applied value stayed live. (Switching to a ``local``
    profile, the other half of the same defect, is held by
    ``test_widget_profiles.TestApplyTouchesOnlyWhatItOwns``.)
    """

    def test_emptying_a_box_in_the_profile_being_edited_clears_it(self, capsys):
        """The stored profile still holds the key file the user just deleted
        from the box, and the box is what the user is looking at."""
        widget = _widget_showing(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            key_file="~/.ssh/id_ed25519",
            password_env_var="HPC_PASSWORD",
            use_env_password=True,
        )
        _apply(widget, capsys)
        assert get_config().key_file == "~/.ssh/id_ed25519"
        assert get_config().use_env_password is True

        widget.widgets["ssh_key_file"].value = ""
        widget.widgets["local_env_var"].value = ""
        told = _apply(widget, capsys)

        assert "Applied." in told, told
        live = get_config()
        assert live.key_file is None
        assert live.password_env_var == ""
        assert live.use_env_password is False
        assert live.cluster_host == "hpc.example.edu"
        assert live.username == "researcher"

    def test_a_cleared_list_box_clears_to_an_empty_list_not_to_none(self, capsys):
        """The cleared value has to be the field's real default, not ``None``.

        ``module_loads``, ``environment_variables`` and
        ``pre_execution_commands`` are the three controls every consumer
        iterates. ``None`` looks like a clear and passes every assertion
        phrased as "not the old value", while leaving a configuration that
        raises ``TypeError: 'NoneType' object is not iterable`` the first
        time a job script is built.
        """
        widget = _widget_showing(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            module_loads=["python/3.11", "cuda/12.1"],
            environment_variables={"OMP_NUM_THREADS": "4"},
            pre_execution_commands=["source activate env"],
        )
        assert widget.widgets["modules"].value == "python/3.11 cuda/12.1"
        _apply(widget, capsys)
        assert get_config().module_loads == ["python/3.11", "cuda/12.1"]
        assert get_config().environment_variables == {"OMP_NUM_THREADS": "4"}
        assert get_config().pre_execution_commands == ["source activate env"]

        widget.widgets["modules"].value = ""
        widget.widgets["env_vars"].value = ""
        widget.widgets["pre_exec_commands"].value = ""
        told = _apply(widget, capsys)

        assert "Applied." in told, told
        live = get_config()
        assert live.module_loads == []
        assert live.environment_variables == {}
        assert live.pre_execution_commands == []
        # Said the way the code that breaks says it.
        assert [f"module load {name}" for name in live.module_loads] == []
        assert sorted(live.environment_variables.items()) == []


#: A profile's settings the form has no control for. Real ``ClusterConfig``
#: fields, each of which changes what a job does.
SETTINGS_WITH_NO_CONTROL = {
    "stage_warn_bytes": 4096,
    "job_poll_interval": 5,
    "venv_setup_timeout": 900,
}


def _uncontrolled(config):
    return {name: getattr(config, name) for name in SETTINGS_WITH_NO_CONTROL}


class TestAProfileSettingWithNoControlIsCarried:
    """A profile is more than the form can show.

    ``ClusterConfig`` has far more fields than the widget has controls. A
    profile holding one can only have come from a bundle the user imported
    or from ``configure()`` before the widget opened, and in both cases it
    is a setting they made. The widget this one replaced carried such fields
    through Apply and kept them in the profile.

    Looking at the profile, switching away from it and renaming it are safe
    now that editing does not write. Apply and Duplicate still rebuild the
    profile from the controls alone.
    """

    REASON = (
        "PRODUCT GAP, not a test problem. Apply and Duplicate build the "
        "profile from the controls alone (_get_config_from_widgets -> "
        "ClusterConfig(**widget fields)), so a profile setting with no "
        "control -- stage_warn_bytes, job_poll_interval, venv_setup_timeout, "
        "ssh_host_key_policy, hf_image, ... -- is reset to its default in "
        "the saved profile the first time it is applied or duplicated, and "
        "Apply never forwards it to configure() in the first place. The "
        "deleted widget carried these. Remove this marker when the profile "
        "is updated from the controls instead of replaced by them."
    )

    def _widget(self):
        return _widget_showing(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            **SETTINGS_WITH_NO_CONTROL,
        )

    @pytest.mark.xfail(strict=True, reason=REASON)
    def test_it_reaches_the_live_configuration(self, capsys):
        widget = self._widget()

        told = _apply(widget, capsys)

        assert "Applied." in told, told
        assert get_config().cluster_host == "hpc.example.edu"
        assert _uncontrolled(get_config()) == SETTINGS_WITH_NO_CONTROL

    @pytest.mark.xfail(strict=True, reason=REASON)
    def test_it_is_still_in_the_profile_after_apply(self, capsys):
        widget = self._widget()

        _apply(widget, capsys)

        kept = widget.profile_manager.load_profile("Under test")
        assert _uncontrolled(kept) == SETTINGS_WITH_NO_CONTROL

    @pytest.mark.xfail(strict=True, reason=REASON)
    def test_a_duplicate_has_it_too(self):
        widget = self._widget()

        widget._on_add_profile(widget.widgets["add_profile_btn"])

        copied = widget.profile_manager.load_profile(
            widget.widgets["profile_dropdown"].value
        )
        assert copied.cluster_host == "hpc.example.edu"
        assert _uncontrolled(copied) == SETTINGS_WITH_NO_CONTROL

    def test_it_survives_looking_at_another_profile(self):
        """No button at all: select another profile and come back."""
        widget = self._widget()

        widget.widgets["host"].value = "edited.example.edu"
        widget.widgets["profile_dropdown"].value = "Local single-core"
        widget.widgets["profile_dropdown"].value = "Under test"

        kept = widget.profile_manager.load_profile("Under test")
        assert _uncontrolled(kept) == SETTINGS_WITH_NO_CONTROL

    def test_it_survives_renaming_the_profile(self):
        widget = self._widget()

        widget.widgets["host"].value = "edited.example.edu"
        widget.widgets["profile_name"].value = "Renamed"

        kept = widget.profile_manager.load_profile("Renamed")
        assert _uncontrolled(kept) == SETTINGS_WITH_NO_CONTROL

    def test_the_fixture_really_holds_the_settings(self):
        """Or the xfails above would be failing on a profile that never had
        them, and the marker would be hiding a broken test."""
        widget = self._widget()

        held = widget.profile_manager.load_profile("Under test")
        assert _uncontrolled(held) == SETTINGS_WITH_NO_CONTROL
        assert _uncontrolled(ClusterConfig()) != SETTINGS_WITH_NO_CONTROL
        assert not set(SETTINGS_WITH_NO_CONTROL) & set(WIDGET_MANAGED_FIELDS)
        assert widget.widgets["host"].value == "hpc.example.edu"


class TestTheControlsAddedForSettingsThatHadNone:
    """Partition, the HuggingFace time limit, an existing conda environment
    and cleanup-on-success each got a control; each has to reach
    ``configure()``, or it is a box that does nothing."""

    def test_they_reach_the_live_configuration(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "slurm"
        controls["host"].value = "hpc.example.edu"
        controls["username"].value = "researcher"
        controls["partition"].value = "gpu-long"
        controls["conda_env"].value = "analysis"
        controls["cleanup"].value = False

        told = _apply(widget, capsys)

        assert "Applied." in told, told
        live = get_config()
        assert live.default_partition == "gpu-long"
        assert live.conda_env_name == "analysis"
        assert live.cleanup_on_success is False

    def test_the_huggingface_time_limit_reaches_the_live_configuration(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "huggingface"
        controls["hf_namespace"].value = "contextlab"
        controls["hf_timeout"].value = "45m"

        _apply(widget, capsys)

        assert get_config().hf_job_timeout == "45m"

    def test_a_partition_does_not_follow_the_form_to_another_backend(self, capsys):
        """A partition names a queue on one scheduler. Applied to plain SSH
        or to this machine it describes somewhere the job is not going."""
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "slurm"
        controls["host"].value = "hpc.example.edu"
        controls["username"].value = "researcher"
        controls["partition"].value = "gpu-long"
        _apply(widget, capsys)
        assert get_config().default_partition == "gpu-long"

        controls["cluster_type"].value = "ssh"
        _apply(widget, capsys)

        assert get_config().default_partition is None
        assert get_config().cluster_host == "hpc.example.edu"
        # Still on the form, for when the user switches back.
        assert controls["partition"].value == "gpu-long"

    def test_clearing_them_clears_the_live_setting(self, capsys):
        configure(default_partition="debug", conda_env_name="old")
        widget = _widget_showing(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            default_partition="debug",
            conda_env_name="old",
        )

        widget.widgets["partition"].value = ""
        widget.widgets["conda_env"].value = "   "
        _apply(widget, capsys)

        assert get_config().default_partition is None
        assert get_config().conda_env_name is None


class TestWhatApplySaysAndShows:
    def test_the_result_says_applied_and_the_header_says_in_use(self, capsys):
        widget = ModernClustrixWidget()
        assert applied(widget) == "not applied"

        told = _apply(widget, capsys)

        assert pill(widget) == "applied"
        assert applied(widget) == "in use"
        assert "Saved to the profile 'Local single-core'." in told

    def test_a_host_typed_as_user_at_host_is_split_before_it_is_applied(self, capsys):
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "ssh"

        controls["host"].value = "  alice@login.example.edu "
        assert controls["host"].value == "login.example.edu"
        assert controls["username"].value == "alice"
        _apply(widget, capsys)

        live = get_config()
        assert (live.username, live.cluster_host) == ("alice", "login.example.edu")

    def test_surrounding_whitespace_is_not_part_of_a_setting(self, capsys):
        """A host pasted with a trailing space is the same host; unstripped
        it fails as "Name or service not known", which does not say why."""
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "slurm"
        controls["host"].value = " hpc.example.edu "
        controls["username"].value = "researcher\t"
        controls["ssh_key_file"].value = " ~/.ssh/id_hpc "
        controls["ram"].value = " 32GB "
        controls["time"].value = " 02:00:00 "
        controls["python_executable"].value = " python3 "

        told = _apply(widget, capsys)

        assert "Applied." in told, told
        live = get_config()
        assert live.cluster_host == "hpc.example.edu"
        assert live.username == "researcher"
        assert live.key_file == "~/.ssh/id_hpc"
        assert live.default_memory == "32GB"
        assert live.default_time == "02:00:00"
        assert live.python_executable == "python3"

    def test_a_password_is_not_stripped(self, capsys):
        """A password may begin or end with a space; it is not a "setting"."""
        widget = ModernClustrixWidget()
        controls = widget.widgets
        controls["cluster_type"].value = "ssh"
        controls["host"].value = "h.example.edu"
        controls["username"].value = "u"
        controls["password"].value = " leading and trailing "

        assert widget._get_config_from_widgets().password == " leading and trailing "


class TestHowAProfileWidensTheMenu:
    """``set_choice``'s properties, where a user meets them: profiles."""

    def _widget_with_profiles(self, **package_managers):
        widget = ModernClustrixWidget()
        for name, manager in package_managers.items():
            widget.profile_manager.save_profile(
                name, ClusterConfig(cluster_type="local", package_manager=manager)
            )
        widget._update_profile_dropdown()
        return widget

    def test_showing_one_profile_three_times_adds_one_entry(self):
        """Selecting a profile reloads it every time, so this is the ordinary
        path. Without the guard the menu grew a fresh copy on every load."""
        widget = self._widget_with_profiles(**{"Mamba Box": "mamba"})
        menu = widget.widgets["package_manager"]
        baseline = list(menu.options)

        for _ in range(3):
            widget.widgets["profile_dropdown"].value = "Mamba Box"
            widget.widgets["profile_dropdown"].value = "Local single-core"
        widget.widgets["profile_dropdown"].value = "Mamba Box"

        assert list(menu.options) == baseline + ["mamba"]
        assert menu.value == "mamba"

    def test_a_profile_whose_package_manager_was_cleared_adds_no_blank_entry(self):
        widget = self._widget_with_profiles(Cleared="")
        menu = widget.widgets["package_manager"]
        baseline = list(menu.options)

        widget.widgets["profile_dropdown"].value = "Cleared"

        assert list(menu.options) == baseline
        assert menu.value in baseline

    def test_widening_accumulates_across_profile_switches_on_purpose(self):
        """Recorded as a decision, not left to be rediscovered.

        Three profiles with three unlisted package managers leave all three
        in the menu for the rest of the session. The alternative --
        rebuilding the menu from the hardcoded list on each load -- means
        switching back to the first profile raises the very ``TraitError``
        ``set_choice`` exists to prevent.
        """
        widget = self._widget_with_profiles(A="mamba", B="pixi", C="poetry")
        menu = widget.widgets["package_manager"]
        baseline = list(menu.options)

        for name in ("A", "B", "C"):
            widget.widgets["profile_dropdown"].value = name

        assert list(menu.options) == baseline + ["mamba", "pixi", "poetry"]

        # Going back is the point of keeping them.
        widget.widgets["profile_dropdown"].value = "A"
        assert menu.value == "mamba"
        assert widget._config_data_from_widgets()["package_manager"] == "mamba"
        # And leaving C did not rewrite it with what A or B left on screen.
        assert widget.profile_manager.load_profile("C").package_manager == "poetry"

    def test_the_widened_options_are_not_persisted(self, tmp_path):
        """The accumulation above is only acceptable because it dies with the
        widget. What is saved is the *value*; the widened list is not a
        setting and must not become one."""
        widget = self._widget_with_profiles(**{"Mamba Box": "mamba"})
        baseline = list(widget.widgets["package_manager"].options)
        widget.widgets["profile_dropdown"].value = "Mamba Box"
        assert "mamba" in widget.widgets["package_manager"].options

        target = tmp_path / "bundle.yml"
        widget.widgets["config_filename"].value = str(target)
        widget._on_save_config(widget.widgets["save_btn"])

        written = target.read_text(encoding="utf-8")
        assert "package_manager: mamba" in written
        for offered in baseline:
            if offered != "pip":  # pip is the default of every other profile
                assert f"- {offered}" not in written, written
        # A widget opened on another store offers the hardcoded list again.
        elsewhere = ModernClustrixWidget(ProfileManager(str(tmp_path / "elsewhere")))
        assert list(elsewhere.widgets["package_manager"].options) == baseline


class TestAProfileNamingARemovedBackend:
    """``cluster_type`` is exempt from ``set_choice``, and the reason is the
    inverse of the one that applies to every other dropdown.

    ``set_choice`` widens a menu because the saved configuration is
    authoritative: ``ClusterConfig`` accepts any string for ``hf_flavor`` or
    ``package_manager``, so a list baked into the UI has no standing to veto
    one. ``cluster_type`` is the single field with an *enforced domain* --
    ``ClusterConfig(cluster_type="pbs")`` raises ``ValueError`` naming issue
    #140 -- so here the selector is authoritative and the saved value is the
    thing that can be wrong. Widening would offer a backend the executor
    cannot dispatch and defer the failure to submission.

    The value can still reach the widget, in a bundle written by an older
    clustrix. Loading it has to say which backend, in which profile, and
    where it went -- not a bare ``TraitError`` out of a control.
    """

    BUNDLE = (
        "active_profile: Old PBS Cluster\n"
        "profiles:\n"
        "  Old PBS Cluster:\n"
        "    cluster_type: pbs\n"
        "    cluster_host: hpc.example.edu\n"
        "    username: researcher\n"
    )

    def _load(self, tmp_path, capsys):
        bundle = tmp_path / "old.yml"
        bundle.write_text(self.BUNDLE, encoding="utf-8")
        widget = ModernClustrixWidget()
        before = {
            "names": widget.profile_manager.get_profile_names(),
            "selected": widget.widgets["profile_dropdown"].value,
            "backend": widget.widgets["cluster_type"].value,
        }
        widget.widgets["config_filename"].value = str(bundle)
        told = press(
            lambda: widget._on_load_config(widget.widgets["load_btn"]),
            widget.widgets["output"],
            capsys,
        )
        return widget, before, told

    def test_loading_it_names_the_backend_and_its_tracking_issue(
        self, tmp_path, capsys
    ):
        widget, _, told = self._load(tmp_path, capsys)

        assert pill(widget) == "import failed"
        assert "Could not import" in told
        assert "pbs" in told
        assert "#140" in told
        assert "Old PBS Cluster" in told
        assert "local, ssh, slurm, huggingface" in told

    def test_it_does_not_widen_the_selector_or_half_load_the_bundle(
        self, tmp_path, capsys
    ):
        widget, before, _ = self._load(tmp_path, capsys)

        offered = [value for _, value in widget.widgets["cluster_type"].options]
        assert offered == list(SUPPORTED_CLUSTER_TYPES)
        assert widget.widgets["cluster_type"].value == before["backend"]
        # Load replaces every profile, so a refused bundle must leave every
        # profile that was there, and the form showing what it was showing.
        assert widget.profile_manager.get_profile_names() == before["names"]
        assert widget.widgets["profile_dropdown"].value == before["selected"]
        assert widget.widgets["host"].value != "hpc.example.edu"

    def test_the_selector_is_the_supported_tuple_itself(self):
        """Compared against the tuple, not a hardcoded copy of the four names:
        a copy pins the drift it was meant to catch."""
        widget = ModernClustrixWidget()

        assert [value for _, value in widget.widgets["cluster_type"].options] == list(
            SUPPORTED_CLUSTER_TYPES
        )
