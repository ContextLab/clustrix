#!/usr/bin/env python3
"""Shipped code must not consult test machinery.

Regression guard for issue #116.

THE RULE THIS TEST ENFORCES -- this docstring is the specification, and
nothing outside it is claimed:

1. ``clustrix/`` must not import a mocking library or a test framework
   (``mock``, ``unittest.mock``, ``pytest``, ``_pytest``), by any import
   statement, however aliased.
2. It must not reach the same modules through ``__import__`` or
   ``importlib.import_module`` when the module name can be worked out by
   reading the source -- a string literal, an implicit or ``+``
   concatenation of literals, or a variable assigned one of those.
3. It must not ask the interpreter whether a test framework is loaded.
   ``sys.modules`` may not be subscripted at all, and a membership test or
   ``.get()`` against it whose key is knowable must not name a test
   framework or a mocking library.
4. It must not read ``sys.argv``. A library does not inspect the process
   command line; the CLI receives its arguments from click.
5. It must read only the environment variables in ``ALLOWED_ENV`` below.
   That list is a positive allowlist: adding to it is a deliberate,
   reviewable act, which is exactly what ``CLUSTRIX_TEST_MODE`` or
   ``PYTEST_CURRENT_TEST`` would need.

WHAT IT DELIBERATELY DOES NOT CLAIM. Every rule above stops at what the
parser can work out from the source, and three of them have doors that
must stay open because real code in this package needs them:

* ``__import__(module_name)`` with a name computed at runtime is
  *permitted*: ``dependency_analysis.py``, ``file_packaging.py`` and
  ``utils.py`` import the user's own modules by name to replicate their
  environment. Flagging every dynamic import, as one review suggested,
  false-positives on all three.
* ``sys.modules.get(module_name)`` with a computed name is likewise
  permitted -- ``utils.py`` and ``file_packaging.py`` do it four times for
  the same reason. Only *subscripting* is banned, which nothing does.
* ``os.environ.get(self.config.password_env_var)`` reads a variable the
  user names in their config, so the allowlist cannot see it. That is the
  documented credential channel (see ``CLAUDE.md``), not a leak in the
  rule.

Anything that hides a name from the parser -- ``"".join([...])``,
``chr(112) + "ytest"``, a name arriving as a function argument -- is
outside all five rules. No AST guard can close that, and pretending
otherwise is worse than saying so.

WHY IT IS NOT A SEARCH FOR THE WORD "MOCK". The previous version of this
file flagged any of ``Mock``/``MagicMock``/``create_autospec`` appearing
as an identifier, and any string equal to ``"pytest"``. Both are spelling
checks, and spellings are infinite in one direction and shared with
innocent code in the other: ``DEV_EXTRAS = ["pytest", ...]``, a pip-freeze
filter ``SKIP = {"pytest", "_pytest"}`` in environment replication, and
``raise RuntimeError("pytest")`` are all legitimate and all would have
been flagged. The first allowlist entry added to quiet one of those kills
the guard. Naming the mock classes is also unnecessary: ``import
unittest`` alone does not expose ``unittest.mock`` (verified -- it raises
``AttributeError``), so a mock object cannot be obtained without an import
that rule 1 or rule 2 already sees.

``test_guard_catches_every_known_bypass`` plants each bypass into a copy
of the *real* package on disk and requires the guard to report it, so the
failure path is exercised against real modules rather than against a list
this file also reads.
"""

import ast
import pathlib
import shutil

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The shipped package. Tests, scripts and docs may use whatever they like;
#: this guard is about what users install.
SHIPPED_PACKAGE = "clustrix"

#: Modules that exist only to fake things out, and the test frameworks
#: whose presence shipped code must never react to.
FORBIDDEN_MODULES = frozenset({"mock", "unittest.mock", "pytest", "_pytest"})

#: Functions that turn a module name in a string into a module object.
DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module"})

#: Every environment variable ``clustrix/`` is allowed to read by name.
#: Two of these are the config channels documented in ``CLAUDE.md``
#: (``CLUSTRIX_CONFIG_DIR`` and whatever ``password_env_var`` points at);
#: the rest are credentials and cluster coordinates. A new entry here is a
#: deliberate decision, which is the point: ``CLUSTRIX_TEST_MODE`` cannot
#: arrive by accident.
ALLOWED_ENV = frozenset(
    {
        "CLUSTER_PASSWORD",
        "CLUSTRIX_AUTO_WIDGET",
        "CLUSTRIX_CONFIG_DIR",
        "CLUSTRIX_DEFAULT_PASSWORD",
        "CLUSTRIX_VALIDATION_SLURM_HOST",
        "CLUSTRIX_VALIDATION_SLURM_NAME",
        "CLUSTRIX_VALIDATION_SSH_HOST",
        "CLUSTRIX_VALIDATION_SSH_NAME",
        "EDITOR",
        "GITHUB_ACTIONS",
        "HF_HOME",
        "HF_TOKEN",
        "HF_USERNAME",
        "HUGGINGFACE_TOKEN",
        "HUGGINGFACE_USERNAME",
        "SSH_HOST",
        "SSH_PASSWORD",
        "SSH_PORT",
        "SSH_PRIVATE_KEY_PATH",
        "SSH_USERNAME",
        "USER",
    }
)

_SKIP_DIR_PARTS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache"})


def _python_files(root):
    for path in sorted(root.rglob("*.py")):
        if _SKIP_DIR_PARTS & set(path.parts):
            continue
        yield path


def _fold(node, names):
    """The string ``node`` evaluates to, or ``None`` if it is not knowable.

    Constant folding is what makes the rules resistant to spelling games
    without being a substring search: ``"unittest" ".mock"``,
    ``"py" + "test"`` and ``_M = "unittest.mock"`` all reduce to the name
    they denote. ``names`` maps identifiers to the strings assigned to them
    anywhere in the file, which is deliberately scope-blind: over-reading
    an assignment can only make the guard notice more, never less.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold(node.left, names)
        right = _fold(node.right, names)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name):
        bound = names.get(node.id)
        return bound[0] if bound and len(bound) == 1 else None
    return None


def _fold_all(node, names):
    """Every string ``node`` might be, for a name bound to several."""
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    folded = _fold(node, names)
    return [folded] if folded is not None else []


def _string_bindings(tree):
    """``{identifier: [strings assigned to it]}`` for the whole file.

    Handles both ``NAME = "literal"`` and the list-of-names-then-loop shape
    ``env_vars = ["A", "B"]`` / ``for var in env_vars: os.getenv(var)``,
    which is how ``auth_fallbacks.py`` really reads its variables.
    """
    names: dict = {}

    def record(target, value):
        if not isinstance(target, ast.Name):
            return
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            found = [_fold(elt, {}) for elt in value.elts]
        else:
            found = [_fold(value, {})]
        kept = [f for f in found if f is not None]
        if kept:
            names.setdefault(target.id, []).extend(kept)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                record(target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            record(node.target, node.value)
        elif isinstance(node, ast.For):
            # ``for var in env_vars:`` -- var takes each of env_vars' values.
            if isinstance(node.target, ast.Name):
                for value in _fold_all(node.iter, names):
                    names.setdefault(node.target.id, []).append(value)
                if isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)):
                    record(node.target, node.iter)
    return names


def _is_sys_modules(node):
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and (isinstance(node.value, ast.Name) and node.value.id == "sys")
    )


def _is_sys_argv(node):
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "argv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _is_environ(node):
    """``os.environ`` however it was imported."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id == "environ"


def _env_read_argument(call):
    """The node naming the environment variable ``call`` reads, if any.

    Covers ``os.getenv(...)``, a bare ``getenv(...)`` imported from ``os``,
    and ``.get``/``.setdefault`` on ``os.environ`` or a bare ``environ``.
    """
    func = call.func
    if not call.args:
        return None
    if isinstance(func, ast.Name) and func.id == "getenv":
        return call.args[0]
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "getenv":
        return call.args[0]
    if func.attr in {"get", "setdefault"} and _is_environ(func.value):
        return call.args[0]
    return None


def _violations_in(rel, text):  # noqa: C901 - one branch per stated rule
    hits = []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:  # pragma: no cover - a broken file is a bug
        return [f"{rel}:{exc.lineno}: could not be parsed: {exc.msg}"]

    names = _string_bindings(tree)

    def forbidden(dotted):
        return dotted in FORBIDDEN_MODULES or dotted.split(".")[0] in {
            m for m in FORBIDDEN_MODULES if "." not in m
        }

    for node in ast.walk(tree):
        # Rule 1: static imports.
        if isinstance(node, ast.Import):
            for alias in node.names:
                if forbidden(alias.name):
                    hits.append(f"{rel}:{node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            candidates = {module} | {
                f"{module}.{a.name}" if module else a.name for a in node.names
            }
            if any(forbidden(c) for c in candidates if c):
                hits.append(f"{rel}:{node.lineno}: imports from {module or '.'}")

        elif isinstance(node, ast.Call):
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None
            )

            # Rule 2: dynamic import of a knowable name.
            if called in DYNAMIC_IMPORTERS and node.args:
                for value in _fold_all(node.args[0], names):
                    if forbidden(value):
                        hits.append(f"{rel}:{node.lineno}: imports {value} dynamically")

            # Rule 3: sys.modules.get("pytest")
            if (
                called in {"get", "__contains__"}
                and isinstance(func, ast.Attribute)
                and _is_sys_modules(func.value)
                and node.args
            ):
                for value in _fold_all(node.args[0], names):
                    if forbidden(value):
                        hits.append(
                            f"{rel}:{node.lineno}: asks sys.modules whether "
                            f"{value} is loaded"
                        )

            # Rule 5: environment variables.
            argument = _env_read_argument(node)
            if argument is not None:
                for value in _fold_all(argument, names):
                    if value not in ALLOWED_ENV:
                        hits.append(
                            f"{rel}:{node.lineno}: reads environment variable "
                            f"{value!r}, which is not in ALLOWED_ENV"
                        )

        # Rule 3: sys.modules[...] -- never legitimate here.
        elif isinstance(node, ast.Subscript):
            if _is_sys_modules(node.value):
                hits.append(f"{rel}:{node.lineno}: subscripts sys.modules")
            elif _is_environ(node.value):
                for value in _fold_all(node.slice, names):
                    if value not in ALLOWED_ENV:
                        hits.append(
                            f"{rel}:{node.lineno}: reads environment variable "
                            f"{value!r}, which is not in ALLOWED_ENV"
                        )

        # Rule 3: "pytest" in sys.modules
        elif isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if not isinstance(op, (ast.In, ast.NotIn)):
                    continue
                if not _is_sys_modules(comparator):
                    continue
                candidates = _fold_all(node.left, names)
                if not candidates:
                    hits.append(
                        f"{rel}:{node.lineno}: tests sys.modules for a name "
                        "the source does not reveal"
                    )
                for value in candidates:
                    if forbidden(value):
                        hits.append(
                            f"{rel}:{node.lineno}: asks sys.modules whether "
                            f"{value} is loaded"
                        )

        # Rule 4: sys.argv, however it is reached.
        if _is_sys_argv(node):
            hits.append(f"{rel}:{node.lineno}: reads sys.argv")

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


def test_shipped_code_does_not_consult_test_machinery():
    hits = _violations(REPO_ROOT / SHIPPED_PACKAGE)
    assert not hits, (
        f"{SHIPPED_PACKAGE}/ is what users install, and it must not know it "
        "is being tested (issue #116). A mock or test-only branch here means "
        "real users execute a path that exists only to make a test pass, so "
        "the tested path is not the shipped path. Move the fake into the "
        "test, or make the real thing injectable. If an environment variable "
        "is genuinely new and genuinely user-facing, add it to ALLOWED_ENV "
        "and say why. Offending lines:\n  " + "\n  ".join(hits)
    )


#: Every bypass below is a real way to get a mock, or a test-only branch,
#: into shipped code. The first ten defeat a grep for ``unittest.mock`` or
#: ``MagicMock``; the last five defeated the previous version of this
#: guard, which returned ``[]`` for all of them.
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
    # --- the five that defeated the previous guard ---
    "split_name_dynamic_import": (
        "import importlib\n"
        "\n"
        "\n"
        "def client():\n"
        "    module = importlib.import_module('unittest' + '.mock')\n"
        "    return getattr(module, 'Magic' + 'Mock')()\n"
    ),
    "implicit_concatenation_in_sys_modules": (
        "import sys\n"
        "\n"
        "\n"
        "def client():\n"
        "    return sys.modules['unittest' '.mock'].MagicMock()\n"
    ),
    "concatenated_membership_test": (
        "import sys\n"
        "\n"
        "\n"
        "def submit():\n"
        "    if 'py' + 'test' in sys.modules:\n"
        "        return 'fake-job-id'\n"
        "    return _really_submit()\n"
    ),
    "custom_env_flag": (
        "import os\n"
        "\n"
        "\n"
        "def submit():\n"
        "    if os.environ.get('CLUSTRIX_TEST_MODE'):\n"
        "        return 'fake-job-id'\n"
        "    return _really_submit()\n"
    ),
    "argv_sniff": (
        "import sys\n"
        "\n"
        "\n"
        "def submit():\n"
        "    if sys.argv[0].endswith('py.test'):\n"
        "        return 'fake-job-id'\n"
        "    return _really_submit()\n"
    ),
    # Naming the flag through a constant does not hide it either.
    "env_flag_behind_a_constant": (
        "import os\n"
        "\n"
        "_FLAG = 'CLUSTRIX_TEST_MODE'\n"
        "\n"
        "\n"
        "def submit():\n"
        "    if os.environ.get(_FLAG):\n"
        "        return 'fake-job-id'\n"
        "    return _really_submit()\n"
    ),
}


@pytest.fixture(scope="module")
def real_package_copy(tmp_path_factory):
    """A copy of the real ``clustrix/`` package, for planting bypasses in.

    Planting into a copy of the real package rather than into a lone
    snippet is deliberate: it proves the guard still finds the violation
    among two hundred files of legitimate code, and that the surrounding
    real modules do not drown it in false positives.
    """
    destination = tmp_path_factory.mktemp("planted") / SHIPPED_PACKAGE
    shutil.copytree(
        REPO_ROOT / SHIPPED_PACKAGE,
        destination,
        ignore=shutil.ignore_patterns(*_SKIP_DIR_PARTS),
    )
    return destination


@pytest.mark.parametrize("name", sorted(BYPASSES))
def test_guard_catches_every_known_bypass(name, real_package_copy):
    """Plant each bypass in the real package and prove the guard reports it."""
    planted = real_package_copy / "sneaky_new_backend.py"
    planted.write_text(BYPASSES[name], encoding="utf-8")
    try:
        hits = _violations(real_package_copy)
    finally:
        planted.unlink()

    assert hits, f"bypass {name!r} was not caught"
    assert all(
        h.startswith(f"{SHIPPED_PACKAGE}/sneaky_new_backend.py:") for h in hits
    ), f"the surrounding real package produced noise as well: {hits}"

    with pytest.raises(AssertionError):
        assert not hits, "planted violation must trip the same assertion"


#: Things that look like violations to a grep, or to the previous version
#: of this guard, and are not. The first four are drawn from code that
#: really is in ``clustrix/``; the rest are plausible code that the
#: previous guard would have flagged, each of which would have earned an
#: allowlist entry and killed it.
INNOCENT = {
    # clustrix/notebook_magic.py:83 -- a comment, not a mock.
    "comment_mentions_mocks": (
        "def render(widgets):\n"
        "    # IPython components (may be mocks)\n"
        "    return widgets\n"
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
    # clustrix/utils.py:486 -- environment replication looks the user's own
    # modules up by a name only known at runtime.
    "sys_modules_get_by_computed_name": (
        "import sys\n"
        "\n"
        "\n"
        "def module_of(module_name):\n"
        "    return sys.modules.get(module_name)\n"
    ),
    # A dependency list that happens to name the test framework.
    "dev_extras_list": ('DEV_EXTRAS = ["pytest", "pytest-cov", "black"]\n'),
    # A pip-freeze filter, entirely plausible in environment replication.
    "pip_freeze_skip_set": (
        'SKIP = {"pytest", "_pytest"}\n'
        "\n"
        "\n"
        "def replicate(packages):\n"
        "    return [p for p in packages if p not in SKIP]\n"
    ),
    # An error message that mentions the framework.
    "error_message_mentions_pytest": (
        "def require_cluster():\n" "    raise RuntimeError('pytest')\n"
    ),
    # Identifiers that merely end in a mock-ish word.
    "unrelated_identifier": (
        "class JobMocker:\n"
        "    def mock_up_a_plan(self):\n"
        "        return {'cores': 4}\n"
    ),
    # Dynamic import of the user's own package, which is why rule 2 stops
    # at names the source reveals.
    "dynamic_import_of_user_module": (
        "import importlib\n"
        "\n"
        "\n"
        "def load(module_name):\n"
        "    return importlib.import_module(module_name)\n"
    ),
}


@pytest.mark.parametrize("name", sorted(INNOCENT))
def test_guard_is_quiet_about_legitimate_code(name, tmp_path):
    """Flagging real code is how a guard gets an allowlist and dies."""
    package = tmp_path / SHIPPED_PACKAGE
    package.mkdir()
    (package / "well_behaved.py").write_text(INNOCENT[name], encoding="utf-8")

    assert _violations(package) == []
