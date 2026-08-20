"""Control helpers shared by both notebook widgets.

Small enough to be tempting to copy into each widget; kept here instead so
the two cannot disagree about what loading a saved configuration into a
dropdown means.
"""

from typing import Any


def set_choice(field: Any, value: Any) -> None:
    """Select ``value`` in a dropdown, widening the options if need be.

    These assignments used to be bare ``field.value = ...``, so loading a
    configuration whose hardware flavor, region or package manager was not in
    the hardcoded list raised

        TraitError: Invalid selection: value not found

    and broke the widget outright -- the user could not open it at all. New
    hardware flavors appear faster than any list baked into a UI, and
    ``ClusterConfig`` validates none of these fields, so an ordinary config
    file reaches this.

    The saved configuration is authoritative -- a list baked into the UI
    should not be able to veto it -- so an unrecognised value is added to the
    options rather than discarded.
    """
    if value in (None, ""):
        return
    if value not in field.options:
        field.options = list(field.options) + [value]
    field.value = value
