# Replacing Mock-Based Tests With Real Ones

Roughly a fifth of the test modules still assert against `unittest.mock` in
ways the project's testing policy does not allow. Replacing them is issue
[#117](https://github.com/ContextLab/clustrix/issues/117). This page is the
working guide for that: how to tell which tests need replacing, what to
replace them with, and how to check the replacement is real.

New tests must not add to the count.

## Contents
1. [Why](#why)
2. [Strategy](#strategy)
3. [Common patterns](#common-patterns)
4. [Worked examples](#worked-examples)
5. [Tools](#tools)
6. [Checklist](#checklist)

## Why

### What a mock-based test does not tell you

Mock-based tests often miss critical issues:

```python
# ❌ Mock-based test that passes but hides real problems
@patch('clustrix.executor.ClusterExecutor')
def test_remote_execution(mock_executor):
    mock_executor.return_value.submit_job.return_value = '12345'
    mock_executor.return_value.wait_for_result.return_value = {'result': 42}
    
    @cluster(cores=4)
    def compute(x):
        return x * 2  # Never actually executed!
    
    result = compute(21)
    assert result == {'result': 42}  # Passes but doesn't test real execution
```

**Hidden Issues:**
- Function serialization failures
- Import errors in remote environment
- Resource allocation problems
- Network connectivity issues
- Actual computation errors

### Benefits of Real Tests

Real tests catch actual problems:

```python
# Real test that validates actual functionality
def test_remote_execution_real():
    configure(cluster_type="local")  # the backend is set here, not on @cluster

    @cluster(cores=4)
    def compute(x):
        import numpy as np  # a real import, in a real interpreter
        return np.array([x]) * 2  # a real computation

    result = compute(21)
    assert result[0] == 42  # the assertion is on what the function returned
```

Two things about that example. `cluster_type` is not a `@cluster` keyword —
passing it there logs "@cluster received unrecognised option(s)" and is
ignored, so the backend has to come from `configure`. And on the `local`
backend `cores=4` has no effect: the function runs in the caller's own
process, sequentially. That is issue
[#152](https://github.com/ContextLab/clustrix/issues/152). Real in-process
parallelism lives in `clustrix.local_executor.LocalExecutor`, which is a
separate entry point.

## Strategy

### Step 1: Find the tests that need replacing

Run the audit script to find tests using mocks:

```bash
python tests/audit_antipatterns.py
```

Output:
```
High-priority files to refactor:
1. test_executor.py - 86 anti-patterns
2. test_decorator.py - 72 anti-patterns
...
```

### Step 2: Order the work

Take them in this order:
1. **Critical Path Tests**: Core functionality tests
2. **Integration Tests**: Multi-component interactions
3. **User-Facing Tests**: Public API tests
4. **Utility Tests**: Helper function tests

### Step 3: Stand up the infrastructure

The real tests need somewhere real to run:

```bash
# Bring the local test services up
python tests/infrastructure/setup_test_infrastructure.py setup

# Check what is running
python tests/infrastructure/setup_test_infrastructure.py status
docker ps
```

`tests/infrastructure/docker-compose.yml` defines the services: an SSH server
and a SLURM container. Tear them down again with
`... setup_test_infrastructure.py teardown`.

### Step 4: Rewrite

Follow the patterns below.

## Common Patterns

### Pattern 1: Mock SSH Client → Real SSH Server

**Before (Mocked):**
```python
@patch('paramiko.SSHClient')
def test_ssh_connection(mock_ssh):
    mock_ssh.return_value.connect.return_value = None
    mock_ssh.return_value.exec_command.return_value = (None, 'output', '')
    
    executor = ClusterExecutor(config)
    executor.connect()
    
    assert mock_ssh.called
```

**After (Real):**
```python
def test_ssh_connection_real():
    """Test real SSH connection."""
    config = ClusterConfig()
    config.cluster_type = "ssh"
    config.cluster_host = "localhost"
    config.cluster_port = 2222
    config.username = "testuser"
    config.password = "testpass"
    
    executor = ClusterExecutor(config)
    executor.connect()
    
    # Test real command execution
    stdout, stderr = executor._execute_command("echo 'test'")
    assert stdout.strip() == "test"
    
    executor.disconnect()
```

### Pattern 2: Mock HTTP-API backend → Real HuggingFace Job

**Before (Mocked):**
```python
@patch('clustrix.hf_jobs.HFJobsManager.submit_job')
def test_hf_job(mock_submit):
    mock_submit.return_value = 'test-job'

    job_id = submit_hf_job(func_data, config)
    assert job_id == 'test-job'
```

**After (Real):**
```python
@pytest.mark.real_world
def test_hf_job_real():
    """Test real HuggingFace Jobs submission."""
    configure(
        cluster_type="huggingface",
        hf_namespace="contextlab",
    )

    @cluster(cores=1, memory="512MB")
    def hf_task():
        import os
        import socket
        return {
            'hostname': socket.gethostname(),
            'container': os.environ.get('HOSTNAME', 'unknown'),
        }

    result = hf_task()
    assert result['hostname']
```

### Pattern 3: Mock File Operations → Real File System

**Before (Mocked):**
```python
@patch('builtins.open')
@patch('os.path.exists')
def test_file_operations(mock_exists, mock_open):
    mock_exists.return_value = True
    mock_open.return_value.__enter__.return_value.read.return_value = 'data'
    
    result = process_file('test.txt')
    assert result == 'data'
```

**After (Real):**
```python
def test_file_operations_real():
    """Test real file operations."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write('test data')
        temp_path = f.name
    
    try:
        @cluster(cores=1, memory="1GB")
        def process_file(filepath):
            with open(filepath, 'r') as f:
                data = f.read()
            return {'content': data, 'size': len(data)}
        
        result = process_file(temp_path)
        assert result['content'] == 'test data'
        assert result['size'] == 9
        
    finally:
        os.unlink(temp_path)
```

### Pattern 4: Mock Serialization → Real Serialization

**Before (Mocked):**
```python
@patch('cloudpickle.dumps')
def test_serialization(mock_pickle):
    mock_pickle.return_value = b'serialized'
    
    data = serialize_function(my_func, args, kwargs)
    assert data == b'serialized'
```

**After (Real):**
```python
def test_serialization_real():
    """Test real function serialization."""
    @cluster(cores=2, memory="2GB")
    def complex_function(data):
        import numpy as np
        import pandas as pd
        
        df = pd.DataFrame(data)
        return df.describe().to_dict()
    
    # Test serialization
    import cloudpickle
    serialized = cloudpickle.dumps(complex_function)
    assert len(serialized) > 0
    
    # Test deserialization and execution
    deserialized = cloudpickle.loads(serialized)
    result = deserialized({'col': [1, 2, 3, 4, 5]})
    assert result['col']['mean'] == 3.0
```

## Worked examples

### Example 1: a whole test class

**Mock-based:**
```python
class TestClusterExecutor:
    @patch('paramiko.SSHClient')
    def test_connect(self, mock_ssh):
        mock_ssh.return_value.connect.return_value = None
        executor = ClusterExecutor(self.config)
        executor.connect()
        mock_ssh.assert_called_once()
    
    @patch('ClusterExecutor._execute_command')
    def test_submit_job(self, mock_exec):
        mock_exec.return_value = ('job_123', '')
        job_id = self.executor.submit_job(func_data, config)
        assert job_id == 'job_123'
```

**Real:**
```python
class TestClusterExecutorReal:
    @pytest.fixture
    def test_config(self):
        """Create real test configuration."""
        config = ClusterConfig()
        config.cluster_type = "local"
        config.cleanup_on_success = True
        return config
    
    def test_connect_real(self, test_config):
        """Test real connection establishment."""
        executor = ClusterExecutor(test_config)
        executor.connect()
        
        # For local, no SSH needed
        assert executor.ssh_client is None
        
        # For SSH testing
        if os.getenv("TEST_SSH_HOST"):
            ssh_config = ClusterConfig()
            ssh_config.cluster_type = "ssh"
            ssh_config.cluster_host = os.getenv("TEST_SSH_HOST")
            ssh_config.username = os.getenv("TEST_SSH_USERNAME")
            
            ssh_executor = ClusterExecutor(ssh_config)
            ssh_executor.connect()
            assert ssh_executor.ssh_client is not None
            ssh_executor.disconnect()
    
    def test_submit_job_real(self, test_config):
        """Test real job submission."""
        executor = ClusterExecutor(test_config)
        executor.connect()
        
        # Real function to execute
        def compute_stats(data):
            import numpy as np
            return {
                'mean': float(np.mean(data)),
                'std': float(np.std(data))
            }
        
        # Serialize and submit
        from clustrix.utils import serialize_function
        func_data = serialize_function(compute_stats, ([1,2,3,4,5],), {})
        
        job_id = executor.submit_job(func_data, {'cores': 1, 'memory': '1GB'})
        assert job_id is not None
        
        # Wait for real result
        result = executor.wait_for_result(job_id)
        assert result['mean'] == 3.0
        assert result['std'] > 0
        
        executor.disconnect()
```

### Example 2: an integration test

**Mock-based:**
```python
@patch('clustrix.executor.ClusterExecutor.submit_job')
@patch('clustrix.executor.ClusterExecutor.wait_for_result')
def test_end_to_end(mock_wait, mock_submit):
    mock_submit.return_value = 'job_123'
    mock_wait.return_value = {'result': 'success'}
    
    @cluster(cores=4)
    def my_task():
        return "never executed"
    
    result = my_task()
    assert result == {'result': 'success'}
```

**Real:**
```python
def test_end_to_end_real():
    """Test complete workflow with real execution."""
    # Step 1: Configure
    configure(
        cluster_type="local",
        default_cores=4,
        default_memory="4GB"
    )
    
    # Step 2: Define realistic task
    @cluster(cores=4, memory="4GB")
    def analyze_dataset(data_path):
        import pandas as pd
        import numpy as np
        
        # Real data processing
        df = pd.DataFrame({
            'values': np.random.randn(1000),
            'categories': np.random.choice(['A', 'B', 'C'], 1000)
        })
        
        results = {
            'mean_by_category': df.groupby('categories')['values'].mean().to_dict(),
            'overall_std': float(df['values'].std()),
            'record_count': len(df)
        }
        
        return results
    
    # Step 3: Execute
    result = analyze_dataset("dummy_path")
    
    # Step 4: Validate real results
    assert 'mean_by_category' in result
    assert 'A' in result['mean_by_category']
    assert result['record_count'] == 1000
    assert result['overall_std'] > 0
```

## Tools

`tests/audit_antipatterns.py` walks the test tree and reports what it finds,
ranked by file. Run it before you start and again when you think you are
finished:

```bash
python tests/audit_antipatterns.py
```

Two grep checks are worth keeping in your fingers. The first is the one the
project treats as a hard invariant — production code must never know it is
being tested, so this must stay empty:

```bash
grep -rn "unittest.mock\|MagicMock\|isinstance(.*Mock" clustrix/
```

The second tells you whether a file you just rewrote still has mocks in it:

```bash
grep -rn "@patch\|MagicMock\|Mock(" tests/test_executor_real.py
```

To check the local services a real test needs are up:

```bash
python tests/infrastructure/setup_test_infrastructure.py status
```

That reports on the containers defined in
`tests/infrastructure/docker-compose.yml` — SSH server and SLURM.

## Checklist

After rewriting a test, check:

### Functionality Checklist
- [ ] Test executes without mocks
- [ ] Real infrastructure is used (local process, or the Docker services above)
- [ ] Actual computations are performed
- [ ] Results are validated for correctness
- [ ] Error cases are tested with real errors
- [ ] Resources are properly cleaned up

### Code Quality Checklist
- [ ] No `@patch` decorators remain
- [ ] No `Mock()` or `MagicMock()` objects
- [ ] No `exec()` for function execution
- [ ] Meaningful assertions on real results
- [ ] Proper error handling
- [ ] Docstrings explain what's tested

### Performance Checklist
- [ ] Test completes in reasonable time (<30s for most tests)
- [ ] No unnecessary delays or sleeps
- [ ] Resources are efficiently used
- [ ] Parallel tests don't interfere

### Example Validation

```python
# Run migrated test
pytest tests/test_executor_real.py -v

# Check for remaining mocks
grep -r "@patch\|Mock\|MagicMock" tests/test_executor_real.py

# Verify real execution
pytest tests/test_executor_real.py -v -s --log-cli-level=DEBUG

# Check coverage
pytest tests/test_executor_real.py --cov=clustrix.executor
```

## Common Pitfalls

### 1. Incomplete Mock Removal

```python
# ❌ Still using mock for part of test
def test_hybrid():
    with patch('some_module'):  # Mock still present!
        @cluster(cores=2)
        def task():
            return "partial mock"
```

### 2. Trivial Computations

```python
# ❌ Not meaningful computation
@cluster(cores=4)
def task():
    return 1 + 1  # Too simple

# ✅ Meaningful computation
@cluster(cores=4)
def task():
    import numpy as np
    data = np.random.randn(1000, 1000)
    return np.linalg.svd(data, compute_uv=False)
```

### 3. Missing Cleanup

```python
# ❌ Resources not cleaned up
def test_file_ops():
    f = open('/tmp/test.txt', 'w')
    f.write('test')
    # File never closed or deleted!

# ✅ Proper cleanup
def test_file_ops():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b'test')
        temp_path = f.name
    
    try:
        # Use file
        process_file(temp_path)
    finally:
        os.unlink(temp_path)  # Always cleanup
```

## What you get for the effort

A test that ran against the real thing is evidence about the real thing. It
also doubles as a working example, and it does not break every time someone
reorders the arguments of a function it never actually called.

Follow this guide to systematically migrate your tests and join the **NO MOCKS** revolution!