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
