"""Read what the notebook widget told the user, without a browser.

Shared by every test module that presses the widget's buttons, so there is
one definition of "what the user was told".
"""


def press(action, output_widget, capsys):
    """Run a button handler and return everything it told the user.

    The widget prints inside an ``ipywidgets.Output`` context. With a live
    kernel that lands in ``output.outputs``; without one it falls through to
    stdout. Both are read so the assertions do not depend on which.
    """
    already = len(output_widget.outputs)
    action()
    streamed = capsys.readouterr().out
    captured = "".join(
        entry.get("text", "")
        for entry in output_widget.outputs[already:]
        if isinstance(entry, dict)
    )
    return (streamed + captured).replace("\x1b[2K\r", "")


def _words(html):
    """The text inside a pill's one ``<span>``; ``""`` for an empty pill."""
    if not html:
        return ""
    return html.split(">", 1)[1].rsplit("<", 1)[0]


def pill(widget):
    """How the last action went: ``applied``, ``connected``, ``invalid``...

    Empty when no action has run since the form last changed -- a result
    describes the form it was produced from, and is cleared by the next edit.
    """
    return _words(widget.widgets["status_pill"].value)


def applied(widget):
    """The header's answer to "is the form what @cluster is using?":
    ``in use`` or ``not applied``. Independent of the last action's result."""
    return _words(widget.widgets["applied_pill"].value)
