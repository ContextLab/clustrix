"""``pyproject.toml`` is the one description of the package, and it is right.

What this pins, each of which was wrong:

* ``setup.py`` repeated the metadata and had drifted from ``pyproject.toml``
  (its ``dev`` extra was missing packages). It is a shim now.
* The suite runs on Python 3.13 and the classifiers stopped at 3.12.
* ``[tool.flake8]`` in ``pyproject.toml`` said 88 columns; flake8 does not
  read that file, and ``.flake8`` said 120. One configuration.
* ``setuptools-scm`` was a build requirement nothing used for versioning; its
  side effect was a source distribution holding every tracked file. Without
  it the default was ``tests/test*.py`` alone -- 35 modules without their
  conftest. The sdist is built for real here and its contents checked.
"""

import ast
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import clustrix

REPO_ROOT = Path(__file__).resolve().parents[2]

# ``tomllib`` is standard from 3.11; on 3.10 the same parser is ``tomli``,
# which pytest itself depends on there.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib


@pytest.fixture(scope="module")
def pyproject():
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_setup_py_carries_no_metadata_of_its_own():
    tree = ast.parse((REPO_ROOT / "setup.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setup"
    ]

    assert len(calls) == 1
    assert calls[0].args == [] and calls[0].keywords == [], (
        "setup.py passes metadata to setup(); pyproject.toml is the single "
        "source, and a second copy is how the two drifted apart"
    )


def test_the_interpreter_running_the_suite_is_a_declared_one(pyproject):
    declared = {
        match.group(1)
        for classifier in pyproject["project"]["classifiers"]
        if (
            match := re.fullmatch(
                r"Programming Language :: Python :: (3\.\d+)", classifier
            )
        )
    }
    running = f"{sys.version_info.major}.{sys.version_info.minor}"

    print("declared:", sorted(declared), "running:", running)
    assert "3.13" in declared
    assert running in declared, (
        f"the suite is running on Python {running}, which the classifiers "
        "do not list"
    )


def test_the_version_is_the_same_everywhere_it_is_written(pyproject):
    conf = (REPO_ROOT / "docs" / "source" / "conf.py").read_text(encoding="utf-8")
    documented = re.search(r'^release = "([^"]+)"', conf, re.MULTILINE).group(1)

    assert pyproject["project"]["version"] == clustrix.__version__ == documented


def test_flake8_has_exactly_one_configuration(pyproject):
    assert "flake8" not in pyproject.get("tool", {}), (
        "flake8 does not read pyproject.toml; a [tool.flake8] table there is "
        "a second, ignored configuration"
    )
    assert (REPO_ROOT / ".flake8").exists()
    assert not (REPO_ROOT / "tox.ini").exists()
    assert not (REPO_ROOT / "setup.cfg").exists()


def test_the_build_does_not_require_setuptools_scm(pyproject):
    requirements = " ".join(pyproject["build-system"]["requires"]).lower()

    assert "setuptools-scm" not in requirements
    assert "setuptools_scm" not in requirements


def test_the_source_distribution_ships_the_package_and_not_the_repository(tmp_path):
    """A real ``python -m build --sdist``, and the real archive it produces."""
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path), "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(result.stdout[-2000:], result.stderr[-2000:])
    assert result.returncode == 0

    (archive,) = tmp_path.glob("clustrix-*.tar.gz")
    with tarfile.open(archive) as tar:
        names = [name.split("/", 1)[1] for name in tar.getnames() if "/" in name]
    print(f"{len(names)} members")

    top_level = {name.split("/", 1)[0] for name in names}
    assert top_level == {
        "CHANGELOG.md",
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "clustrix",
        "clustrix.egg-info",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
    }, sorted(top_level)
    # Every module of the package is there...
    shipped = {name for name in names if name.startswith("clustrix/")}
    tracked = subprocess.run(
        ["git", "ls-files", "clustrix/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked and set(tracked) <= shipped
    # ...and none of the test suite, in whole or in part.
    assert not [name for name in names if name.startswith("tests")]
