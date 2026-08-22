"""A configuration file that cannot be read must not report as an empty one.

Issue #168, site 1. ``load_config_from_file`` used to answer every failure --
a path typo, a permissions problem, malformed YAML -- with ``{}``, which is
also its answer for a file that genuinely holds no configurations. The widget
then treated the result as a valid, blank profile and the user was told
nothing.

The distinction this restores is the one ``clustrix.config`` already draws and
is the reason there is no third policy here:

* a file the caller **named** is a file somebody chose, so failing to read it
  is an error and it raises -- exactly as :func:`clustrix.config.load_config`
  does for the same file;
* a file merely **discovered** by the widget's scan of the standard locations
  is best effort, so it stays non-fatal -- but the reason is logged rather
  than discarded, because "I could not read it" and "it holds nothing" are
  different answers.

Every case below uses a real file on disk: a real ``chmod 0o000``, real
malformed YAML, real malformed JSON, real undecodable bytes.
"""

import json
import logging
import os
import stat

import pytest
import yaml

from clustrix.notebook_magic_config import load_config_from_file

pytestmark = pytest.mark.usefixtures("isolate_home")


def _write(path, text):
    path.write_text(text)
    return path


class TestANamedFileRaises:
    """The caller named the path, so failing to read it is an error."""

    def test_malformed_yaml_raises_and_names_the_problem(self, tmp_path):
        bad = _write(tmp_path / "bad.yml", "invalid: yaml: content: [")
        with pytest.raises(yaml.YAMLError):
            load_config_from_file(str(bad))

    def test_malformed_json_raises(self, tmp_path):
        bad = _write(tmp_path / "bad.json", '{"invalid": json content}')
        with pytest.raises(json.JSONDecodeError):
            load_config_from_file(str(bad))

    def test_a_missing_file_raises_rather_than_reporting_empty(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config_from_file(str(tmp_path / "nowhere.yml"))

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root can read a 0o000 file, so there is nothing to fail on",
    )
    def test_an_unreadable_file_raises_rather_than_reporting_empty(self, tmp_path):
        """A real permissions problem, made with a real chmod."""
        locked = _write(tmp_path / "locked.yml", "profile:\n  cluster_type: local\n")
        os.chmod(locked, 0o000)
        try:
            with pytest.raises(PermissionError):
                load_config_from_file(str(locked))
        finally:
            os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)

    def test_undecodable_bytes_raise(self, tmp_path):
        bad = tmp_path / "binary.yml"
        bad.write_bytes(b"\xff\xfe\x00\x00invalid encoding")
        with pytest.raises(UnicodeDecodeError):
            load_config_from_file(str(bad))

    def test_a_readable_file_still_loads(self, tmp_path):
        """The raising path must not have cost the ordinary one."""
        good = _write(
            tmp_path / "good.yml",
            yaml.dump({"profile": {"cluster_type": "local", "default_cores": 2}}),
        )
        assert load_config_from_file(good) == {
            "profile": {"cluster_type": "local", "default_cores": 2}
        }


class TestADiscoveredFileIsReportedNotDiscarded:
    """Nobody named it, so it stays non-fatal -- but the reason is said."""

    def test_malformed_yaml_is_survivable_and_the_reason_is_logged(
        self, tmp_path, caplog
    ):
        bad = _write(tmp_path / "clustrix.yml", "invalid: yaml: content: [")
        with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_config"):
            assert load_config_from_file(bad, discovered=True) == {}
        message = caplog.text
        assert str(bad) in message, "the message must name the file it gave up on"
        assert "YAMLError" in message or "yaml" in message.lower(), (
            "the message must carry the reason, not just the fact of failure: "
            f"got {message!r}"
        )

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root can read a 0o000 file, so there is nothing to fail on",
    )
    def test_an_unreadable_file_is_survivable_and_the_reason_is_logged(
        self, tmp_path, caplog
    ):
        locked = _write(tmp_path / "clustrix.yml", "profile:\n  cluster_type: local\n")
        os.chmod(locked, 0o000)
        try:
            with caplog.at_level(
                logging.WARNING, logger="clustrix.notebook_magic_config"
            ):
                assert load_config_from_file(locked, discovered=True) == {}
        finally:
            os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)
        assert str(locked) in caplog.text
        assert "PermissionError" in caplog.text

    def test_a_file_that_really_holds_nothing_is_not_reported(self, tmp_path, caplog):
        """The whole point: silence means empty, noise means unreadable."""
        empty = _write(tmp_path / "clustrix.yml", "")
        with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_config"):
            assert load_config_from_file(empty, discovered=True) == {}
        assert caplog.text == "", (
            "an empty file is not a failure and must not be reported as one: "
            f"got {caplog.text!r}"
        )


class TestTheWidgetScanUsesTheDiscoveredContract:
    """The widget globs for these files; it must not blow up, and must say so."""

    def test_the_widget_survives_an_unreadable_discovered_file_and_reports_it(
        self, tmp_path, monkeypatch, caplog
    ):
        ipywidgets = pytest.importorskip("ipywidgets")
        assert ipywidgets  # the widget module refuses to build without it

        from clustrix.notebook_magic import EnhancedClusterConfigWidget

        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "clustrix.yml", "invalid: yaml: content: [")

        with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_config"):
            widget = EnhancedClusterConfigWidget()

        assert "Local Single-core" in widget.configs, (
            "a file the scan could not read must not take the built-in "
            "templates down with it"
        )
        assert str(tmp_path / "clustrix.yml") in caplog.text, (
            "the widget's scan must report the file it could not read; "
            f"log was {caplog.text!r}"
        )
