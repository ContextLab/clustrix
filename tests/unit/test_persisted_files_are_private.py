#!/usr/bin/env python3
"""Nothing clustrix writes may be readable by another local user.

**This is the guarantee for issue #111.** The static scan in
``tests/unit/test_credential_file_permissions.py`` is a fast lint that runs
first and catches the common shape early; it is not the guarantee, because
it cannot be. The set of ways to create a file in Python is unbounded --
``logging.FileHandler``, ``sqlite3.connect``, ``shutil.copyfile``,
``tempfile.mkstemp``, ``zipfile.ZipFile``, ``os.popen(..., "w")``, a bound
``emit = path.write_text``, ``getattr(os, "op" + "en")``, a dict of
dispatch functions, a subprocess -- and a guard that enumerates spellings
was defeated seven ways, then fourteen. Four of those fourteen really did
leave mode 0644 under umask 022, which is exactly the bug the guard
existed to prevent.

So this test does not read source at all. It observes the property:

    Point ``$HOME`` and the clustrix configuration directory at a temporary
    tree, run every public API that persists anything, then walk the whole
    tree. Every file must be no wider than 0600 and every directory no
    wider than 0700.

A bypass cannot pass this by being spelled differently, because the
spelling is never examined -- only the mode bits of whatever ended up on
disk. ``logging.FileHandler`` and ``sqlite3.connect`` are caught
identically to ``open(path, "w")``, and so is a file created by a
subprocess, which no AST guard can see at all.

The umask is deliberately 0o000 for the whole run: under a developer's
0o077 even a completely broken writer produces 0600, and the test would
pass against code it is supposed to reject.

The content half of the guard works the same way, and did not used to.
It checked each line's key against ``{password, api_key, hf_token,
AWS_SECRET_ACCESS_KEY}`` -- a name list, i.e. exactly the shape that
failed above -- and so reported nothing while ``aws_secret_access_key``,
``client_secret`` and ``token`` sat in the file the widget had just
written. It now plants a distinct **sentinel value** in every credential
slot the exercises touch and then looks for those *values* in the bytes on
disk. A leak is caught whatever its key is called, including keys nobody
has thought of yet, because no key is ever examined.

No secret-shaped literals appear below: the sentinels are assembled from
parts at import time, and the ``<redacted>`` spelling is the one
``tests/unit/test_check_for_secrets.py`` already treats as a placeholder.
"""

import json
import os
import shutil
import stat
import warnings

import pytest

import clustrix.config as config_module
from clustrix.cli_credentials import _write_credentials_to_env_file
from clustrix.config import ClusterConfig, get_config_dir, save_config
from clustrix.credential_manager import FlexibleCredentialManager
from clustrix.profile_manager import ProfileManager, _mkdir_private
from clustrix.ssh_utils import generate_ssh_key, update_ssh_config

#: The widest a file clustrix creates may be: owner read/write, nothing else.
MAX_FILE_MODE = 0o600

#: The widest a directory clustrix creates may be. Traversal by another user
#: is enough to reach a file inside by name even when the directory cannot
#: be listed, so this is not decoration.
MAX_DIR_MODE = 0o700

#: The single documented exception, and the reason it is one: an SSH public
#: key is meant to be handed out. ``ssh-keygen`` creates it 0644 itself.
#: Matched on the exact suffix so that nothing else can drift in under it.
PUBLIC_BY_DESIGN = (".pub",)

#: A umask that hides nothing, so a mode of 0600 can only have come from an
#: explicit ``os.open`` mode or ``fchmod`` and never from the environment.
WIDE_OPEN_UMASK = 0o000


def _sentinel(slot):
    """A value that exists nowhere else, so finding it means it was written."""
    return "-".join(["clustrix", "sentinel", slot, "value"])


#: One sentinel per place a credential can hide, planted by the exercises
#: below and hunted for by ``test_no_persisted_file_contains_a_credential``.
#:
#: The point of the list is that the *code under test* is never told any of
#: these names. Half of them are not ``ClusterConfig`` fields at all, three
#: of the environment-variable names match no pattern clustrix has ever
#: had, and ``USE_PASSWORD`` was actively exempted by a rule about the
#: boolean field ``use_env_password``. If the guard is ever narrowed back to
#: recognising keys by name, every one of these goes undetected again.
SENTINELS = {
    # Declared credential fields.
    "password": _sentinel("password"),
    "api_key": _sentinel("apikey"),
    "hf_token": _sentinel("hftoken"),
    # Keys that are not ClusterConfig fields, which is how they reached
    # disk verbatim: the widget hands strip_secret_fields whatever a
    # previously saved file contained.
    "aws_secret_access_key": _sentinel("aws"),
    "client_secret": _sentinel("clientsecret"),
    "private_key": _sentinel("privatekey"),
    "token": _sentinel("token"),
    "secret_key": _sentinel("secretkey"),
    "PASSWORD": _sentinel("shoutypassword"),
    "legacy_auth_blob": _sentinel("blob"),
    # Environment variable names chosen by the user.
    "AWS_SECRET_ACCESS_KEY": _sentinel("envaws"),
    "SSH_PASSPHRASE": _sentinel("passphrase"),
    "GITHUB_PAT": _sentinel("pat"),
    "USE_PASSWORD": _sentinel("usepassword"),
    "DATABASE_URL": _sentinel("dburl"),
}

#: The environment-variable half, ready to drop into a config. The database
#: URL hides its sentinel inside a value whose *key* says nothing at all.
SENTINEL_ENVIRONMENT = {
    "OMP_NUM_THREADS": "4",
    "AWS_SECRET_ACCESS_KEY": SENTINELS["AWS_SECRET_ACCESS_KEY"],
    "SSH_PASSPHRASE": SENTINELS["SSH_PASSPHRASE"],
    "GITHUB_PAT": SENTINELS["GITHUB_PAT"],
    "USE_PASSWORD": SENTINELS["USE_PASSWORD"],
    "DATABASE_URL": f"postgres://u:{SENTINELS['DATABASE_URL']}@db.example.edu/app",
}

#: The top-level half: keys the configuration file format does not define,
#: which is what a config file written by an older clustrix can contain and
#: what the widget therefore carries around in ``self.configs``.
SENTINEL_UNKNOWN_KEYS = {
    key: SENTINELS[key]
    for key in (
        "aws_secret_access_key",
        "client_secret",
        "private_key",
        "token",
        "secret_key",
        "PASSWORD",
        "legacy_auth_blob",
    )
}


def _sentinels_in(text):
    """Which planted values appear in ``text``, by the slot they were put in."""
    return sorted(slot for slot, value in SENTINELS.items() if value in text)


def _too_wide(root):
    """Every path under ``root`` whose mode lets another local user in.

    Returns ``[(relative path, mode)]``. Symlinks are skipped: their own
    mode is 0777 on every POSIX system and means nothing, and whatever they
    point at inside the tree is walked in its own right.
    """
    findings = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            continue
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            if mode & ~MAX_DIR_MODE:
                findings.append((str(path.relative_to(root)), oct(mode)))
        elif path.suffix in PUBLIC_BY_DESIGN:
            continue
        elif mode & ~MAX_FILE_MODE:
            findings.append((str(path.relative_to(root)), oct(mode)))
    return findings


def _files_under(root):
    return [p for p in root.rglob("*") if p.is_file()]


@pytest.fixture
def private_tree(tmp_path, monkeypatch):
    """A throwaway ``$HOME`` with the clustrix config dir inside it.

    ``Path.home()`` reads ``$HOME`` on POSIX and ``get_config_dir()`` reads
    ``CLUSTRIX_CONFIG_DIR``, so pointing both here means the real code runs
    against real files without going anywhere near the developer's own
    ``~/.clustrix`` or ``~/.ssh``.
    """
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(home / ".clustrix"))

    previous = os.umask(WIDE_OPEN_UMASK)
    try:
        yield home
    finally:
        os.umask(previous)


def _a_config():
    """A config with every credential field populated.

    Filled in deliberately: a writer that persists these is both a
    permissions bug and a redaction bug, and the tree walk below is what
    notices the first while ``test_no_persisted_file_contains_a_credential``
    notices the second.
    """
    return ClusterConfig(
        cluster_type="ssh",
        cluster_host="cluster.example.edu",
        username="researcher",
        password=SENTINELS["password"],
        api_key=SENTINELS["api_key"],
        hf_token=SENTINELS["hf_token"],
        environment_variables=dict(SENTINEL_ENVIRONMENT),
    )


# --------------------------------------------------------------------------
# One exercise per persisting surface, so a failure names the culprit.
# --------------------------------------------------------------------------


def _exercise_config_saves(home):
    """``ClusterConfig.save_to_file`` and the module-level ``save_config``."""
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    _a_config().save_to_file(str(config_dir / "clustrix.yml"))
    _a_config().save_to_file(str(config_dir / "clustrix.json"))
    # The opt-in that deliberately writes credentials still may not write
    # them where anyone else can read them.
    _a_config().save_to_file(str(config_dir / "with-secrets.yml"), include_secrets=True)
    # Overwriting an existing file must not inherit its mode either.
    stale = config_dir / "stale.yml"
    stale.write_text("cluster_type: local\n", encoding="utf-8")
    stale.chmod(0o666)
    _a_config().save_to_file(str(stale))
    save_config(str(config_dir / "global.yml"))


def _exercise_profile_manager(home):
    """Every ProfileManager entry point that touches disk.

    Seven mutators call ``_persist()`` without being asked to save
    anything, which is what made the mode of the profile store matter so
    much: it is written as a side effect of ordinary use.
    """
    manager = ProfileManager()
    manager.create_profile("with-credentials", _a_config())
    manager.clone_profile("with-credentials", "cloned")
    manager.rename_profile("cloned", "renamed")
    manager.save_profile("renamed", _a_config())
    manager.set_active_profile("renamed")
    manager.remove_profile("renamed")

    exported = home / "exports"
    exported.mkdir(mode=0o700)
    manager.export_profile("with-credentials", str(exported / "profile.yml"))
    manager.export_profile("with-credentials", str(exported / "profile.json"))
    manager.import_profile(str(exported / "profile.yml"), "imported")
    manager.save_to_file(str(exported / "bundle.yml"))
    manager.save_to_file(str(exported / "bundle.json"))


def _exercise_credential_writers(home):
    """The .env template and the interactive credential writer."""
    FlexibleCredentialManager(config_dir=get_config_dir())

    env_file = home / ".clustrix" / ".env"
    assert _write_credentials_to_env_file(
        env_file, {"CLUSTRIX_SSH_PASSWORD": "<redacted>"}
    )
    # Second pass: the rewrite path, over a file somebody left wide open.
    env_file.chmod(0o666)
    assert _write_credentials_to_env_file(
        env_file, {"CLUSTRIX_SSH_PASSWORD": "<redacted>"}
    )


def _exercise_notebook_widgets(home):
    """The "Save configuration" button of both notebook widgets.

    Included because a widget save is a mutator, not an export: it fires
    from ordinary editing rather than from anyone asking to persist a
    secret, and one file holds every configuration in the dropdown, so a
    single wide file is N credentials at once. It used to be a plain
    ``open(path, "w")`` under ``mkdir(exist_ok=True)``, which left
    ``~/.clustrix`` at 0755 and the file at 0644 with the password in it.
    """
    pytest.importorskip("ipywidgets")
    from clustrix.modern_notebook_widget import ModernClustrixWidget
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    legacy = EnhancedClusterConfigWidget()
    legacy.config_name.value = "widget-saved"
    legacy.cluster_type.value = "ssh"
    legacy.host_field.value = "cluster.example.edu"
    legacy.username_field.value = "researcher"
    legacy.password_field.value = SENTINELS["password"]
    legacy.env_vars_field.value = json.dumps(SENTINEL_ENVIRONMENT)
    legacy.current_config_name = "widget-saved"
    legacy.configs = {"widget-saved": legacy._save_config_from_widgets()}
    legacy.save_filename_input.value = "widget-single.yml"
    legacy._on_save_config(None)

    # The other branch: more than one configuration goes into one file, and
    # a HuggingFace token is a credential the SSH branch never produces.
    legacy.cluster_type.value = "huggingface"
    legacy.hf_token_field.value = SENTINELS["hf_token"]
    legacy.configs["widget-hf"] = legacy._save_config_from_widgets()
    # A configuration as it comes *off disk*: the widget stores the parsed
    # mapping unchanged, so a file written by an older clustrix -- or by
    # anything else -- puts keys the format does not define straight back
    # into the next save. This is the path on which aws_secret_access_key,
    # client_secret and token reached disk verbatim.
    legacy.configs["widget-restored"] = {
        "name": "widget-restored",
        "cluster_type": "ssh",
        "cluster_host": "cluster.example.edu",
        "username": "researcher",
        "environment_variables": dict(SENTINEL_ENVIRONMENT),
        **SENTINEL_UNKNOWN_KEYS,
    }
    legacy.save_filename_input.value = "widget-many.yml"
    legacy._on_save_config(None)

    modern = ModernClustrixWidget()
    modern.widgets["config_filename"].value = "widget-profiles.yml"
    modern._on_save_config(None)


def _exercise_ssh_writers(home):
    """The local files the SSH setup flow creates."""
    update_ssh_config("cluster.example.edu", "researcher", "/keys/id_ed25519", "demo")
    if shutil.which("ssh-keygen") is not None:
        generate_ssh_key(str(home / ".ssh" / "id_ed25519"), comment="clustrix-test")


EXERCISES = {
    "config saves": _exercise_config_saves,
    "profile manager": _exercise_profile_manager,
    "credential writers": _exercise_credential_writers,
    "notebook widgets": _exercise_notebook_widgets,
    "ssh writers": _exercise_ssh_writers,
}


@pytest.mark.parametrize("name", sorted(EXERCISES))
def test_each_persisting_surface_leaves_nothing_readable(
    name, private_tree, monkeypatch
):
    """Run one surface, then look at what is on disk."""
    monkeypatch.setattr(config_module, "_config", _a_config())

    EXERCISES[name](private_tree)

    wide = _too_wide(private_tree)
    assert not wide, (
        f"{name} left files or directories readable by other local users. "
        "A credential file at the umask default is world readable with the "
        "secrets already in it (issue #111). Offending paths:\n  "
        + "\n  ".join(f"{p} is {m}" for p, m in wide)
    )


def test_the_whole_flow_leaves_nothing_readable(private_tree, monkeypatch):
    """Every surface into one tree: the combination is where drift shows.

    Running them separately misses the case where one writer creates a
    parent directory that a later writer's file then sits inside.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())

    for exercise in EXERCISES.values():
        exercise(private_tree)

    wide = _too_wide(private_tree)
    assert not wide, "\n  ".join(f"{p} is {m}" for p, m in wide)


# --------------------------------------------------------------------------
# The walk itself has to be worth trusting.
# --------------------------------------------------------------------------


def test_the_walk_actually_inspects_a_realistic_number_of_files(
    private_tree, monkeypatch
):
    """A walk that found nothing would pass forever.

    If an exercise stopped writing -- an API renamed, an exception
    swallowed -- the assertion above would go green while observing an
    empty directory, which reads as coverage.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())
    for exercise in EXERCISES.values():
        exercise(private_tree)

    written = _files_under(private_tree)
    assert len(written) >= 10, f"only {len(written)} files written: {written}"

    names = {p.name for p in written}
    expected = {
        "profiles.yml",
        "clustrix.yml",
        ".env",
        "config",
        # The widget handlers catch and print their own exceptions, so an
        # exercise that stopped writing would otherwise go unnoticed.
        "widget-single.yml",
        "widget-many.yml",
        "widget-profiles.yml",
    }
    assert expected <= names, sorted(names)


@pytest.mark.parametrize("mode", [0o644, 0o604, 0o640, 0o666, 0o777])
def test_the_walk_reports_a_file_any_other_user_can_read(private_tree, mode):
    """Plant a wide file and prove the check fails on it.

    This is the half that would have caught every one of the bypasses the
    static guard missed: whatever created the file, it ends up here.
    """
    planted = private_tree / "planted.txt"
    planted.write_text("<redacted>\n", encoding="utf-8")
    planted.chmod(mode)

    assert ("planted.txt", oct(mode)) in _too_wide(private_tree)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o705, 0o777])
def test_the_walk_reports_a_directory_any_other_user_can_enter(private_tree, mode):
    planted = private_tree / "planted"
    planted.mkdir()
    planted.chmod(mode)

    assert ("planted", oct(mode)) in _too_wide(private_tree)


def test_the_walk_accepts_the_modes_that_are_correct(private_tree):
    """Flagging correct code is how a check gets an allowlist and dies."""
    (private_tree / "tight").mkdir(mode=0o700)
    (private_tree / "tight" / "file").write_text("x", encoding="utf-8")
    (private_tree / "tight" / "file").chmod(0o600)
    (private_tree / "tight" / "readonly").write_text("x", encoding="utf-8")
    (private_tree / "tight" / "readonly").chmod(0o400)
    (private_tree / "tight" / "id_ed25519.pub").write_text("ssh-ed25519 AAA\n")
    (private_tree / "tight" / "id_ed25519.pub").chmod(0o644)

    assert _too_wide(private_tree) == []


# --------------------------------------------------------------------------
# The other half of the same defect: what the bytes say, not just who can
# read them.
# --------------------------------------------------------------------------


def test_no_persisted_file_contains_a_credential(private_tree, monkeypatch):
    """Only the file that explicitly opted in may hold a secret.

    ``ProfileManager`` serialised ``asdict(config)`` directly, which routed
    around the filtering ``ClusterConfig.save_to_file`` applies, so
    passwords and API tokens reached ``profiles.yml`` in plaintext even
    though the supported path would have withheld them.

    **Values, not key names.** This test used to compare each line's key
    against ``{password, api_key, hf_token, AWS_SECRET_ACCESS_KEY}``, which
    is the same shape as the bug it exists to catch: run against the file
    the widget writes it reported nothing while ``aws_secret_access_key``,
    ``client_secret`` and ``token`` were sitting in it. Sentinel values are
    planted by the exercises instead and hunted for in the raw bytes, so a
    credential is found under whatever key it was filed -- one nobody has
    thought of, one spelled in a different case, or one buried inside a
    connection URL where there is no key to read at all.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())
    for exercise in EXERCISES.values():
        exercise(private_tree)

    leaked = []
    for path in _files_under(private_tree):
        if path.name == "with-secrets.yml":
            continue  # save_to_file(include_secrets=True) was asked for it
        if path.name == ".env":
            continue  # the credential file itself, by definition
        text = path.read_text(encoding="utf-8", errors="replace")
        for slot in _sentinels_in(text):
            leaked.append(f"{path.relative_to(private_tree)}: value planted as {slot}")

    assert not leaked, "credentials were written where nobody asked for them:\n  " + (
        "\n  ".join(leaked)
    )


def test_the_sentinels_really_were_planted(private_tree, monkeypatch):
    """A hunt for values nobody planted would pass forever.

    The assertion above is only worth anything if the sentinels actually
    passed through a persisting surface. If ``_a_config`` stopped carrying
    them the guard would go green while observing nothing, which is
    precisely how the name-list version survived so long. The file written
    by ``save_to_file(include_secrets=True)`` is the whole config as the
    exercises supplied it, so it is the place to look.

    (The widget half is pinned the same way, against what the widget was
    handed, in
    ``tests/unit/test_widget_save_withholds_unnamed_secrets.py``.)
    """
    monkeypatch.setattr(config_module, "_config", _a_config())
    _exercise_config_saves(private_tree)

    opted_in = (get_config_dir() / "with-secrets.yml").read_text(encoding="utf-8")
    planted = set(_sentinels_in(opted_in))

    # The opt-in file is the whole config, so every ClusterConfig-borne
    # sentinel has to be in it.
    expected = {"password", "api_key", "hf_token"} | set(SENTINEL_ENVIRONMENT) - {
        "OMP_NUM_THREADS"
    }
    assert expected <= planted, sorted(expected - planted)


def test_the_hunt_reports_a_sentinel_that_was_written(private_tree):
    """Plant a leak by hand and prove the check fails on it.

    Without this the guard could be looking for the wrong strings, or in
    the wrong files, and nobody would know.
    """
    leaked_file = private_tree / ".clustrix" / "leaked.yml"
    leaked_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    leaked_file.write_text(
        f"some_key_nobody_listed: {SENTINELS['legacy_auth_blob']}\n", encoding="utf-8"
    )

    found = _sentinels_in(leaked_file.read_text(encoding="utf-8"))

    assert found == ["legacy_auth_blob"]


def test_dropping_a_profile_credential_is_announced_once(private_tree):
    """Silently discarding what the user typed is its own surprise.

    Profiles deliberately do not persist credentials, so the user has to
    be told -- once, not seven times, because ``_persist()`` fires from
    every mutator and a repeated warning is one that gets filtered out.
    """
    manager = ProfileManager()
    with pytest.warns(UserWarning, match="not a credential store") as recorded:
        manager.create_profile("with-credentials", _a_config())
        manager.save_profile("with-credentials", _a_config())
        manager.set_active_profile("with-credentials")

    assert len(recorded) == 1, [str(w.message) for w in recorded]
    message = str(recorded[0].message)
    assert "password" in message and "hf_token" in message
    assert "password_env_var" in message, "the supported channel must be named"


def test_a_profile_without_credentials_is_saved_silently(private_tree):
    """The warning must mean something, so it may not fire for everyone."""
    manager = ProfileManager()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        manager.create_profile(
            "plain", ClusterConfig(cluster_type="local", default_cores=2)
        )

    assert [str(w.message) for w in recorded] == []


def test_the_opt_in_really_does_write_the_secret(private_tree, monkeypatch):
    """The exemption above must be an exemption from something real.

    If ``include_secrets=True`` silently stopped writing secrets, the test
    above would pass for the wrong reason.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())
    _exercise_config_saves(private_tree)

    text = (get_config_dir() / "with-secrets.yml").read_text(encoding="utf-8")
    # Built rather than written out: a literal "<field>:" followed by a
    # quoted string is the shape tests/unit/test_check_for_secrets.py
    # flags as an assigned credential.
    for field in ("password", "hf_token"):
        assert f"{field}:" in text, f"{field} is missing despite include_secrets"


# --------------------------------------------------------------------------
# The tree the walk above starts from is always empty, which is exactly the
# case a real machine is not in.
# --------------------------------------------------------------------------


def test_a_config_directory_that_is_already_too_wide_is_narrowed(private_tree):
    """An install made before this fix must not stay wide forever.

    The walk above only ever sees directories clustrix created during the
    test, so it cannot notice the case that matters most: ``~/.clustrix``
    on a machine where clustrix has been used already. Measured on a real
    one, it is ``drwxr-xr-x`` with ``clustrix.yml`` at 0644 inside it, and
    a fix that only tightens *new* directories changes nothing there.

    Narrowing the directory is enough to remediate the files under it --
    another local user cannot reach ``clustrix.yml`` by name through a
    directory they cannot traverse, whatever the file's own mode is -- so
    nothing here rewrites a single file the user owns.
    """
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    config_dir.chmod(0o755)
    stale = config_dir / "clustrix.yml"
    stale.write_text("cluster_type: local\n", encoding="utf-8")
    stale.chmod(0o644)

    with pytest.warns(UserWarning, match="narrowed"):
        ProfileManager()

    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    # Traversal is now impossible for anybody else, which is what makes the
    # file underneath unreachable regardless of its own mode.
    assert not _too_wide(private_tree / ".clustrix" / "profiles")


def test_narrowing_stops_at_the_config_directory(private_tree):
    """$HOME is the user's, not clustrix's.

    Remediating a directory clustrix owns is one thing; re-moding a home
    directory somebody else set up is the overreach this must not become.
    """
    home_mode_before = stat.S_IMODE(private_tree.stat().st_mode)

    ProfileManager()

    assert stat.S_IMODE(private_tree.stat().st_mode) == home_mode_before


def test_a_directory_is_created_at_0700_whatever_the_umask_is(private_tree):
    """``mkdir(mode=...)`` is masked, so on its own it guarantees nothing.

    Under ``umask 0022`` a 0700 request lands as 0755. Under ``umask
    0200`` it lands as 0500 -- a directory clustrix cannot write into,
    which made creating the parent fail the very next ``mkdir`` with
    ``PermissionError``.
    """
    for umask in (0o022, 0o200, 0o077, 0o000):
        target = private_tree / ".clustrix" / f"under-{umask:04o}" / "profiles"
        previous = os.umask(umask)
        try:
            _mkdir_private(target)
        finally:
            os.umask(previous)

        assert target.is_dir(), f"umask {umask:04o} blocked the create"
        assert stat.S_IMODE(target.stat().st_mode) == 0o700, f"umask {umask:04o}"
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
