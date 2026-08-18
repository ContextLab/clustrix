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


class TestFailuresAreLoudNotSilent:
    """The worst outcome is a payload that looks fine and fails on the cluster."""

    def test_an_unembeddable_module_raises_here_not_there(self, tmp_path, monkeypatch):
        """Some objects cannot be embedded at all, and that must be said now.

        A function that takes a module-level lock cannot be serialized by
        value. The attempt raised, and the code fell through to a dill payload
        that referenced the module by name -- so the user saw
        ModuleNotFoundError minutes later, on the cluster, naming a module
        sitting on their own disk. It must fail here, with the real reason.

        (A lock the function does not touch is fine: cloudpickle embeds only
        what is actually referenced.)
        """
        package = tmp_path / "lockpkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "util.py").write_text("import threading\nLOCK = threading.Lock()\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        from lockpkg.util import LOCK

        def guarded(x):
            with LOCK:
                return x * 2

        with pytest.raises(RuntimeError, match="Cannot send your local module"):
            serialize_function(guarded, (1,), {})

    def test_an_unreferenced_unpicklable_object_is_not_a_problem(
        self, tmp_path, monkeypatch
    ):
        """Embedding is per-object, so a bystander lock must not block a job."""
        package = tmp_path / "okpkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "util.py").write_text(
            "import threading\nLOCK = threading.Lock()\n\ndef helper(x):\n    return x + 1\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        from okpkg.util import helper

        def uses_helper(x):
            return helper(x)

        assert serialize_function(uses_helper, (1,), {})["function"]


class TestTheWalkIsBounded:
    """This runs on every submission, so it must not be pathological."""

    def test_a_million_element_argument_is_cheap(self):
        """Scalars cannot reference a module, so they are never enqueued."""
        import time

        payload = list(range(1_000_000))
        start = time.time()
        _referenced_local_modules((payload,))
        assert time.time() - start < 1.0

    def test_a_self_referential_argument_terminates(self):
        cycle: list = []
        cycle.append(cycle)
        assert _referenced_local_modules((cycle,)) == []

    def test_mutually_referencing_containers_terminate(self):
        left: dict = {}
        right = {"left": left}
        left["right"] = right
        assert _referenced_local_modules((left,)) == []


class TestRegistryHygiene:
    """cloudpickle's by-value registry is process-global state."""

    def test_the_registry_is_left_as_it_was_found(self):
        import cloudpickle

        before = set(cloudpickle.list_registry_pickle_by_value())
        serialize_function(uses_local_function, (4,), {})
        assert set(cloudpickle.list_registry_pickle_by_value()) == before

    def test_a_users_own_registration_survives(self):
        """Unregistering the user's module would change how their code pickles."""
        import cloudpickle

        import mypkg.mathutils

        cloudpickle.register_pickle_by_value(mypkg.mathutils)
        try:
            serialize_function(uses_local_function, (4,), {})
            registry = {
                getattr(m, "__name__", m)
                for m in cloudpickle.list_registry_pickle_by_value()
            }
            assert "mypkg.mathutils" in registry
        finally:
            cloudpickle.unregister_pickle_by_value(mypkg.mathutils)

    def test_concurrent_serialization_keeps_modules_embedded(self):
        """Two threads racing the registry used to hand one of them a
        by-reference payload that failed remotely."""
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            sizes = [
                len(f.result()["function"])
                for f in [
                    pool.submit(serialize_function, uses_local_function, (4,), {})
                    for _ in range(5)
                ]
            ]
        # An embedded module is several hundred bytes; a bare reference is ~300.
        assert min(sizes) == max(sizes), f"payload sizes diverged: {sizes}"
        assert min(sizes) > 500, f"payload dropped to by-reference: {sizes}"
