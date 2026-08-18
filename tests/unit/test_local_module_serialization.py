"""A function that reaches into the user's own project must take it along.

dill and cloudpickle both store an importable object by *reference* -- "import
mypkg.mathutils, then get triple". That is right for numpy, which the worker
also has, and wrong for the user's own modules, which exist on no machine but
theirs. Every such function used to fail remotely with
`ModuleNotFoundError: No module named 'mypkg'`.

These tests deserialize in a subprocess whose sys.path cannot reach the
package, which is the only way to tell an embedded module from an imported one.
"""

import pathlib
import subprocess
import sys
import textwrap

import pytest

from clustrix.utils import (
    _is_local_module,
    _referenced_local_modules,
    serialize_function,
)

LOCAL_PROJECT = pathlib.Path(__file__).parent / "localproject"
sys.path.insert(0, str(LOCAL_PROJECT))

from mypkg.mathutils import SCALE, Widget, triple  # noqa: E402


def uses_local_function(x):
    return triple(x) + SCALE


def uses_local_class(n):
    return Widget(n).value()


def takes_local_instance(widget):
    return widget.value() * 2


def _round_trip(func, args):
    """Deserialize and call in an interpreter that cannot import the package."""
    data = serialize_function(func, args, {})
    program = textwrap.dedent(
        """
        import sys, base64
        import cloudpickle, dill

        def load(raw):
            try:
                return cloudpickle.loads(raw)
            except Exception:
                return dill.loads(raw)

        func = load(base64.b64decode(sys.argv[1]))
        args = load(base64.b64decode(sys.argv[2]))
        print(repr(func(*args)))
        """
    )
    import base64

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            base64.b64encode(data["function"]).decode(),
            base64.b64encode(data["args"]).decode(),
        ],
        capture_output=True,
        text=True,
        cwd="/",  # so the package is not importable via the working directory
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    if result.returncode != 0:
        pytest.fail(f"remote-side execution failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


class TestLocalModulesTravel:
    def test_function_from_a_sibling_module(self):
        assert _round_trip(uses_local_function, (4,)) == repr(uses_local_function(4))

    def test_class_from_a_sibling_module(self):
        assert _round_trip(uses_local_class, (3,)) == repr(uses_local_class(3))

    def test_instance_passed_as_an_argument(self):
        widget = Widget(2)
        assert _round_trip(takes_local_instance, (widget,)) == repr(
            takes_local_instance(widget)
        )


class TestWhatCountsAsLocal:
    def test_a_project_module_is_local(self):
        import mypkg.mathutils

        assert _is_local_module(mypkg.mathutils)

    @pytest.mark.parametrize("name", ["json", "pathlib", "subprocess"])
    def test_the_standard_library_is_not_local(self, name):
        assert not _is_local_module(__import__(name))

    def test_an_installed_package_is_not_local(self):
        """The worker mirrors the local environment, so installs are already there."""
        import cloudpickle

        assert not _is_local_module(cloudpickle)

    def test_clustrix_itself_is_never_embedded(self):
        """A checkout of clustrix in every payload would be pure weight."""
        import clustrix.utils

        assert not _is_local_module(clustrix.utils)

    def test_detection_finds_the_module_and_its_parent(self):
        found = {m.__name__ for m in _referenced_local_modules(uses_local_function)}
        assert {"mypkg", "mypkg.mathutils"} <= found

    def test_detection_reaches_instances_inside_containers(self):
        """Arguments arrive wrapped in the args tuple, one container deep."""
        found = {m.__name__ for m in _referenced_local_modules((Widget(1),))}
        assert "mypkg.mathutils" in found
