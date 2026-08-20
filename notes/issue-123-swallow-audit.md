# Issue #123 — full `except Exception` audit

Complete, per-site record for the swallow audit required by #159's definition
of done:

> `grep -rn "except Exception:\s*$" clustrix/` reviewed line by line, with a
> recorded decision per site

Measured on the branch `work/silent-failures` after the fixes.

## Counts

| Measure | Before | After |
|-|-|-|
| `grep -rEn 'except Exception:[[:space:]]*$' clustrix/` | 36 | 18 |
| ... including handlers with a trailing `# pragma` / `# noqa` comment | 45 | 24 |
| `except Exception` handlers of every form (AST count, incl. `as e`) | 123 | 123 |
| Handlers whose body is **only** `pass` / `return None` / `continue` | 21 | 2 |

The two remaining shrug-shaped handlers are `executor_core.__del__` and
`auth_fallbacks.get_cluster_password`; both are on the allowlist in
`tests/unit/test_no_silent_swallows.py::JUSTIFIED_SWALLOWS`, with the reason
written out there. That allowlist is enforced in both directions — a new
unjustified shrug fails `test_every_bare_swallow_has_a_recorded_decision`, and
a stale entry fails `test_the_allowlist_has_no_stale_entries`.

The ~99 `except Exception as e` handlers were reviewed as a class rather than
individually: they all bind the exception, and spot-checking confirmed they
either re-raise it, log it, or put it in a user-facing message. They are not
instances of this defect. The three worth a follow-up are listed at the end.

## Part A — sites changed (21)

Line numbers are pre-change, matching the issue text.

| Site | Decision | What the caller used to believe | Killing test |
|-|-|-|-|
| `config.py:716` `_load_default_config` (found-but-unloadable file) | **raise** `ConfigFileError` | "there is no configuration file", while the user's `cluster_host` silently never took effect | `test_a_found_but_unusable_file_raises_instead_of_reverting_to_defaults` |
| `config.py:712` `Path.exists()` outside the try | **narrow + log** (`OSError` → warn, skip candidate) | nothing — it propagated EACCES and made `import clustrix` raise | `test_import_survives_a_config_directory_it_cannot_read` |
| `config.py` `Path.cwd()` (new, found by the tests) | **log and continue** | n/a — `getcwd()` on a deleted cwd would have raised out of the search | `test_an_unreadable_candidate_is_skipped_and_reported` |
| `config.py:721` `_load_default_config()` at import | **defer to first use** | n/a — import-time side effect | `test_import_opens_no_file_in_the_users_home_or_cwd` |
| `executor_scheduler_status.py:117` `check_job_status` | **log (warning) + return `"unknown"`** | "the job is still running" — so `wait_for_result` burned the whole `job_wait_timeout` and then blamed a job that had already stopped | `test_an_unmeasurable_error_file_is_unknown_not_running` |
| `executor_scheduler_status.py:344` traceback scan | **log and continue** (warning names the skipped file) | that the scan was exhaustive when it had skipped a file | `test_a_file_that_cannot_be_scanned_for_a_traceback_is_named` |
| `executor_scheduler_status.py:594` `get_error_log` | **log + report honestly in the return value** | "No error log found" — a claim about the cluster, made after every read failed | `test_an_unreadable_error_log_says_so_instead_of_saying_there_is_none` |
| `loop_analysis.py:332` `_evaluate_binop` | **narrow** to `(TypeError, ValueError, OverflowError)` + debug log | an unknown loop bound, which is correct — but a bug *in the evaluator* was laundered into the same answer | `test_every_bare_swallow_has_a_recorded_decision` (structural); `test_a_bound_that_cannot_be_folded_gives_no_range_rather_than_a_wrong_one` (behavioural) |
| `loop_analysis.py:638` argument binding | **narrow** to `(TypeError, ValueError)` + **warning** | "this function has no resolvable loop bounds" — the user asked for parallelism and silently ran serially | `test_arguments_that_cannot_be_bound_are_reported` |
| `utils.py:744` `_dumps_by_value` dill(recurse) | **log (debug) and continue** | correct — the next strategy is a genuine alternative; only the reason was lost | `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:748` `_dumps_by_value` dill | **log (debug) and continue** | same | same |
| `utils.py:752` `_dumps_by_value` → `pickle.dumps` | **raise**, naming all three strategies | that the job had serialized. stdlib pickle stores functions by qualified name, so the payload *looked* fine here and died on the worker as "Can't get attribute". The function's own docstring already promised this. | `test_an_unserializable_payload_is_refused_rather_than_shipped_by_reference` |
| `utils.py:776` `serialize_function` `getsource` | **narrow** to `(OSError, TypeError)` + debug | correct (source is optional); narrowed for consistency with the site two lines below | `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:806` `func_info["source"]` | **narrow** to `(OSError, TypeError)` + debug | correct — dill works from the code object | `test_serialize_function_source_exception` (rewritten), `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:880` `_source_checkout_path` | **narrow** + **warning** | "this is an ordinary installed package", so an editable checkout got pinned as `name==version` and the worker installed something else | `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:961` `_distribution_records` name/version | **narrow** + **warning** | that the distribution did not exist, so it never reached the worker's requirements | `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:968` `direct_url.json` read | **narrow** + **warning** | "an ordinary index install" — same consequence as `_source_checkout_path` | `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:1098` `get_environment_info` | **log (warning)**, keep the empty return | "this environment has no packages", which is never true. Also now warns on a non-zero `pip list` exit, which was silent too. | `test_a_failed_environment_capture_is_reported` |
| `utils.py:1526` remote python probe | **narrow** to `(IndexError, ValueError)` + debug | correct — an unparseable banner is an unusable candidate, and `_select_remote_python` raises if none are | `test_every_bare_swallow_has_a_recorded_decision` |
| `utils.py:1863` remote python probe (2) | **narrow** + debug | same | same |
| `credential_manager.py:444` `list_available_providers` | **log (warning) and continue** | correct listing, but a broken keychain was reported identically to an empty one | `test_every_bare_swallow_has_a_recorded_decision` |
| `credential_manager.py:496` `get_credential_status` | **log (warning) and continue** | correct — the credentials are already in hand; only the attribution was lost | same |
| `auth_manager.py:179` `_should_store_in_env_file` | **log (debug) and continue** | correct — the terminal prompt asks the same question and gets the same answer. Debug, not warning: a notebook with no display reaches here every time. | same |
| `utils.py:259` `detect_loops` | **log (warning) + return None** | "no parallelizable loops". Running the loop whole is always correct, so this stays log-and-continue — but the user lost their parallelism with no explanation. | same |
| `executor_core.py:376` `__del__` | **keep, justified** | nothing — a finaliser has no caller to give an answer to, and the interpreter discards anything it raises. `with ClusterExecutor(...)` is the real teardown story. | on the allowlist |

## Part B — remaining bare handlers, decision recorded, no change (24)

| Site | Function | Decision |
|-|-|-|
| `executor_core.py:375` | `__del__` | **keep** — allowlisted; see above |
| `auth_fallbacks.py:139` | `get_cluster_password` | **keep** — allowlisted. Colab's `userdata.get` raises for a secret that is simply not set, the ordinary case for three of the four name variants. Three further credential sources follow, and the caller raises if none supplies a password. |
| `dependency_analysis.py:348` | `_analyze_filesystem_calls` | **keep** — substitutes the literal `'<unparseable>'`, which *is* the report of the failure. The value is only ever read by a human. |
| `hf_jobs.py:491` | `submit_job` | **keep** — re-raises after un-staging the payload. Already correct. |
| `executor_connections.py:148` | `setup_ssh_connection` | **keep** — logs at warning; the comment already records the decision (other credential sources follow; `connect()` raises if none authenticates). |
| `executor_connections.py:347` | `disconnect` | **keep** — logs at warning; the transport close below reclaims the descriptor, so "this connection is now closed" stays true. |
| `executor_scheduler_status.py:287` | `_check_job_completion_with_retry` | **keep** — `slurm_files = []` when the glob fails. Feeds an *additional-context* scan; the authoritative verdict comes from the accounting query below it. |
| `executor_scheduler_status.py:602` | `get_error_log` | **keep** — already logs at warning ("error.pkl verified but could not be deserialized; falling back to text logs") and the text-log fallback is a real alternative. |
| `executor_scheduler_status.py:731` | `extract_original_exception` | **keep** — already logs at warning; returning None sends the caller to the text log, which it handles. |
| `loop_analysis.py:275` | `visit_Call` | **keep** — sets `self.safe = False`, which is the explicit "I could not determine this range" flag the caller checks. Not a shrug. |
| `loop_analysis.py:541` | `_analyze_for_loop` | **keep** — substitutes the string `"unknown"` for an un-unparseable iterable. Display only. |
| `loop_analysis.py:588` | `_analyze_while_loop` | **keep** — as above, for a while condition. |
| `utils.py:244` | `detect_loops` | **keep** — already `logger.info`s that the range could not be evaluated and refuses to parallelize. |
| `utils.py:671` | `_unpicklable_location` | **keep** — the exception *is* the signal: this is the probe that decides which child object is unpicklable, and the failure is what it is looking for. |
| `utils.py:889` | `deserialize_function` | **keep** (flagged below) — falls back from dill to cloudpickle; if cloudpickle also fails its exception propagates. |
| `utils.py:1133`, `utils.py:1140` | `_distribution_import_names` | **keep** — falls back from `top_level.txt` to the file list and then to an empty set. Used only to *widen* an unreproducible-package warning, so an empty answer cannot cause a wrong submission to be accepted. |
| `utils.py:2056`, `utils.py:2072` | `exists`, `resolve_remote_python` | **keep** — `False` / `"?"` feed a message that already ends in `raise RuntimeError("No pythonX.Y on the remote host...")`. The failure is reported. |
| `cli_credentials.py:404` | `edit_credentials_command` | **keep** — prints "please edit the file manually", which is the correct instruction when no editor could be launched. |
| `modern_notebook_widget.py:1592` | `_looks_like_a_profile_bundle` | **keep** — answers "is this offerable as a profile bundle". Unreadable and malformed both correctly mean "no". |
| `notebook_magic_widget.py:1017` | `_test_remote_connectivity` | **keep** — a socket that will not open is exactly what "not reachable" means. The `False` is the measurement. |
| `notebook_magic_config.py:115` | `load_config_from_file` | **no change, flagged below** |
| `notebook_magic_widget.py:941` | `_update_existing_files` | **no change, flagged below** |

## Part C — found, not fixed (follow-up)

1. **`notebook_magic_config.py:115`** — `load_config_from_file` returns `{}` for
   a file the user explicitly selected in the widget and which turned out to be
   unreadable or malformed. That is the same defect as `config.py:716`: an
   instruction accepted, discarded, and reported as success. Not changed here
   because the widget's error surface is a separate piece of work; the fix
   should mirror `ConfigFileError`.
2. **`notebook_magic_widget.py:941`** — `_update_existing_files` empties the
   "Overwrite:" dropdown when the directory scan fails, so existing config
   files become invisible and the user creates a duplicate instead of
   overwriting. Should report the scan failure.
3. **`utils.py:889`** — `deserialize_function` falls back from dill to
   cloudpickle. If cloudpickle also fails, the surfacing exception names only
   cloudpickle's failure; dill's reason is lost, and dill is the one that was
   supposed to work. Should collect both, the way `_dumps_by_value` now does.
4. **`black==26.3.1` is not installable on this project's floor.** It requires
   Python >= 3.10, and the package supports 3.9 (the repo's own interpreter
   here is 3.9.13). `pip install -e ".[dev]"` under 3.9 therefore cannot
   satisfy the pin in `pyproject.toml` / `setup.py`. Formatting for this branch
   was verified with a real 26.3.1 installed under Python 3.12.
