"""Renaming a profile onto a name another profile holds must not destroy it.

``_on_config_name_change`` popped the configuration out from under its old
name and wrote it under the new one with no collision check, so typing the
name of another profile into the name box overwrote that profile and said
nothing about it. A profile is the only place a ``password`` or an
``hf_token`` lives -- ``save_to_file`` omits both -- so the loss had no
recovery path, and the action that caused it was a *rename*, which nobody
expects to delete anything. Issue #171.

The decided behaviour is **refuse**, stated in ``_on_config_name_change``
itself. These tests drive that real handler on a real widget the only way the
name box drives it: by assigning to ``config_name.value``.

The assertions are on the surviving profile's *contents*. A count survives an
overwrite -- one key replaced by another leaves five profiles either way --
which is exactly how the defect went unnoticed while the module's other
rename tests passed.
"""

import copy
import io
from contextlib import redirect_stdout

import pytest

pytest.importorskip("ipywidgets")

from clustrix.notebook_magic_config import DEFAULT_CONFIGS  # noqa: E402
from clustrix.notebook_magic_widget import (  # noqa: E402
    EnhancedClusterConfigWidget,
)


def _widget_selecting(name):
    """A real widget with ``name`` picked in the dropdown, as a user picks it."""
    widget = EnhancedClusterConfigWidget()
    assert (
        name in widget.config_dropdown.options
    ), f"the widget did not offer {name!r}: {widget.config_dropdown.options}"
    widget.config_dropdown.value = name
    assert widget.current_config_name == name
    return widget


def _type_name(widget, text):
    """Type into the name box, returning whatever the widget printed."""
    captured = io.StringIO()
    with redirect_stdout(captured):
        widget.config_name.value = text
    return captured.getvalue()


def test_renaming_onto_an_existing_profile_leaves_that_profile_intact():
    """The issue's reproduction: pick HuggingFace Jobs, type the SSH name.

    RED before the fix: ``configs["SSH Remote Server"]`` was the HuggingFace
    configuration, and the SSH host, username and work directory were gone.
    """
    widget = _widget_selecting("HuggingFace Jobs")
    occupant = copy.deepcopy(widget.configs["SSH Remote Server"])
    renamed = copy.deepcopy(widget.configs["HuggingFace Jobs"])

    _type_name(widget, "SSH Remote Server")

    assert (
        widget.configs["SSH Remote Server"] == occupant
    ), "renaming a profile onto this name overwrote the profile that held it"
    assert (
        widget.configs["HuggingFace Jobs"] == renamed
    ), "the profile being renamed was moved even though the rename was refused"
    assert set(widget.configs) == set(DEFAULT_CONFIGS)


def test_the_refusal_says_which_configuration_already_holds_the_name():
    """Silently doing nothing is the same defect wearing a different hat."""
    widget = _widget_selecting("HuggingFace Jobs")

    printed = _type_name(widget, "SSH Remote Server")

    assert "❌" in printed, f"the refusal was not reported at all: {printed!r}"
    assert "SSH Remote Server" in printed
    assert "HuggingFace Jobs" in printed


def test_a_refused_rename_leaves_the_widget_able_to_finish_the_rename():
    """Refusing must not strand the widget, because the box fires per keystroke.

    ``current_config_name`` is left where it was, so the next keystroke that
    reaches a free name renames the configuration the user was actually
    editing -- a user typing "SSH Remote Server 2" passes through the taken
    name on the way and must still arrive.
    """
    widget = _widget_selecting("HuggingFace Jobs")

    _type_name(widget, "SSH Remote Server")
    assert widget.current_config_name == "HuggingFace Jobs"

    _type_name(widget, "SSH Remote Server 2")

    assert widget.current_config_name == "SSH Remote Server 2"
    assert widget.configs["SSH Remote Server 2"]["cluster_type"] == "huggingface"
    assert widget.configs["SSH Remote Server"]["cluster_type"] == "ssh"
    assert widget.configs["SSH Remote Server"]["cluster_host"] == "remote.server.com"
    assert "HuggingFace Jobs" not in widget.configs


def test_a_refused_rename_does_not_reach_the_dropdown_either():
    """The dropdown lists ``self.configs``; both names must still be in it."""
    widget = _widget_selecting("HuggingFace Jobs")

    _type_name(widget, "SSH Remote Server")

    assert "SSH Remote Server" in widget.config_dropdown.options
    assert "HuggingFace Jobs" in widget.config_dropdown.options
