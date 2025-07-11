# Real Cluster Job Testing Guide

This guide explains how to use the comprehensive real cluster job testing system for Clustrix. These tests actually submit jobs to real cluster systems using the `@cluster` decorator and validate the complete end-to-end workflow.

## Overview

The real cluster job testing system provides:

- **Real job submission tests** for all cluster types (SLURM, PBS, SGE, Kubernetes, SSH)
- **Complete end-to-end validation** using the `@cluster` decorator
- **Comprehensive monitoring** of job status and resource usage
- **Automatic validation** of job results and error handling
- **Detailed reporting** with metrics and analysis

## Test Structure

### Cluster-Specific Test Files

- `tests/real_world/test_slurm_job_submission_real.py` - SLURM job submission tests
- `tests/real_world/test_pbs_job_submission_real.py` - PBS job submission tests
- `tests/real_world/test_sge_job_submission_real.py` - SGE job submission tests
- `tests/real_world/test_kubernetes_job_submission_real.py` - Kubernetes job submission tests
- `tests/real_world/test_ssh_job_execution_real.py` - SSH-based job execution tests

### Supporting Infrastructure

- `tests/real_world/cluster_job_validator.py` - Job monitoring and validation framework
- `scripts/run_cluster_job_tests.py` - Comprehensive test runner
- `tests/real_world/credential_manager.py` - Secure credential management

## Test Categories

### Basic Tests (`@pytest.mark.real_world`)

These tests validate core functionality:

- Simple function execution
- Environment variable access
- File I/O operations
- Error handling
- Resource allocation
- Job monitoring

### Expensive Tests (`@pytest.mark.expensive`)

These tests are resource-intensive and run longer:

- Memory-intensive computations
- Long-running jobs
- Parallel processing
- Large data processing

## Running Tests

### Prerequisites

1. **Install dependencies:**
   ```bash
   pip install -e ".[test]"
   ```

2. **Set up credentials:**
   - Follow the [Credential Setup Guide](CREDENTIAL_SETUP.md)
   - Ensure 1Password CLI is configured (local development)
   - Or set up GitHub Actions secrets (CI/CD)

3. **Verify cluster access:**
   ```bash
   python scripts/run_cluster_job_tests.py --check-only
   ```

### Running Tests

#### Test All Available Clusters

```bash
# Run basic tests on all available clusters
python scripts/run_cluster_job_tests.py --cluster all --tests basic

# Run all tests (including expensive ones)
python scripts/run_cluster_job_tests.py --cluster all --tests all

# Run with custom timeout
python scripts/run_cluster_job_tests.py --cluster all --tests basic --timeout 600
```

#### Test Specific Cluster Types

```bash
# Test only SLURM
python scripts/run_cluster_job_tests.py --cluster slurm

# Test only Kubernetes
python scripts/run_cluster_job_tests.py --cluster kubernetes

# Test only SSH
python scripts/run_cluster_job_tests.py --cluster ssh
```

#### Using pytest Directly

```bash
# Run SLURM tests
pytest tests/real_world/test_slurm_job_submission_real.py -v -m "real_world"

# Run basic tests only
pytest tests/real_world/test_slurm_job_submission_real.py -v -m "real_world and not expensive"

# Run expensive tests
pytest tests/real_world/test_slurm_job_submission_real.py -v -m "expensive"
```

## Test Examples

### Simple Function Test

```python
@pytest.mark.real_world
def test_simple_function_slurm_submission(self, slurm_config):
    """Test submitting a simple function to SLURM."""
    
    @cluster(cores=1, memory="1GB", time="00:05:00")
    def add_numbers(x: int, y: int) -> int:
        """Simple addition function for testing."""
        return x + y
    
    # Submit job and wait for result
    result = add_numbers(10, 32)
    
    # Validate result
    assert result == 42
    assert isinstance(result, int)
```

### Environment Access Test

```python
@pytest.mark.real_world
def test_function_with_slurm_environment(self, slurm_config):
    """Test SLURM job that accesses environment variables."""
    
    @cluster(cores=1, memory="1GB", time="00:05:00")
    def get_job_environment() -> Dict[str, str]:
        """Get SLURM job environment variables."""
        import os
        
        return {
            "SLURM_JOB_ID": os.getenv("SLURM_JOB_ID", "not_set"),
            "SLURM_JOB_NAME": os.getenv("SLURM_JOB_NAME", "not_set"),
            "SLURM_CPUS_PER_TASK": os.getenv("SLURM_CPUS_PER_TASK", "not_set"),
            "HOSTNAME": os.getenv("HOSTNAME", "not_set"),
            "USER": os.getenv("USER", "not_set")
        }
    
    result = get_job_environment()
    
    # Validate SLURM environment
    assert isinstance(result, dict)
    assert result["SLURM_JOB_ID"] != "not_set"
    assert result["USER"] != "not_set"
```

### Parallel Processing Test

```python
@pytest.mark.real_world
def test_parallel_loop_slurm(self, slurm_config):
    """Test parallel loop execution on SLURM."""
    
    @cluster(cores=4, memory="4GB", time="00:10:00", parallel=True)
    def compute_squares(numbers: List[int]) -> List[int]:
        """Compute squares of numbers (should be parallelized)."""
        import time
        
        results = []
        for num in numbers:
            # Simulate some work
            time.sleep(0.1)
            results.append(num * num)
        
        return results
    
    # Submit job with test data
    test_numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    result = compute_squares(test_numbers)
    
    # Validate results
    assert isinstance(result, list)
    assert len(result) == len(test_numbers)
    expected = [num * num for num in test_numbers]
    assert result == expected
```

## Job Validation Framework

### ClusterJobValidator

The `ClusterJobValidator` class provides comprehensive job monitoring:

```python
from tests.real_world.cluster_job_validator import create_validator

# Create validator for SLURM
validator = create_validator("slurm", 
                           cluster_host="cluster.example.com",
                           username="user")

# Validate job submission
result = validator.validate_job_submission(job_id, "my_function", "args", {})

# Monitor job execution
execution_result = validator.monitor_job_execution(job_id, timeout=300)

# Validate job output
output_result = validator.validate_job_output(job_id, expected_output=42)
```

### Validation Features

- **Job submission validation** - Verify job was submitted correctly
- **Resource allocation validation** - Check requested vs allocated resources
- **Execution monitoring** - Track job status changes
- **Output validation** - Verify job results match expectations
- **Error detection** - Identify and report job failures
- **Metrics collection** - Gather performance and resource usage data

## Cluster-Specific Testing

### SLURM Tests

Test SLURM-specific features:

- SLURM environment variables (`SLURM_JOB_ID`, `SLURM_CPUS_PER_TASK`, etc.)
- Resource allocation (cores, memory, time limits)
- Partition and queue specification
- Job arrays and parallel execution
- SLURM accounting and metrics

### PBS Tests

Test PBS-specific features:

- PBS environment variables (`PBS_JOBID`, `PBS_NODEFILE`, etc.)
- Queue specification
- Node file processing
- Resource management
- Job arrays simulation

### SGE Tests

Test SGE-specific features:

- SGE environment variables (`JOB_ID`, `QUEUE`, `SGE_TASK_ID`, etc.)
- Parallel environments
- Queue specification
- Resource limits
- Array job simulation

### Kubernetes Tests

Test Kubernetes-specific features:

- Kubernetes environment variables (`KUBERNETES_SERVICE_HOST`, etc.)
- Pod and container management
- Resource specifications (CPU, memory limits)
- Namespace isolation
- Persistent storage access
- Service account and secrets

### SSH Tests

Test SSH-based execution:

- Remote environment access
- System command execution
- File operations
- Network connectivity
- Resource monitoring
- Python environment analysis

## Test Results and Reporting

### Automatic Reporting

The test runner generates comprehensive reports:

```bash
# Run tests with custom output file
python scripts/run_cluster_job_tests.py --output my_test_results.json
```

### Report Contents

- **Session information** (ID, timestamp, duration)
- **Test results** (passed, failed, skipped counts)
- **Cluster availability** status
- **Individual test details**
- **Error messages** and stack traces
- **Performance metrics**
- **Environment information**

### Sample Report Structure

```json
{
  "session_info": {
    "session_id": "abc12345",
    "start_time": 1625097600.0,
    "duration": 450.2,
    "environment": {
      "python_version": "3.9.7",
      "platform": "linux"
    }
  },
  "results": {
    "total_tests": 45,
    "total_passed": 42,
    "total_failed": 2,
    "total_skipped": 1,
    "success_rate": 93.3,
    "cluster_results": {
      "slurm": {
        "tests_passed": 12,
        "tests_failed": 0,
        "tests_skipped": 0,
        "duration": 180.5
      }
    }
  },
  "credential_status": {
    "slurm": true,
    "kubernetes": true,
    "ssh": true
  }
}
```

## Troubleshooting

### Common Issues

#### Job Submission Failures

1. **Check cluster connectivity:**
   ```bash
   python scripts/run_cluster_job_tests.py --check-only
   ```

2. **Verify credentials:**
   ```bash
   python scripts/test_real_world_credentials.py
   ```

3. **Check cluster queue:**
   ```bash
   # SLURM
   squeue -u $USER
   
   # PBS
   qstat -u $USER
   
   # SGE
   qstat -u $USER
   ```

#### Test Timeouts

1. **Increase timeout:**
   ```bash
   python scripts/run_cluster_job_tests.py --timeout 600
   ```

2. **Run basic tests only:**
   ```bash
   python scripts/run_cluster_job_tests.py --tests basic
   ```

3. **Check cluster load:**
   ```bash
   # SLURM
   sinfo
   
   # Check job queue
   squeue
   ```

#### Permission Errors

1. **Verify SSH key permissions:**
   ```bash
   ls -la ~/.ssh/
   chmod 600 ~/.ssh/id_rsa
   ```

2. **Test SSH connection:**
   ```bash
   ssh -vvv user@cluster.example.com
   ```

3. **Check cluster account:**
   ```bash
   # SLURM
   sacctmgr show user $USER
   ```

### Debug Mode

Enable verbose output for debugging:

```bash
# Run with pytest verbose mode
pytest tests/real_world/test_slurm_job_submission_real.py -v -s

# Enable debug logging
export CLUSTRIX_DEBUG=1
python scripts/run_cluster_job_tests.py --cluster slurm
```

## Best Practices

### Test Development

1. **Start with simple tests** - Validate basic functionality first
2. **Use descriptive test names** - Make purpose clear
3. **Test error conditions** - Verify error handling works
4. **Include resource validation** - Check resource allocation
5. **Test parallel execution** - Verify loop parallelization

### Resource Management

1. **Use appropriate resources** - Don't over-allocate
2. **Set reasonable timeouts** - Allow sufficient time
3. **Clean up test artifacts** - Remove temporary files
4. **Monitor cluster usage** - Be considerate of other users

### Credential Security

1. **Use secure credential storage** - 1Password or GitHub secrets
2. **Rotate credentials regularly** - Follow security best practices
3. **Limit credential scope** - Use minimal required permissions
4. **Never commit credentials** - Keep them out of version control

### Test Organization

1. **Group related tests** - Use test classes for organization
2. **Use appropriate markers** - `@pytest.mark.real_world`, `@pytest.mark.expensive`
3. **Document test purpose** - Clear docstrings and comments
4. **Handle cluster unavailability** - Skip gracefully when clusters not available

## Integration with CI/CD

### GitHub Actions

The tests integrate with GitHub Actions:

```yaml
name: Real Cluster Job Tests

on:
  push:
    branches: [ main ]
  workflow_dispatch:
    inputs:
      cluster_type:
        description: 'Cluster type to test'
        required: true
        default: 'all'
        type: choice
        options:
        - all
        - slurm
        - kubernetes
        - ssh

jobs:
  cluster-tests:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: |
        pip install -e ".[test]"
    
    - name: Run cluster job tests
      env:
        CLUSTRIX_USERNAME: ${{ secrets.CLUSTRIX_USERNAME }}
        CLUSTRIX_PASSWORD: ${{ secrets.CLUSTRIX_PASSWORD }}
        LAMBDA_CLOUD_API_KEY: ${{ secrets.LAMBDA_CLOUD_API_KEY }}
      run: |
        python scripts/run_cluster_job_tests.py --cluster ${{ inputs.cluster_type }}
    
    - name: Upload test results
      uses: actions/upload-artifact@v4
      if: always()
      with:
        name: cluster-test-results
        path: test_results/
```

### Local Development

For local development:

1. **Set up 1Password CLI** - Follow credential setup guide
2. **Configure cluster access** - Ensure SSH keys and permissions
3. **Run tests incrementally** - Start with basic tests
4. **Monitor resource usage** - Be mindful of cluster load

## Extending the Test Suite

### Adding New Cluster Types

1. **Create test file** - `test_<cluster_type>_job_submission_real.py`
2. **Implement cluster-specific tests** - Environment, resources, etc.
3. **Update credential manager** - Add credential support
4. **Update test runner** - Add cluster type support
5. **Update documentation** - Document new cluster support

### Adding New Test Cases

1. **Identify test scenario** - What functionality to test
2. **Create test function** - Use appropriate decorators
3. **Add validation** - Verify expected behavior
4. **Test error conditions** - Ensure robust error handling
5. **Update documentation** - Document new test cases

## Support and Troubleshooting

For issues with real cluster job testing:

1. **Check this documentation** - Review troubleshooting section
2. **Verify cluster status** - Ensure clusters are operational
3. **Test credentials** - Run credential validation tests
4. **Check logs** - Review detailed test output
5. **Report issues** - Include test results and error messages

---

This comprehensive testing system ensures that Clustrix's `@cluster` decorator works correctly across all supported cluster types with real job submissions and validation.