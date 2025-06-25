# GitHub Issues to Create for NotImplementedError Functions

## Issue 1: Implement SGE (Sun Grid Engine) Job Submission Support

**Title**: Implement SGE (Sun Grid Engine) Job Submission Support  
**Labels**: enhancement, scheduler-support, sge  
**Priority**: Medium

**Description**:
```markdown
## Overview
The SGE (Sun Grid Engine) scheduler support is currently incomplete with `NotImplementedError` placeholders.

## Current Status
- **Location**: `clustrix/executor.py:195`
- **Method**: `_submit_sge_job()`
- **Status**: Raises `NotImplementedError("SGE support not yet implemented")`

## Requirements

### 1. Job Submission (`_submit_sge_job`)
- Implement SGE job submission using `qsub` command
- Handle SGE-specific job configuration parameters
- Parse job ID from qsub output
- Store job information in `active_jobs`

### 2. Status Checking (`_check_job_status`)
- Add SGE case to status checking logic
- Use `qstat` command to check job status
- Map SGE status codes to internal status strings
- Handle completed/failed job detection

### 3. Script Generation (`_create_sge_script`)
- **Location**: `clustrix/utils.py:407`
- Generate SGE batch scripts with proper directives
- Support SGE-specific parameters (queues, resources, etc.)
- Include environment setup and job execution logic

## Implementation Details

### SGE Directives to Support
```bash
#$ -N job_name
#$ -o output_file
#$ -e error_file  
#$ -pe parallel_env slots
#$ -l h_vmem=memory
#$ -l h_rt=walltime
#$ -q queue_name
```

### Status Mapping
- `qw` (queued waiting) → "pending"
- `r` (running) → "running" 
- `s` (suspended) → "running"
- Job not found → check for result files → "completed" or "failed"

## Acceptance Criteria
- [ ] `_submit_sge_job()` successfully submits jobs to SGE
- [ ] `_check_job_status()` correctly reports SGE job status
- [ ] `_create_sge_script()` generates valid SGE batch scripts
- [ ] All existing SGE tests pass
- [ ] Add comprehensive SGE integration tests

## Related Files
- `clustrix/executor.py` - Job submission and status checking
- `clustrix/utils.py` - Script generation  
- `tests/test_executor.py` - Tests expecting SGE functionality
- `tests/test_utils.py` - Script generation tests
```

---

## Issue 2: Implement Kubernetes Job Support

**Title**: Implement Kubernetes Job Support for Container-based Execution  
**Labels**: enhancement, scheduler-support, kubernetes, containers  
**Priority**: High

**Description**:
```markdown
## Overview
Kubernetes job support is currently incomplete with `NotImplementedError` placeholder. This is a high-priority feature for modern container-based cluster environments.

## Current Status
- **Location**: `clustrix/executor.py:202`
- **Method**: `_submit_k8s_job()`
- **Status**: Raises `NotImplementedError("Kubernetes support not yet implemented")`

## Requirements

### 1. Job Submission (`_submit_k8s_job`)
- Use Kubernetes Job API to submit containerized jobs
- Create Job manifests with proper resource specifications
- Handle Docker image management and environment setup
- Parse job name/ID from Kubernetes API response

### 2. Status Checking (`_check_job_status`)
- Add Kubernetes case to status checking logic
- Use Kubernetes API to check job and pod status
- Map Kubernetes job phases to internal status strings
- Handle job completion and failure detection

### 3. Container Image Management
- Build or use pre-built Docker images with Python environment
- Handle dependency installation in containers
- Support custom Docker registries and authentication

### 4. Result Collection
- Implement result retrieval from completed pods
- Handle persistent volume claims for result storage
- Support logs and error collection from failed pods

## Implementation Details

### Kubernetes Job Manifest Template
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: clustrix-job-{job_id}
spec:
  template:
    spec:
      containers:
      - name: clustrix-worker
        image: clustrix/worker:latest
        resources:
          requests:
            memory: "{memory}"
            cpu: "{cores}"
          limits:
            memory: "{memory}"
            cpu: "{cores}"
        command: ["python", "/app/execute_function.py"]
        volumeMounts:
        - name: job-data
          mountPath: /app/data
      volumes:
      - name: job-data
        configMap:
          name: job-{job_id}-data
      restartPolicy: Never
```

### Status Mapping
- `Pending` → "pending"
- `Running` → "running"
- `Succeeded` → "completed"
- `Failed` → "failed"

### Dependencies
- `kubernetes` Python package
- Docker image build pipeline
- Kubernetes cluster access and RBAC configuration

## Acceptance Criteria
- [ ] `_submit_k8s_job()` successfully submits jobs to Kubernetes
- [ ] `_check_job_status()` correctly reports Kubernetes job status
- [ ] Result collection works from completed pods
- [ ] Docker image building and management
- [ ] Error handling for pod failures and resource constraints
- [ ] All existing Kubernetes tests pass
- [ ] Add comprehensive Kubernetes integration tests

## Related Files
- `clustrix/executor.py` - Job submission and status checking
- `clustrix/config.py` - Kubernetes configuration options
- `tests/test_executor.py` - Tests expecting Kubernetes functionality
- `docker/` - Docker image definitions (to be created)

## Additional Tasks
- [ ] Create Dockerfile for clustrix worker image
- [ ] Add Kubernetes configuration options to ClusterConfig
- [ ] Document Kubernetes setup requirements
- [ ] Add examples for Kubernetes deployment
```

---

## Issue 3: Fix Loop Detection for Dynamically Created Functions

**Title**: Enhanced Loop Detection for Dynamically Created Test Functions  
**Labels**: bug, testing, loop-analysis  
**Priority**: Medium

**Description**:
```markdown
## Problem
Loop detection tests are failing because `inspect.getsource()` cannot retrieve source code for functions defined within test methods.

## Current Failures
- `test_detect_loops_for_loop` - Expected ≥1 loops, got 0
- `test_detect_loops_while_loop` - Expected ≥1 loops, got 0  
- `test_detect_loops_nested` - Expected ≥2 loops, got 0

## Root Cause
Functions defined in test methods like:
```python
def test_detect_loops_for_loop(self):
    def func_with_for_loop():  # This function has no accessible source
        for i in range(10):
            pass
```

The `inspect.getsource(func_with_for_loop)` fails with "could not get source code".

## Possible Solutions

### Option 1: File-based Test Functions
Move test functions to separate module files where source is accessible.

### Option 2: String-based AST Testing
Test loop detection directly with AST strings:
```python
source_code = '''
def test_func():
    for i in range(10):
        pass
'''
tree = ast.parse(source_code)
# Test AST analysis directly
```

### Option 3: Enhanced Source Detection
Improve the loop detection to handle more edge cases of source retrieval.

## Implementation
Recommended approach is Option 2 (string-based testing) as it:
- Tests the actual AST parsing logic
- Doesn't require external files
- Allows testing edge cases easily
- Maintains test isolation

## Acceptance Criteria
- [ ] All loop detection tests pass
- [ ] Test both for and while loops
- [ ] Test nested loop detection
- [ ] Test functions with no loops
- [ ] Maintain existing API compatibility
```

---

## Issue 4: Fix DNS Resolution Mocking in Executor Tests

**Title**: Fix DNS Resolution Mocking Strategy for ClusterExecutor Tests  
**Labels**: bug, testing, mocking  
**Priority**: High

**Description**:
```markdown
## Problem
ClusterExecutor tests are failing with DNS resolution errors because tests attempt to resolve "test.cluster.com" despite mocking.

## Error Details
```
socket.gaierror: [Errno 8] nodename nor servname provided, or not known
```

## Root Cause
Current mocking strategy patches `paramiko.SSHClient` but the constructor still attempts DNS resolution during `connect()` call.

## Current Failures
- All 17 executor tests are erroring
- Tests in `test_executor.py` cannot run due to DNS issues
- Integration tests also affected

## Solution Strategy

### Improved Mocking Approach
1. **Mock Earlier in Chain**: Patch at the `ClusterExecutor.__init__` level
2. **Mock DNS Resolution**: Patch `socket.getaddrinfo` for tests
3. **Complete SSH Mock**: Ensure all paramiko methods are properly mocked

### Implementation Example
```python
@patch('clustrix.executor.paramiko.SSHClient')
@patch('socket.getaddrinfo')
def test_executor_method(self, mock_getaddrinfo, mock_ssh_class):
    # Setup DNS mock
    mock_getaddrinfo.return_value = [('AF_INET', 'SOCK_STREAM', 6, '', ('127.0.0.1', 22))]
    
    # Setup SSH mock
    mock_ssh = Mock()
    mock_ssh_class.return_value = mock_ssh
    
    # Test execution
```

## Acceptance Criteria
- [ ] All executor tests pass without DNS resolution
- [ ] Mocking strategy is consistent across test files
- [ ] Tests verify correct SSH connection parameters
- [ ] Integration tests work with improved mocking
- [ ] No real network connections attempted during tests
```
```

---

## Instructions for Creating Issues

1. Go to https://github.com/ContextLab/clustrix/issues
2. Click "New Issue" 
3. Copy the title and description from above for each issue
4. Add appropriate labels
5. Set priority/milestone as needed
6. Assign to appropriate team members

## Priority Order
1. **High**: Fix DNS Resolution Mocking in Executor Tests
2. **High**: Implement Kubernetes Job Support  
3. **Medium**: Enhanced Loop Detection for Test Functions
4. **Medium**: Implement SGE Job Submission Support