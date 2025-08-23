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

#### `pre_commit_auto_format.py`
**Purpose**: Pre-commit hook utility for automatic formatting  
**Usage**: Used by Git pre-commit hooks  
**Features**:
- Lightweight formatting check for Git workflow
- Prevents commits with formatting issues
- Integrates with `.git/hooks/pre-commit`

### Project Maintenance

#### `generate_badges.py`
**Purpose**: README badge generator for project status  
**Usage**: `python scripts/generate_badges.py [--run-coverage] [--check-linting]`  
**Features**:
- Generates shield.io badge URLs
- Updates README.md with current status
- Coverage percentage from `coverage.json`
- Supports multiple badge types (tests, coverage, style, etc.)

#### `run_real_world_tests.py` 
**Purpose**: Comprehensive real-world test runner  
**Usage**: `python scripts/run_real_world_tests.py [options]`  
**Features**:
- Runs external API validation tests
- Manages SSH and cluster connectivity tests  
- Credential management integration
- Supports specific test categories (`--filesystem`, `--ssh`, `--api`, etc.)
- **Note**: Requires actual credentials and external services

### Setup and Configuration

#### `setup_validation_credentials.py`
**Purpose**: Secure credential setup using 1Password  
**Usage**: `python scripts/setup_validation_credentials.py`  
**Features**:
- Guides through 1Password vault setup
- Tests credential accessibility
- Supports multiple cloud providers (AWS, GCP, Lambda Cloud, HuggingFace)
- Fallback to environment variables
- **Security**: Uses 1Password CLI for secure credential storage

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
# Setup credentials (one-time)
python scripts/setup_validation_credentials.py

# Run specific test categories
python scripts/run_real_world_tests.py --filesystem
python scripts/run_real_world_tests.py --ssh
python scripts/run_real_world_tests.py --api

# Run all tests
python scripts/run_real_world_tests.py --all
```

## File Organization

### Moved Test Files
All test files have been reorganized into the `tests/` directory:

- **tests/real_world/cluster_validation/**: Cluster-specific test files
  - `test_slurm_*.py` - SLURM cluster tests
  - `test_ssh_*.py` - SSH connectivity tests  
  - `test_*_comprehensive.py` - Comprehensive cluster tests

- **tests/real_world/api_validation/**: External API validation tests
  - `validate_*.py` - Service-specific validation scripts
  - `debug_*.py` - API debugging utilities

- **tests/real_world/validation/**: Shell scripts and validation utilities
  - `*.sh` - Bash test scripts
  - `*.json` - Test reports and configurations

### Scripts Removed
The following categories of files were removed during cleanup:
- Duplicate test files
- Experimental/prototype scripts  
- Outdated debug utilities
- Redundant validation scripts

## Security Considerations

- **Credential Management**: All sensitive credentials are managed through 1Password CLI
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
- **1Password CLI**: For secure credential management

Install development dependencies:
```bash
pip install -e ".[dev]"
```