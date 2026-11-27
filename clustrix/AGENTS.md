# clustrix/ — THE PACKAGE

Flat layout, no subpackages. `__init__.__all__` is the public API; everything else is internal.

## MODULE GROUPS

| Group | Modules | Notes |
|-|-|-|
| Entry | `decorator.py`, `async_executor_simple.py` | `@cluster`; one shared async executor per process |
| Loop division | `parallelize.py`, `loop_workers.py` | `plan`/`explain`/`run`; local workers are plain subprocesses, not `multiprocessing`. The only loop analyser |
| Execution | `executor_core.py`, `executor_connections.py`, `executor_schedulers.py`, `executor_scheduler_status.py`, `local_executor.py`, `hf_jobs.py` | `executor.py` is a compat shim — edit the split modules, not the shim. `local_executor.py` holds `LocalJobManager` only |
| Config | `config.py`, `profile_manager.py` | `SUPPORTED_CLUSTER_TYPES` is the single backend list; no ClusterType enum |
| Auth/credentials | `auth_manager.py`, `auth_methods.py`, `auth_fallbacks.py`, `credential_manager.py`, `credential_release.py`, `secure_credentials.py`, `cli_credentials.py` | Credential priority: an environment variable overrides the same name in `.env`; `.env` supplies the rest; GitHub Actions last. `.env` values are single-quoted and read literally. Every stored SSH secret is released through `credential_release.release_credential` |
| SSH | `ssh_security.py`, `ssh_utils.py` | Host-key policy lives ONLY in `ssh_security.configure_host_key_policy` |
| Notebook | `notebook_magic.py`, `notebook_magic_core.py`, `notebook_magic_fallback.py`, `modern_notebook_widget.py`, `widget_style.py`, `widget_controls.py` | One widget (`ModernClustrixWidget`); `notebook_magic.py` re-exports core; fallback provides no-IPython stubs |
| Data | `auto_stage.py`, `staging.py` | `auto_stage` = input files found and staged on ssh/slurm; `staging` = declared `data_package` objects |
| Misc | `utils.py` (serialization, two-venv commands, env setup, dependency detection), `filesystem.py` (read-only `cluster_*`), `cli.py` | `cli.py` is the `clustrix` entry point (`pyproject [project.scripts]`) |

## CONVENTIONS

- Serialization pairs come in symmetric dill/cloudpickle twins (`serialize_function`/`deserialize_function`, `serialize_result`/`deserialize_result`). Change one side → change both.
- Every remote result is HMAC-SHA256 verified before unpickling; the per-job key travels via `CLUSTRIX_RESULT_KEY` from a 0600 file, never baked into world-readable `job.sh` (`utils.result_key_export_line`).
- Two-venv execution: VENV1 (only dill/cloudpickle) moves bytes and signs the result; VENV2 runs the function and is the only place user objects exist. Environments are content-keyed under `<remote_work_dir>/envs/` and reused. Commands generated in `utils.py`; round-trip tests in `tests/unit/test_two_venv_execution.py`.
- Removed config settings raise `ValueError` naming the replacement (`config._removed_setting_reason`) — never silently ignore a stale key.
- Extensive rationale comments with issue refs (#109–#159) are the house style. Keep them current when you change the code they explain.

## ANTI-PATTERNS

Root ANTI-PATTERNS apply in full — the two most likely to bite here: host-key policy only via `ssh_security.configure_host_key_policy`, and dill/cloudpickle only (never stdlib `pickle`) for function/result payloads. Module-local additions:

- No new module may read `cluster_type` as an enum — it is a plain `str`, validated against `SUPPORTED_CLUSTER_TYPES`.
- No test-awareness in production code (`isinstance(x, Mock)`, env sniffing for pytest).
- No UI side effects at import time — widget display is opt-in (`%%remote`, `display_modern_widget()`, or `CLUSTRIX_AUTO_WIDGET=1`).
- No second loop analyser and no second widget.
