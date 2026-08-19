#!/usr/bin/env python3
"""``_execute_single`` must ship the user's function and return the user's answer.

This is the regression suite for the worst defect the repo has had.

``clustrix.decorator._execute_single`` used to run every function through
``analyze_function_complexity`` and, when that said "complex", replace it with
something else before serialising:

* ``auto_flatten_if_needed`` -- a source rewriter, or
* ``create_simple_subprocess_fallback`` -- a closure that shelled out to a
  hardcoded script whose entire body was ``result = "Function execution
  completed"``.

The second one never ran the user's function at all, so clustrix returned the
string ``'Function execution completed'`` as the job's answer, with no error.
It was reached whenever flattening failed -- and flattening failed for exactly
the functions whose source ``inspect.getsource`` cannot recover (REPL,
notebook cells, ``exec``-created), because the complexity analyser's except
branch reported ``is_complex: True``.

Nothing here is mocked. ``SubprocessJobRunner`` is a genuine, minimal
implementation of the two-method contract ``_execute_single`` uses: it writes
the exact bytes ``serialize_function`` produced to disk and runs them in a
fresh Python interpreter with the real ``deserialize_function``, calling the
recovered function for real. That is the remote worker's contract, executed
locally, so a pass here means the payload clustrix actually ships computes the
caller's answer.
"""

import os
import pickle
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import clustrix
from clustrix.decorator import _execute_single

REPO_ROOT = str(Path(clustrix.__file__).resolve().parent.parent)

# What the deleted fallback fabricated. If this string ever comes back as a
# result, the defect is back.
FABRICATED_RESULT = "Function execution completed"

# Pickle is clustrix's own wire format: serialize_function emits a dict of
# pickled bytes and the remote worker unpickles it. Reproducing that faithfully
# is the point of this suite. The only data unpickled here is data this test
# wrote moments earlier into a private temporary directory, so there is no
# untrusted input anywhere in the loop.
WORKER = textwrap.dedent("""
    import pickle, sys
    from clustrix.utils import deserialize_function

    with open(sys.argv[1], "rb") as fh:
        payload = pickle.load(fh)

    func, args, kwargs = deserialize_function(payload)
    result = func(*args, **kwargs)

    with open(sys.argv[2], "wb") as fh:
        pickle.dump(result, fh)
    """)


class SubprocessJobRunner:
    """A real executor: serialises to disk, runs in a fresh interpreter.

    Implements only what ``_execute_single`` calls -- ``submit_job`` and
    ``wait_for_result`` -- and implements them for real. ``submitted`` keeps
    every payload it was handed so a test can inspect what was actually put on
    the wire.
    """

    def __init__(self, workdir):
        self.workdir = workdir
        self.submitted = []
        self._jobs = {}

    def submit_job(self, func_data, job_config):
        job_id = f"job{len(self.submitted)}"
        self.submitted.append(func_data)

        payload_path = os.path.join(self.workdir, f"{job_id}.payload")
        with open(payload_path, "wb") as fh:
            pickle.dump(func_data, fh)
        self._jobs[job_id] = payload_path
        return job_id

    def wait_for_result(self, job_id):
        payload_path = self._jobs[job_id]
        result_path = payload_path + ".result"
        worker_path = payload_path + ".worker.py"
        with open(worker_path, "w") as fh:
            fh.write(WORKER)

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [REPO_ROOT] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
        completed = subprocess.run(
            [sys.executable, worker_path, payload_path, result_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
            timeout=300,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "worker failed:\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        with open(result_path, "rb") as fh:
            return pickle.load(fh)


@pytest.fixture
def runner():
    with tempfile.TemporaryDirectory() as tmp:
        yield SubprocessJobRunner(tmp)


# ---------------------------------------------------------------------------
# The functions under test. These are the categories that used to be
# misclassified as "complex" and substituted away.
# ---------------------------------------------------------------------------

MODULE_CONSTANT = 7


def module_helper(value):
    return value * 3


def make_exec_created_add():
    """A function with no retrievable source -- the original reproduction.

    ``inspect.getsource`` cannot recover this, exactly as for a function typed
    into the REPL or defined in a notebook cell.
    """
    namespace = {}
    exec("def add(a, b):\n    return a + b\n", namespace)
    return namespace["add"]


def make_exec_created_zero_arg():
    """Source-less *and* zero-argument: where the fabrication was silent.

    The deleted stub returned a closure ``simple_fallback()`` taking no
    arguments. Called with arguments it at least blew up with a TypeError;
    called with none -- an ordinary ``@cluster def compute(): ...`` -- it ran
    happily and handed back the string ``'Function execution completed'`` as
    the job's answer.
    """
    namespace = {}
    exec("def compute():\n    return 6 * 7\n", namespace)
    return namespace["compute"]


def nested_helper_function(x, y, z=42):
    """One ordinary nested helper -- enough to be classed complex."""

    def inner_add(a, b):
        return a + b

    return inner_add(x, y) + z


def doubly_nested(n):
    def outer(value):
        def inner(w):
            return w * w

        return inner(value) + MODULE_CONSTANT

    return sum(outer(i) for i in range(n))


def reads_module_globals(n):
    return module_helper(n) + MODULE_CONSTANT


def make_closure(multiplier):
    def uses_closure(x):
        def inner(y):
            return y * multiplier

        return inner(x) + multiplier

    return uses_closure


CASES = [
    ("exec_created_no_source", make_exec_created_add(), (2, 3), {}, 5),
    ("exec_created_zero_arg", make_exec_created_zero_arg(), (), {}, 42),
    ("nested_helper", nested_helper_function, (1, 2), {}, 45),
    ("nested_helper_kwargs", nested_helper_function, (1, 2), {"z": 100}, 103),
    ("doubly_nested", doubly_nested, (5,), {}, 65),
    ("module_globals", reads_module_globals, (4,), {}, 19),
    ("closure", make_closure(10), (3,), {}, 40),
]


@pytest.mark.parametrize(
    "label,func,args,kwargs,expected",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_execute_single_returns_the_users_answer(
    runner, label, func, args, kwargs, expected
):
    """The value that comes back is what the function computes. Really runs it."""
    # Sanity: the expectation matches what the function does locally, so a
    # failure below is about shipping it, not about the arithmetic.
    assert func(*args, **kwargs) == expected

    result = _execute_single(runner, func, args, kwargs, job_config={})

    assert result == expected, (
        f"{label}: clustrix returned {result!r} but the function computes "
        f"{expected!r}"
    )
    assert (
        result != FABRICATED_RESULT
    ), f"{label}: clustrix fabricated a result instead of running the function"


@pytest.mark.parametrize(
    "label,func,args,kwargs,expected",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_execute_single_ships_the_original_function(
    runner, label, func, args, kwargs, expected
):
    """No substitute is put on the wire -- the payload names the user's function.

    ``create_simple_subprocess_fallback`` returned a closure named
    ``simple_fallback``, and the basic flattener returned one named
    ``<name>_flattened``. Either name appearing here means a substitution
    happened.
    """
    _execute_single(runner, func, args, kwargs, job_config={})

    assert len(runner.submitted) == 1
    shipped_name = runner.submitted[0]["func_info"]["name"]

    assert shipped_name == func.__name__, (
        f"{label}: clustrix serialised {shipped_name!r} instead of "
        f"{func.__name__!r}"
    )
    assert shipped_name != "simple_fallback"
    assert not shipped_name.endswith("_flattened")


def test_execute_single_propagates_errors_instead_of_fabricating(runner):
    """A function that raises must surface as a failure, not a canned string."""

    def explodes(x):
        raise ValueError(f"boom: {x}")

    with pytest.raises(RuntimeError) as excinfo:
        _execute_single(runner, explodes, (1,), {}, job_config={})

    assert "boom: 1" in str(excinfo.value)


def test_no_module_assigns_a_canned_value_to_result():
    """No clustrix module may assign a hardcoded string literal to ``result``.

    That assignment -- ``result = "Function execution completed"`` -- was the
    whole body of the deleted stub's subprocess script, and it is the shape any
    revival of the bug would take.

    Matched on the assignment, not on the phrase. ``clustrix/utils.py`` prints
    ``'Function execution completed successfully'`` in the two-venv job script,
    which is a progress log emitted *after* ``result = func(*args, **kwargs)``
    has really run; the value it pickles is the function's. That line is fine
    and must not be flagged.
    """
    package_dir = Path(clustrix.__file__).resolve().parent
    canned_assignment = re.compile(r"""^\s*result\s*=\s*['"][^'"]*['"]\s*$""")

    offenders = []
    for path in sorted(package_dir.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if canned_assignment.match(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert offenders == [], f"a canned result is assigned at: {offenders}"


def test_the_source_rewriting_machinery_no_longer_exists():
    """The modules are deleted, not merely unreferenced.

    ``create_simple_subprocess_fallback`` lived in
    ``clustrix.function_flattening``, which together with
    ``clustrix.dependency_resolution`` has been removed: neither generator
    ever produced code that ran, and ``serialize_function`` already covers
    every case they were meant to rescue. Importing them must fail.
    """
    import importlib

    for name in (
        "clustrix.function_flattening",
        "clustrix.dependency_resolution",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_decorator_does_not_reach_for_the_flattener():
    """The execution path must not import the source-rewriting machinery.

    Substituting a rewritten function into a job submission cannot be made
    safe: equivalence is unverifiable without running the user's function. The
    import being absent is the structural guarantee that nobody re-adds the
    substitution by accident.
    """
    import clustrix.decorator as decorator_module

    source = Path(decorator_module.__file__).read_text()
    code_lines = [
        line
        for line in source.splitlines()
        if ("function_flattening" in line or "dependency_resolution" in line)
        and not line.lstrip().startswith("#")
    ]
    assert code_lines == [], f"decorator.py still references flattening: {code_lines}"

    for name in (
        "analyze_function_complexity",
        "auto_flatten_if_needed",
        "create_simple_subprocess_fallback",
    ):
        assert not hasattr(
            decorator_module, name
        ), f"clustrix.decorator still exposes {name}"
