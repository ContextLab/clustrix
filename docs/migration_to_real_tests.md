# Migration Guide: From Mocked to Real Tests

This guide helps you migrate existing mock-based tests to real-world tests following the NO MOCKS principle.

## Table of Contents
1. [Why Migrate?](#why-migrate)
2. [Migration Strategy](#migration-strategy)
3. [Common Patterns](#common-patterns)
4. [Step-by-Step Examples](#step-by-step-examples)
5. [Tools and Helpers](#tools-and-helpers)
6. [Validation Checklist](#validation-checklist)

## Why Migrate?

### Problems with Mock-Based Tests

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
# ✅ Real test that validates actual functionality
def test_remote_execution_real():
    configure(cluster_type="local")  # Real execution
    
    @cluster(cores=4)
    def compute(x):
        import numpy as np  # Real import
        return np.array([x]) * 2  # Real computation
    
    result = compute(21)
    assert result[0] == 42  # Tests actual execution
```

## Migration Strategy

### Phase 1: Identify Tests to Migrate

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

### Phase 2: Prioritize Migration

Migrate in this order:
1. **Critical Path Tests**: Core functionality tests
2. **Integration Tests**: Multi-component interactions
3. **User-Facing Tests**: Public API tests
4. **Utility Tests**: Helper function tests

### Phase 3: Setup Infrastructure

Ensure test infrastructure is available:

```bash
# Setup local test infrastructure
python tests/infrastructure/setup_test_infrastructure.py setup

# Verify services
docker ps
kubectl cluster-info
```

### Phase 4: Migrate Tests

Follow the patterns below to convert mock-based tests to real tests.

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

### Pattern 2: Mock Kubernetes API → Real Kind Cluster

**Before (Mocked):**
```python
@patch('kubernetes.client.BatchV1Api')
def test_k8s_job(mock_api):
    mock_response = Mock()
    mock_response.metadata.name = 'test-job'
    mock_api.return_value.create_namespaced_job.return_value = mock_response
    
    job_id = submit_k8s_job(func_data, config)
    assert job_id == 'test-job'
```

**After (Real):**
```python
@pytest.mark.real_world
def test_k8s_job_real():
    """Test real Kubernetes job submission."""
    configure(
        cluster_type="kubernetes",
        namespace="default"
    )
    
    @cluster(cores=1, memory="512Mi")
    def k8s_task():
        import socket
        return {
            'hostname': socket.gethostname(),
            'pod': os.environ.get('HOSTNAME', 'unknown')
        }
    
    result = k8s_task()
    assert 'clustrix-job' in result['hostname'] or 'pod' in result['pod']
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

## Step-by-Step Examples

### Example 1: Migrating a Complete Test Class

**Original Mock-Based Test:**
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

**Migrated Real Test:**
```python
class TestClusterExecutorReal:
    @pytest.fixture
    def test_config(self):
        """Create real test configuration."""
        config = ClusterConfig()
        config.cluster_type = "local"
        config.cleanup_remote_files = True
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
            ssh_config.username = os.getenv("TEST_SSH_USER")
            
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

### Example 2: Migrating Integration Tests

**Original Mock-Based Integration Test:**
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

**Migrated Real Integration Test:**
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

## Tools and Helpers

### Migration Helper Script

```python
#!/usr/bin/env python3
"""
Helper script to assist in test migration.
"""

import ast
import sys
from pathlib import Path

def find_mock_usage(filepath):
    """Find mock usage in a test file."""
    with open(filepath, 'r') as f:
        tree = ast.parse(f.read())
    
    mocks = []
    for node in ast.walk(tree):
        # Find @patch decorators
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    if hasattr(decorator.func, 'id') and decorator.func.id == 'patch':
                        mocks.append({
                            'type': 'patch',
                            'line': decorator.lineno,
                            'function': node.name
                        })
        
        # Find Mock() usage
        if isinstance(node, ast.Call):
            if hasattr(node.func, 'id') and 'Mock' in node.func.id:
                mocks.append({
                    'type': 'mock_object',
                    'line': node.lineno
                })
    
    return mocks

def suggest_replacement(mock_info):
    """Suggest replacement for mock usage."""
    suggestions = {
        'paramiko.SSHClient': 'Use test SSH server on localhost:2222',
        'kubernetes.client': 'Use Kind cluster or Docker Desktop Kubernetes',
        'builtins.open': 'Use tempfile.NamedTemporaryFile',
        'cloudpickle.dumps': 'Test actual serialization/deserialization',
        'subprocess.run': 'Execute real commands in Docker container'
    }
    
    return suggestions.get(mock_info.get('target'), 'Use real implementation')

if __name__ == '__main__':
    test_file = sys.argv[1] if len(sys.argv) > 1 else 'test_example.py'
    
    mocks = find_mock_usage(test_file)
    
    print(f"Found {len(mocks)} mock usages in {test_file}")
    for mock in mocks:
        print(f"  Line {mock['line']}: {mock['type']}")
        print(f"    Suggestion: {suggest_replacement(mock)}")
```

### Test Infrastructure Validator

```python
def validate_test_infrastructure():
    """Validate that test infrastructure is ready."""
    checks = {
        'Docker': check_docker,
        'Kubernetes': check_kubernetes,
        'SSH Server': check_ssh,
        'MinIO': check_minio,
        'PostgreSQL': check_postgres,
        'Redis': check_redis
    }
    
    ready = True
    for name, check_func in checks.items():
        try:
            check_func()
            print(f"✅ {name} is ready")
        except Exception as e:
            print(f"❌ {name} is not ready: {e}")
            ready = False
    
    return ready

def check_docker():
    subprocess.run(['docker', 'ps'], check=True, capture_output=True)

def check_kubernetes():
    subprocess.run(['kubectl', 'cluster-info'], check=True, capture_output=True)

def check_ssh():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    result = sock.connect_ex(('localhost', 2222))
    sock.close()
    if result != 0:
        raise ConnectionError("SSH server not accessible")
```

## Validation Checklist

After migrating a test, verify:

### Functionality Checklist
- [ ] Test executes without mocks
- [ ] Real infrastructure is used (local/Docker/Kind)
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

## Conclusion

Migrating from mocked to real tests requires effort but provides:
- **Confidence**: Tests validate actual functionality
- **Coverage**: Real issues are caught before production
- **Documentation**: Tests serve as working examples
- **Maintainability**: Less brittle than mock-based tests

Follow this guide to systematically migrate your tests and join the **NO MOCKS** revolution!