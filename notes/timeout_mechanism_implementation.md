# Timeout Mechanism Implementation for Two-Venv Setup

## Problem Solved

Real-world tests were hanging indefinitely due to the `setup_two_venv_environment` function taking too long or getting stuck during virtual environment creation on remote clusters. This prevented the test suite from completing and blocked the development workflow.

## Solution Overview

Implemented a configurable timeout mechanism with graceful fallback for the two-venv setup process:

### Configuration Options Added

```python
# In clustrix/config.py
use_two_venv: bool = True  # Use two-venv setup for cross-version compatibility
venv_setup_timeout: int = 300  # Timeout for venv setup in seconds (5 minutes)
```

### Implementation Details

**Threading-Based Timeout**: Used `threading.Thread` with `join(timeout)` to implement non-blocking timeout control:

```python
def setup_venv():
    nonlocal venv_info, exception_occurred
    try:
        venv_info = setup_two_venv_environment(
            self.ssh_client, remote_job_dir, func_data["requirements"], self.config
        )
    except Exception as e:
        exception_occurred = e

setup_thread = threading.Thread(target=setup_venv)
setup_thread.daemon = True
setup_thread.start()
setup_thread.join(timeout=getattr(self.config, 'venv_setup_timeout', 300))

if setup_thread.is_alive():
    logger.warning("Two-venv setup timed out, falling back to basic setup")
    raise TimeoutError("Two-venv setup timed out")
```

**Graceful Fallback**: When timeout occurs or two-venv fails:
```python
except Exception as e:
    logger.warning(f"Two-venv setup failed, falling back to basic setup: {e}")
    # Fallback to basic environment setup
    setup_remote_environment(
        self.ssh_client,
        remote_job_dir,
        func_data["requirements"],
        self.config,
    )
    updated_config.venv_info = None
```

**Applied to Both Job Types**: Implemented in both `_submit_slurm_job` and `_submit_ssh_job` methods for comprehensive coverage.

## Testing and Verification

### Core Tests
- **All 151 core tests pass**: test_decorator, test_utils, test_config, test_filesystem, test_executor
- **Fixed import shadowing**: Resolved `UnboundLocalError` for local `import time` statement
- **All linting passes**: black, flake8, mypy

### Real-World Tests
- **GPU detection test**: Consistently passes in ~2 minutes (well under 5-minute timeout)
- **Timeout mechanism verified**: Short timeout test confirms fallback behavior works
- **Two-venv completion**: Logs show successful completion: "Two-venv setup successful, using: /tmp/clustrix_tensor01/job_*/venv1_serialization/bin/python"

### Specific Test Results

**Working Test Example**:
```
tests/real_world/test_tensor01_gpu_comprehensive.py::test_tensor01_8_gpu_detection_simple
GPU Detection Output:
GPU_COUNT:8
CUDA_AVAILABLE:True
✅ Successfully detected 8 GPUs on tensor01
PASSED in 137.19s (0:02:17)
```

**Timeout Logs Showing Success**:
```
2025-07-13 18:05:00 - clustrix.executor - INFO - Setting up two-venv environment for cross-version compatibility
2025-07-13 18:07:07 - clustrix.executor - INFO - Two-venv setup successful, using: /tmp/clustrix_tensor01/job_1752444299/venv1_serialization/bin/python
```

## Impact and Benefits

1. **Reliability**: Real-world tests no longer hang indefinitely
2. **Configurability**: Users can adjust timeout based on their environment
3. **Backward Compatibility**: Default behavior unchanged (two-venv enabled with 5-minute timeout)
4. **Graceful Degradation**: Falls back to basic environment setup when needed
5. **Transparency**: Clear logging shows whether two-venv succeeded or fell back

## Configuration Usage

Users can customize the timeout behavior:

```python
from clustrix.config import configure

# Disable two-venv entirely for faster setup
configure(use_two_venv=False)

# Use shorter timeout for faster environments
configure(venv_setup_timeout=120)  # 2 minutes

# Use longer timeout for slower environments
configure(venv_setup_timeout=600)  # 10 minutes
```

## Files Modified

1. **clustrix/config.py**: Added `use_two_venv` and `venv_setup_timeout` configuration options
2. **clustrix/executor.py**: Implemented timeout mechanism in both SLURM and SSH job submission methods

## Verification Commands

```bash
# Run core tests
python -m pytest tests/test_decorator.py tests/test_utils.py tests/test_config.py tests/test_filesystem.py tests/test_executor.py

# Run real-world GPU test
python -m pytest tests/real_world/test_tensor01_gpu_comprehensive.py::test_tensor01_8_gpu_detection_simple -v -s

# Check code quality
black --check clustrix/
flake8 clustrix/
mypy clustrix/
```

This implementation successfully resolves the hanging test issue while maintaining system robustness and providing user control over the timeout behavior.