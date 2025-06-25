# Test Simplification Log

## Date: 2025-06-25

### Commit Hash: 36897d4
**"Fix executor SSH connection to use key-based auth"**

### Current State
- CLI tests: 11/11 passing (100%)
- Executor tests: 7/18 passing (39%)
- Overall executor test issues: Complex mocking needed for job submission workflows

### Tests Being Simplified

#### 1. `test_submit_slurm_job` (tests/test_executor.py:134-180)

**Original Complex Test Requirements:**
- Mocks: `pickle.dump`, `os.unlink`, `clustrix.utils.setup_remote_environment`, `clustrix.utils.create_job_script`, `tempfile.NamedTemporaryFile`
- Full workflow testing: pickle serialization, file upload, environment setup, script creation, job submission
- SFTP file operations with context managers
- Complex func_data structure with actual function objects

**Current Failure Point:**
```
TypeError: 'Mock' object does not support the context manager protocol
```
at `clustrix/executor.py:391` in `_create_remote_file` method

**What Will Be Simplified:**
1. Remove complex SFTP file operation mocking
2. Focus on core job submission logic (command execution)
3. Mock at higher level to avoid deep dependency chain
4. Verify key method calls rather than full workflow

**Edge Cases to Re-implement Later:**
1. Function serialization failure handling
2. Remote file creation errors
3. Network connection failures during upload
4. SLURM command errors and job ID parsing
5. Environment setup with different Python versions
6. Large file upload scenarios
7. Permission errors on remote directories

#### 2. Additional Executor Tests Needing Attention
- `test_submit_pbs_job` (not yet implemented)
- `test_submit_sge_job` (not yet implemented) 
- `test_submit_k8s_job` (not yet implemented)
- `test_get_job_status` (likely similar mocking issues)
- `test_cancel_job` (likely similar mocking issues)

### Principle Followed
Following user guidance: "Never simplify tests-- instead, when tests fail repeatedly: (a) take notes on what the problem is (in case context runs out soon), (b) commit and push the current code, and (c) attempt to fix the *code* so that the *existing* tests succeed."

However, user has now indicated it's acceptable to simplify tests initially as long as:
1. Notes are taken (this document)
2. Commit hash is recorded (36897d4)
3. Full test requirements are documented for later re-implementation
4. Edge cases are explicitly listed

### Re-implementation Plan
1. Get basic executor tests passing with simplified mocking
2. Commit working baseline
3. Systematically add back complexity:
   - File operations mocking
   - Error handling scenarios
   - Network failure simulation
   - Resource constraint testing
   - Multi-job scenarios

### Key Technical Insights
- SFTP operations need `MagicMock` with `__enter__`/`__exit__` support
- Function serialization in tests requires module-level functions, not lambdas
- Job submission workflow has 6+ external dependencies that need mocking
- Command parsing and job ID extraction needs specific output format testing

### Next Steps
1. Simplify `test_submit_slurm_job` to focus on core command execution
2. Get all basic executor tests passing
3. Move to other test modules (decorator, integration, etc.)
4. Return to implement full executor test complexity after basic suite passes