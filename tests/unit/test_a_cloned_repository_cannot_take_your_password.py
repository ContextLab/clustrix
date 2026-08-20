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

import pytest

import clustrix.config as config_module
import clustrix.credential_manager as credential_manager_module
from clustrix.auth_methods import stored_credential_is_for_config
from clustrix.config import (
    CONFIG_SOURCE_EXPLICIT_FILE,
    CONFIG_SOURCE_RUNTIME,
    CONFIG_SOURCE_USER_CONFIG_DIR,
    CONFIG_SOURCE_WORKING_DIRECTORY,
    ClusterConfig,
    configure,
    get_config,
    get_config_dir,
    get_config_source,
    load_config,
)
from clustrix.executor_connections import ConnectionManager
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


def test_a_credential_that_names_this_host_is_used_from_anywhere(
    attacker_server, env_file, tmp_path, monkeypatch
):
    """``SSH_HOST`` in the credential file is the user authorising a host.

    Provenance only decides the case where the credential names no host. A
    credential that names one has already been told where it may go, so a
    working-directory config file naming that same host is not an escalation
    -- the user authorised it in a file only they can write.
    """
    env_file(SSH_HOST=attacker_server.host, SSH_PASSWORD=SENTINEL_PASSWORD)

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
