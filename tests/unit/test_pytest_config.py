"""Guard the pytest configuration itself against silent shadowing.

See issue #130.

For a long time this repo carried two pytest config files, and *neither* was
in effect. ``pytest.ini`` opened with ``[tool:pytest]`` -- a section header
that is only valid inside ``setup.cfg``. pytest still *selected* ``pytest.ini``
as the config file, and having selected one it stops searching, so
``pyproject.toml``'s ``[tool.pytest.ini_options]`` was never read either.

The failure mode is nasty precisely because it is silent: ``addopts``,
``testpaths``, ``filterwarnings``, every ``markers`` entry and ``--strict-markers``
were all declared and all inert. Nothing warns about it. The only way to notice
is to check which file pytest actually loaded.

These tests do that check, so a stray ``pytest.ini`` / ``tox.ini`` / ``setup.cfg``
added later cannot quietly take over again.
"""

import pathlib

import pytest

EXPECTED_CONFIG_NAME = "pyproject.toml"

# Config files pytest will prefer over pyproject.toml if they exist at the
# rootdir, in pytest's own precedence order. Any of these silently wins.
SHADOWING_CONFIG_NAMES = ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg")

# Markers this project defines and relies on. Registration is what makes
# ``-m <marker>`` a usable selector and what lets --strict-markers catch typos.
REQUIRED_MARKERS = (
    "real_world",
    "expensive",
    "integration",
    "dartmouth_network",
    "performance",
    "slow",
    "unit",
)


def test_pytest_reads_the_intended_config(pytestconfig):
    """pytest must actually be loading pyproject.toml, not something else."""
    assert pytestconfig.inipath is not None, (
        "pytest loaded no config file at all -- pyproject.toml's "
        "[tool.pytest.ini_options] is not being applied (see #130)"
    )
    assert pytestconfig.inipath.name == EXPECTED_CONFIG_NAME, (
        f"pytest loaded {pytestconfig.inipath}; a stray config file is "
        f"shadowing {EXPECTED_CONFIG_NAME} (see #130)"
    )


def test_no_shadowing_config_file_exists(pytestconfig):
    """Fail loudly if a higher-precedence config file reappears in the repo.

    ``test_pytest_reads_the_intended_config`` only catches a shadow that is
    live during *this* run. A config file that exists but happens not to win
    (for instance because the run was launched from a subdirectory with a
    different rootdir) would slip past it, so check the tree directly.
    """
    rootdir = pathlib.Path(str(pytestconfig.rootpath))
    strays = [name for name in SHADOWING_CONFIG_NAMES if (rootdir / name).exists()]
    assert not strays, (
        f"found {strays} at {rootdir}; pytest prefers these over "
        f"{EXPECTED_CONFIG_NAME} and stops searching once one is selected, so "
        f"{EXPECTED_CONFIG_NAME} would be silently ignored (see #130)"
    )


def test_project_markers_are_registered(pytestconfig):
    """Every marker the suite uses must be declared in the live config."""
    # getini("markers") also returns plugin-provided markers such as
    # "timeout(timeout, method=None, ...): ..." -- strip the argspec as well as
    # the description before comparing names.
    registered = {
        entry.split(":")[0].split("(")[0].strip()
        for entry in pytestconfig.getini("markers")
    }
    missing = [name for name in REQUIRED_MARKERS if name not in registered]
    assert not missing, (
        f"markers {missing} are not registered. Unregistered markers are not "
        f"usable as -m selectors and become hard errors under --strict-markers "
        f"(see #130). Declare them in {EXPECTED_CONFIG_NAME}."
    )


def test_strict_markers_is_active(pytestconfig):
    """--strict-markers must be live, or marker typos silently do nothing.

    This is the setting that makes the two tests above self-enforcing: without
    it, a mistyped ``@pytest.mark.reel_world`` is accepted as a no-op and the
    test it decorates quietly stops being selectable.

    --strict-markers reaches us through ``addopts``, so a run that deliberately
    clears addopts (``pytest -o addopts=``, used when comparing collection
    counts across config changes) legitimately has it off. Detect that override
    rather than reporting a failure the config is not responsible for.
    """
    overrides = getattr(pytestconfig.option, "override_ini", None) or []
    if any(str(entry).startswith("addopts=") for entry in overrides):
        pytest.skip("addopts overridden on the command line with -o addopts=")
    assert pytestconfig.option.strict_markers, (
        "--strict-markers is not active; a mistyped marker is silently ignored "
        "instead of erroring (see #130)"
    )
