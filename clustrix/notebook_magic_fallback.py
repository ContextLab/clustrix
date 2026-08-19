"""Fallback implementations used when IPython / ipywidgets are not installed.

``ipywidgets`` is an optional dependency (see the ``widgets`` extra in
pyproject.toml): clustrix is a distributed-computing framework first, and the
notebook configuration widget is a convenience for the subset of users who
run it inside Jupyter. Importing ``clustrix.notebook_magic*`` must not blow
up for everyone else, so this module supplies the small amount of real,
honestly-degraded behaviour those modules fall back to when IPython and/or
ipywidgets cannot be imported.

Two different things live here, and they are held to different standards:

* ``Magics``, ``magics_class``, ``cell_magic``, ``line_magic``, ``display``,
  ``get_ipython`` and ``HTML`` are genuinely exercised without IPython
  installed -- ``ClusterfyMagics`` (see notebook_magic_core.py) still has to
  be importable and its ``%clustrix`` line magic still has to run so that
  ``pip install clustrix`` (no IPython) does not break plain Python use.
  These are real, if minimal, implementations: ``get_ipython`` returning
  ``None`` matches what IPython's own ``get_ipython()`` does outside a
  notebook, ``display`` is a legitimate no-op because there is no display
  backend to hand anything to, and so on.

* ``widgets`` is different. ``EnhancedClusterConfigWidget`` (see
  notebook_magic_widget.py) refuses to construct itself unless real IPython
  *and* real ipywidgets are both present -- it raises ``ImportError``
  immediately in ``__init__``, before any ``widgets.Dropdown(...)`` call is
  ever reached. So a full fake ipywidgets implementation here would be dead
  code: elaborate, never executed, and dishonest about pretending to be a
  working widget toolkit. Instead ``widgets`` is a placeholder whose
  attributes raise a clear, actionable ``ImportError`` the instant anything
  touches them, which is both accurate (ipywidgets truly is not installed)
  and impossible to mistake for a working substitute.
"""

from typing import Any


class Magics:  # type: ignore
    """Stand-in base class for ``IPython.core.magic.Magics``."""

    pass


def magics_class(cls):
    return cls


def _magic_decorator(func):
    """Wrap a magic method so calling it still runs its body."""

    def method_wrapper(self, line="", cell=""):
        return func(self, line, cell)

    method_wrapper.__name__ = getattr(func, "__name__", "magic")
    method_wrapper.__doc__ = getattr(func, "__doc__", "")
    method_wrapper._original = func
    return method_wrapper


def cell_magic(*args, **kwargs):
    """Stand-in for IPython's cell_magic, used when IPython is absent.

    IPython allows both `@cell_magic` and `@cell_magic("name")`. This only
    handled the second form: applied bare -- which is how clustrix uses it --
    it returned its own inner `decorator`, so calling the magic invoked that
    with (self, line, cell), fell through to the catch-all branch, and
    returned `lambda: None` without ever running the method. Every magic was
    therefore a no-op whenever IPython was unavailable, which is precisely the
    situation this module exists for.
    """
    if len(args) == 1 and callable(args[0]) and not kwargs:
        # Bare @cell_magic
        return _magic_decorator(args[0])

    # @cell_magic("name")
    def decorator(func):
        return _magic_decorator(func)

    return decorator


def line_magic(*args, **kwargs):
    """Stand-in for IPython's line_magic; same two calling conventions."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        func = args[0]

        def method_wrapper(self, line=""):
            return func(self, line)

        method_wrapper.__name__ = getattr(func, "__name__", "magic")
        method_wrapper.__doc__ = getattr(func, "__doc__", "")
        method_wrapper._original = func
        return method_wrapper

    def decorator(func):
        return line_magic(func)

    return decorator


def display(*args, **kwargs):
    """No-op: there is no notebook display backend to render into."""
    pass


def get_ipython():
    """Matches real IPython: returns None outside an interactive session."""
    return None


class HTML:  # type: ignore
    """Placeholder for ``IPython.display.HTML``; stores its input, renders nothing."""

    def __init__(self, data: str = "", *args, **kwargs):
        self.data = data


class _WidgetsUnavailable:
    """Placeholder for the ``ipywidgets`` module when it is not installed.

    ``EnhancedClusterConfigWidget`` raises ``ImportError`` in its own
    ``__init__`` before touching any ``widgets.*`` attribute when ipywidgets
    is missing, so nothing in clustrix ever reaches through this object at
    runtime. It exists solely so that ``notebook_magic_widget.py`` -- whose
    method bodies reference ``widgets.Dropdown``, ``widgets.Button``, etc. --
    remains importable without ipywidgets installed. Unlike a mock, it does
    not simulate the ipywidgets API: any attribute access fails immediately
    and explicitly, so a bug that somehow did reach this path would raise a
    clear, actionable error instead of silently behaving like a fake widget.
    """

    def __getattr__(self, name: str) -> Any:
        raise ImportError(
            f"ipywidgets is not installed, so widgets.{name} is unavailable. "
            "Install it with: pip install ipywidgets"
        )


widgets = _WidgetsUnavailable()
