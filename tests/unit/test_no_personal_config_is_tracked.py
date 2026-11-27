"""No working-directory clustrix configuration is tracked in the repository.

``clustrix.yml.local-fossil`` sat at the repository root: somebody's own
``./clustrix.yml`` -- real username, hostname and key path -- renamed out of
the way and then committed, because ``.gitignore`` named ``clustrix.yml``
exactly and nothing else. A file like that is personal data in a public
repository, and under its original name it is also a configuration every
clone would silently adopt.

Asked of the real repository with the real ``git``.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``clustrix.yml``, ``clustrix.yaml`` or ``clustrix.json`` at the root, with
#: or without something appended.
PERSONAL_CONFIG = re.compile(r"^clustrix\.(yml|yaml|json)(\..*|~)?$")


def _git(*arguments, check=True):
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout (an sdist has no index to inspect)")
    return subprocess.run(
        ["git", *arguments], cwd=REPO_ROOT, capture_output=True, text=True, check=check
    )


def test_no_working_directory_config_is_tracked():
    listing = _git("ls-files").stdout.splitlines()
    tracked = [name for name in listing if "/" not in name]

    offenders = [name for name in tracked if PERSONAL_CONFIG.match(name)]

    assert offenders == [], (
        "a personal clustrix configuration is tracked at the repository "
        f"root: {offenders}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "clustrix.yml",
        "clustrix.yml.local-fossil",
        "clustrix.yml.bak",
        "clustrix.yaml",
        "clustrix.yaml.old",
        "clustrix.json",
        "clustrix.json.mine",
    ],
)
def test_gitignore_covers_every_spelling(name):
    """``git check-ignore`` is the real answer to "would git add skip this"."""
    result = _git("check-ignore", "--no-index", "-q", name, check=False)

    assert result.returncode == 0, f"{name} would be picked up by `git add`"
