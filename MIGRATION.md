# Migration Guide: Repository Reorganization

This guide helps developers adapt to the new repository structure introduced in the clean-up-repository epic.

## Overview

The Clustrix repository has been reorganized from a cluttered structure to a clean, standards-compliant Python project layout. This migration improves maintainability, enables better tooling integration, and follows Python packaging best practices.

## What Changed

### Before: Cluttered Repository
- 100+ files in root directory
- Tests scattered throughout different locations  
- Duplicate and orphaned files
- Large monolithic modules (>2000 lines)
- Mixed content types in single directories

### After: Clean Organization
- **Root Directory**: Only essential project files (README, setup.py, etc.)
- **tests/**: All tests organized by category
- **clustrix/**: Modular source code with focused responsibilities
- **docs/**: Comprehensive documentation structure
- **scripts/**: Essential utility scripts only
- **Git History**: Preserved through `git mv` operations

## Directory Structure Changes

### Test Organization
```diff
- Old: Tests in root, scripts/, and various subdirectories
+ New: Organized test structure
```

**New Test Structure:**
```
tests/
├── unit/              # Fast, isolated unit tests (run in CI)
├── integration/       # Integration tests (run in CI)  
├── real_world/        # Tests requiring cluster access
├── comprehensive/     # Performance and edge case tests
└── infrastructure/    # Test infrastructure setup
```

### Source Code Refactoring
Large modules have been broken into focused components:

**notebook_magic.py** (2883 lines → 5 modules):
- `notebook_magic.py` (88 lines) - Main entry point
- `notebook_magic_config.py` (213 lines) - Configuration handling
- `notebook_magic_core.py` (74 lines) - Core magic functionality
- `notebook_magic_mocks.py` (171 lines) - Mock objects
- `notebook_magic_widget.py` (1977 lines) - Widget implementation

**executor.py** (2362 lines → 7 modules):
- `executor.py` (39 lines) - Main interface
- `executor_core.py` (466 lines) - Core execution logic
- `executor_connections.py` (390 lines) - Connection management
- `executor_schedulers.py` (378 lines) - Scheduler interfaces
- `executor_scheduler_status.py` (651 lines) - Status monitoring
- `executor_kubernetes.py` (461 lines) - Kubernetes integration
- `executor_cloud.py` (470 lines) - Cloud provider support

## Developer Impact

### Import Changes
**All imports remain backward compatible.** The refactoring maintained public APIs:

```python
# These imports continue to work unchanged
from clustrix import cluster, configure
from clustrix import ClusterConfig
from clustrix.filesystem import cluster_ls, cluster_find
```

### Test Discovery
**Pytest configuration updated** to properly discover tests:

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests/unit", "tests/integration"]
markers = [
    "real_world: marks tests as real world tests",
    "slow: marks tests as slow",
    "unit: marks tests as unit tests", 
    "integration: marks tests as integration tests",
]
```

### CI/CD Updates
**GitHub Actions workflows updated** for new structure:
- Test paths fixed to use `tests/unit/` and `tests/integration/`
- Coverage reporting configured for new layout
- Documentation build paths updated
- Real world test paths corrected

### Development Workflow Changes

#### Before
```bash
# Old workflow (no longer works)
pytest .                    # Tests scattered everywhere
python some_script.py       # Scripts mixed with tests
```

#### After  
```bash
# New workflow
pytest tests/unit/ tests/integration/     # Run CI tests
python scripts/check_quality.py          # Quality validation
python scripts/run_real_world_tests.py   # Real world tests
```

## Migration Steps for Contributors

### 1. Pull Latest Changes
```bash
git pull origin master
```

### 2. Update Development Environment
```bash
# Reinstall in development mode
pip install -e ".[dev,test]"
```

### 3. Verify Setup
```bash
# Test imports work
python -c "import clustrix; from clustrix import cluster; print('✅ Imports working')"

# Test CLI works  
clustrix --help

# Run quick test
pytest tests/unit/test_dartmouth_network_detection.py -v
```

### 4. Update Bookmarks/Scripts
- **Tests**: Use `tests/unit/` and `tests/integration/` instead of root directory
- **Quality Checks**: Use `python scripts/check_quality.py` 
- **Documentation**: Build with `cd docs && make html`

## Key Benefits

### For Developers
- **Faster Test Discovery**: Focused test directories reduce collection time
- **Better IDE Support**: Standard structure enables better code navigation
- **Clearer Separation**: Unit vs integration vs real-world tests clearly distinguished
- **Improved Tooling**: Better support from pytest, coverage, and linting tools

### For New Contributors
- **Intuitive Structure**: Standard Python project layout
- **Clear Entry Points**: Easy to understand where different functionality lives
- **Better Documentation**: Comprehensive guides and examples
- **Reduced Confusion**: No more duplicate or orphaned files

### For Maintainers
- **Modular Code**: Smaller, focused modules easier to maintain
- **Better Testing**: Clear test categorization enables better CI/CD
- **Reduced Technical Debt**: Cleanup removed 76MB of unnecessary files
- **Prevention**: Git configuration prevents future accumulation

## Troubleshooting

### Import Errors
If you encounter import errors:
```bash
# Reinstall package
pip uninstall clustrix
pip install -e ".[dev]"
```

### Test Discovery Issues
If pytest can't find tests:
```bash
# Verify pytest configuration
pytest --collect-only tests/unit/
pytest --collect-only tests/integration/
```

### Path Issues
If scripts can't find files:
- Update paths to use new structure
- Check that you're running from repository root
- Verify working directory in scripts

### Git Issues
If you have local changes conflicting with reorganization:
```bash
# Stash local changes
git stash

# Pull latest 
git pull origin master

# Apply stash (resolve conflicts if any)
git stash pop
```

## Support

If you encounter issues with the migration:

1. **Check this guide** for common solutions
2. **Search existing issues** on GitHub for similar problems  
3. **Create a new issue** with details about your specific problem
4. **Tag issues** with `migration` label for quick response

## Validation

After migration, verify everything works:

```bash
# Run comprehensive validation
python scripts/check_quality.py

# Test core functionality
python -c "
from clustrix import cluster, configure
configure(cluster_host=None)  # Local execution

@cluster(cores=1)
def test():
    return 'success'

result = test()
print(f'✅ Migration successful: {result}')
"
```

The migration is successful when all quality checks pass and core functionality works without errors.