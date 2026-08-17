import os
import pathlib
import pytest
import tempfile
import shutil
from unittest.mock import Mock, patch
from clustrix.config import ClusterConfig, configure

_INTEGRATION_DIR = (pathlib.Path(__file__).parent / "integration").resolve()
_OPT_IN_VAR = "CLUSTRIX_ALLOW_BILLABLE"
_TRUTHY = {"1", "true", "yes", "on"}


def _billable_tests_enabled():
    return os.environ.get(_OPT_IN_VAR, "").strip().lower() in _TRUTHY


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
    """
    if _billable_tests_enabled():
        return
    for arg in config.args:
        # strip pytest's "::TestClass::test_name" node-id suffix
        candidate = pathlib.Path(str(arg).split("::")[0])
        if not candidate.is_absolute():
            candidate = (pathlib.Path(str(config.rootpath)) / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate == _INTEGRATION_DIR or _INTEGRATION_DIR in candidate.parents:
            raise pytest.UsageError(
                f"Refusing to run {arg!r}: tests/integration provisions real, "
                f"billable cloud resources (AWS EKS/EC2). Set {_OPT_IN_VAR}=1 to "
                f"run them deliberately, e.g.\n"
                f"    {_OPT_IN_VAR}=1 pytest {arg}"
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
def reset_config():
    """Reset configuration after each test."""
    yield
    # Reset to default config
    configure(
        cluster_type="slurm",
        cluster_host=None,
        username=None,
        password=None,
        key_file=None,
        default_cores=4,
        default_memory="8GB",
        default_time="01:00:00",
    )
