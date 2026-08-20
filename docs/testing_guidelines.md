# Clustrix Testing Guidelines

## Table of Contents
1. [Testing Philosophy](#testing-philosophy)
2. [Test Categories](#test-categories)
3. [Writing Tests](#writing-tests)
4. [Running Tests](#running-tests)
5. [CI/CD Pipeline](#cicd-pipeline)
6. [Best Practices](#best-practices)
7. [Troubleshooting](#troubleshooting)

## Testing Philosophy

### The Mocking Policy

Clustrix does **not** follow a strict "no mocks ever" rule -- 19 of the
project's 152 `test_*.py` modules import `unittest.mock`, and pretending
otherwise would just make this document wrong. Getting that number to zero is
issue [#117](https://github.com/ContextLab/clustrix/issues/117). The policy in
the meantime:

1. **Real first, always.** A capability may not be marked working until it
   has been exercised against the real thing -- a real cluster, a real API,
   a real file on disk, a real socket. A test that has only ever passed
   against a mock is evidence of nothing.
2. **Mocks are a cost-control measure, never a correctness argument.** Once
   a real call has verified the contract, a mocked test using the *same*
   call syntax may stand in for it in CI to avoid per-run API fees and
   credential requirements. Re-verify against the real service when the
   contract could have changed.
3. **A mock may never be a fallback.** If real functionality is
   unavailable, the test must fail or raise. Silently substituting a mock
   turns a broken feature into a green test.
4. **Production code must never know it is being tested.** No
   `isinstance(x, Mock)`, no test-only branches, no importable module of
   fake widgets.
5. **Never weaken a test to make it pass.** If a test fails, fix the code.
   If the test itself asserts wrong behaviour, say so explicitly and
   rewrite the assertion -- do not quietly relax it.

### Why Real Tests Come First

1. **Catch Real Issues**: Mocks hide serialization problems, network issues, and integration failures
2. **Validate User Experience**: Tests mirror exactly how users interact with Clustrix
3. **Build Confidence**: Real tests provide confidence in production deployments
4. **Document Usage**: Tests serve as working examples of how to use Clustrix

## Test Categories

### 1. Unit Tests (Local Execution)
Tests that validate individual functions and classes using local execution.

```python
def test_local_execution():
    """Test function execution locally."""
    configure(cluster_type="local")

    @cluster(cores=2, memory="4GB")
    def process_data(data):
        import numpy as np
        return np.mean(data)

    result = process_data([1, 2, 3, 4, 5])
    assert result == 3.0
```

The backend is chosen by `configure`, not by the decorator: `cluster_type` is
not a `@cluster` keyword and passing it there is ignored with a warning. On
the `local` backend `cores=2` is also inert — the function runs in the
caller's own process, sequentially (issue
[#152](https://github.com/ContextLab/clustrix/issues/152)). For actual
in-process parallelism, use `clustrix.local_executor.LocalExecutor` directly.

### 2. Integration Tests
Tests that validate interactions between components using real infrastructure.

```python
@pytest.mark.real_world
def test_huggingface_integration():
    """Test HuggingFace Jobs submission."""
    configure(
        cluster_type="huggingface",
        hf_namespace="contextlab",
    )

    @cluster(cores=2, memory="2GB")
    def hf_task():
        import socket
        return socket.gethostname()

    result = hf_task()
    assert isinstance(result, str) and result
```

### 3. Edge Case Tests
Tests that validate behavior in unusual or boundary conditions.

An edge-case test has to assert what the code actually does. `@cluster` does
not validate `cores`, so `cores=0` decorates and runs without complaint; a
test asserting `pytest.raises(ValueError)` there fails. What *is* validated is
`cluster_type`:

```python
def test_unsupported_backend_rejected():
    """An unimplemented backend is rejected by name, with its issue number."""
    with pytest.raises(ValueError, match="#140"):
        configure(cluster_type="pbs")
```

### 4. Performance Tests
Tests that measure and validate performance characteristics.

```python
def test_local_dispatch_overhead():
    """The local backend adds negligible overhead to a trivial call."""
    configure(cluster_type="local")

    @cluster(cores=1, memory="1GB")
    def quick_task():
        return "done"

    start = time.perf_counter()
    result = quick_task()
    elapsed = time.perf_counter() - start

    assert result == "done"
    assert elapsed < 1.0
```

Mark anything that measures a remote round trip with `@pytest.mark.slow` and
`@pytest.mark.real_world`; wall-clock assertions against a shared scheduler are
assertions about that scheduler's queue, not about clustrix.

### 5. Failure Recovery Tests
Tests that validate error handling and recovery mechanisms.

There is no retry setting on `@cluster` and no `connection_retry_*` field on
`ClusterConfig`. `@cluster` accepts `cores`, `memory`, `time`, `partition`,
`queue`, `parallel`, `auto_gpu_parallel`, `environment` and `async_submit`,
plus the pass-through extras `hf_token`, `hf_username`, `hf_flavor`,
`hf_timeout`, `hf_namespace` and `key_file`; anything else is logged as
unrecognised and dropped. So a recovery test drives the failure itself:

```python
def test_unreachable_host_raises():
    """A host that cannot be reached fails loudly rather than hanging."""
    configure(
        cluster_type="ssh",
        cluster_host="unreachable.invalid",
        username="user",
    )

    @cluster(cores=2, memory="2GB")
    def task():
        return "should not execute"

    with pytest.raises(Exception):
        task()
```

## Writing Tests

### Test Structure Template

```python
"""
Real-world test for [component name].

This test validates [what it validates] using actual infrastructure
without mocks.
"""

import pytest
from clustrix import cluster, configure
from clustrix.config import ClusterConfig


class TestComponentReal:
    """Test [component] with real infrastructure."""
    
    @pytest.fixture
    def test_config(self):
        """Create test configuration."""
        config = ClusterConfig()
        config.cluster_type = "local"
        # Configure as needed
        return config
    
    def test_user_workflow(self, test_config):
        """
        Test [specific workflow] as users would use it.
        
        This demonstrates:
        - [Key aspect 1]
        - [Key aspect 2]
        - [Key aspect 3]
        """
        # Step 1: Configuration. configure() takes keyword arguments only --
        # passing a ClusterConfig positionally is a TypeError.
        configure(**vars(test_config))
        
        # Step 2: Define function (realistic user code)
        @cluster(cores=2, memory="4GB")
        def user_function(data):
            """Realistic computation."""
            # Real imports
            import numpy as np
            import pandas as pd
            
            # Real computation
            df = pd.DataFrame(data)
            result = df.describe().to_dict()
            
            return result
        
        # Step 3: Execute (normal function call)
        test_data = {"col1": [1, 2, 3], "col2": [4, 5, 6]}
        result = user_function(test_data)
        
        # Step 4: Validate (check actual results)
        assert "col1" in result
        assert result["col1"]["mean"] == 2.0
```

### Required Test Elements

Every test must include:

1. **Docstring**: Explain what the test validates
2. **Real Configuration**: Use actual ClusterConfig
3. **Meaningful Computation**: Perform real work, not trivial operations
4. **Result Validation**: Check actual computation results
5. **Error Handling**: Handle and validate real errors

### Marking Tests

Use pytest markers to categorize tests:

`--strict-markers` is enabled, so an unregistered marker is a hard collection
error, not a warning. These are the markers that actually exist — the full list
is `[tool.pytest.ini_options] markers` in `pyproject.toml`:

```python
@pytest.mark.real_world    # opens real SSH or API connections
@pytest.mark.slow          # takes a long time
@pytest.mark.unit          # a unit test
@pytest.mark.integration   # exercises several components together
@pytest.mark.expensive     # provisions billable resources
@pytest.mark.performance   # a benchmark
```

Three more are registered from conftest rather than `pyproject.toml`, because
the suites that use them are routinely excluded from a run:
`cluster_network` (from `tests/conftest.py`, for tests needing a private host
named by `CLUSTRIX_TEST_*_HOST`), and `visual` and `ssh_required` (from
`tests/real_world/conftest.py`).

`real_world` is applied automatically to everything under `tests/real_world/`
by that directory's `conftest.py`, so you do not need to add it by hand — and
more importantly, forgetting it cannot silently expose a live-network test to
the ordinary run.

To add a marker, register it in `pyproject.toml` first. Names not on the
lists above — `kubernetes`, `ssh`, `flaky` and anything else you might reach
for by habit — are unregistered, and using one is a collection error.

## Running Tests

### Prerequisite for real-world tests: host keys in `known_hosts`

Anything under `tests/real_world/` that opens an SSH connection goes through
`clustrix.ssh_security.configure_host_key_policy()`, whose default policy is
`"reject"`. **A host that is not in `known_hosts` raises
`HostKeyVerificationError` rather than connecting.**

Add the host key once, deliberately, before running those tests:

```bash
ssh-keyscan cluster.example.edu >> ~/.ssh/known_hosts   # add -p PORT if non-standard
ssh-keygen -F cluster.example.edu                       # confirm it landed
```

See `docs/REAL_WORLD_TESTING.md` for which environment variables name the
hosts, and for the CI equivalent of this step.

### Local Development

```bash
# Run all tests
pytest tests/

# Run specific category
pytest tests/comprehensive/

# Run with real infrastructure
pytest -m real_world

# Run specific test file
pytest tests/test_executor_real.py -v

# Run with coverage
pytest --cov=clustrix --cov-report=html
```

### Using Test Infrastructure

```bash
# Bring the local services up (SSH server, SLURM)
python tests/infrastructure/setup_test_infrastructure.py setup

# See what is running
python tests/infrastructure/setup_test_infrastructure.py status

# Run against them
pytest tests/real_world/

# Take them down again
python tests/infrastructure/setup_test_infrastructure.py teardown
```

### Running the real-world test runner

```bash
# Everything
python tests/run_real_world_tests.py

# One or more categories: executor, decorator, config, credentials, ssh, notebook
python tests/run_real_world_tests.py --category executor decorator

# Bring the Docker services up first and take them down afterwards
python tests/run_real_world_tests.py \
    --setup-infrastructure \
    --teardown-infrastructure
```

## CI/CD Pipeline

There are three workflows in `.github/workflows/`:

| Workflow | Trigger | What it runs |
|-|-|-|
| `fast_ci.yml` | push, pull request | the quick format/lint/type gate |
| `tests.yml` | push, pull request | the credential-free test suite |
| `real-world-tests.yml` | `workflow_dispatch`, weekly `schedule` | the real SSH, SLURM and HF Jobs jobs |

The split matters. Nothing that needs a credential runs on `push` or
`pull_request`, so a pull request from a fork cannot reach a real cluster or
spend money. `real-world-tests.yml` resolves secret presence into job outputs
in a `check-secrets` job first, because the `secrets` context is not available
in `if:` conditions, and every credentialed job gates on those outputs.

What the credential-free run covers, in the order it fails fastest:

1. **Formatting and typing** — black, flake8, mypy. Under a minute.
2. **Unit tests** — everything under `tests/` that needs nothing external.
3. **Documentation** — `scripts/check_docs_examples.py` executes the Python
   blocks on the published pages, and the Sphinx build runs with `-W`.

There is no cloud-provider stage, because there is no cloud backend to test.
`tests/integration/` provisions real billable AWS resources through boto3 and
never runs in CI at all; it refuses to start without `CLUSTRIX_ALLOW_BILLABLE=1`.

## Best Practices

### 1. Use Real Data

```python
# ❌ Bad: Trivial data
@cluster(cores=2)
def process():
    return 1 + 1

# ✅ Good: Realistic data
@cluster(cores=2)
def process():
    import pandas as pd
    df = pd.read_csv("real_data.csv")
    return df.groupby("category").mean().to_dict()
```

### 2. Test Error Conditions

```python
# ✅ Good: Test real error handling
def test_out_of_memory():
    @cluster(cores=1, memory="100MB")
    def memory_intensive():
        import numpy as np
        # Try to allocate 1GB
        try:
            huge_array = np.zeros((1024, 1024, 1024))
        except MemoryError:
            return {"status": "handled_oom"}
    
    result = memory_intensive()
    assert result["status"] == "handled_oom"
```

### 3. Validate Actual Results

```python
# ❌ Bad: Just checking execution
result = my_function()
assert result is not None

# ✅ Good: Validate computation correctness
result = my_function()
assert result["mean"] == pytest.approx(5.0, rel=1e-3)
assert result["std"] > 0
assert len(result["data"]) == 100
```

### 4. Use Fixtures for Setup

```python
@pytest.fixture
def cluster_config():
    """Shared cluster configuration."""
    config = ClusterConfig()
    config.cluster_type = "slurm"
    config.cluster_host = "login.hpc.example.edu"
    yield config
    # Cleanup if needed

def test_with_config(cluster_config):
    configure(**vars(cluster_config))
    # Run test
```

### 5. Document Test Purpose

```python
def test_parallel_execution():
    """
    Test parallel execution across multiple cores.
    
    This validates:
    - Work distribution across cores
    - Result aggregation
    - Parallel efficiency > 70%
    
    User story: As a data scientist, I want to parallelize
    my analysis across available cores for faster processing.
    """
    # Test implementation
```

## Troubleshooting

### Common Issues

#### 1. Infrastructure Not Available

```bash
# Check Docker
docker ps

# Check the SSH server container
ssh -p 2222 testuser@localhost echo "connected"

# Restart everything
docker compose -f tests/infrastructure/docker-compose.yml restart
```

#### 2. Test Timeouts

`pytest-timeout` is a declared test dependency, so both of these work:

```python
@pytest.mark.timeout(300)  # 5 minutes
def test_slow_operation():
    ...
```

```bash
pytest --timeout=600 tests/
```

#### 3. Serialization Failures

```python
# Debug serialization issues
import cloudpickle
import pickle

try:
    serialized = cloudpickle.dumps(my_function)
except Exception as e:
    print(f"Serialization failed: {e}")
    # Try alternative serialization
    import dill
    serialized = dill.dumps(my_function)
```

#### 4. Resource Constraints

```bash
# Check available resources
docker system df
df -h
free -h

# Clean up
docker system prune -a
pytest --cache-clear
```

#### 5. Flaky Tests

`@pytest.mark.flaky` needs the `pytest-rerunfailures` plugin, which this
project does not depend on — the marker is unavailable and unregistered.

Prefer removing the flakiness. Where a test genuinely depends on something
external, gate it on that thing being present rather than retrying until it
passes: a test that succeeds on the third attempt is telling you something
real about the code.

```python
# Handle it in the test
for attempt in range(3):
    try:
        result = flaky_operation()
        break
    except ConnectionError:
        if attempt == 2:
            raise
        time.sleep(2)
```

### Getting Help

1. Check test output for detailed error messages
2. Review logs in `tests/infrastructure/*.log`
3. Run with verbose output: `pytest -vvs`
4. Check GitHub Actions logs for CI failures
5. Open an issue with:
   - Test name and error message
   - Environment details
   - Steps to reproduce

## Contributing Tests

When contributing new tests:

1. **Follow the mocking policy** (real first; mocks only stand in for an already-verified real call, never as a fallback)
2. **Use the test template structure**
3. **Add appropriate markers**
4. **Include docstrings**
5. **Validate real functionality**
6. **Ensure tests are repeatable**
7. **Clean up resources**
8. **Update this documentation if needed**

### Checklist for New Tests

- [ ] Any mock stands in for a call already verified against the real thing (not a fallback)
- [ ] Tests real infrastructure or local execution
- [ ] Meaningful computation performed
- [ ] Results validated for correctness
- [ ] Error cases handled
- [ ] Docstring explains test purpose
- [ ] Appropriate markers added
- [ ] Resources cleaned up
- [ ] Test is repeatable
- [ ] Test completes in reasonable time

## The short version

A test earns its place by being able to fail for a reason you care about. Run
it against the real thing, assert on what came back, and clean up after it.