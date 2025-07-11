# Function Serialization Limitations in Clustrix

## Overview

This document describes the limitations of function serialization in Clustrix, particularly around functions defined in interactive Python environments.

## Key Limitation: Python REPL Functions

### Problem

Functions defined interactively in the Python REPL (Read-Eval-Print Loop) cannot be serialized for remote execution because their source code is not available to `inspect.getsource()`.

### Affected Environments

- **Interactive Python sessions** (command line `python` interpreter)
- **Some notebook environments** that don't preserve function source code
- **Dynamic function creation** using `exec()` or `eval()` in certain contexts

### Root Cause

The Python `inspect.getsource()` function, which Clustrix uses to extract function source code for remote execution, cannot access the source of functions defined interactively because:

1. Interactive functions are created in the `__main__` module without persistent source files
2. The source code is not stored in a way that `inspect` can retrieve
3. The function's `__code__` object doesn't contain the original source text

### Technical Details

When a function is defined in a Python file, `inspect.getsource()` can read the source from the file. However, for interactive functions:

```python
# In Python REPL:
>>> @cluster(cores=2)
... def my_function(x):
...     return x * 2
>>> 
>>> import inspect
>>> inspect.getsource(my_function)
# Raises OSError: could not get source code
```

## Workaround: Source Code Fallback

Clustrix implements a sophisticated fallback mechanism for cross-version compatibility:

1. **Binary Serialization**: First attempts to serialize functions using `dill` and `cloudpickle`
2. **Source Code Fallback**: If binary serialization fails (e.g., cross-Python version issues), attempts to use `inspect.getsource()` to get the function source
3. **Source Code Recreation**: Recreates the function on the remote system by executing the source code

This approach works well for file-based functions but fails for REPL-defined functions.

## Recommended Solutions

### 1. Define Functions in Files

```python
# In a .py file or Jupyter notebook cell:
@cluster(cores=2)
def my_function(x):
    return x * 2

result = my_function(5)  # Works correctly
```

### 2. Use Jupyter Notebooks

Jupyter notebooks preserve function source code and work correctly with Clustrix:

```python
# In a Jupyter notebook cell:
@cluster(cores=4, memory='8GB')
def process_data(data):
    import pandas as pd
    return pd.DataFrame(data).sum()

result = process_data(my_data)  # Works correctly
```

### 3. Use IPython

IPython generally preserves function source better than the basic Python REPL:

```python
# In IPython:
In [1]: @cluster(cores=2)
   ...: def my_function(x):
   ...:     return x * 2

In [2]: my_function(5)  # Usually works
```

## Error Messages

When this limitation is encountered, users will see error messages like:

```
RuntimeError: Could not deserialize function with dill, cloudpickle, pickle, or source code.
```

Or more specifically:

```
OSError: could not get source code
```

## Implementation Details

The limitation is handled in `clustrix/utils.py` in the `serialize_function()` function:

```python
def serialize_function(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    # ... other serialization attempts ...
    
    # Try to get source code for fallback
    try:
        func_source = inspect.getsource(func)
    except (OSError, TypeError):
        func_source = None  # Source not available (e.g., REPL functions)
    
    # ... rest of function ...
```

## Future Improvements

Potential improvements to handle this limitation better:

1. **Enhanced Error Messages**: Provide clearer error messages when REPL functions are detected
2. **Alternative Serialization Methods**: Explore additional serialization techniques for interactive functions
3. **Function Introspection**: Use code object analysis to recreate simple functions without source
4. **Documentation**: Better user guidance on supported environments

## Cross-Version Compatibility

This limitation is particularly relevant for cross-version compatibility (e.g., Python 3.12 → Python 3.6.8) because:

1. Binary serialization often fails between different Python versions
2. Source code fallback becomes the primary mechanism
3. REPL functions have no source code to fall back to

The two-venv architecture implemented in Clustrix helps with version compatibility but still requires function source code to be available.

## Conclusion

While this limitation affects a specific use case (interactive Python REPL), it represents a fundamental constraint of Python's introspection capabilities. The recommended approach is to define functions in files or supported notebook environments where source code is preserved.

Users should be aware of this limitation and structure their code accordingly for the best Clustrix experience.