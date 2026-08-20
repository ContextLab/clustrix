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

import copy
import json
import os
import pathlib
import subprocess
import sys
import textwrap
import warnings

import pytest

import clustrix
import clustrix.config as config_module
import clustrix.credential_manager as credential_manager_module
from clustrix.auth_methods import stored_credential_is_for_config
from clustrix.config import (
    CONFIG_SOURCES_KEY,
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
    ``_hostname_matches`` documents -- so both ends go through the one
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
    """
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
    assert config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES == {}, (
        "a file that named a different host condemned the hostname the user " "typed"
    )
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
