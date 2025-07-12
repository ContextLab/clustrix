# Conda PyTorch CUDA Implementation Session - 2025-07-12

## Session Summary
Successfully identified path to modern PyTorch CUDA support on tensor01 through conda environments, with implementation partially complete.

## Key Discovery: Conda Available on tensor01 🎉

**Critical Finding**: tensor01.dartmouth.edu has conda 4.10.3 installed, enabling creation of modern Python environments:

```bash
# Confirmed working on tensor01:
conda create -n test_pytorch_env python=3.9 -y
conda activate test_pytorch_env
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Results:
PyTorch version: 2.7.1+cu118
CUDA available: True
Device count: 8
Current device: 0
Device name: NVIDIA RTX A6000
```

## Architecture Solution: Enhanced Two-Venv System

**Problem**: tensor01 only has Python 3.6.8 available by default, limiting PyTorch to old versions
**Solution**: Use conda for VENV2 to enable modern Python 3.9 + latest PyTorch CUDA

### Enhanced Architecture:
- **VENV1**: Python 3.6 venv for serialization compatibility (unchanged)
- **VENV2**: Python 3.9 conda environment for modern PyTorch execution

## Implementation Status

### ✅ Completed
1. **Verified Hardware**: 8x NVIDIA RTX A6000 GPUs (49GB VRAM each), driver 575.57.08
2. **Confirmed Conda Availability**: conda 4.10.3 working on tensor01
3. **Manual PyTorch Verification**: Modern PyTorch CUDA installation successful
4. **Python 3.6 Compatibility Fixes**: Updated subprocess calls for compatibility
5. **Comprehensive CUDA Detection**: Working with 8 GPUs detected
6. **Two-Venv Refactoring**: Centralized duplicate code successfully

### 🔄 In Progress
1. **Modified `setup_two_venv_environment()`**: Added conda detection and Python 3.9 env creation
2. **Updated `generate_two_venv_execution_commands()`**: Added conda environment parameter
3. **Configuration Updates**: Enhanced tensor01_config.yml for modern PyTorch

### ❌ Current Issue
**VENV2 Execution Failing**: After conda integration, the centralized execution commands are not properly activating/using the conda environment.

**Error**: `result_raw.pkl not found - VENV2 execution may have failed`

## Technical Implementation Details

### Changes Made to `clustrix/utils.py`:

```python
# Enhanced setup_two_venv_environment() function:
def setup_two_venv_environment(ssh_client, work_dir, requirements, config=None):
    # Check conda availability
    conda_available = False
    stdin, stdout, stderr = ssh_client.exec_command("conda --version 2>/dev/null")
    if "conda" in stdout.read().decode():
        conda_available = True
    
    if conda_available:
        # Create conda environment with Python 3.9 for VENV2
        conda_env_name = f"clustrix_venv2_{work_dir.split('/')[-1]}"
        commands.extend([
            f"conda create -n {conda_env_name} python=3.9 -y",
            f"conda activate {conda_env_name}",
            # ... install packages
        ])
        venv2_python_path = f"conda run -n {conda_env_name} python"
```

### Updated Execution Commands:
```python
def generate_two_venv_execution_commands(remote_job_dir: str, conda_env_name: str = None):
    # Handle both conda and venv activation
    activate_cmd = f"conda activate {conda_env_name}" if conda_env_name else f"source {remote_job_dir}/venv2_execution/bin/activate"
    python_cmd = f'conda run -n {conda_env_name} python' if conda_env_name else f'{remote_job_dir}/venv2_execution/bin/python'
```

## Debugging Required When Resuming

### 1. Check Job Logs
Need to examine the latest job execution logs to see where conda environment activation is failing:
```bash
# Check latest job logs on tensor01
find /tmp/clustrix_tensor01 -name "job_*" -type d | sort | tail -1
# Examine job.sh, job.out, job.err files
```

### 2. Script Generation Issue
The `generate_two_venv_execution_commands()` function needs the conda environment name, but the script generation functions don't currently have access to this information. Need to:
- Update SLURM and SSH script generation to detect conda usage
- Pass conda environment information through the execution pipeline
- Ensure proper conda activation syntax in generated scripts

### 3. Execution Environment Variables
May need to set conda environment variables or initialize conda properly in the execution scripts.

## Test Results So Far

### ✅ Working Tests
1. **CUDA Detection**: 8 GPUs detected successfully
2. **Simple GPU Detection**: Basic hardware detection working
3. **SLURM Integration**: ndoli cluster working after refactor
4. **SSH Integration**: tensor01 connection and basic execution working

### ❌ Failing Tests  
1. **Single GPU Computation**: VENV2 execution failing with conda changes
2. **Dual GPU Computation**: Same VENV2 execution issue

## Next Session Priorities

### Immediate (High Priority)
1. **Debug Conda Execution**: Fix VENV2 conda environment activation
2. **Update Script Generation**: Properly pass conda environment info to execution commands
3. **Test PyTorch Import**: Verify PyTorch loads correctly in conda VENV2
4. **Single GPU Test**: Get basic GPU computation working

### Follow-up (Medium Priority)
1. **Dual GPU Tests**: Multi-GPU PyTorch computation
2. **Performance Benchmarks**: Compare CPU vs GPU computation times
3. **Memory Usage Tests**: GPU memory allocation and cleanup

## Configuration Files Updated

### `tensor01_config.yml`
```yaml
module_loads:
  - "python"
  - "cuda"  # Added for CUDA toolkit access

cluster_packages:
  - package: "torch torchvision torchaudio"
    pip_args: "--index-url https://download.pytorch.org/whl/cu118"
    timeout: 600
```

## Key Learnings

1. **Conda is Available**: tensor01 has conda, enabling modern Python environments
2. **Hardware is Excellent**: 8x RTX A6000 GPUs with proper CUDA drivers
3. **Two-Venv Architecture Works**: Can use different Python versions for different purposes
4. **PyTorch CUDA Confirmed**: Modern PyTorch with CUDA support verified working manually

## Repository Status

**Latest Commit**: `4c28367` - "WIP: Implement conda support for VENV2 to enable modern Python + PyTorch CUDA"

**Files Modified**:
- `clustrix/utils.py`: Enhanced two-venv setup with conda support
- `tensor01_config.yml`: Updated for CUDA module loading

**Tests Status**:
- All previous functionality maintained (ndoli SLURM working)
- tensor01 GPU detection working
- Single/dual GPU tests blocked on conda execution fix

---

**Resume Point**: Debug and fix conda environment activation in VENV2 execution scripts to enable modern PyTorch CUDA functionality.