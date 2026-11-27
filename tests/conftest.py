import copy
import os
import pathlib
import pytest
import tempfile
import shutil
from dataclasses import fields as dataclass_fields
import clustrix.config as config_module
import clustrix.credential_manager as credential_manager_module
import clustrix.decorator as decorator_module
from clustrix.config import CONFIG_DIR_ENV_VAR


def pytest_configure(config):
    """Declare the marker the private-cluster tests are skipped by."""
    # Registered here rather than in pyproject.toml because the suite that
    # uses it (tests/real_world) is routinely excluded from a run, and
    # --strict-markers needs the name declared in *every* run for
    # tests/unit/test_pytest_config.py to see it.
    config.addinivalue_line(
        "markers",
        "cluster_network: mark test as requiring network access to a private "
        "test cluster named by CLUSTRIX_TEST_*_HOST",
    )


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture(autouse=True)
def isolate_home():
    """Give every test its own ``$HOME``, so none can touch the real ~/.ssh.

    ``ssh_host_key_policy="auto_add"`` makes paramiko *write* the host key it
    just accepted into ``~/.ssh/known_hosts``. The in-process test server in
    ``tests/ssh_server.py`` generates a fresh key per run and binds a new
    ephemeral port per test, so every one of those writes is a new line that
    will never match anything again.

    Measured on a developer machine before this fixture existed: 1,191 of the
    1,223 entries in the real known_hosts were loopback junk from test runs,
    leaving 32 genuine ones. Two runs at once interleave their appends and
    corrupt the file, after which unrelated tests fail with ``InvalidHostKey``
    -- and so does the developer's own ssh.

    Individual test files had begun growing their own ``isolated_home``
    fixtures. One autouse fixture here covers every test that exists and
    every test anyone writes later, which is the only version of this that
    stays true.

    ``USERPROFILE`` is set alongside ``HOME`` because that is what
    ``Path.home()`` reads on Windows.
    """
    with tempfile.TemporaryDirectory(prefix="clustrix-test-home-") as tmp:
        ssh_dir = os.path.join(tmp, ".ssh")
        os.makedirs(ssh_dir, exist_ok=True)
        os.chmod(ssh_dir, 0o700)

        saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = tmp
        os.environ["USERPROFILE"] = tmp
        try:
            yield tmp
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@pytest.fixture(autouse=True)
def isolate_config_dir(isolate_home):
    """Point clustrix's config directory at a throwaway for the whole run.

    Function-scoped, not session-scoped. A single directory shared by the
    whole run is enough to stop the suite writing into the developer's real
    ~/.clustrix, but it still lets tests leak to each other through it: a
    profiles.yml written by one test changed which profile the notebook
    widget considered active in a later one, so eleven widget tests passed
    alone and failed in a full run.

    Without this the suite writes into whoever is running it. Observed on a
    developer machine: an `integration_test` profile appended to the real
    ~/.clustrix/clustrix.yml, plus test.yml and test_all_configs.yml beside
    it, and a stray test_config.yml dropped into the repository root. Tests
    that chdir into a tmpdir do not help, because the save path is derived
    from the config directory rather than the working directory.

    It is ``$HOME/.clustrix`` inside the isolated home rather than a
    directory of its own, because that is where a real user's configuration
    directory is, and clustrix now tells the two apart: a config directory
    named by ``CLUSTRIX_CONFIG_DIR`` *somewhere else* is not trusted to
    choose which host receives a stored credential (see
    ``clustrix.config.CONFIG_SOURCE_REDIRECTED_CONFIG_DIR``). Pointing the
    variable at an unrelated tmpdir made every test run in a configuration
    no real user is in.
    """
    tmp = str(pathlib.Path(isolate_home) / ".clustrix")
    os.makedirs(tmp, mode=0o700, exist_ok=True)
    previous = os.environ.get(CONFIG_DIR_ENV_VAR)
    os.environ[CONFIG_DIR_ENV_VAR] = tmp
    try:
        yield tmp
    finally:
        if previous is None:
            os.environ.pop(CONFIG_DIR_ENV_VAR, None)
        else:
            os.environ[CONFIG_DIR_ENV_VAR] = previous


@pytest.fixture(autouse=True)
def reset_config():
    """Restore the global configuration singleton after every test.

    This used to reset eight hand-listed fields. Everything else a test set
    -- remote_work_dir, package_manager, environment_variables,
    ssh_host_key_policy -- leaked into every test that ran afterwards, and
    ClusterConfig has over a hundred fields. The notebook widget reads the
    live config to populate itself, so it inherited whatever the previous
    test happened to leave behind: eleven widget tests passed on their own
    and failed in a full run, purely on ordering.

    Snapshotting every field by name means a newly added field is covered
    automatically, rather than silently joining the set of things that leak.
    The same object is restored in place, so anything holding a reference to
    the singleton sees the restored values.
    """
    config_object = config_module._config
    before = {
        field_def.name: copy.deepcopy(getattr(config_object, field_def.name))
        for field_def in dataclass_fields(config_object)
    }
    yield
    # Two things have to be undone, because there are two ways to change the
    # configuration: mutating the singleton's fields, and rebinding the module
    # attribute to a different ClusterConfig entirely (monkeypatch.setattr on
    # clustrix.config._config, or load_config() building a fresh one). Restoring
    # only the fields left the module pointing at the test's object; restoring
    # only the binding left a mutated object in place.
    config_module._config = config_object
    for name, value in before.items():
        setattr(config_object, name, value)

    # The record of which hostnames an untrusted configuration file has
    # named is process-global and deliberately append-only -- a public "this
    # host is fine now" call would be the laundering route it exists to
    # close. It is still per-*process* state that one test can leave behind
    # for another, exactly like the singleton above, so the fixture that
    # already undoes process state reaches in and clears it. There is no
    # production caller of this and there must not be one.
    config_module._HOSTS_NAMED_BY_UNTRUSTED_SOURCES.clear()

    # Lazily-created module singletons cache the config directory at the moment
    # they are first constructed. With a per-test config directory, one built
    # during an earlier test hands a stale path to every test after it. Any new
    # singleton of this shape belongs in this list.
    credential_manager_module._credential_manager = None
    # The decorator caches one async executor per process so that async
    # submissions reuse a thread pool instead of building one per call.
    # Across tests that cache is shared state like any other: leaving it
    # set means a later test gets the executor an earlier one created,
    # including one built from a patched class.
    decorator_module._ASYNC_EXECUTOR = None
