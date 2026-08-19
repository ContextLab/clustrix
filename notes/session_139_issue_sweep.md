# Session: comprehensive sweep of all remaining open issues

Started 2026-08-18, from master @ `0ca28fa`, on branch `fix/remaining-issues-sweep`.

Goal (user's words): "comprehensively address *all* remaining open issues, post
comments to each with DIRECT EVIDENCE that the implementation is correct, and
verify that all CI tests are green (fix as needed)".

40 issues were open at the start.

## The most serious thing found

`@cluster` can return a fabricated answer instead of the user's result.

`clustrix/decorator.py::_execute_single` classifies the function, attempts
flattening, and on failure calls `create_simple_subprocess_fallback` from
`clustrix/function_flattening.py`. That function returns a closure that runs a
hardcoded subprocess whose entire body is `result = "Function execution
completed"`. The user's function is never called. Reproduced:

```
REAL ANSWER      : 5
complexity_score : 999 | is_complex: True
flatten success  : False
WHAT CLUSTRIX RUNS -> 'Function execution completed'
```

Reachability, all reproduced:

* `analyze_function_complexity` returns `complexity_score: 999,
  is_complex: True` from its `except` branch, i.e. whenever it cannot read the
  function's source. So flattening is attempted precisely when it cannot work
  (REPL, notebook cell, `exec`-generated function).
* `nested_functions > 0` alone forces `is_complex`, so an ordinary function
  with one inner helper qualifies. Both flatteners then crash on it:
  `No module named 'range'` (advanced) and `name 'i' is not defined` (basic).
* `auto_flatten_if_needed` returns `success: True` even when it fell back to
  the original function, so the flag is wrong in both directions.

The justification for the whole mechanism is gone: `serialize_function` /
`deserialize_function` round-trip the same source-less function and return the
correct answer. Flattening was a workaround for a serializer that could not
handle closures; since PR #137 it can.

Reproduction scripts kept in the session scratchpad: `repro_flatten.py`,
`repro_silent.py`, `repro_ser.py`.

## Safety hole found while working

`CLAUDE.md` documents `pytest tests/ -m "not real_world"` as the safe,
CI-compatible command. Most files under `tests/real_world/` carry no
`@pytest.mark.real_world`, and `tests/real_world/conftest.py`'s
`pytest_collection_modifyitems` only adds *skip* markers for
expensive/visual/dartmouth categories — it never applies the `real_world`
marker itself. So the documented command runs them, making real SSH and cloud
calls. A run started during this session had to be killed for that reason.

## Independently verified corrections to open issues

| Issue | Its claim | Verified reality |
|-|-|-|
| #99 | `clustrix/providers/aws.py`, 291 lines, 48% | `clustrix/providers/` does not exist |
| #104 | `executor.py` 71% → 85% | 39 lines, 7 imports, a re-export shim |
| #102, #122 | five `notebook_magic_*` modules, ~2,678 lines | absent on master; present only on `origin/epic/test-coverage-90-percent` |
| #122 | "~5,100 lines orphaned" | ~2,435 lines on master; `notebook_magic_widget.py` is imported by `notebook_magic.py:37` |
| #132 | comprehensions auto-parallelized | 0 comprehension visitors on master, 4 on the epic branch |
| #114 | 127 failures, 8 errors | 83 failed, 1437 passed, 10 skipped, 2 errors |
| #124 | version drift | `pyproject.toml` 0.1.1, `setup.py` 0.1.1, `clustrix/__init__.py` 0.1.0, `docs/source/conf.py` 0.1.0 |
| #121 | pickle RCE + host keys | HMAC verification IS implemented; `AutoAddPolicy` still unconditional at 12 sites |
| #126 | "no HMAC anywhere" | wrong — `executor_core.py`, `hf_jobs.py`, `utils.py` all carry it |

## Other verified facts

* `from clustrix import ClusterConfig` raises ImportError — `ClusterConfig` is
  not in `clustrix/__init__.py`'s exports, though `ClusterExecutor`,
  `ClusterFilesystem`, `ProfileManager` and others are. `MIGRATION.md:71` tells
  users it works.
* `clustrix/cli.py:26` offers `["slurm","pbs","sge","kubernetes","ssh","local"]`
  — the `huggingface` backend cannot be selected from the CLI at all, though the
  widget offers it and the executor dispatches it.
* Sphinx builds clean (`exit 0`) with 60 warnings, 58 of which are the
  `clustrix.filesystem` module being autodoc'd from two places.
* `black` is NOT a transitive dependency of `docs/requirements.txt`: a fresh
  resolution of that file installs 117 packages, none of them black. The
  dependabot alert (GHSA-3936-cmfr-pm3m) predates the pin added in `bf524a4`
  and has not been re-evaluated; the SBOM records `black` with
  `versionInfo: null`.

## Workstreams dispatched

Parallel agents, each owning a disjoint file set:

1. flattening / fabricated results (+ #89, #90) — `function_flattening.py`, `decorator.py`, `dependency_resolution.py`
2. SSH host-key verification + config file permissions (#121, #111) — `ssh_utils.py`, `executor_connections.py`, `filesystem.py`, `validation.py`, `cli_credentials.py`, `config.py`; produced new `clustrix/ssh_security.py`
3. mock-awareness out of shipped code (#116) — `executor_scheduler_status.py`, `notebook_magic*.py`
4. `real_world` marker safety hole (#109, #114) — `tests/real_world/conftest.py`
5. orphaned-module deletion (#122)
6. failure categorization (#114) — read-only
7. cloud/kubernetes/PBS backends (#119, #120) — `executor_*.py`, `cloud_providers/*`
8. CI workflows (#113, #118) — `.github/`
9. AWS cleanup utilities (#95) — `scripts/aws/`
10. docs: K8s guide, usage examples, sphinx warnings (#70, #96, #88, #124) — `docs/`, `MIGRATION.md`
11. red-team: security seam (#126, #121)
12. red-team: serialization + environment replication (#137's claims)

Retained for the main thread: `README.md`, `CLAUDE.md` (#125), version
unification across four files (#124, #127), `CHANGELOG.md`, `cli.py`, and the
final evidence comments on every issue.

## Defects found but NOT fixed (need issues filed — ask first)

1. **`_combine_local_results` returns a different shape than the sequential
   path.** A parallel local run returns a raw list of per-chunk results; the
   sequential run of the same function returns the function's own return value.
   So turning parallelism on or off changes the type of what the caller gets.
   Found by the flattening-deletion agent while fixing #120 item 2.

2. **Local auto-parallelization is narrower than it looks.** After the #106
   loop-analysis correctness fixes, the analyser only accepts a loop body that
   reads nothing but the loop variable. That means the injected
   `_parallel_<var>` chunk can never be consumed *inside* the split loop — the
   convention is only usable by reading it outside the loop. Worth deciding
   whether the convention or the analyser is wrong.

3. **`tests/test_cloud_providers_gcp_real.py:510`**
   `TestGCPProviderIntegrationWorkflows::test_complete_cluster_lifecycle`
   requests a `gcp_credentials` fixture defined inside a *different* class
   (`TestGCPProviderReal`), so pytest cannot find it — a collection error, not a
   failure. Pre-existing.

4. **`|| echo 'Failed to install ...'` guards remain** in
   `setup_python_compatible_environment` and the GPU-package installer. These
   are different functions from the two-venv path that was fixed, and are not on
   the cached-environment path, but they still swallow install failures.

5. **`tests/test_decorator_real.py::test_async_execution`** fails with
   `'AsyncJobResult' object is not subscriptable`. `AsyncJobResult` exposes
   `get_result()`, not Future-style `result()`. Verified identical with and
   without the flattening change.

## User data to review

`~/.clustrix/profiles/profiles.yml` (162 KB, mode 0644) accumulated 47 junk
`Current configuration (N)` profiles because `ProfileManager.__init__`
hardcoded `~/.clustrix/profiles` and ignored `CLUSTRIX_CONFIG_DIR`, so every
test run wrote to the developer's real config. The code bug is fixed in
`2a23b27`; the existing file is left alone deliberately — it also holds the 9
real built-in profiles.
