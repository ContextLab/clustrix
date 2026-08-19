import copy
import os
import pathlib
import pytest
import tempfile
import shutil
from dataclasses import fields as dataclass_fields
from unittest.mock import Mock, patch
import clustrix.config as config_module
import clustrix.credential_manager as credential_manager_module
import clustrix.decorator as decorator_module
from clustrix.config import CONFIG_DIR_ENV_VAR, ClusterConfig, configure

_INTEGRATION_DIR = (pathlib.Path(__file__).parent / "integration").resolve()
_OPT_IN_VAR = "CLUSTRIX_ALLOW_BILLABLE"
_TRUTHY = {"1", "true", "yes", "on"}


def _billable_tests_enabled():
    return os.environ.get(_OPT_IN_VAR, "").strip().lower() in _TRUTHY


def _iter_candidate_targets(config):
    """Yield every path-ish string this run could end up collecting from.

    Reads `config.args` -- the *effective* target list -- rather than
    `config.invocation_params.args`, which is only what the operator literally
    typed. The difference is the whole ballgame, because there are three ways
    to aim pytest at a directory without typing its path:

        PYTEST_ADDOPTS=tests/integration/test_x.py pytest
        pytest -o testpaths=tests/integration/test_x.py
        (testpaths in pyproject.toml)

    All three land in `config.args` and none appear in `invocation_params.args`.
    Red-teaming confirmed the first two collected billable modules when the
    guard read the typed argv.

    Reading `config.args` is only safe because `testpaths` is `["tests"]`. If
    it ever names tests/integration again, a bare `pytest` will be refused --
    loudly and correctly, since testpaths would then be pointing every default
    run at billable tests.
    """
    for arg in config.args:
        yield str(arg).split("::")[0]
    # --pyargs addresses modules by dotted name, which never looks like a path.
    if getattr(config.option, "pyargs", False):
        for arg in config.args:
            yield str(arg).split("::")[0].replace(".", os.sep)
    # -p imports a plugin by dotted name, before collection begins.
    for plugin in getattr(config.option, "plugins", None) or []:
        yield str(plugin).replace(".", os.sep)


def _targets_integration_dir(candidate, config):
    """True if `candidate` names tests/integration under any sane resolution.

    Paths on the command line resolve against the invocation directory, while
    `testpaths` resolves against rootdir. Those differ whenever pytest is run
    from a subdirectory, and resolving against only one of them was a real
    hole: from `tests/`, `pytest integration/test_x.py` was not recognised.

    Both bases are tried. A guard protecting real money should over-match
    rather than under-match.
    """
    raw = pathlib.Path(candidate)
    if raw.is_absolute():
        attempts = [raw]
    else:
        invocation_dir = getattr(config.invocation_params, "dir", None)
        bases = [invocation_dir, pathlib.Path.cwd(), pathlib.Path(str(config.rootpath))]
        attempts = [pathlib.Path(str(base)) / raw for base in bases if base]
    for attempt in attempts:
        try:
            resolved = attempt.resolve()
        except OSError:  # pragma: no cover - defensive, unresolvable path
            continue
        if resolved == _INTEGRATION_DIR or _INTEGRATION_DIR in resolved.parents:
            return True
    return False


def pytest_configure(config):
    """Refuse to start when a run explicitly targets tests/integration.

    See issue #109. `tests/integration/conftest.py` sets `collect_ignore_glob`,
    which keeps the directory out of ordinary runs -- but that only filters
    *directory traversal*. A path named explicitly as a pytest argument is not
    filtered by it, so

        pytest tests/integration/test_timeout_mechanism.py

    collected and imported the module anyway. Those modules reach real cloud
    APIs, so the check has to happen before collection begins.

    This runs at configure time, which is before any test module is imported.

    Deliberately scoped to *explicit* targeting: a plain `pytest tests/` must
    keep working and silently skip the directory, while someone who asked for
    these tests by name gets told why they got nothing, rather than an
    inscrutable empty run.

    Target discovery is in `_iter_candidate_targets` and path matching is in
    `_targets_integration_dir`; both carry the reasoning for why they look
    where they do. In short: read the *effective* target list, and resolve
    relative paths against every plausible base.
    """
    # Registered here rather than in pyproject.toml because the suite that
    # uses it (tests/real_world) is routinely excluded from a run, and
    # --strict-markers needs the name declared in *every* run for
    # tests/unit/test_pytest_config.py to see it.
    config.addinivalue_line(
        "markers",
        "cluster_network: mark test as requiring network access to a private "
        "test cluster named by CLUSTRIX_TEST_*_HOST",
    )

    if _billable_tests_enabled():
        return
    for candidate in _iter_candidate_targets(config):
        if _targets_integration_dir(candidate, config):
            raise pytest.UsageError(
                f"Refusing to run {candidate!r}: tests/integration provisions "
                f"real, billable cloud resources (AWS EKS/EC2). Set "
                f"{_OPT_IN_VAR}=1 to run them deliberately, e.g.\n"
                f"    {_OPT_IN_VAR}=1 pytest {candidate}"
            )


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_config():
    """Create a mock configuration for testing."""
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="test.cluster.com",
        username="testuser",
        key_file="~/.ssh/test_key",
        default_cores=4,
        default_memory="8GB",
        default_time="01:00:00",
        remote_work_dir="/tmp/test_clustrix",
        cleanup_on_success=True,
    )
    return config


@pytest.fixture
def mock_ssh_client():
    """Create a mock SSH client."""
    with patch("paramiko.SSHClient") as mock_client:
        mock_instance = Mock()
        mock_client.return_value = mock_instance

        # Mock exec_command
        mock_stdout = Mock()
        mock_stdout.read.return_value = b"Success"
        mock_stdout.channel.recv_exit_status.return_value = 0

        mock_stderr = Mock()
        mock_stderr.read.return_value = b""

        mock_instance.exec_command.return_value = (None, mock_stdout, mock_stderr)

        # Mock SFTP
        mock_sftp = Mock()
        mock_instance.open_sftp.return_value = mock_sftp

        yield mock_instance


@pytest.fixture
def sample_function():
    """Sample function for testing."""

    def test_func(x, y):
        return x + y

    return test_func


@pytest.fixture
def sample_loop_function():
    """Sample function with loop for testing parallelization."""

    def loop_func(data):
        results = []
        for item in data:
            results.append(item * 2)
        return results

    return loop_func


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
def isolate_config_dir():
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
    """
    with tempfile.TemporaryDirectory(prefix="clustrix-test-config-") as tmp:
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
