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
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
INTEGRATION_DIR = REPO_ROOT / "tests" / "integration"
OPT_IN_VAR = "CLUSTRIX_ALLOW_BILLABLE"

_COUNT_RE = re.compile(r"(\d+)(?:/\d+)? tests? collected")


def _collected_count(output: str):
    """Parse the number of tests pytest actually collected.

    Deliberately NOT prose-matching on "no tests collected": that phrase also
    appears when collection *errors out*, so an import-error storm could
    masquerade as a working gate. Returns None when no count line is present,
    which callers must treat as "could not verify" rather than as zero.
    """
    matches = _COUNT_RE.findall(output)
    if not matches:
        # pytest prints "no tests ran"/"no tests collected" with no number.
        if "no tests collected" in output or "no tests ran" in output:
            return 0
        return None
    return max(int(m) for m in matches)


# Sentinel for "run pytest with no path argument at all", which is a distinct
# case from "run it against the default target": with no path on the command
# line pytest fills its target list from `testpaths` in pyproject.toml. That
# path is what regressed in #130 and needs its own coverage.
NO_TARGET = object()


def _collect_integration(opt_in: bool, tmp_home, target=None):
    """Run `pytest --collect-only` against tests/integration in a subprocess.

    Pass `target=NO_TARGET` to omit the path argument entirely.

    `-o addopts=` strips the project's default addopts so this does not depend
    on xdist being installed. It deliberately does not touch `testpaths`, so a
    NO_TARGET run still exercises testpaths resolution.

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
    argv = [sys.executable, "-m", "pytest"]
    if target is not NO_TARGET:
        argv.append(str(target if target is not None else INTEGRATION_DIR))
    argv += ["--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider"]
    return subprocess.run(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def test_default_suite_does_not_collect_integration_tests(tmp_path):
    """The documented command must not pull in tests/integration at all.

    Targets `tests/` -- the command a developer actually runs -- rather than
    the integration directory itself, so this asserts the real-world property
    instead of a proxy for it.
    """
    result = _collect_integration(
        opt_in=False, tmp_home=tmp_path, target=REPO_ROOT / "tests"
    )
    combined = (result.stdout or "") + (result.stderr or "")

    collected_integration = [
        line
        for line in combined.splitlines()
        if line.strip().startswith("tests/integration/")
        or line.strip().startswith("tests\\integration\\")
    ]
    assert (
        not collected_integration
    ), "The default suite collected integration tests:\n" + "\n".join(
        collected_integration[:10]
    )


def test_bare_pytest_is_not_refused_by_the_guard(tmp_path):
    """A bare `pytest` must still run. See #130.

    This is the counterpart to the refusal tests, and it guards a trap that
    already sprang once. The guard originally inspected `config.args`, which
    reads like "what the user asked for" but is not: when no path is given on
    the command line, pytest populates `config.args` from `testpaths` in
    pyproject.toml. `testpaths` used to list `tests/integration`, so the moment
    the project's config actually took effect, a bare `pytest` matched the
    guard and aborted the entire suite with the billable-resources refusal.

    The fix was to read `config.invocation_params.args` -- the real argv -- so
    the guard fires on explicit targeting only. Reverting that change, or
    putting `tests/integration` back into `testpaths`, breaks bare `pytest`
    for everyone, and nothing else in the suite would notice.
    """
    result = _collect_integration(opt_in=False, tmp_home=tmp_path, target=NO_TARGET)
    combined = (result.stdout or "") + (result.stderr or "")

    assert "Refusing to run" not in combined, (
        "A bare `pytest` was refused by the billable-resources guard. The guard "
        "is matching testpaths-derived arguments instead of what the operator "
        "typed (see #130).\n" + combined[-2000:]
    )
    assert result.returncode == 0, (
        f"A bare `pytest --collect-only` failed (exit {result.returncode}).\n"
        + combined[-2000:]
    )
    assert (_collected_count(combined) or 0) > 0, (
        "A bare `pytest` collected nothing at all.\n" + combined[-2000:]
    )


def test_explicitly_targeting_integration_is_refused(tmp_path):
    """Naming the directory or a file in it must fail loudly, not silently.

    `collect_ignore_glob` only filters directory traversal, so an explicitly
    named path bypassed it entirely and pytest imported the module -- which is
    the actual hazard, since several of them call boto3 at import. A
    pre-collection guard now refuses the run, and it must say why.
    """
    result = _collect_integration(opt_in=False, tmp_home=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")

    assert (
        result.returncode != 0
    ), f"Explicitly targeting tests/integration succeeded (exit 0).\n{combined[-2000:]}"
    assert OPT_IN_VAR in combined, (
        f"The refusal does not tell the user how to opt in ({OPT_IN_VAR} not "
        f"mentioned).\n{combined[-2000:]}"
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

    count = _collected_count(combined)
    assert count is not None and count > 0, (
        "tests/integration collected nothing even with opt-in; the guard is "
        f"deleting tests rather than gating them.\n{combined[-3000:]}"
    )
    # Deliberately no assertion on returncode: collection errors from
    # optional missing dependencies legitimately produce exit 2, and that is a
    # property of the environment, not of the gate under test.


@pytest.mark.parametrize(
    "path",
    sorted(INTEGRATION_DIR.rglob("test_*.py")),
    ids=lambda p: p.name,
)
def test_no_integration_file_is_collectable_by_default(path, tmp_path):
    """Every individual file under tests/integration must be un-collectable.

    This is per-file on purpose. An earlier version of this test took a `path`
    parameter and then ignored it, grepping conftest.py 48 identical times --
    it proved nothing about any specific file. This version actually targets
    each file, which is the invocation form that defeated the original
    directory-level gate.

    Uses rglob so a file added in a subdirectory cannot escape.
    """
    result = _collect_integration(opt_in=False, tmp_home=tmp_path, target=path)
    combined = (result.stdout or "") + (result.stderr or "")

    count = _collected_count(combined)
    assert count in (
        0,
        None,
    ), f"{path.name} collected {count} tests without opt-in.\n{combined[-2000:]}"
    assert (
        result.returncode != 0 or count == 0
    ), f"{path.name} ran successfully without opt-in.\n{combined[-2000:]}"


def test_the_gate_itself_is_present_and_blocks_at_collection_time():
    """The gate must stop collection, not merely skip at runtime.

    Several modules under tests/integration reach real cloud APIs at import,
    so a runtime skip would be too late.
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
        "not merely skip at runtime."
    )


# Files in tests/integration that execute real work at module scope. Naming one
# of these on the command line used to bypass the directory gate entirely,
# because pytest's collect_ignore_glob does not apply to paths given explicitly
# as arguments -- it only filters directory traversal.
_IMPORT_UNSAFE_FILES = (
    "test_aws_eks_debug.py",
    "test_eks_permissions.py",
)


@pytest.mark.parametrize("filename", _IMPORT_UNSAFE_FILES)
def test_naming_a_billable_file_directly_does_not_execute_it(tmp_path, filename):
    """`pytest tests/integration/<file>.py` must not run the module.

    Regression test for a hole found while red-teaming the original fix:
    collect_ignore_glob filters directory traversal but NOT explicitly-named
    command-line paths, so this invocation imported the module and executed its
    module-scope boto3 calls.
    """
    target = INTEGRATION_DIR / filename
    result = _collect_integration(opt_in=False, tmp_home=tmp_path, target=target)
    combined = (result.stdout or "") + (result.stderr or "")

    for evidence in (
        "Getting AWS credentials",
        "Testing EKS permissions",
        "Got credentials for account",
        "Cluster spec created",
        "botocore",
    ):
        assert evidence not in combined, (
            f"{filename} executed at import when named directly "
            f"(saw {evidence!r}).\n{combined[-2000:]}"
        )
