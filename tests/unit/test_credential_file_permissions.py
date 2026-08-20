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

``clustrix/ssh_utils.py`` had the same shape at its SSH-config writer and
was not covered by the first version of this guard, which is why it is in
``CREDENTIAL_WRITERS`` below. All three now go through
``write_text_securely``.

THE RULE THIS TEST ENFORCES -- this docstring is the specification:

    **No module in ``CREDENTIAL_WRITERS`` may create or write a file except
    through ``write_text_securely()``.** Every file-creating operation in
    those modules is flagged: ``open()`` for writing (builtin or
    ``Path.open``), ``Path.write_text`` / ``write_bytes`` / ``touch``,
    ``os.open`` / ``os.fdopen`` / ``os.creat`` / ``os.symlink`` /
    ``os.link``, ``tempfile``'s factories, and ``shutil``'s copiers.
    Directory creation must pass an explicit ``mode=`` with no group or
    other bits. The single sanctioned implementation is exempt, and there
    must be exactly one of it in the package.

WHY IT IS PHRASED THAT WAY, AND NOT AS "NO CHMOD". The first version of
this guard forbade ``chmod`` in these modules, on the reasoning that the
window exists because a ``chmod`` follows a write. That rule was defeated
seven ways -- ``from os import chmod as _c``, ``getattr(os, "ch" + "mod")``,
``subprocess.run(["chmod", ...])``, binding ``p.chmod`` to a local, a
``narrow()`` helper in a third module -- and, far worse, it *passed*
``p.write_text(secret)`` with no chmod at all, which leaves the file 0644
permanently. It forbade the shape of the fix while permitting the bug it
was written to prevent. Every one of those seven still has to write the
file somewhere, so flagging the write catches all of them and the
no-chmod-at-all case too. ``test_guard_catches_every_known_bypass`` plants
each one in a copy of the real package and proves it.

WHAT IT DOES NOT CLAIM. The rule is about Python-level file creation
inside these three modules.

* A file created by a subprocess is out of scope: ``ssh_utils`` runs
  ``ssh-keygen``, and ``cli_credentials`` opens ``$EDITOR`` on the .env
  file. ``ssh-keygen`` creates the private key 0600 itself, which
  ``test_generated_private_key_is_never_world_readable`` verifies against
  the real binary under a permissive umask rather than assuming.
* ``Path.replace``/``rename`` is permitted: it moves an inode that already
  exists rather than creating one, and any insecure creation of that inode
  inside these modules is itself flagged. That is what makes the
  write-to-scratch-then-``replace()`` atomic write in ``cli_credentials``
  legitimate.
* A credential file created by a *fourth* module that these three merely
  call is not visible to a module-scoped scan.
* ``mkdir(mode=0o700, exist_ok=True)`` does not widen an existing
  directory, and this guard does not require it to.

No secret-shaped literals appear below: the fixture content uses the
``<redacted>`` spelling that ``tests/unit/test_check_for_secrets.py``
already treats as a placeholder.
"""

import ast
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

from clustrix.cli_credentials import _write_credentials_to_env_file
from clustrix.credential_manager import (
    FlexibleCredentialManager,
    write_text_securely,
)
from clustrix.ssh_utils import generate_ssh_key, update_ssh_config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The modules that write credential files or the SSH configuration that
#: points at them. None may create a file except through the helper.
CREDENTIAL_WRITERS = (
    "clustrix/config.py",
    "clustrix/cli_credentials.py",
    "clustrix/credential_manager.py",
    "clustrix/ssh_utils.py",
)

#: The one sanctioned way to put bytes in a file in those modules.
SANCTIONED_WRITER = "write_text_securely"

#: ``open()`` modes that cannot create or truncate anything.
READ_ONLY_MODES = frozenset({"r", "rb", "rt", "tr", "br", "rU", "U"})

#: File-creating callables, grouped by the module they come from so that an
#: unrelated ``some_dict.copy()`` is not mistaken for ``shutil.copy()``.
MODULE_CREATORS = {
    "os": frozenset(
        {"open", "fdopen", "creat", "symlink", "link", "mknod", "truncate"}
    ),
    "shutil": frozenset(
        {"copy", "copy2", "copyfile", "copyfileobj", "copytree", "move"}
    ),
    "tempfile": frozenset(
        {
            "mkstemp",
            "mktemp",
            "mkdtemp",
            "NamedTemporaryFile",
            "TemporaryFile",
            "SpooledTemporaryFile",
        }
    ),
}

#: Methods that create a file on any path-like object.
PATH_CREATORS = frozenset(
    {"write_text", "write_bytes", "touch", "symlink_to", "hardlink_to", "link_to"}
)

#: Directory creation: permitted, but only with an explicit narrow mode.
DIRECTORY_CREATORS = frozenset({"mkdir", "makedirs"})


def _literal(node):
    """The string ``node`` evaluates to, or ``None``.

    Folds ``"w" + "b"`` and implicit concatenation so that a mode spelled
    unusually is still recognised as a mode.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return None if left is None or right is None else left + right
    return None


def _argument(call, index, keyword):
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if len(call.args) > index:
        return call.args[index]
    return None


def _callee_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _imported_from(tree, modules):
    """``{local name: source module}`` for ``from os import open`` shapes."""
    origins = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in modules:
            for alias in node.names:
                origins[alias.asname or alias.name] = node.module
    return origins


def _open_violation(rel, call, what, index=1):
    """``open()`` is a violation unless it demonstrably only reads.

    ``index`` is where the mode sits: second argument for the builtin
    ``open(path, mode)``, first for ``Path.open(mode)``.
    """
    mode = _argument(call, index, "mode")
    if mode is None:
        return None  # defaults to "r"
    text = _literal(mode)
    if text is not None and text in READ_ONLY_MODES:
        return None
    described = repr(text) if text is not None else ast.unparse(mode)
    return f"{rel}:{call.lineno}: {what} with mode {described}"


def _mkdir_violation(rel, call, what):
    mode = _argument(call, 1 if _callee_name(call) == "makedirs" else 0, "mode")
    if mode is None:
        return f"{rel}:{call.lineno}: {what} without an explicit mode"
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, int)):
        return f"{rel}:{call.lineno}: {what} with a non-literal mode"
    if mode.value & 0o077:
        return (
            f"{rel}:{call.lineno}: {what} with mode {oct(mode.value)}, which "
            "is readable by group or other"
        )
    return None


def _exempt_lines(tree):
    """Line numbers belonging to the sanctioned helper's own body.

    The helper is the one place that is *supposed* to call ``os.open``; the
    exemption is by function, not by file, so the rest of the module is
    still covered.
    """
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == SANCTIONED_WRITER:
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _file_creations(rel, text):  # noqa: C901 - one branch per creator family
    """Every file-creating call in ``text``, as ``rel:lineno: what`` strings."""
    hits = []
    tree = ast.parse(text, filename=rel)
    exempt = _exempt_lines(tree)
    origins = _imported_from(tree, set(MODULE_CREATORS))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.lineno in exempt:
            continue
        func = node.func
        name = _callee_name(node)
        if name is None:
            continue

        owner = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in MODULE_CREATORS:
                owner = func.value.id
        elif isinstance(func, ast.Name):
            owner = origins.get(func.id)

        if owner and name in MODULE_CREATORS[owner]:
            if name == "open":
                hit = _open_violation(rel, node, f"calls {owner}.open()")
                # os.open() takes flags, not a mode string: always a hit.
                hits.append(hit or f"{rel}:{node.lineno}: calls {owner}.open()")
            else:
                hits.append(f"{rel}:{node.lineno}: calls {owner}.{name}()")
            continue

        if name in PATH_CREATORS:
            hits.append(f"{rel}:{node.lineno}: calls {name}()")
        elif name in DIRECTORY_CREATORS:
            hit = _mkdir_violation(rel, node, f"calls {name}()")
            if hit:
                hits.append(hit)
        elif name == "open":
            # Builtin ``open(path, mode)`` versus ``Path.open(mode)``.
            index = 1 if isinstance(func, ast.Name) else 0
            hit = _open_violation(rel, node, "opens a file", index)
            if hit:
                hits.append(hit)

    return sorted(set(hits))


def _creations_in_package(package_root):
    hits = []
    for rel in CREDENTIAL_WRITERS:
        path = package_root.parent / rel
        hits.extend(_file_creations(rel, path.read_text(encoding="utf-8")))
    return sorted(hits)


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


def test_a_descriptor_opened_during_the_window_cannot_read_the_secret(
    permissive_umask, tmp_path
):
    """The pre-existing-file case, as an attacker actually exploits it.

    Permissions are checked when a file is *opened*, so narrowing the mode
    afterwards does not revoke a descriptor somebody already holds. With
    ``O_TRUNC`` on the existing inode -- the previous implementation --
    this test read back the credential through a descriptor opened while
    the file was still 0666. The fix is that the secret goes into a new
    inode that nobody could have opened.
    """
    target = tmp_path / "secrets.env"
    target.write_text("stale\n", encoding="utf-8")
    target.chmod(0o666)

    eavesdropper = os.open(str(target), os.O_RDONLY)
    try:
        write_text_securely(target, 'password = "<redacted>"\n')
        overheard = os.pread(eavesdropper, 4096, 0)
    finally:
        os.close(eavesdropper)

    assert b"<redacted>" not in overheard, (
        "a descriptor opened while the file was still world-readable read "
        f"the credential back: {overheard!r}"
    )
    assert _mode(target) == 0o600
    assert "<redacted>" in target.read_text(encoding="utf-8")


def test_write_text_securely_does_not_write_through_a_symlink(
    permissive_umask, tmp_path
):
    """A symlink at the target used to redirect the secret, and the chmod.

    Before the fix this wrote the credential into ``victim`` and left it at
    0600 -- writing a secret into a file chosen by whoever planted the
    link.
    """
    victim = tmp_path / "victim.txt"
    victim.write_text("not mine\n", encoding="utf-8")
    victim.chmod(0o644)
    link = tmp_path / "secrets.env"
    link.symlink_to(victim)

    write_text_securely(link, 'password = "<redacted>"\n')

    assert victim.read_text(encoding="utf-8") == "not mine\n"
    assert _mode(victim) == 0o644
    assert not link.is_symlink(), "the link must have been replaced, not followed"
    assert _mode(link) == 0o600


def test_append_mode_creates_owner_only_and_leaves_an_existing_file_alone(
    permissive_umask, tmp_path
):
    """``append=True`` is for ~/.ssh/config: create tight, never re-mode.

    The two guarantees differ deliberately, and this pins both: a file the
    helper creates is 0600 from the instant it exists, and a file the user
    already had keeps its own mode and its own content.
    """
    fresh = tmp_path / "config"
    write_text_securely(fresh, "Host one\n", append=True)
    assert _mode(fresh) == 0o600
    assert fresh.read_text(encoding="utf-8") == "Host one\n"

    fresh.chmod(0o644)
    write_text_securely(fresh, "Host two\n", append=True)
    assert _mode(fresh) == 0o644, "an existing file's mode is the user's business"
    assert fresh.read_text(encoding="utf-8") == "Host one\nHost two\n"


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


def test_ssh_config_entry_is_created_owner_only(
    permissive_umask, tmp_path, monkeypatch
):
    """``update_ssh_config`` writes a real ~/.ssh/config, so give it one.

    ``Path.home()`` reads ``$HOME``, so this exercises the real function
    against a real file rather than standing in for it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".ssh" / "config"

    update_ssh_config("cluster.example.edu", "researcher", "/keys/id_ed25519", "demo")

    assert config.exists()
    assert _mode(config) == 0o600, (
        "a config this function created existed at the umask default until "
        f"the old chmod landed; got {oct(_mode(config))}"
    )
    assert "Host demo" in config.read_text(encoding="utf-8")

    # A config the user already had keeps their mode and their content.
    config.chmod(0o644)
    update_ssh_config("other.example.edu", "researcher", "/keys/id_ed25519", "second")
    text = config.read_text(encoding="utf-8")
    assert "Host demo" in text and "Host second" in text
    assert _mode(config) == 0o644


def test_generated_private_key_is_never_world_readable(permissive_umask, tmp_path):
    """Run the real ``ssh-keygen`` and look at what it leaves on disk.

    ``generate_ssh_key`` chmods the key afterwards, which would be too late
    if ``ssh-keygen`` created it at the umask default. It does not -- it
    creates the key 0600 itself -- but that is a property of a binary we do
    not ship, so it is verified rather than assumed. Under umask 000 a
    umask-derived key would be 0666.
    """
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is not installed")

    key_path = tmp_path / "keys" / "id_ed25519"
    private, public = generate_ssh_key(str(key_path), comment="clustrix-test")

    raw = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "raw_key")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert raw.returncode == 0
    assert _mode(tmp_path / "raw_key") == 0o600, (
        "ssh-keygen no longer creates keys owner-only, so the chmod in "
        "generate_ssh_key() is now closing a real window and must be "
        "replaced by something without one"
    )

    assert _mode(pathlib.Path(private)) == 0o600
    assert _mode(pathlib.Path(public)) == 0o644
    assert _mode(key_path.parent) & 0o077 == 0, "the key directory is not private"


# --------------------------------------------------------------------------
# Structural: the half that fails against the pre-fix code.
# --------------------------------------------------------------------------


def test_the_scan_actually_reads_the_modules():
    """A scan that parses nothing would pass forever."""
    for rel in CREDENTIAL_WRITERS:
        path = REPO_ROOT / rel
        assert path.exists(), f"{rel} moved; this guard is now checking nothing"
        source = path.read_text(encoding="utf-8")
        assert (
            f"{SANCTIONED_WRITER}(" in source
        ), f"{rel} no longer writes through {SANCTIONED_WRITER}()"


def test_there_is_exactly_one_secure_writer_in_the_package():
    """A second copy of the helper is a second thing to get wrong."""
    definitions = []
    for path in sorted((REPO_ROOT / "clustrix").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == SANCTIONED_WRITER:
                definitions.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    # Pinned to a file, not a line: the definition lives in config.py, the
    # lowest-level module, because credential_manager imports config and the
    # reverse would be a cycle. What matters is that there is exactly one --
    # a second copy is a second thing to get wrong, and the copy this
    # replaced had already drifted into missing O_NOFOLLOW and reusing a
    # pre-existing inode.
    assert len(definitions) == 1, definitions
    assert definitions[0].startswith("clustrix/config.py:"), definitions


def test_credential_writers_create_files_only_through_the_helper():
    hits = _creations_in_package(REPO_ROOT / "clustrix")
    assert not hits, (
        "A credential file created by anything other than "
        f"{SANCTIONED_WRITER}() exists at the umask default -- world "
        "readable, with the secrets already in it -- for as long as it "
        "takes to narrow it, and permanently if nobody remembers to narrow "
        "it at all (issue #111). Route the write through the helper. "
        "Offending lines:\n  " + "\n  ".join(hits)
    )


#: Every way the previous "no chmod" guard was defeated, plus the case it
#: rewarded: writing the file and never narrowing it at all.
BYPASSES = {
    "aliased_chmod_import": (
        "from os import chmod as _c\n"
        "\n"
        "\n"
        "def save(path, secret):\n"
        "    path.write_text(secret)\n"
        "    _c(path, 0o600)\n"
    ),
    "getattr_chmod": (
        "import os\n"
        "\n"
        "\n"
        "def save(path, secret):\n"
        "    path.write_text(secret)\n"
        '    getattr(os, "ch" + "mod")(path, 0o600)\n'
    ),
    "subprocess_chmod": (
        "import subprocess\n"
        "\n"
        "\n"
        "def save(path, secret):\n"
        "    path.write_text(secret)\n"
        '    subprocess.run(["chmod", "600", str(path)], check=True)\n'
    ),
    "bound_method_chmod": (
        "def save(path, secret):\n"
        "    path.write_text(secret)\n"
        "    narrow = path.chmod\n"
        "    narrow(0o600)\n"
    ),
    "helper_in_a_third_module": (
        "from clustrix.config import get_config_dir\n"
        "\n"
        "\n"
        "def narrow(path):\n"
        "    pass\n"
        "\n"
        "\n"
        "def save(path, secret):\n"
        "    path.write_text(secret)\n"
        "    narrow(path)\n"
    ),
    # The one the old guard rewarded: no chmod at all, 0644 forever.
    "no_chmod_at_all": ("def save(path, secret):\n" "    path.write_text(secret)\n"),
    "open_for_writing": (
        "def save(path, secret):\n"
        '    with open(path, "w") as handle:\n'
        "        handle.write(secret)\n"
    ),
    "path_open_for_writing": (
        "def save(path, secret):\n"
        '    with path.open("w", encoding="utf-8") as handle:\n'
        "        handle.write(secret)\n"
    ),
    "computed_open_mode": (
        "def save(path, secret, mode):\n"
        "    with open(path, mode) as handle:\n"
        "        handle.write(secret)\n"
    ),
    "write_bytes": (
        "def save(path, secret):\n" "    path.write_bytes(secret.encode())\n"
    ),
    "touch_then_write": (
        "def save(path, secret):\n" "    path.touch()\n" "    path.write_text(secret)\n"
    ),
    "named_temporary_file": (
        "import tempfile\n"
        "\n"
        "\n"
        "def save(secret):\n"
        "    with tempfile.NamedTemporaryFile(delete=False) as handle:\n"
        "        handle.write(secret.encode())\n"
    ),
    "shutil_copy": (
        "import shutil\n"
        "\n"
        "\n"
        "def save(source, destination):\n"
        "    shutil.copy(source, destination)\n"
    ),
    "raw_os_open": (
        "import os\n"
        "\n"
        "\n"
        "def save(path, secret):\n"
        "    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT)\n"
        "    os.write(fd, secret.encode())\n"
        "    os.close(fd)\n"
    ),
    "wide_directory": (
        "def prepare(directory):\n" "    directory.mkdir(mode=0o755, exist_ok=True)\n"
    ),
    "directory_without_a_mode": (
        "def prepare(directory):\n" "    directory.mkdir(exist_ok=True)\n"
    ),
}


@pytest.fixture
def real_package_copy(tmp_path):
    """A copy of the real credential modules, for planting bypasses in.

    Planting into copies of the real files rather than into a lone snippet
    proves the guard still finds the violation in situ, and that the real
    code around it produces no noise of its own.
    """
    destination = tmp_path / "clustrix"
    destination.mkdir()
    for rel in CREDENTIAL_WRITERS:
        shutil.copy(REPO_ROOT / rel, destination / pathlib.Path(rel).name)
    return destination


@pytest.mark.parametrize("name", sorted(BYPASSES))
@pytest.mark.parametrize("target", CREDENTIAL_WRITERS)
def test_guard_catches_every_known_bypass(name, target, real_package_copy):
    """Append each bypass to each real module and prove the guard reports it."""
    planted = real_package_copy / pathlib.Path(target).name
    planted.write_text(
        planted.read_text(encoding="utf-8") + "\n\n" + BYPASSES[name],
        encoding="utf-8",
    )

    hits = _creations_in_package(real_package_copy)

    assert hits, f"bypass {name!r} planted in {target} was not caught"
    assert all(
        h.startswith(f"{target}:") for h in hits
    ), f"the other real modules produced noise as well: {hits}"

    with pytest.raises(AssertionError):
        assert not hits, "planted violation must trip the same assertion"


#: Shapes that are not violations, each drawn from the real modules.
INNOCENT = {
    # ssh_utils reads existing keys and configs.
    "open_for_reading": (
        "def read(path):\n"
        '    with open(path, "r") as handle:\n'
        "        return handle.read()\n"
    ),
    "open_with_no_mode": (
        "def read(path):\n"
        "    with open(path) as handle:\n"
        "        return handle.read()\n"
    ),
    "read_text": ("def read(path):\n" '    return path.read_text(encoding="utf-8")\n'),
    # The atomic write: the scratch file was created by the helper, and
    # replace() moves that inode rather than creating a new one.
    "atomic_replace": (
        "def save(temp_file, env_file):\n" "    temp_file.replace(env_file)\n"
    ),
    # str.replace is not Path.replace, and neither creates a file.
    "string_replace": (
        "def alias(hostname):\n" '    return hostname.replace(".", "_")\n'
    ),
    # A private directory, created private.
    "private_directory": (
        "def prepare(directory):\n" "    directory.mkdir(mode=0o700, exist_ok=True)\n"
    ),
    # dict.copy() is not shutil.copy().
    "dict_copy": ("def defaults(settings):\n" "    return settings.copy()\n"),
    # ssh-keygen creates its own key; subprocesses are out of scope.
    "subprocess_keygen": (
        "import subprocess\n"
        "\n"
        "\n"
        "def generate(path):\n"
        '    subprocess.run(["ssh-keygen", "-f", str(path)], check=True)\n'
    ),
    "sanctioned_write": (
        "from clustrix.credential_manager import write_text_securely\n"
        "\n"
        "\n"
        "def save(path, secret):\n"
        "    write_text_securely(path, secret)\n"
    ),
    "sanctioned_append": (
        "from clustrix.credential_manager import write_text_securely\n"
        "\n"
        "\n"
        "def save(path, entry):\n"
        "    write_text_securely(path, entry, append=True)\n"
    ),
}


@pytest.mark.parametrize("name", sorted(INNOCENT))
def test_guard_is_quiet_about_legitimate_code(name):
    """Flagging real code is how a guard gets an allowlist and dies."""
    assert _file_creations("planted.py", INNOCENT[name]) == []
