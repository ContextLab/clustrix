---
name: Bug report
about: Create a report to help us improve Clustrix
title: '[BUG] '
labels: 'bug'
assignees: ''

---

## Bug Description
<!-- Provide a clear and concise description of the bug -->

## To Reproduce
<!-- Steps to reproduce the behavior -->
1. Configure Clustrix with '...'
2. Run function '...'
3. See error

## Expected Behavior
<!-- What you expected to happen -->

## Actual Behavior
<!-- What actually happened -->

## Error Messages
<!-- If applicable, paste the full error message/traceback -->
```python
# Paste error message here
```

## Environment
- **Clustrix Version**: <!-- e.g., 0.1.1 -->
- **Python Version**: <!-- e.g., 3.10.0 -->
- **Operating System**: <!-- e.g., Ubuntu 22.04 -->
- **Cluster Type**: <!-- e.g., SLURM, PBS, SGE, Kubernetes, SSH -->
- **Installation Method**: <!-- pip, conda, from source -->

## Technical Details

### Cluster Configuration
<!-- Provide your Clustrix configuration (remove sensitive information like passwords/keys) -->
```python
import clustrix

clustrix.configure(
    cluster_type='...',
    cluster_host='...',
    username='...',
    remote_work_dir='...',
    # Other relevant config
)
```

### Function Code
<!-- If the issue is with a specific decorated function, provide the code -->
```python
@cluster(cores=4, memory='8GB')
def my_function():
    # Your function code here
    pass
```

### Cluster Environment Details
- **Scheduler Version**: <!-- e.g., SLURM 20.11.9 -->
- **Node Configuration**: <!-- e.g., 4 cores, 16GB RAM per node -->
- **Python Environment on Cluster**: <!-- e.g., system Python, conda, venv -->
- **Available Modules**: <!-- e.g., module avail output for relevant modules -->
- **Queue/Partition**: <!-- e.g., which queue or partition used -->

### Network & Connectivity
- **SSH Connection Method**: <!-- e.g., password, key-based, agent forwarding -->
- **Network Environment**: <!-- e.g., VPN, direct connection, jump host -->
- **Firewall/Security**: <!-- Any network restrictions or security policies -->

### Job Execution Details
- **Job ID**: <!-- If available, cluster job ID -->
- **Job Status**: <!-- e.g., RUNNING, FAILED, COMPLETED -->
- **Resource Usage**: <!-- Actual vs requested resources -->
- **Execution Time**: <!-- How long job ran before failing -->
- **Remote Logs**: <!-- Content of cluster logs if available -->

### Serialization & Dependencies
- **Function Dependencies**: <!-- List of imports/modules used in decorated function -->
- **Serialization Method**: <!-- cloudpickle, dill, etc. -->
- **Package Versions**: <!-- pip freeze output for key packages -->
- **Virtual Environment**: <!-- Local vs remote environment differences -->

## Minimal Reproducible Example
<!-- Provide the smallest possible code that reproduces the issue -->
```python
import clustrix

# Your minimal example here
```

## Logs and Output
### Local Logs
```
# Paste local Clustrix logs here
```

### Remote Cluster Logs
```
# Paste cluster scheduler logs (SLURM output, etc.)
```

### SSH/Connection Logs
```
# If connection issues, paste relevant SSH debug output
```

## Additional Context
<!-- Add any other context about the problem here -->

## Impact Assessment
- **Severity**: <!-- Critical/High/Medium/Low -->
- **Frequency**: <!-- Always/Often/Sometimes/Rare -->
- **Workaround Available**: <!-- Yes/No - describe if yes -->
- **Affects Production**: <!-- Yes/No -->

## Screenshots
<!-- If applicable, add screenshots to help explain your problem -->

## Possible Solution
<!-- If you have ideas on how to fix this, please share -->

## Related Issues
<!-- Link to any related issues -->

## Checklist
- [ ] I have searched existing issues to ensure this bug hasn't been reported
- [ ] I have included all relevant error messages and logs
- [ ] I have provided steps to reproduce the issue
- [ ] I have included my environment details and cluster configuration
- [ ] I have provided a minimal reproducible example
- [ ] I have removed sensitive information (passwords, keys, private hostnames)