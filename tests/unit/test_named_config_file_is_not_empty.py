"""A profiles file that cannot be read must not report as an empty one.

Issue #168, site 1. A loader that answers every failure -- a path typo, a
permissions problem, malformed YAML -- with "nothing here" gives the same
answer it gives for a file that genuinely holds nothing, and the widget this
one replaced then offered the result as a valid, blank profile and told the
user nothing.

The distinction to hold is the one ``clustrix.config`` draws:

* a file the user **named** in the "Profiles file" box and pressed Load on is
  a file somebody chose, so failing to read it is an error that is *said*,
  and it must not cost them the profiles they already had -- Load replaces
  every profile, so "loaded nothing" would mean "deleted everything";
* a file merely **discovered** by the scan that fills the Load menu is best
  effort, so it never stops the widget opening -- but a file that could not
  be *read* is reported, because "I could not read it" and "it is not a
  profile bundle" are different answers and only one deserves silence.

Every case below uses a real file on disk: a real ``chmod 0o000``, real
malformed YAML, real malformed JSON, real undecodable bytes.
"""

import json
import logging
import os
import stat
import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "drives config loading through POSIX permission and shell edges",
        allow_module_level=True,
    )
import yaml

pytest.importorskip("ipywidgets")

from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402
from tests.widget_console import pill, press  # noqa: E402

WIDGET_LOGGER = "clustrix.modern_notebook_widget"

running_as_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root can read a 0o000 file, so there is nothing to fail on",
)


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _load(path, capsys):
    """Name ``path`` in the box and press the real Load button.

    Returns the widget, what it told the user, and the profiles it held
    before, so a caller can assert that a failed load cost nothing.
    """
    widget = ModernClustrixWidget()
    widget.widgets["profile_dropdown"].value = "SLURM cluster"
    widget.widgets["host"].value = "kept.example.edu"
    widget.widgets["profile_dropdown"].value = "Local single-core"
    before = dict(widget.profile_manager.profiles)
    widget.widgets["config_filename"].value = str(path)
    told = press(
        lambda: widget._on_load_config(widget.widgets["load_btn"]),
        widget.widgets["output"],
        capsys,
    )
    return widget, told, before


def _assert_nothing_was_lost(widget, before):
    assert widget.profile_manager.profiles == before, (
        "a file that could not be loaded replaced the profiles that were "
        "already there"
    )
    assert before["SLURM cluster"].cluster_host == "kept.example.edu"
    assert list(widget.widgets["profile_dropdown"].options) == list(before)
    assert widget.widgets["profile_dropdown"].value == "Local single-core"


class TestANamedFileThatCannotBeLoadedSaysSo:
    """The user named the path, so failing to read it is an error."""

    def test_malformed_yaml_is_reported_and_costs_nothing(self, tmp_path, capsys):
        bad = _write(tmp_path / "bad.yml", "invalid: yaml: content: [")

        widget, told, before = _load(bad, capsys)

        assert pill(widget) == "load failed"
        assert "Could not load" in told
        _assert_nothing_was_lost(widget, before)

    def test_malformed_json_is_reported_and_costs_nothing(self, tmp_path, capsys):
        bad = _write(tmp_path / "bad.json", '{"invalid": json content}')

        widget, told, before = _load(bad, capsys)

        assert pill(widget) == "load failed"
        assert "Could not load" in told
        _assert_nothing_was_lost(widget, before)

    def test_a_missing_file_is_reported_rather_than_read_as_empty(
        self, tmp_path, capsys
    ):
        missing = tmp_path / "nowhere.yml"

        widget, told, before = _load(missing, capsys)

        assert pill(widget) == "load failed"
        assert str(missing) in told, "the message must name the file it looked for"
        _assert_nothing_was_lost(widget, before)

    @running_as_root
    def test_an_unreadable_file_is_reported_rather_than_read_as_empty(
        self, tmp_path, capsys
    ):
        """A real permissions problem, made with a real chmod."""
        locked = _write(
            tmp_path / "locked.yml", "profiles:\n  p:\n    cluster_type: local\n"
        )
        os.chmod(locked, 0o000)
        try:
            widget, told, before = _load(locked, capsys)
        finally:
            os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)

        assert pill(widget) == "load failed"
        assert "Permission denied" in told
        _assert_nothing_was_lost(widget, before)

    def test_undecodable_bytes_are_reported_and_cost_nothing(self, tmp_path, capsys):
        bad = tmp_path / "binary.yml"
        bad.write_bytes(b"\xff\xfe\x00\x00invalid encoding")

        widget, told, before = _load(bad, capsys)

        assert pill(widget) == "load failed"
        assert "Could not load" in told
        _assert_nothing_was_lost(widget, before)

    @pytest.mark.parametrize(
        "content",
        ["", "just some prose\n", "- a\n- list\n", "cluster_type: local\n"],
        ids=["empty", "prose", "a list", "a flat config, not a bundle"],
    )
    def test_a_file_holding_no_profiles_is_refused_not_loaded_as_none(
        self, content, tmp_path, capsys
    ):
        """The headline defect: "holds nothing" must not become "now you
        have nothing"."""
        empty = _write(tmp_path / "empty.yml", content)

        widget, told, before = _load(empty, capsys)

        assert pill(widget) == "load failed"
        assert "Could not load" in told
        assert str(empty) in told, "the refusal must name the file"
        _assert_nothing_was_lost(widget, before)

    def test_a_readable_bundle_still_loads(self, tmp_path, capsys):
        """The refusing paths must not have cost the ordinary one."""
        good = _write(
            tmp_path / "good.yml",
            yaml.dump({"profiles": {"mine": {"cluster_type": "local"}}}),
        )

        widget, told, _ = _load(good, capsys)

        assert pill(widget) == "loaded"
        assert widget.profile_manager.get_profile_names() == ["mine"]
        assert widget.widgets["profile_dropdown"].value == "mine"
        assert "Loaded 1 profile(s)" in told and "mine" in told

    def test_a_readable_json_bundle_still_loads(self, tmp_path, capsys):
        good = _write(
            tmp_path / "good.json",
            json.dumps({"profiles": {"mine": {"cluster_type": "local"}}}),
        )

        widget, _, _ = _load(good, capsys)

        assert pill(widget) == "loaded"
        assert widget.profile_manager.get_profile_names() == ["mine"]


class TestADiscoveredFileNeverStopsTheWidget:
    """Nobody named it, so it stays non-fatal -- but an unread file is said."""

    def _offered(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        widget = ModernClustrixWidget()
        return list(widget.widgets["config_filename"].options)

    def test_malformed_yaml_is_survivable_and_simply_not_offered(
        self, tmp_path, monkeypatch, caplog
    ):
        """Read in full and not parseable is a real answer, and a quiet one:
        a working tree is full of YAML that has nothing to do with clustrix."""
        bad = _write(tmp_path / "clustrix.yml", "invalid: yaml: content: [")
        _write(tmp_path / "mine.yml", "profiles:\n  p:\n    cluster_type: local\n")

        with caplog.at_level(logging.WARNING, logger=WIDGET_LOGGER):
            offered = self._offered(tmp_path, monkeypatch)

        assert str(bad) not in offered
        assert str(tmp_path / "mine.yml") in offered, offered
        assert [r for r in caplog.records if r.name == WIDGET_LOGGER] == []

    def test_undecodable_bytes_are_survivable_and_the_reason_is_logged(
        self, tmp_path, monkeypatch, caplog
    ):
        """This file was never *read*, so "not a bundle" would be a guess."""
        bad = tmp_path / "binary.yml"
        bad.write_bytes(b"\xff\xfe\x00\x00invalid encoding")

        with caplog.at_level(logging.WARNING, logger=WIDGET_LOGGER):
            offered = self._offered(tmp_path, monkeypatch)

        assert str(bad) not in offered
        message = "\n".join(
            r.getMessage() for r in caplog.records if r.name == WIDGET_LOGGER
        )
        assert str(bad) in message, "the message must name the file it gave up on"
        assert "not evidence that it holds no profiles" in message

    @running_as_root
    def test_an_unreadable_file_is_survivable_and_the_reason_is_logged(
        self, tmp_path, monkeypatch, caplog
    ):
        locked = _write(
            tmp_path / "locked.yml", "profiles:\n  p:\n    cluster_type: local\n"
        )
        os.chmod(locked, 0o000)
        try:
            with caplog.at_level(logging.WARNING, logger=WIDGET_LOGGER):
                offered = self._offered(tmp_path, monkeypatch)
        finally:
            os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)

        assert str(locked) not in offered
        assert str(locked) in caplog.text
        assert "Permission denied" in caplog.text

    def test_a_file_that_really_holds_nothing_is_not_reported(
        self, tmp_path, monkeypatch, caplog
    ):
        """The whole point: silence means "not a bundle", noise means
        "could not read"."""
        empty = _write(tmp_path / "clustrix.yml", "")

        with caplog.at_level(logging.WARNING, logger=WIDGET_LOGGER):
            offered = self._offered(tmp_path, monkeypatch)

        assert str(empty) not in offered
        assert [r for r in caplog.records if r.name == WIDGET_LOGGER] == [], (
            "an empty file is not a failure and must not be reported as one: "
            f"got {caplog.text!r}"
        )

    def test_the_built_in_profiles_are_all_still_there(self, tmp_path, monkeypatch):
        """A file the scan could not use must not take the templates with it."""
        _write(tmp_path / "clustrix.yml", "invalid: yaml: content: [")
        (tmp_path / "binary.yml").write_bytes(b"\xff\xfe\x00\x00")
        monkeypatch.chdir(tmp_path)

        widget = ModernClustrixWidget()

        names = widget.profile_manager.get_profile_names()
        assert "Local single-core" in names and "SLURM cluster" in names
        assert widget.widgets["profile_dropdown"].value in names
