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
from typing import Optional

import pytest

import clustrix
from clustrix.config import (
    CONFIG_SOURCE_EXPLICIT_FILE,
    CONFIG_SOURCE_UNRECORDED_PROVENANCE,
    CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
    CONFIG_SOURCE_RUNTIME,
    CONFIG_SOURCE_USER_CONFIG_DIR,
    CONFIG_SOURCE_WORKING_DIRECTORY,
    ClusterConfig,
    config_source_for_discovered_path,
    config_source_is_trusted,
    get_config_source,
    set_config_source,
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


def _run(
    script: str,
    home: Path,
    tree: str = _REPO_ROOT,
    extra_env: Optional[dict] = None,
) -> dict:
    """Run ``script`` in a *real* fresh interpreter rooted at ``home``.

    ``CLUSTRIX_CONFIG_DIR`` is removed rather than pointed somewhere, so the
    child's configuration directory is ``$HOME/.clustrix`` -- the trusted
    one, which is the whole point: the leak is a profile arriving there from
    somewhere else.

    ``PYTHONPATH`` is pinned to ``tree`` so the child cannot pick up an
    installed clustrix from another tree. ``tree`` defaults to this checkout;
    the upgrade tests point it at a *pre-fix* one instead.
    """
    env = dict(os.environ)
    env.pop("CLUSTRIX_CONFIG_DIR", None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = tree
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env or {})
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
    # Absence is not a downgrade *or* a promotion -- it is silence, and it
    # used to be resolved to the file's own trusted source. That was route
    # 9a: it is exactly the state every store written before this key
    # existed is in. The assertion this replaces asserted the defect.
    assert (
        _restored_profile_source(CONFIG_SOURCE_USER_CONFIG_DIR, None)
        == CONFIG_SOURCE_UNRECORDED_PROVENANCE
    )
    # Unless the caller named the file, which is the one act that answers
    # the question the store failed to.
    assert (
        _restored_profile_source(CONFIG_SOURCE_EXPLICIT_FILE, None)
        == CONFIG_SOURCE_EXPLICIT_FILE
    )
    # And where the file itself is untrusted there was never any silence to
    # resolve: that is knowledge about the file, and it is already the worse
    # answer.
    for discovered in (
        CONFIG_SOURCE_WORKING_DIRECTORY,
        CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
    ):
        assert _restored_profile_source(discovered, None) == discovered
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
    """A bundle the caller named still loads at that caller's source.

    Naming a path is the act ``load_from_file``'s default already treats as
    authorisation for a bundle carrying no provenance of its own, and it is
    the way back from ``unrecorded-provenance``. What changed with route 9a
    is the *discovered* case -- see
    ``test_a_store_that_records_nothing_is_not_an_answer``.
    """
    legacy = tmp_path / "legacy.yml"
    legacy.write_text(BUNDLE, encoding="utf-8")

    manager = ProfileManager(config_dir=str(tmp_path / "profiles"))
    manager.load_from_file(str(legacy), source=CONFIG_SOURCE_EXPLICIT_FILE)
    assert get_config_source(manager.profiles["shipped"]) == CONFIG_SOURCE_EXPLICIT_FILE


# --------------------------------------------------------------------------
# Route 9a. The upgrade path failed open, so the fix protected nobody who was
# already affected.
#
# ``_restored_profile_source`` resolved a *missing* record to the file's own
# source. Every store written before ``PROFILE_SOURCES_KEY`` existed is
# missing it, and every one of those stores is a store a pre-fix
# ``_persist()`` may already have laundered a profile into. So installing the
# fix changed nothing for them: ``user-config-dir``, trusted, credential
# released. It also put two opposite defaults in one subsystem --
# ``get_config_source`` reads a missing record as *untrusted*.
#
# Absence now fails closed. See ``_restored_profile_source`` for why a store
# version key would only restate what absence already says, and
# ``clustrix.config.CONFIG_SOURCE_UNRECORDED_PROVENANCE`` for why the answer
# is a source of its own rather than ``redirected-config-dir``.
# --------------------------------------------------------------------------

#: The last commit before provenance was persisted at all -- the release a
#: real upgrading user is coming *from*. Tests below check the fix against a
#: store this code really wrote, not against one shaped like it.
PRE_FIX_COMMIT = "7f82333"


@pytest.fixture(scope="module")
def pre_fix_tree(tmp_path_factory):
    """A working tree of the pre-fix release, from ``git archive``.

    Skipped rather than faked when the object is unreachable -- a shallow CI
    clone has no history. ``test_a_store_that_records_nothing_is_not_an_answer``
    is the hermetic guard that still runs there; this one is what establishes
    that the hermetic guard's idea of the pre-fix file format is real.
    """
    destination = tmp_path_factory.mktemp("pre-fix-release")
    archive = subprocess.run(
        ["git", "archive", PRE_FIX_COMMIT],
        cwd=_REPO_ROOT,
        capture_output=True,
        timeout=120,
    )
    if archive.returncode != 0:
        pytest.skip(
            f"commit {PRE_FIX_COMMIT} is not in this checkout: "
            f"{archive.stderr.decode(errors='replace').strip()}"
        )
    subprocess.run(
        ["tar", "-x", "-C", str(destination)],
        input=archive.stdout,
        check=True,
        timeout=120,
    )
    manager = destination / "clustrix" / "profile_manager.py"
    assert manager.exists(), "git archive produced no clustrix package"
    assert PROFILE_SOURCES_KEY not in manager.read_text(encoding="utf-8"), (
        f"{PRE_FIX_COMMIT} already records provenance, so it is not the "
        f"release the upgrade is from"
    )
    return str(destination)


#: Process 1, run by the *pre-fix* interpreter: discover the repository's
#: bundle -- refused there, correctly -- and select a profile, which is
#: enough to copy the whole bundle into ``~/.clustrix``.
_PRE_FIX_LAUNDER = """
    import json, os
    from clustrix.config import config_source_for_discovered_path, get_config_source
    from clustrix.profile_manager import ProfileManager
    bundle = os.environ["CLUSTRIX_TEST_BUNDLE"]
    pm = ProfileManager()
    pm.load_from_file(bundle, source=config_source_for_discovered_path(bundle))
    out = {"source": get_config_source(pm.profiles["shipped"])}
    pm.set_active_profile("shipped")
    out["store"] = str(pm.store_path)
    print(json.dumps(out))
"""

#: Process 2, run by *this* checkout: the upgraded user, doing the ordinary
#: thing -- opening the store and applying a profile. ``configure(**asdict)``
#: is what both widgets' Apply buttons do, and it is the step that made
#: marking only the object untrusted worth nothing: it builds a fresh object
#: whose own source is ``runtime``.
_AFTER_UPGRADE = """
    import json, os
    from dataclasses import asdict
    from clustrix.config import (
        configure, get_config, get_config_dir, get_config_source,
        config_source_is_trusted,
    )
    from clustrix.auth_methods import stored_credential_is_for_config
    from clustrix.executor_connections import ConnectionManager
    from clustrix.profile_manager import ProfileManager
    import clustrix.credential_manager as credential_manager

    # The documented setup: the credential file holds the secret and names
    # no host, the configuration holds the host.
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    env_file.write_text(
        "SSH_PASSWORD=" + os.environ["CLUSTRIX_TEST_SENTINEL"] + "\\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    credential_manager._credential_manager = None

    pm = ProfileManager()
    profile = pm.profiles["shipped"]
    out = {
        "source": get_config_source(profile),
        "trusted": config_source_is_trusted(profile),
        "released": stored_credential_is_for_config(profile, {}) is None,
    }
    configure(**asdict(profile))
    configure(ssh_host_key_policy="auto_add")
    out["source_after_apply"] = get_config_source(get_config())
    out["released_after_apply"] = (
        stored_credential_is_for_config(get_config(), {}) is None
    )
    manager = ConnectionManager(get_config())
    try:
        manager.setup_ssh_connection()
        out["authenticated"] = manager.ssh_client.get_transport().is_authenticated()
    except Exception:
        out["authenticated"] = False
    finally:
        manager.disconnect()
    print(json.dumps(out))
"""


def test_a_store_written_by_the_pre_fix_release_is_not_trusted_on_upgrade(
    pre_fix_tree, tmp_path
):
    """Route 9a, written by real pre-fix code and read by this one.

    RED before the fix, measured: process 2 reported ``user-config-dir``,
    ``trusted=True``, the credential released, and the sentinel
    **authenticated** to the loopback server the repository's bundle named.

    Nothing here is shaped like the old release; it *is* the old release,
    extracted with ``git archive`` and run by its own interpreter. The two
    processes share nothing but the file ``_persist()`` wrote.

    The server accepts the sentinel and nothing else, so an entry in
    ``authentications`` is a measurement that the secret left the machine
    rather than an inference from a source string.
    """
    from tests.ssh_server import LocalSSHServer

    sentinel_password = "-".join(["clustrix", "sentinel", "sshpassword", "value"])
    home = tmp_path / "home"
    repo = tmp_path / "cloned-repo"
    server_root = tmp_path / "server-root"
    for directory in (home, repo, server_root):
        directory.mkdir(parents=True)

    with LocalSSHServer(root=str(server_root), password=sentinel_password) as server:
        (repo / "profiles.yml").write_text(
            "active_profile: shipped\n"
            "profiles:\n"
            "  shipped:\n"
            "    cluster_type: ssh\n"
            f"    cluster_host: {server.host}\n"
            f"    cluster_port: {server.port}\n"
            "    username: victim\n"
            "  filler:\n"
            "    cluster_type: local\n",
            encoding="utf-8",
        )
        environment = {
            "CLUSTRIX_TEST_BUNDLE": str(repo / "profiles.yml"),
            "CLUSTRIX_TEST_SENTINEL": sentinel_password,
        }

        first = _run(_PRE_FIX_LAUNDER, home, tree=pre_fix_tree, extra_env=environment)
        assert first["source"] == CONFIG_SOURCE_REDIRECTED_CONFIG_DIR

        store = Path(first["store"])
        assert str(home / ".clustrix" / "profiles") in str(store)
        written = store.read_text(encoding="utf-8")
        assert PROFILE_SOURCES_KEY not in written, (
            "the pre-fix release recorded provenance after all, so this test "
            "is not measuring the upgrade"
        )

        second = _run(_AFTER_UPGRADE, home, extra_env=environment)

    assert second["source"] == CONFIG_SOURCE_UNRECORDED_PROVENANCE
    assert second["trusted"] is False
    assert second["released"] is False
    # The step that matters: applying the profile is the ordinary way to use
    # one, and it rebuilds the config from scratch. Marking only the object
    # untrusted left this reading ``runtime`` and the sentinel on the wire.
    assert second["source_after_apply"] == CONFIG_SOURCE_UNRECORDED_PROVENANCE
    assert second["released_after_apply"] is False
    assert second["authenticated"] is False
    assert server.authentications == [], (
        "a store the pre-fix release wrote released the credential to the "
        "host the repository's bundle named: " + repr(server.authentications)
    )


def test_a_store_that_records_nothing_is_not_an_answer(tmp_path):
    """The same rule, hermetically, so it still guards without git history.

    The file format this writes is the one
    ``test_a_store_written_by_the_pre_fix_release_is_not_trusted_on_upgrade``
    proves the pre-fix release really produced: a bundle with ``profiles``
    and no ``profile_sources``. Here it is discovered in the user's *own*
    configuration directory, which is the case that used to come back
    trusted.
    """
    from clustrix.config import _HOSTS_NAMED_BY_UNTRUSTED_SOURCES, get_config_dir
    from clustrix.auth_methods import stored_credential_is_for_config

    store = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(BUNDLE, encoding="utf-8")
    assert PROFILE_SOURCES_KEY not in BUNDLE

    with pytest.warns(UserWarning, match="predate clustrix recording"):
        manager = ProfileManager()
    profile = manager.profiles["shipped"]

    assert config_source_for_discovered_path(store) == CONFIG_SOURCE_USER_CONFIG_DIR
    assert get_config_source(profile) == CONFIG_SOURCE_UNRECORDED_PROVENANCE
    assert config_source_is_trusted(profile) is False
    assert stored_credential_is_for_config(profile, {}) is not None
    # And the *hostname* carries it, not just this object, or the next
    # ``configure(**asdict(profile))`` would undo the whole thing.
    assert _HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        SENTINEL_HOST: CONFIG_SOURCE_UNRECORDED_PROVENANCE
    }


def test_the_refusal_for_an_unrecorded_store_says_what_it_is(tmp_path):
    """It is a gap in an old file, not a verdict, and the message must say so.

    The generic refusal blames "a file chosen by where the process runs or by
    an inherited environment variable", which is false here and sends the
    user looking for a file that does not exist. It also offers only remedies
    for a *permanent* record; this one is undoable.
    """
    from clustrix.config import get_config_dir
    from clustrix.auth_methods import stored_credential_is_for_config

    store = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(BUNDLE, encoding="utf-8")
    with pytest.warns(UserWarning, match="predate clustrix recording"):
        manager = ProfileManager()

    reason = stored_credential_is_for_config(manager.profiles["shipped"], {})
    assert reason is not None
    assert "adopt_profile_store" in reason
    assert "before clustrix recorded where each profile came from" in reason
    assert "inherited environment variable" not in reason


def test_the_warning_names_only_the_profiles_that_name_a_host(tmp_path):
    """It lists ``shipped`` and not ``filler``. Both are unrecorded.

    Provenance decides who may receive a credential, so a profile naming
    nobody has nothing at stake. The filter matters because a pre-fix
    ``_persist`` copied all six built-in templates into the store, and every
    one of them is hostless: without it the warning names seven profiles and
    buries the single entry the user actually has to look at, which is the
    same as not warning.

    Guards ``profile_manager``'s ``and config.cluster_host``. Removing that
    clause left the whole suite green -- the message was asserted only by its
    ``predate clustrix recording`` prefix, never by whom it named.
    """
    from clustrix.config import get_config_dir

    store = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(BUNDLE, encoding="utf-8")

    with pytest.warns(UserWarning, match="predate clustrix recording") as caught:
        manager = ProfileManager()

    # Both profiles really are unrecorded -- the filter is about what the
    # message says, not about which profiles carry the doubt.
    assert get_config_source(manager.profiles["shipped"]) == (
        CONFIG_SOURCE_UNRECORDED_PROVENANCE
    )
    assert get_config_source(manager.profiles["filler"]) == (
        CONFIG_SOURCE_UNRECORDED_PROVENANCE
    )
    assert manager.profiles["filler"].cluster_host is None

    message = str(
        [w for w in caught if "predate clustrix recording" in str(w.message)][0].message
    )
    assert message.startswith("1 profile(s)"), message
    assert "shipped" in message
    assert "filler" not in message


def test_naming_the_store_is_the_way_back(tmp_path):
    """``adopt_profile_store`` is the remedy the refusal names, and it works.

    Without a way back the fix would be a lock-out: restoring a legacy store
    condemns its hostnames for the life of the process, and that record is
    deliberately proof against ``configure()`` and ``load_config()``. So the
    remedy works on the *file*, before anything reads it.
    """
    from clustrix.config import get_config_dir
    from clustrix.profile_manager import adopt_profile_store

    store = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(BUNDLE, encoding="utf-8")

    assert sorted(adopt_profile_store()) == ["filler", "shipped"]

    import yaml

    recorded = yaml.safe_load(store.read_text(encoding="utf-8"))[PROFILE_SOURCES_KEY]
    assert recorded == {
        "shipped": CONFIG_SOURCE_EXPLICIT_FILE,
        "filler": CONFIG_SOURCE_EXPLICIT_FILE,
    }
    # And a store that already has an answer for everything is left alone.
    assert adopt_profile_store() == []

    manager = ProfileManager()
    assert get_config_source(manager.profiles["shipped"]) == (
        CONFIG_SOURCE_USER_CONFIG_DIR
    )
    assert config_source_is_trusted(manager.profiles["shipped"]) is True


def test_adopting_a_store_cannot_promote_a_profile_it_knows_is_untrusted(tmp_path):
    """It stops withholding trust; it does not grant it.

    Otherwise the remedy would be the laundering route: run it on a store a
    repository's bundle had been copied into and the refusal disappears. An
    entry that already records where it came from is not silence, so it is
    not this function's to answer.
    """
    from clustrix.config import get_config_dir
    from clustrix.profile_manager import adopt_profile_store

    store = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(
        BUNDLE + f"{PROFILE_SOURCES_KEY}:\n"
        f"  shipped: {CONFIG_SOURCE_WORKING_DIRECTORY}\n"
        f"  filler: {CONFIG_SOURCE_UNRECORDED_PROVENANCE}\n",
        encoding="utf-8",
    )

    assert adopt_profile_store() == ["filler"]

    manager = ProfileManager()
    assert get_config_source(manager.profiles["shipped"]) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )
    assert config_source_is_trusted(manager.profiles["shipped"]) is False


def test_a_recorded_unrecorded_marker_is_read_as_silence_again(tmp_path):
    """Re-persisting a legacy store must not freeze the doubt into a verdict.

    Every mutator persists, so a user who merely selects a profile writes the
    restored source back. If ``unrecorded-provenance`` were then read as an
    ordinary untrusted source it could never be upgraded -- a recorded
    untrusted source may only downgrade -- and ``adopt_profile_store`` would
    have nothing to work on. It is an admission of ignorance, and ignorance
    does not become knowledge by being written down.
    """
    for file_source in (
        CONFIG_SOURCE_USER_CONFIG_DIR,
        CONFIG_SOURCE_EXPLICIT_FILE,
        CONFIG_SOURCE_WORKING_DIRECTORY,
        CONFIG_SOURCE_REDIRECTED_CONFIG_DIR,
    ):
        assert _restored_profile_source(
            file_source, CONFIG_SOURCE_UNRECORDED_PROVENANCE
        ) == _restored_profile_source(file_source, None), file_source


def test_each_profile_keeps_its_own_source_and_not_its_neighbour_s(tmp_path):
    """Kills: writing one profile's source for every profile in the store.

    The store is a bulk file and the mapping is per name, so a mutant that
    borrows the first entry's answer for all of them left every earlier test
    green: they all have one interesting profile. Two profiles with genuinely
    different provenance is what makes the per-name keying load-bearing.
    """
    from clustrix.config import config_built_from_file, get_config_dir

    mine = ClusterConfig(cluster_type="ssh", cluster_host="mine.example")
    with config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
        theirs = ClusterConfig(cluster_type="ssh", cluster_host="theirs.example")
    set_config_source(theirs, CONFIG_SOURCE_WORKING_DIRECTORY)

    manager = ProfileManager()
    manager.profiles = {"mine": mine, "theirs": theirs}
    store = get_config_dir() / "profiles" / ProfileManager.STORE_FILENAME
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    manager.save_to_file(str(store))

    import yaml

    recorded = yaml.safe_load(store.read_text(encoding="utf-8"))[PROFILE_SOURCES_KEY]
    assert recorded == {
        "mine": CONFIG_SOURCE_RUNTIME,
        "theirs": CONFIG_SOURCE_WORKING_DIRECTORY,
    }

    # And through the loader: ``mine`` must survive its neighbour.
    restored = ProfileManager()
    assert config_source_is_trusted(restored.profiles["mine"]) is True
    assert config_source_is_trusted(restored.profiles["theirs"]) is False


#: Process 1: taint a hostname with a real untrusted read, then build a
#: *fresh* config naming the same host in Python and save it as a profile.
#: Its own attribute says ``runtime``; only the taint map knows better.
_PERSIST_A_REBUILT_PROFILE = """
    import json, os
    from clustrix.config import (
        ClusterConfig, CONFIG_SOURCE_WORKING_DIRECTORY, get_config_source,
    )
    from clustrix.profile_manager import ProfileManager
    import clustrix.config as config_module
    import warnings
    host = os.environ["CLUSTRIX_TEST_SENTINEL"]
    # A real loader reading a real ``./clustrix.yml``: that is what condemns
    # the hostname for the process. Deliberately *not* a profile bundle --
    # a profile naming the same host would be restored alongside the one
    # under test in process 2 and would re-condemn the hostname there, which
    # would make this pass whatever the store recorded. For the same reason
    # the file is in a directory of its own rather than in the child's cwd:
    # importing clustrix runs the search, so a ``clustrix.yml`` next to the
    # reader would condemn the host there too.
    os.chdir(os.environ["CLUSTRIX_TEST_REPO"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config_module._load_default_config()
    assert config_module.get_config_source(config_module.get_config()) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )
    # Now a *fresh* config naming the same host, built in Python. Its own
    # attribute says runtime; only the taint map knows better.
    rebuilt = ClusterConfig(cluster_type="ssh", cluster_host=host, username="victim")
    pm = ProfileManager()
    pm.create_profile("rebuilt", rebuilt)
    print(json.dumps({
        "raw_attribute": getattr(rebuilt, "_clustrix_config_source"),
        "effective": get_config_source(rebuilt),
    }))
"""

_READ_THE_REBUILT_PROFILE = """
    import json
    from clustrix.config import (
        config_source_is_trusted, get_config_source,
        _HOSTS_NAMED_BY_UNTRUSTED_SOURCES,
    )
    from clustrix.profile_manager import ProfileManager
    import os
    pm = ProfileManager()
    cfg = pm.profiles["rebuilt"]
    print(json.dumps({
        "source": get_config_source(cfg),
        "trusted": config_source_is_trusted(cfg),
        "tainted": dict(_HOSTS_NAMED_BY_UNTRUSTED_SOURCES),
        "others_naming_the_host": sorted(
            name for name, c in pm.profiles.items()
            if name != "rebuilt"
            and c.cluster_host == os.environ["CLUSTRIX_TEST_SENTINEL"]
        ),
    }))
"""


def test_the_store_records_where_the_hostname_came_from_not_where_the_object_did(
    tmp_path,
):
    """Kills: persisting ``_clustrix_config_source`` instead of asking.

    ``get_config_source`` answers about the *hostname*: a config rebuilt from
    an untrusted one -- ``dataclasses.replace``, ``configure(**asdict(cfg))``,
    the widget's Apply -- carries ``runtime`` on itself while the name it
    holds is still condemned. Persisting the raw attribute writes ``runtime``
    into the store, and the next process, whose taint map is empty, has no
    way to know better. Every other test here saves a config whose attribute
    and whose hostname agree, so the mutant survived all of them.
    """
    home = tmp_path / "home"
    repo = tmp_path / "cloned-repo"
    home.mkdir(parents=True)
    repo.mkdir(parents=True)
    (repo / "clustrix.yml").write_text(
        f"cluster_type: ssh\ncluster_host: {SENTINEL_HOST}\nusername: victim\n",
        encoding="utf-8",
    )
    environment = {
        "CLUSTRIX_TEST_SENTINEL": SENTINEL_HOST,
        "CLUSTRIX_TEST_REPO": str(repo),
    }

    first = _run(_PERSIST_A_REBUILT_PROFILE, home, extra_env=environment)
    # The two really do disagree, or the mutant would be untestable here.
    assert first["raw_attribute"] == CONFIG_SOURCE_RUNTIME
    assert first["effective"] == CONFIG_SOURCE_WORKING_DIRECTORY

    second = _run(_READ_THE_REBUILT_PROFILE, home, extra_env=environment)
    assert second["source"] == CONFIG_SOURCE_WORKING_DIRECTORY
    assert second["trusted"] is False
    # Nothing else in the store names this host, so the answer can only have
    # come out of the file rather than from a sibling profile re-condemning
    # it in the reading process.
    assert second["others_naming_the_host"] == []
    assert second["tainted"] == {SENTINEL_HOST: CONFIG_SOURCE_WORKING_DIRECTORY}, (
        "the reading process condemned the hostname by itself, so this says "
        "nothing about what the store recorded"
    )
