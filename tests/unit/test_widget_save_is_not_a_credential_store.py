"""Nothing the widget writes may contain a secret, however it is spelled.

Two files are written from the form. ``Save all`` writes a bundle wherever
the user says; and, without any button being pressed, every edit is kept in
the widget's own store under ``~/.clustrix/profiles`` so that profiles
survive the session. A password typed to test a connection must reach
neither. ``password_env_var`` is the supported way to supply a password
without writing it down, so there is no opt-in here.

``tests/unit/test_persisted_files_are_private.py`` is the guarantee for the
tree as a whole -- it walks every file clustrix writes and checks modes and
contents. This module is the widget's half, driven through its real controls
and handlers.

The tests never name a secret to the code under test. They plant sentinel
*values* -- strings that appear nowhere else in the project -- in every place
the form will take one, and then read every byte the widget wrote looking for
any of them. A guard that recognises a key by its name cannot pass this by
learning a new spelling, because no spelling is being checked: the widget
this replaced filtered ``environment_variables`` entry by entry on key names,
and ``SSH_PASSPHRASE``, ``GITHUB_PAT``, a ``DATABASE_URL`` with the password
inside it and ``USE_PASSWORD`` all reached disk.
"""

import pytest
import yaml

pytest.importorskip("ipywidgets")

from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402
from tests.widget_console import pill, press  # noqa: E402


#: Assembled from parts so that no credential-shaped literal appears in the
#: file -- the shape ``tests/unit/test_check_for_secrets.py`` flags -- and
#: distinctive enough that finding one on disk can only mean it was written.
def _sentinel(slot):
    return "-".join(["clustrix", "sentinel", slot, "value"])


#: Environment variable names no pattern over key names would catch, a value
#: carrying its secret where no key name can see it, and one a ``^use_`` rule
#: would have exempted.
SENTINEL_ENVIRONMENT = {
    "SSH_PASSPHRASE": _sentinel("passphrase"),
    "GITHUB_PAT": _sentinel("pat"),
    "DATABASE_URL": f"postgres://u:{_sentinel('dburl')}@db.example.edu/app",
    "USE_PASSWORD": _sentinel("usepassword"),
}

SENTINELS = {
    "password": _sentinel("password"),
    "hf_token": _sentinel("hftoken"),
    **SENTINEL_ENVIRONMENT,
}


def _sentinels_in(text):
    return sorted(slot for slot, value in SENTINELS.items() if value in text)


def _widget_holding_every_secret():
    """Two profiles, each with every credential the form accepts typed in.

    Two, because one file carries every profile: the one on screen is
    rebuilt from the controls when Save is pressed, and the other is written
    from what was captured when the user switched away from it. Both paths
    have to withhold.
    """
    widget = ModernClustrixWidget()
    controls = widget.widgets

    controls["profile_dropdown"].value = "SSH remote machine"
    controls["host"].value = "cluster.example.edu"
    controls["username"].value = "researcher"
    controls["cpus"].value = 6
    controls["home_dir"].value = "/scratch/researcher/clustrix"
    controls["password"].value = SENTINELS["password"]
    controls["local_env_var"].value = "MY_CLUSTER_PASSWORD"
    controls["env_vars"].value = "OMP_NUM_THREADS=4\n" + "\n".join(
        f"{key}={value}" for key, value in SENTINEL_ENVIRONMENT.items()
    )

    # Switching away is what captures the profile above into the store.
    controls["profile_dropdown"].value = "HuggingFace Jobs (CPU)"
    controls["hf_namespace"].value = "contextlab"
    controls["hf_token"].value = SENTINELS["hf_token"]
    controls["env_vars"].value = f"HF_HUB_TOKEN_COPY={SENTINELS['GITHUB_PAT']}"
    return widget


def _save(widget, target, capsys):
    widget.widgets["config_filename"].value = str(target)
    return press(
        lambda: widget._on_save_config(widget.widgets["save_btn"]),
        widget.widgets["output"],
        capsys,
    )


class TestTheSentinelsWerePlanted:
    def test_every_sentinel_is_in_what_the_session_holds(self):
        """A test that planted nothing would pass forever.

        If the fixture stopped carrying the sentinels -- a renamed control, a
        swallowed exception -- the assertions below would go green while
        proving nothing.
        """
        widget = _widget_holding_every_secret()
        manager = widget.profile_manager

        ssh = manager.load_profile("SSH remote machine")
        huggingface = widget._get_config_from_widgets()

        assert ssh.password == SENTINELS["password"]
        assert ssh.environment_variables == {
            "OMP_NUM_THREADS": "4",
            **SENTINEL_ENVIRONMENT,
        }
        assert huggingface.hf_token == SENTINELS["hf_token"]
        held = yaml.safe_dump([ssh.__dict__, huggingface.__dict__], default_style='"')
        assert _sentinels_in(held) == sorted(SENTINELS)


class TestNoSentinelReachesDisk:
    def test_save_all_writes_no_sentinel(self, tmp_path, capsys):
        widget = _widget_holding_every_secret()
        target = tmp_path / "bundle.yml"

        _save(widget, target, capsys)

        assert pill(widget) == "saved"
        leaked = _sentinels_in(target.read_text(encoding="utf-8"))
        assert not leaked, f"values planted under {leaked} were written to {target}"

    def test_the_widgets_own_store_holds_no_sentinel(self):
        """No button at all: the store is rewritten on every captured edit."""
        widget = _widget_holding_every_secret()
        # Capture the profile on screen too, the way leaving it does.
        widget.widgets["profile_dropdown"].value = "Local single-core"

        store = widget.profile_manager.store_path
        assert store.exists(), f"the widget kept nothing at {store}"
        leaked = _sentinels_in(store.read_text(encoding="utf-8"))
        assert not leaked, f"values planted under {leaked} were written to {store}"

    def test_a_json_bundle_writes_no_sentinel_either(self, tmp_path, capsys):
        """The format is chosen by the extension, and is a second writer."""
        widget = _widget_holding_every_secret()
        target = tmp_path / "bundle.json"

        _save(widget, target, capsys)

        assert pill(widget) == "saved"
        assert not _sentinels_in(target.read_text(encoding="utf-8"))


class TestWhatSurvives:
    def test_the_ordinary_settings_still_round_trip(self, tmp_path, capsys):
        """Withholding must not become a general loss of the user's work."""
        widget = _widget_holding_every_secret()
        target = tmp_path / "bundle.yml"

        _save(widget, target, capsys)

        saved = yaml.safe_load(target.read_text(encoding="utf-8"))["profiles"]
        ssh = saved["SSH remote machine"]
        assert ssh["cluster_type"] == "ssh"
        assert ssh["cluster_host"] == "cluster.example.edu"
        assert ssh["username"] == "researcher"
        assert ssh["default_cores"] == 6
        assert ssh["remote_work_dir"] == "/scratch/researcher/clustrix"
        assert saved["HuggingFace Jobs (CPU)"]["hf_namespace"] == "contextlab"

    def test_the_supported_channel_for_a_password_is_written(self, tmp_path, capsys):
        """The *name* of the variable is the setting; its contents never are."""
        widget = _widget_holding_every_secret()
        target = tmp_path / "bundle.yml"

        _save(widget, target, capsys)

        ssh = yaml.safe_load(target.read_text(encoding="utf-8"))["profiles"][
            "SSH remote machine"
        ]
        assert ssh["password_env_var"] == "MY_CLUSTER_PASSWORD"
        assert ssh["use_env_password"] is True

    def test_the_secrets_are_still_usable_in_the_session(self, tmp_path, capsys):
        """Withheld from disk is not the same as thrown away."""
        widget = _widget_holding_every_secret()

        _save(widget, tmp_path / "bundle.yml", capsys)

        assert widget.widgets["hf_token"].value == SENTINELS["hf_token"]
        widget.widgets["profile_dropdown"].value = "SSH remote machine"
        assert widget.widgets["password"].value == SENTINELS["password"]
        assert SENTINEL_ENVIRONMENT["SSH_PASSPHRASE"] in (
            widget.widgets["env_vars"].value
        )


class TestWhatTheUserIsTold:
    def test_saving_says_that_credentials_were_left_out(self, tmp_path, capsys):
        """Silently dropping what the user typed is its own surprise."""
        widget = _widget_holding_every_secret()

        told = _save(widget, tmp_path / "announced.yml", capsys)

        assert "Saved" in told
        assert "Passwords and tokens are never written to this file" in told

    def test_no_sentinel_is_echoed_while_saying_so(self, tmp_path, capsys):
        widget = _widget_holding_every_secret()

        told = _save(widget, tmp_path / "quiet.yml", capsys)

        assert not _sentinels_in(told), told
