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

Everything listed under "Added" was run
against a real `sshd` and a single-node SLURM installation, or locally; what
was not is marked.

### Breaking changes, and what to change

- **`clustrix.setup_environment` is deleted**, with `utils.is_uv_available`,
  `is_conda_available` and `get_package_manager_command`. It built a list of
  commands, ran none of them and returned the path of an interpreter it had
  not created; nothing called it. *Migration:* none needed; every remote
  job builds its own environment.
- **`package_manager="conda"` with `use_two_venv=False` raises.** That path
  created a conda environment the job never ran in, at a Python version
  made by replacing "python" with "3.11" in `python_executable`.
  *Migration:* leave `use_two_venv` at its default, or name an existing
  environment with `conda_env_name`.

- **A divided loop returns the function's own return value.** The old
  protocol is gone: `@cluster` no longer passes `_chunk_range_<var>`,
  `_chunk_index` or `_parallel_<var>` keywords, and never returns a list of
  per-chunk results. *Migration:* delete those parameters and the code that
  merged chunk results; write the loop plainly. Check the decision with
  `print(clustrix.explain(function))`.
- **`clustrix.loop_analysis` is deleted**, with `detect_loops`,
  `detect_loops_in_function`, `find_parallelizable_loops`, `LoopAnalyzer` and
  `analyze_function_loops`. *Migration:* `clustrix.explain(function)`.
- **`LocalExecutor`, `create_local_executor` and `choose_executor_type` are
  deleted.** *Migration:* `@cluster(cores=N)` with no `cluster_host` divides
  a qualifying loop among `N` worker processes; for anything else use
  `concurrent.futures` or joblib directly.
- **The legacy notebook widget is deleted**: `EnhancedClusterConfigWidget`,
  `DEFAULT_CONFIGS`, `detect_config_files`, `load_config_from_file`,
  `validate_ip_address`, `validate_hostname`, and the modules
  `clustrix.notebook_magic_widget` and `clustrix.notebook_magic_config`.
  *Migration:* `ModernClustrixWidget` (or `%%remote`) for the form,
  `clustrix.config.load_config(path)` to read a file.
- **`display_modern_widget()` and `show_widget()` return `None`.** They used
  to return the container, which showed the widget twice when the call ended
  a cell. *Migration:* construct `ModernClustrixWidget()` to keep a handle.
- **`clustrix.validation` is deleted.** *Migration:* `clustrix status`, or
  `ClusterExecutor(config).connect()`.
- **`ssh_port` is no longer a field.** It is accepted as a deprecated alias
  for `cluster_port` and warns. *Migration:* rename it to `cluster_port`.
- **`configure()` validates types and ranges** and applies nothing when a
  value is refused. `configure(default_cores=0)`, `cluster_port="abc"` and
  `ssh_host_key_policy="yolo"` now raise `ValueError`.
- **`clustrix status` exits 1 when the connection fails.** It used to exit 0.
  With no `ssh` or `slurm` host configured it no longer prints "No cluster
  configured": it prints where `@cluster` runs a function, in the words
  `%clustrix status` uses (`backend      : local (...)`), makes no
  connection, and exits 0.
- **`clustrix config --api-key` is removed.** It set a field nothing reads
  and put a secret on a command line.
- **`clustrix config --cores 0` is an error** rather than "not given".
- **`clustrix credentials list` no longer shows a `LOCAL` provider.**
- **Values in the credential file (`.env`) are read literally.** `${VAR}` is
  no longer expanded, and values are written single-quoted. *Migration:* if a
  stored value relied on expansion, write the expanded value.
- **A failed or timed-out remote environment build raises.** It used to fall
  back silently to an environment containing only the serializers.
- **`replicate_local_environment=False` installs the packages the job was
  detected to need.** It used to install nothing.
- **Local and remote Python must be the same minor version**; a mismatch is
  refused at submission instead of failing inside the job.
- **`setup.py` is a shim.** The version lives in `pyproject.toml`,
  `clustrix/__init__.py` and `docs/source/conf.py`.
- **`ClusterConfig.api_key` is removed.** Nothing read it. `configure(api_key=...)`
  and an `api_key` key in a configuration file are refused with that
  explanation. *Migration:* delete it.
- **`%clustrix status` returns `None`.** It used to return the configuration,
  which a notebook then printed in full under the summary. Its first line is
  now `backend`, the backend in effect, not `cluster_type`: a configuration
  with no `cluster_host` is reported as `local (no cluster host set; ...)`.
  *Migration:* `%clustrix config` still returns the configuration.
- **A SLURM job is named after its function**, not `clustrix`; a chunk of a
  divided loop is `<function>.body`. *Migration:* anything that selects jobs
  with `squeue --name=clustrix` needs another selector, such as `--user`.
- **A job that was stopped rather than raising reports in a few lines.** The
  exception is `JobFailedError`, still a `RuntimeError`; the text `Error
  log:` followed by the entire log is gone from the message, and the log is
  the exception's `error_log`. *Migration:* read `error_log` instead of
  parsing the message.
- **`python_executable`, when set to anything but `"python"`, is the
  interpreter both remote virtualenvs are built from** on a host without
  conda, and must exist there at this session's minor version. It used to be
  ignored on the default path. On a host with conda it still does not choose
  the build interpreter; that branch was not run. *Migration:* leave it at
  the default unless you mean it.

### Added

- **Automatic loop parallelisation** (`clustrix.parallelize`). A top-level
  `for` loop, or a single-`for` comprehension statement, whose iterations are
  independent is divided: among `cores` worker processes locally (`cores=-1`
  uses every CPU available to the process), or into one job per piece, at
  most `max_parallel_jobs`, on `ssh` and `slurm`. Writes to collected values
  are replayed in iteration order, so the result is the sequential one.
  Objects that existed before the loop are collected as themselves, not as
  copies. Limits: only a top-level loop of the decorated function. No split
  when a value is carried between iterations, on `break`, on `return` in the
  loop, when a value assigned in the loop is used or deleted after it, when
  the loop changes anything the function did not create, on any direct use of
  the global random generators (seeding included), or when a function defined
  before the loop reads a variable assigned later. Not analysed at all: a
  function with a `global` or `nonlocal` statement, one wrapped by another
  decorator, a method using `super()` or a class-private name, a function
  whose file changed after import. Run-time checks catch a value the loop
  reads that can reach what it collects into, a modified shared value, moved
  global random state, a collected value that cannot be copied, and a
  function created in the loop that outlives its iteration; each falls back
  to the sequential loop. A loop over a generator, `map`, a file or another
  one-shot iterator runs in order (wrap it in `list(...)` to have it
  divided), and a write the loop's own `try`/`with` depends on
  (`counts[k] += 1` under `except KeyError`) is refused. No check sees module caches, files, databases, GPU
  random state, process-wide settings such as the `decimal` context, or an
  object whose pickle omits its state. The checks cost time proportional to
  the data the loop reads (about 2 s per million small objects). There are
  no SLURM job arrays, and no GPU path was verified on real hardware. One job
  per piece on HuggingFace Jobs follows from the same code path and was
  **not run**.
- **`@cluster(gpus=N)`**: GPUs per job. `--gres=gpu:N` on SLURM; on `ssh`
  and locally, each piece of a divided loop is given its own devices through
  `CUDA_VISIBLE_DEVICES` and no more pieces run at once than there are
  devices for. Too few GPUs raises instead of running without one, on every
  local and `ssh` route: a function with no loop, `cores=1`,
  `parallel=False` and `async_submit=True` are checked like a divided loop,
  before anything is submitted (on `ssh`, one remote command per host per
  session). The devices are the ones the caller may use: when
  `CUDA_VISIBLE_DEVICES` is set, that list is what is divided, with its ids
  as spelled (indices or UUIDs), and only entries `nvidia-smi` lists count.
  The SLURM request was run against a real scheduler with placeholder `gpu`
  resources; **no computation was run on a real GPU**, and GPU counting on
  `ssh` and locally was not run on a machine that has one -- what was run
  there is the refusal when there is none, and the id rules against the
  text `nvidia-smi` prints.
- **`clustrix.explain(function)`** returns the decision and, for a loop that
  is not divided, the line and the reason.
- **Local timing.** A function's first local call divides its loop and
  measures it; later calls divide only when the loop is long enough to be
  worth it (about 0.2 s of work).
- **Automatic staging of input files** on `ssh` and `slurm`
  (`clustrix.auto_stage`; `ClusterConfig.auto_stage`, default on;
  `auto_stage_max_bytes`, default 1 GB; `@cluster(data=[...], no_data=[...])`).
  An input already on the cluster at the same path is used in place only
  when it is shown to be the same data: same size and SHA-256 for a file,
  the same files with the same sizes and SHA-256 for a directory, computed
  on the cluster. Different data stops the call with what differs; a
  cluster that cannot hash gets an upload. Otherwise the input is uploaded
  once over SSH and linked at the path the code uses. Limits: a path found
  in code must be inside the project, and the code must be the project's
  own (nothing under `site-packages`/`dist-packages`); the home directory,
  a directory containing it and `/` are never a project, so from there
  nothing found in code is staged; credential files are refused, judged by
  the path as written and by its real path behind any symbolic link, from a
  list of names (`staging.SENSITIVE_PATTERNS`: keys, `.env`/`*.env`,
  `.netrc`, `.pgpass`, `.my.cnf`, `.vault-token`, `kaggle.json`, cloud and
  HuggingFace credential locations, ...) plus one content check for Google
  service-account JSON -- a secret in an ordinarily named file is not
  recognised; a path computed at run time is not found; it does not
  apply to `async_submit=True` (which now warns) or HuggingFace Jobs; files a
  job writes are not brought back; staged data is never deleted
  automatically. Inside a staged directory, credential files, `no_data`
  paths, symbolic links and `.git`/`__pycache__`/`.ipynb_checkpoints` are
  left out, and each credential left out is named in a warning. The project
  directory, the working directory, the home directory and anything above
  them are refused, as constants and as arguments. **Staged copies are
  read-only and private** (store `0700`, copies `0500`, files `0400`,
  whatever the remote umask; an open store is closed when next used): a job
  that writes into a staged input gets `PermissionError`, now with a
  sentence saying the path was staged as an input and that
  `@cluster(no_data=[path])` keeps it out; removing the store by hand needs
  `chmod -R u+w <remote_work_dir>/data` first. A relative input named like
  a file clustrix writes in the job directory (`result.pkl`, `job.sh`, ...)
  is refused at submission. A copy is only used once its upload finished;
  an upload cut short
  by a lost connection leaves a `.incoming-*` directory that nothing removes.
- **Project modules travel with the job**, including a module imported
  inside a function body.
- **Dependency detection** from the serialized job (`required_packages`).
- **Declared data packages work on `ssh` and `slurm` workers** when they
  travel inline. A package stored in a HuggingFace repository still cannot be
  read there, and now says so.
- **Notebook widget**: edits are a draft until **Apply**; **Revert**; separate
  "applied" and "last result" indicators; **Test connection** and **Run test
  job** through the real executor; **Trust this host**; **Set up SSH key**;
  **Export** / **Import** of profiles; fields for partition, an existing
  conda environment and the HuggingFace time limit.
- **`clustrix.ssh_security.trust_offered_host_key(error)`** records exactly
  the key a refused connection was offered.
- **`%clustrix status | config | config <profile> | load <file>`**, and
  `%load_ext clustrix` puts `cluster`, `configure` and `get_config` in the
  notebook namespace.
- **Python 3.13** classifier and CI job.
- Documentation pages: loop parallelisation, input files, command line.
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

- **Waiting for a result** starts polling one second apart and backs off to
  `job_poll_interval`, which is now the ceiling. Four one-second SLURM jobs
  went from 67 s to 9.5 s in the commit's own measurement.
- **Remote environments are keyed by content** under
  `<remote_work_dir>/envs/` and reused; a set of requirements is resolved once
  per executor.
- **`clustrix config ...` and `clustrix load` persist** to
  `<config dir>/config.yml` (mode 0600, secrets omitted). They used to print
  success and save nothing.
- **`%%clusterfy`** is a deprecated alias for `%%remote`.
- **Remote tracebacks reach the caller**, and a job that died without an
  error report says how it ended.
- **A class sent to a worker by value comes back as the caller's class**, so
  `isinstance` and `except MyError` work on results and remote errors.
- **`cancel_job` on the `ssh` backend kills the process tree.**
- **Ctrl-C during a divided loop stops it.** Locally the call returns at
  once and no worker is left running; on `ssh` and `slurm` every job the
  loop started is cancelled, including the one being waited for.
- **SIGTERM does the same, and so does the death of the client.** On `ssh`
  and `slurm`, SIGTERM while a call waits cancels the jobs it started -- a
  divided loop's, or a single job's, which Ctrl-C now cancels too -- and the
  process then ends by the signal. A SIGTERM handler the program installed
  is left in charge; calls from other threads and `async_submit=True` are
  not covered. Local loop workers exit within about a second of their
  parent's death, however it died. **SIGKILL of a client with remote jobs
  cannot be handled**: those jobs keep running.
- **Local workers see the caller's current directory and environment
  variables** as they are at the call, not as they were when the workers
  started. What iterations write to standard error is shown on standard
  error, warnings reach the caller's warning filters, and an exception
  arrives as its own type even when its class cannot be rebuilt from its
  arguments.
- **A loop that reads something which cannot be pickled runs in order here**
  instead of failing.
- **An SSH connection attempt gives up after `ssh_connect_timeout`** (30 s).
- Version strings in `pyproject.toml`, `setup.py`, `clustrix/__init__.py` and
  `docs/source/conf.py` now agree. They had drifted to two different values.
- Documentation builds with zero sphinx warnings, down from 60.
- Deleted 1,837 lines of genuinely orphaned modules. (The issue that requested
  this claimed ~5,100 lines and named five files that do not exist on `master`.)

### Fixed

- With `use_two_venv=False`, a virtualenv that could not be created was
  reported as a successful setup: the unpinned install meant as a retry ran
  outside any virtualenv and its exit status was the answer.
  `package_manager="auto"` on that path chose conda because the *submitting*
  machine had it.
- A `uv` named in `package_manager` and missing on the cluster is an error
  on both paths, not a quiet pip.
- Idle local loop workers died with a traceback each on Ctrl-C and at
  notebook kernel shutdown. They now ignore the interrupt; a running task
  still receives it.
- NumPy and other third-party objects as arguments or return values failed
  on `ssh` and `slurm` with `ModuleNotFoundError`.
- An `ssh` job that was killed or called `sys.exit` appeared to be running
  for a day; job ids could collide.
- `module_loads` did nothing in a non-login shell.
- `sbatch` refusing a job produced an `IndexError`.
- Two notebook sessions sharing a profile store erased each other's profiles;
  an unreadable store was overwritten with the built-in profiles.
- `clustrix credentials setup` could not change an existing value and
  cancelled itself on first run; the `.env` template stated the opposite
  precedence from the code.
- `%load_ext clustrix` failed when Clustrix was imported before a shell
  existed; `%clustrix config <profile>` raised `AttributeError`.
- `package_manager="auto"` on the single-venv path passed `uv` a flag it
  rejects.
- A local worker pool could exceed its size, and a decorated function called
  from inside a divided loop divided again in every worker.
- `key_file="~/.ssh/id_x"` failed with "No such file"; `cluster_ls("~/dir")`
  and every relative path under a `~` work directory returned nothing on a
  remote cluster.
- Saving a configuration into a directory that did not exist failed; the
  directory is now created, mode 0700.
- `import clustrix` failed without `ipywidgets` installed.
- The tutorials were listed out of order in the table of contents, and the
  documentation workflow ran `nbconvert` on a deleted notebook.

### Removed

- See "Breaking changes". Also removed: `setuptools-scm` from the build
  requirements (the sdist had contained `notes/`, `.claude/` and all of
  `tests/`), `[tool.flake8]` from `pyproject.toml` (flake8 never read it),
  and a tracked personal `clustrix.yml`.

### Security

- **An unknown SSH host is refused by default**, with the offered fingerprint
  in the message. The notebook widget no longer suggests a shell command; it
  shows the fingerprint and records exactly that key on request.
- **Installing a key no longer removes other Clustrix keys** from the
  cluster's `authorized_keys`. Setting up a second computer used to lock out
  the first.
- **`update_ssh_config` only replaces a block Clustrix wrote**; a `Host`
  block of yours with the same alias is left unchanged.
- **Passwords in the credential file round-trip exactly**; a value containing
  spaces or `#` used to be truncated.
- **A password or token typed into the notebook widget is not left in the
  form.** With "Save Widget State Automatically" on, Jupyter writes every
  field's content into the `.ipynb` file. The widget now moves a typed secret
  into memory and empties the field as soon as it is entered. The same now
  holds for **environment variables**: each value is held in memory and the
  field shows `NAME=••••`; an unchanged line keeps its value, a retyped one
  replaces it. A **Forget** button drops a held password or token, and
  **Duplicate** no longer copies one into the new profile. Not covered: a
  line that is only a valid name with no `=` stays in the field as typed.
  Checked in a real Jupyter Notebook 7 with widget-state saving on, light
  theme only.
- **Automatic staging no longer treats the home directory as a project.**
  With the home directory as the working directory and no git repository,
  a constant such as `"~/.kaggle/kaggle.json"` in an installed library was
  uploaded. See "Automatic staging of input files" for the rules now.
- **A symbolic link in a parent directory no longer hides a credential from
  staging** (`keys -> ~/.ssh`, then `keys/aws-prod` or `keys`).
- **Staged copies are no longer world-readable** on the cluster.

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

- **No computation has been run on a real GPU.** `@cluster(gpus=N)` was run
  against a SLURM whose two `gpu` resources are placeholders; device
  counting on an ssh host or locally was not run on a machine that has one.
- Only a `for` loop at the top level of the decorated function is divided,
  and only when it can be read from source. The run-time checks behind the
  analysis do not see state outside the values the loop was handed; see
  "What no check sees" in the parallel-loops guide.
- On `ssh` and `slurm` each piece of a divided loop is its own job; there
  are no SLURM job arrays.
- Output files a job writes are not brought back, only the return value.
  Automatic input staging does not apply to `async_submit` or to
  HuggingFace Jobs, and cannot see a path computed at run time.
- Nothing staged on a cluster is deleted automatically.
- PBS, SGE, Kubernetes and the cloud VM providers are absent (#140–#146).

## [0.1.1] and earlier

No changelog was kept. See the git history.
