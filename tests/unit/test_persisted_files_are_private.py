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

No secret-shaped literals appear below -- the ``<redacted>`` spelling is
the one ``tests/unit/test_check_for_secrets.py`` already treats as a
placeholder.
"""

import os
import shutil
import stat
import warnings

import pytest

import clustrix.config as config_module
from clustrix.cli_credentials import _write_credentials_to_env_file
from clustrix.config import ClusterConfig, get_config_dir, save_config
from clustrix.credential_manager import FlexibleCredentialManager
from clustrix.profile_manager import ProfileManager
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
        password="<redacted>",
        api_key="<redacted>",
        hf_token="<redacted>",
        environment_variables={"OMP_NUM_THREADS": "4", "AWS_SECRET_ACCESS_KEY": "<r>"},
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
    legacy.password_field.value = "<redacted>"
    legacy.current_config_name = "widget-saved"
    legacy.configs = {"widget-saved": legacy._save_config_from_widgets()}
    legacy.save_filename_input.value = "widget-single.yml"
    legacy._on_save_config(None)

    # The other branch: more than one configuration goes into one file, and
    # a HuggingFace token is a credential the SSH branch never produces.
    legacy.cluster_type.value = "huggingface"
    legacy.hf_token_field.value = "<redacted>"
    legacy.configs["widget-hf"] = legacy._save_config_from_widgets()
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
        for line in text.splitlines():
            key = line.split(":")[0].strip().lstrip("- ")
            if key in {"password", "api_key", "hf_token", "AWS_SECRET_ACCESS_KEY"}:
                leaked.append(f"{path.relative_to(private_tree)}: {line.strip()}")

    assert not leaked, "credentials were written where nobody asked for them:\n  " + (
        "\n  ".join(leaked)
    )


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
