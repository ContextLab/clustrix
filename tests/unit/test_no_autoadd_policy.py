#!/usr/bin/env python3
"""Unknown SSH host keys must be refused -- proven statically and live.

Regression guard for issue #148.

Paramiko's auto-add policy silently trusts whatever host key a server offers
on first contact, which is precisely the machine-in-the-middle hole
``clustrix/ssh_security.py`` exists to close. Production code was fixed
first; the test suite kept 37 call sites of its own, which meant every SSH
connection the real-world suite opened was still unverified, and any new test
copied from a neighbouring file inherited the hole.

This module has two halves.

**The static guard.** An earlier version searched for the literal name of
paramiko's auto-add policy class and nothing else. That checks a *name*, but
the property that matters is that an unknown host key is *refused*, and a
reviewer defeated the name check four ways -- each of which really did
connect to a server whose key was unknown:

1. ``getattr(paramiko, "Auto" + "AddPolicy")``
2. ``vars(paramiko)["Auto" "AddPolicy"]``
3. a custom ``MissingHostKeyPolicy`` subclass whose ``missing_host_key``
   returns instead of raising
4. paramiko's warn-then-accept policy, which prints a warning and then
   trusts the key anyway

Nothing about the argument's *spelling* can catch all of those, because an
expression can be spelled infinitely many ways. What they have in common is
the only thing that can install a policy at all:
``client.set_missing_host_key_policy(...)``. So the guard is now structural
(``ast``, not substring) and checks three things:

* nobody outside ``clustrix/ssh_security.py`` may **call**
  ``set_missing_host_key_policy`` -- whatever the argument is. Everyone else
  calls ``configure_host_key_policy(client, config)``, which is the one place
  the reject/auto-add decision is made and audited.
* nobody outside that file may **subclass** ``MissingHostKeyPolicy``. A
  subclass is how bypass 3 smuggles an accept-everything policy past a
  name check, and a legitimate one belongs next to the existing
  ``RejectUnknownHostKeyPolicy``.
* the accepting policy classes may not be **named** outside a short
  allowlist. This is the weakest of the three and is kept only because it
  catches a bare ``from paramiko import ...`` of one of them at the point it
  is written rather than at the point it is used.

**The live proof.** A static guard cannot show that the default actually
refuses anything. Every converted test in the suite passes
``ssh_host_key_policy="auto_add"`` -- necessarily, since the in-process
server generates a fresh key per run -- so the default ``reject`` path was
never once exercised over a real socket. ``test_default_policy_refuses...``
below does exactly that, end to end, against the real server.

The forbidden class names are assembled at runtime so that this module is
not itself a match and the allowlist stays honest rather than growing an
entry for this file.
"""

import ast
import pathlib

import paramiko
import pytest

from clustrix.config import ClusterConfig
from clustrix.ssh_security import (
    HostKeyVerificationError,
    configure_host_key_policy,
    user_known_hosts_path,
)
from tests.ssh_server import LocalSSHServer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Assembled rather than written out, so this file is not itself a match.
NEEDLE = "Auto" + "AddPolicy"

#: Paramiko policy classes that accept an unknown host key. The second is
#: the warn-then-accept one, which is auto-add with extra noise.
ACCEPTING_POLICY_NAMES = frozenset({NEEDLE, "Warning" + "Policy"})

#: The base class every host-key policy derives from. Subclassing it outside
#: ``ssh_security.py`` is how an accept-everything policy hides from a scan
#: that only looks at names.
POLICY_BASE = "Missing" + "HostKeyPolicy"

#: Installing a policy goes through exactly this method, however the policy
#: object was obtained. This is the choke point the guard actually defends.
INSTALL_CALL = "set_missing_host_key_policy"

#: The only Python file permitted to install a host key policy or to define
#: one. ``configure_host_key_policy`` lives here and is what everyone else
#: must call.
POLICY_IMPLEMENTATION = "clustrix/ssh_security.py"

#: Files additionally permitted to *name* an accepting policy class: the
#: implementation, plus the test for it, which has to be able to assert on
#: the object that implementation produces.
NAME_ALLOWED = frozenset(
    {
        POLICY_IMPLEMENTATION,
        "tests/unit/test_host_key_policy.py",
    }
)

#: Directories that hold Python sources this repository is responsible for.
SEARCH_ROOTS = ("clustrix", "tests", "scripts")

_SKIP_DIR_PARTS = frozenset({".git", "__pycache__", ".mypy_cache", ".pytest_cache"})


def _python_files(repo_root):
    for root in SEARCH_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if _SKIP_DIR_PARTS & set(path.parts):
                continue
            yield path


def _attribute_name(node):
    """The last component of a possibly-dotted name, or ``None``.

    ``paramiko.MissingHostKeyPolicy``, ``MissingHostKeyPolicy`` and
    ``pm.MissingHostKeyPolicy`` all reduce to the same string, so the guard
    cannot be sidestepped by changing how paramiko is imported.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _structural_violations(path, rel, text):
    """Policy installations and policy subclasses, found by parsing."""
    hits = []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - a broken file is a bug
        return [f"{rel}:{exc.lineno}: could not be parsed: {exc.msg}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _attribute_name(node.func) == INSTALL_CALL:
                hits.append(f"{rel}:{node.lineno}: calls {INSTALL_CALL}(...) directly")
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if _attribute_name(base) == POLICY_BASE:
                    hits.append(
                        f"{rel}:{node.lineno}: class {node.name} subclasses "
                        f"{POLICY_BASE}"
                    )
    return hits


def _name_violations(rel, text):
    """Accepting policy classes named in source, line by line."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name in sorted(ACCEPTING_POLICY_NAMES):
            if name in line:
                hits.append(f"{rel}:{lineno}: names {name}: {line.strip()}")
    return hits


def _violations(repo_root=REPO_ROOT):
    hits = []
    for path in _python_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel != POLICY_IMPLEMENTATION:
            hits.extend(_structural_violations(path, rel, text))
        if rel not in NAME_ALLOWED:
            hits.extend(_name_violations(rel, text))
    return sorted(hits)


def test_search_actually_finds_the_allowed_uses():
    """The scan must be able to see the legitimate uses.

    Without this, a broken glob or a wrong repo root would make the real
    check below pass vacuously -- a guard that can never fail is worse than
    no guard, because it reads as coverage.
    """
    named = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in _python_files(REPO_ROOT)
        if any(
            n in p.read_text(encoding="utf-8", errors="replace")
            for n in ACCEPTING_POLICY_NAMES
        )
    }
    assert named == set(NAME_ALLOWED), (
        "The allowlist and reality have drifted apart. Files naming an "
        f"accepting policy that the scan found: {sorted(named)}; allowlist: "
        f"{sorted(NAME_ALLOWED)}."
    )

    implementation = REPO_ROOT / POLICY_IMPLEMENTATION
    installs = _structural_violations(
        implementation,
        POLICY_IMPLEMENTATION,
        implementation.read_text(encoding="utf-8"),
    )
    assert installs, (
        f"{POLICY_IMPLEMENTATION} is supposed to be the one place that "
        f"calls {INSTALL_CALL} and defines a {POLICY_BASE}. The structural "
        "scan found neither there, so it is looking for the wrong thing."
    )


def test_no_host_key_policy_installed_outside_the_implementation():
    """Nothing outside ssh_security.py may install or define a policy."""
    hits = _violations()
    assert not hits, (
        f"Only {POLICY_IMPLEMENTATION} may call {INSTALL_CALL}, subclass "
        f"{POLICY_BASE}, or name an accepting policy class. Anything else "
        "risks trusting an unknown SSH host key, which is exactly the "
        "machine-in-the-middle hole clustrix/ssh_security.py closes. Call "
        "configure_host_key_policy(client, config) instead. Offending "
        "lines:\n  " + "\n  ".join(hits)
    )


#: The four ways a reviewer got an accepting policy past the old name-only
#: guard. Each really connected to a server whose host key was unknown.
BYPASSES = {
    "getattr": (
        "import paramiko\n"
        'policy = getattr(paramiko, "Auto" + "AddPolicy")\n'
        "client.set_missing_host_key_policy(policy())\n"
    ),
    "vars": (
        "import paramiko\n"
        'client.set_missing_host_key_policy(vars(paramiko)["Auto" "AddPolicy"]())\n'
    ),
    "subclass": (
        "import paramiko\n"
        "\n"
        "\n"
        "class AcceptEverything(paramiko.MissingHostKeyPolicy):\n"
        "    def missing_host_key(self, client, hostname, key):\n"
        "        return None\n"
        "\n"
        "\n"
        "client.set_missing_host_key_policy(AcceptEverything())\n"
    ),
    # Spelled from the assembled needles, so this module is not itself a
    # match for the name half of its own guard.
    "warning_policy": (
        "import paramiko\n"
        "client.set_missing_host_key_policy(paramiko."
        + sorted(ACCEPTING_POLICY_NAMES - {NEEDLE})[0]
        + "())\n"
    ),
    "plain": (
        "import paramiko\n"
        "client.set_missing_host_key_policy(paramiko." + NEEDLE + "())\n"
    ),
}


@pytest.mark.parametrize("name", sorted(BYPASSES))
def test_guard_catches_every_known_bypass(name, tmp_path):
    """Plant each real bypass on disk and prove the guard reports it.

    These are files, not strings handed to the matcher: the failure path is
    exercised against the same ``rglob``/parse pipeline the real check uses.
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "clustrix").mkdir(parents=True)
    # The implementation is exempt, so put a copy of the same code there too
    # and prove it is *not* reported. Otherwise the guard could be passing by
    # flagging everything.
    (fake_repo / "clustrix" / "ssh_security.py").write_text(BYPASSES[name])
    (fake_repo / "clustrix" / "sneaky_new_backend.py").write_text(BYPASSES[name])

    hits = _violations(fake_repo)
    offenders = {hit.split(":", 1)[0] for hit in hits}
    assert offenders == {"clustrix/sneaky_new_backend.py"}, (
        f"bypass {name!r} was not caught outside the implementation, or was "
        f"wrongly reported inside it: {hits}"
    )

    with pytest.raises(AssertionError):
        assert not hits, "planted violation must trip the same assertion"


def test_guard_is_quiet_about_the_approved_call(tmp_path):
    """A file doing the right thing must produce no hits.

    A guard that flags the sanctioned pattern would be trained away within a
    week, so this pins that the approved call site stays clean.
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "clustrix").mkdir(parents=True)
    (fake_repo / "clustrix" / "well_behaved_backend.py").write_text(
        "from clustrix.ssh_security import configure_host_key_policy\n"
        "\n"
        "\n"
        "def connect(client, config):\n"
        "    configure_host_key_policy(client, config)\n"
    )

    assert _violations(fake_repo) == []


# --------------------------------------------------------------------------
# The live half: the default really refuses an unknown key over a real socket.
# --------------------------------------------------------------------------


def _client_for(config):
    client = paramiko.SSHClient()
    configure_host_key_policy(client, config)
    return client


def test_default_policy_refuses_unknown_host_then_accepts_a_known_one(tmp_path):
    """The whole point, end to end, with no ``auto_add`` anywhere.

    Every SSH test converted away from mocks sets
    ``ssh_host_key_policy="auto_add"``, because the in-process server mints a
    fresh host key on every run and no known_hosts can predict it. That is
    reasonable per-test and disastrous in aggregate: it left the *default*
    -- the setting real users get -- with no coverage over a real socket at
    all. So this test takes the default, connects to the real server, and
    requires the refusal; then writes the server's actual key into a
    known_hosts file it controls and requires the same connection to
    succeed and run a real command.
    """
    root = tmp_path / "root"
    root.mkdir()
    # user_known_hosts_path() derives this from $HOME, and the autouse
    # isolate_home fixture has already pointed $HOME at a throwaway, so the
    # file below is this test's alone.
    known_hosts = user_known_hosts_path()
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    assert not known_hosts.exists()

    with LocalSSHServer(root=str(root), password="hunter2") as server:
        config = ClusterConfig(
            cluster_host=server.host,
            cluster_port=server.port,
            username="tester",
            password="hunter2",
        )
        # Not passed in: this is the shipped default, and that is the claim.
        assert config.ssh_host_key_policy == "reject"

        client = _client_for(config)
        with pytest.raises(HostKeyVerificationError) as exc:
            client.connect(
                server.host,
                port=server.port,
                username="tester",
                password="hunter2",
                look_for_keys=False,
                allow_agent=False,
            )
        client.close()
        assert "hunter2" not in str(exc.value)
        assert not known_hosts.exists(), (
            "the reject policy must not write the key it just refused; if it "
            "does, a second attempt would silently succeed"
        )

        # Now learn the keys the way ssh-keyscan would, and try again.
        entries = paramiko.HostKeys()
        for host_key in server.host_keys():
            entries.add(f"[{server.host}]:{server.port}", host_key.get_name(), host_key)
        entries.save(str(known_hosts))

        client = _client_for(config)
        client.connect(
            server.host,
            port=server.port,
            username="tester",
            password="hunter2",
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, _ = client.exec_command("echo verified-over-a-known-key")
        assert stdout.read().decode().strip() == "verified-over-a-known-key"
        client.close()
