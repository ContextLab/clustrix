#!/usr/bin/env python3
"""Guard: paramiko's auto-add host key policy must not reappear in Python source.

Regression guard for issue #148.

That policy silently trusts whatever host key a server offers on first
contact, which is precisely the machine-in-the-middle hole
``clustrix/ssh_security.py`` exists to close. Production code was fixed
first; the test suite kept 37 call sites of its own, which meant every SSH
connection the real-world suite opened was still unverified, and any new test
copied from a neighbouring file inherited the hole.

Only two files in the repository may name it:

* ``clustrix/ssh_security.py`` -- the single implementation, which uses it to
  honour an explicit ``ssh_host_key_policy="auto_add"`` opt-in.
* ``tests/unit/test_host_key_policy.py`` -- the test *for* that policy, which
  must be able to assert on the object it produces.

Everything else must call ``configure_host_key_policy(client, config)``.

This module never writes the literal class name it searches for; the needle is
assembled at runtime so that the guard cannot match itself and so that the
allowlist below stays honest rather than growing an entry for this file.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Assembled rather than written out, so this file is not itself a match.
NEEDLE = "Auto" + "AddPolicy"

#: The only Python files permitted to name the auto-add policy class.
ALLOWED = frozenset(
    {
        "clustrix/ssh_security.py",
        "tests/unit/test_host_key_policy.py",
    }
)

#: Directories that hold Python sources this repository is responsible for.
SEARCH_ROOTS = ("clustrix", "tests", "scripts")

_SKIP_DIR_PARTS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache"})


def _python_files(repo_root):
    for root in SEARCH_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if _SKIP_DIR_PARTS & set(path.parts):
                continue
            yield path


def _violations(repo_root=REPO_ROOT):
    hits = []
    for path in _python_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        if rel in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if NEEDLE in line:
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


def test_search_actually_finds_the_allowed_uses():
    """The scan must be able to see the two legitimate uses.

    Without this, a broken glob or a wrong repo root would make the real
    check below pass vacuously -- a guard that can never fail is worse than
    no guard, because it reads as coverage.
    """
    found = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in _python_files(REPO_ROOT)
        if NEEDLE in p.read_text(encoding="utf-8", errors="replace")
    }
    assert found == set(ALLOWED), (
        "The allowlist and reality have drifted apart. Files naming "
        f"{NEEDLE} that the scan found: {sorted(found)}; allowlist: "
        f"{sorted(ALLOWED)}."
    )


def test_no_auto_add_policy_outside_allowlist():
    """No Python source outside the allowlist may name the auto-add policy."""
    hits = _violations()
    assert not hits, (
        f"paramiko.{NEEDLE} must not be used outside {sorted(ALLOWED)}. It "
        "silently trusts unknown SSH host keys, which is exactly the "
        "machine-in-the-middle hole clustrix/ssh_security.py closes. Call "
        "configure_host_key_policy(client, config) instead. Offending "
        "lines:\n  " + "\n  ".join(hits)
    )


def test_guard_detects_a_planted_violation(tmp_path):
    """Prove the guard fails when a violation exists.

    Plants a real file naming the forbidden policy inside a stand-in repo
    tree, so the failure path is exercised against actual files on disk
    rather than merely asserted about.
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "clustrix").mkdir(parents=True)
    # Allowlisted: this one must NOT be reported.
    (fake_repo / "clustrix" / "ssh_security.py").write_text(
        f"client.set_missing_host_key_policy(paramiko.{NEEDLE}())\n"
    )
    # Not allowlisted: this one must be reported.
    (fake_repo / "clustrix" / "sneaky_new_backend.py").write_text(
        f"client.set_missing_host_key_policy(paramiko.{NEEDLE}())\n"
    )

    hits = _violations(fake_repo)
    assert len(hits) == 1, hits
    assert hits[0].startswith("clustrix/sneaky_new_backend.py:1:")

    with pytest.raises(AssertionError):
        assert not hits, "planted violation must trip the same assertion"
