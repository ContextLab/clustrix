#!/usr/bin/env python3
"""Provenance has to survive the process boundary, or the gate is asked a lie.

Route 8, measured end to end and reproduced here before it was fixed:

1. **Process 1.** A profile bundle a repository ships is loaded from outside
   the configuration directory, so it is ``redirected-config-dir``. The
   credential layer refuses it and the hostname is tainted for the life of
   that process. Everything correct so far.
2. ``ProfileManager._persist()`` fires -- from any of *seven* mutators, one
   of which is merely selecting a profile -- and copies the bundle into
   ``<config dir>/profiles/profiles.yml``, i.e. into ``~/.clustrix``, which
   is the user's own directory and therefore trusted.
3. ``save_to_file`` writes ``strip_secret_fields(asdict(config))``: the
   declared fields, and provenance deliberately is not one of them.
4. **Process 2.** ``_restore()`` re-derives the source from where the file
   now *is* -- ``user-config-dir`` -- and gets a legitimately computed
   *trusted* answer. The taint map is empty, because it is per-process. The
   stored credential is released to the repository's host.

The important part is that **no choke point can subsume this**. In process 2
the gate asks ``config_source_is_trusted`` and is answered correctly; the
input to the question had already been destroyed at the process boundary. A
choke point is only as good as what it is given.

Nothing here is mocked and nothing simulates a process. Every step runs in a
real interpreter started by ``subprocess``, with its own ``$HOME``, and the
only channel between them is the file ``_persist()`` wrote.

The fix persists the source alongside the profile rather than refusing to
persist an untrusted one, so a user who deliberately keeps a project-local
profile keeps it -- and keeps the refusal that goes with it. A persisted
source may only ever *downgrade* trust, which is what stops the key being a
laundering route in its own right; see
``clustrix.profile_manager._restored_profile_source``.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import clustrix
from clustrix.config import (
    CONFIG_SOURCE_EXPLICIT_FILE,
    CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
    CONFIG_SOURCE_RUNTIME,
    CONFIG_SOURCE_USER_CONFIG_DIR,
    CONFIG_SOURCE_WORKING_DIRECTORY,
    config_source_for_discovered_path,
    config_source_is_trusted,
    get_config_source,
)
from clustrix.profile_manager import (
    PROFILE_SOURCES_KEY,
    ProfileManager,
    _restored_profile_source,
)

#: Not a credential and not a real host. Every assertion below is about where
#: this name came from, never about a secret.
SENTINEL_HOST = "sentinel-attacker.invalid"

#: The bundle a cloned repository ships. Two profiles, because
#: ``remove_profile`` refuses to remove the last one.
BUNDLE = """\
active_profile: shipped
profiles:
  shipped:
    cluster_type: ssh
    cluster_host: %s
    username: victim
  filler:
    cluster_type: local
""" % (SENTINEL_HOST,)

#: Every mutator that calls ``_persist()``. The fix has to hold for all of
#: them, not just the one route 8 was measured through.
AUTO_FIRING_MUTATORS = {
    "create_profile": "pm.create_profile('extra', ClusterConfig(cluster_type='local'))",
    "clone_profile": "pm.clone_profile('shipped')",
    "remove_profile": "pm.remove_profile('filler')",
    "save_profile": "pm.save_profile('extra', ClusterConfig(cluster_type='local'))",
    "rename_profile": "pm.rename_profile('shipped', 'renamed')",
    "set_active_profile": "pm.set_active_profile('shipped')",
    "import_profile": (
        "pm.export_profile('filler', str(Path(os.environ['HOME']) / 'exported.yml')); "
        "pm.import_profile(str(Path(os.environ['HOME']) / 'exported.yml'))"
    ),
}

_REPO_ROOT = str(Path(clustrix.__file__).resolve().parent.parent)


def _run(script: str, home: Path) -> dict:
    """Run ``script`` in a *real* fresh interpreter rooted at ``home``.

    ``CLUSTRIX_CONFIG_DIR`` is removed rather than pointed somewhere, so the
    child's configuration directory is ``$HOME/.clustrix`` -- the trusted
    one, which is the whole point: the leak is a profile arriving there from
    somewhere else.

    ``PYTHONPATH`` is pinned to this checkout so the child cannot pick up an
    installed clustrix from another tree.
    """
    env = dict(os.environ)
    env.pop("CLUSTRIX_CONFIG_DIR", None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = _REPO_ROOT
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env=env,
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"child process failed ({completed.returncode}):\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


#: Process 1: discover the repository's bundle, confirm it is refused here,
#: then fire one of the seven mutators.
_PROCESS_ONE = """
    import json, os
    from pathlib import Path
    from clustrix.config import (
        ClusterConfig,
        config_source_for_discovered_path,
        config_source_is_trusted,
        get_config_source,
        _HOSTS_NAMED_BY_UNTRUSTED_SOURCES,
    )
    from clustrix.auth_methods import stored_credential_is_for_config
    from clustrix.profile_manager import ProfileManager

    bundle = os.environ["CLUSTRIX_TEST_BUNDLE"]
    pm = ProfileManager()
    store = pm.store_path
    pm.load_from_file(bundle, source=config_source_for_discovered_path(bundle))
    cfg = pm.profiles["shipped"]
    result = {
        "store_path": str(store),
        "source": get_config_source(cfg),
        "trusted": config_source_is_trusted(cfg),
        "tainted": dict(_HOSTS_NAMED_BY_UNTRUSTED_SOURCES),
        "released": stored_credential_is_for_config(cfg, {}) is None,
    }
    MUTATOR
    result["store_exists"] = store.exists()
    print(json.dumps(result))
"""

#: Process 2: a fresh interpreter that has never seen the repository. It does
#: nothing but construct a ProfileManager, which restores the store by itself.
_PROCESS_TWO = """
    import json, os
    from clustrix.config import (
        config_source_is_trusted,
        get_config_source,
        _HOSTS_NAMED_BY_UNTRUSTED_SOURCES,
    )
    from clustrix.auth_methods import stored_credential_is_for_config
    from clustrix.profile_manager import ProfileManager

    sentinel = os.environ["CLUSTRIX_TEST_SENTINEL"]
    # Nothing has crossed the boundary yet: this record is per-process and
    # cannot be otherwise, which is exactly why the file has to carry it.
    tainted_before = dict(_HOSTS_NAMED_BY_UNTRUSTED_SOURCES)
    pm = ProfileManager()
    matches = {
        name: cfg
        for name, cfg in pm.profiles.items()
        if cfg.cluster_host == sentinel
    }
    # ``clone_profile`` leaves two profiles naming the sentinel; every one of
    # them has to be refused, so the report is over all of them.
    assert matches, "the shipped profile did not survive the mutator"
    print(json.dumps({
        "names": sorted(matches),
        "sources": sorted({get_config_source(c) for c in matches.values()}),
        "trusted_any": any(config_source_is_trusted(c) for c in matches.values()),
        "tainted_before": tainted_before,
        "tainted_after": dict(_HOSTS_NAMED_BY_UNTRUSTED_SOURCES),
        "released_any": any(
            stored_credential_is_for_config(c, {}) is None for c in matches.values()
        ),
    }))
"""


@pytest.mark.parametrize("mutator", sorted(AUTO_FIRING_MUTATORS))
def test_persisted_profile_keeps_the_source_it_was_read_with(tmp_path, mutator):
    """Route 8, across two real processes, for each auto-firing mutator."""
    home = tmp_path / mutator / "home"
    repo = tmp_path / mutator / "cloned-repo"
    home.mkdir(parents=True)
    repo.mkdir(parents=True)
    bundle = repo / "profiles.yml"
    bundle.write_text(BUNDLE, encoding="utf-8")

    os.environ["CLUSTRIX_TEST_BUNDLE"] = str(bundle)
    os.environ["CLUSTRIX_TEST_SENTINEL"] = SENTINEL_HOST
    try:
        first = _run(
            _PROCESS_ONE.replace("MUTATOR", AUTO_FIRING_MUTATORS[mutator]), home
        )
        # Process 1 gets it right, and always did.
        assert first["source"] == CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
        assert first["trusted"] is False
        assert first["tainted"] == {SENTINEL_HOST: CONFIG_SOURCE_REDIRECTED_CONFIG_DIR}
        assert first["released"] is False
        # The mutator really did copy it into the trusted directory; without
        # this the test could pass by the bundle never being persisted.
        assert first["store_exists"] is True
        assert str(home / ".clustrix" / "profiles") in first["store_path"]

        second = _run(_PROCESS_TWO, home)
    finally:
        os.environ.pop("CLUSTRIX_TEST_BUNDLE", None)
        os.environ.pop("CLUSTRIX_TEST_SENTINEL", None)

    # The second process starts from an empty taint map -- that record is
    # per-process and cannot be otherwise -- so the file is the only thing
    # that can carry the answer across, and it has to.
    assert second["tainted_before"] == {}, "no in-memory state crosses processes"
    assert second["sources"] == [CONFIG_SOURCE_REDIRECTED_CONFIG_DIR], (
        f"{mutator} laundered the profile into "
        f"{second['sources']!r} by persisting it"
    )
    assert second["trusted_any"] is False
    assert second["released_any"] is False
    # Restoring the store re-taints the hostname, so a config later rebuilt
    # from it -- ``configure(**asdict(cfg))``, ``dataclasses.replace`` --
    # cannot launder it back to ``runtime`` in this process either.
    assert second["tainted_after"] == {
        SENTINEL_HOST: CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    }


def test_the_store_records_a_source_for_every_profile(tmp_path):
    """Whatever is written, every profile in it is accounted for."""
    manager = ProfileManager(config_dir=str(tmp_path / "profiles"))
    manager.save_to_file(str(tmp_path / "out.yml"))

    import yaml

    data = yaml.safe_load((tmp_path / "out.yml").read_text(encoding="utf-8"))
    assert set(data[PROFILE_SOURCES_KEY]) == set(data["profiles"])
    for source in data[PROFILE_SOURCES_KEY].values():
        assert source in (
            CONFIG_SOURCE_RUNTIME,
            CONFIG_SOURCE_EXPLICIT_FILE,
            CONFIG_SOURCE_USER_CONFIG_DIR,
            CONFIG_SOURCE_WORKING_DIRECTORY,
            CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
        )


def test_a_recorded_source_may_downgrade_but_never_promote(tmp_path):
    """The key writing the answer down must not become the laundering route.

    This is the reason ``_clustrix_config_source`` is not a dataclass field:
    anything a file can set, a hostile file can set. Persisting the source is
    only safe because it is read as a *ceiling*, never as a licence.
    """
    for claimed in (
        CONFIG_SOURCE_RUNTIME,
        CONFIG_SOURCE_EXPLICIT_FILE,
        CONFIG_SOURCE_USER_CONFIG_DIR,
    ):
        assert (
            _restored_profile_source(CONFIG_SOURCE_WORKING_DIRECTORY, claimed)
            == CONFIG_SOURCE_WORKING_DIRECTORY
        )
    assert (
        _restored_profile_source(
            CONFIG_SOURCE_USER_CONFIG_DIR, CONFIG_SOURCE_WORKING_DIRECTORY
        )
        == CONFIG_SOURCE_WORKING_DIRECTORY
    )
    assert (
        _restored_profile_source(CONFIG_SOURCE_USER_CONFIG_DIR, None)
        == CONFIG_SOURCE_USER_CONFIG_DIR
    )
    # Anything unrecognisable is a redirect, not an error and not a licence.
    for nonsense in ("trusted", "", 17, {"source": "runtime"}, True):
        assert (
            _restored_profile_source(CONFIG_SOURCE_USER_CONFIG_DIR, nonsense)
            == CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
        )


def test_a_hostile_bundle_cannot_promote_itself_through_the_store(tmp_path):
    """End to end for the same rule, through the real loader."""
    hostile = tmp_path / "cloned-repo" / "profiles.yml"
    hostile.parent.mkdir(parents=True)
    hostile.write_text(
        BUNDLE + f"{PROFILE_SOURCES_KEY}:\n  shipped: runtime\n  filler: runtime\n",
        encoding="utf-8",
    )

    manager = ProfileManager(config_dir=str(tmp_path / "profiles"))
    manager.load_from_file(
        str(hostile), source=config_source_for_discovered_path(hostile)
    )
    config = manager.profiles["shipped"]
    assert get_config_source(config) == CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    assert config_source_is_trusted(config) is False


def test_a_store_without_the_key_still_loads(tmp_path):
    """Stores written before this existed keep working, at the file's source."""
    legacy = tmp_path / "legacy.yml"
    legacy.write_text(BUNDLE, encoding="utf-8")

    manager = ProfileManager(config_dir=str(tmp_path / "profiles"))
    manager.load_from_file(str(legacy), source=CONFIG_SOURCE_EXPLICIT_FILE)
    assert get_config_source(manager.profiles["shipped"]) == CONFIG_SOURCE_EXPLICIT_FILE
