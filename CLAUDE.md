# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Clustrix is a Python distributed computing framework that enables seamless execution of Python functions on remote clusters (SLURM, PBS, SGE, Kubernetes, SSH) using a simple `@cluster` decorator.

## Development Commands

### Setup
```bash
# Install package with development dependencies
pip install -e ".[dev]"

# Install with Kubernetes support
pip install -e ".[kubernetes,dev]"
```

### Code Quality

**CRITICAL: Always run these checks before committing or pushing code:**

```bash
# Use the comprehensive quality check script (recommended)
python scripts/check_quality.py

# Or run individual checks:
black clustrix/          # Format code
flake8 clustrix/         # Run linter  
mypy clustrix/           # Type checking
pytest --cov=clustrix    # Run tests with coverage
```

**Pre-commit hooks are installed** - they will automatically run black, flake8, and mypy before each commit. If any check fails, the commit will be blocked until fixed.

**Never push without running quality checks** - GitHub Actions will fail if code doesn't pass black, flake8, and mypy.

## Architecture

### Core Components

1. **`@cluster` Decorator** (`clustrix/decorator.py`): Main user interface for marking functions for remote execution. Supports resource specification and automatic loop parallelization.
   
   **⚠️ REPL Limitation**: Functions defined interactively in the Python REPL cannot be serialized because `inspect.getsource()` cannot access their source code. This affects interactive Python sessions and some notebook environments. Functions must be defined in `.py` files or environments where source code is preserved.

2. **ClusterExecutor** (`clustrix/executor.py`): Central execution engine handling:
   - Job submission to different schedulers (SLURM, PBS, SGE, Kubernetes, SSH)
   - SSH connection management via Paramiko
   - File transfer via SFTP
   - Job monitoring and result collection
   - Remote environment setup

3. **Configuration System** (`clustrix/config.py`): Singleton configuration management supporting:
   - YAML/JSON file loading
   - Hierarchical configuration (defaults → file → runtime)
   - Standard location discovery (`~/.clustrix/`, `/etc/clustrix/`)

4. **Utilities** (`clustrix/utils.py`): 
   - Function serialization using cloudpickle/dill
   - Environment capture and replication
   - AST-based loop detection for parallelization
   - Cluster-specific job script generation

5. **Filesystem Utilities** (`clustrix/filesystem.py`): Unified filesystem operations for local and remote clusters:
   - **ClusterFilesystem class**: Core implementation handling local and SSH-based remote operations
   - **Convenience functions**: `cluster_ls()`, `cluster_find()`, `cluster_stat()`, `cluster_exists()`, `cluster_isdir()`, `cluster_isfile()`, `cluster_glob()`, `cluster_du()`, `cluster_count_files()`
   - **Data classes**: `FileInfo` for file metadata, `DiskUsage` for directory usage statistics
   - **Automatic connection management**: SSH connections handled transparently
   - **Config-driven**: Uses `ClusterConfig` to determine local vs remote operations

### Key Design Patterns

- **Strategy Pattern**: Different cluster types handled via specific submission methods
- **Decorator Pattern**: Clean API through `@cluster` decorator
- **Singleton Pattern**: Global configuration instance
- **Template Pattern**: Job script generation with cluster-specific variations

### Workflow

1. User decorates function with `@cluster`
2. Function and dependencies serialized with cloudpickle
3. Job submitted to cluster:
   - Remote directory created
   - Serialized data uploaded
   - Python environment setup (venv/conda)
   - Job script generated and submitted
4. Remote execution with unpickling
5. Results polled and downloaded

## Important Considerations

- The project is in beta (v0.1.0) and lacks test coverage
- SSH-based clusters require proper key setup or password authentication
- Remote environments are recreated based on local pip freeze output
- Job scripts are bash-based with scheduler-specific directives
- Results communicated via pickled files (result.pkl/error.pkl)
- Automatic cleanup of remote files configurable via `cleanup_remote_files`

## Common Tasks

### Adding New Cluster Type Support
1. Add new cluster type to `ClusterType` enum in `config.py`
2. Implement `_submit_{type}_job` method in `ClusterExecutor`
3. Add status checking logic to `get_job_status`
4. Update job script generation in `utils.py` if needed

### Using Filesystem Utilities
```python
from clustrix import cluster_ls, cluster_find, cluster_stat, cluster_exists, cluster_glob
from clustrix.config import ClusterConfig

# Configure for local or remote operations
config = ClusterConfig(
    cluster_type="slurm",  # or "local" for local operations
    cluster_host="cluster.edu",
    username="researcher",
    remote_work_dir="/scratch/project"
)

# List directory contents (works locally and remotely)
files = cluster_ls("data/", config)

# Find files by pattern
csv_files = cluster_find("*.csv", "datasets/", config)

# Check file existence
if cluster_exists("results/output.json", config):
    print("Results already computed!")

# Get file information
file_info = cluster_stat("large_dataset.h5", config)
print(f"Dataset size: {file_info.size / 1e9:.1f} GB")

# Use with @cluster decorator for data-driven workflows
@cluster(cores=8)
def process_datasets(config):
    # Find all data files on the cluster
    data_files = cluster_glob("*.csv", "input/", config)
    
    results = []
    for filename in data_files:  # Loop gets parallelized automatically
        # Check file size before processing
        file_info = cluster_stat(filename, config)
        if file_info.size > 100_000_000:  # Large files
            result = process_large_file(filename, config)
        else:
            result = process_small_file(filename, config)
        results.append(result)
    
    return results
```

### Debugging Remote Execution
- Check remote logs in `~/.clustrix/jobs/{job_id}/`
- Enable SSH debug mode in Paramiko connection
- Verify environment setup with `pip freeze` comparison
- Check scheduler-specific logs (SLURM: slurm-*.out)

### Configuration Priority
1. Runtime parameters (highest priority)
2. Configuration file (`clustrix.yml`)
3. Environment variables
4. Default values (lowest priority)

## ⚠️ MANDATORY PRE-COMMIT WORKFLOW ⚠️

**EVERY SINGLE TIME before committing/pushing:**

1. **Run quality checks repeatedly**: `python scripts/pre_push_check.py`
   - This runs black (formatting), flake8 (linting), mypy (type checking), and pytest (tests)
   - **It automatically retries up to 5 times** until ALL checks pass
   - Black may auto-fix formatting issues, so subsequent runs check if everything is clean
2. **Only commit and push when ALL checks pass**

**CRITICAL: You must run checks repeatedly until they ALL pass. Don't just run once!**

**Pre-commit hooks will block commits that fail quality checks.**

The GitHub Actions CI will fail if code doesn't pass black, flake8, mypy, and pytest. Always run the full cycle locally first.

## Testing Guidelines

### Test Organization

**Unit Tests** (`tests/` excluding `real_world/`):
- Run in GitHub Actions CI
- Mock external dependencies  
- Fast execution, no external resources required
- Included in coverage reports

**Real-World Tests** (`tests/real_world/`):
- **Excluded from GitHub Actions** due to SSH/credential requirements
- **Run automatically in pre-push hook** when credentials are available
- Test actual cluster functionality, API calls, SSH connections
- Marked with `@pytest.mark.real_world`

### Pre-Push Hook Workflow

A custom pre-push hook (`.git/hooks/pre-push`) automatically runs real-world tests when:
1. Cluster credentials are available (1Password, environment variables, etc.)
2. Real-world test runner script exists

**Hook behavior:**
- ✅ **With credentials**: Runs all real-world tests and blocks push on failure
- ⚠️ **Without credentials**: Skips with warning, allows push
- 🔧 **Script missing**: Falls back to basic pytest real-world test run

### Running Tests Manually

```bash
# All unit tests (CI-compatible)
pytest tests/ -m "not real_world"

# Real-world tests only
pytest tests/real_world/ -m real_world

# Using real-world test runner (when available)
python scripts/run_real_world_tests.py --filesystem
python scripts/run_real_world_tests.py --ssh  
python scripts/run_real_world_tests.py --api
python scripts/run_real_world_tests.py --visual
```

### External Function Validation

- **External Function Validation**: When testing external functions and features (i.e., anything that we cannot directly test locally), we *MUST* validate that those functions work (without resorting to local fallbacks) at least once before we can check the issue off as completed. Use GitHub issue spec criteria and comments to track what has been validated. We can store API keys, usernames, and/or passwords locally (if we can do so securely) in order to enable this. Once we have verified functionality (again, WITHOUT resorting to fallbacks), we can check off that functionality as "tested" and then use mocked functions or objects in pytests to avoid incurring excessive API fees.