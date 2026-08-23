# PROJECT KNOWLEDGE BASE

**Generated:** 2026-08-21 16:23 UTC
**Commit:** 7d28428
**Branch:** work/priorities-and-docs

## OVERVIEW

Clustrix is a Python distributed computing framework: `@cluster` on a function serializes it (dill/cloudpickle, by value) and runs it on a configured backend — `local`, `ssh`, `slurm`, `huggingface` (HF Jobs). Those four are the whole list (`clustrix.config.SUPPORTED_CLUSTER_TYPES`); pbs/sge/kubernetes/AWS/GCP/Azure/Lambda raise `ValueError` (issues #140–#146). Python >=3.10, version 0.2.0, beta.

**CLAUDE.md is the deep curated knowledge base** (architecture, security invariants, mocking policy, two-venv execution). This file is the map; read CLAUDE.md before non-trivial work.

## STRUCTURE

```
clustrix/
├── clustrix/            # the package — flat, 34 modules (see clustrix/AGENTS.md)
├── tests/               # unit/ + real_world/ + integration/ + comprehensive/ (see tests/AGENTS.md)
├── scripts/             # dev/ops tooling; aws/ is operator cleanup, NOT a backend
├── docs/                # source/ (Sphinx) + evidence/ (committed proof) + build/ (generated)
├── notes/               # session notes; per user policy, update as work proceeds
├── .claude/             # pm command system (commands/pm, scripts/pm, rules, agents)
├── .github/workflows/   # tests.yml, fast_ci.yml, real-world-tests.yml
├── build/               # STALE setuptools output — see NOTES
├── htmlcov/, performance_test_results/, docs/build/   # generated; ignore
└── pyproject.toml       # the ONLY pytest/coverage config; black/mypy/flake8 too
```

## WHERE TO LOOK

| Task | Location | Notes |
|-|-|-|
| Add a backend | `config.SUPPORTED_CLUSTER_TYPES` + `executor_core.py` dispatch + `executor_schedulers.py` | Gate: real job on real hardware, evidence committed |
| Change execution flow | `executor_core.py` (ClusterExecutor), split across `executor_connections/_schedulers/_scheduler_status` | `executor.py` is a 39-line shim |
| Touch serialization | `utils.py` `serialize_function`/`deserialize_function`, `generate_two_venv_execution_commands` | Keep each serialize/deserialize pair symmetric; never stdlib `pickle` |
| Config change | `config.py` (ClusterConfig, configure, load_config) | No env-var overlay exists; only `CLUSTRIX_CONFIG_DIR` + `password_env_var` |
| SSH/auth | `ssh_security.py` (host keys), `ssh_utils.py`, `auth_manager.py`, `credential_manager.py` | Never `AutoAddPolicy` directly |
| Notebook UI | `notebook_magic_core.py` (%%remote, %clustrix), `modern_notebook_widget.py` | Widget never auto-displays on import unless `CLUSTRIX_AUTO_WIDGET=1` |
| Data staging | `staging.py` (`data_package`, `materialize_packages`) | Declaration only; nothing inferred; nothing auto-deleted |
| Quality gates | `scripts/pre_push_check.py` (retries 5x), `scripts/check_quality.py` | Run repeatedly until ALL pass before commit |
| Regenerate backend evidence | `scripts/verify_cluster_usecases.py`, `scripts/collect_execution_evidence.py` | Output committed under `docs/evidence/` |
| CI changes | `.github/workflows/` | `real-world-tests.yml` has NO push/PR trigger deliberately (credentialed jobs); secrets gate via `check-secrets` job outputs — `secrets` context is illegal in `if:` |

## CODE MAP

Centrality from codegraph (Python LSP not installed; ruff is lint-only).

| Symbol | Type | Location | Refs | Role |
|-|-|-|-|-|
| `configure` | function | `clustrix/config.py:559` | 288 | Singleton config entry point; validates all keys before applying any |
| `ClusterExecutor` | class | `clustrix/executor_core.py:27` | 81 | Dispatch, submission, HMAC-verified result retrieval |
| `cluster` | decorator | `clustrix/decorator.py:58` | public API | `@cluster`; extras limited to `hf_*` + `key_file` — `cluster_type=` is NOT accepted |
| `ClusterConfig` | dataclass | `clustrix/config.py` | high | Plain-`str` `cluster_type`; no ClusterType enum |
| `ClusterfyMagics` | class | `clustrix/notebook_magic_core.py:69` | 6 | `%%remote`, `%clustrix`, deprecated `%%clusterfy` alias |
| `LocalExecutor` | class | `clustrix/local_executor.py` | — | Real local parallelism; `choose_executor_type` at :339 |
| `HFJobsManager` | class | `clustrix/hf_jobs.py` | — | HuggingFace Jobs backend; 256 KB payload cap |
| `data_package` | function | `clustrix/staging.py` | — | Declared data staging; large packages go to a private HF dataset repo |

## CONVENTIONS

- black line-length 88, target py310, **pinned `black==26.3.1`** (unbounded `>=` broke CI, #110); flake8 max-line 88, extend-ignore E203/W503; mypy python_version 3.10, `files=["clustrix/"]`, tests ignored, `follow_imports="skip"`.
- **pyproject.toml is the only pytest config.** No pytest.ini/tox.ini/setup.cfg — the first found shadows this block (#130); `tests/unit/test_pytest_config.py` enforces it.
- pytest `--strict-markers`; all 6 markers registered in pyproject. `testpaths` must never list `tests/integration` (billable, #109).
- Coverage `fail_under = 66` — a floor 2 points under measured 68%, not a target (#115).
- Pre-commit runs on python3.12 explicitly (system python3 may be 3.9, which the project does not support).
- Version string lives in 4 places and must stay identical: `pyproject.toml`, `setup.py`, `clustrix/__init__.py`, `docs/source/conf.py`.
- Comments explain *why*, often with issue refs (#109–#159). Match that style; do not strip them.

## ANTI-PATTERNS (THIS PROJECT)

- Never `set_missing_host_key_policy(paramiko.AutoAddPolicy())` — go through `ssh_security.configure_host_key_policy`.
- Never stdlib `pickle` in the two-venv handoffs — dill/cloudpickle only, symmetric pairs.
- Never a mock as a fallback when the real thing is unavailable — fail instead. Production code must never know it is being tested (no `isinstance(x, Mock)`; `grep -rn "unittest.mock\|MagicMock" clustrix/` stays empty).
- Never weaken a failing test; fix the code or explicitly rewrite a wrong assertion.
- Never document unsupported backends (pbs/sge/k8s/cloud VMs), cost monitoring, or HF Spaces as working.
- Never add `@cluster(cluster_type=...)` to examples — it is ignored with a warning; backend is set via `configure()`.
- `auto_gpu_parallel` does nothing (deleted; it fabricated results). Don't document it as a feature.
- No `cluster_put`/`cluster_get` — the `cluster_*` fs helpers are read-only by design.

## UNIQUE STYLES

- Honesty-first docs: verified vs unsupported backends stated up front; evidence transcripts committed under `docs/evidence/`.
- Defensive validation with explanatory errors (removed settings raise `ValueError` naming the replacement; unknown config keys get did-you-mean).
- One shared async executor per process (`decorator._shared_async_executor`), context-manager support on `SimpleAsyncClusterExecutor`.

## COMMANDS

```bash
pip install -e ".[dev]"                 # dev env (widget extra needed for widget tests)
python scripts/pre_push_check.py        # black+flake8+mypy+pytest, retries until clean — run before EVERY commit
pytest tests/ -m "not real_world" --ignore=tests/real_world --ignore=tests/integration   # what CI runs
pytest tests/unit/ -q                   # fast loop
python scripts/collect_execution_evidence.py   # real job per reachable backend; needs credentials
cd docs && make html                    # docs build
```

## NOTES

- **`build/lib/clustrix/` is stale**: it still contains `kubernetes/`, `cloud_providers/`, `pricing_clients/`, `cost_providers/` — modules deleted from the source tree. Grep results there are ghosts; exclude `build/` from searches.
- `htmlcov/`, `performance_test_results/`, `docs/build/` are generated output, not source.
- `tests/integration/` provisions real billable AWS resources; refuses without `CLUSTRIX_ALLOW_BILLABLE=1` (guard reads `config.args`, deliberately).
- `remote_work_dir` must be on a filesystem compute nodes see — `/tmp` dies with exit 127 on SLURM.
- Fresh count: 21 of 166 test modules use `unittest.mock` (issue #117 migrates them; new tests must not add to it).
