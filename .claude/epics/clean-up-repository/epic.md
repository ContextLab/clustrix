---
name: clean-up-repository
status: completed
created: 2025-08-23T03:33:46Z
progress: 100%
prd: .claude/prds/clean-up-repository.md
github: https://github.com/ContextLab/clustrix/issues/73
---

# Epic: clean-up-repository

## Overview

Technical implementation to transform the Clustrix repository from its current cluttered state into a clean, well-organized Python project following best practices. This epic focuses on automated file migration, content preservation through GitHub issues, and establishing preventive measures to maintain repository health.

## Architecture Decisions

### File Organization Strategy
- **Decision**: Adopt standard Python project structure (src/, tests/, docs/, scripts/)
- **Rationale**: Industry standard, improves discoverability, enables better tooling integration
- **Pattern**: Group tests by functional area rather than mirroring source structure

### Content Preservation Approach
- **Decision**: Use GitHub issues as archive for valuable content from deleted files
- **Rationale**: Maintains searchability, creates actionable items, preserves history
- **Tooling**: Automated script to extract TODOs/FIXMEs and create issues with proper labels

### Automation First
- **Decision**: Build cleanup scripts that can be rerun as needed
- **Rationale**: Prevents regression, enables incremental cleanup, documents decisions
- **Implementation**: Python scripts using pathlib and git-python for safe operations

### Testing Strategy
- **Decision**: Verify all tests pass after each migration phase
- **Rationale**: Ensures no functionality breaks during reorganization
- **Approach**: Run full test suite after each batch of moves, fix import paths programmatically

## Technical Approach

### Automation Scripts
- **File Migration Script**: Bulk move files based on patterns with import fixing
- **Content Extraction Script**: Parse markdown/Python files for TODOs, create GitHub issues
- **Import Fixer**: Update all import statements after file moves
- **Test Validator**: Ensure all tests discoverable and passing post-migration

### Directory Structure
```
clustrix/
├── clustrix/           # Source code (existing)
├── tests/              # All test files (organized by category)
│   ├── unit/
│   ├── integration/
│   ├── real_world/
│   └── fixtures/
├── docs/               # Documentation (existing)
├── scripts/            # Utility scripts only (< 10 files)
└── .github/            # CI/CD and templates
```

### Quality Gates
- Pre-commit hooks to prevent future accumulation
- .gitignore patterns for all temporary file types
- CI checks for file size limits and structure compliance

## Implementation Strategy

### Phase-Based Approach
1. **Automated Content Extraction**: Script to preserve valuable content
2. **Bulk File Migration**: Automated moves with import fixing
3. **Directory Cleanup**: Remove empty directories and artifacts
4. **Validation & Testing**: Ensure everything still works
5. **Prevention Setup**: Hooks and automation to maintain cleanliness

### Risk Mitigation
- Create full repository backup before starting
- Test migration scripts on a branch first
- Preserve file history through git mv operations
- Run operations in small, reversible batches

## Task Breakdown Preview

High-level tasks to be created (targeting ≤10 total tasks):

- [ ] **Task 1: Content Preservation Automation** - Script to extract valuable content and create GitHub issues
- [ ] **Task 2: Test File Migration** - Move 80+ test files to tests/ with import fixing
- [ ] **Task 3: Scripts Folder Cleanup** - Identify and migrate test files from scripts/
- [ ] **Task 4: Documentation Consolidation** - Move essential docs, remove redundant files
- [ ] **Task 5: Directory Removal** - Clean up notes/, backup dirs, coverage files
- [ ] **Task 6: Large File Refactoring** - Break down files >600 lines into modules
- [ ] **Task 7: Import & Reference Updates** - Fix all imports and cross-references
- [ ] **Task 8: Git Configuration** - Update .gitignore and add pre-commit hooks
- [ ] **Task 9: CI/CD Updates** - Adjust paths in GitHub Actions workflows
- [ ] **Task 10: Final Validation** - Run full test suite, update README

## Dependencies

### External Tools
- GitHub CLI (gh) for issue creation
- Python AST module for import fixing
- Pre-commit framework for hooks
- Black, flake8, mypy for code quality

### Internal Requirements
- All current tests must pass before starting
- Backup of repository required
- Team agreement on new structure

## Success Criteria (Technical)

### Quantitative Metrics
- **File Count**: Root directory files reduced from 100+ to <10
- **Test Organization**: 100% of tests in tests/ directory
- **Script Reduction**: Scripts/ folder reduced from 41 to <10 files
- **Code Size**: No Python file exceeds 600 lines
- **CI Performance**: 15% faster pipeline execution

### Qualitative Metrics
- All tests passing with no import errors
- Clean git status after fresh clone
- New contributor can understand structure in <5 minutes
- No duplicate or orphaned files

## Estimated Effort

### Timeline
- **Total Duration**: 5-7 days of focused work
- **Parallelizable**: Tasks 1-4 can run concurrently
- **Critical Path**: Task 2 (test migration) → Task 7 (import fixes) → Task 10 (validation)

### Resource Requirements
- 1 developer for implementation
- Code review from 1-2 team members
- ~2 hours of team time for structure agreement

### Complexity Assessment
- **Low Complexity**: File moves, deletions, .gitignore updates
- **Medium Complexity**: Import fixing, test organization
- **High Complexity**: Large file refactoring (can be deferred if needed)

## Implementation Notes

### Key Principles
1. **Preserve Git History**: Use git mv instead of delete/add
2. **Maintain Functionality**: Never break existing features
3. **Incremental Progress**: Each task leaves repo in working state
4. **Automate Everything**: Scripts for repeatability and documentation

### Simplification Opportunities
- Use existing tools (black, isort) instead of custom formatting
- Leverage pytest's automatic test discovery
- Defer large file refactoring if it risks stability
- Combine related small files instead of just moving them

This epic prioritizes practical improvements over perfection, focusing on the highest-impact changes that can be completed reliably within a week.

## Tasks Created
- [x] #74 - Content Preservation Automation (parallel: true) ✅
- [x] #75 - Test File Migration (parallel: true) ✅
- [x] #76 - Scripts Folder Cleanup (parallel: true) ✅
- [x] #78 - Documentation Consolidation (parallel: true) ✅
- [x] #79 - Directory Removal (parallel: false) ✅
- [x] #80 - Large File Refactoring (parallel: true) ✅
- [x] #81 - Import & Reference Updates (parallel: false) ✅
- [x] #82 - Git Configuration (parallel: false) ✅
- [x] #83 - CI/CD Updates (parallel: false) ✅
- [x] #84 - Final Validation (parallel: false) ✅

Total tasks: 10
Parallel tasks: 5 (can be worked on simultaneously)
Sequential tasks: 5 (have dependencies)
Estimated total effort: 52 hours (~6.5 days)
