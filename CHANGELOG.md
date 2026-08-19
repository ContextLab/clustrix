# Changelog

All notable changes to clustrix are recorded here. Dates are the date the work
landed on `master`.

The guiding rule for this file: a capability is only listed as working if it has
been exercised against the real thing. Anything implemented but unproven is
labelled as such and stays labelled until someone runs it for real — and as of
0.2.0, anything that stayed unproven was removed rather than shipped.

## [0.2.0] — unreleased

The first release in which `@cluster` demonstrably runs a function on remote
compute and returns the right answer. Before this, it never had — on any
backend.

### Fixed — correctness

Some entries below describe defects in backends that this same release then
removed (see **Removed — unverified backends**). They are kept because the
defects were real and the record matters; they are not claims that those
backends now work.

- **`@cluster` returned a fabricated GPU result instead of your answer.**
  `_attempt_client_side_gpu_parallelization` never called the decorated
  function. It ran a fixed `torch.randn(100, 100)` program on each GPU,
  scraped the matrix trace out of stdout, and returned those numbers to the
  caller. `auto_gpu_parallel` defaulted to `True` and the path triggered on
  any host reporting two or more GPUs. The path is deleted, and
  `clustrix/gpu_utils.py` went with it — its other four public functions had
  no callers anywhere, and two generated code referencing undefined names.
  `auto_gpu_parallel` and `max_gpu_parallel_jobs` are kept so existing
  configurations keep loading, but have no effect and now warn.
- **Remote loop parallelization crashed on any function it selected.** It
  injected `_chunk_range_<var>` and `_chunk_index` with no signature check, so
  a chosen function failed with `TypeError: ... got an unexpected keyword
  argument '_chunk_range_i'`. Both the local and remote chunkers now share one
  signature check and decline, with a log line, rather than injecting an
  argument the callee cannot take.
- **Loop ranges were guessed.** `detect_loops` fell back to `range(10)`
  whenever it could not evaluate a range expression, so a loop over
  `range(n)` was chunked as ten iterations and the caller silently received a
  tenth of the work. It now declines to parallelize. The value was also
  obtained by calling `eval()` on text sliced out of the user's source, under
  a comment admitting the approach was dangerous; that is replaced by a
  literal-only reader that cannot execute anything.
- **`@cluster` could return a fabricated answer instead of your result.**
  When a function was classified "complex" and flattening failed,
  `_execute_single` substituted `create_simple_subprocess_fallback`, whose
  entire remote body was `result = "Function execution completed"`. The user's
  function was never called, and nothing reported an error. For
  `def add(a, b): return a + b` the caller received that string instead of `5`.
  The stub is deleted and the caller's own function is always what gets
  serialized.
- **Flattening is removed entirely** (1,384 lines). Both generators emitted code
  that could not compile — the basic one dedented bodies to column 0, dropped
  `for` headers and printed instead of returning; the advanced one emitted
  `import range` for a builtin. It was attempted precisely when it could not
  work, because `analyze_function_complexity` reported a score of 999 and
  "complex" whenever it could not read the source. Nothing is lost:
  `serialize_function` already round-trips nested functions, closures,
  module-level helpers and functions created by `exec()`.
- **Loop analysis reported unparallelizable loops as parallelizable.**
  `visit_AugAssign` never registered its target as a read, so `total += i` came
  back with zero dependencies and `is_parallelizable=True`.
  `detect_loops_in_function` never dedented `inspect.getsource()`, so any method
  or closure raised `IndentationError` into a swallowed exception and always
  returned `[]`; it also bypassed the detector's level tracking, leaving
  `nested_level` permanently `-1` and the nesting-depth filter inert.
  `SafeRangeEvaluator` could not evaluate negative literals, so `range(10, 0, -1)`
  lost its range information.
- **Local auto-parallelization never parallelized anything.** It injected a
  `_parallel_<var>` keyword argument the callee could not accept, then swallowed
  the resulting `TypeError` and silently ran sequentially.
- **Environment replication silently dropped a third of the environment.**
  `get_environment_requirements` skipped every freeze line containing `@`. With
  `uv` on `PATH` — which is tried first — every conda-built package is rendered
  `name @ file:///...`, so 187 of 563 packages vanished with no warning, and the
  behaviour changed depending on whether `uv` happened to be installed. A
  requirement that genuinely cannot be reproduced remotely (an editable install,
  a git checkout) is now refused at submit time, naming the package, instead of
  producing a job that fails on import.
- **The scheduler backends did not share a staging and environment-setup path**,
  so they drifted. They now do.
- **`cluster_type="local"` raised `ValueError: Unsupported cluster type`**, though
  it was offered in the widget and the CLI.
- The by-value serialization walk missed instance attributes, local classes
  subclassing builtin containers, PEP-420 namespace packages, and
  `functools.partial`; and it silently degraded to by-reference at its 20,000-node
  cap, producing a payload that could not load.
- `cloudpickle>=2.0.0` was too low a floor: 2.x cannot load a by-value package
  that defines a `typing.NamedTuple` when the module object is in the function's
  globals — i.e. the ordinary `import mypkg; mypkg.f(x)` idiom.

### Fixed — security

- **Remote-to-local code execution via `error.pkl`.** Results were HMAC-verified
  before deserialization; error payloads were not, and no `error.pkl.hmac` existed
  anywhere. A hostile or compromised cluster only had to make the job fail and
  write its own `error.pkl`, which the caller then ran through `dill.load` at two
  sites — both wrapped in `except Exception: pass`, so a failed exploit was
  silent. Demonstrated with a payload whose `__reduce__` called `os.system`.
  Every payload a job hands back is now signed and verified.
- Cloud VM results were deserialized with no signature and no key.
- Verification **failed open**: a job with no recorded key, or an empty key,
  logged a warning and loaded the payload anyway. It now refuses.
- **SSH host keys were never verified.** `paramiko.AutoAddPolicy()` was used
  unconditionally at 12 sites, accepting any host key. Host keys are now checked
  against `known_hosts` by default, with an actionable error naming the host and
  the exact `ssh-keyscan` command. Opting out requires
  `ClusterConfig.ssh_host_key_policy="auto_add"`.
- **Shell injection into the generated job script.** `clustrix/utils.py` contained
  no `shlex.quote` at all; `remote_work_dir`, module loads, environment variables,
  partition, queue and requirement strings all reached the shell unquoted, and two
  of the sites were inside a `python -c "` string. Values that are ordinary shell
  words are now quoted; values the scheduler itself parses are validated and
  rejected if they carry shell metacharacters.
- The result-signing key stayed in the job's environment while user code ran, so
  anything in the container could forge a validly-tagged result. It is now removed
  before user code — and, on HuggingFace, before `pip install` runs third-party
  install hooks.
- Saved configuration files were written world-readable with credentials in
  plaintext. They are now created 0600, and secret-bearing fields are omitted
  unless `include_secrets=True`. `environment_variables` is filtered entry by
  entry, so `OMP_NUM_THREADS` survives a save/reload while `AWS_SECRET_ACCESS_KEY`
  does not.

### Fixed — the test suite could not be trusted

- **The documented "safe" test command ran tests that make real SSH and cloud
  calls.** Six files under `tests/real_world/` carried no `@pytest.mark.real_world`,
  so 26 tests were selected by `pytest tests/ -m "not real_world"`. The marker is
  now applied by path, so it cannot be forgotten.
- Production code branched on `isinstance(..., Mock)`, and a module of fake
  widgets was importable from the shipped package.
- `.flake8` sat in the working tree with unresolved conflict markers, so flake8
  silently fell back to its defaults.
- CI's flake8 step passed `--exit-zero` and could not fail; its mypy step carried
  `continue-on-error: true`; and it ran only `tests/unit/` — about 350 of the
  ~1,750 non-billable tests that exist.

### Added

- HuggingFace Jobs backend (`cluster_type="huggingface"`) — verified end to end.
- `scripts/aws/` — resource cleanup and cluster destruction utilities. They
  default to a dry run and refuse to touch anything not tagged
  `clustrix:managed=true`. (The originals, recovered from git history, deleted
  every NAT gateway and VPC in the account with no ownership check at all.)
- A usage-patterns tutorial. Every example in the docs is executed by
  `scripts/check_docs_examples.py`.
- `clustrix.config.SUPPORTED_CLUSTER_TYPES` as the single source of truth. The
  CLI's own list had drifted and omitted `huggingface` entirely, so a working
  backend could not be selected from the command line.
- `ClusterConfig` is exported from the package root.

### Changed

- Version strings in `pyproject.toml`, `setup.py`, `clustrix/__init__.py` and
  `docs/source/conf.py` now agree. They had drifted to two different values.
- Documentation builds with zero sphinx warnings, down from 60.
- Deleted 1,837 lines of genuinely orphaned modules. (The issue that requested
  this claimed ~5,100 lines and named five files that do not exist on `master`.)

### Removed — unverified backends (BREAKING)

Seven execution backends were implemented in full and not one of them had ever
been shown to run a job end to end against real hardware. Rather than keep
publishing them as if they worked, they were removed. `SUPPORTED_CLUSTER_TYPES`
is now exactly `local`, `ssh`, `slurm`, `huggingface` — the four backends that
have each run a real job and returned the right answer. Anything else raises
`ValueError: Unsupported cluster type` at submit time.

Each removed backend has a tracking issue and is planned for a future release.
The gate for restoring one is the gate the surviving four already passed: a
real job, on real hardware, whose result comes back and is checked in as
evidence. No date is promised.

| Removed | Issue | What it was |
|-|-|-|
| PBS | [#140](https://github.com/ContextLab/clustrix/issues/140) | `cluster_type="pbs"` — the PBS/Torque scheduler |
| SGE | [#141](https://github.com/ContextLab/clustrix/issues/141) | `cluster_type="sge"` — Sun/Son of Grid Engine |
| Kubernetes | [#142](https://github.com/ContextLab/clustrix/issues/142) | `cluster_type="kubernetes"`, the `k8s_*` settings, cluster auto-provisioning |
| AWS | [#143](https://github.com/ContextLab/clustrix/issues/143) | `provider="aws"` — EC2 and EKS |
| GCP | [#144](https://github.com/ContextLab/clustrix/issues/144) | `provider="gcp"` — Google Compute Engine |
| Azure | [#145](https://github.com/ContextLab/clustrix/issues/145) | `provider="azure"` — Azure VMs |
| Lambda Cloud | [#146](https://github.com/ContextLab/clustrix/issues/146) | `provider="lambda"` — Lambda Labs GPU cloud |

The HuggingFace **Spaces** provider (`provider="huggingface"`) was removed with
them. This is a different thing from `cluster_type="huggingface"`, which is
HuggingFace **Jobs**: that backend is verified end to end and is fully
supported.

### Removed — cost monitoring API (BREAKING)

The cost monitoring and cloud pricing API is gone, along with all five of its
public functions:

- `cost_tracking_decorator`
- `get_cost_monitor`
- `start_cost_monitoring`
- `generate_cost_report`
- `get_pricing_info`

Importing any of them from `clustrix` now raises `ImportError`. They priced the
cloud VM backends, so with those backends removed the API had nothing left to
price. Use your provider's own pricing calculator instead.

`scripts/aws/` is unaffected — it is operator cleanup tooling, not an execution
backend, and it stays.

### Known limitations

- Functions defined in the REPL still lose the source-based features — loop
  parallelization and complexity analysis — because those parse source with
  `ast`. Serialization itself does not need source and works correctly.
- Loop detection does not see tuple-unpacking targets
  (`for i, x in enumerate(...)`), and its "any external name read" heuristic is
  conservative enough to reject the canonical `results.append(f(x))` pattern.

## [0.1.1] and earlier

No changelog was kept. See the git history.
