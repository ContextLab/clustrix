# Issue #123 — full `except Exception` audit

Complete, per-site record for the swallow audit required by #159's definition
of done:

> `grep -rn "except Exception:\s*$" clustrix/` reviewed line by line, with a
> recorded decision per site

Measured on the branch `work/silent-failures`. "Before" is `214fbce^`,
"214fbce" is the first fix commit, "now" includes the follow-up commit that
closed the guard's bypasses.

## Counts

| Measure | Before | 214fbce | Now |
|-|-|-|-|
| `grep -rEn 'except Exception:[[:space:]]*$' clustrix/` | 36 | 18 | 14 |
| ... including handlers with a trailing `# pragma` / `# noqa` comment | 45 | 24 | 15 |
| `except Exception` handlers of every form (AST count, incl. `as e`) | 134 | 123 | 123 |
| Handlers whose body is **only** `pass` / `return None` / `continue` | 21 | 2 | 2 |
| Sites the guard now calls silent (any spelling, any shrug) | 32 | 12 | 3 |

Two corrections to the first version of this table, both found by
red-teaming it:

* the all-forms count was recorded as "123 → 123". That was the *after* number
  written into both columns. The real before figure is **134**: eleven
  handlers were narrowed to specific exception types, so they stopped being
  `except Exception` at all. A row that says a number did not move, when it
  moved by eleven, is the same kind of defect as the ones being audited.
* the last row is new, and is the one that matters most, because the row above
  it counts only the *shape* `pass`/`return None`/`continue`. The guard now
  asks whether a handler does anything about the failure, in any spelling, so
  it sees `return False`, `...`, `break`, a dead assignment, `except
  BaseException`, `contextlib.suppress(Exception)` and nine other bypasses
  that the shape-based count could not. Measured with the guard's own
  `scan_tree`, so the number and the test cannot disagree.

The three remaining sites are `executor_core.ClusterExecutor.__del__` and
`auth_fallbacks.get_cluster_password`, both on the allowlist in
`tests/unit/test_no_silent_swallows.py::JUSTIFIED_SWALLOWS` with the reason
written out there, and `notebook_magic_config.load_config_from_file`, which is
a real defect recorded in `TRACKED_DEFECTS` against
[#168](https://github.com/ContextLab/clustrix/issues/168) rather than
pretended to be a decision. Both lists are enforced in both directions — a new
unjustified swallow fails `test_the_lint_finds_no_unrecorded_silent_swallow`,
and a stale entry fails `test_the_allowlists_have_no_stale_entries`.

What the guard still cannot see is written down in `KNOWN_BLIND_SPOTS` in the
same file, with one executable example each, so a green run is not read as
more than it is.

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
| `loop_analysis.py:332` `_evaluate_binop` | **narrow** to `(TypeError, ValueError, OverflowError)` + debug log | an unknown loop bound, which is correct — but a bug *in the evaluator* was laundered into the same answer | `test_the_lint_finds_no_unrecorded_silent_swallow` (structural); `test_a_bound_that_cannot_be_folded_gives_no_range_rather_than_a_wrong_one` (behavioural) |
| `loop_analysis.py:638` argument binding | **narrow** to `(TypeError, ValueError)` + **warning** | "this function has no resolvable loop bounds" — the user asked for parallelism and silently ran serially | `test_arguments_that_cannot_be_bound_are_reported` |
| `utils.py:744` `_dumps_by_value` dill(recurse) | **log (debug) and continue** | correct — the next strategy is a genuine alternative; only the reason was lost | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:748` `_dumps_by_value` dill | **log (debug) and continue** | same | same |
| `utils.py:752` `_dumps_by_value` → `pickle.dumps` | **raise**, naming all three strategies | that the job had serialized. stdlib pickle stores functions by qualified name, so the payload *looked* fine here and died on the worker as "Can't get attribute". The function's own docstring already promised this. | `test_an_unserializable_payload_is_refused_rather_than_shipped_by_reference` |
| `utils.py:776` `serialize_function` `getsource` | **narrow** to `(OSError, TypeError)` + debug | correct (source is optional); narrowed for consistency with the site two lines below | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:806` `func_info["source"]` | **narrow** to `(OSError, TypeError)` + debug | correct — dill works from the code object | `test_serialize_function_source_exception` (rewritten), `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:880` `_source_checkout_path` | **narrow** + **warning** | "this is an ordinary installed package", so an editable checkout got pinned as `name==version` and the worker installed something else | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:961` `_distribution_records` name/version | **narrow** + **warning** | that the distribution did not exist, so it never reached the worker's requirements | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:968` `direct_url.json` read | **narrow** + **warning** | "an ordinary index install" — same consequence as `_source_checkout_path` | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:1098` `get_environment_info` | **log (warning)**, keep the empty return | "this environment has no packages", which is never true. Also now warns on a non-zero `pip list` exit, which was silent too. | `test_a_failed_environment_capture_is_reported` |
| `utils.py:1526` remote python probe | **narrow** to `(IndexError, ValueError)` + debug | correct — an unparseable banner is an unusable candidate, and `_select_remote_python` raises if none are | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `utils.py:1863` remote python probe (2) | **narrow** + debug | same | same |
| `credential_manager.py:444` `list_available_providers` | **log (warning) and continue** | correct listing, but a broken keychain was reported identically to an empty one | `test_the_lint_finds_no_unrecorded_silent_swallow` |
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

## Part D — round three: what the second red-team found (2026-08-20)

The verdict on the round above was *it does not hold*. Four findings, and the
first of them was the issue's own headline defect, still live.

### D1 — seven sites were annotated, not fixed

Of the nine swallows the inverted lint exposed, two were genuinely fixed (the
`executor_scheduler_status` slurm listing, and `resolve_remote_python`'s
version read, whose `"?"` is visible in the message that is raised). Seven got
a `logger.debug` line and nothing else: the caller still received the same
wrong answer, at a level the lint's own list treats as unwatched.

| Site | Was | Now | Killing test |
|-|-|-|-|
| `notebook_magic_widget._test_remote_connectivity` | `False` + debug — "could not tell" rendered by the caller as "Cannot reach {host}:{port}" | returns `(True/False/None, reason)`; `None` means the probe never ran, and the caller says so instead of blaming the host. Warning carries why. | `test_a_connectivity_probe_that_never_ran_is_not_reported_as_unreachable` |
| `modern_notebook_widget._looks_like_a_profile_bundle` | one `except Exception` + debug — unreadable indistinguishable from not-a-profile | split: `(OSError, UnicodeDecodeError)` warns and names the file; `(yaml.YAMLError, json.JSONDecodeError)` is the real answer and stays at debug | `test_a_profile_file_that_could_not_be_read_says_so` + `test_a_file_that_is_simply_not_a_profile_stays_quiet` |
| `utils._distribution_import_names` (`top_level.txt`) | debug | warning saying the consequence: the modules go missing from the reproducibility check, so a job that imports them is allowed to run and dies on the worker | `test_unreadable_top_level_metadata_is_reported` |
| `utils._distribution_import_names` (`dist.files`) | debug | same | `test_a_distribution_whose_file_list_cannot_be_read_is_reported` |
| `utils.resolve_remote_python.exists` | `False` + debug — a transport failure produced "No pythonX.Y on the remote host" | raises `RuntimeError` naming the connection failure and saying it is not evidence the interpreter is absent | `test_a_transport_failure_is_not_reported_as_a_missing_interpreter` |
| `loop_analysis.LoopDetector` iterable / condition rendering | debug + `"unknown"` | unchanged — `"unknown"` is already a value the caller can tell from a real answer | (accepted as partly fixed) |

Two further sites were exposed by tightening the lint (below) and fixed in the
same pass:

| Site | Now | Killing test |
|-|-|-|
| `loop_analysis.SafeRangeEvaluator.visit_Call` | narrowed to `(TypeError, ValueError, OverflowError, RecursionError)` + debug, matching the sibling in `_evaluate_binop`; a bug in the evaluator now surfaces instead of becoming "unknown bound" | `test_the_lint_finds_no_unrecorded_silent_swallow` |
| `notebook_magic_widget._update_existing_files` | warns that the overwrite list is empty because the scan failed, not because there are no files (this was Part C item 2) | `test_a_config_scan_that_failed_is_not_an_empty_config_directory` |

### D2 — `configure()` was torn in half by a concurrent `load_config`

`configure` took the lock only for `_ensure_default_config_loaded()`; its
`setattr` loop ran unlocked, reading the module global `_config` on every
iteration while `load_config` rebinds it. A load landing mid-loop left the
already-applied keywords on the discarded object and the rest on the new one:
`configure(cluster_host=…, username=…, cluster_port=…, remote_work_dir=…)`
returned success with `cluster_host` reverted to the file's value and the
other three applied. The whole function now runs under `_DEFAULT_CONFIG_LOCK`
and the loop writes to a target bound once.

`load_config`'s docstring said the winner is "the last caller to *enter*".
That was false against a `configure` competitor, which took no lock and so
could not queue behind anything; corrected to "the last caller to acquire the
lock", with the residue stated explicitly — an explicit file load still
replaces the configuration wholesale, including keywords set before it, and
the guarantee across threads is only that neither call is observed
half-applied.

Killing test: `test_a_configure_is_not_torn_in_half_by_a_concurrent_load`.
Unforced this is rare (0 in 200 trials with `sys.setswitchinterval` at a
nanosecond), so the interleaving is scheduled with a trace function on the
`configure` thread rather than waited for. Reverting the lock reproduces the
exact reported shape.

### D3 — the fork test could not see two thirds of the handler it tested

Measured against the old probe: deleting the whole `os.register_at_fork`
registration was invisible (5/5 green), and deleting only the
`_default_config_loading = False` clear failed 1 run in 5. The probe slept
0.5 s and then checked the *published* flag, and it built a
`multiprocessing.Process` — process setup, argument pickling and `Queue`
construction — between deciding the window was open and actually forking, so
the search routinely finished in the gap.

The probe now waits on `_default_config_loading`, which is true exactly while
the window is open, and forks with `os.fork()` directly (what
`multiprocessing` calls one layer down) microseconds after the check. It runs
five rounds, rewinding the module to its unsearched state between them, and
stops at the first round that is not correct.

Measured after the change, with the early stop removed so every round is
visible: registration deleted → 5/5 `DEADLOCK`; flag-clear deleted → 5/5
`HOST None`. As the test actually stands, both fail on round one.

### D4 — the lint was defeated 22 ways out of 24

Fourth AST guard in this repository, fourth defeat (12, 30, 14-and-16, 22).
The conclusion drawn here is that "did this handler do something useful about
the failure?" is not decidable by inspecting the handler, so the structure of
`tests/unit/test_no_silent_swallows.py` changed rather than the rule being
patched again:

* **the behavioural tests are now the guarantee.** Each drives a real clustrix
  surface with a real failure — a permission bit, a closed SSH transport,
  metadata that is not valid UTF-8, a name reserved never to resolve — and
  asserts the failure was audible: propagated, or logged at a level someone
  watches with the reason in it, or returned as a value the caller can tell
  apart from a real answer. Spelling is never examined. Same move as
  `tests/unit/test_persisted_files_are_private.py` after two static guards
  failed there.
* **the AST scan is labelled a lint**, and the tests are renamed to say so
  (`test_the_lint_finds_no_unrecorded_silent_swallow`,
  `test_the_lint_catches_every_known_bypass`,
  `test_the_lint_admits_what_it_cannot_see`).

The rule was also tightened where tightening is sound, which caught 13 of the
20 itemised bypasses:

* mentioning the bound name no longer counts on its own, only carrying it into
  a call, a raise or an assignment does — kills `f"{exc}"`, a bare `exc`,
  `None if exc else None`;
* `n += 1`, `del x`, `assert True`, `import os`, `global FLAG`, a subscript
  assignment and an attribute assignment stop counting as accounting unless
  they carry the exception — 7 spellings;
* the walk no longer descends into a nested `def`/`async def`, so a `raise`
  inside a function nothing calls is not read as a re-raise — 2;
* a constant-only logging call is content-free at *every* level, not just
  `debug`/`info`, so `logger.warning("")` and `logger.error("oops")` are
  caught — 2. `logger.exception(...)` is exempt: it attaches the traceback
  whatever its arguments.

Seven remain open and are now recorded as `BLIND_SPOTS` entries, each asserted
to be missed: six shapes of "any call at all reads as reporting"
(`_record(exc)` where `_record` is empty, `errors.append(exc)`, `int()`,
`NULL_REPORTER.report(exc)`, `if want_to_log(): pass`, `message = str(exc)`)
and `_ = exc`, which is indistinguishable from the `failure = exc` stash this
package really does. `KNOWN_BLIND_SPOTS` is 12 and is asserted equal to
`len(BLIND_SPOTS)`.

### D5 — M6 did not survive

The reported finding was that deleting `_default_config_loaded = True` from
`_load_config_locked` survives the full suite. Reproduced exactly as
described, at `5970455`, on the full gate command: **1 failed, 1781 passed** —
`test_an_explicit_load_supersedes_the_search` catches it. So the mutant was
already pinned; nothing was added for it beyond an explicit assertion on the
flag in that test, so a future failure names the line rather than only the
symptom.

### Counts for this round

Measured with one script over `clustrix/**/*.py` for both columns, so the two
numbers cannot be produced by different methods (which is how the "123 → 123"
error above happened):

| Measure | `5970455` | Now |
|-|-|-|
| catch-all handlers of every form (`except:`, `Exception`, `BaseException`, tuples) | 127 | 125 |
| bare `except Exception:` lines | 14 | 12 |
| handlers whose body is only `pass`/`return None`/`continue` | 2 | 2 |
| sites the lint calls silent and unrecorded | 0 | 0 |
| the second red-team's 22 probes, reconstructed as `BYPASSES` / `BLIND_SPOTS` entries | 2 caught | 15 caught, 7 recorded as blind spots |
| `BYPASSES` (each asserted caught) / `ACCEPTED` / `BLIND_SPOTS` | 17 / 8 / 7 | 33 / 8 / 12 |
