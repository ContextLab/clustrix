#!/usr/bin/env python3
"""``scripts/run_real_world_tests.py`` must exit non-zero when tests fail.

Regression guard for issue #147.

The pre-push hook gates on this script:
``if ! python scripts/run_real_world_tests.py --filesystem; then ...``. Every
category method returned ``True``/``False`` and ``main()`` threw the values
away, so a failing category printed "failed" and the script still exited 0 --
the hook could never block a push. The same bug had a twin: only ``stdout``
was printed on failure, so a collection error (which pytest writes to
``stderr``) told the operator something broke and not what.

Both are fixed, and ``grep -rn run_real_world_tests tests/`` was empty, so
nothing pinned either. This module pins both.

**No real-world test runs here.** The script derives every path from its own
location -- ``project_root = Path(__file__).parent.parent`` -- so a verbatim
copy of it placed in a throwaway ``scripts/`` directory looks for
``tests/real_world/`` inside that throwaway tree instead of the repo's. The
copy is taken from the real file at test time, so what runs is the shipped
code, byte for byte, driven as a real subprocess against a real pytest that
really passes or really fails. Nothing is faked and nothing touches an SSH
host, a cluster or a cloud API.
"""

import pathlib
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run_real_world_tests.py"

#: The file ``--filesystem`` runs. In the throwaway tree this is ours to
#: write; in the repo it is the real filesystem suite, which we never touch.
TARGET = pathlib.Path("tests") / "real_world" / "test_filesystem_real.py"

#: The file ``--api`` runs. Used only to give one category something that
#: passes while another fails.
API_TARGET = pathlib.Path("tests") / "real_world" / "test_cloud_apis_real.py"

FAILING_TEST = (
    "def test_that_really_fails():\n"
    "    # A genuine assertion failure, not a skip and not an error.\n"
    "    assert 2 + 2 == 5\n"
)

PASSING_TEST = "def test_that_really_passes():\n    assert 2 + 2 == 4\n"

#: Broken at import time, so pytest reports a collection error. That is the
#: case whose diagnosis went to stderr and used to be swallowed.
UNCOLLECTABLE_TEST = "import a_module_that_does_not_exist  # noqa: F401\n"


def _throwaway_tree(tmp_path, filesystem_source, api_source=None):
    """A miniature project the runner will treat as its own.

    Returns the path of the copied script. ``shutil.copy`` rather than a
    rewritten stub: if someone changes the runner, this test exercises the
    change instead of a stale transcription of it.
    """
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / RUNNER.name
    shutil.copy(RUNNER, script)

    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    target.write_text(filesystem_source, encoding="utf-8")

    if api_source is not None:
        api_target = tmp_path / API_TARGET
        api_target.write_text(api_source, encoding="utf-8")

    return script


def _run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(script.parent.parent),
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture(autouse=True)
def runner_exists():
    assert RUNNER.is_file(), (
        f"{RUNNER} is gone. If the runner moved, this guard must follow it, "
        "not be deleted -- the pre-push hook still gates on its exit code."
    )


def test_runner_exits_non_zero_when_a_category_fails(tmp_path):
    """The whole point: a failing category must fail the process."""
    script = _throwaway_tree(tmp_path, FAILING_TEST)

    result = _run(script, "--filesystem")

    assert result.returncode != 0, (
        "the runner reported a failing category and still exited 0, so "
        "`if ! python scripts/run_real_world_tests.py --filesystem` in the "
        f"pre-push hook can never fire (issue #147).\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}"
    )
    assert "Filesystem tests failed" in result.stdout


def test_runner_exits_zero_when_the_category_passes(tmp_path):
    """The control. Without it the test above passes for any exit code.

    An exit-code guard that only ever sees failures would also pass against
    a runner that exited 1 unconditionally, which would break every push.
    """
    script = _throwaway_tree(tmp_path, PASSING_TEST)

    result = _run(script, "--filesystem")

    assert result.returncode == 0, (
        f"a passing category must not fail the process.\nstdout:\n"
        f"{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Filesystem tests passed" in result.stdout


def test_runner_reports_what_failed_not_merely_that_it_failed(tmp_path):
    """A collection error goes to stderr; the operator must still see it.

    This is the other half of #147: the four categories the pre-push hook
    runs all reported failure with an empty body, because only stdout was
    printed.
    """
    script = _throwaway_tree(tmp_path, UNCOLLECTABLE_TEST)

    result = _run(script, "--filesystem")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "a_module_that_does_not_exist" in combined, (
        "the runner swallowed the reason for the failure; an operator is "
        f"told only that something broke.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "(no output captured)" not in result.stdout


def test_one_failing_category_fails_the_run_beside_a_passing_one(tmp_path):
    """A passing category must not rescue a failing one.

    ``main()`` collects every outcome and exits 1 if *any* is False. That
    ``all(...)`` is one character away from ``any(...)``, which would let a
    real failure through whenever anything else in the same invocation
    happened to pass.
    """
    script = _throwaway_tree(tmp_path, FAILING_TEST, api_source=PASSING_TEST)

    result = _run(script, "--api", "--filesystem")

    assert "API tests passed" in result.stdout, (
        f"the passing half did not run.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "Filesystem tests failed" in result.stdout
    assert result.returncode != 0, (
        "one category failed and the process still succeeded.\nstdout:\n"
        f"{result.stdout}\nstderr:\n{result.stderr}"
    )
