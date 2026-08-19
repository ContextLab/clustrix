"""
Configuration and fixtures for real-world tests.
"""

import os
import pytest
from pathlib import Path
import tempfile
import functools
import socket
import threading
import time

from tests.real_world import RealWorldTestManager, TestCredentials, TempResourceManager
from tests.real_world.credential_manager import (
    HOST_ENV_VARS,
    configured_test_hosts,
)

# Create global test manager instance
test_manager = RealWorldTestManager()

_THIS_DIR = Path(__file__).parent.resolve()


#: Whole-detection budget, shared across every host probed. This only gates
#: which tests run, so it must answer quickly and wrongly-but-safely rather
#: than slowly and exactly. Off-network CI runners blackhole the lookups
#: below: on a macOS runner each call took about seventy seconds, and three
#: calls exhausted the job's fifteen-minute budget before the suite could
#: finish.
NETWORK_DETECTION_TIMEOUT = 3.0

#: Named in skip reasons so a reader knows what to set to make these run.
CLUSTER_HOST_ENV_VARS = ", ".join(names[0] for names in HOST_ENV_VARS.values())


def _within(seconds, func, *args):
    """Run `func`, giving up if it takes longer than `seconds`.

    The resolver calls here are not interruptible, so the worker thread is left
    to finish on its own; it is a daemon and holds nothing the caller needs.
    """
    # A plain daemon thread, not a ThreadPoolExecutor. The executor's workers
    # are non-daemon, and `concurrent.futures` joins every one of them --
    # untimed -- on the way out of the interpreter, even after
    # `shutdown(wait=False)`. Abandoning a seventy-second lookup that way only
    # moved the wait from here to process exit. A daemon thread is genuinely
    # abandonable: nothing joins it and the interpreter does not wait for it.
    outcome = {}

    def call():
        try:
            outcome["value"] = func(*args)
        except BaseException as exc:  # re-raised below, in the caller's thread
            outcome["error"] = exc

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(seconds)

    error = outcome.get("error")
    if error is not None:
        # Unresolvable names are the expected off-network answer, not a fault.
        if isinstance(error, OSError):
            return None
        raise error
    # Absent on timeout, because the worker never got as far as storing one.
    return outcome.get("value")


@functools.lru_cache(maxsize=1)
def can_reach_configured_cluster():
    """Whether the configured private test clusters are reachable from here.

    The hosts come from the CLUSTRIX_TEST_*_HOST environment variables (see
    `credential_manager`), so this repository names no machine of its own.
    With none of them set there is nothing to reach and the answer is False,
    which is also the fast path: no lookup is attempted at all.

    Cached: this is consulted repeatedly to decide whether to skip, and the
    answer cannot change usefully within one test run.
    """
    hosts = configured_test_hosts()
    if not hosts:
        return False

    # One budget for the whole detection, not one per host: the lookups
    # blackhole rather than fail off-network, so N hosts must not cost N
    # timeouts.
    deadline = time.monotonic() + NETWORK_DETECTION_TIMEOUT
    for host in hosts:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if _within(remaining, socket.gethostbyname, host):
            return True
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
def require_cluster_network():
    """
    Fixture that skips the test unless a configured cluster is reachable.

    Usage:
        def test_something(require_cluster_network):
            # Skipped unless CLUSTRIX_TEST_*_HOST names a reachable machine
            pass
    """
    if not can_reach_configured_cluster():
        pytest.skip(
            "No reachable private test cluster: set one of "
            f"{CLUSTER_HOST_ENV_VARS} and connect to its network (VPN or "
            "on-site)"
        )


@pytest.fixture
def gpu_cluster_credentials(test_credentials, require_cluster_network):
    """SSH-GPU cluster credentials (requires the cluster network)."""
    creds = test_credentials.get_gpu_cluster_credentials()
    if not creds:
        pytest.skip("SSH-GPU cluster credentials not available")
    return creds


@pytest.fixture
def slurm_cluster_credentials(test_credentials, require_cluster_network):
    """SSH-SLURM cluster credentials (requires the cluster network)."""
    creds = test_credentials.get_slurm_cluster_credentials()
    if not creds:
        pytest.skip("SSH-SLURM cluster credentials not available")
    return creds


@pytest.fixture
def screenshots_dir(tmp_path_factory):
    """Directory for screenshot outputs.

    A tmp directory, not `tests/real_world/screenshots`. Writing generated
    artefacts back into the source tree meant every test run left the working
    copy dirty, so a `git status` after running the suite could not be read at
    a glance -- and the checked-in copies drifted with whoever ran it last.
    Set CLUSTRIX_SCREENSHOT_DIR to keep them somewhere durable for inspection.
    """
    override = os.environ.get("CLUSTRIX_SCREENSHOT_DIR")
    directory = Path(override) if override else tmp_path_factory.mktemp("screenshots")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


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


def pytest_collection_modifyitems(config, items):
    """Modify test collection for real-world tests.

    Every item collected from this directory is forced to carry the
    `real_world` marker, regardless of whether the test file itself applies
    `@pytest.mark.real_world`. Before this, `-m "not real_world"` (the
    documented CI-safe command) silently collected and ran any file under
    `tests/real_world/` that forgot the decorator -- making real SSH
    connections and cloud API calls. Location under this directory is now
    sufficient by itself; a developer adding a new file here cannot forget
    the marker and accidentally leak it into the "safe" test run. See
    issue #109/#114.

    This hook is registered by this conftest.py, but pytest calls it once
    per session with *every* collected item, not just the ones under this
    directory -- so the path check below is essential. Without it, a run
    like `pytest tests/` (which loads this conftest because it traverses
    into tests/real_world/) would mark the entire test suite as
    `real_world` and `-m "not real_world"` would deselect everything.
    """
    real_world_marker = pytest.mark.real_world
    for item in items:
        try:
            item_path = Path(str(item.fspath)).resolve()
        except Exception:  # pragma: no cover - defensive, path may be virtual
            continue
        if item_path == _THIS_DIR or _THIS_DIR in item_path.parents:
            item.add_marker(real_world_marker)

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

    # Skip private-cluster tests unless a configured cluster is reachable
    if not can_reach_configured_cluster():
        skip_cluster_network = pytest.mark.skip(
            reason=(
                "No reachable private test cluster: set one of "
                f"{CLUSTER_HOST_ENV_VARS} and connect to its network "
                "(VPN or on-site)"
            )
        )
        for item in items:
            if "cluster_network" in item.keywords:
                item.add_marker(skip_cluster_network)
            # Also skip tests named after the GPU / SLURM cluster roles
            if any(
                keyword in item.name.lower()
                for keyword in ["gpu_cluster", "slurm_cluster"]
            ):
                item.add_marker(skip_cluster_network)


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
    # Screenshots and scratch files go to a temp directory; see screenshots_dir
    # for why the source tree is not a good place for generated artefacts.
    temp_dir = Path(tempfile.gettempdir()) / "clustrix-real-world"
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
