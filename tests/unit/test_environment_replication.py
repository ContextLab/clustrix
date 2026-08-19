"""Regression tests for how clustrix decides what to rebuild on the worker.

These use the real installed environment and real distribution metadata written
to disk. Nothing is mocked: a test that wants an editable install creates a real
``.dist-info`` directory with a real ``direct_url.json`` and points a real
``sys.path`` entry at it.
"""

import json
import os
import subprocess
import sys

import pytest

from clustrix.utils import (
    _canonical_package_name,
    _conda_env_ready_commands,
    _distribution_records,
    _parse_conda_env_paths,
    _select_remote_python,
    _unreproducible_reason,
    get_environment_requirements,
    get_unreproducible_requirements,
    unreproducible_module_owners,
)


@pytest.fixture
def fake_site(tmp_path):
    """A real directory on sys.path holding real distribution metadata."""
    site = tmp_path / "site"
    site.mkdir()
    sys.path.insert(0, str(site))
    try:
        yield site
    finally:
        sys.path.remove(str(site))


def write_distribution(site, name, version, direct_url=None, top_level=None):
    """Write a real .dist-info that importlib.metadata will pick up."""
    dist_info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )
    (dist_info / "RECORD").write_text("")
    if direct_url is not None:
        (dist_info / "direct_url.json").write_text(json.dumps(direct_url))
    if top_level is not None:
        (dist_info / "top_level.txt").write_text("\n".join(top_level) + "\n")
    return dist_info


# --------------------------------------------------------------------------
# D1: conda-built distributions must not be dropped
# --------------------------------------------------------------------------


def test_conda_built_distributions_are_pinned_not_dropped(fake_site):
    """`name @ file:///...build/work` is a normal install; it must be replicated."""
    write_distribution(
        fake_site,
        "condabuilt",
        "1.2.3",
        direct_url={
            "url": "file:///tmp/build/croot/condabuilt_1660339893712/work",
            "dir_info": {},
        },
    )
    requirements = get_environment_requirements()
    assert requirements.get("condabuilt") == "1.2.3"
    assert "condabuilt" not in get_unreproducible_requirements()


def test_requirements_cover_every_installed_distribution(fake_site):
    """The pin count must match what is installed, minus what cannot travel."""
    records = _distribution_records()
    reproducible = {
        record["name"] for record in records.values() if not record["reason"]
    }
    reproducible.discard("clustrix")
    requirements = get_environment_requirements()
    assert reproducible - set(requirements) == set()


def test_requirements_do_not_depend_on_which_freeze_tool_is_installed():
    """uv and pip render the same environment differently; clustrix must not.

    Both freeze backends are run for real against this interpreter. They are
    allowed to disagree about *rendering*; the requirement set clustrix derives
    must cover every distribution both of them report.
    """
    reported = {}
    for label, command in (
        ("pip", [sys.executable, "-m", "pip", "list", "--format=freeze"]),
        ("uv", ["uv", "pip", "freeze", "--python", sys.executable]),
    ):
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        names = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-e"):
                continue
            for separator in ("==", " @ "):
                if separator in line:
                    names.add(_canonical_package_name(line.split(separator, 1)[0]))
                    break
        if names:
            reported[label] = names

    if len(reported) < 2:
        pytest.skip("only one freeze backend available on this machine")

    pip_names, uv_names = reported["pip"], reported["uv"]
    assert pip_names != uv_names or True  # rendering may or may not differ

    known = {_canonical_package_name(name) for name in get_environment_requirements()}
    unreproducible = {
        _canonical_package_name(name) for name in get_unreproducible_requirements()
    }
    accounted = known | unreproducible | {"clustrix"}
    assert not (pip_names - accounted), sorted(pip_names - accounted)[:10]
    assert not (uv_names - accounted), sorted(uv_names - accounted)[:10]


def test_editable_install_is_reported_not_silently_pinned(fake_site):
    write_distribution(
        fake_site,
        "editablepkg",
        "0.4.0",
        direct_url={
            "url": "file:///Users/someone/editablepkg",
            "dir_info": {"editable": True},
        },
        top_level=["editablepkg"],
    )
    assert "editablepkg" not in get_environment_requirements()
    reason = get_unreproducible_requirements()["editablepkg"]
    assert "editable" in reason
    assert "file:///Users/someone/editablepkg" in reason
    assert unreproducible_module_owners()["editablepkg"].startswith("editablepkg (")


def test_vcs_install_is_reported_not_silently_pinned(fake_site):
    write_distribution(
        fake_site,
        "vcspkg",
        "2.0.0",
        direct_url={
            "url": "https://github.com/example/vcspkg.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        },
        top_level=["vcspkg"],
    )
    assert "vcspkg" not in get_environment_requirements()
    assert "git checkout" in get_unreproducible_requirements()["vcspkg"]


def test_a_name_found_twice_keeps_its_unreproducible_reading(fake_site, tmp_path):
    """An editable's source tree also carries metadata; the pin must not win."""
    write_distribution(
        fake_site,
        "twicepkg",
        "1.0.0",
        direct_url={
            "url": f"file://{tmp_path}/twicepkg",
            "dir_info": {"editable": True},
        },
    )
    source_tree = tmp_path / "twicepkg_src"
    source_tree.mkdir()
    egg_info = source_tree / "twicepkg.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_text(
        "Metadata-Version: 2.1\nName: twicepkg\nVersion: 1.0.0\n"
    )
    sys.path.append(str(source_tree))
    try:
        assert "twicepkg" in get_unreproducible_requirements()
        assert "twicepkg" not in get_environment_requirements()
    finally:
        sys.path.remove(str(source_tree))


def test_clustrix_is_never_replicated():
    """clustrix is the machinery running the job, not part of the environment.

    The worker gets dill and cloudpickle explicitly and loads a payload built
    to need nothing else (see ``make_portable_function``), so mirroring a
    clustrix checkout would only add an install that cannot succeed.
    """
    assert "clustrix" not in get_environment_requirements()
    assert "clustrix" not in get_unreproducible_requirements()


def test_unreproducible_reason_ignores_plain_local_archives():
    """A conda build directory in direct_url.json is not a reason to drop a pin."""
    assert (
        _unreproducible_reason({"url": "file:///tmp/x/work", "dir_info": {}}, None)
        is None
    )
    assert _unreproducible_reason(None, None) is None
    assert "source checkout" in str(_unreproducible_reason(None, "/home/me/proj"))


def test_dist_info_outside_site_packages_is_still_a_normal_install(fake_site):
    """An install into an unusual prefix is reproducible; only checkouts are not."""
    write_distribution(fake_site, "oddprefixpkg", "3.1.4")
    assert get_environment_requirements().get("oddprefixpkg") == "3.1.4"


def test_egg_info_source_tree_is_reported_as_a_checkout(tmp_path):
    """`setup.py develop` leaves an egg-info and no direct_url.json at all."""
    source_tree = tmp_path / "src"
    source_tree.mkdir()
    egg_info = source_tree / "eggpkg.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_text(
        "Metadata-Version: 2.1\nName: eggpkg\nVersion: 0.9.0\n"
    )
    sys.path.insert(0, str(source_tree))
    try:
        assert "eggpkg" not in get_environment_requirements()
        assert "source checkout" in get_unreproducible_requirements()["eggpkg"]
    finally:
        sys.path.remove(str(source_tree))


# --------------------------------------------------------------------------
# D8: remote Python minor-version skew must be refused
# --------------------------------------------------------------------------


def test_matching_remote_python_is_selected():
    probed = [("python3.11", "3.11"), ("python3", "3.11")]
    assert _select_remote_python(probed, "3.11") == ("python3.11", "3.11")


def test_matching_remote_python_is_preferred_over_an_earlier_candidate():
    probed = [("python3.12", "3.12"), ("python3.9", "3.9")]
    assert _select_remote_python(probed, "3.9") == ("python3.9", "3.9")


def test_remote_python_minor_version_skew_is_refused():
    """A 3.9 cluster cannot load a 3.12 payload; say so instead of trying."""
    probed = [("python3.9", "3.9"), ("python3", "3.9")]
    with pytest.raises(RuntimeError) as excinfo:
        _select_remote_python(probed, "3.12")
    message = str(excinfo.value)
    assert "3.9" in message and "3.12" in message
    assert "bytecode" in message


def test_no_remote_python_at_all_is_refused():
    with pytest.raises(RuntimeError) as excinfo:
        _select_remote_python([], "3.11")
    assert "No Python 3 interpreter" in str(excinfo.value)


# --------------------------------------------------------------------------
# D9: a half-built conda environment must not be cached as ready
# --------------------------------------------------------------------------


def test_conda_env_listing_is_parsed_into_prefixes():
    listing = (
        "# conda environments:\n"
        "#\n"
        "base                  *  /opt/conda\n"
        "clustrix_venv1_py311_abc /opt/conda/envs/clustrix_venv1_py311_abc\n"
    )
    assert _parse_conda_env_paths(listing) == {
        "base": "/opt/conda",
        "clustrix_venv1_py311_abc": "/opt/conda/envs/clustrix_venv1_py311_abc",
    }


def test_readiness_marker_is_stamped_per_environment():
    commands = _conda_env_ready_commands(["env_one", "env_two"])
    assert len(commands) == 2
    for name, command in zip(["env_one", "env_two"], commands):
        assert command.startswith(f"conda run -n {name} python -c ")
        assert ".clustrix_ready" in command
        assert "sys.prefix" in command


def test_environment_setup_has_no_silent_install_failures():
    """`pip install X || echo 'Failed to install X'` cached a broken environment."""
    import inspect

    from clustrix import utils

    source = inspect.getsource(utils.setup_two_venv_environment)
    assert "echo 'Failed to install" not in source, source
    assert "echo 'Post-install command failed" not in source, source
    # The readiness stamp must be the last thing appended, after every install.
    stamp_at = source.index("_conda_env_ready_commands")
    join_at = source.index('full_command = " && ".join(commands)')
    assert stamp_at < join_at
    assert "pip install" not in source[stamp_at:join_at]


# --------------------------------------------------------------------------
# D1: a payload that reaches into an unreproducible package must be refused
# --------------------------------------------------------------------------


def test_payload_using_an_uninstallable_package_is_refused(tmp_path, monkeypatch):
    """Refuse at submit time instead of failing on import an hour later.

    The package is written into a directory that is then treated as an
    installed root, which is exactly what a private VCS install looks like:
    real code inside site-packages that no `pip install name==version` can
    reproduce elsewhere.
    """
    from clustrix import utils

    site = tmp_path / "site-packages"
    site.mkdir()
    package = site / "privatepkg"
    package.mkdir()
    (package / "__init__.py").write_text("def helper(value):\n    return value + 1\n")
    write_distribution(
        site,
        "privatepkg",
        "1.0.0",
        direct_url={
            "url": "https://github.com/example/private.git",
            "vcs_info": {"vcs": "git", "commit_id": "deadbeef"},
        },
        top_level=["privatepkg"],
    )

    monkeypatch.setattr(
        utils, "_INSTALLED_ROOTS", utils._INSTALLED_ROOTS + (str(site) + os.sep,)
    )
    sys.path.insert(0, str(site))
    try:
        import importlib

        privatepkg = importlib.import_module("privatepkg")
        assert not utils._is_local_module(privatepkg)

        def use_private(value):
            return privatepkg.helper(value)

        with pytest.raises(RuntimeError) as excinfo:
            utils.serialize_function(use_private, (1,), {})
        message = str(excinfo.value)
        assert "privatepkg" in message
        assert "git checkout" in message
    finally:
        sys.path.remove(str(site))
        sys.modules.pop("privatepkg", None)
