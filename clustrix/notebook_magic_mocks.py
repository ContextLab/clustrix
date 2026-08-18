"""
Mock classes for non-IPython environments.

This module provides placeholder classes and functions when IPython/Jupyter
is not available, allowing the notebook magic functionality to gracefully
degrade in non-interactive environments.
"""


# Create placeholder classes for non-notebook environments
class Magics:  # type: ignore
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
    """Placeholder display function."""
    pass


def get_ipython():
    return None


class HTML:  # type: ignore
    """Placeholder HTML class."""

    def __init__(self, *args, **kwargs):
        pass


# Mock widgets module - each class creates independent instances
class _MockLayout:
    def __init__(self, *args, **kwargs):
        self.display = ""
        self.border = ""
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockDropdown:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value")
        self.options = kwargs.get("options", [])
        self.layout = _MockLayout()

    def observe(self, *args, **kwargs):
        pass


class _MockButton:
    def __init__(self, *args, **kwargs):
        self.layout = _MockLayout()

    def on_click(self, *args, **kwargs):
        pass


class _MockText:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value", "")
        self.layout = _MockLayout()

    def observe(self, *args, **kwargs):
        pass


class _MockIntText:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value", 0)
        self.layout = _MockLayout()

    def observe(self, *args, **kwargs):
        pass


class _MockTextarea:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value", "")
        self.layout = _MockLayout()

    def observe(self, *args, **kwargs):
        pass


class _MockOutput:
    def __init__(self, *args, **kwargs):
        self.layout = _MockLayout()

    def clear_output(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockVBox:
    def __init__(self, *args, **kwargs):
        self.children = args[0] if args else []
        self.layout = _MockLayout()


class _MockHBox:
    def __init__(self, *args, **kwargs):
        self.children = args[0] if args else []
        self.layout = _MockLayout()


class _MockHTML:
    def __init__(self, *args, **kwargs):
        self.value = args[0] if args else ""
        self.layout = _MockLayout()


class _MockCheckbox:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value", False)
        self.layout = _MockLayout()

    def observe(self, *args, **kwargs):
        pass


class _MockAccordion:
    def __init__(self, *args, **kwargs):
        self.children = args[0] if args else []
        self.selected_index = None
        self.layout = _MockLayout()

    def set_title(self, *args, **kwargs):
        pass


class widgets:  # type: ignore
    Layout = _MockLayout
    Dropdown = _MockDropdown
    Button = _MockButton
    Text = _MockText
    IntText = _MockIntText
    Textarea = _MockTextarea
    Output = _MockOutput
    VBox = _MockVBox
    HBox = _MockHBox
    HTML = _MockHTML
    Checkbox = _MockCheckbox
    Accordion = _MockAccordion
