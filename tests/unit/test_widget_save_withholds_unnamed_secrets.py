#!/usr/bin/env python3
"""What the widget writes may not depend on how a secret is spelled.

``strip_secret_fields`` used to answer "may this reach disk?" with a name
test: a precomputed set derived from ``fields(ClusterConfig)`` for whole
fields, and a regular expression over key names for the entries inside
``environment_variables``. The widget does not hand it ``ClusterConfig``
fields, though -- ``self.configs`` holds whatever a previously saved file
contained (see ``EnhancedClusterConfigWidget._initialize_configs``), so any
key at all can arrive. The result:

* at the top level, ``aws_secret_access_key``, ``client_secret``,
  ``private_key``, ``token``, ``secret_key`` and ``PASSWORD`` all reached
  disk verbatim, because none of them is a ``ClusterConfig`` field and the
  name set only ever contained field names;
* inside ``environment_variables``, ``SSH_PASSPHRASE``, ``GITHUB_PAT`` and
  a ``DATABASE_URL`` with the password in the URL were not recognised at
  all, and ``USE_PASSWORD`` was actively *exempted* by the ``^use_`` rule
  that exists to describe the boolean field ``use_env_password``.

So this module never names a secret to the code under test. It plants
sentinel *values* -- strings that appear nowhere else in the project -- and
then reads every byte the widget wrote looking for any of them. A guard
that recognises a key by name cannot pass this by adding a spelling,
because no spelling is being checked.
"""

import io
from contextlib import redirect_stdout

import pytest
import yaml

from clustrix.config import get_config_dir

pytest.importorskip("ipywidgets")

from clustrix.notebook_magic_widget import (  # noqa: E402
    EnhancedClusterConfigWidget,
)


#: One sentinel per hiding place. The values are assembled from parts so
#: that no credential-shaped literal appears in the file -- the shape
#: ``tests/unit/test_check_for_secrets.py`` flags -- and are distinctive
#: enough that finding one in a config file can only mean it was written.
def _sentinel(slot):
    return "-".join(["clustrix", "sentinel", slot, "value"])


#: Top-level keys that are *not* ``ClusterConfig`` fields, which is exactly
#: why the old name set could never have caught them.
UNKNOWN_TOP_LEVEL_SECRETS = {
    "aws_secret_access_key": _sentinel("aws"),
    "client_secret": _sentinel("clientsecret"),
    "private_key": _sentinel("privatekey"),
    "token": _sentinel("token"),
    "secret_key": _sentinel("secretkey"),
    "PASSWORD": _sentinel("shoutypassword"),
    # Not secret-looking by any rule, and still a credential.
    "legacy_auth_blob": _sentinel("blob"),
}

#: Environment variable names the pattern does not match -- plus the one it
#: matched and then exempted.
UNKNOWN_ENVIRONMENT_SECRETS = {
    "SSH_PASSPHRASE": _sentinel("passphrase"),
    "GITHUB_PAT": _sentinel("pat"),
    "DATABASE_URL": f"postgres://u:{_sentinel('dburl')}@db.example.edu/app",
    "USE_PASSWORD": _sentinel("usepassword"),
}

ALL_SENTINELS = {**UNKNOWN_TOP_LEVEL_SECRETS, **UNKNOWN_ENVIRONMENT_SECRETS}


#: What a config file the widget found on disk can look like.
#: ``_initialize_configs`` stores the parsed mapping unchanged, so the next
#: save round-trips whatever was in it -- which is how keys that are not
#: ``ClusterConfig`` fields reach ``strip_secret_fields`` at all.
def _config_as_loaded_from_disk(name="restored"):
    return {
        "name": name,
        "cluster_type": "ssh",
        "cluster_host": "cluster.example.edu",
        "username": "researcher",
        "default_cores": 4,
        "environment_variables": {
            "OMP_NUM_THREADS": "4",
            **UNKNOWN_ENVIRONMENT_SECRETS,
        },
        **UNKNOWN_TOP_LEVEL_SECRETS,
    }


def _widget_showing_a_loaded_file():
    """One loaded configuration, displayed -- the single-file save branch.

    ``_load_config_to_widgets`` is called because that is what selecting a
    configuration does, and it is what puts the loaded
    ``environment_variables`` into the free-text field the save then reads
    back. Without it the widget would be saving its own defaults.
    """
    widget = EnhancedClusterConfigWidget()
    widget.configs = {"restored": _config_as_loaded_from_disk()}
    widget._load_config_to_widgets("restored")
    return widget


def _widget_holding_a_loaded_file():
    """A loaded configuration that is *not* the one on screen.

    The multi-configuration branch writes every entry in the dropdown into
    one file, and only the selected entry is refreshed from the widget
    fields. So this is the path on which a loaded mapping reaches disk with
    every key it arrived with, unknown ones included.
    """
    widget = EnhancedClusterConfigWidget()
    widget.configs = {
        "restored": _config_as_loaded_from_disk(),
        "on-screen": _config_as_loaded_from_disk("on-screen"),
    }
    widget._load_config_to_widgets("on-screen")
    return widget


def _save(widget, filename):
    widget.save_filename_input.value = filename
    captured = io.StringIO()
    with redirect_stdout(captured):
        widget._on_save_config(None)
    return captured.getvalue()


def _written_bytes(filename):
    path = get_config_dir() / filename
    assert path.exists(), f"the widget wrote nothing to {path}"
    return path.read_text(encoding="utf-8")


def _sentinels_in(text):
    return sorted(slot for slot, value in ALL_SENTINELS.items() if value in text)


class TestNoSentinelReachesDisk:
    def test_the_single_configuration_branch_writes_no_sentinel(self):
        widget = _widget_showing_a_loaded_file()

        _save(widget, "restored-single.yml")

        leaked = _sentinels_in(_written_bytes("restored-single.yml"))
        assert not leaked, f"values planted under {leaked} were written to disk"

    def test_the_multi_configuration_branch_writes_no_sentinel(self):
        """One file, every configuration in the dropdown: N credentials."""
        widget = _widget_holding_a_loaded_file()

        _save(widget, "restored-many.yml")

        leaked = _sentinels_in(_written_bytes("restored-many.yml"))
        assert not leaked, f"values planted under {leaked} were written to disk"

    @pytest.mark.parametrize(
        "build, key",
        [
            (_widget_showing_a_loaded_file, "restored"),
            (_widget_holding_a_loaded_file, "restored"),
        ],
    )
    def test_the_sentinels_are_actually_in_what_was_offered(self, build, key):
        """A test that planted nothing would pass forever.

        If the fixtures stopped carrying the sentinels -- a renamed
        attribute, a swallowed exception -- the assertions above would go
        green while proving nothing. The single-file branch only carries
        the environment ones, because the widget rebuilds the selected
        configuration from its own fields and those have no home for an
        ``aws_secret_access_key``; the whole set has to survive on the
        branch that writes a loaded mapping through untouched.
        """
        widget = build()

        offered = yaml.safe_dump(widget.configs[key])
        offered += yaml.safe_dump(widget._save_config_from_widgets())

        assert set(_sentinels_in(offered)) >= set(UNKNOWN_ENVIRONMENT_SECRETS)


class TestWhatSurvives:
    def test_the_ordinary_settings_still_round_trip(self):
        """Withholding must not become a general loss of the user's work."""
        widget = _widget_showing_a_loaded_file()

        _save(widget, "restored-ordinary.yml")

        saved = yaml.safe_load(_written_bytes("restored-ordinary.yml"))
        assert saved["cluster_type"] == "ssh"
        assert saved["cluster_host"] == "cluster.example.edu"
        assert saved["username"] == "researcher"
        assert saved["default_cores"] == 4
        # The widget's own label for the configuration, which it reads back.
        assert saved["name"] == "restored"

    def test_a_key_the_file_format_does_not_define_is_not_written(self):
        """Not only the secret-looking ones.

        The rule is an allowlist, so a key that is not part of the file
        format is absent whether or not anyone thought it was dangerous.
        ``ClusterConfig.load_from_file`` does ``cls(**config_data)`` and
        ``ProfileManager.load_from_file`` rejects unknown settings outright,
        so such a key could never have been read back anyway.
        """
        widget = _widget_holding_a_loaded_file()
        widget.configs["restored"]["some_future_setting"] = "not a secret at all"

        _save(widget, "restored-unknown.yml")

        saved = yaml.safe_load(_written_bytes("restored-unknown.yml"))["restored"]
        assert "some_future_setting" not in saved
        assert "legacy_auth_blob" not in saved


class TestWhatTheUserIsTold:
    def test_every_withheld_key_is_named(self):
        """Silently dropping what the user had is its own surprise."""
        widget = _widget_holding_a_loaded_file()

        output = _save(widget, "restored-announced.yml")

        for withheld in ("environment_variables", "token", "client_secret"):
            assert withheld in output, f"{withheld} was dropped without saying so"
        assert "not a credential store" in output

    def test_the_environment_variable_advice_is_given(self):
        """The user needs somewhere else to put them, not just a refusal."""
        widget = _widget_showing_a_loaded_file()

        output = _save(widget, "restored-advice.yml")

        assert "environment_variables" in output
        assert "shell" in output
