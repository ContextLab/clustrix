"""Fallback implementations used when IPython / ipywidgets are not installed.

``ipywidgets`` is an optional dependency (see the ``widgets`` extra in
pyproject.toml): clustrix is a distributed-computing framework first, and the
notebook configuration widget is a convenience for the subset of users who
run it inside Jupyter. Importing ``clustrix.notebook_magic*`` must not blow
up for everyone else, so this module supplies the small amount of real,
honestly-degraded behaviour those modules fall back to when IPython and/or
ipywidgets cannot be imported.

``Magics``, ``magics_class``, ``cell_magic``, ``line_magic`` and
``get_ipython`` are genuinely exercised without IPython installed --
``ClusterfyMagics`` (see notebook_magic_core.py) still has to be importable
and its ``%clustrix`` line magic still has to run so that
``pip install clustrix`` (no IPython) does not break plain Python use. These
are real, if minimal, implementations: ``get_ipython`` returning ``None``
matches what IPython's own ``get_ipython()`` does outside a notebook, and a
magic decorated here still runs its body when called.
"""


class Magics:  # type: ignore
    """Stand-in base class for ``IPython.core.magic.Magics``."""

    pass


def magics_class(cls):
    return cls


def _passthrough_decorator(*args, **kwargs):
    """Stand-in for IPython's ``cell_magic`` / ``line_magic``.

    IPython allows both ``@cell_magic`` and ``@cell_magic("name")``. An
    earlier version handled only the second form: applied bare -- which is
    how clustrix uses it -- it returned its own inner decorator, so calling
    the magic invoked *that* with (self, line, cell) and returned a no-op
    without ever running the method. Every magic was therefore a no-op
    whenever IPython was unavailable, which is precisely the situation this
    module exists for. Both forms now hand back the method itself, so calling
    it runs its body with whatever arguments it declares.
    """
    if len(args) == 1 and callable(args[0]) and not kwargs:
        # Bare @cell_magic
        return args[0]

    # @cell_magic("name")
    def decorator(func):
        return func

    return decorator


cell_magic = _passthrough_decorator
line_magic = _passthrough_decorator


def get_ipython():
    """Matches real IPython: returns None outside an interactive session."""
    return None
