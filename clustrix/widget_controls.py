"""Control helpers shared by both notebook widgets.

Small enough to be tempting to copy into each widget; kept here instead so
the two cannot disagree about what loading a saved configuration into a
dropdown means.
"""

from typing import List, Protocol, Sequence


class Choice(Protocol):
    """The slice of an ``ipywidgets.Dropdown`` this module touches.

    A structural type rather than the real class: ipywidgets is an optional
    dependency, and ``notebook_magic_fallback`` stands in for it when it is
    absent, so naming ``widgets.Dropdown`` here would either make this module
    unimportable or make it lie. ``object`` rather than ``str`` on both
    members is deliberate -- a hand-edited YAML can put anything in a config
    field, and the point of :func:`set_choice` is that the widget still opens
    when it does -- but it is not ``Any``: every use below has to narrow.
    """

    options: Sequence[object]
    value: object


def set_choice(field: Choice, value: object) -> None:
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
    options rather than discarded. Four properties of *how* it is added are
    load-bearing, and each is pinned by a test:

    ``None``, blank and whitespace-only are not choices.
        They mean "nothing was saved", not "save this". Selecting one would
        put an entry in the menu with nothing legible in it. Whitespace is
        stripped rather than merely rejected, so a value padded by a hand
        edit selects the entry it obviously means instead of growing a
        near-duplicate beside it.

    Appended, never prepended.
        The dropdown's own order is its design -- the modern widget's
        flavor menu runs cheapest first -- and the first entry is what a
        fresh widget shows. Putting the widened value at the front would
        both scramble that order and change the default for every later
        configuration.

    Added once.
        Loading the same profile twice, which the config dropdown's observer
        does routinely, must not stack duplicate entries.

    Never persisted.
        The widened list lives on this ``Dropdown`` instance for this
        session. That means widening *accumulates* while the widget is open:
        loading three profiles with three unlisted flavors leaves all three
        in the menu, and switching back to the first still works. That is
        the intended behaviour, not a leak -- the alternative, rebuilding the
        menu on every load, would make going back raise the very
        ``TraitError`` this function exists to prevent. Nothing writes the
        options anywhere, so a new widget starts from the hardcoded list.

    Only flat, string-valued menus are understood. ipywidgets also accepts
    ``(label, value)`` pairs, and appending a bare value to those would add a
    duplicate entry *and* relabel every existing one (the bare string is read
    as a label with itself as the value, and ipywidgets then rejects the
    heterogeneous list or renders it wrongly). No caller does that today, so
    rather than guess at a pairing this refuses, loudly, for whoever does it
    first.
    """
    if value is None:
        return
    # str() rather than a type check: a Dropdown's options are labels, so a
    # number from a hand-edited YAML is rendered as one instead of making the
    # menu heterogeneous. Refusing outright would be the widget failing to
    # open, which is the whole defect this function fixes.
    choice = str(value).strip()
    if not choice:
        return

    options: List[object] = list(field.options)
    for option in options:
        if not isinstance(option, str):
            raise TypeError(
                "set_choice understands a flat list of strings; this menu "
                f"offers {option!r}. Appending a bare value to (label, value) "
                "pairs adds a duplicate and relabels every other entry, so "
                "handle the pairs explicitly rather than calling this."
            )

    if choice not in options:
        field.options = options + [choice]
    field.value = choice
