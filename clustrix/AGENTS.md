# clustrix/ — THE PACKAGE

Flat layout: 34 modules, no subpackages. `__init__.py` re-exports 53 symbols — that is the public API; everything else is internal.

## MODULE GROUPS

| Group | Modules | Notes |
|-|-|-|
| Entry | `decorator.py`, `async_executor_simple.py` | `@cluster`; one shared async executor per process |
| Execution | `executor_core.py`, `executor_connections.py`, `executor_schedulers.py`, `executor_scheduler_status.py`, `local_executor.py`, `hf_jobs.py` | `executor.py` is a 39-line compat shim — edit the split modules, not the shim |
| Config | `config.py`, `profile_manager.py` | `SUPPORTED_CLUSTER_TYPES` is the single backend list; no ClusterType enum |
| Auth/credentials | `auth_manager.py`, `auth_methods.py`, `auth_fallbacks.py`, `credential_manager.py`, `secure_credentials.py`, `cli_credentials.py` | Credential priority: `.env` file → environment → GitHub Actions |
| SSH | `ssh_security.py`, `ssh_utils.py` | Host-key policy lives ONLY in `ssh_security.configure_host_key_policy` |
| Notebook | `notebook_magic.py`, `notebook_magic_core.py`, `notebook_magic_config.py`, `notebook_magic_widget.py`, `notebook_magic_fallback.py`, `modern_notebook_widget.py` | `notebook_magic.py` re-exports core; fallback provides no-IPython stubs |
| Data/packaging | `staging.py`, `file_packaging.py`, `dependency_analysis.py`, `loop_analysis.py` | staging = declared `data_package` objects; packaging = function shipping |
| Misc | `utils.py` (serialization, two-venv commands, env setup), `filesystem.py` (read-only `cluster_*`), `validation.py`, `cli.py`, `modern_notebook_widget.py` | `cli.py` is the `clustrix` entry point (`pyproject [project.scripts]`) |

## CONVENTIONS

- Serialization pairs come in symmetric dill/cloudpickle twins (`serialize_function`/`deserialize_function`, `serialize_result`/`deserialize_result`). Change one side → change both.
- Every remote result is HMAC-SHA256 verified before unpickling; the per-job key travels via `CLUSTRIX_RESULT_KEY` from a 0600 file, never baked into world-readable `job.sh` (`utils.result_key_export_line`).
- Two-venv execution: a bootstrap venv (only dill/cloudpickle) deserializes a payload that builds the real venv. Commands generated in `utils.py`; round-trip tests in `tests/unit/test_two_venv_execution.py`.
- Removed config settings raise `ValueError` naming the replacement (`config._removed_setting_reason`) — never silently ignore a stale key.
- Extensive rationale comments with issue refs (#109–#159) are the house style. Keep them current when you change the code they explain.

## ANTI-PATTERNS

Root ANTI-PATTERNS apply in full — the two most likely to bite here: host-key policy only via `ssh_security.configure_host_key_policy`, and dill/cloudpickle only (never stdlib `pickle`) for function/result payloads. Module-local additions:

- No new module may read `cluster_type` as an enum — it is a plain `str`, validated against `SUPPORTED_CLUSTER_TYPES`.
- No test-awareness in production code (`isinstance(x, Mock)`, env sniffing for pytest).
- No UI side effects at import time — widget display is opt-in (`%%remote`, `display_config_widget()`, or `CLUSTRIX_AUTO_WIDGET=1`).
