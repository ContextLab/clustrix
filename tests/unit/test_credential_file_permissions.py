#!/usr/bin/env python3
"""Credential files must never exist world-readable, not even briefly.

Regression guard for issue #111.

``clustrix/cli_credentials.py`` and ``clustrix/credential_manager.py`` both
used to write a file and *then* narrow it::

    path.write_text(secrets)
    path.chmod(0o600)

Under the usual umask the first line creates the file at mode 0644 with the
credentials already in it, and only the second line closes it. Anything else
running as another local user can read it in between. Reproduced directly::

    >>> os.umask(0o022)
    >>> f.write_text("...")
    >>> oct(stat.S_IMODE(f.stat().st_mode))
    '0o644'
    >>> f.chmod(0o600)

Both call sites now go through ``write_text_securely``, which hands the mode
to ``os.open()`` so the file is never wider than 0600 even momentarily --
the same shape as ``clustrix.config._write_config_file_securely``.

The tests come in two halves, because neither alone is sufficient:

* the **behavioural** half runs the real writers against real files on disk
  under a deliberately permissive umask and checks the resulting mode. It
  proves the end state is right, but it passes against the buggy code too,
  because ``chmod`` does eventually run.
* the **structural** half is the one that would have caught the bug. The
  window exists precisely because a ``chmod`` follows a write, so these
  modules may not call ``chmod`` at all -- the correct pattern has no use
  for it. ``test_the_structural_guard_catches_a_planted_chmod`` plants the
  old code in a scratch file and proves the guard reports it.

No secret-shaped literals appear below: the fixture content uses the
``<redacted>`` spelling that ``tests/unit/test_check_for_secrets.py``
already treats as a placeholder.
"""

import ast
import os
import pathlib
import stat

import pytest

from clustrix.cli_credentials import _write_credentials_to_env_file
from clustrix.credential_manager import (
    FlexibleCredentialManager,
    write_text_securely,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The two modules that write credential files. Neither may narrow
#: permissions after the fact; both must create the file already narrow.
CREDENTIAL_WRITERS = (
    "clustrix/cli_credentials.py",
    "clustrix/credential_manager.py",
)

#: A deliberately permissive umask. With this in effect a plain
#: ``write_text`` produces mode 0666, so a test that still sees 0600 is
#: seeing the ``os.open()`` mode rather than an accident of the environment.
WIDE_OPEN_UMASK = 0o000


@pytest.fixture
def permissive_umask():
    """Run the body under a umask that hides nothing.

    Without this the developer's own umask could mask the bug: at 0o077 even
    the broken write produced 0600 and the test would have passed against
    the code it is supposed to reject.
    """
    previous = os.umask(WIDE_OPEN_UMASK)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


# --------------------------------------------------------------------------
# Behavioural: real writers, real files, permissive umask.
# --------------------------------------------------------------------------


def test_write_text_securely_creates_an_owner_only_file(permissive_umask, tmp_path):
    target = tmp_path / "secrets.env"
    write_text_securely(target, 'password = "<redacted>"\n')

    assert _mode(target) == 0o600, (
        "a freshly created credential file must be owner-only; got "
        f"{oct(_mode(target))} under umask {oct(WIDE_OPEN_UMASK)}"
    )
    assert target.read_text(encoding="utf-8") == 'password = "<redacted>"\n'


def test_write_text_securely_tightens_a_pre_existing_wide_file(
    permissive_umask, tmp_path
):
    """Overwriting somebody else's loose file must not inherit its mode."""
    target = tmp_path / "secrets.env"
    target.write_text("stale\n", encoding="utf-8")
    target.chmod(0o666)

    write_text_securely(target, 'password = "<redacted>"\n')

    assert _mode(target) == 0o600
    assert "stale" not in target.read_text(encoding="utf-8")


def test_write_text_securely_leaks_no_descriptor_when_open_succeeds(tmp_path):
    """The helper owns a raw fd; it must not leave one behind.

    ``os.open`` returns a descriptor that nothing else closes, so a mistake
    here is a real resource leak (and on Windows makes the file
    undeletable). Compare the process's open-descriptor set before and
    after.
    """
    target = tmp_path / "secrets.env"
    before = set(os.listdir("/dev/fd"))
    write_text_securely(target, 'password = "<redacted>"\n')
    after = set(os.listdir("/dev/fd"))

    assert after - before == set(), f"descriptors leaked: {sorted(after - before)}"


def test_write_text_securely_closes_the_descriptor_when_it_cannot_proceed(tmp_path):
    """A failure between open() and fdopen() must still close the fd."""
    missing = tmp_path / "no-such-dir" / "secrets.env"
    before = set(os.listdir("/dev/fd"))

    with pytest.raises(OSError):
        write_text_securely(missing, "unused")

    after = set(os.listdir("/dev/fd"))
    assert after - before == set(), f"descriptors leaked: {sorted(after - before)}"


def test_env_template_is_created_owner_only(permissive_umask, tmp_path):
    """The template ``FlexibleCredentialManager`` drops on first use."""
    config_dir = tmp_path / "clustrix-config"
    manager = FlexibleCredentialManager(config_dir=config_dir)

    assert manager.env_file.exists(), "the manager is supposed to seed a template"
    assert _mode(manager.env_file) == 0o600, (
        "the credential template must be owner-only; got "
        f"{oct(_mode(manager.env_file))}"
    )


def test_written_credentials_are_owner_only_and_leave_no_temp_file(
    permissive_umask, tmp_path
):
    """The interactive setup path, which writes actual user credentials."""
    env_file = tmp_path / ".env"

    assert _write_credentials_to_env_file(
        env_file, {"CLUSTRIX_SSH_PASSWORD": "<redacted>"}
    )

    assert _mode(env_file) == 0o600, (
        f"credentials were left at {oct(_mode(env_file))}, readable by other "
        "local users"
    )
    assert "CLUSTRIX_SSH_PASSWORD=<redacted>" in env_file.read_text(encoding="utf-8")

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != ".env"]
    assert leftovers == [], (
        "the scratch file the atomic write uses also holds the credentials "
        f"and must not survive: {leftovers}"
    )


def test_rewriting_credentials_keeps_the_file_owner_only(permissive_umask, tmp_path):
    """The second write goes through ``replace()``; its mode must survive."""
    env_file = tmp_path / ".env"
    env_file.write_text("# existing\n", encoding="utf-8")
    env_file.chmod(0o644)

    assert _write_credentials_to_env_file(
        env_file, {"CLUSTRIX_SSH_PASSWORD": "<redacted>"}
    )

    assert _mode(env_file) == 0o600
    assert "# existing" in env_file.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Structural: the half that fails against the pre-fix code.
# --------------------------------------------------------------------------


def _chmod_calls(rel, text):
    """Every ``chmod`` call in ``text``, as ``rel:lineno`` strings.

    ``path.chmod(...)``, ``os.chmod(...)`` and ``os.fchmod(...)`` all reduce
    to the same attribute name, so this is not defeated by importing the
    function differently. It is an ``ast`` walk rather than a substring
    search so that the word appearing in a comment or docstring -- as it
    does in the rationale above ``write_text_securely`` -- is not a hit.
    """
    hits = []
    tree = ast.parse(text, filename=rel)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name) else None
        )
        if name in {"chmod", "fchmod", "lchmod"}:
            hits.append(f"{rel}:{node.lineno}: calls {name}(...)")
    return hits


def test_credential_writers_never_narrow_permissions_after_the_fact():
    """No ``chmod`` in the credential writers -- the mode goes to ``open``.

    ``write_text_securely`` lives in ``credential_manager.py`` and is
    permitted its own ``fchmod`` on an already-0600 descriptor, which is why
    the guard exempts that one function by name rather than the whole file.
    """
    hits = []
    for rel in CREDENTIAL_WRITERS:
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
        helper_lines = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "write_text_securely":
                helper_lines.update(
                    range(node.lineno, (node.end_lineno or node.lineno) + 1)
                )
        hits.extend(
            hit
            for hit in _chmod_calls(rel, text)
            if int(hit.split(":")[1]) not in helper_lines
        )

    assert not hits, (
        "A credential file that is chmod()ed after it is written existed at "
        "the umask default -- world readable, with the secrets already in it "
        "-- until the chmod landed (issue #111). Create it through "
        "write_text_securely() instead, which passes the mode to os.open(). "
        "Offending lines:\n  " + "\n  ".join(hits)
    )


def test_the_structural_guard_catches_a_planted_chmod(tmp_path):
    """Plant the exact pre-fix code and prove the guard reports it.

    A guard that cannot fail reads as coverage while checking nothing, so
    the failure path is exercised here against the same parser the real
    check uses.
    """
    planted = tmp_path / "regressed.py"
    planted.write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def write(path: Path, template: str) -> None:\n"
        '    path.write_text(template, encoding="utf-8")\n'
        "    path.chmod(0o600)  # too late\n",
        encoding="utf-8",
    )

    hits = _chmod_calls("regressed.py", planted.read_text(encoding="utf-8"))
    assert hits == ["regressed.py:6: calls chmod(...)"], hits

    with pytest.raises(AssertionError):
        assert not hits, "the planted violation must trip the same assertion"


def test_the_structural_guard_ignores_the_word_in_prose(tmp_path):
    """Comments and docstrings that mention chmod are not violations.

    The rationale comment above ``write_text_securely`` says the word
    several times. A substring guard would flag it, get suppressed, and stop
    guarding anything.
    """
    innocent = tmp_path / "prose.py"
    innocent.write_text(
        '"""We deliberately do not chmod() after writing."""\n'
        "\n"
        "\n"
        "def write(path, text):\n"
        "    # chmod would be too late here\n"
        '    fd = open(path, "w")\n'
        "    fd.write(text)\n"
        "    fd.close()\n",
        encoding="utf-8",
    )

    assert _chmod_calls("prose.py", innocent.read_text(encoding="utf-8")) == []
