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
import subprocess
import warnings

import pytest
import yaml

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


def _awkward_sentinel(slot):
    """A sentinel that no serializer can write literally.

    A newline, a tab, an ESC and a non-ASCII character. YAML quotes and
    escapes all four (``yaml.dump`` also escapes non-ASCII to ``\\uXXXX``
    unless asked not to) and JSON escapes them too, so **searching the raw
    bytes for this value finds nothing** however plainly it was written.

    That is not a hypothetical. ``private_key`` below is a PEM, which is
    multi-line by construction, so the raw-byte hunt this guard used to be
    missed a private key written out verbatim -- the single worst thing it
    is supposed to catch.
    """
    return "-".join(["clustrix", "sentinel", slot, "v\u00e5lue\twith\x1bcontrol\n"])


#: A sentinel shaped like the thing it stands for: multi-line, so no
#: serializer emits it literally and only a decoded search can find it.
PEM_SENTINEL = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    + _sentinel("privatekey")
    + "\n-----END OPENSSH PRIVATE KEY-----\n"
)


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
    "password": _awkward_sentinel("password"),
    "api_key": _sentinel("apikey"),
    "hf_token": _sentinel("hftoken"),
    # Keys that are not ClusterConfig fields, which is how they reached
    # disk verbatim: the widget hands strip_secret_fields whatever a
    # previously saved file contained.
    "aws_secret_access_key": _sentinel("aws"),
    "client_secret": _sentinel("clientsecret"),
    "private_key": PEM_SENTINEL,
    "token": _sentinel("token"),
    "secret_key": _sentinel("secretkey"),
    "PASSWORD": _sentinel("shoutypassword"),
    "legacy_auth_blob": _sentinel("blob"),
    # Environment variable names chosen by the user.
    "AWS_SECRET_ACCESS_KEY": _sentinel("envaws"),
    "SSH_PASSPHRASE": _awkward_sentinel("passphrase"),
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


def _decoded_strings(document):
    """Every string reachable inside a parsed YAML/JSON document."""
    if isinstance(document, str):
        yield document
    elif isinstance(document, dict):
        for key, value in document.items():
            yield from _decoded_strings(key)
            yield from _decoded_strings(value)
    elif isinstance(document, (list, tuple)):
        for item in document:
            yield from _decoded_strings(item)


def _readable_forms(path):
    """``path`` as bytes, and as every string a parser gets back out of it.

    Both halves are needed and neither is sufficient. The raw bytes catch a
    secret in a file with no structure at all -- a ``.env`` line, an SSH
    config, a stray log. The decoded strings catch everything a serializer
    escapes on the way out, which the raw bytes cannot: a newline, a tab, an
    ESC or a non-ASCII character in the value is enough to hide it, and a
    PEM private key contains newlines by definition. ``yaml.safe_load``
    handles JSON too, JSON being a subset of YAML.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    forms = [raw]
    try:
        forms.extend(_decoded_strings(yaml.safe_load(raw)))
    except Exception:
        # Not a structured document -- an SSH config, a key file, a log.
        # The raw form above already covers it.
        pass
    return forms


def _sentinels_in_file(path):
    """Which planted values ``path`` holds, however it spells them."""
    forms = _readable_forms(path)
    return sorted(
        slot
        for slot, value in SENTINELS.items()
        if any(value in form for form in forms)
    )


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


def _leaks_under(root, opted_in):
    """Every planted value found in a file nobody asked to hold one.

    ``opted_in`` is a set of **paths**, declared by the exercise that asked
    for them. It used to be two filenames, skipped with
    ``if path.name == "with-secrets.yml" or path.name == ".env": continue``,
    which exempts anything that happens to pick one of those names -- a
    ``.env`` written somewhere unexpected was never looked at, and any
    writer could evade the guard entirely by choosing the name.
    """
    leaked = []
    for path in _files_under(root):
        if path in opted_in:
            continue  # save_to_file(include_secrets=True) was asked for it
        for slot in _sentinels_in_file(path):
            leaked.append(f"{path.relative_to(root)}: value planted as {slot}")
    return leaked


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

    # A writer that resolves a relative path lands in the *working*
    # directory, which is not under ``$HOME`` and so was invisible to a walk
    # that started at ``home``. Redirecting it into ``tmp_path`` -- which is
    # what the hunts below walk -- makes that case observable instead of
    # dropping a file into the repository, which is how a stray
    # ``test_config.yml`` used to reach the checkout.
    working = tmp_path / "cwd"
    working.mkdir(mode=0o700)
    monkeypatch.chdir(working)

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
    """``ClusterConfig.save_to_file`` and the module-level ``save_config``.

    Returns the paths this exercise *asked* to hold credentials, so the
    content hunt can exempt them by identity. It used to exempt them by
    filename -- ``if path.name == "with-secrets.yml": continue`` -- which
    means any writer that happened to choose one of those two names was
    exempt too, and a real ``.env`` written somewhere unexpected was never
    looked at.
    """
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
    # A relative path resolves against the *working* directory, which is not
    # under $HOME. A walk rooted at $HOME cannot see this file at all.
    save_config("stray-relative.yml")
    return {config_dir / "with-secrets.yml"}


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
    return set()


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
    return set()


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
    return set()


def _exercise_ssh_writers(home):
    """The local files the SSH setup flow creates."""
    update_ssh_config("cluster.example.edu", "researcher", "/keys/id_ed25519", "demo")
    if shutil.which("ssh-keygen") is not None:
        generate_ssh_key(str(home / ".ssh" / "id_ed25519"), comment="clustrix-test")
    return set()


EXERCISES = {
    "config saves": _exercise_config_saves,
    "profile manager": _exercise_profile_manager,
    "credential writers": _exercise_credential_writers,
    "notebook widgets": _exercise_notebook_widgets,
    "ssh writers": _exercise_ssh_writers,
}


@pytest.mark.parametrize("name", sorted(EXERCISES))
def test_each_persisting_surface_leaves_nothing_readable(
    name, private_tree, tmp_path, monkeypatch
):
    """Run one surface, then look at what is on disk.

    Rooted at ``tmp_path`` rather than at ``$HOME``: a writer that resolves
    a relative path writes into the working directory, and a walk that
    starts inside ``$HOME`` cannot see it however wide it is.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())

    EXERCISES[name](private_tree)

    wide = _too_wide(tmp_path)
    assert not wide, (
        f"{name} left files or directories readable by other local users. "
        "A credential file at the umask default is world readable with the "
        "secrets already in it (issue #111). Offending paths:\n  "
        + "\n  ".join(f"{p} is {m}" for p, m in wide)
    )


def test_the_whole_flow_leaves_nothing_readable(private_tree, tmp_path, monkeypatch):
    """Every surface into one tree: the combination is where drift shows.

    Running them separately misses the case where one writer creates a
    parent directory that a later writer's file then sits inside.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())

    for exercise in EXERCISES.values():
        exercise(private_tree)

    wide = _too_wide(tmp_path)
    assert not wide, "\n  ".join(f"{p} is {m}" for p, m in wide)


# --------------------------------------------------------------------------
# The walk itself has to be worth trusting.
# --------------------------------------------------------------------------


def test_the_walk_actually_inspects_a_realistic_number_of_files(
    private_tree, tmp_path, monkeypatch
):
    """A walk that found nothing would pass forever.

    If an exercise stopped writing -- an API renamed, an exception
    swallowed -- the assertion above would go green while observing an
    empty directory, which reads as coverage.
    """
    monkeypatch.setattr(config_module, "_config", _a_config())
    for exercise in EXERCISES.values():
        exercise(private_tree)

    written = _files_under(tmp_path)
    assert len(written) >= 10, f"only {len(written)} files written: {written}"

    names = {p.name for p in written}
    expected = {
        "profiles.yml",
        "clustrix.yml",
        ".env",
        "config",
        # Written through a relative path, so it lands outside $HOME. Its
        # presence here is what proves the walk reaches past $HOME at all.
        "stray-relative.yml",
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


def test_no_persisted_file_contains_a_credential(private_tree, tmp_path, monkeypatch):
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
    opted_in = set()
    for exercise in EXERCISES.values():
        opted_in |= exercise(private_tree)

    leaked = _leaks_under(tmp_path, opted_in)

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

    opted_in = get_config_dir() / "with-secrets.yml"
    planted = set(_sentinels_in_file(opted_in))

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

    assert _sentinels_in_file(leaked_file) == ["legacy_auth_blob"]


@pytest.mark.parametrize("slot", ["private_key", "password", "SSH_PASSPHRASE"])
@pytest.mark.parametrize("dump", [yaml.dump, json.dumps])
def test_a_serialized_secret_is_found_although_the_bytes_never_contain_it(
    private_tree, slot, dump
):
    """The blind spot this guard had, reproduced and closed.

    The hunt compared the file's **raw bytes** against the planted values.
    Any value a serializer has to escape -- a newline, a tab, an ESC, a
    non-ASCII character -- is therefore never found however plainly it was
    written, and a PEM private key contains newlines by construction. So a
    private key written verbatim into a config file produced ``found=[]``.

    This writes each awkward sentinel out with a real serializer, asserts
    that the raw-byte search really does miss it (otherwise the test proves
    nothing), and then asserts the decoded search finds it.
    """
    leaked_file = private_tree / ".clustrix" / f"leaked-{slot}.yml"
    leaked_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    leaked_file.write_text(dump({"some_key": SENTINELS[slot]}), encoding="utf-8")

    raw = leaked_file.read_text(encoding="utf-8")
    assert _sentinels_in(raw) == [], (
        "this sentinel does not exercise escaping, so the test below would "
        "have passed against the old raw-bytes-only hunt: " + repr(raw)
    )

    assert slot in _sentinels_in_file(leaked_file)


def test_the_hunt_exempts_by_path_and_not_by_filename(private_tree, tmp_path):
    """A leak into a file *called* ``.env`` must still be reported.

    Both historical exemptions were by filename. Planting the same sentinel
    under each of those two names, in a directory no exercise opted in for,
    is what tells the two rules apart: by name both are invisible, by path
    both are leaks.
    """
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / ".env").write_text(
        f"SSH_PASSWORD={SENTINELS['api_key']}\n", encoding="utf-8"
    )
    (elsewhere / "with-secrets.yml").write_text(
        f"token: {SENTINELS['token']}\n", encoding="utf-8"
    )
    genuinely_opted_in = elsewhere / "asked-for.yml"
    genuinely_opted_in.write_text(
        f"password: {SENTINELS['password']!r}\n", encoding="utf-8"
    )

    leaked = _leaks_under(tmp_path, {genuinely_opted_in})

    assert sorted(leaked) == [
        "somewhere-else/.env: value planted as api_key",
        "somewhere-else/with-secrets.yml: value planted as token",
    ]


def test_a_dot_env_file_is_no_longer_exempt_by_its_name(private_tree):
    """The hunt used to ``continue`` on any file called ``.env``.

    So did it on ``with-secrets.yml``. Both are exemptions by *filename*,
    which means anything that happens to pick one of those names is exempt
    too -- and a real ``.env`` written somewhere nobody expected was never
    looked at either. Deliberate opt-ins are now declared by the exercise
    that asked for them, as paths.
    """
    planted = private_tree / "somewhere" / ".env"
    planted.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    planted.write_text(f"SSH_PASSWORD={SENTINELS['api_key']}\n", encoding="utf-8")

    assert _sentinels_in_file(planted) == ["api_key"]


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


def test_a_config_directory_that_is_already_too_wide_is_reported(private_tree):
    """An install made before this fix must not stay wide *unnoticed*.

    **This assertion is the reverse of what it used to be, deliberately.**
    It required ``ProfileManager()`` to chmod an existing ``~/.clustrix``
    from 0755 to 0700 and warn that it had "narrowed" it. That behaviour was
    an overreach with two demonstrated consequences, so the assertion was
    asserting a defect:

    * ``CLUSTRIX_CONFIG_DIR=$HOME`` is supported and documented (containers,
      CI images, shared machines). It made the configuration directory
      ``$HOME``, and ``_clustrix_owned`` then yielded ``$HOME`` -- so
      constructing a ``ProfileManager`` chmod-ed the user's home directory
      to 0700. That case is pinned below.
    * A configuration directory deliberately shared with a group at 0770 was
      forced to 0700 and the group locked out of a directory the user had
      set up for them.

    The remediation the old behaviour existed for is not lost, it is handed
    back to the person entitled to make it: the warning names the mode and
    the exact ``chmod`` command. What clustrix *creates* is still 0700, and
    every file it writes is 0600 in its own right -- the directory mode is
    defence in depth, never the guarantee.
    """
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    config_dir.chmod(0o755)

    with pytest.warns(UserWarning, match="will not change the mode") as recorded:
        ProfileManager()

    assert (
        stat.S_IMODE(config_dir.stat().st_mode) == 0o755
    ), "clustrix re-moded a directory it did not create"
    message = str(recorded[0].message)
    assert f"chmod 700 {config_dir}" in message, message
    # What it did create is still its own to mode.
    assert stat.S_IMODE((config_dir / "profiles").stat().st_mode) == 0o700


def test_a_group_shared_config_directory_is_left_as_the_user_set_it(private_tree):
    """0770 on a shared machine is a decision, not an accident to correct."""
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o770, parents=True, exist_ok=True)
    config_dir.chmod(0o770)

    with pytest.warns(UserWarning, match="will not change the mode"):
        ProfileManager()

    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o770


def test_the_home_directory_is_never_narrowed_even_when_it_is_the_config_dir(
    private_tree, monkeypatch
):
    """``CLUSTRIX_CONFIG_DIR=$HOME`` must not chmod the home directory.

    The reproduction for the ``_clustrix_owned`` defect: it stopped at the
    configuration directory, which is the right rule only while that
    directory is *inside* ``$HOME``. Point it at ``$HOME`` -- an entirely
    supported setting -- and "stop at the configuration directory" became
    "narrow ``$HOME``".
    """
    private_tree.chmod(0o755)
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(private_tree))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ProfileManager()

    assert stat.S_IMODE(private_tree.stat().st_mode) == 0o755
    assert stat.S_IMODE((private_tree / "profiles").stat().st_mode) == 0o700


def test_the_home_directory_asked_for_directly_is_still_left_alone(private_tree):
    """``ProfileManager(config_dir=$HOME)`` must not touch ``$HOME`` either.

    The other half of the exclusion. ``_clustrix_owned`` yields the
    directory it was handed unconditionally before it consults the
    configuration directory at all, so a caller naming ``$HOME`` reached it
    by a different route than ``CLUSTRIX_CONFIG_DIR=$HOME`` does.
    """
    private_tree.chmod(0o755)

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        ProfileManager(config_dir=str(private_tree))

    assert stat.S_IMODE(private_tree.stat().st_mode) == 0o755
    mentioned = [
        str(w.message)
        for w in recorded
        if str(w.message).startswith(f"{private_tree} is mode")
    ]
    assert mentioned == []


def test_the_home_directory_is_not_even_mentioned(private_tree, monkeypatch):
    """A warning that fires on every ordinary machine is one nobody reads.

    ``$HOME`` is 0755 on essentially every real installation, so warning
    about it would fire always and be filtered out -- taking the warning
    about ``~/.clustrix``, which the user *can* act on, with it.
    """
    private_tree.chmod(0o755)
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(private_tree))

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        ProfileManager()

    assert [w for w in recorded if str(private_tree) + "'" in str(w.message)] == []
    assert [
        w for w in recorded if str(w.message).startswith(f"{private_tree} is mode")
    ] == []


def test_narrowing_stops_below_the_home_directory(private_tree):
    """$HOME is the user's, not clustrix's."""
    home_mode_before = stat.S_IMODE(private_tree.stat().st_mode)

    ProfileManager()

    assert stat.S_IMODE(private_tree.stat().st_mode) == home_mode_before


def test_an_immutable_config_directory_is_reported_not_swallowed(private_tree):
    """A directory clustrix cannot write into must say so, not fail silently.

    Provoked with a real ``chflags uchg``, which is what makes this
    testable without a second uid: an immutable directory refuses ``mkdir``
    and ``chmod`` alike, with EPERM, for the *owner*. That is the same
    errno a directory belonging to somebody else produces, so it exercises
    the branch a second account would.

    The widget still has to open with an unwritable configuration
    directory -- profiles then live for the session only -- but the user is
    told, because they are the only one who can fix it.
    """
    if shutil.which("chflags") is None:
        pytest.skip("chflags is not available on this platform")

    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if subprocess.run(["chflags", "uchg", str(config_dir)]).returncode != 0:
        pytest.skip("this filesystem does not support the immutable flag")
    try:
        # Nothing was created, so the mkdir of `profiles` inside it is the
        # call that fails.
        with pytest.raises(OSError):
            _mkdir_private(config_dir / "profiles")

        with pytest.warns(UserWarning, match="Cannot create profile directory"):
            ProfileManager()
    finally:
        subprocess.run(["chflags", "nouchg", str(config_dir)])


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
