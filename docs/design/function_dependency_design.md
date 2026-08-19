# Function flattening: a post-mortem

**Status: abandoned. The code this document described was deleted in the 0.2.0
cycle. Do not rebuild it without reading this first.**

The original version of this file proposed a "comprehensive function dependency
resolution system" — hoisting nested functions to module level, resolving
cross-file dependencies, distinguishing local from external code. Some of it was
built, as `clustrix/function_flattening.py` (1,027 lines) and
`clustrix/dependency_resolution.py` (445 lines). Both are gone.

## Why it was removed

**It never produced a runnable function.** Both generators were exercised
against every shape they were meant to handle. The basic flattener emitted a
body dedented to column 0 with the `for` header dropped and statements
reordered, and printed instead of returning. The advanced one emitted
`import range` for a builtin. Live output on an ordinary function with one
nested helper:

```
Generated flattened code did not execute: No module named 'range'
Generated flattened code did not execute: name 'i' is not defined
```

**It was attempted precisely when it could not work.**
`analyze_function_complexity` returned `complexity_score: 999` and
`is_complex: True` from its `except` branch — that is, whenever
`inspect.getsource()` failed. But flattening *requires* source. So the harder
the case, the more confidently the system reached for the one tool guaranteed to
fail on it.

**Its failure was not visible.** `auto_flatten_if_needed` reported
`success: True` even when it had fallen back to returning the original,
unflattened function. Worse, when it reported failure, `_execute_single`
substituted `create_simple_subprocess_fallback`, whose entire remote body was:

```python
result = "Function execution completed"
```

The user's function was never called and no error was raised. For
`def add(a, b): return a + b` the caller received that string instead of `5`.
That is the most serious defect ever found in this project, and this machinery
is where it lived.

**The problem it solved had already been solved elsewhere.** Flattening was a
workaround for a serialization layer that could not ship closures and nested
functions. Since the by-value serialization work,
`clustrix.utils.serialize_function` handles all of it. Verified in a fresh
subprocess interpreter with the defining module off `sys.path`:

```
SUBPROCESS nested_fn           = 45 (direct=45) MATCH
SUBPROCESS deep_nested         = 65 (direct=65) MATCH
SUBPROCESS calls_module_helper = 19 (direct=19) MATCH
SUBPROCESS uses_closure        = 40 (direct=40) MATCH
SUBPROCESS exec_made           =  5 (direct=5)  MATCH
SUBPROCESS with_args           = 21 (direct=21) MATCH
```

There is no function flattening helped that the serializer does not already
handle.

## What the project lost

Nothing that worked. The only real loss is the *aspiration* of rewriting
functions whose source is unavailable — which was never achievable, because
rewriting source requires source, and those are exactly the functions that do
not have it.

## If you are tempted to build this again

Two questions to answer first, with evidence, before writing any code:

1. **Name a function the serializer cannot ship.** Not a hypothetical — write
   it, put it through `serialize_function`/`deserialize_function` in a fresh
   interpreter that cannot import the defining module, and show the failure. If
   you cannot produce one, there is no problem to solve.
2. **Say how a rewritten function is proven equivalent to the original.**
   Substituting a *different* function for the user's and returning its result
   is only safe if equivalence is checked, and equivalence cannot be checked
   without running the user's function — which is the thing you were trying to
   avoid. The previous implementation never answered this, which is how it came
   to return a hardcoded string.

Two related issues, #89 (extract global variables) and #90 (closure variable
handling), were TODOs inside this machinery. They were closed by its removal
rather than implemented: implementing them would have meant building on a
foundation that had never held weight.

See also `COMPLEXITY_THRESHOLD_ANALYSIS.md`, which recorded the symptom that
originally motivated flattening — jobs failing above a complexity threshold with
`result_raw.pkl not found`. That symptom had a different cause, in the two-venv
handoff, and was fixed there.
