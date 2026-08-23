"""Pressing "Save configuration" must not put a password on disk.

``tests/unit/test_persisted_files_are_private.py`` is the guarantee for the
file itself -- it walks the tree and checks modes and contents. This module
covers the half a tree walk cannot see: whether the user is *told* that the
password they typed was withheld, and told once rather than on every click.

The decision matches ``ProfileManager`` deliberately. A widget save fires
from ordinary editing rather than from anyone asking to persist a secret,
one file carries every configuration in the dropdown, and
``password_env_var`` is the supported way to supply a password without
writing it down -- so there is no ``include_secrets`` opt-in here either.

``<redacted>`` is the placeholder spelling
``tests/unit/test_check_for_secrets.py`` already suppresses.
"""

import io
from contextlib import redirect_stdout

import pytest
import yaml

from clustrix.config import get_config_dir

pytest.importorskip("ipywidgets")

from clustrix.notebook_magic_widget import (  # noqa: E402
    EnhancedClusterConfigWidget,
    _dropped_keys,
)


def _configured_widget():
    """A widget holding one SSH configuration with a password typed in."""
    widget = EnhancedClusterConfigWidget()
    widget.config_name.value = "with-credentials"
    widget.cluster_type.value = "ssh"
    widget.host_field.value = "cluster.example.edu"
    widget.username_field.value = "researcher"
    widget.password_field.value = "<redacted>"
    widget.current_config_name = "with-credentials"
    widget.configs = {"with-credentials": widget._save_config_from_widgets()}
    return widget


def _save(widget, filename):
    """Press the button, returning whatever the widget printed."""
    widget.save_filename_input.value = filename
    captured = io.StringIO()
    with redirect_stdout(captured):
        widget._on_save_config(None)
    return captured.getvalue()


def _saved(filename):
    return yaml.safe_load((get_config_dir() / filename).read_text(encoding="utf-8"))


class TestWhatReachesDisk:
    def test_the_password_is_not_written(self):
        widget = _configured_widget()

        _save(widget, "single.yml")

        saved = _saved("single.yml")
        assert saved["cluster_host"] == "cluster.example.edu"
        assert saved["username"] == "researcher"
        assert "password" not in saved

    def test_the_huggingface_token_is_not_written(self):
        widget = _configured_widget()
        widget.cluster_type.value = "huggingface"
        widget.hf_token_field.value = "<redacted>"
        widget.configs = {"hf": widget._save_config_from_widgets()}
        widget.current_config_name = "hf"

        _save(widget, "hf.yml")

        assert "hf_token" not in _saved("hf.yml")

    def test_the_multi_configuration_branch_is_redacted_too(self):
        """One file, every configuration in the dropdown -- N credentials."""
        widget = _configured_widget()
        second = dict(widget.configs["with-credentials"], name="second")
        widget.configs["second"] = second

        _save(widget, "many.yml")

        saved = _saved("many.yml")
        assert set(saved) == {"with-credentials", "second"}
        for name, entry in saved.items():
            assert "password" not in entry, name

    def test_environment_variables_are_withheld_whole(self):
        """The field name is innocuous and the entries cannot be judged.

        **Rewritten, not relaxed.** This used to assert that
        ``OMP_NUM_THREADS`` survived while ``AWS_SECRET_ACCESS_KEY`` was
        dropped -- i.e. that each entry is judged by its key name. Measured
        against user-chosen names that rule fails: ``SSH_PASSPHRASE`` and
        ``GITHUB_PAT`` match nothing, a ``DATABASE_URL`` carries its
        password where no key name can see it, and ``USE_PASSWORD`` was
        *exempted* by the ``^use_`` rule written for the boolean field
        ``use_env_password``. Both the names and the values here are the
        user's, so clustrix cannot classify them and no longer guesses.
        ``tests/unit/test_widget_save_withholds_unnamed_secrets.py`` is the
        value-level proof.
        """
        widget = _configured_widget()
        widget.env_vars_field.value = (
            '{"OMP_NUM_THREADS": "4", "SSH_PASSPHRASE": "<redacted>"}'
        )
        widget.configs = {"with-credentials": widget._save_config_from_widgets()}

        _save(widget, "envvars.yml")

        assert "environment_variables" not in _saved("envvars.yml")

    def test_the_ordinary_settings_still_round_trip(self):
        """The redaction must not be a general loss of the user's work."""
        widget = _configured_widget()

        _save(widget, "ordinary.yml")

        saved = _saved("ordinary.yml")
        assert saved["cluster_type"] == "ssh"
        assert saved["default_cores"] == widget.cores_field.value
        assert saved["remote_work_dir"] == widget.work_dir_field.value


class TestWhatTheUserIsTold:
    def test_the_user_is_told_what_was_withheld(self):
        widget = _configured_widget()

        output = _save(widget, "announced.yml")

        assert "password" in output
        assert "password_env_var" in output, "the supported channel must be named"
        assert "not a credential store" in output

    def test_the_notice_names_every_dropped_field(self):
        widget = _configured_widget()
        widget.cluster_type.value = "huggingface"
        widget.hf_token_field.value = "<redacted>"
        widget.configs = {"hf": widget._save_config_from_widgets()}
        widget.current_config_name = "hf"

        output = _save(widget, "both.yml")

        assert "password" in output and "hf_token" in output

    def test_the_notice_fires_once_per_widget(self):
        """``_on_save_config`` is a button. A repeated notice stops being read."""
        widget = _configured_widget()

        first = _save(widget, "once-a.yml")
        second = _save(widget, "once-b.yml")

        assert "password_env_var" in first
        assert "password_env_var" not in second
        assert "Configuration saved to" in second

    def test_a_configuration_without_credentials_saves_silently(self):
        """The notice must mean something, so it may not fire for everyone."""
        widget = EnhancedClusterConfigWidget()
        widget.config_name.value = "plain"
        widget.cluster_type.value = "local"
        widget.password_field.value = ""
        widget.current_config_name = "plain"
        widget.configs = {"plain": widget._save_config_from_widgets()}

        output = _save(widget, "plain.yml")

        assert "password_env_var" not in output
        assert "Configuration saved to" in output


class TestTheDroppedKeyDiff:
    """The notice is only as honest as the comparison behind it."""

    def test_a_removed_field_is_named(self):
        assert _dropped_keys({"a": 1, "password": "x"}, {"a": 1}) == {"password"}

    def test_a_removed_mapping_is_named_by_its_field(self):
        """Rewritten: entries are no longer filtered one at a time.

        This asserted that ``API_KEY`` was named individually, which only
        made sense while ``environment_variables`` was being filtered entry
        by entry on key names. The whole mapping is withheld now, so the
        field is what the user has to be told about.
        """
        before = {"environment_variables": {"OMP_NUM_THREADS": "4", "API_KEY": "x"}}
        after: dict = {}

        assert _dropped_keys(before, after) == {"environment_variables"}

    def test_a_key_the_format_does_not_define_is_named(self):
        """The widget carries whatever a loaded file contained.

        Those keys are dropped by the allowlist, and dropping something the
        user can see in their file without saying so is the surprise this
        notice exists to prevent.
        """
        before = {"cluster_type": "ssh", "aws_secret_access_key": "x"}
        after = {"cluster_type": "ssh"}

        assert _dropped_keys(before, after) == {"aws_secret_access_key"}

    def test_nothing_removed_is_reported_as_nothing(self):
        assert _dropped_keys({"a": 1}, {"a": 1}) == set()
