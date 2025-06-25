# Test Failures Analysis - 2025-06-25

## Key Problems Identified:

### 1. **ClusterExecutor Implementation Issues**
- Tests expect methods like `connect()`, `disconnect()`, `_execute_command()` that don't exist
- Current implementation auto-connects in `__init__` but tests expect manual connection
- Methods like `_execute_command()` vs `_execute_remote_command()` naming mismatch
- Missing `sftp_client` attribute that tests expect

### 2. **Loop Detection Failures**
- `detect_loops_in_function()` returning empty lists when it should detect loops
- Issue with `inspect.getsource()` failing on dynamically created functions in tests
- AST parsing might not be working correctly for test functions

### 3. **CLI Test Failures**
- Description still shows "ClusterPy" instead of "Clustrix" in some places
- CLI interface might have changed but tests expect old interface

### 4. **Decorator Test Issues**
- Tests expect `_cluster_config` attribute but might not be set correctly
- Import path issues with helper functions
- Method signature mismatches

### 5. **Local Executor Issues**
- Timeout tests failing - might be timing-related
- Pickle tests failing - might be import/execution context issues

## Strategy:
1. Fix ClusterExecutor to match test expectations
2. Fix loop detection implementation
3. Update CLI to be consistent
4. Ensure decorator properly sets attributes
5. Fix local executor timeout handling