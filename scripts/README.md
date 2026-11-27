# Clustrix Scripts Directory

This directory contains essential utility scripts for development and maintenance of the Clustrix project. All test files have been moved to the appropriate `tests/` subdirectories.

## Utility Scripts

### Code Quality and Formatting

#### `check_quality.py` 
**Purpose**: Comprehensive code quality checker  
**Usage**: `python scripts/check_quality.py`  
**Features**: 
- Runs black, flake8, mypy, and pytest
- Provides clear pass/fail status with colored output
- Generates coverage reports and badge URLs
- Used by CI/CD pipeline for quality gates

#### `pre_push_check.py`
**Purpose**: Pre-push validation script with automatic retry  
**Usage**: `python scripts/pre_push_check.py`  
**Features**:
- Runs all quality checks repeatedly until they pass (max 5 attempts)
- Automatically applies black formatting
- Prevents pushing code that will fail GitHub Actions
- **CRITICAL**: Run before every push to avoid CI failures

#### `auto_format_and_check.py`
**Purpose**: Automated formatting with Git commit integration  
**Usage**: `python scripts/auto_format_and_check.py`  
**Features**:
- Auto-applies black formatting
- Commits formatting changes with proper message
- Runs full quality check suite
- Pushes changes automatically


#### `generate_badges.py`
**Purpose**: README badge generator for project status  
**Usage**: `python scripts/generate_badges.py [--run-coverage] [--check-linting]`  
**Features**:
- Generates shield.io badge URLs
- Updates README.md with current status
- Coverage percentage from `coverage.json`
- Supports multiple badge types (tests, coverage, style, etc.)

#### `run_real_world_tests.py` 
**Purpose**: Runs the suites under `tests/real_world` by category and exits non-zero if one fails  
**Usage**: `python scripts/run_real_world_tests.py [options]`  
**Categories**:
- `--filesystem`: the filesystem helpers on this machine
- `--ssh`: `@cluster` against a real sshd and SLURM; skips unless `CLUSTRIX_TEST_SSH_HOST`, `_USER` and `_KEY` name one (`scripts/setup_local_slurm.sh` creates it)
- `--all`: everything under `tests/real_world`
- `--unit`: everything outside it
- `--check-creds`: report which credentials the suite can find

### Setup and Configuration

Credential setup lives in the CLI, not in a script here:

```bash
clustrix credentials setup    # interactive wizard, writes ~/.clustrix/.env
clustrix credentials test     # validates each configured credential for real
```

`setup_validation_credentials.py` used to duplicate this and was deleted: it
gated everything behind a 1Password CLI check, and 1Password support was
removed in #97, so `is_op_available()` returns `False` unconditionally. Every
run exited at that check and told the reader to install a CLI clustrix no
longer uses.

## Workflow Integration

### Pre-commit Workflow
```bash
# Install pre-commit hooks (one-time setup)
pip install pre-commit
pre-commit install

# Manual pre-push checks (recommended)
python scripts/pre_push_check.py
```

### Quality Assurance Workflow
```bash
# Check current code quality
python scripts/check_quality.py

# Auto-fix formatting issues
python scripts/auto_format_and_check.py  

# Generate updated badges
python scripts/generate_badges.py --run-coverage
```

### Real-World Testing Workflow
```bash
# Stand up the cluster the --ssh tests run against (one-time, as root)
sudo scripts/setup_local_slurm.sh

# Run specific test categories
python scripts/run_real_world_tests.py --filesystem
python scripts/run_real_world_tests.py --ssh

# Run all tests
python scripts/run_real_world_tests.py --all
```

## File Organization

### Moved Test Files
All test files have been reorganized into the `tests/` directory:

- **tests/real_world/**: tests that run against a real sshd and SLURM
  controller (`scripts/setup_local_slurm.sh` creates one); see
  `docs/REAL_WORLD_TESTING.md`

### Scripts Removed
The following categories of files were removed during cleanup:
- Duplicate test files
- Experimental/prototype scripts  
- Outdated debug utilities
- Redundant validation scripts

## Security Considerations

- **Credential Management**: All sensitive credentials are managed through environment variables
- **Environment Isolation**: Real-world tests run in isolated environments
- **Access Control**: API keys have minimal required permissions
- **Secret Scanning**: No credentials are stored in repository files

## Contributing

When adding new scripts:

1. **Utilities Only**: Only add essential development/maintenance utilities
2. **Documentation**: Update this README with purpose, usage, and features
3. **Security Review**: Ensure no credentials or sensitive data in scripts
4. **Test Integration**: Add appropriate tests in `tests/` directory
5. **Quality Checks**: Run `python scripts/pre_push_check.py` before committing

## Dependencies

Most scripts depend on:
- **Core**: `clustrix` package installed in development mode
- **Quality Tools**: `black`, `flake8`, `mypy`, `pytest`
- **External Services**: For real-world testing only
- **Environment Variables**: For secure credential management

Install development dependencies:
```bash
pip install -e ".[dev]"
```