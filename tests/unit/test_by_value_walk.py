"""Regression tests for the by-value walk that decides what travels with a job.

Every test here builds a REAL package on disk, serializes a REAL function
through :func:`clustrix.utils.serialize_function`, and deserializes it in a
REAL fresh interpreter that cannot see that package. The worker refuses to run
unless ``importlib.util.find_spec`` proves the package is unimportable, so a
test that passes proves the payload was self-contained rather than proving the
worker happened to have the code on its path.
"""

import os
import subprocess
import sys
import textwrap

import pytest

import clustrix
from clustrix.utils import (
    WalkTooLargeError,
    _referenced_local_modules,
    _walk_referenced_modules,
    serialize_function,
)

#: The checkout the worker must import clustrix from -- clustrix/__init__.py ->
#: clustrix/ -> the repository root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(clustrix.__file__)))

WORKER_SOURCE = textwrap.dedent('''
    """Load a clustrix payload in an interpreter that cannot import the project."""
    import importlib.util
    import pickle
    import sys

    repo_root, payload_path, forbidden = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, repo_root)

    for name in [n for n in forbidden.split(",") if n]:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is not None:
            sys.exit(
                "HARNESS BROKEN: %r is importable in the worker (%r)" % (name, spec)
            )

    from clustrix.utils import deserialize_function

    with open(payload_path, "rb") as handle:
        func, args, kwargs = deserialize_function(pickle.loads(handle.read()))
    sys.stdout.write("RESULT:" + repr(func(*args, **kwargs)))
    ''')


@pytest.fixture
def project(tmp_path):
    """A throwaway project directory that is importable only inside the test."""

    class Project:
        def __init__(self, root):
            self.root = root
            self.package_names = []

        def add_package(self, name, body, namespace=False):
            """Create a real importable package and remember it is project-local."""
            package_dir = self.root / name
            package_dir.mkdir(parents=True, exist_ok=True)
            if namespace:
                # PEP 420: no __init__.py at all.
                (package_dir / "mod.py").write_text(textwrap.dedent(body))
            else:
                (package_dir / "__init__.py").write_text(textwrap.dedent(body))
            self.package_names.append(name)
            return package_dir

        def load(self, dotted):
            import importlib

            return importlib.import_module(dotted)

    root = tmp_path / "project"
    root.mkdir()
    sys.path.insert(0, str(root))
    project = Project(root)
    try:
        yield project
    finally:
        sys.path.remove(str(root))
        for name in list(sys.modules):
            if any(
                name == pkg or name.startswith(pkg + ".")
                for pkg in project.package_names
            ):
                del sys.modules[name]


def run_in_fresh_interpreter(tmp_path, payload, forbidden):
    """Deserialize `payload` where `forbidden` packages provably cannot be imported."""
    import pickle

    worker = tmp_path / "worker.py"
    worker.write_text(WORKER_SOURCE)
    payload_path = tmp_path / "payload.pkl"
    payload_path.write_bytes(pickle.dumps(payload))

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(worker),
            REPO_ROOT,
            str(payload_path),
            ",".join(forbidden),
        ],
        capture_output=True,
        text=True,
        cwd=str(elsewhere),
        env=env,
    )
    assert "HARNESS BROKEN" not in (result.stdout + result.stderr), (
        result.stdout + result.stderr
    )
    assert result.returncode == 0, (
        "worker failed:\n" + result.stdout + "\n" + result.stderr
    )
    assert result.stdout.startswith("RESULT:"), result.stdout
    return result.stdout[len("RESULT:") :]


# --------------------------------------------------------------------------
# D2: the walk must not degrade to by-reference when the argument graph is big
# --------------------------------------------------------------------------


def test_large_argument_graph_still_ships_local_class(project, tmp_path):
    """30k filler nodes must not push the local class out of the payload."""
    project.add_package(
        "bigwalkpkg",
        """
        class Point:
            def __init__(self, value):
                self.value = value

            def doubled(self):
                return self.value * 2
        """,
    )
    bigwalkpkg = project.load("bigwalkpkg")

    def first_point_doubled(items):
        return items[0].doubled()

    payload = [bigwalkpkg.Point(7)] + [[] for _ in range(30000)]
    serialized = serialize_function(first_point_doubled, (payload,), {})

    assert run_in_fresh_interpreter(tmp_path, serialized, ["bigwalkpkg"]) == "14"


def test_oversized_argument_graph_is_refused_not_truncated(
    project, tmp_path, monkeypatch
):
    """Past the ceiling clustrix refuses to submit instead of shipping a dud."""
    project.add_package(
        "ceilingpkg",
        """
        class Point:
            def __init__(self, value):
                self.value = value
        """,
    )
    ceilingpkg = project.load("ceilingpkg")

    def read(items):
        return items[0].value

    monkeypatch.setattr("clustrix.utils._MAX_WALK_NODES", 50)
    payload = [[] for _ in range(500)] + [ceilingpkg.Point(1)]

    with pytest.raises(WalkTooLargeError) as excinfo:
        serialize_function(read, (payload,), {})
    assert "will not submit" in str(excinfo.value)


# --------------------------------------------------------------------------
# D3: instance attributes must be walked
# --------------------------------------------------------------------------


def test_local_instance_held_only_in_an_attribute(project, tmp_path):
    """A wrapper object holding a project-local instance must still ship it."""
    project.add_package(
        "attrpkg",
        """
        class Inner:
            def __init__(self, value):
                self.value = value

            def shout(self):
                return "inner-%d" % self.value
        """,
    )
    attrpkg = project.load("attrpkg")

    class Wrapper:  # defined in this test module, not in attrpkg
        def __init__(self, inner):
            self.inner = inner

    def read_wrapper(wrapper):
        return wrapper.inner.shout()

    serialized = serialize_function(read_wrapper, (Wrapper(attrpkg.Inner(4)),), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["attrpkg"]) == "'inner-4'"


def test_local_instance_held_only_in_a_slot(project, tmp_path):
    """__slots__ hides attributes from __dict__; the walk must read them too."""
    project.add_package(
        "slotpkg",
        """
        class Inner:
            def __init__(self, value):
                self.value = value

            def shout(self):
                return "slot-%d" % self.value
        """,
    )
    slotpkg = project.load("slotpkg")

    class SlottedWrapper:
        __slots__ = ("inner",)

        def __init__(self, inner):
            self.inner = inner

    def read_slot(wrapper):
        return wrapper.inner.shout()

    serialized = serialize_function(read_slot, (SlottedWrapper(slotpkg.Inner(9)),), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["slotpkg"]) == "'slot-9'"


# --------------------------------------------------------------------------
# D4: local classes that subclass builtin containers
# --------------------------------------------------------------------------


def test_local_dict_subclass_travels(project, tmp_path):
    project.add_package(
        "dictsubpkg",
        """
        class ConfigDict(dict):
            def scale(self):
                return sum(self.values()) * 10
        """,
    )
    dictsubpkg = project.load("dictsubpkg")

    def use_config(config):
        return config.scale()

    serialized = serialize_function(use_config, (dictsubpkg.ConfigDict(a=1, b=2),), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["dictsubpkg"]) == "30"


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason=(
        "clustrix requires Python >= 3.10 (pyproject requires-python). On 3.9 "
        "cloudpickle cannot rebuild a typing.NamedTuple subclass by value at "
        "all -- typing.NamedTupleMeta raises KeyError('__module__') -- which is "
        "an interpreter limitation, not a walk defect. Verified working on 3.11."
    ),
)
def test_local_named_tuple_keeps_its_methods(project, tmp_path):
    """The silent-corruption case: a NamedTuple arrived stripped of its methods."""
    project.add_package(
        "ntpkg",
        """
        from typing import NamedTuple


        class Sample(NamedTuple):
            left: int
            right: int

            def combined(self):
                return self.left * 100 + self.right
        """,
    )
    ntpkg = project.load("ntpkg")

    def use_sample(sample):
        return sample.combined()

    serialized = serialize_function(use_sample, (ntpkg.Sample(3, 4),), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["ntpkg"]) == "304"


def test_local_list_subclass_travels(project, tmp_path):
    project.add_package(
        "listsubpkg",
        """
        class Stack(list):
            def total(self):
                return sum(self)
        """,
    )
    listsubpkg = project.load("listsubpkg")

    def use_stack(stack):
        return stack.total()

    serialized = serialize_function(use_stack, (listsubpkg.Stack([1, 2, 3]),), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["listsubpkg"]) == "6"


# --------------------------------------------------------------------------
# D5: PEP 420 namespace packages
# --------------------------------------------------------------------------


def test_namespace_package_is_local(project, tmp_path):
    project.add_package(
        "nspkg",
        """
        def triple(value):
            return value * 3
        """,
        namespace=True,
    )
    nspkg_mod = project.load("nspkg.mod")
    nspkg = sys.modules["nspkg"]

    assert nspkg.__file__ is None, "fixture is not a namespace package"
    found, _ = _walk_referenced_modules(nspkg_mod)
    assert "nspkg" in found and "nspkg.mod" in found

    def use_namespace(value):
        return nspkg_mod.triple(value)

    serialized = serialize_function(use_namespace, (2,), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["nspkg"]) == "6"


# --------------------------------------------------------------------------
# D6: functools.partial over a local function
# --------------------------------------------------------------------------


def test_functools_partial_over_local_function(project, tmp_path):
    import functools

    project.add_package(
        "partialpkg",
        """
        def multiply(left, right):
            return left * right
        """,
    )
    partialpkg = project.load("partialpkg")

    def call(bound):
        return bound()

    bound = functools.partial(partialpkg.multiply, 6, right=7)
    assert [m.__name__ for m in _referenced_local_modules(bound)] == ["partialpkg"]

    serialized = serialize_function(call, (bound,), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["partialpkg"]) == "42"


def test_bound_method_of_local_class(project, tmp_path):
    project.add_package(
        "methodpkg",
        """
        class Counter:
            def __init__(self, start):
                self.start = start

            def bump(self):
                return self.start + 1
        """,
    )
    methodpkg = project.load("methodpkg")

    def call(bound):
        return bound()

    serialized = serialize_function(call, (methodpkg.Counter(10).bump,), {})
    assert run_in_fresh_interpreter(tmp_path, serialized, ["methodpkg"]) == "11"


# --------------------------------------------------------------------------
# D7: the unpicklable-object message must describe what was actually found
# --------------------------------------------------------------------------


def test_closure_held_lock_is_reported_as_a_closure(project):
    """The old text blamed module level and told the user to do what they did."""
    project.add_package(
        "closurepkg",
        """
        import threading


        def make_worker():
            lock = threading.Lock()

            def work():
                with lock:
                    return 1

            return work
        """,
    )
    closurepkg = project.load("closurepkg")
    worker = closurepkg.make_worker()

    with pytest.raises(RuntimeError) as excinfo:
        serialize_function(worker, (), {})

    message = str(excinfo.value)
    assert "closure variable 'lock'" in message, message
    assert "make_worker" in message or "work()" in message, message
    assert "held at module level" not in message, message
    assert "Move it inside a function" not in message, message


def test_module_level_lock_is_reported_at_module_level(project):
    project.add_package(
        "modlockpkg",
        """
        import threading

        LOCK = threading.Lock()


        def work():
            with LOCK:
                return 2
        """,
    )
    modlockpkg = project.load("modlockpkg")

    with pytest.raises(RuntimeError) as excinfo:
        serialize_function(modlockpkg.work, (), {})

    message = str(excinfo.value)
    assert "module-level name 'LOCK'" in message, message
    assert "_thread.lock" in message or "lock" in message, message
