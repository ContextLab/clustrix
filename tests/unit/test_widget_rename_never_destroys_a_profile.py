"""Renaming a profile onto a name another profile holds must not destroy it.

Typing into the Name box renames the selected profile. A rename that lands on
a name another profile already holds has two possible implementations --
overwrite the occupant, or refuse -- and the first destroys a profile from an
action nobody expects to delete anything. A profile is also the only place a
``password`` or an ``hf_token`` lives for the session (``save_to_file`` omits
both), so the loss has no recovery path. Issue #171.

The decided behaviour is **refuse**. These tests drive the real handler on a
real widget the only way the Name box drives it: by assigning to
``profile_name.value``.

The assertions are on the surviving profiles' *contents*. A count survives an
overwrite -- one key replaced by another leaves the same number of profiles
either way -- which is exactly how the defect went unnoticed in the widget
this one replaced.
"""

import copy

import pytest

pytest.importorskip("ipywidgets")

from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402
from tests.widget_console import pill, press  # noqa: E402

RENAMED = "HuggingFace Jobs (CPU)"
OCCUPANT = "SSH remote machine"


def _widget_selecting(name):
    """A real widget with ``name`` picked in the dropdown, as a user picks it.

    The occupant is given a host and a session-only password first, so that
    "intact" means something a template's defaults could not fake.
    """
    widget = ModernClustrixWidget()
    controls = widget.widgets
    controls["profile_dropdown"].value = OCCUPANT
    controls["host"].value = "remote.example.edu"
    controls["username"].value = "researcher"
    controls["password"].value = "only-in-this-session"
    assert name in controls["profile_dropdown"].options
    controls["profile_dropdown"].value = name
    assert widget.profile_manager.active_profile == name
    return widget


def _type_name(widget, text, capsys):
    """Type into the Name box, returning whatever the widget told the user."""

    def typed():
        widget.widgets["profile_name"].value = text

    return press(typed, widget.widgets["output"], capsys)


def _profiles(widget):
    return copy.deepcopy(widget.profile_manager.profiles)


def test_renaming_onto_an_existing_profile_leaves_that_profile_intact(capsys):
    """The issue's reproduction: pick the HuggingFace profile, type the SSH
    profile's name."""
    widget = _widget_selecting(RENAMED)
    before = _profiles(widget)
    assert before[OCCUPANT].cluster_host == "remote.example.edu"
    assert before[OCCUPANT].password == "only-in-this-session"

    _type_name(widget, OCCUPANT, capsys)

    after = _profiles(widget)
    assert (
        after[OCCUPANT] == before[OCCUPANT]
    ), "renaming a profile onto this name overwrote the profile that held it"
    assert after[OCCUPANT].cluster_type == "ssh"
    assert (
        after[RENAMED] == before[RENAMED]
    ), "the profile being renamed was moved even though the rename was refused"
    assert list(after) == list(before)


def test_the_refusal_says_which_profile_already_holds_the_name(capsys):
    """Silently doing nothing is the same defect wearing a different hat."""
    widget = _widget_selecting(RENAMED)

    told = _type_name(widget, OCCUPANT, capsys)

    assert pill(widget) == "not renamed"
    assert "Could not rename" in told, f"the refusal was not reported: {told!r}"
    assert OCCUPANT in told
    assert RENAMED in told


def test_a_refused_rename_puts_the_real_name_back_in_the_box(capsys):
    """The box must not go on showing a name the profile does not have.

    Otherwise the form says "SSH remote machine" over the HuggingFace
    profile's settings, and the next edit is made to a profile the user
    believes is a different one.
    """
    widget = _widget_selecting(RENAMED)

    _type_name(widget, OCCUPANT, capsys)

    assert widget.widgets["profile_name"].value == RENAMED
    assert widget.widgets["profile_dropdown"].value == RENAMED
    assert widget.widgets["cluster_type"].value == "huggingface"


def test_a_refused_rename_leaves_the_widget_able_to_finish_the_rename(capsys):
    """Refusing must not strand the widget on the profile it refused."""
    widget = _widget_selecting(RENAMED)

    _type_name(widget, OCCUPANT, capsys)
    assert widget.profile_manager.active_profile == RENAMED

    _type_name(widget, OCCUPANT + " 2", capsys)

    profiles = widget.profile_manager.profiles
    assert widget.profile_manager.active_profile == OCCUPANT + " 2"
    assert profiles[OCCUPANT + " 2"].cluster_type == "huggingface"
    assert profiles[OCCUPANT].cluster_type == "ssh"
    assert profiles[OCCUPANT].cluster_host == "remote.example.edu"
    assert RENAMED not in profiles


def test_a_refused_rename_does_not_reach_the_dropdown_either(capsys):
    """The dropdown lists the profiles; both names must still be in it."""
    widget = _widget_selecting(RENAMED)

    _type_name(widget, OCCUPANT, capsys)

    offered = list(widget.widgets["profile_dropdown"].options)
    assert offered.count(OCCUPANT) == 1
    assert offered.count(RENAMED) == 1


@pytest.mark.parametrize("blank", ["", "   ", "\t"], ids=repr)
def test_a_profile_cannot_be_renamed_to_nothing(blank, capsys):
    """An empty name is a profile nobody can select again."""
    widget = _widget_selecting(RENAMED)
    before = _profiles(widget)

    told = _type_name(widget, blank, capsys)

    assert _profiles(widget) == before
    assert pill(widget) == "not renamed"
    assert "A profile needs a name" in told
    assert widget.widgets["profile_name"].value == RENAMED


def test_a_rename_that_is_allowed_keeps_what_was_on_screen(capsys):
    """The control: the refusals above must not have cost the rename itself.

    Including an edit that had not been captured yet -- the rename happens to
    the profile the user is looking at, not to its last saved copy.
    """
    widget = _widget_selecting(OCCUPANT)
    widget.widgets["cpus"].value = 12

    told = _type_name(widget, "My cluster", capsys)

    profiles = widget.profile_manager.profiles
    assert OCCUPANT not in profiles
    assert profiles["My cluster"].cluster_host == "remote.example.edu"
    assert profiles["My cluster"].password == "only-in-this-session"
    assert profiles["My cluster"].default_cores == 12
    assert widget.widgets["profile_dropdown"].value == "My cluster"
    assert "Could not rename" not in told
    # Surrounding whitespace is not part of a name.
    _type_name(widget, "  Padded  ", capsys)
    assert "Padded" in widget.profile_manager.profiles
    assert widget.widgets["profile_dropdown"].value == "Padded"
