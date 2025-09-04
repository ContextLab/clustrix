---
issue: 72
title: clean up repository
analyzed: 2025-08-24T21:16:42Z
estimated_hours: 8
parallelization_factor: 3.0
---

# Parallel Work Analysis: Issue #72

## Overview
Comprehensive cleanup of repository root directory to remove/migrate 30+ extraneous files and directories. Current state has 14 Python scripts, 6 notebooks, 9 config files, 2 log files, and 12 directories that need review and action.

## Current Inventory Analysis

### Python Files (14 files) - PRIORITY 1
**Debug Scripts (4 files):**
- `debug_cluster_execution.py` (1.2KB) - Debug utilities
- `debug_cluster_test.py` (3.6KB) - Test debugging
- `debug_slurm_logs.py` (2.7KB) - SLURM log analysis
- `debug_venv2_execution.py` (6.1KB) - Environment debugging

**Check Scripts (4 files):**
- `check_python_versions.py` (3.2KB) - Python version verification
- `check_remote_logs.py` (6.6KB) - Remote log analysis
- `check_slurm_results.py` (2.6KB) - SLURM result checking
- `check_tensor01_status.py` (3.0KB) - Tensor01 cluster status

**Test/Analysis Scripts (4 files):**
- `user_analysis.py` (1.7KB) - User workflow analysis
- `user_workflow_test.py` (6.4KB) - Workflow testing
- `working_functions.py` (1.5KB) - Function testing
- `cleanup_test_resources.py` (3.9KB) - Resource cleanup

**Infrastructure Scripts (2 files):**
- `destroy_cluster.py` (6.7KB) - Cluster teardown
- `fix_notebooks.py` (3.8KB) - Notebook repair utilities

### Notebook Files (6 files) - PRIORITY 2
**Test Notebooks:**
- `clustrix_demo.ipynb` (28KB) - Main demo (KEEP - move to docs/notebooks/)
- `test_ssh_setup.ipynb` (35KB) - SSH setup testing (DELETE)
- `test_enhanced_widget.ipynb` (8KB) - Widget testing (DELETE)
- `test_modern_widget_notebook.ipynb` (6KB) - Widget testing (DELETE)
- `test_widget_colab.ipynb` (4KB) - Colab testing (DELETE)
- `colab_test_modern_widget.ipynb` (6KB) - Colab testing (DELETE)

### Configuration Files (9 files) - PRIORITY 3
**Cluster Configs (3 files):**
- `clustrix.yml` (403B) - Main config (EVALUATE - possibly example)
- `ndoli_config.yml` (789B) - Personal config (DELETE)
- `tensor01_config.yml` (2.5KB) - Personal config (DELETE)
- `test_config.yml` (2.7KB) - Test config (MOVE to tests/)

**Build/Coverage Files (5 files):**
- `.ccpm_tracking.json` (232B) - CCPM tracking (DELETE)
- `coverage.json` (278KB) - Coverage report (DELETE - generated file)
- `.coverage` (70KB) - Coverage data (DELETE - generated file)
- `eks_user_policy.json` (1.2KB) - AWS policy (MOVE to docs/aws/)

**Other Config:**
- `pytest.ini` (863B) - Test config (KEEP - project config)

### Log Files (2 files) - PRIORITY 4
- `aws_provision_test.log` (118KB) - AWS test logs (DELETE)
- `jupyter.log` (17KB) - Jupyter logs (DELETE)

### Script Files (2 files) - PRIORITY 3
- `add_eks_user_policy.sh` (2.1KB) - AWS setup (MOVE to docs/aws/)
- `setup_aws_permissions.sh` (4.2KB) - AWS setup (MOVE to docs/aws/)

### Directories (12 non-hidden) - PRIORITY 2
**Generated/Cache Directories (7 dirs):**
- `.checkpoints/` - CCPM checkpoints (DELETE)
- `.ipynb_checkpoints/` - Jupyter cache (DELETE)
- `build/` - Build artifacts (DELETE)
- `dist/` - Distribution files (DELETE)
- `clustrix.egg-info/` - Package metadata (DELETE)
- `htmlcov/` - Coverage HTML (DELETE)
- `test_results/` - Test artifacts (DELETE)

**Environment Directories (2 dirs):**
- `clustrix_test_env/` - Test environment (DELETE)
- `py310-test/` - Python test env (DELETE)

**Content Directories (3 dirs):**
- `examples/` - Example files (KEEP - validate and organize)
- `clustrix/` - Main package (KEEP)
- `tests/` - Test suite (KEEP)
- `docs/` - Documentation (KEEP)
- `scripts/` - Utility scripts (KEEP - already cleaned)

## Parallel Streams

### Stream A: Python Script Analysis & Migration
**Scope**: Analyze all Python files for value and migrate/delete appropriately
**Files**:
- `debug_*.py` (4 files)
- `check_*.py` (4 files)
- `user_*.py`, `working_functions.py`, `cleanup_test_resources.py` (4 files)
- `destroy_cluster.py`, `fix_notebooks.py` (2 files)
**Agent Type**: general-purpose
**Can Start**: immediately
**Estimated Hours**: 3
**Dependencies**: none
**Actions**:
- Review each script for utility vs obsolescence
- Extract any useful functionality to proper locations
- Create GitHub issues for valuable debugging patterns/insights
- Delete obsolete scripts, move useful ones to scripts/ or docs/

### Stream B: Notebook & Configuration Cleanup
**Scope**: Handle notebooks, config files, and shell scripts
**Files**:
- `*.ipynb` (6 notebooks)
- `*.yml`, `*.yaml`, `*.json` config files (9 files)
- `*.sh` script files (2 files)
**Agent Type**: general-purpose  
**Can Start**: immediately
**Estimated Hours**: 2.5
**Dependencies**: none
**Actions**:
- Move `clustrix_demo.ipynb` to `docs/notebooks/`
- Delete test notebooks (extract any valuable patterns first)
- Delete personal config files (ndoli, tensor01)
- Move AWS files to `docs/aws/` directory
- Move `test_config.yml` to `tests/`

### Stream C: Directory & Artifact Cleanup
**Scope**: Remove generated files, cache directories, and test artifacts
**Files**:
- Cache directories (`.checkpoints/`, `.ipynb_checkpoints/`, `htmlcov/`, etc.)
- Build artifacts (`build/`, `dist/`, `clustrix.egg-info/`)
- Log files (`*.log`)
- Generated files (`coverage.json`, `.coverage`, `.ccpm_tracking.json`)
- Test environments (`clustrix_test_env/`, `py310-test/`)
**Agent Type**: general-purpose
**Can Start**: immediately  
**Estimated Hours**: 1.5
**Dependencies**: none
**Actions**:
- Delete all cache and generated directories
- Delete log files and coverage artifacts
- Delete test environment directories
- Validate `examples/` directory and organize if needed

### Stream D: Content Preservation & Documentation
**Scope**: Extract valuable insights before deletion and update documentation
**Files**:
- Content extraction from scripts being deleted
- Update .gitignore patterns
- Update documentation references
**Agent Type**: general-purpose
**Can Start**: after Stream A analysis complete
**Estimated Hours**: 1
**Dependencies**: Stream A (to know what's being deleted)
**Actions**:
- Create GitHub issues for valuable debugging patterns
- Update .gitignore to prevent future accumulation
- Update README/documentation to reflect clean structure
- Verify no documentation references deleted files

## Coordination Points

### Shared Files
- `.gitignore` - Stream C & D (add patterns for prevented accumulation)
- Documentation files - Stream B & D (move files, update references)

### Sequential Requirements
1. Stream A must analyze scripts before Stream D extracts content
2. Stream B & C can run independently and simultaneously  
3. Stream D runs after A completes to preserve content before final deletion

## Conflict Risk Assessment
- **Low Risk**: Most files are independent and can be handled separately
- **Medium Risk**: .gitignore updates need coordination between streams
- **Documentation references**: Need to verify no docs reference files being deleted

## Parallelization Strategy

**Recommended Approach**: hybrid

Launch Streams A, B, C simultaneously (they work on different file types).
Start Stream D when Stream A completes its analysis phase.

## Expected Timeline

With parallel execution:
- Wall time: 3 hours (max of all streams)
- Total work: 8 hours  
- Efficiency gain: 62%

Without parallel execution:
- Wall time: 8 hours

## File Actions Summary

### DELETE (28+ files):
- All `debug_*.py`, `check_*.py` scripts (after content review)
- Test notebooks (5 files)
- Personal config files (ndoli, tensor01)  
- Log files (2 files)
- Generated files (coverage, .ccpm_tracking)
- Cache directories (7 directories)
- Test environments (2 directories)

### MOVE (4-5 files):
- `clustrix_demo.ipynb` → `docs/notebooks/`
- `eks_user_policy.json` → `docs/aws/`
- `*.sh` files → `docs/aws/`
- `test_config.yml` → `tests/`

### KEEP (core files):
- Essential project files (README, setup.py, etc.)
- Main directories (clustrix/, tests/, docs/, scripts/)

## Notes
- This cleanup will reduce repository size significantly (200+ MB of artifacts)
- Focus on preserving any useful debugging patterns or insights in GitHub issues
- Be aggressive with deletion - these are development artifacts, not production code  
- Update .gitignore to prevent future accumulation of similar files
- Priority order: Python scripts (most analysis needed) → Notebooks → Configs → Directories