## Problem

The clustrix filesystem module uses Python 3.7+ `dataclasses` but some clusters (e.g., ndoli.dartmouth.edu SSH) run Python 3.6.8, causing:

```
No module named 'dataclasses'
```

## Evidence

**Failing Test Scripts:**
- `scripts/test_ssh_packaging_with_1password.py` (filesystem/complex tests)
- `scripts/test_slurm_packaging_jobs.py` (filesystem/complex tests when paramiko issue is resolved)

**Error Location:**
- `clustrix/filesystem.py` imports dataclasses for FileInfo, DiskUsage classes
- Remote execution fails when extracting packages with filesystem utilities

**Clusters Affected:**
- SSH: ndoli.dartmouth.edu (Python 3.6.8)
- Potentially other older cluster environments

## Solutions

### Option 1: Compatibility Layer (Recommended)
```python
try:
    from dataclasses import dataclass
except ImportError:
    # Python 3.6 compatibility
    def dataclass(cls):
        return cls
```

### Option 2: Remove dataclasses dependency
- Replace dataclass objects with regular classes
- Maintain same API but use manual `__init__` methods

### Option 3: Version Detection
- Detect Python version in packaging
- Include dataclasses backport when needed

## Acceptance Criteria

- [ ] Filesystem module works on Python 3.6.8+
- [ ] `scripts/test_ssh_packaging_with_1password.py` filesystem test passes
- [ ] No regression in Python 3.7+ environments
- [ ] All dataclass functionality preserved (FileInfo, DiskUsage)

## Priority

**HIGH** - This blocks complete validation of the packaging system filesystem operations.

## Related Issues

- Issue #64: Function serialization architecture (core system working)
- Issue #63: External API validation (completed)