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
```bash
# Format code
black clustrix/

# Run linter
flake8 clustrix/

# Type checking
mypy clustrix/

# Run tests (when tests are added)
pytest
pytest --cov=clustrix  # with coverage
```

## Architecture

### Core Components

1. **`@cluster` Decorator** (`clustrix/decorator.py`): Main user interface for marking functions for remote execution. Supports resource specification and automatic loop parallelization.

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