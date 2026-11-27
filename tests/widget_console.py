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


def pill(widget):
    """The words in the header pill: ``in use``, ``connected``, ``invalid``..."""
    html = widget.widgets["status_pill"].value
    return html.split(">", 1)[1].rsplit("<", 1)[0]
