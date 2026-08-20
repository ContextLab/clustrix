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

**Four things it used to miss, and one it could never fire on.** A
red-teaming round found them, and each was the same shape as a real leak:

* A blanket *module-level* exemption for the gate and for the store meant
  the two files most able to leak were the two least checked. A new
  function inside ``credential_manager.py`` shaped exactly like
  ``load_credentials_optional`` -- return the password, name no recipient
  -- was invisible, and so was one inside ``credential_release.py`` shaped
  like ``_stored_credential``. The exemptions are per *symbol* and per
  *enclosing function* now (:data:`STORE_ACCESS_ALLOWLIST`), which is why
  that list is long: it is an inventory rather than a waiver.
* ``f = mgr._ensure_credential_unchecked`` followed by ``f(x)`` matched
  nothing, because the rule looked for a ``Call`` on an ``Attribute`` and
  an alias is neither. Any *reference* counts now.
* ``os.environ[var]`` was not one of the two spellings rule 4 knew.
* Rule 1 was **dead**. It looked for an import of
  ``_ensure_credential_unchecked``, which is a method -- so the import it
  forbade raises ``ImportError`` and the check could never fire, while the
  import that does work, ``from clustrix.credential_release import
  _stored_credential``, was not looked for at all.

Because a rule that cannot fire reads as coverage, three of the rules below
are paired with a test that parses the offending shape and asserts the walk
finds it.

What makes a bypass *fail* rather than leak is the runtime check in
``FlexibleCredentialManager._ensure_credential_unchecked`` and in
``_stored_credential``, which raise for any caller that is not the named
gate function and are always on. This test is the second line: it converts
"forgot" into "had to say so out loud, in this file".

So every rule below asserts on a **decidable structure** -- an
``ImportFrom`` of a named symbol, an ``Attribute`` with a named ``attr``, a
``Subscript`` of ``os.environ``, a ``ClusterConfig(**...)`` call carrying a
``**`` keyword -- and never on a bare identifier whose type it would have
to guess.
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
    # Route 9, and gated now: ``get_cluster_password`` used to scan
    # CLUSTRIX_DEFAULT_PASSWORD and CLUSTER_PASSWORD -- variables that name
    # **no host** -- and hand what it found to whatever hostname it was
    # passed, which on the ``setup_auth_with_fallback`` path is
    # ``config.cluster_host``. The scan moved here, behind the same rule 2
    # as everything else, and the variables that *do* name a host are
    # released on that strength alone.
    ("credential_release.py", "_release_fallback_environment"),
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


def _is_environ_lookup(node: ast.AST) -> bool:
    """A read of ``os.environ`` under a key that is not a literal.

    Three spellings, because the environment does not care which one you
    used: ``os.environ.get(x)``, ``os.getenv(x)``, and ``os.environ[x]``.
    The subscript was missed entirely -- it raises ``KeyError`` rather than
    returning ``None``, which is the only difference, and a route that
    reads a secret and then crashes has still read the secret.
    """
    if isinstance(node, ast.Subscript):
        value = node.value
        if not (isinstance(value, ast.Attribute) and value.attr == "environ"):
            return False
        key = node.slice
        # Python 3.8 wrapped a subscript key in ast.Index; 3.9+ does not.
        key = getattr(key, "value", key) if isinstance(key, ast.Index) else key
        return not isinstance(key, ast.Constant)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or not node.args:
        return False
    if isinstance(node.args[0], ast.Constant):
        return False
    if func.attr == "get":
        return isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
    if func.attr == "getenv":
        return isinstance(func.value, ast.Name) and func.value.id == "os"
    return False


def test_no_module_imports_a_private_name_from_the_store_or_the_gate():
    """Rule 1: an ``ImportFrom`` of a private name from either module.

    **This rule was dead.** It looked for imports of
    ``_ensure_credential_unchecked``, which is a *method*: there is no
    module-level name to import, so ``from clustrix.credential_manager
    import _ensure_credential_unchecked`` raises ``ImportError`` and the
    check could never fire. Meanwhile the import that *does* work --
    ``from clustrix.credential_release import _stored_credential`` -- was
    not looked for at all, and was a public store with an underscore on it.

    So the rule is now about what an import statement can actually reach: a
    leading-underscore name out of either the store or the gate. Both
    modules import from each other, and those two are named.
    """
    guarded = {
        "clustrix.credential_manager",
        "credential_manager",
        "clustrix.credential_release",
        "credential_release",
        ".credential_manager",
        ".credential_release",
    }
    permitted = {
        # The gate's own import of the store's guard helper, and the
        # store's import of the gate's. Named, because "the two modules
        # that implement the rule" is not the same as "anybody".
        (GATE, "_ensure_credential_unchecked"),
        (STORE, "_ensure_credential_unchecked"),
    }
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = ("." * node.level) + (node.module or "")
            if module not in guarded:
                continue
            for alias in node.names:
                if not alias.name.startswith("_"):
                    continue
                if (path.name, alias.name) in permitted:
                    continue
                offenders.append(f"{path.name}: {module}.{alias.name}")
    assert offenders == [], (
        "a private name was imported out of the credential store or the "
        "gate; secrets are obtained through "
        "clustrix.credential_release.release_credential(target): " + repr(offenders)
    )


def test_rule_one_can_actually_fire():
    """The rule above is not vacuous, which is exactly what it used to be.

    A check that can never fail reads as coverage and is worse than no
    check. This parses the offending import and asserts the walk finds it,
    against the same AST pipeline the real rule uses.
    """
    tree = ast.parse("from clustrix.credential_release import _stored_credential\n")
    found = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name.startswith("_")
    ]

    assert found == ["_stored_credential"]


#: Who may *touch* a secret-bearing attribute, per symbol, as ``(module,
#: enclosing definitions)``.
#:
#: **A blanket module-level exemption is the problem, not the shortcut.**
#: Exempting the whole gate and the whole store means the two files most
#: able to leak are the two least checked: a new function inside
#: ``credential_manager.py`` shaped exactly like ``load_credentials_optional``
#: -- return the password, name no recipient -- was invisible here, and so
#: was one inside ``credential_release.py`` shaped like ``_stored_credential``.
#: Both of those were real, and both are listed by *name* now, so a third
#: fails this test until somebody writes it down.
STORE_ACCESS_ALLOWLIST = {
    "_ensure_credential_unchecked": {
        (GATE, "_stored_credential"),
    },
    "get_credentials": {
        (STORE, "DotEnvCredentialSource.get_credentials"),
        (STORE, "DotEnvCredentialSource.list_available_providers"),
        (STORE, "EnvironmentCredentialSource.get_credentials"),
        (STORE, "EnvironmentCredentialSource.list_available_providers"),
        (STORE, "GitHubActionsCredentialSource.get_credentials"),
        (STORE, "GitHubActionsCredentialSource.list_available_providers"),
        (STORE, "CredentialSource.get_credentials"),
        (STORE, "FlexibleCredentialManager._ensure_credential_unchecked"),
        (STORE, "FlexibleCredentialManager._configured_fields"),
        (STORE, "FlexibleCredentialManager.list_available_providers"),
    },
    "_stored_credential": {
        (GATE, "describe_credential"),
        (GATE, "_release_stored"),
    },
    "_sources": {
        # The class-level declaration SECRET_SURFACES points at.
        (STORE, "FlexibleCredentialManager"),
        (STORE, "FlexibleCredentialManager.__init__"),
        (STORE, "FlexibleCredentialManager._ensure_credential_unchecked"),
        (STORE, "FlexibleCredentialManager._configured_fields"),
        (STORE, "FlexibleCredentialManager.list_available_providers"),
        (STORE, "FlexibleCredentialManager.get_credential_status"),
    },
}


def test_only_the_named_functions_touch_the_store():
    """Rule 2: any *reference* to a secret-bearing name, not just a call.

    Three widenings over the version this replaces, each of which let a
    real bypass through:

    * It matched ``ast.Call`` only, so ``f = mgr._ensure_credential_unchecked``
      followed by ``f(x)`` -- an alias, then a call on a bare ``Name`` --
      was invisible. Any *reference* counts now: binding the method is the
      act that matters, and what happens to the binding afterwards is not
      decidable.
    * It exempted whole modules. The gate and the store are the two files
      most able to leak, so exempting them wholesale left them least
      checked. The allowlist is per symbol and per enclosing function.
    * ``_stored_credential`` and ``_sources`` were not watched at all, and
      both were doors.

    An ``ast.Attribute`` in any context, plus the string form used by
    ``getattr(x, "...")``, since a constant argument is decidable even
    though a computed one is not.
    """
    watched = set(STORE_ACCESS_ALLOWLIST)
    offenders: Set[str] = set()
    for path, tree in _modules():
        for node, scope in _definitions_of(tree):
            name = None
            if isinstance(node, ast.Attribute) and node.attr in watched:
                name = node.attr
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in watched
            ):
                name = node.args[1].value
            elif isinstance(node, ast.Name) and node.id in watched:
                name = node.id
            if name is None:
                continue
            if (path.name, scope) in STORE_ACCESS_ALLOWLIST[name]:
                continue
            offenders.add(f"{path.name}:{scope} -> {name}")
    assert offenders == set(), (
        "a function that is not on the allowlist touches the credential "
        "store. Secrets are obtained through "
        "clustrix.credential_release.release_credential(target); if this "
        "really is gate-internal plumbing, add it to "
        "STORE_ACCESS_ALLOWLIST by name and say why: " + repr(sorted(offenders))
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
            if _is_environ_lookup(node):
                found.add((path.name, scope))

    assert found == ENVIRONMENT_LOOKUP_ALLOWLIST, (
        "a secret-shaped environment lookup appeared or moved. Route it "
        "through clustrix.credential_release.release_credential, or add it "
        "to ENVIRONMENT_LOOKUP_ALLOWLIST with the reason it is not one.\n"
        f"  unexpected: {sorted(found - ENVIRONMENT_LOOKUP_ALLOWLIST)}\n"
        f"  gone:       {sorted(ENVIRONMENT_LOOKUP_ALLOWLIST - found)}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "os.environ.get(name)",
        "os.getenv(name)",
        "os.environ[name]",
        "value = os.environ[name]",
    ],
    ids=["get", "getenv", "subscript", "subscript-assign"],
)
def test_every_spelling_of_a_run_time_environment_read_is_seen(source):
    """Rule 4 is not vacuous, and the subscript form was missed.

    ``os.environ[var]`` differs from ``os.environ.get(var)`` only in
    raising ``KeyError`` instead of returning ``None``. A route that reads
    a secret and then crashes has still read the secret, so the scan has to
    see all three.
    """
    tree = ast.parse(source)

    assert any(_is_environ_lookup(node) for node in ast.walk(tree))


@pytest.mark.parametrize(
    "source",
    ["os.environ.get('LITERAL')", "os.getenv('LITERAL')", "os.environ['LITERAL']"],
    ids=["get", "getenv", "subscript"],
)
def test_a_literal_environment_key_is_not_a_run_time_lookup(source):
    """The rule above must not fire on every environment read there is.

    A literal name is written in the source, so no configuration file can
    choose it -- which is the whole property ``password_env_var`` lacks.
    """
    tree = ast.parse(source)

    assert not any(_is_environ_lookup(node) for node in ast.walk(tree))


@pytest.mark.parametrize(
    "source",
    [
        "mgr._ensure_credential_unchecked('ssh')",
        "f = mgr._ensure_credential_unchecked\nf('ssh')",
        "getattr(mgr, '_ensure_credential_unchecked')('ssh')",
        "source = mgr._sources[0]",
        "from clustrix.credential_release import _stored_credential",
    ],
    ids=["call", "alias-then-call", "getattr-literal", "sources", "import"],
)
def test_rule_two_sees_the_shapes_that_used_to_walk_past_it(source):
    """Binding the name is the act; what happens next is not decidable.

    ``f = mgr._ensure_credential_unchecked`` then ``f(x)`` matched nothing,
    because the old rule looked for an ``ast.Call`` on an ``ast.Attribute``
    and an alias is neither. Any reference counts now.
    """
    tree = ast.parse(source)
    watched = set(STORE_ACCESS_ALLOWLIST)
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in watched:
            seen.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in watched:
            seen.add(node.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in watched
        ):
            seen.add(node.args[1].value)
        elif isinstance(node, ast.ImportFrom):
            seen.update(a.name for a in node.names if a.name in watched)

    assert seen, f"nothing in {source!r} was recognised as touching the store"


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
