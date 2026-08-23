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

### Security — one gate for every credential release

- **A stored credential could reach a host you never named, by seven separate
  routes.** Issue #167 was reported as one leak and closed as seven, one at a
  time. Seven call sites for one decision is not a bug with instances; it is a
  decision with no home. All of them now go through
  `clustrix.credential_release.release_credential(target)`, whose first
  positional parameter is the recipient: a frozen `CredentialTarget` naming the
  hostname, the username, and who chose the hostname. A target that names
  nobody cannot be constructed, a release carries a secret or a refusal but
  never both and never neither, and
  `FlexibleCredentialManager._ensure_credential_unchecked` raises for any
  caller that is not the gate.

- **`ClusterConfig.get_env_password()` is removed.** It read
  `os.environ[password_env_var]` with no host check and no provenance check,
  and `validation.py` fed the result straight into
  `paramiko.connect(hostname=config.cluster_host)`. With a working-directory
  `clustrix.yml` the whole method was the repository's: the file names
  `password_env_var` as well as `cluster_host`. **This is a user-visible
  behaviour change**: `clustrix credentials`/validation now reports a refusal,
  rather than "✅ Environment variable X contains password", for a
  `cluster_host` that came from a source you did not choose. The refusal names
  the remedy.

- **The interactive password prompt no longer offers to persist an untrusted
  host.** It offered to write `SSH_HOST=<whatever cluster_host says>` plus the
  password you had just typed into `~/.clustrix/.env` — manufacturing a
  permanent authorisation, in every future process, for a host a file had
  chosen.

- **`ConnectionManager.setup_ssh_connection` now honours `password_env_var`**,
  which it never did, under exactly the same two rules as every other source.

- **`ClusterConfig.from_file_content(mapping, source)`** is the only supported
  way to build a config out of parsed file bytes. Provenance is a required
  argument rather than something each loader must remember to declare, and
  because it is an argument it survives being handed to another thread.

- **`clustrix credentials test` never worked for SSH.** It passed the
  lower-case field names the credential resolver emits to a helper that indexes
  `SSH_HOST`/`SSH_USERNAME`/…, so every run raised `KeyError` inside that
  helper's own `try` block and reported "invalid or inaccessible" for
  credentials that were fine.

- **`scripts/aws/` could never authenticate.** They asked the clustrix
  credential manager for provider `"aws"`, which has never existed in
  `PROVIDER_ENV_NAMES`, so the lookup always returned `None`. They use boto3's
  own credential chain now, which also keeps AWS keys out of clustrix's
  credential surface entirely.

### Fixed — correctness

Landed last on the merge train, after this draft was first written:

- **A malformed configuration file you explicitly chose reported as empty**
  (#168). The widget's Load answered `{}` for any read failure — path typo,
  permissions problem, malformed YAML — which is also the answer for a file
  holding nothing, so the widget offered a blank profile as if your settings
  were in force. A named file now raises like `clustrix.config.load_config`
  does for the same file; only *discovered* files are skipped, and their
  reason is logged rather than discarded.
- **Renaming a profile onto an existing name silently destroyed that other
  profile** (#171) — no warning, no undo; the occupant's host, username and
  key file were gone. The rename is refused and names both profiles. The
  refusal deliberately does not reset the name box (it observes the
  keystream), so it can show a name the profile does not hold until you type
  again — recorded here as a known limitation rather than filed separately;
  there is no data loss either way.
- **`detect_gpu_capabilities` reported a GPU as available when it could not
  parse `nvidia-smi`'s output** (#172). `gpu_available` was set before
  parsing, so a driver that added a column or emitted a warning line produced
  "GPU available" with an empty device list — observed as real harm when a
  job was routed to a host whose driver output the parser could not read.
  Availability now follows parsed devices, and the `/proc` fallback fixture
  holds what the driver really writes.
- **Docs-only pull requests could not merge** (#169): branch protection
  requires the `CI Status` check, but `fast_ci.yml` is path-filtered and never
  runs for docs-only changes. The status-check job now reports success
  without running the suite for such PRs instead of being absent, and three
  more ways to silence a required check are closed alongside.
- **Pressing the widget's Save bricked the next `import clustrix`.** The
  widget writes a bundle of named profiles into the same standard locations
  the automatic search reads flat configurations from; strict loading then
  raised `ConfigFileError` on its profile names at first use. The search now
  detects the bundle shape, declines to adopt any of them, says so naming the
  file and the profiles, and keeps looking (#159, merge decision (a)).
- **Eleven `ClusterConfig` fields are accepted and read by nothing — and now
  say so when you set one** (#161): `max_gpu_parallel_jobs`,
  `gpu_detection_enabled`, `gpu_memory_fraction`, `local_parallel_threshold`,
  `auto_gpu_packages`, `prefer_gpu_execution`, `cache_credentials`,
  `cuda_version_preference`, `gpu_requirements`, `credential_cache_ttl` and
  `rapids_ecosystem` are leftovers of the automatic-GPU machinery whose
  execution path was deleted. They stay accepted so old configuration files
  keep loading, and each warns with its own reason instead of being silently
  absorbed (#158's precedent). Defaults stay silent.

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
- **The pre-push hook could not block a push** (#147). `scripts/run_real_world_tests.py`
  discarded every `runner.run_*_tests()` return value and never called `sys.exit`,
  so it exited 0 whatever happened. All four categories printed `❌ ... failed`
  and the hook then printed `✅ All real-world tests passed!` and allowed the
  push. Its failure message also printed only `stdout`, which was empty in every
  observed case because a pytest collection error goes to `stderr` — so an
  operator was told something failed and not what.
- **The host-key tests wrote into the developer's real `~/.ssh/known_hosts`**,
  where 83 stale `[127.0.0.1]:<ephemeral port>` entries had accumulated. Port
  reuse against a fresh server key then raised `BadHostKeyException` and failed
  the reject test. The fixture now redirects `~`.

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
have each run a real job and returned the right answer.

Asking for one of the removed backends is refused where you can act on it, and
the message says which kind of wrong it is. A removed backend is named, with
why it went and its tracking issue; an ordinary typo still gets the
did-you-mean hint:

```
ClusterConfig(cluster_type="pbs")
  ValueError: cluster_type='pbs' is no longer implemented. It was removed in
  v0.2.0 because it had never been verified against real hardware. Its return
  is tracked in issue #140. Supported types are: local, ssh, slurm, huggingface.

load_config, file containing `k8s_namespace: compute`
  ValueError: <path> contains unknown setting(s): k8s_namespace configured
  Kubernetes, which has been removed (see issue #142)

load_config, file containing a genuine typo `cluster_hostt`
  ValueError: <path> contains unknown setting(s): cluster_hostt
  (did you mean cluster_host?)
```

`validate_cluster_type()` is the single check, called from
`ClusterConfig.__post_init__`, `configure()`, `load_config()` and the executor.
Previously only `load_config` checked, so every other route carried a dead
backend to `ClusterExecutor.submit_job` — which tested the type *after*
`self.connect()`, spending an SSH round trip on a host that was never going to
be used, and then saying only `Unsupported cluster type: pbs`.

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
supported. Note that `hf_hardware` and `hf_username` sat under a comment
labelling them Spaces settings but are read by `hf_jobs.py`, so they stay;
`hf_sdk` was genuinely Spaces-only and is gone.

Removed with the backends:

- **`configure(auto_install_deps=...)`** — it installed cloud provider
  dependencies.
- **Ten `@cluster` parameters**: `provider`, `instance_type`, `region`,
  `platform`, `auto_provision`, `cluster_name`, `node_count`, `node_type`,
  `kubernetes_version`, `from_scratch`.
- **Every `k8s_*`, `aws_*`, `azure_*`, `gcp_*`, `lambda_*`, `cloud_*`,
  `cost_monitoring` and `hf_sdk` field** on `ClusterConfig`.

`configure()` also now validates every keyword before applying any of them. It
used to `setattr` its way through and raise partway, so a call that *failed*
had still changed the live configuration.

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
