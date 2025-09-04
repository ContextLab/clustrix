---
issue: 97
title: remove 1password
analyzed: 2025-08-25T03:13:03Z
estimated_hours: 6
parallelization_factor: 3.0
---

# Parallel Work Analysis: Issue #97

## Overview
Remove all 1Password integration from the codebase since it requires manual interaction and has been replaced with more convenient .env files. This involves removing code, configuration options, UI elements, and documentation.

## Parallel Streams

### Stream A: Core Code Removal
**Scope**: Remove 1Password authentication methods and configuration
**Files**:
- `clustrix/auth_methods.py` - Remove OnePasswordAuthMethod class
- `clustrix/config.py` - Remove use_1password and onepassword_note fields
- `clustrix/auth_manager.py` - Remove all 1Password integration logic
- `clustrix/validation.py` - Remove validate_1password_integration function
- `clustrix/secure_credentials.py` - Remove 1Password CLI commands
**Agent Type**: backend-specialist
**Can Start**: immediately
**Estimated Hours**: 3
**Dependencies**: none

### Stream B: UI/Widget Cleanup
**Scope**: Remove 1Password options from notebook widgets and user interfaces
**Files**:
- `clustrix/modern_notebook_widget.py` - Remove 1Password checkbox and UI elements
- `clustrix/enhanced_notebook_widget.py` - Remove 1Password widgets and handlers
- Any other widget files with 1Password UI components
**Agent Type**: frontend-specialist
**Can Start**: immediately
**Estimated Hours**: 2
**Dependencies**: none

### Stream C: Configuration & Documentation
**Scope**: Remove 1Password references from configs, tests, and documentation
**Files**:
- `tests/test_config.yml` - Remove 1Password config options
- `ndoli_config.yml` - Remove use_1password settings
- `tensor01_config.yml` - Remove use_1password settings
- `docs/TECHNICAL_DESIGN_AUTH_ENHANCEMENT.md` - Remove 1Password sections
- `docs/CREDENTIAL_SETUP.md` - Remove 1Password setup instructions
- `docs/ssh_key_automation_*.md` - Remove 1Password references
- `CLAUDE.md` - Remove 1Password documentation
- `scripts/README.md` - Remove 1Password usage notes
**Agent Type**: documentation-specialist
**Can Start**: immediately
**Estimated Hours**: 1
**Dependencies**: none

## Coordination Points

### Shared Files
No files require modification by multiple streams - each stream works on distinct file sets.

### Sequential Requirements
All streams can run in parallel as they work on independent components:
1. Core code removal (Stream A)
2. UI cleanup (Stream B) 
3. Config/docs cleanup (Stream C)

## Conflict Risk Assessment
- **Low Risk**: Streams work on completely different files and components
- **No shared dependencies**: Each stream modifies distinct file sets
- **Clean separation**: Authentication code, UI widgets, and documentation are well-separated

## Parallelization Strategy

**Recommended Approach**: parallel

Launch all three streams simultaneously. No coordination required as file sets don't overlap.

## Expected Timeline

With parallel execution:
- Wall time: 3 hours (longest stream)
- Total work: 6 hours
- Efficiency gain: 50%

Without parallel execution:
- Wall time: 6 hours

## Implementation Details

### Stream A Tasks:
1. Remove `OnePasswordAuthMethod` class from `auth_methods.py`
2. Remove `use_1password` and `onepassword_note` from `ClusterConfig` in `config.py`
3. Remove all 1Password logic from `auth_manager.py`
4. Remove `validate_1password_integration()` from `validation.py`
5. Remove 1Password CLI commands from `secure_credentials.py`
6. Update any imports that reference removed components

### Stream B Tasks:
1. Remove 1Password checkbox widgets from notebook interfaces
2. Remove 1Password event handlers and validation
3. Remove 1Password help text and UI labels
4. Update widget layout code to remove 1Password sections
5. Test widget functionality without 1Password options

### Stream C Tasks:
1. Remove 1Password config options from YAML files
2. Remove 1Password setup instructions from documentation
3. Remove 1Password CLI installation guidance
4. Update authentication flow documentation
5. Remove 1Password examples from README/guides

## Testing Strategy
- **Stream A**: Unit tests for authentication without 1Password
- **Stream B**: Widget functionality tests
- **Stream C**: Configuration validation tests

## Notes
- This is a pure removal task with no new functionality
- No database migrations required
- No API changes required
- Focus on clean removal without breaking existing .env-based authentication
- Verify all authentication still works through environment variables after removal