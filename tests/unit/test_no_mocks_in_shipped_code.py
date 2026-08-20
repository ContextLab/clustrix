#!/usr/bin/env python3
"""Shipped code must not know it is being tested.

Regression guard for issue #116.

``grep -rn "unittest.mock\\|MagicMock\\|isinstance(.*Mock" clustrix/`` is
empty today, and that emptiness *is* the resolution of the issue. Nothing
enforced it, though: the next person to reach for a test-only branch in
production code would have found no obstacle, and the property would have
been quietly lost between one release and the next.

The rule, from ``CLAUDE.md``: "Production code must never know it is being
tested. No ``isinstance(x, Mock)``, no test-only branches, no importable
module of fake widgets." A mock reaching shipped code is worse than a bad
test -- it means real users execute a branch that exists only to make a
test pass, and the tested path is not the shipped path.

This is deliberately shaped like ``tests/unit/test_no_autoadd_policy.py``,
including its lesson: a substring search checks a *spelling*, and spellings
are infinite. ``clustrix/notebook_magic.py`` has the word "mocks" in a
comment and ``clustrix/file_packaging.py`` lists ``"unittest"`` in a table
of standard-library module names -- a grep-based guard flags both, gets an
allowlist entry for each, and stops guarding anything. So the scan parses
each file and looks at what the code *does*:

* importing ``mock`` or ``unittest.mock``, however it is spelled -- plain
  import, aliased import, ``from`` import, or ``__import__``/
  ``importlib.import_module`` with the name as a string;
* naming any of the mock classes or factories, whether reached as
  ``MagicMock``, ``mock.MagicMock`` or ``unittest.mock.MagicMock`` -- which
  is what ``isinstance(x, Mock)`` reduces to;
* importing ``pytest``, or mentioning ``pytest``/``PYTEST_CURRENT_TEST`` in
  a string, which is how code sniffs at runtime whether a test is driving
  it.

``test_guard_catches_every_known_bypass`` plants each of those on disk and
requires the guard to report it, so the failure path is exercised rather
than assumed.
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The shipped package. Tests, scripts and docs may use whatever they like;
#: this guard is about what users install.
SHIPPED_PACKAGE = "clustrix"

#: Modules that exist only to fake things out. Reaching any of them from
#: shipped code is the violation, regardless of how the import is written.
MOCK_MODULES = frozenset({"mock", "unittest.mock"})

#: Names from those modules. ``isinstance(x, Mock)`` and
#: ``MagicMock(spec=...)`` both reduce to one of these, and so does the
#: dotted form, because only the last component is compared.
MOCK_NAMES = frozenset(
    {
        "Mock",
        "MagicMock",
        "NonCallableMock",
        "NonCallableMagicMock",
        "AsyncMock",
        "PropertyMock",
        "create_autospec",
        "mock_open",
    }
)

#: Test frameworks. Shipped code importing one means a code path exists for
#: the benefit of the suite rather than the user.
TEST_FRAMEWORK_MODULES = frozenset({"pytest", "_pytest"})

#: Strings that only appear in code sniffing for a test run. ``clustrix/``
#: contains none of them today, so this needs no allowlist -- unlike the
#: word "unittest", which is a legitimate standard-library module name and
#: is therefore deliberately *not* listed here.
TEST_SNIFF_STRINGS = frozenset({"pytest", "_pytest", "PYTEST_CURRENT_TEST"})

#: Functions that turn a module name in a string into a module object --
#: the way an import guard gets sidestepped.
DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module"})

_SKIP_DIR_PARTS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache"})


def _python_files(root):
    for path in sorted(root.rglob("*.py")):
        if _SKIP_DIR_PARTS & set(path.parts):
            continue
        yield path


def _last_component(node):
    """The final component of a possibly-dotted name, or ``None``.

    ``unittest.mock.MagicMock``, ``mock.MagicMock`` and a bare ``MagicMock``
    all reduce to ``"MagicMock"``, so the guard cannot be sidestepped by
    changing how the module is imported.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _module_root(dotted):
    """``"unittest.mock.patch"`` -> checked as both itself and ``unittest``."""
    return dotted.split(".")[0]


def _violations_in(rel, text):
    hits = []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:  # pragma: no cover - a broken file is a bug
        return [f"{rel}:{exc.lineno}: could not be parsed: {exc.msg}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in MOCK_MODULES:
                    hits.append(f"{rel}:{node.lineno}: imports {alias.name}")
                elif _module_root(alias.name) in TEST_FRAMEWORK_MODULES:
                    hits.append(f"{rel}:{node.lineno}: imports {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported = {f"{module}.{a.name}" if module else a.name for a in node.names}
            if module in MOCK_MODULES or imported & MOCK_MODULES:
                hits.append(f"{rel}:{node.lineno}: imports from {module or '.'}")
            elif _module_root(module) in TEST_FRAMEWORK_MODULES:
                hits.append(f"{rel}:{node.lineno}: imports from {module}")

        elif isinstance(node, ast.Call):
            name = _last_component(node.func)
            if name in DYNAMIC_IMPORTERS:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value in MOCK_MODULES:
                        hits.append(
                            f"{rel}:{node.lineno}: imports {arg.value} dynamically"
                        )

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in TEST_SNIFF_STRINGS:
                hits.append(
                    f"{rel}:{node.lineno}: mentions {node.value!r}, which only "
                    "appears in code detecting a test run"
                )

        name = (
            _last_component(node)
            if isinstance(node, (ast.Name, ast.Attribute))
            else None
        )
        if name in MOCK_NAMES:
            hits.append(f"{rel}:{node.lineno}: names {name}")

    return sorted(set(hits))


def _violations(package_root):
    hits = []
    for path in _python_files(package_root):
        rel = path.relative_to(package_root.parent).as_posix()
        hits.extend(
            _violations_in(rel, path.read_text(encoding="utf-8", errors="replace"))
        )
    return sorted(hits)


def test_the_scan_actually_reads_the_package():
    """A guard that inspects nothing would pass forever.

    If the package moved or the glob broke, the real check below would go
    green while checking zero files, which reads as coverage. Pin that the
    scan sees a plausible number of real modules including a couple that
    must always be there.
    """
    seen = {p.name for p in _python_files(REPO_ROOT / SHIPPED_PACKAGE)}
    assert len(seen) > 20, f"only {len(seen)} files scanned: {sorted(seen)}"
    assert {"config.py", "utils.py", "executor_core.py"} <= seen


def test_no_mock_or_test_framework_use_in_shipped_code():
    hits = _violations(REPO_ROOT / SHIPPED_PACKAGE)
    assert not hits, (
        f"{SHIPPED_PACKAGE}/ is what users install, and it must not know it "
        "is being tested (issue #116). A mock or test-only branch here means "
        "real users execute a path that exists only to make a test pass, so "
        "the tested path is not the shipped path. Move the fake into the "
        "test, or make the real thing injectable. Offending lines:\n  "
        + "\n  ".join(hits)
    )


#: Each of these is a real way to get a mock into shipped code. Several
#: defeat a plain grep for ``unittest.mock`` or ``MagicMock``.
BYPASSES = {
    "from_import": (
        "from unittest.mock import MagicMock\n"
        "\n"
        "\n"
        "def client():\n"
        "    return MagicMock()\n"
    ),
    "aliased_module_import": (
        "import unittest.mock as _m\n"
        "\n"
        "\n"
        "def client():\n"
        "    return _m.MagicMock()\n"
    ),
    "submodule_from_import": (
        "from unittest import mock\n"
        "\n"
        "\n"
        "def client():\n"
        "    return mock.Mock()\n"
    ),
    "third_party_mock": (
        "import mock\n" "\n" "\n" "def client():\n" "    return mock.Mock()\n"
    ),
    "isinstance_check": (
        "from unittest import mock\n"
        "\n"
        "\n"
        "def submit(connection):\n"
        "    if isinstance(connection, mock.Mock):\n"
        "        return 'fake-job-id'\n"
        "    return connection.submit()\n"
    ),
    "dynamic_import": (
        "import importlib\n"
        "\n"
        "\n"
        "def client():\n"
        "    return importlib.import_module('unittest.mock').MagicMock()\n"
    ),
    "dunder_import": (
        "def client():\n"
        "    return __import__('unittest.mock', fromlist=['MagicMock'])\n"
    ),
    "pytest_import": (
        "import pytest\n" "\n" "\n" "def submit():\n" "    pytest.skip('no cluster')\n"
    ),
    "runtime_test_sniff": (
        "import sys\n"
        "\n"
        "\n"
        "def submit():\n"
        "    if 'pytest' in sys.modules:\n"
        "        return 'fake-job-id'\n"
        "    return _really_submit()\n"
    ),
    "env_var_sniff": (
        "import os\n"
        "\n"
        "\n"
        "def submit():\n"
        "    if os.environ.get('PYTEST_CURRENT_TEST'):\n"
        "        return 'fake-job-id'\n"
        "    return _really_submit()\n"
    ),
}


@pytest.mark.parametrize("name", sorted(BYPASSES))
def test_guard_catches_every_known_bypass(name, tmp_path):
    """Plant each bypass as a real file and prove the guard reports it."""
    package = tmp_path / SHIPPED_PACKAGE
    package.mkdir()
    (package / "sneaky_new_backend.py").write_text(BYPASSES[name], encoding="utf-8")

    hits = _violations(package)
    assert hits, f"bypass {name!r} was not caught"
    assert all(h.startswith(f"{SHIPPED_PACKAGE}/sneaky_new_backend.py:") for h in hits)

    with pytest.raises(AssertionError):
        assert not hits, "planted violation must trip the same assertion"


#: Things that look like violations to a grep and are not. Every one of
#: these is drawn from code that really is in ``clustrix/``.
INNOCENT = {
    # clustrix/notebook_magic.py:83 -- a comment, not a mock.
    "comment_mentions_mocks": (
        "def render(widgets):\n"
        "    # IPython components (may be mocks)\n"
        "    return widgets\n"
    ),
    # clustrix/notebook_magic_fallback.py:126 -- a docstring saying the
    # opposite of a violation.
    "docstring_disclaims_mocks": (
        "def stub():\n"
        '    """Unlike a mock, it does not pretend the call succeeded."""\n'
        "    raise RuntimeError('ipywidgets is not installed')\n"
    ),
    # clustrix/file_packaging.py:606 -- "unittest" as a stdlib module name
    # in a data table. Listing it is not importing it.
    "stdlib_name_in_a_table": (
        "STDLIB_MODULES = [\n" '    "unittest",\n' '    "json",\n' "]\n"
    ),
    # clustrix/auth_fallbacks.py:24 -- sys.modules is inspected for real
    # reasons; only a test framework in there is a violation.
    "sys_modules_for_a_real_reason": (
        "import sys\n"
        "\n"
        "\n"
        "def in_colab():\n"
        "    return 'google.colab' in sys.modules\n"
    ),
    # A user-facing attribute that merely ends in a mock-ish word.
    "unrelated_identifier": (
        "class JobMocker:\n"
        "    def mock_up_a_plan(self):\n"
        "        return {'cores': 4}\n"
    ),
}


@pytest.mark.parametrize("name", sorted(INNOCENT))
def test_guard_is_quiet_about_legitimate_code(name, tmp_path):
    """Flagging real code is how a guard gets an allowlist and dies."""
    package = tmp_path / SHIPPED_PACKAGE
    package.mkdir()
    (package / "well_behaved.py").write_text(INNOCENT[name], encoding="utf-8")

    assert _violations(package) == []
