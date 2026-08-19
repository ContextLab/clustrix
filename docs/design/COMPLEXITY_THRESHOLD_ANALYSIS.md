# ClustriX Complexity Threshold Analysis

> **Status: historical. Root cause found and fixed, 2026-08-17.** This
> document's "Root cause: still under investigation" line (under "Status"
> below) is no longer accurate. The `result_raw.pkl not found` symptom this
> document investigated turned out to have a different cause than function
> complexity: the two-venv handoff re-serialized functions with stdlib
> `pickle` instead of `dill`, which fails for any function defined in the
> caller's `__main__` -- see the commit for issue #120 ("Make remote
> @cluster execution actually work (two-venv seam)") and
> `docs/design/function_dependency_design.md`, which cross-references this
> file. The rest of this document is left as originally written, as a record
> of the investigation.

## Executive Summary

We have identified a **complexity threshold** in ClustriX function execution where functions exceeding certain complexity levels fail with `result_raw.pkl not found - VENV2 execution may have failed`. This issue affects both SSH and SLURM cluster types.

## Key Findings

### Complexity Threshold Boundaries
- ✅ **Level 1-4**: Functions work reliably
- ❌ **Level 5+**: Functions fail consistently
- **Threshold**: Between Level 4 (high complexity) and Level 5 (very high complexity)

### Pattern Analysis

| Level | Description | Lines of Code | Subprocess Calls | Data Structures | Result |
|-------|-------------|---------------|------------------|-----------------|---------|
| 1 | Minimal | ~10 | 1 | 1 | ✅ PASS |
| 2 | Low-medium | ~20 | 2 | 2 | ✅ PASS |
| 3 | Medium | ~30 | 3 | 3 | ✅ PASS |
| 4 | High | ~80 | 7 | 4 | ✅ PASS |
| 5 | Very high | ~150+ | 10+ | 5+ | ❌ FAIL |

### Cluster Type Impact
- **SSH clusters (gpu)**: Same threshold applies
- **SLURM clusters (hpc2)**: Same threshold applies
- **Issue is cluster-type agnostic**

### GPU Configuration Impact
- **Single GPU**: Same threshold
- **Multi-GPU**: Same threshold  
- **Issue is GPU-configuration agnostic**

## Technical Analysis

### Working Pattern (Levels 1-4)
```python
@cluster(cores=1, memory="4GB")
def working_function():
    import subprocess
    
    # Up to ~7 subprocess calls work
    # Up to ~4 nested data structures work
    # Up to ~80 lines of code work
    # Multiple try/except blocks work
    # Conditional logic and loops work
    
    result = subprocess.run([...], ...)
    return {"success": True, "data": result}
```

### Failing Pattern (Level 5+)
```python
@cluster(cores=1, memory="4GB") 
def failing_function():
    import subprocess
    import json
    import time
    
    # 5+ nested data structures
    # 10+ subprocess calls
    # 150+ lines of code
    # Deep nesting and complex control flow
    # Multiple phases with metadata tracking
    
    # This pattern consistently fails with:
    # "result_raw.pkl not found - VENV2 execution may have failed"
```

## Examples Tested

### ✅ Working Examples
1. **Simple GPU computation**: Single subprocess, basic return
2. **Multi-GPU detection**: 2 subprocess calls, simple data structure  
3. **Medium complexity**: 3 subprocess calls, nested results dict
4. **High complexity**: 7 subprocess calls, 4-phase workflow, conditional logic

### ❌ Failing Examples
1. **Very high complexity**: 10+ subprocess calls, 5+ phases, deep nesting
2. **Complex SLURM workflow**: Multi-step analysis with metadata tracking
3. **Complex multi-GPU computation**: Nested loops, extensive data processing

## Root Cause Hypothesis

The failure appears to be related to:

1. **Serialization complexity**: Very complex function state may exceed pickle/cloudpickle limits
2. **Memory usage**: Complex functions may exceed remote memory constraints
3. **Execution timeout**: Complex functions may hit hidden timeouts in VENV2 execution
4. **File I/O limitations**: Complex functions generate larger intermediate files

## Workarounds

### 1. Function Decomposition
Break complex functions into simpler sub-functions:

```python
# Instead of one complex function
@cluster(cores=1, memory="4GB")
def complex_workflow():
    # 150+ lines of complex logic
    pass

# Use multiple simple functions
@cluster(cores=1, memory="2GB")
def phase1():
    # 20-30 lines
    pass

@cluster(cores=1, memory="2GB") 
def phase2():
    # 20-30 lines
    pass

results = [phase1(), phase2(), ...]
```

### 2. Subprocess Pattern
Use simple functions with subprocess calls for complex operations:

```python
@cluster(cores=1, memory="4GB")
def simple_wrapper():
    import subprocess
    
    # Offload complexity to subprocess
    result = subprocess.run([
        "python", "-c", """
import torch
# Complex computation here
result = complex_gpu_computation()
print(f'RESULT:{result}')
"""
    ], stdout=subprocess.PIPE, ...)
    
    return parse_simple_result(result.stdout)
```

### 3. External Script Pattern
Write complex logic to external files and execute:

```python
@cluster(cores=1, memory="4GB")
def script_executor():
    import subprocess
    
    # Execute pre-written complex script
    result = subprocess.run(
        ["python", "complex_analysis.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )
    
    return {"output": result.stdout}
```

## Recommendations

### For Users
1. **Keep functions simple**: Aim for < 50 lines, < 5 subprocess calls
2. **Use decomposition**: Break complex workflows into multiple simple functions
3. **Test incrementally**: Add complexity gradually and test at each step
4. **Use subprocess pattern**: Offload complex logic to subprocess calls

### For Developers
1. **Investigate root cause**: Determine exact failure mechanism in VENV2 execution
2. **Add complexity detection**: Warn users when functions may exceed threshold  
3. **Improve error messages**: Provide more specific failure reasons
4. **Consider architecture changes**: Evaluate alternatives to current pickle-based approach

## Test Files Created

1. `test_complexity_simple.py` - Basic threshold testing
2. `test_find_threshold.py` - Level 5 complexity identification
3. `test_precise_threshold.py` - Levels 2,4 + SLURM testing
4. `tests/real_world/test_complex_code_analysis.py` - Comprehensive pytest suite

## Status

- ✅ **Multi-GPU functionality**: Working with simple function patterns
- ✅ **Complexity threshold**: Identified between Level 4-5
- ✅ **Cross-cluster validation**: Issue confirmed on both SSH and SLURM
- ⚠️  **Root cause**: Still under investigation
- 🔧 **Workarounds**: Available and documented

## Next Steps

1. Investigate exact failure mechanism in VENV2 execution pipeline
2. Add function complexity analysis and user warnings
3. Implement better error handling and diagnostics
4. Consider architectural improvements for complex function support