"""Opt-in gate for the integration test suite.

See issue #109.

Everything in this directory talks to external infrastructure, and a large
subset consumes **billable** resources (remote GPU nodes, paid job APIs).
Before this gate existed, the documented command
``pytest tests/ -m "not real_world"`` collected this directory, because none of
its files carried a pytest marker.

A marker-based skip would not be sufficient. pytest must *import* a module in
order to collect it, and several modules here are standalone scripts rather
than test modules: they fetch credentials, open connections and invoke
``exit()`` at module scope. Importing them is itself the harm:

* the external calls happen before any marker or skip is consulted, and
* a module-level ``sys.exit(1)`` raises ``SystemExit`` during collection,
  which crashes the whole pytest run with ``INTERNALERROR``.

So the gate has to stop *collection*, which is what ``collect_ignore_glob``
does -- pytest never imports an ignored file.

The gate is deliberately directory-wide (default-deny) rather than a
per-file allowlist. Misclassifying a single file costs real money, and a
newly added file must not be able to run for free simply because nobody
remembered to mark it.

To run these tests deliberately::

    CLUSTRIX_ALLOW_BILLABLE=1 pytest tests/integration/

Be aware that doing so may consume real, chargeable resources.
"""

import os
import pathlib

import pytest

OPT_IN_VAR = "CLUSTRIX_ALLOW_BILLABLE"
_TRUTHY = {"1", "true", "yes", "on"}


def billable_tests_enabled() -> bool:
    """Return True only if the operator explicitly opted in to paying."""
    return os.environ.get(OPT_IN_VAR, "").strip().lower() in _TRUTHY


# Prevent pytest from importing anything in this directory unless opted in.
# This must stay at module scope: conftest collect_ignore hooks are read when
# the directory is first visited, before any test module is imported.
if not billable_tests_enabled():
    collect_ignore_glob = ["*.py"]


_THIS_DIR = pathlib.Path(__file__).parent.resolve()


def pytest_collection_modifyitems(config, items):
    """Mark tests *from this directory* as integration + expensive.

    Only reachable under opt-in (otherwise nothing here is collected). This
    keeps ``-m`` selection meaningful for anyone who has opted in, e.g.
    ``-m "not expensive"`` to run the cheaper integration tests.

    The path filter is essential and easy to get wrong: pytest passes the
    hook the **entire session's** item list, not just items collected from
    this directory. Marking unconditionally would tag every test in the whole
    suite as `expensive`, so `-m "not expensive"` would select nothing at all.
    """
    for item in items:
        try:
            item_path = pathlib.Path(str(item.fspath)).resolve()
        except Exception:  # pragma: no cover - defensive, path may be virtual
            continue
        if item_path == _THIS_DIR or _THIS_DIR in item_path.parents:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.expensive)
