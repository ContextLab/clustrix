import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch
from clustrix.config import ClusterConfig, configure, get_config


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
