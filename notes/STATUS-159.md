# Issue #159 campaign — status

Snapshot. Full record: `notes/2026-08-20-issue-159-campaign.md` (~2,400 lines).

**Goal:** #159 fully addressed including anything surfacing along the way; all
changes merged to `master` with tests green; direct evidence posted per fix;
issues closed as appropriate.

**Protocol:** as each agent finishes, red-team with a NEW agent; fix with
another; repeat until clean. Then merge.

**Owner directive (new):** the credential-release decision is to become a
**single source of truth** — plan, implement, verify functionality preserved,
red-team with DIFFERENT subagents, merge.

## Issue state

| Issue | Fix rounds | Reviews | State |
|-|-|-|-|
| #152 | 6 | 5 | **DONE**, evidence posted — 1746 passed, 0 failed (my own run) |
| #165 | 5 | 5 | **DONE**, evidence posted — 15/15 mutants killed |
| #164 | 7 | 7 | **DONE**, evidence posted — 18 mutants, 3 benign survivors |
| #167 | 11 | 10 | route 12 closed (`720e363`); **fix round running** — 2 defects incl. a DoS we shipped |
| #123 | 6 | 5 | **CLEAN** at `f5cd22b`; F1 independently re-verified by me |
| gate | 1 | 2 | **DOES NOT HOLD** — route 13 found on the wire; fix round running |

## Branches (local only, nothing pushed)

| Worktree | Branch | Tip |
|-|-|-|
| `clustrix` | `work/priorities-and-docs` | `aa1345f` + uncommitted notes |
| `clustrix-fixes` | `work/fixes` | `720e363` (fix round in flight) |
| `clustrix-silent` | `work/silent-failures` | `f5cd22b` — clean |
| `clustrix-widget` | `work/widget-apply` | `feb1fd9` |
| `clustrix-env` | `work/named-env` | `3956d1f` |
| `clustrix-gate` | `work/credential-gate` | `238167a` (fix round in flight) |

`master` = `origin/master` = `f78d153`, fully contained in the branch. No PR yet.

## The credential decision: seven routes

| # | Route | State |
|-|-|-|
| 1 | `stored_host in target_host` | closed |
| 2 | working-directory `clustrix.yml` | closed |
| 3 | `ProfileManager.load_from_file` → `__post_init__` | closed |
| 4 | modern widget's Load dropdown | closed |
| 5 | `%%clusterfy` widget's `detect_config_files()` | round six |
| 6 | `get_env_password()` → `validate_cluster_auth` — **no gate at all** | live |
| 7 | `_offer_credential_storage` writes `SSH_HOST=<untrusted>` — **manufactures trust** | live |

Recorded on #167. Plan at `<scratchpad>/plan-choke-point.md` (651 lines):
one `release_credential(target, *, provider, config)`, a frozen
`CredentialTarget` whose constructor refuses an unnormalisable hostname, a
`CredentialRelease` carrying either a secret or a refusal, and a runtime
caller-module check. ~14 files / ~22 call sites, seven staged commits, widget
diff 8 lines. Smallest viable = steps 1-3, closing routes 1, 6, 7.

## Merge plan (all designed, decide per file not per branch)

1. `config.py` — 5 conflicts. Take **#123's** lazy structure *and* its error
   handling; add **#167's** provenance tags. #167's version reintroduces an
   `except Exception: continue` that #123 exists to remove — do not take it.
   Plus a one-line typing-import union with #165.
2. `configuration.rst` — same trap: #167's prose says a failing file "is skipped
   silently", which #123 makes false. Take #123's body, append #167's
   `.. warning::` unchanged.
3. `collect_execution_evidence.py`, `test_widget_profiles.py` — here **#167
   wins**; #123 keeps a `__dict__` splat that would copy #167's new private
   provenance attribute over the live config. Keep #123's comment.
4. Then: `config_field_names()` consolidation (4 duplicate `fields()` walks);
   `[all]` extras made a union of the others; CLAUDE.md corrections; CHANGELOG
   entries (draft at `<scratchpad>/changelog-draft.md`); re-run
   `local_parallel_comparison.ipynb` **at the merged tip** (recipe proven —
   produces exactly the `stream:stderr` the checker wants).
5. Gates, push, PR (body at `<scratchpad>/pr-body.md`), merge to `master`.
6. Close with evidence (#152 draft at `<scratchpad>/c152.md`, #165 at `c165.md`).
7. **Then** `git reflog expire --expire=now --all && git gc --prune=now` — only
   when no agent is committing. Authorized; tokens already dead (HTTP 401).

## Issues filed by this campaign

#168 residual silent-failure sites · #169 branch protection blocks docs-only PRs
· #170 `parallel=True` return shape · #171 profile rename clobbers

## Standing rules earned here

- **Never quote a credential-shaped literal** — describe it, name the file.
  Five agents plus me tripped the scanner today; every fix moved the literal,
  never the guard.
- **Three ways a mutation run lies**: stale `__pycache__` and a killed run both
  read as SURVIVED (safe); **a command that runs zero tests reads as KILLED**
  (false confidence). Assert a collected-count.
- **A guard is verified by deleting it and watching a test die**, never by the
  test shipped beside it. Six fixes shipped a justification written before it
  was checked.
- **A test written for a specific defect inherits that defect's parameters** —
  ask whether it exercises the *default* configuration. Caught three times.
- **Judge an issue by its own criteria, not its commit count** (#151, #117,
  #122 all looked finished and were not).
- **Set `HOME` in every standalone probe**; the autouse fixture covers pytest
  only. Three `known_hosts` pollutions today, all from that gap.

## Execution waves (dependency-aware)

**Wave A — running, fully independent (one worktree each, no shared files)**

| Task | Worktree | Blocks |
|-|-|-|
| #123 first review of the fix | `clustrix-silent` | B1 |
| #164 round-six review | `clustrix-env` | B1 |
| #167 round-six review | `clustrix-fixes` | B1 |
| Credential gate — implementation | `clustrix-gate` | B2 |

**Wave B — depends on A**
- **B1** fix rounds for any findings, one agent per issue, same worktrees
- **B2** gate red-team, **by DIFFERENT subagents than implemented it** (owner's
  explicit instruction), plus a functionality-preservation check: the two
  positive controls must still release the credential

**Wave C — depends on B (single-threaded, one sitting, by one person)**
1. merge in order: `fixes` → `silent-failures` → `widget-apply` → `named-env`
   → `credential-gate`
2. resolve `config.py` and `configuration.rst` **together** — take #123's
   structure and error handling, #167's provenance tags; delete #167's
   `except Exception: continue` and its "skipped silently" prose
3. resolve `collect_execution_evidence.py` and `test_widget_profiles.py`
   toward **#167**, keeping #123's comment
4. fold in: `config_field_names()` consolidation (4 duplicate `fields()`
   walks), `[all]` extras as a union, CLAUDE.md corrections (incl. the
   grep-is-a-companion-not-a-guard note), CHANGELOG entries
5. re-run `local_parallel_comparison.ipynb` **at the merged tip**
6. gates: pytest, flake8, mypy, pinned black, `sphinx -W`,
   `check_docs_markup.py`, `check_docs_examples.py` (**0 failures**, not a
   fixed total — branches add `.rst` blocks)

**Wave D — depends on C**
- push, open PR (`<scratchpad>/pr-body.md`), merge to `master`
- post evidence, close #152 #153 #157 #158 #164 #165 #166 #167 #147 #150 #116,
  roll up #159
- **last:** `git reflog expire --expire=now --all && git gc --prune=now`, only
  when nothing is committing

**Not merging:** #117, #122, #151, #111 stay open — their own criteria are not
met. #168-#171 are new and out of scope.

## Merge-order simplification (and its one hazard)

`work/credential-gate` was branched from `work/fixes`' tip (`7f82333`) and is a
**descendant** of it. So merging the gate **brings #167's work with it** — one
merge, one lineage, one `config.py` resolution instead of two.

**The hazard:** if #167's round-six review triggers a round-seven fix on
`work/fixes`, the two diverge and that simplification is lost. If that happens,
**rebase `work/credential-gate` onto the new `work/fixes` tip** rather than
merging both — the gate's commits are new files plus small call-site edits and
should rebase cleanly, whereas merging two branches that both rewrote
`config.py` reproduces the exact 5-conflict problem twice.

Revised merge order:

```
work/priorities-and-docs (base, #152 + docs)
  <- work/silent-failures   (#123)      resolve config.py + configuration.rst here
  <- work/widget-apply      (#165)      typing-import union
  <- work/named-env         (#164)      take ITS test_known_hosts_atomicity.py
  <- work/credential-gate   (#167 + the gate)   last, biggest config.py surface
```

Gate last, because it has the largest `config.py` surface and should be resolved
against a tree that already contains everything else — not the other way round.

## Merge simulation at current tips — simpler than planned

```
base aa1345f (#152 + docs)
  <- silent-failures ec0194b : CONFLICT  tests/unit/test_known_hosts_atomicity.py
  <- widget-apply    feb1fd9 : CLEAN
  <- named-env       3956d1f : CONFLICT  tests/unit/test_known_hosts_atomicity.py
  <- credential-gate 238167a : CONFLICT  clustrix/config.py
                                         clustrix/notebook_magic_widget.py
```

**Merging the gate last paid off.** The `config.py` / `configuration.rst`
four-way tangle between #123 and #167 has collapsed: #123 merges cleanly against
the base, and #167's `config.py` arrives once, via the gate, against a tree that
already contains #123. **One `config.py` resolution instead of two.**

Three conflicts remain, all with decided resolutions:

| file | parties | resolution |
|-|-|-|
| `tests/unit/test_known_hosts_atomicity.py` | #123, #164 (and #152 in base) | **take #164's** — the quantitative diagnosis is in its docstring, it has a real 120s deadline, and it distinguishes "exited without appending" from "appended nothing" |
| `clustrix/config.py` | #123 vs the gate (#167) | take **#123's** lazy structure and its error handling; add the gate's provenance. **Do not take #167's `except Exception: continue`** — that is the defect #123 exists to remove |
| `clustrix/notebook_magic_widget.py` | #165 vs the gate (#167) | additive on both sides; #167's edits are confined to `_initialize_configs` / `_on_apply_config` / the sidecar carry, #165's to the save path, `BACKEND_ONLY_FIELDS`, `set_choice` and the `cluster_type` options |

`configuration.rst` no longer conflicts at all.

All six tips re-verified clean under the pinned toolchain: black 26.3.1,
flake8 0 issues, mypy 0 errors.

## Route 13 — the gate does not hold (found 2026-08-20, second red-team)

**Merge of `work/credential-gate` is blocked on this.**

A refusal that still authenticates is not a refusal. `filesystem.py:224` passes
`look_for_keys=True`; `executor_connections.py:169` leaves `look_for_keys` and
`allow_agent` at paramiko's defaults. When the gate REFUSES, the code logs a
warning and connects anyway, so paramiko runs its own key-and-agent search.

Proven on the wire, both paths: a `./clustrix.yml` naming **only**
`cluster_host` — no `key_file`, no password, no stored credential —
authenticated as `('victim','publickey')` from `~/.ssh/id_rsa`, and the
executor then ran the job on the attacker's host. Strictly stronger than
route 10, which needed a `key_file:` entry. Route 10's test misses it only
because it plants the victim key in `tmp_path` rather than `~/.ssh`.

**13b:** `HfApi(token=...)` (`hf_jobs.py:257`, `staging.py:442`) passes no
`endpoint`, so huggingface_hub reads `$HF_ENDPOINT`. The gate released the HF
token for `huggingface.co` and the api object carrying it pointed at
`attacker.invalid`. The comment claiming "compiled in, so nothing untrusted
chose it" is false.

Also open on the gate: `derived_provenance` falls through to reporting on
`config.cluster_host` rather than the host it was asked about; falsy hostnames
(`0`/`None`/`False`/`[]`/`""`) skip the normalise guard and inherit trust;
`dataclasses.replace` launders a `record_host=False` config back to `runtime`;
mutants M6/M8/M10 survive the full suite; and the enforcement suite missed 2 of
3 planted leakers (`read_text()` on `~/.clustrix/.env`, and
`dict(os.environ).get(var)` — rule 4 matches `os.environ` only as a bare
`Attribute`).

## Route 12 follow-ups (`720e363`)

The fix itself **held** against 23 record shapes and every promotion attempt.
Four issues remain, one of them ours:

1. **A DoS we shipped.** `sorted(names)` raises `TypeError` on a non-string
   YAML key — `on:`, `yes:`, `null:`, `2:` all parse as bool/None/int. A cloned
   repo shipping such a `config.yml` makes Save fail outright. New against
   parent `9d0e568`. Fails closed, so no leak.
2. **A second unrecorded writer.** `ClusterConfig.save_to_file` /
   `config.save_config` write a flat config with no `config_sources`, so the CLI
   (`clustrix config --config-file ~/.clustrix/config.yml`) launders the same
   way. Pre-existing; weaker precondition than route 12 (user names the
   destination). The record needs to be a property of the write path, not of one
   button.
3. Mutant M9 survives: recording trusted sources too passes all 121 tests.
   Read side ignores it, so no leak — the invariant is just untested.
4. Host provenance is lost across the restart boundary (errs safe).

## Standing rules learned the hard way

- **The 3.10+ interpreter with deps is `/private/tmp/rt4venv/bin/python`**
  (3.11.16). System `python3` is anaconda base **3.9.13** — below the project
  floor, but it *does* have the deps, so it imports fine and runs a subset. That
  makes it a trap, not an obvious failure.
- **No agent may run `git checkout --`, `git reset --hard` or `git clean`.**
  A red-teamer's harness did this twice and clobbered another agent's
  uncommitted work in `clustrix-silent`. Pristine trees come from
  `git archive <rev> | tar -x -C <fresh dir>`.
- **A verdict is void if the tree moved under it.** The #123 red-team's
  four-defect verdict was against `5970455` while three remediation commits
  landed; it correctly disclaimed itself. Always record `git rev-parse HEAD`
  with a review, and the collected-test count as a second witness.

### Route 13 is wider than the red-team reported (found by me, 2026-08-20)

**A third call site, and it is user-reachable.** The review named
`filesystem.py:224` and `executor_connections.py:169`. There is a third:
`validation.py:98-99`, inside `validate_ssh_key_auth` (lines 75-126), passes
`look_for_keys=True, allow_agent=True` and calls **no gate at all**. The
`release_credential` at `validation.py:161` is in a *different* function
(`run_comprehensive_validation`, from 127), so it protects nothing here.

Reachable from the UI: `modern_notebook_widget.py:2054` calls
`validate_ssh_key_auth(config)` — the widget's "Test connection" button. An
attacker's working-directory config plus one click authenticates from
`~/.ssh/id_rsa` or a running ssh-agent. `validation.py:180` calls it again from
`run_comprehensive_validation`, i.e. before that function's own gate call.

The codebase is inconsistent rather than uniformly wrong:
`ssh_utils.py:148-149,198-199` and `validation.py:44-45` already pass
`look_for_keys=False, allow_agent=False`. The three `True` sites are outliers.

**13b is wider too.** `hf_hub_download` reads `$HF_ENDPOINT` exactly as
`HfApi` does, and two more sites hand it a real gate-released token —
`staging.py:706` and `staging.py:1445` — plus `cli_credentials.py:233`
(`HfApi(token=...)` then `whoami()`). The endpoint fix belongs in one helper
both constructors go through, on the same single-choke-point argument the gate
itself rests on.

Sent to the gate fix agent mid-round so it lands in one commit.

## Merge plan, re-derived at current tips (2026-08-20) — SUPERSEDES the earlier table

Simulated for real in an isolated clone (`<scratchpad>/mergesim`), not with
`merge-tree`: base `aa1345f` -> silent-failures -> widget-apply -> named-env.
Result `0382bc2`. black 26.3.1 clean (236 files), flake8 0, mypy 0 errors.

| Step | Conflict | Resolution |
|-|-|-|
| + `work/silent-failures` `f5cd22b` | `test_known_hosts_atomicity.py` | drop #123's inline loop; also drop the now-orphaned `import time` |
| + `work/widget-apply` `feb1fd9` | `clustrix/config.py` | one `typing` import line — widget-apply's is a strict superset, take theirs |
| + `work/named-env` `3956d1f` | `clustrix/config.py` | **semantic merge, not take-one-side** (below) |
| | `test_known_hosts_atomicity.py` | **take named-env's**, and delete base's helper |

**Correction to the earlier plan.** I previously recorded "take #164's version"
for `test_known_hosts_atomicity.py`. That named the wrong branch. Three
independent fixes exist for the same flake, and the best one is **named-env's**
`_wait_until_the_writer_has_written`:

- the quantitative diagnosis the criterion actually described — first append at
  ~0.45s idle, up to 3.2s oversubscribed 32 ways, 152 of 160 sampled starts
  over 1.0s under load — against the base helper's vaguer "over 1.5 seconds"
- a 120s deadline against the base's 60s
- `known_hosts.stat().st_size` rather than `len(read_text())`: cheaper, and it
  does not read a file that is actively growing
- `try: ... finally: process.kill()` always kills; the base's
  `except subprocess.TimeoutExpired:` only kills on timeout
- and the base helper has a latent bug — it calls `process.communicate()`
  inside the helper while the call site calls it again afterwards.
  named-env uses `process.stderr.read()`

The two sides differ *only* in the helper, its call site and the import, so
taking named-env's whole file loses nothing from #123 or #164.

**The `config.py` / named-env conflict is a real semantic merge.** HEAD carries
the `cluster_type` validation, the locked `target = _config` apply loop, and
`config_field_names()` / `split_config_kwargs()`; named-env carries the
`conda_env_name` validation and the *old unlocked* loop. Keep HEAD's structure,
graft named-env's `conda_env_name` validation in beside the `cluster_type` one
at 8-space indent (inside `with _DEFAULT_CONFIG_LOCK:`), and let the old
unlocked loop die. **A careless "keep ours" silently drops `conda_env_name`
validation** — the refusal would then happen at submission, with the job
directory already created on the cluster and the pickle already uploaded.

`work/fixes` and `work/credential-gate` are NOT in this simulation — both are
being modified by fix agents. The gate branched from `work/fixes` at `7f82333`
and `work/fixes` is 4 commits ahead since, so the rebase decision waits for
both to land.

### A merge-only defect: #123's message vs #164's assertion (fixed `f2a152d`)

The first full run of the merged tree was **1 failed, 2155 passed** —
`test_named_environment.py::...::test_without_one_the_build_still_happens`.

**Neither branch's suite could see it.** The message lives on #123's branch and
the assertion on #164's; each is green alone. It exists only in the merge. This
is the argument for running the real merge rather than trusting a conflict
count: `git` reported no conflict in either file.

The probe at `utils.py:2369` used to swallow its own failure and return
`False`, sending the caller into a message stating flatly that no matching
interpreter exists on the cluster — a confident claim about a machine clustrix
never managed to ask. `5db9631` replaced that swallow with a `RuntimeError`
that names `cluster_host` directly and falls back to the literal "the remote
host" only when the field is empty. `test_without_one_the_build_still_happens`
asserted the bare word `"remote"` appeared, which had been true only because
the old message was generic.

Fixed on `work/named-env` at **`f2a152d`**: the assertion now checks the
requirement the test exists for — that the reader is told which end failed —
accepting either the configured host or the fallback wording. Not a weakening;
confirmed still armed by a mutant that names neither, which fails it. 198
passed on `work/named-env` alone, so the repair does not depend on #123 being
present.

## #159's own definition of done (read from the issue, 2026-08-20)

Quoted from the issue body, not paraphrased:

- [ ] #152, #153, #157, #158 closed with reproductions that now fail
- [ ] #123's remaining items closed or split into concretely-scoped successors
- [ ] Each fix mutation-tested
- [ ] `grep -rn "except Exception:\s*$" clustrix/` reviewed line by line, with a
      recorded decision per site

So #159 proper is **#152, #153, #157, #158, #123** — narrower than the closure
list I had been carrying. Everything else (#164, #165, #166, #167, #147, #150,
#116) is work that surfaced along the way and is in scope under the owner's
standing goal, not under #159's checklist.

**Nothing is closed yet.** All of #152, #153, #157, #158, #123, #164, #165,
#166, #167, #147, #150, #116 are still open; evidence was posted on some
without closing them.

**Five new issues surfaced during the campaign** and need triage before the
closure set is final: #168 (three silent-failure sites left over from #123 —
this is exactly the "concretely-scoped successors" the DoD allows), #169
(branch protection vs a path-filtered workflow; may be repo settings with no
code fix), #170 (`parallel=True` return shape — reads as an undecided design
question rather than a defect), #171 (renaming a profile onto an existing name
destroys the other), #172 (`detect_gpu_capabilities` reports a GPU when it
cannot parse `nvidia-smi`).

Note the DoD's last bullet is a deliverable in its own right: a **recorded
decision per swallow site**, not just a clean grep. #123's branch has the
blind-spot family list (A..J, 25 entries); that is the artifact which should
satisfy it, and it needs checking against the actual grep output before #159
can be closed.

Also note #159's body cites `config.py:431` and `:596` for the import-time
side effects; both line numbers are stale (`_config` is at `:556`,
`_load_default_config()` at `:721`). Do not quote the issue's numbers back as
evidence.

## Merged tree of the four stable branches: GREEN

`aa1345f` + silent-failures `f5cd22b` + widget-apply `feb1fd9` + named-env
`f2a152d` → **2156 passed, 17 skipped, 27 deselected, 0 failed** (5m44s),
black 26.3.1 clean (236 files), flake8 0, mypy 0 errors.

## Triage of the five campaign-era issues (#168-#172)

| Issue | Verdict | Remaining |
|-|-|-|
| #168 | **PARTIAL** — 1 of 3 sites fixed | 2 sites, ~20 lines + 2 tests |
| #169 | untouched; CI config, no code on any branch | drop `paths:` from `fast_ci.yml` |
| #170 | **design decision, not a defect** | do NOT gate the merge on it |
| #171 | untouched (the collision case) | ~30 lines |
| #172 | untouched | ~30 lines |

**#168.** Site 2 is fixed on `work/silent-failures` (`5db9631`,
`notebook_magic_widget.py:941-953` — the scan failure is now logged as "the
overwrite list is empty because the scan failed, not because there are no
files"), armed by
`test_a_config_scan_that_failed_is_not_an_empty_config_directory`, which
`chmod 0o000`s a real directory. Sites 1 and 3 are live on all six branches
and have no test: `notebook_magic_config.load_config_from_file` still returns
`{}` for any read failure (its docstring calls that "a deliberate contract"),
and `utils.py:838-839` rebinds `func = cloudpickle.loads(...)` in an
`except Exception:` with no chaining, losing the original reason.

**#170 stays open.** `_combine_local_results` is byte-identical on all six
branches (`if len(results) == 1: return results[0] ... return results`), and
the behaviour is *deliberately pinned* by `tests/unit/test_local_cores.py:450`
and `docs/source/limitations.rst:303`. Choosing among the issue's options is a
user-visible API decision plus a release note, not a defect fix. #171 and #172
are also framed as decisions, but each has an obviously-safe default, so they
are closeable inside a defect campaign.

**#169 cannot be verified locally.** Branch protection requires
`["Tests Status", "CI Status"]`; `CI Status` comes from `fast_ci.yml`, which is
`paths:`-filtered, so a docs-only PR never gets the context. `tests.yml` has no
filter, so `Tests Status` is fine. The definition of done is a docs-only PR
that actually merges.

## Worktree contention map (keep this current before dispatching)

| Worktree | Branch | State |
|-|-|-|
| `clustrix` | `work/priorities-and-docs` | idle (base) |
| `clustrix-fixes` | `work/fixes` | **BUSY** — route-12 follow-ups |
| `clustrix-silent` | `work/silent-failures` | **BUSY** — red-team, needs a clean tree |
| `clustrix-widget` | `work/widget-apply` | idle |
| `clustrix-env` | `work/named-env` | idle |
| `clustrix-gate` | `work/credential-gate` | **BUSY** — route-13 fix |
| `clustrix-leftovers` | `work/leftovers` | **BUSY** — #172 + #169 |

#171 touches `notebook_magic_widget.py` and #168's site 1 touches
`notebook_magic_config.py`; both collide with `work/fixes`, so they wait for
the route-12 agent to land rather than being dispatched in parallel.

## Round results, 2026-08-20 (later)

**Route-12 follow-ups: `9973d82` on `work/fixes`.** 1985 passed / 0 failed.
The agent rejected my suggested `sorted(names, key=repr)` as patching one call
site while leaving `_rebuild_config_dropdown`'s two sorts and the paste door
broken; it fixed at the boundary where document keys become names
(`config_name_from_document`) instead. It also found the same laundering hole
in **`ProfileManager.export_profile`** — export untrusted, import, trusted — a
third writer nobody had named, and made the record a property of the write
path (`config_document()`) inherited by `save_to_file`, `save_config`, the CLI
and export/import alike. M9 now dies.

**#165 is NOT broken.** That agent flagged `_on_apply_config` passing `name=`
to `configure()` as a live no-op. True on `work/fixes` alone; **resolved by the
merge** — at the merged state the handler calls
`split_config_kwargs(config_data, PROFILE_BOOKKEEPING_KEYS, reset_fields=...)`
and then `configure(**settings)`, so `name` is stripped. Do not reopen #165 on
that report.

**#123 red-team at `f5cd22b`: real breaks, fix round dispatched.**
- **S1**: five error reports can be deleted *simultaneously* and the suite is
  byte-identically green (1850 passed either way). For four of them the report
  is the only thing separating a real failure from a normal answer. The lint
  cannot backstop them — all are narrow `except` clauses (family D), so
  `except RecursionError: return None` with no log at all reaches master green.
- **S2**: the prose test does not read the per-family spelling counts. A
  fabricated "Nine spellings are recorded below" added to family E SURVIVED.
- **F1 is armed only as a pair.** My own verification reverted both halves at
  once, so it proved the fix real but not the test granular. M14 (drop
  `configure`'s lock, keep `target`), M15 (revert `target`, keep the lock) and
  M16 (drop `load_config`'s lock) each survive alone — and `load_config`'s
  lock, whose docstring calls it load-bearing, **has no test at all**.
- `except*` **is** correctly seen: `ast.TryStar` is in `TRY_NODES`, probed
  directly and CAUGHT. The blind-spot arithmetic is honest
  (6+1+1+1+1+1+1+8+1+4 = 25, families A..J = 10); one entry is mis-filed.

## Stray `clustrix.yml` in checkout roots — test pollution, and it hides

`.gitignore:35` lists `clustrix.yml`, so a config written into a checkout root
**never appears in `git status`**. It persists silently, and a config in the
working directory is exactly the untrusted-provenance path the credential work
is about, so leftover state can change later runs.

| Worktree | File | Verdict |
|-|-|-|
| `clustrix` | 403 bytes, **Jun 29 2025**, holds `Ndoli Cluster` | **the user's own file — do not touch** |
| `clustrix-fixes` | 286 bytes, Aug 20 2026 11:10, holds `Integration Test Config` | agent/test pollution — quarantined to `<scratchpad>/quarantine/`, not deleted |

Neither contains a secret-bearing key (checked by key name, values never read).
The autouse `isolate_home` fixture isolates `HOME`, not the working directory —
that is the gap. Finding the test that writes it is the lowest-priority item on
the #123 fix round.

## Sequencing settled by measurement, not guesswork (2026-08-20)

Three orderings were tried in the isolated clone at
`work/fixes` = `9973d82`, `work/credential-gate` = `238167a`:

| Approach | Cost |
|-|-|
| base → silent → widget → named-env → **fixes** | fixes conflicts: 5 files, **16 regions** |
| base → **fixes** → silent-failures → … | fixes CLEAN, then silent-failures: 6 files, **14 regions** |
| **rebase** gate onto fixes | **fails at the gate's first commit** (`1bf4654`, "give the credential decision one home") — 14 commits each able to re-conflict |
| **merge** fixes → gate | 6 files, **13 regions**, ONE reconciliation |

Order barely matters between the first two: the #123 ↔ #167 `config.py`
reconciliation has to happen once whichever way round, and only the sense of
"ours" changes. **Do not rebase the gate.** It branched from `work/fixes` at
`7f82333` and fixes has moved 4 commits since; rebasing replays 14 commits
through the same conflict repeatedly, and it already fails on the first.

**Final plan:**

1. base `aa1345f` → silent-failures → widget-apply → named-env.
   **Already done and verified: 2156 passed, 0 failed**, black/flake8/mypy
   clean, `sphinx -W` clean, markup checker clean, examples checker 152/152.
   (Re-do once the #123 fix round lands, since silent-failures will move.)
2. **Merge `work/fixes` into `work/credential-gate`** — one reconciliation,
   13 regions across `auth_methods.py`, `config.py`,
   `notebook_magic_widget.py`, `profile_manager.py`, `test_auth_fallbacks.py`,
   `test_a_cloned_repository_cannot_take_your_password.py`.
3. Merge the combined gate branch into the line from step 1 — this is where
   the big `config.py` reconciliation lands (8 regions when tried against
   fixes alone, one of them ~545 lines).
4. Then the whole-tree gates again, from scratch.

Two conflicts are unavoidable and everything else is bookkeeping: fixes↔gate,
and #123↔#167 in `config.py`.

## Route 13 closed at four of six sites (`f31a98f` on `work/credential-gate`)

2028 passed / 0 failed; black 26.3.1, flake8, mypy clean.

The gate now decides paramiko's own discovery rather than each call site:
`CredentialRelease.local_identities` (= `hostless_secret_refusal(...) is None`),
read by `filesystem.py` and `executor_connections.py`, with
`validation.validate_ssh_key_auth` asking the same rule directly.

**Wire proof**, fresh process, redirected `HOME`+`CLUSTRIX_CONFIG_DIR`, real
`LocalSSHServer`, victim key at `~/.ssh/id_rsa`, `./clustrix.yml` naming only
`cluster_host`: **before** `[('victim','publickey')]` on all three paths,
**after** `[]` on all three. Control arm: a trusted host
(`~/.clustrix/config.yml`) still gets all three — not a blanket disable.
Route 10's test was repaired too: `_victim_keypair` now writes to
`~/.ssh/id_rsa` rather than `tmp_path`, which is why it had missed this.

**My third site was confirmed and a fourth was found.** `validation.py:98` was
exactly as I described. `ssh_utils.setup_ssh_keys` additionally offers *every*
key in `~/.ssh` to `config.cluster_host`, one `key_filename=` at a time via
`detect_existing_ssh_key` — reachable from `clustrix ssh-setup`, both widgets,
and `setup_auth_with_fallback`. So route 13 had **five** sites, not the two the
red-team reported.

**A sixth is still open** and is why route 13 is not yet closed:
`notebook_magic_widget._test_ssh_connectivity` (`:1058`, called at `:1162`)
takes a **dict of widget fields** rather than a `ClusterConfig`, and calls
`ssh_client.connect(**connect_params)` with the defaults left in place. Fix
round dispatched: build a real `ClusterConfig` via `split_config_kwargs` — the
route `_on_apply_config` already uses — and ask the same rule. Explicitly NOT a
second copy of the rule.

**13b closed properly.** One helper, `huggingface_client_kwargs()`, across all
five sites plus a real_world debug script. Wire proof with `HF_ENDPOINT` at a
loopback listener: before, 2 requests carrying the sentinel token to
`http://127.0.0.1:…`; after, 0 token-bearing requests and endpoint
`https://huggingface.co`. AST rule 7 blocks a sixth unpinned client.

**`dataclasses.replace` deliberately NOT "fixed", and said so.** Every loader
records the hostname itself (`record_host=True`), so an attacker's config
survives `replace`; only the `record_host=False` *guess* is reversed. Making
the guess survive would mean a permanent hostname claim derived from a guess —
the thing that over-tainted 96,739 of 96,740. Documented, with a test pinning
both halves. This is the right call.

Mutants M6, M8, M10 now die. Enforcement gained rule 5 (bulk `os.environ` via
`dict()`, `{**}`, `.copy()`, alias, argument, iteration) and rule 6 (the
credential file: `".env"` literal plus `env_file`/`env_file_path`), so the
planted L1/L2 leakers are now caught. The documented limits were rewritten to
list what actually remains rather than staying silent about L1 and L2.

## #172 and #169 fixed on `work/leftovers` (`ea8aedf`, `732a5f0`, `e0c90bd`)

1760 passed / 17 skipped / 0 failed (baseline 1746, +14 new); black 26.3.1,
flake8, mypy clean.

**#172 — contract chosen: a third state, not a raise.** `gpu_available=True`
now means a GPU was *positively identified*; unparseable `nvidia-smi` output
sets `gpu_detection_inconclusive` and records the offending lines in
`detection_errors`, claiming nothing about availability or count. Parsing is
all-or-nothing per response and requires exactly 5 fields (was `>= 5`).

The reasoning for not raising, which is the right distinction: in
`_select_remote_python` no interpreter means no job can run at all, so raising
is the only honest answer; here a caller proceeds perfectly well without a
device list — it just must not be told a GPU exists. `/proc/driver/nvidia` and
`lspci` still run afterwards and can give a genuine yes on their own evidence.

RED evidence against a pristine `aa1345f` (unpacked with `git archive`, tests
copied in): 9 of 9 failed, and the unparseable and partial-parse cases failed
on the *behaviour* (`assert True is False` on `gpu_available`), not merely on a
missing key. No mocks: a real `LocalSSHServer`, a real `nvidia-smi` executable
on its PATH emitting the bytes under test, a real paramiko client, with `nvcc`
and `lspci` shadowed so the result comes from the response rather than the
host. The issue's own surviving mutant now dies.

A second commit was needed because `enhanced_setup_two_venv_environment` had
two printed sentences for three outcomes and announced "No GPUs detected" for
the unreadable case — the same defect one layer up.

**#169 — `paths:` dropped from `fast_ci.yml`'s `pull_request` trigger.** No job
depends on the filter: no job-level `paths`, no `if` keyed on changed files,
and `status-check` already runs `if: always()` across all four jobs. The repo
is public so Actions minutes are free; a companion workflow would duplicate the
context name across two files. The `push:` filter stays, since `CI Status` is
not required for develop pushes.

**Explicitly unverifiable locally, and that is the important part:** nobody can
confirm from here that GitHub triggers the workflow, publishes `CI Status`, and
clears the merge block. **Someone must open a docs-only PR against `master`**
(touching only e.g. `README.md`), confirm Fast CI runs and `CI Status` goes
green, and confirm the PR becomes mergeable without an admin override. Until
that happens #169 is fixed-but-unproven and must not be closed.
