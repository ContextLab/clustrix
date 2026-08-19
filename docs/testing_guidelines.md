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

Clustrix does **not** follow a strict "no mocks ever" rule -- 42 of the
project's 215 `test_*.py` modules use `unittest.mock`, and pretending
otherwise would just make this document wrong. The actual policy:

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

```python
def test_zero_resources():
    """Test handling of zero resource requests."""
    with pytest.raises(ValueError):
        @cluster(cores=0, memory="1GB")
        def invalid_task():
            return "should not execute"
```

### 4. Performance Tests
Tests that measure and validate performance characteristics.

```python
def test_submission_latency():
    """Test job submission latency."""
    start = time.perf_counter()
    
    @cluster(cores=1, memory="1GB")
    def quick_task():
        return "done"
    
    result = quick_task()
    latency = time.perf_counter() - start
    
    assert latency < 1.0  # Should submit in <1 second
```

### 5. Failure Recovery Tests
Tests that validate error handling and recovery mechanisms.

```python
def test_connection_recovery():
    """Test recovery from connection failures."""
    configure(
        cluster_type="ssh",
        connection_retry_count=3,
        connection_retry_delay=1
    )
    
    @cluster(cores=2, memory="2GB", retry_on_failure=True)
    def resilient_task():
        # Task that might experience connection issues
        return process_data()
    
    # Should retry and eventually succeed
    result = resilient_task()
    assert result is not None
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
        # Step 1: Configuration (as users would do)
        configure(test_config)
        
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
@pytest.mark.real_world        # opens real SSH/cloud connections
@pytest.mark.slow              # takes a long time
@pytest.mark.unit              # a unit test
@pytest.mark.integration       # exercises several components together
@pytest.mark.expensive         # provisions billable resources
@pytest.mark.cluster_network # needs the configured cluster network
@pytest.mark.performance       # a benchmark
```

`real_world` is applied automatically to everything under `tests/real_world/`
by that directory's `conftest.py`, so you do not need to add it by hand — and
more importantly, forgetting it cannot silently expose a live-network test to
the ordinary run.

To add a marker, register it in `pyproject.toml` first. This document
previously listed `kubernetes`, `ssh` and `flaky`; none were registered and no
test used them, so following it produced a collection error.

## Running Tests

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
# Setup local infrastructure
python tests/infrastructure/setup_test_infrastructure.py setup

# Run tests against infrastructure
source tests/infrastructure/test.env
pytest tests/real_world/

# Teardown infrastructure
python tests/infrastructure/setup_test_infrastructure.py teardown
```

### Running Comprehensive Test Suite

```bash
# Run all comprehensive tests
python tests/run_real_world_tests.py

# Run specific category
python tests/run_real_world_tests.py --category performance

# With infrastructure setup/teardown
python tests/run_real_world_tests.py \
    --setup-infrastructure \
    --teardown-infrastructure
```

## CI/CD Pipeline

### Workflow Structure

```yaml
on:
  push:         # Run on push to main
  pull_request: # Run on PRs
  schedule:     # Daily comprehensive tests
  workflow_dispatch: # Manual trigger

jobs:
  quick-checks:    # Fast format/lint/type checks
  unit-tests:      # Unit tests without infrastructure
  integration:     # Integration tests with Docker
  edge-cases:      # Edge case validation
  performance:     # Performance benchmarks
  failure-recovery: # Failure scenario tests
  cloud-providers: # Cloud-specific tests (scheduled)
```

### Test Stages

1. **Quick Checks** (< 1 minute)
   - Black formatting
   - Flake8 linting
   - MyPy type checking
   - Quick unit tests

2. **Local Tests** (< 5 minutes)
   - Unit tests
   - Local integration
   - Serialization tests

3. **Infrastructure Tests** (< 30 minutes)
   - Docker-based tests
   - SSH server tests

4. **Comprehensive Tests** (< 60 minutes)
   - Edge cases
   - Performance benchmarks
   - Failure recovery
   - Full integration

5. **Cloud Tests** (scheduled)
   - AWS/GCP/Azure tests
   - Real cluster tests
   - Production validation

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
    configure(cluster_config)
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

# Check Kind cluster
kubectl cluster-info

# Check SSH server
ssh -p 2222 testuser@localhost echo "connected"

# Restart infrastructure
cd tests/infrastructure
docker-compose restart
```

#### 2. Test Timeouts

```python
# Increase timeout for slow tests
@pytest.mark.timeout(300)  # 5 minutes
def test_slow_operation():
    pass

# Or use command line
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

## Conclusion

By following these guidelines, you'll create tests that:
- Catch real issues before they reach production
- Serve as documentation for users
- Build confidence in Clustrix reliability
- Validate actual functionality, not mocked behavior

Remember: **Every test should mirror real user workflows!**