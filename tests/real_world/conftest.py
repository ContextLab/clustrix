"""
Configuration and fixtures for real-world tests.
"""

import os
import pytest
from pathlib import Path
import tempfile
import socket
import subprocess

from tests.real_world import RealWorldTestManager, TestCredentials, TempResourceManager


# Create global test manager instance
test_manager = RealWorldTestManager()


def is_dartmouth_network():
    """
    Check if we're on Dartmouth network (on campus or VPN).
    
    Returns:
        bool: True if on Dartmouth network, False otherwise
    """
    try:
        # Method 1: Check hostname
        hostname = socket.getfqdn()
        if '.dartmouth.edu' in hostname:
            return True
        
        # Method 2: Try to resolve a Dartmouth-specific host
        try:
            socket.gethostbyname('tensor01.dartmouth.edu')
            return True
        except socket.gaierror:
            pass
        
        # Method 3: Check if we can ping tensor01 (VPN test)
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '3000', 'tensor01.dartmouth.edu'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        return False
        
    except Exception:
        return False


@pytest.fixture(scope="session")
def real_world_test_manager():
    """Global test manager for real-world tests."""
    return test_manager


@pytest.fixture(scope="session")
def test_credentials():
    """Test credentials manager."""
    return TestCredentials()


@pytest.fixture
def temp_resource_manager():
    """Create temporary resource manager for test."""
    with TempResourceManager() as manager:
        yield manager


@pytest.fixture
def aws_credentials(test_credentials):
    """AWS credentials for testing."""
    creds = test_credentials.get_aws_credentials()
    if not creds:
        pytest.skip("AWS credentials not available")
    return creds


@pytest.fixture
def azure_credentials(test_credentials):
    """Azure credentials for testing."""
    creds = test_credentials.get_azure_credentials()
    if not creds:
        pytest.skip("Azure credentials not available")
    return creds


@pytest.fixture
def gcp_credentials(test_credentials):
    """GCP credentials for testing."""
    creds = test_credentials.get_gcp_credentials()
    if not creds:
        pytest.skip("GCP credentials not available")
    return creds


@pytest.fixture
def ssh_credentials(test_credentials):
    """SSH credentials for testing."""
    creds = test_credentials.get_ssh_credentials()
    if not creds:
        pytest.skip("SSH credentials not available")
    return creds


@pytest.fixture
def require_dartmouth_network():
    """
    Fixture that skips test if not on Dartmouth network.
    
    Usage:
        def test_something(require_dartmouth_network):
            # Test will be skipped if not on Dartmouth VPN
            pass
    """
    if not is_dartmouth_network():
        pytest.skip("Requires Dartmouth network access (VPN or on-campus)")


@pytest.fixture
def tensor01_credentials(test_credentials, require_dartmouth_network):
    """tensor01 credentials (requires Dartmouth network)."""
    creds = test_credentials.get_tensor01_credentials()
    if not creds:
        pytest.skip("tensor01 credentials not available")
    return creds


@pytest.fixture
def ndoli_credentials(test_credentials, require_dartmouth_network):
    """ndoli credentials (requires Dartmouth network).""" 
    creds = test_credentials.get_ndoli_credentials()
    if not creds:
        pytest.skip("ndoli credentials not available")
    return creds


@pytest.fixture
def screenshots_dir():
    """Directory for screenshot outputs."""
    screenshots_dir = Path("tests/real_world/screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    return screenshots_dir


@pytest.fixture
def api_rate_limit_check(real_world_test_manager):
    """Check API rate limits before test."""

    def check_limit(cost=0.01):
        if not real_world_test_manager.can_make_api_call(cost):
            pytest.skip("API rate limit or cost limit reached")
        return True

    return check_limit


@pytest.fixture
def record_api_call(real_world_test_manager):
    """Record API call after test."""

    def record(cost=0.01):
        real_world_test_manager.record_api_call(cost)

    return record


def pytest_configure(config):
    """Configure pytest for real-world tests."""
    # Add custom markers
    config.addinivalue_line(
        "markers", "real_world: mark test as using real external resources"
    )
    config.addinivalue_line(
        "markers", "expensive: mark test as potentially expensive (API costs)"
    )
    config.addinivalue_line(
        "markers", "visual: mark test as requiring visual verification"
    )
    config.addinivalue_line(
        "markers", "ssh_required: mark test as requiring SSH access"
    )
    config.addinivalue_line(
        "markers", "aws_required: mark test as requiring AWS credentials"
    )
    config.addinivalue_line(
        "markers", "azure_required: mark test as requiring Azure credentials"
    )
    config.addinivalue_line(
        "markers", "gcp_required: mark test as requiring GCP credentials"
    )
    config.addinivalue_line(
        "markers", "dartmouth_network: mark test as requiring Dartmouth network access (VPN or on-campus)"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection for real-world tests."""
    # Skip expensive tests by default unless explicitly requested
    if not config.getoption("--run-expensive"):
        skip_expensive = pytest.mark.skip(
            reason="Expensive tests skipped (use --run-expensive)"
        )
        for item in items:
            if "expensive" in item.keywords:
                item.add_marker(skip_expensive)

    # Skip visual tests unless requested
    if not config.getoption("--run-visual"):
        skip_visual = pytest.mark.skip(reason="Visual tests skipped (use --run-visual)")
        for item in items:
            if "visual" in item.keywords:
                item.add_marker(skip_visual)
    
    # Skip Dartmouth network tests if not on Dartmouth network
    if not is_dartmouth_network():
        skip_dartmouth = pytest.mark.skip(
            reason="Dartmouth network tests skipped (requires VPN or on-campus access)"
        )
        for item in items:
            if "dartmouth_network" in item.keywords:
                item.add_marker(skip_dartmouth)
            # Also skip specific tensor01 and ndoli tests by name
            if any(keyword in item.name.lower() for keyword in ["tensor01", "ndoli"]):
                item.add_marker(skip_dartmouth)


def pytest_addoption(parser):
    """Add command line options for real-world tests."""
    parser.addoption(
        "--run-expensive",
        action="store_true",
        default=False,
        help="Run expensive tests that may incur API costs",
    )
    parser.addoption(
        "--run-visual",
        action="store_true",
        default=False,
        help="Run visual tests that require manual verification",
    )
    parser.addoption(
        "--api-cost-limit",
        type=float,
        default=5.0,
        help="Maximum API cost limit in USD (default: 5.0)",
    )
    parser.addoption(
        "--api-call-limit",
        type=int,
        default=100,
        help="Maximum number of API calls per session (default: 100)",
    )


@pytest.fixture(scope="session", autouse=True)
def configure_test_limits(request):
    """Configure test limits from command line options."""
    cost_limit = request.config.getoption("--api-cost-limit")
    call_limit = request.config.getoption("--api-call-limit")

    test_manager.cost_limit_usd = cost_limit
    test_manager.daily_limit = call_limit

    return test_manager


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment."""
    # Create screenshots directory
    screenshots_dir = Path("tests/real_world/screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # Create temporary files directory
    temp_dir = Path("tests/real_world/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    yield

    # Cleanup is handled by individual test managers


@pytest.fixture
def cluster_config_template():
    """Template for cluster configuration."""
    from clustrix.config import ClusterConfig

    return {
        "local": ClusterConfig(cluster_type="local"),
        "slurm": ClusterConfig(
            cluster_type="slurm",
            cluster_host="localhost",
            username=os.getenv("USER", "testuser"),
            default_cores=1,
            default_memory="1GB",
            default_time="00:30:00",
        ),
        "ssh": ClusterConfig(
            cluster_type="ssh",
            cluster_host="localhost",
            username=os.getenv("USER", "testuser"),
            default_cores=1,
            default_memory="1GB",
        ),
    }


@pytest.fixture
def mock_cluster_responses():
    """Mock responses for cluster operations."""
    return {
        "job_submit": {
            "job_id": "12345",
            "status": "submitted",
            "message": "Job submitted successfully",
        },
        "job_status": {"job_id": "12345", "status": "completed", "exit_code": 0},
        "job_output": {
            "stdout": "Task completed successfully",
            "stderr": "",
            "result": {"value": 42},
        },
    }
