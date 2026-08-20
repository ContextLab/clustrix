#!/usr/bin/env python3
"""Nothing reaches a secret except through ``clustrix.credential_release``.

**What this test proves, and what it does not.** It proves that an eighth
route cannot be added *silently*: every structure in the tree that touches a
secret-bearing surface is enumerated here, and a new one fails this test
until somebody writes it into the file named after the rule. It does **not**
prove that no such route exists. A static check cannot follow
``getattr(manager, "_ensure_" + "credential_unchecked")``, ``importlib``,
``eval``, a name rebound at runtime, a plugin, a notebook, or a downstream
package. Nor can it tell ``x.password`` where ``x`` is a ``ClusterConfig``
from ``x.password`` where ``x`` is a dictionary of the user's own -- AST has
no types, and attempting to match ``.password`` textually is precisely the
mistake that made an earlier guard in this project fire on ``def
joblib(self)``.

What makes a bypass *fail* rather than leak is the runtime check in
``FlexibleCredentialManager._ensure_credential_unchecked``, which raises for
any caller that is not the gate and is always on. This test is the second
line: it converts "forgot" into "had to say so out loud, in this file".

So every rule below asserts on a **decidable structure** -- an
``ImportFrom`` of a named symbol, a ``Call`` on a named attribute, a
``ClusterConfig(**...)`` call carrying a ``**`` keyword -- and never on a
bare identifier.
"""

import ast
import pathlib
from typing import List, Set, Tuple

import pytest

from clustrix.credential_release import SECRET_SURFACES

CLUSTRIX = pathlib.Path(__file__).resolve().parents[2] / "clustrix"

#: The module that may obtain a secret. The same string the runtime guard
#: compares against.
GATE = "credential_release.py"

#: The store's own module. ``CredentialSource.get_credentials`` is the
#: protocol method every source implements, and the store consumes its own
#: sources; anywhere *else* a call to it is a way around the gate.
STORE = "credential_manager.py"

#: ``ClusterConfig(**mapping)`` outside ``config.py``, as ``(module,
#: enclosing definitions)``. Each of these is a rebuild or a set of literals
#: -- **not** parsed file content, which must go through
#: ``ClusterConfig.from_file_content(mapping, source)`` so that the source is
#: an argument nobody can forget. Adding a row here is a claim that the
#: mapping did not come off a disk; make it deliberately.
CLUSTER_CONFIG_SPLAT_ALLOWLIST = {
    # The widget's own fields, typed by the user in this session.
    ("modern_notebook_widget.py", "ModernClustrixWidget._get_config_from_widgets"),
    # Literals in the class body: the built-in profile templates.
    ("profile_manager.py", "ProfileManager._load_default_profiles"),
    # asdict() of a config that already exists, so its provenance is already
    # recorded against the hostname it names.
    ("profile_manager.py", "ProfileManager.clone_profile"),
}

#: Reads of ``os.environ`` under a key that is not a literal, as ``(module,
#: enclosing definitions)``. A non-literal key means "a name chosen at run
#: time", which is how ``password_env_var`` works -- and a configuration file
#: can set ``password_env_var``, so this is the shape route 6 had.
ENVIRONMENT_LOOKUP_ALLOWLIST = {
    # The gated one: the environment branch of the gate itself.
    ("credential_release.py", "_release_environment"),
    # CLUSTRIX_CONFIG_DIR, a module constant, and not a secret.
    ("config.py", "get_config_dir"),
    # A module constant naming the auto-display switch. Not a secret.
    ("notebook_magic_core.py", "auto_display_on_import"),
    # NOT gated, and listed here so that it cannot be forgotten:
    # ``get_cluster_password`` scans CLUSTRIX_DEFAULT_PASSWORD and
    # CLUSTER_PASSWORD -- variables that name **no host** -- and hands what
    # it finds to whatever hostname it was passed, which on the
    # ``setup_auth_with_fallback`` path is ``config.cluster_host``. That is
    # the same shape as routes 2 and 6 and it is an open finding rather than
    # an approved exception; it is written down here because a surface
    # nobody has written down is the one that gets closed eighth.
    ("auth_fallbacks.py", "get_cluster_password"),
}


def _definitions_of(tree: ast.AST) -> List[Tuple[ast.AST, str]]:
    """Every node paired with the dotted name of the definitions enclosing it."""
    found: List[Tuple[ast.AST, str]] = []

    class Walker(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: List[str] = []

        def _scoped(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _scoped
        visit_AsyncFunctionDef = _scoped
        visit_ClassDef = _scoped

        def generic_visit(self, node):
            found.append((node, ".".join(self.stack)))
            super().generic_visit(node)

    Walker().visit(tree)
    return found


def _modules():
    for path in sorted(CLUSTRIX.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _is_environ_lookup(call: ast.Call) -> bool:
    """``os.environ.get(x)`` or ``os.getenv(x)`` with a non-literal key."""
    func = call.func
    if not isinstance(func, ast.Attribute) or not call.args:
        return False
    if isinstance(call.args[0], ast.Constant):
        return False
    if func.attr == "get":
        return isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
    if func.attr == "getenv":
        return isinstance(func.value, ast.Name) and func.value.id == "os"
    return False


def test_no_module_imports_the_private_store():
    """Rule 1: an ``ImportFrom`` of a literal symbol from a literal module."""
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "_ensure_credential_unchecked":
                        offenders.append(f"{path.name}: {node.module}")
    assert offenders == [], (
        "the private store is imported by name; secrets are obtained through "
        "clustrix.credential_release.release_credential(target): " + repr(offenders)
    )


def test_only_the_gate_calls_the_store():
    """Rule 2: a ``Call`` whose ``func`` is a named ``Attribute``.

    ``get_credentials`` is the ``CredentialSource`` protocol method -- the
    real floor of the store -- so it counts as much as the manager method
    does.
    """
    offenders: Set[str] = set()
    for path, tree in _modules():
        for node, scope in _definitions_of(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr == "_ensure_credential_unchecked" and path.name != GATE:
                offenders.add(f"{path.name}:{scope}")
            if func.attr == "get_credentials" and path.name not in (GATE, STORE):
                offenders.add(f"{path.name}:{scope}")
    assert (
        offenders == set()
    ), "a module other than the gate reads the credential store directly: " + repr(
        sorted(offenders)
    )


def test_no_config_is_built_from_a_mapping_outside_the_allowlist():
    """Rule 3: a ``Call`` to ``ClusterConfig`` carrying a ``**`` keyword.

    That is the structure of "build a config out of parsed content", and it
    is decidable -- an ``ast.keyword`` whose ``arg`` is ``None`` -- unlike
    any rule that matches names. ``config.py`` is exempt because
    ``from_file_content`` is where the one legitimate splat lives.
    """
    found: Set[Tuple[str, str]] = set()
    for path, tree in _modules():
        if path.name == "config.py":
            continue
        for node, scope in _definitions_of(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name == "ClusterConfig" and any(k.arg is None for k in node.keywords):
                found.add((path.name, scope))

    assert found == CLUSTER_CONFIG_SPLAT_ALLOWLIST, (
        "a ClusterConfig is built from a mapping somewhere new. If the "
        "mapping is parsed file content, use "
        "ClusterConfig.from_file_content(mapping, source) so the provenance "
        "is an argument nobody can forget; if it is not, add it to "
        "CLUSTER_CONFIG_SPLAT_ALLOWLIST and say why.\n"
        f"  unexpected: {sorted(found - CLUSTER_CONFIG_SPLAT_ALLOWLIST)}\n"
        f"  gone:       {sorted(CLUSTER_CONFIG_SPLAT_ALLOWLIST - found)}"
    )


def test_every_run_time_environment_lookup_is_written_down():
    """Rule 4: the allowlist assertion, and the honest core of this file.

    A key chosen at run time is the shape ``password_env_var`` has, and a
    configuration file can set ``password_env_var`` -- so a new one of these
    is a candidate route until somebody says otherwise here.
    """
    found: Set[Tuple[str, str]] = set()
    for path, tree in _modules():
        for node, scope in _definitions_of(tree):
            if isinstance(node, ast.Call) and _is_environ_lookup(node):
                found.add((path.name, scope))

    assert found == ENVIRONMENT_LOOKUP_ALLOWLIST, (
        "a secret-shaped environment lookup appeared or moved. Route it "
        "through clustrix.credential_release.release_credential, or add it "
        "to ENVIRONMENT_LOOKUP_ALLOWLIST with the reason it is not one.\n"
        f"  unexpected: {sorted(found - ENVIRONMENT_LOOKUP_ALLOWLIST)}\n"
        f"  gone:       {sorted(ENVIRONMENT_LOOKUP_ALLOWLIST - found)}"
    )


@pytest.mark.parametrize("module, symbol", SECRET_SURFACES)
def test_every_declared_secret_surface_still_exists(module, symbol):
    """``SECRET_SURFACES`` is a list of real things, not a stale comment.

    A surface that has been renamed away silently is a surface nobody is
    watching any more.
    """
    import importlib

    obj = importlib.import_module(module)
    for part in symbol.split("."):
        if hasattr(obj, part):
            obj = getattr(obj, part)
            continue
        # An instance attribute exists only on instances, so the class
        # declaration is what a reader -- and this test -- can point at.
        # ``_sources`` is one: constructing a manager to look for it would
        # create a ~/.clustrix directory as a side effect of an AST test.
        annotations = getattr(obj, "__annotations__", {})
        assert part in annotations, f"{module}.{symbol} no longer exists"
        return
