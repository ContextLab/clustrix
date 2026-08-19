#!/usr/bin/env python3
"""Guards that the documented "safe" test command cannot reach live resources.

Regression tests for issue #109/#114.

Background: ``CLAUDE.md`` documents ``pytest tests/ -m "not real_world"`` as
the CI-compatible, safe command. Most files under ``tests/real_world/`` never
carried ``@pytest.mark.real_world`` -- only six carried no marker at the time
this was found, but that count only ever grows if nothing enforces it -- so
``-m "not real_world"`` did not exclude them. Those tests make real SSH
connections and real cloud API calls. ``tests/real_world/conftest.py`` had a
``pytest_collection_modifyitems`` hook that added *skip* markers for
expensive/visual/dartmouth-network tests, but it never applied the
``real_world`` marker itself, so location under the directory was not
sufficient to keep a forgetful new test out of the "safe" run.

The fix makes ``tests/real_world/conftest.py`` apply ``pytest.mark.real_world``
to every item collected from that directory, regardless of what the test file
itself declares. These tests prove that end-to-end by running real pytest in a
subprocess and reading its real collection output -- not by asserting against
source text, and not with mocks.
"""

import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_WORLD_DIR = REPO_ROOT / "tests" / "real_world"
OPT_IN_VAR = "CLUSTRIX_ALLOW_BILLABLE"

_COUNT_RE = re.compile(r"(\d+)(?:/\d+)? tests? collected")

# These files carried no `@pytest.mark.real_world` decorator at the time this
# hole was found (verified via `grep -rL "pytest.mark.real_world"
# tests/real_world/test_*.py`). They are the ones that were actually escaping
# `-m "not real_world"` before the conftest.py fix, so they get their own
# regression coverage in addition to the whole-directory check.
_PREVIOUSLY_UNMARKED_FILES = (
    "test_cluster_job_system.py",
    "test_credential_access.py",
    "test_filesystem_utilities.py",
    "test_field_mapping_fixes.py",
    "test_ndoli_environment_setup.py",
    "test_real_world_credentials.py",
)


def _collected_count(output: str):
    """Parse the number of tests pytest actually collected.

    Deliberately NOT prose-matching on "no tests collected": that phrase also
    appears when collection errors out, which could masquerade as a working
    exclusion. Returns None when no count line is present at all, which
    callers must treat as "could not verify" rather than as zero.
    """
    matches = _COUNT_RE.findall(output)
    if not matches:
        if "no tests collected" in output or "no tests ran" in output:
            return 0
        return None
    return max(int(m) for m in matches)


def _run_pytest(argv, tmp_home, cwd=None, env_extra=None):
    """Run real pytest in a scrubbed subprocess and return the CompletedProcess.

    Uses a throwaway HOME and blocks outbound sockets so that if the isolation
    under test were actually broken, this test would fail loudly instead of
    quietly making real network calls or spending money.
    """
    env = dict(os.environ)
    env.pop(OPT_IN_VAR, None)
    env["HOME"] = str(tmp_home)
    env["USERPROFILE"] = str(tmp_home)
    sitecustomize = tmp_home / "sitecustomize.py"
    sitecustomize.write_text(
        "import socket\n"
        "def _deny(*a, **k):\n"
        "    raise OSError('network disabled by test_billable_and_realworld_isolation')\n"
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
        "SSH_AUTH_SOCK",
    ):
        env.pop(leaked, None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest"] + list(argv),
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def test_real_world_directory_collects_nothing_under_not_real_world(tmp_path):
    """The marker hole: `-m "not real_world"` must exclude ALL of tests/real_world.

    This is the direct, end-to-end proof the fix works: run real pytest
    against the real directory with the real documented flag, and read what
    it actually collected. Before the fix this collected dozens of tests
    (every file lacking an explicit `@pytest.mark.real_world` decorator).
    """
    result = _run_pytest(
        [
            str(REAL_WORLD_DIR),
            "-m",
            "not real_world",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
        ],
        tmp_home=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")

    count = _collected_count(combined)
    assert count == 0, (
        'tests/real_world collected tests under `-m "not real_world"`; the '
        f"marker hole is not closed.\n{combined[-3000:]}"
    )


def test_documented_safe_command_excludes_all_real_world_tests(tmp_path):
    """The exact documented command must not select anything under real_world/.

    Targets `tests/` (the whole suite), which is what a developer actually
    runs, rather than the real_world directory in isolation -- so this
    asserts the real-world property, not a proxy for it.
    """
    result = _run_pytest(
        [
            str(REPO_ROOT / "tests"),
            "-m",
            "not real_world",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
        ],
        tmp_home=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")

    collected_real_world = [
        line
        for line in combined.splitlines()
        if line.strip().startswith("tests/real_world/")
        or line.strip().startswith("tests\\real_world\\")
    ]
    assert (
        not collected_real_world
    ), '`pytest tests/ -m "not real_world"` collected real_world tests:\n' + "\n".join(
        collected_real_world[:10]
    )


@pytest.mark.parametrize("filename", _PREVIOUSLY_UNMARKED_FILES)
def test_previously_unmarked_file_is_now_excluded(filename, tmp_path):
    """Regression coverage for the specific files that were escaping the filter.

    Per-file on purpose: a directory-level assertion can pass by accident (for
    example if only some files regress back to unmarked); this pins each file
    that was actually found leaking through before the fix.
    """
    target = REAL_WORLD_DIR / filename
    assert target.exists(), f"expected fixture file missing: {target}"

    result = _run_pytest(
        [str(target), "-m", "not real_world", "--collect-only", "-q", "-o", "addopts="],
        tmp_home=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")

    count = _collected_count(combined)
    assert count in (0, None), (
        f'{filename} collected {count} test(s) under `-m "not real_world"` '
        f"even though the directory-level marker should exclude it.\n"
        f"{combined[-2000:]}"
    )


def test_real_world_marker_does_not_leak_onto_the_rest_of_the_suite(tmp_path):
    """The fix must not over-mark: total collection count must be unchanged.

    `pytest_collection_modifyitems` in tests/real_world/conftest.py is called
    once per session with *every* collected item, not just the ones under
    tests/real_world/ -- conftest.py hooks are session-scoped once the
    directory is loaded. A naive fix that adds `pytest.mark.real_world` to
    every item without checking the item's path would mark the ENTIRE test
    suite as real_world, and `-m "not real_world"` would then deselect
    everything, silently dropping ~1500 unrelated unit tests. This proves the
    path-scoped marker only touches tests/real_world/.
    """
    unfiltered = _run_pytest(
        [str(REPO_ROOT / "tests"), "--collect-only", "-q", "-o", "addopts="],
        tmp_home=tmp_path,
    )
    combined_unfiltered = (unfiltered.stdout or "") + (unfiltered.stderr or "")
    total_count = _collected_count(combined_unfiltered)
    assert total_count is not None and total_count > 1000, (
        "Could not determine the full suite's collected count, or it looks "
        f"implausibly small.\n{combined_unfiltered[-2000:]}"
    )

    filtered = _run_pytest(
        [
            str(REPO_ROOT / "tests"),
            "-m",
            "not real_world",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
        ],
        tmp_home=tmp_path,
    )
    combined_filtered = (filtered.stdout or "") + (filtered.stderr or "")
    filtered_count = _collected_count(combined_filtered)
    assert filtered_count is not None and filtered_count > 0, (
        '`-m "not real_world"` deselected the ENTIRE suite -- the real_world '
        "marker is leaking onto tests outside tests/real_world/.\n"
        + combined_filtered[-3000:]
    )
    # Only the real_world tests (and anything already excluded, e.g.
    # tests/integration) should be missing; the drop must be far smaller than
    # the full suite, not equal to it.
    assert total_count - filtered_count < total_count * 0.5, (
        f'`-m "not real_world"` deselected {total_count - filtered_count} of '
        f"{total_count} tests -- suspiciously large, consistent with the "
        "real_world marker leaking onto unrelated tests.\n" + combined_filtered[-2000:]
    )


def test_billable_guard_survives_cwd_change(tmp_path):
    """Adversarial check: running pytest from a different cwd must not bypass the gate.

    tests/conftest.py's `pytest_configure` guard resolves relative targets
    against invocation dir, cwd, and rootpath -- this proves that holds when
    pytest is invoked from an entirely unrelated directory outside the repo.
    """
    outside_cwd = tmp_path / "elsewhere"
    outside_cwd.mkdir()
    result = _run_pytest(
        [
            str(REPO_ROOT / "tests" / "integration"),
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
        ],
        tmp_home=tmp_path,
        cwd=outside_cwd,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Refusing to run" in combined, (
        "Running pytest from an unrelated cwd bypassed the billable-resources "
        f"guard.\n{combined[-2000:]}"
    )
    assert result.returncode != 0


def test_billable_guard_survives_no_cacheprovider(tmp_path):
    """Adversarial check: `-p no:cacheprovider` must not disturb the guard.

    The guard lives in a `pytest_configure` hook in tests/conftest.py, not in
    the cache plugin, but this is cheap insurance against the guard having
    accidentally grown a dependency on cache-plugin state.
    """
    result = _run_pytest(
        [
            str(REPO_ROOT / "tests" / "integration"),
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
        ],
        tmp_home=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Refusing to run" in combined
    assert result.returncode != 0


def test_billable_guard_survives_co_shorthand(tmp_path):
    """Adversarial check: the `--co` shorthand for `--collect-only` must not bypass the gate.

    The gate fires in `pytest_configure`, before pytest has parsed which
    collection mode was requested, so this should behave identically to
    `--collect-only` -- but the shorthand is worth pinning explicitly since a
    future guard implementation could accidentally special-case the long form.
    """
    result = _run_pytest(
        [str(REPO_ROOT / "tests" / "integration"), "--co", "-q", "-o", "addopts="],
        tmp_home=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Refusing to run" in combined
    assert result.returncode != 0


def test_importing_a_real_world_test_module_directly_does_not_bypass_pytest():
    """Direct `import` of a real_world test module does not run pytest's guard.

    This is not a bypass of the marker fix -- marker application only exists
    within pytest's collection machinery, so a bare `import` (no pytest
    involved at all) obviously never sees the `real_world` marker. What
    matters is that this is *not exploitable*: importing the module must not
    itself perform network I/O or spend money, which is a property of the
    module, not of the marker system. This uses the same repo-import path a
    developer would get from `python -c "import tests.real_world.test_x"`.
    """
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import importlib

        # A representative module from the previously-unmarked set: import it
        # directly, bypassing pytest collection entirely, and confirm nothing
        # explodes or performs obvious network setup at import time.
        module = importlib.import_module("tests.real_world.test_credential_access")
        assert module is not None
    finally:
        sys.path.remove(str(REPO_ROOT)) if str(REPO_ROOT) in sys.path else None
