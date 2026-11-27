# PROJECT KNOWLEDGE BASE

**Updated:** 2026-11-27 (branch `ccr-d2f64557-m5wit9`)

## OVERVIEW

Clustrix is a Python distributed computing framework: `@cluster` on a function serializes it (dill/cloudpickle, by value) and runs it on a configured backend — `local`, `ssh`, `slurm`, `huggingface` (HF Jobs). Those four are the whole list (`clustrix.config.SUPPORTED_CLUSTER_TYPES`); pbs/sge/kubernetes/AWS/GCP/Azure/Lambda raise `ValueError` (issues #140–#146). Python >=3.10 (tested to 3.13), beta. A top-level loop in a decorated function is divided automatically (`parallelize.py`); local input files are staged automatically on ssh/slurm (`auto_stage.py`).

**CLAUDE.md is the deep curated knowledge base** (architecture, security invariants, mocking policy, two-venv execution). This file is the map; read CLAUDE.md before non-trivial work.

## STRUCTURE

```
clustrix/
├── clustrix/            # the package — flat, no subpackages (see clustrix/AGENTS.md)
├── tests/               # unit/ + real_world/ + comprehensive/ (see tests/AGENTS.md)
├── scripts/             # dev/ops tooling; aws/ is operator cleanup, NOT a backend
├── docs/                # source/ (Sphinx) + evidence/ (committed proof) + build/ (generated)
├── notes/               # session notes; per user policy, update as work proceeds
├── .claude/             # pm command system (commands/pm, scripts/pm, rules, agents)
├── .github/workflows/   # tests.yml, fast_ci.yml, real-world-tests.yml
├── build/               # STALE setuptools output — see NOTES
├── htmlcov/, performance_test_results/, docs/build/   # generated; ignore
└── pyproject.toml       # the ONLY package metadata and pytest/coverage config; black/mypy too (flake8 reads .flake8; setup.py is a shim)
```

## WHERE TO LOOK

| Task | Location | Notes |
|-|-|-|
| Add a backend | `config.SUPPORTED_CLUSTER_TYPES` + `executor_core.py` dispatch + `executor_schedulers.py` | Gate: real job on real hardware, evidence committed |
| Change execution flow | `executor_core.py` (ClusterExecutor), split across `executor_connections/_schedulers/_scheduler_status` | `executor.py` is a shim. Result polling starts at 1 s and backs off to `job_poll_interval` |
| Loop division | `parallelize.py` (`plan`, `explain`, `run`), `loop_workers.py` (local worker subprocesses), `decorator._run_split_locally/_run_split_remotely` | The only analyser. Static rule + three run-time checks; cannot see state outside the loop's values. No SLURM arrays, no verified GPU path |
| Input staging | `auto_stage.py` (`prepare`), `@cluster(data=, no_data=)`, `ClusterConfig.auto_stage`, `auto_stage_max_bytes` | ssh/slurm only; not async, not HF; outputs are not brought back; nothing auto-deleted |
| Touch serialization | `utils.py` `serialize_function`/`deserialize_function`, `generate_two_venv_execution_commands` | Keep each serialize/deserialize pair symmetric; never stdlib `pickle` |
| Config change | `config.py` (ClusterConfig, configure, load_config) | No env-var overlay exists; only `CLUSTRIX_CONFIG_DIR` + `password_env_var`. `configure()` validates types and ranges. `ssh_port` is only a deprecated alias for `cluster_port`. `clustrix config`/`clustrix load` persist to `<config dir>/config.yml` |
| SSH/auth | `ssh_security.py` (host keys, `trust_offered_host_key`), `ssh_utils.py`, `auth_manager.py`, `credential_manager.py`, `credential_release.py` | Never `AutoAddPolicy` directly; every stored secret is released through `release_credential` |
| Notebook UI | `notebook_magic_core.py` (%%remote, %clustrix), `modern_notebook_widget.py`, `widget_style.py` | One widget. Form is a draft; Apply saves and makes live. Never auto-displays on import unless `CLUSTRIX_AUTO_WIDGET=1` |
| Declared data | `staging.py` (`data_package`, `materialize_packages`) | Explicit packages; a large one lives in a private HF repo that an ssh/slurm worker cannot read; nothing auto-deleted |
| Docs | `docs/source/`, `scripts/check_docs_examples.py`, `scripts/check_docs_markup.py` | Every Python block is executed or, if marked `# cluster-required`, statically checked |
| Quality gates | `scripts/pre_push_check.py` (retries 5x), `scripts/check_quality.py` | Run repeatedly until ALL pass before commit |
| Regenerate backend evidence | `scripts/verify_cluster_usecases.py`, `scripts/collect_execution_evidence.py` | Output committed under `docs/evidence/` |
| CI changes | `.github/workflows/` | `real-world-tests.yml` has NO push/PR trigger deliberately (credentialed jobs); secrets gate via `check-secrets` job outputs — `secrets` context is illegal in `if:` |

## CODE MAP

| Symbol | Type | Location | Role |
|-|-|-|-|
| `cluster` | decorator | `clustrix/decorator.py` | `@cluster`; extras limited to `hf_*`, `key_file`, `data`, `no_data` — `cluster_type=` is NOT accepted |
| `explain` | function | `clustrix/parallelize.py` | Says whether a function's loop would be divided, and why not |
| `configure` | function | `clustrix/config.py` | Singleton config entry point; validates all keys and values before applying any |
| `ClusterConfig` | dataclass | `clustrix/config.py` | Plain-`str` `cluster_type`; no ClusterType enum |
| `ClusterExecutor` | class | `clustrix/executor_core.py` | Dispatch, submission, HMAC-verified result retrieval |
| `LocalJobManager` | class | `clustrix/local_executor.py` | The `cluster_type="local"` backend (no pool; `LocalExecutor` is gone) |
| `HFJobsManager` | class | `clustrix/hf_jobs.py` | HuggingFace Jobs backend; 256 KB payload cap |
| `ModernClustrixWidget` | class | `clustrix/modern_notebook_widget.py` | The notebook widget |
| `ClusterfyMagics` | class | `clustrix/notebook_magic_core.py` | `%%remote`, `%clustrix`, deprecated `%%clusterfy` alias |
| `data_package` | function | `clustrix/staging.py` | Declared data packages |

## CONVENTIONS

- black line-length 88, target py310, **pinned `black==26.3.1`** (unbounded `>=` broke CI, #110); flake8 is configured in `.flake8` only; mypy python_version 3.10, `files=["clustrix/"]`, tests ignored, `follow_imports="skip"`.
- **pyproject.toml is the only pytest config.** No pytest.ini/tox.ini/setup.cfg — the first found shadows this block (#130); `tests/unit/test_pytest_config.py` enforces it.
- pytest `--strict-markers`; all 4 markers registered in pyproject (`cluster_network` is registered by `tests/conftest.py`).
- Coverage `fail_under = 66` — a floor 2 points under measured 68%, not a target (#115).
- Pre-commit runs on python3.12 explicitly (system python3 may be 3.9, which the project does not support).
- Version string lives in 3 places and must stay identical: `pyproject.toml`, `clustrix/__init__.py`, `docs/source/conf.py` (`setup.py` is a shim).
- Comments explain *why*, often with issue refs (#109–#159). Match that style; do not strip them.

## ANTI-PATTERNS (THIS PROJECT)

- Never `set_missing_host_key_policy(paramiko.AutoAddPolicy())` — go through `ssh_security.configure_host_key_policy`.
- Never stdlib `pickle` in the two-venv handoffs — dill/cloudpickle only, symmetric pairs.
- Never a mock as a fallback when the real thing is unavailable — fail instead. Production code must never know it is being tested (no `isinstance(x, Mock)`; `grep -rn "unittest.mock\|MagicMock" clustrix/` stays empty).
- Never weaken a failing test; fix the code or explicitly rewrite a wrong assertion.
- Never document unsupported backends (pbs/sge/k8s/cloud VMs), cost monitoring, or HF Spaces as working.
- Never add `@cluster(cluster_type=...)` to examples — it is ignored with a warning; backend is set via `configure()`.
- `auto_gpu_parallel` does nothing (deleted; it fabricated results). Don't document it as a feature.
- Never document the removed chunk-keyword protocol (`_chunk_range_*`, `_parallel_*`), `clustrix.loop_analysis`, `LocalExecutor`, `clustrix.validation`, or the legacy widget (`EnhancedClusterConfigWidget`, `notebook_magic_widget`, `notebook_magic_config`).
- Never describe loop division as a proof of safety: say what no check can see (module caches, files, databases, GPU RNG).
- No `cluster_put`/`cluster_get` — the `cluster_*` fs helpers are read-only by design.

## UNIQUE STYLES

- Honesty-first docs: verified vs unsupported backends stated up front; evidence transcripts committed under `docs/evidence/`.
- Defensive validation with explanatory errors (removed settings raise `ValueError` naming the replacement; unknown config keys get did-you-mean).
- One shared async executor per process (`decorator._shared_async_executor`), context-manager support on `SimpleAsyncClusterExecutor`.

## COMMANDS

```bash
pip install -e ".[dev]"                 # dev env (widget extra needed for widget tests)
python scripts/pre_push_check.py        # black+flake8+mypy+pytest, retries until clean — run before EVERY commit
pytest tests/ -m "not real_world" --ignore=tests/real_world   # what CI runs
pytest tests/unit/ -q                   # fast loop
python scripts/collect_execution_evidence.py   # real job per reachable backend; needs credentials
cd docs && make html                    # docs build
```

## NOTES

- **`build/lib/clustrix/` is stale**: it still contains `kubernetes/`, `cloud_providers/`, `pricing_clients/`, `cost_providers/` — modules deleted from the source tree. Grep results there are ghosts; exclude `build/` from searches.
- `htmlcov/`, `performance_test_results/`, `docs/build/` are generated output, not source.
- `remote_work_dir` must be on a filesystem compute nodes see — `/tmp` dies with exit 127 on SLURM.
- No test module uses `unittest.mock` (issue #117); the one file the pattern matches is `tests/unit/test_no_mocks_in_shipped_code.py`, which names what it scans for. Recount before quoting a figure: `grep -lE "unittest\.mock|Mock\(|MagicMock\(|@patch" $(find tests -name "test_*.py") | wc -l`.
