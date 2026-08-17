#!/usr/bin/env python3
"""Guards that the default test suite cannot spend money.

Regression tests for issue #109.

Background: ``tests/integration/`` originally carried no pytest markers, so the
documented command ``pytest tests/ -m "not real_world"`` selected
``test_aws_eks_real_provision.py`` ("this WILL create resources and incur
costs!") and opened live connections to AWS.

Two files in that directory (``test_eks_permissions.py`` and
``test_aws_eks_debug.py``) are standalone scripts with no ``__main__`` guard:
they fetch credentials, call boto3, and invoke ``exit()`` at *module scope*.
That means a marker-based skip is not sufficient -- pytest imports a module in
order to collect it, so the AWS calls happen before any marker is consulted.
The guard must therefore prevent *collection*, not merely execution.

These tests run pytest in a subprocess so they exercise the real collection
machinery rather than asserting against a stubbed pytest.
"""

import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
INTEGRATION_DIR = REPO_ROOT / "tests" / "integration"
OPT_IN_VAR = "CLUSTRIX_ALLOW_BILLABLE"

# Phrases pytest uses when a run selected nothing. Kept in one place so the
# "gate" and "not a deletion" tests cannot drift apart.
_EMPTY_COLLECTION_PHRASES = (
    "no tests collected",
    "no tests ran",
    "collected 0 items",
)


def _collected_nothing(output: str) -> bool:
    return any(phrase in output for phrase in _EMPTY_COLLECTION_PHRASES)


def _collect_integration(opt_in: bool, tmp_home):
    """Run `pytest --collect-only` against tests/integration in a subprocess.

    `-o addopts=` strips the project's default addopts so this does not depend
    on xdist being installed.

    The subprocess gets a throwaway HOME and a scrubbed environment. This is
    deliberate: a test whose job is to prove the suite cannot spend money must
    not itself be able to spend money if the guard it is testing is absent or
    broken. `FlexibleCredentialManager` reads `~/.clustrix/.env`
    (credential_manager.py:304-305), so redirecting HOME removes the only
    on-disk credential source these modules would otherwise find.
    """
    env = dict(os.environ)
    env.pop(OPT_IN_VAR, None)
    if opt_in:
        env[OPT_IN_VAR] = "1"
    # HOME on POSIX, USERPROFILE on Windows -- set both so credential discovery
    # is redirected regardless of platform.
    env["HOME"] = str(tmp_home)
    env["USERPROFILE"] = str(tmp_home)

    # Hard network block for the subprocess.
    #
    # Scrubbing HOME is not sufficient on its own: FlexibleCredentialManager
    # also resolves credentials through the 1Password CLI, so a developer with
    # an unlocked `op` session still gets live AWS keys. If the guard under
    # test is broken, this subprocess would then make real API calls -- the
    # test would cause the very harm it exists to detect. Blocking socket
    # connections makes the failure mode "loud error" instead of "AWS bill".
    sitecustomize = tmp_home / "sitecustomize.py"
    sitecustomize.write_text(
        "import socket\n"
        "class _Blocked(OSError):\n"
        "    pass\n"
        "def _deny(*a, **k):\n"
        "    raise _Blocked('network disabled by test_billable_safety')\n"
        "socket.socket.connect = _deny\n"
        "socket.socket.connect_ex = _deny\n"
        "socket.create_connection = _deny\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_home), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    for leaked in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "LAMBDA_CLOUD_API_KEY",
    ):
        env.pop(leaked, None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(INTEGRATION_DIR),
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def test_integration_tests_are_not_collected_by_default(tmp_path):
    """Without explicit opt-in, tests/integration must collect zero tests.

    This is the core guarantee: the default suite is free to run.
    """
    result = _collect_integration(opt_in=False, tmp_home=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")

    assert _collected_nothing(combined), (
        "tests/integration was collected without opt-in.\n"
        f"exit={result.returncode}\n{combined[-3000:]}"
    )


def test_default_collection_does_not_import_billable_modules(tmp_path):
    """Collection must not *import* the unguarded AWS scripts.

    `test_eks_permissions.py` and `test_aws_eks_debug.py` make real boto3 calls
    and call exit() at module scope, so importing them is itself the harm.
    """
    result = _collect_integration(opt_in=False, tmp_home=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")

    for marker in (
        "Testing EKS permissions",  # printed at import by test_eks_permissions
        "Getting AWS credentials",  # printed at import by test_aws_eks_debug
        "botocore",
        "NoCredentialsError",
    ):
        assert marker not in combined, (
            f"Billable module was imported during default collection "
            f"(saw {marker!r}).\n{combined[-3000:]}"
        )


def test_integration_tests_are_still_reachable_with_opt_in(tmp_path):
    """The guard is a gate, not a deletion.

    With CLUSTRIX_ALLOW_BILLABLE=1 the directory must become collectable again,
    otherwise we have silently dropped the tests instead of protecting them.
    """
    result = _collect_integration(opt_in=True, tmp_home=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")

    assert not _collected_nothing(combined), (
        "tests/integration collected nothing even with opt-in; the guard is "
        f"deleting tests rather than gating them.\n{combined[-3000:]}"
    )


@pytest.mark.parametrize(
    "path",
    sorted(INTEGRATION_DIR.glob("*.py")),
    ids=lambda p: p.name,
)
def test_every_integration_file_is_declared_billable(path):
    """Every file under tests/integration must be covered by the opt-in gate.

    A new file dropped into this directory must not be able to run for free.
    The gate is directory-wide rather than a per-file allowlist, so this
    asserts the gate itself is in place and blocks at collection time. Every
    file in the directory is covered by construction, including files added
    after this test was written.
    """
    conftest = INTEGRATION_DIR / "conftest.py"
    assert conftest.exists(), (
        "tests/integration/conftest.py is missing; without it the directory "
        "collects for free."
    )
    source = conftest.read_text(encoding="utf-8")
    assert (
        OPT_IN_VAR in source
    ), f"tests/integration/conftest.py does not reference {OPT_IN_VAR}"
    assert "collect_ignore" in source, (
        "tests/integration/conftest.py must prevent collection (collect_ignore), "
        "not merely skip at runtime -- several modules make live AWS calls at "
        "import time."
    )
