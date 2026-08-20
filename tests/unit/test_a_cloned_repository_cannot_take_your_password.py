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

import pathlib
import warnings

import pytest

import clustrix.config as config_module
import clustrix.credential_manager as credential_manager_module
from clustrix.auth_methods import stored_credential_is_for_config
from clustrix.credential_release import CredentialRelease, CredentialTarget
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

    store = get_config_dir() / "profiles" / "profiles.yml"
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(_profile_bundle(attacker_server), encoding="utf-8")

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

    store = get_config_dir() / "profiles.yml"
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.write_text(_profile_bundle(attacker_server), encoding="utf-8")
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
            provenance=CONFIG_SOURCE_RUNTIME,
            described_as="a test",
        )


def test_a_target_must_name_a_provenance_that_exists():
    """A typo may not invent a source that is neither trusted nor untrusted."""
    with pytest.raises(ValueError):
        CredentialTarget(
            hostname="hpc.example.edu",
            username="victim",
            provenance="totally-fine-honest",
            described_as="a test",
        )


def test_a_target_may_name_no_username():
    """Not every provider has one, and the documented ``.env`` names none."""
    target = CredentialTarget(
        hostname="hpc.example.edu",
        username="",
        provenance=CONFIG_SOURCE_RUNTIME,
        described_as="a test",
    )
    assert target.username == ""


def _a_target():
    return CredentialTarget(
        hostname="hpc.example.edu",
        username="victim",
        provenance=CONFIG_SOURCE_RUNTIME,
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


def test_a_release_is_truthy_exactly_when_it_carries_a_secret():
    assert CredentialRelease(
        target=_a_target(), method="stored-credential", password=SENTINEL_PASSWORD
    )
    assert not CredentialRelease(target=_a_target(), refusal="not for you")


def test_a_target_built_from_a_config_carries_that_config_s_provenance(tmp_path):
    """Provenance is a fact about the hostname, and the target records it."""
    config = ClusterConfig(cluster_host="hpc.example.edu", username="victim")
    target = CredentialTarget.for_config(config)

    assert target.hostname == "hpc.example.edu"
    assert target.username == "victim"
    assert target.provenance == CONFIG_SOURCE_RUNTIME
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


def test_a_target_cannot_be_built_without_saying_where_the_host_came_from():
    """Provenance is required, and there is no "unknown" that reads as safe.

    The gate decides with two facts: who receives the secret, and who chose
    them. It is *given* the second one -- it cannot recompute a fact that
    was destroyed upstream, which is what route 8 (the profile store writing
    a config without its provenance) does. So the least it can do is refuse
    to answer at all when the caller has not established it.
    """
    with pytest.raises(TypeError):
        CredentialTarget(  # type: ignore[call-arg]
            hostname="hpc.example.edu", username="victim", described_as="a test"
        )


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

    assert target.provenance == CONFIG_SOURCE_WORKING_DIRECTORY


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
