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

## Pre-push secret check — the repo's scanner does NOT cover history

`scripts/check_for_secrets.py` scans `git ls-files`, i.e. tracked files in the
**working tree**, and explicitly excludes `.git`. A credential committed in an
earlier commit and removed later passes it. We are about to push ~70 commits
from a campaign that handled credentials, so the working-tree gate is not
sufficient on its own.

**Working tree: clean.** 23 passed in every worktree — `clustrix`,
`clustrix-fixes`, `clustrix-env`, `clustrix-widget`, `clustrix-leftovers`, and
the merged simulation tree.

**History: scanned separately**, by driving the scanner's own `TOKEN_PATTERNS`
and `PEM_BODY_LINE` over `git log -p master..<branch>` for all seven branches.
Result: **1 hit, benign**, and no history surgery is warranted.

The hit is the AWS *documentation* example access-key id, in a notes table row
that recorded removing scanner bait — and reproduced the literal while doing
so. It is a public documentation constant with no secret value, the scanner
allowlists it as a placeholder, and it legitimately appears in
`scripts/check_for_secrets.py` and `tests/unit/test_check_for_secrets.py`,
which must be able to detect it.

The notes copy was mine and broke the standing rule I set after tripping the
scanner twice: **describe the literal, name the file, never reproduce it**.
Now rewritten to describe both literals; 0 occurrences remain in `notes/`.

**Add to the pre-push checklist:** run the history scan as well as the
working-tree one. The working-tree scanner passing is not evidence that the
commits being pushed are clean.

## #172's fix was largely moot in practice (red-team RT-1, 2026-08-20)

The third state is **unreachable on real hardware**. `detect_gpu_capabilities`
tries methods in sequence; when `nvidia-smi` is unreadable the next method,
`lspci | grep -i nvidia | wc -l`, runs and re-sets `gpu_available=True`,
clearing the inconclusive state. Probed against the real in-process SSH server
with a real single-GPU `lspci` listing (VGA function plus its companion audio
function):

```
gpu_available=True  gpu_detection_inconclusive=False  gpu_count=2  gpu_devices=[]
summary = "GPU detected (2 devices), setting up GPU-enabled VENV2..."
```

That is the pre-fix defect verbatim: a confident yes, an empty device list, and
a count of PCI **functions** rather than GPUs. The new state survives only when
`lspci` also finds nothing — i.e. only when there is no GPU.

**The tests could not see it because the fixture stubs `lspci` to `exit 1`.**
This is a fourth instance of the campaign's recurring pattern: a test written
for a specific defect inherits that defect's parameters and leaves the ordinary
path uncovered, while coverage tooling reports the line covered.

**RT-2**, pre-existing but now load-bearing:
`ls -la /proc/driver/nvidia/gpus/ | wc -l` minus 2 counts the `total` line, so
1 real GPU yields `gpu_count == 2`. It also clears `inconclusive`.

**RT-3**: #169's guard misses a job-level `if:`. Adding
`if: github.event_name == 'push'` to `status-check` silences the required
`CI Status` context on every PR while all four tests pass — the guard only
inspects `on.pull_request`.

**RT-4**, accepted: `REQUIRED_CONTEXTS` is a hardcoded tuple, honestly labelled
as a copy, and verified accurate against the live API today.

Confirmed sound and not to be churned: the `!= 5` tightening loses no shape
that previously gave a correct answer; all-or-nothing per response is right and
pinned; `status-check` requiring `result == "success"` across all four jobs
does fail on skipped/cancelled. 13 of 14 mutants were caught.

Fix round dispatched. **Asking the red-team specifically whether a later
detection method re-sets the flag is what surfaced this** — the fix itself was
correct in isolation and would have shipped looking complete.

## Systematic hunt for the RT-1 shape — one known, one candidate

RT-1's shape is "an honest refusal overridden by a later, weaker method". I
scanned every function in `clustrix/` with an AST pass for functions that
assert a positive result (`*avail*`, `*detect*`, `*found*`, `*success*`,
`*present*`, `*support*`, …) as `True` in more than one place. Exactly two:

| Function | Assignments | Status |
|-|-|-|
| `utils.py:2778 detect_gpu_capabilities` | `gpu_available` ×3 | **RT-1, known, fix dispatched** |
| `utils.py:1408 setup_two_venv_environment` | `conda_available` ×2 | **candidate — see below** |

**The conda candidate — stated precisely, because I have NOT verified it is a
defect.** What I did verify:

- The first probe looks for `etc/profile.d/conda.sh` across eight locations and
  on success sets both `conda_available = True` and
  `conda_setup_prefix = "source <path>"` (`:1473-1474`).
- The `else` branch runs `bash -lc 'conda --version'` and sets
  `conda_available = True` on the substring `"conda"` — leaving
  `conda_setup_prefix` **empty** (`:1478-1481`).
- Downstream handles an empty prefix by simply omitting the source:
  `_conda_envs_exist` (`:1322`) and `venv_info` consumption (`:2130`) both do
  `f"{prefix} && " if prefix else ""`.
- **Neither branch is asserted by any test.** No test references either of the
  two distinct print strings; the two unit modules that drive
  `setup_two_venv_environment` do not check the conda outcome.

What I have NOT verified, and will not claim: that this actually breaks a job.
The concern is that the fallback detects conda inside a **login** shell
(`bash -lc`) while asserting availability with no way to activate it, so if the
job script's shell does not put conda on `PATH`, `conda run` / `conda activate`
would fail at run time with nothing to fall back on. Proving or disproving it
needs a `LocalSSHServer` with a real `conda` executable on PATH and no
`conda.sh` — exactly the technique the #172 round used for `nvidia-smi`.

Queued, not dispatched (three agents already running). If it proves out it is a
new issue; if not, the branch still needs a test, because an untested fallback
in the environment-setup path is how #172's defect survived.

## Route 13: six sites closed, a seventh dispatched (`9a7e54f`)

2036 passed / 0 failed; black 26.3.1, flake8, mypy clean.

**Site 5 — the widget.** `_test_ssh_connectivity` now builds a real
`ClusterConfig` (`_config_under_test`, stamped with the same
`config_source_map` provenance Apply uses) and asks
`release_credential(..., sources=("config-field","stored-credential","environment"))`;
`look_for_keys`/`allow_agent` start `False` and take `release.local_identities`.
Wire evidence: before `[('victim','publickey')]`, after `[]`; control arm with
`~/.clustrix/config.yml` authenticates both before and after.

**My direction for that fix was wrong and the agent was right to ignore it.** I
told it to use `split_config_kwargs` / `PROFILE_BOOKKEEPING_KEYS`. Those do not
exist on `work/credential-gate` — they come from `work/widget-apply`, so I was
reasoning from the merged tree rather than the branch being edited. **Merge-time
note:** once widget-apply and the gate are both in, check whether
`_config_under_test` and `split_config_kwargs` overlap, and collapse them if so.

**Site 6, found by the new AST rule** (any `x.connect(...)` with keywords must
name both settings; it reads the innermost enclosing function so `**kwargs`
sites count): `ssh_utils.deploy_public_key`. Measured at `f31a98f`:
`RESULT True AUTH [('victim','publickey')]` — the victim's key authenticated
*and* the requested key was installed. Now gated.

Also fixed: `CredentialTarget.for_config` turned a `None` host into the
hostname `"None"`, so the `ValueError` that four call sites catch never fired
for the no-host case.

**Site 7, dispatched.** `deploy_public_key` shells out to `ssh-copy-id` before
the paramiko path, and the subprocess is outside both the gate and the AST
rule. Two defects there, both confirmed by reading the code:

1. No `IdentitiesOnly=yes`, so OpenSSH offers the `-i` key **plus** the default
   identities **plus** any running agent — route 13 by subprocess.
2. `StrictHostKeyChecking=accept-new` is hardcoded, so the one path that
   reaches for OpenSSH applies a weaker host-key policy than every paramiko
   call in the codebase, where the default is deliberately `reject`.

`UserKnownHostsFile={_user_known_hosts_path()}` there is correct and must stay:
OpenSSH resolves `~` from the passwd database rather than `$HOME`, so without
it clustrix verifies against a file it is not writing to.

**Known and left alone, correctly:** `EnhancedClusterConfigWidget._on_apply_config`
raises `ValueError: Unknown configuration parameter: name` for any named
profile — pre-existing, documented in the suite's route-5 comment, orthogonal
to credentials.

## RT-1/RT-2/RT-3 fixed (`ab99700` #172, `25640bd` #169)

1772 passed / 0 failed (baseline 1760, +12); black 26.3.1, flake8, mypy clean.

**Contract chosen, and it is the right one: each method is trusted only for
what it observes.** `lspci` genuinely proves NVIDIA hardware is attached, so
`gpu_available=True` stays — that is real evidence, not a guess. But it counts
PCI *functions*, so `gpu_count` is now `None` rather than a fabricated number,
`gpu_devices` stays `[]` on both fallbacks, and the summary gained a
number-free sentence for that case.

**RT-2 was worse than reported.** `ls -la … | wc -l` minus 2 counted the
`total` line, so 1 GPU read as 2 — and an **empty** `/proc/driver/nvidia/gpus/`
read as **1**, a GPU conjured from nothing. Fixed by dropping the flags: plain
`ls` emits one line per GPU, so the count is real and there is nothing to
subtract. Unlike the `lspci` count this one is determinable, so it is fixed
rather than refused.

**RT-3**: M13 applied to pristine `732a5f0` gave 4 passed — it really did
survive. The guard now requires an `always()`-shaped `if:` on any job
publishing a required context, and requires it to be carried when the job has
`needs:`. M13 now fails 2/2, as do dropping `if: always()` and
`always() && github.event_name != 'pull_request'`.

**RT-4 declined, deliberately and correctly.** `REQUIRED_CONTEXTS` stays a
hardcoded tuple: reading branch protection at test time would make which
assertions run depend on network reachability and a mutable remote setting —
flaky by definition — and CI holds no credentials for that endpoint. Verified
accurate today against the live API.

## Process defect: agents share the scratchpad and clobber each other

The #172 agent's mutation copy was **destroyed mid-run by another agent** using
the same scratch path; it re-ran all mutant evidence in a uniquely-named
directory. Its worktree was never affected.

This is the same class as the earlier `git checkout --` incident: parallel
agents colliding over shared state. Both prompts and practice now require a
**uniquely-named scratch directory per agent**, alongside the existing ban on
destructive git in shared worktrees.

## Second red-team on `work/leftovers` (`25640bd`) — both fixes broken again

Gates reproduce: 1772 passed / 0 failed. Worktree clean before and after.

### #172 — RT5-6 is the consequential one
`lspci | grep -i nvidia` matches the **vendor string**, not the device class.
Probed against the real server: an NVIDIA HD-Audio function with *no* display
controller, and an **nForce SMBus/Ethernet chipset**, each give
`gpu_available=True` and "NVIDIA hardware detected". Downstream,
`setup_gpu_enabled_venv2` gates on `gpu_available` **alone** — never
`cuda_available` — and installs `torch --index-url .../cu118`. So an
NVIDIA-vendor *audio* chip is promised as a compute GPU and pulls a CUDA build.

The contract adopted last round ("each method is trusted only for what it
observes") is right; it simply was not applied to *what `lspci` actually
matched*.

- **RT5-5**: mutant M8 survives — `inconclusive = smi_unreadable`, dropping the
  `and not gpu_available` this very commit added. Nothing asserts `inconclusive`
  when smi is unreadable *and* a fallback answered, so the mutant reports
  `gpu_available=True` and `inconclusive=True` at once, invisibly.
- **RT5-7**: plain `ls` fixed the decoration bug but not the count — an `ls`
  wrapper forcing `-C` reports **4 real GPUs as 2**. `ls -1` closes it.
- Clean: `gpu_count=None` has exactly one production reader
  (`gpu_detection_summary`), which branches on `None` first; no arithmetic or
  comparison on it anywhere. Methods 2-4 have no third re-set path.

### #169 — the YAML guard is defeated five ways, all with 8/8 passing
- **RT5-1 (worst)**: `if: github.event_name == 'push'` on `status-check`'s only
  **step**. The job runs, every step skips, the job concludes **success**, and
  the required context reports **pass on every PR without checking anything**.
  The tests inspect `job.if` and `job.needs`, never `steps`.
- **RT5-2**: `on.pull_request.types: [labeled]` — stops firing on
  opened/synchronize, so the context is never reported and the PR sticks on
  "Expected — Waiting", which is exactly the #169 failure. The guard checks
  `paths`/`paths-ignore`/`branches`, not `types`.
- **RT5-3**: `_publisher()` returns the *first* sorted match, so an
  `aaa_decoy.yml` with a compliant job of the same name passes 8/8 while the
  real gate is silenced.
- **RT5-4 / G5**: `continue-on-error: true`, and a matrix on the publisher —
  both uncaught.

**This is the #123 lint story again**, and it gets the same answer: close what
is closeable, then state the residual blind spots in an *executable* form that
fails if one ever becomes detectable — the pattern this repo already uses for
the silent-failure families. A YAML guard cannot decide "will GitHub actually
report this context"; only a real docs-only PR can. #169 must not be closed on
a green test suite.

Fix round dispatched. #168's two remaining sites dispatched to `work/fixes`.

## #123 fix round three: `fcd922a` on `work/silent-failures`

1869 passed / 17 skipped / 0 failed (baseline 1850, +19 tests); black 26.3.1,
flake8, mypy clean. All eleven of the previous round's mutants now die,
including the three that pin F1's lock **independently** — `configure`'s lock,
the `target = _config` binding, and `load_config`'s lock, which previously had
no test at all despite its docstring calling it load-bearing.

**S1 armed at all five sites.** The reviewer's own attack was reproduced first
(all five reports deleted → 1850 passed, byte-identical), then the new tests in
that same mutant tree give 8 failures across all five. Each site pins the
**level** (`levelno == WARNING`), not just the text, so a demotion to `debug`
fails too. The triggers are real: a `DependencyAnalyzer` subclass that genuinely
exhausts the stack, a real `exec`'d function with no source, real `int`
subclasses, a real unparseable YAML file.

**S2 made mechanical.** The family letter now lives on each entry
(`BLIND_SPOTS: name -> (family, source)`), and each family's stated count is
compared against a count of entries carrying that letter. The two defeats now
fail: family A six→seven gives `assert 7 == 6`, and the *fabricated* "Nine
spellings" in family E gives `assert 9 == 1`.

**M25 deleted rather than tested** — correct: re-adding dead code cannot fail a
test, and the caller's `continue` was proven to be the load-bearing guard
(removing *that* fails 2 tests). The project's rule is use it or delete it.

**Refiling**: the exception-accessor entry moved H → new family K (a
name-matching false negative, not dead code). H 8→7, root causes ten→eleven,
total still 25.

## The stray `clustrix.yml` is a fossil, not live pollution — my finding corrected

I reported a test writing `clustrix.yml` into a checkout root. **The suite does
not do this.** A per-test teardown detector across all 1867 items found no
writer, and nothing in the tree — including `real_world` and `integration`,
grepped statically — writes such a path. `_resolve_config_path`, whose docstring
names the repo root as where bare filenames *used* to land, has been an ancestor
since `fade843` (2026-08-18).

So the file in `clustrix-fixes` was written by an **agent's manual probe**, not
by the suite. The quarantine was harmless and no code defect exists. The
`.gitignore` observation still stands on its own: because `clustrix.yml` is
ignored, anything that does land in a checkout root is invisible to
`git status` — worth knowing, but not evidence of a bug.

## Version strings: consistent

All four required strings (`pyproject.toml`, `setup.py`,
`clustrix/__init__.py`, `docs/source/conf.py`) read **0.2.0** on all seven
branches. Checked with a parser rather than a shell one-liner, after a
`bad substitution` produced silently empty fields for two of them — an empty
field would have read as agreement.

## CHANGELOG draft ready — and three merge actions fall out of it

Draft: `<scratchpad>/changelog-draft-159-campaign-a1.md`. 92 entries plus a
"Corrections to existing entries" section of 8. Written in the file's own voice
and scanned clean by the repo's secret scanner. Branch tips it read are
recorded at its head; later commits need folding in before it is applied.

| Section | Entries |
|-|-|
| Fixed — security: credential (#167 + gate) | 14 |
| Fixed — security: other (#111, host keys, #154) | 13 |
| Fixed — correctness: #152 `cores` | 5 |
| Fixed — correctness: #123 | 9 |
| Fixed — correctness: #172 | 4 |
| Fixed — correctness: other (#158 etc.) | 4 |
| Fixed — the widget (#165) | 5 |
| Fixed — the test suite could not be trusted (incl. #169) | 11 |
| Added / Changed / Removed / Known limitations | 19 |

### Merge action 1 — CHANGELOG will conflict
`work/credential-gate` already carries a partial CHANGELOG section (blob
`22860cc`). The draft is written to **supersede** it. Resolve that conflict by
taking the draft, not by merging both.

### Merge action 2 — the two #167 branches number things differently
`work/fixes` numbers its commits **"Round 11..16"** while
`work/credential-gate` numbers **"route 3/5/6/7/9/13"**, and both use "route N"
in prose with different schemes. They also fix #167 by **different strategies**:
`work/fixes` does per-route fixes plus write-path provenance;
`work/credential-gate` does one choke point plus read-path derivation. After the
merge these must read as one coherent story, so a pass over the merged
comments and docs is required — this is documentation reconciliation, not code.

### Merge action 3 — nothing is written for #168 or #171
`git log --all --grep` finds no commit mentioning either, which is correct:
both were dispatched after the draft was made. #168's two remaining sites are
in flight on `work/fixes`; #171 is still queued. Fold both in before applying.

### Already correct in the draft, do not re-litigate
- #164, #169 and #172's `lspci` hole are labelled **fixed-but-unproven** or
  **open**, each with the reason it cannot be closed locally.
- The "#165 Apply is a no-op" report is explicitly **not** written up: it is
  true on `work/fixes` alone and resolved by the merge.

## Route 13 fully closed at seven sites (`19a5026` on `work/credential-gate`)

2045 passed / 0 failed; black 26.3.1, flake8, mypy clean. **`sphinx -W` exit 0
verified by me** on this branch, closing the agent's stated gap (d) — sphinx is
not in `rt4venv`; it lives in `/private/tmp/clustrix-docs-venv`. The worktree
was left clean.

Two mechanisms worth remembering:

- **`IdentitiesOnly=yes` alone is insufficient.** `ssh -G` shows the default
  identity files survive it. The fix needed `IdentityFile=<the key being
  deployed>` and `IdentityAgent=none` as well.
- **`ssh-copy-id` pins identities only in its *filter* step.** The invocation
  that actually logs in and appends to `authorized_keys` runs plain `ssh`. So
  reading the first invocation and concluding it was safe would have been wrong.

Measured, not argued: with a repo-named host under the **default `reject`**
policy, `known_hosts` went 0 → 882 bytes. `add_host_key` is now conditional on
`auto_add` and stays exported for deliberate use. Its #123-shape swallow was
fixed too — a failed scan and an empty scan are now distinguishable.

The AST rule was extended to `subprocess` invocations of ssh/ssh-copy-id/scp/
sftp, anchored on `subprocess.*` so a list like `["ssh","huggingface"]` is not a
false positive, with a self-test.

**A test was rewritten, not relaxed**, and said so:
`test_deploy_public_key_ssh_copy_id_success` asserted the *defective* argv.

### Stated honestly rather than left silent — and one is a real remaining hole

- **`ssh_host_key_policy` is an ordinary declared field**, so an untrusted
  `./clustrix.yml` can set `auto_add` itself. Same shape as route 10. Recorded
  in `test_host_key_policy.py`. **This is a genuine open hole**, not a caveat —
  the next red-team is asked to establish how far it actually gets an attacker.
- The wire proof measures only the **ssh-agent half**: OpenSSH resolves
  `~/.ssh/id_rsa` from the passwd database rather than `$HOME`, so no test can
  redirect the default-identity half. The `ssh -v` trace does show the real
  defaults being attempted.
- The AST rule cannot see command lists built across functions, `shell=True`,
  or `ssh-keyscan` — all three in its docstring.

## Merge target re-verified at current tips

base `0cbce38` + silent-failures `fcd922a` + widget-apply `feb1fd9` + named-env
`f2a152d` = `376701f`: **2175 passed, 17 skipped, 0 failed**, black/flake8/mypy
clean. Same three conflicts, same resolutions, executed twice independently
with identical results — the recorded plan is proven rather than predicted.

## #168 fully closed (`4e76040` on `work/fixes`) — and it creates a merge that WILL FAIL

2002 passed / 0 failed (baseline 1985, +17); black 26.3.1, flake8, mypy clean;
`sphinx -W` clean.

**Site 1** — `notebook_magic_config.load_config_from_file`. Contract: the one
`clustrix/config.py` already draws, no third policy invented. A file the caller
**named** (the default) raises the real error — `FileNotFoundError`,
`PermissionError`, `yaml.YAMLError`, `JSONDecodeError` — exactly as
`load_config` does. A file the widget **discovered** by globbing
(`discovered=True`, now passed by the widget's scan) stays non-fatal but logs
the absolute path and the reason at WARNING. Parsing split into
`_read_config_document` so both share one reader.

**Site 3** — `utils.deserialize_function`. The fallback fires routinely, so the
success path stays silent; a double failure raises `RuntimeError` naming both
loaders and both reasons, `from cloudpickle_error`, with dill's exception
surviving as `__context__` so all three tracebacks print.

**One detail worth carrying forward:** dill and cloudpickle emit *identical*
text for synthesizable bad payloads, so the both-reasons test counts
occurrences rather than trusting distinct strings — "otherwise it would have
passed against the defect". That is the arming discipline working as intended.

### ⚠️ MERGE ACTION — this merge fails unless handled

`TRACKED_DEFECTS` does **not** exist on `work/fixes`; it lives in
`tests/unit/test_no_silent_swallows.py` on `work/silent-failures`, and its
entry `("notebook_magic_config.py", "load_config_from_file")` records exactly
the defect that `4e76040` has now fixed.

`test_the_allowlists_have_no_stale_entries` fails when a dict names a site that
no longer exists. **So when `work/fixes` meets `work/silent-failures`, that
entry must be deleted in the merge commit**, and the swallow audit re-counted.
Neither branch's suite can see this — the same class of cross-branch
interaction that produced the `named-env` assertion failure earlier, and the
second instance of it in this campaign.

**#168 is now fully fixed** (site 2 on `work/silent-failures`, sites 1 and 3
here) and can be closed with evidence after the merge — which also means it
must be removed from `TRACKED_DEFECTS` rather than left pointing at a closed
issue.

### The predicted merge failure is VERIFIED, not assumed

Both halves checked directly:

1. The merged tree `376701f` (base + silent-failures + widget-apply +
   named-env) carries the entry at
   `tests/unit/test_no_silent_swallows.py:1239`:
   `("notebook_magic_config.py", "load_config_from_file")`.
2. `test_the_allowlists_have_no_stale_entries` (`:1766`) computes
   `(set(JUSTIFIED_SWALLOWS) | set(TRACKED_DEFECTS)) - live` and asserts it is
   empty, where `live` is the set of sites the lint currently detects.
3. On `work/fixes` at `4e76040` that handler **no longer discards** — it logs
   the absolute path and the reason at `WARNING` ("This is not the same as the
   file holding no configurations") and only for the `discovered=True` branch;
   the named branch returns `_read_config_document(...)` directly and raises.

So the lint will not report it, `live` will not contain the key,
`TRACKED_DEFECTS - live` will be non-empty, and the test fails.

**Fix at merge time:** delete that one entry from `TRACKED_DEFECTS` in the
merge commit, and re-count the swallow audit. Nothing else is required — this
is a bookkeeping consequence of the fix, not a defect in either branch.

## #172's defect was real-world harm, now fixed (`da5bed8`); #169 hardened (`de9e742`)

1791 passed / 0 failed; black 26.3.1, flake8, mypy clean.

**RT5-6 was not theoretical.** Verified with a real `lspci` on the real SSH
server and a real `pip` on the host PATH recording its own invocation: an
NVIDIA HD-Audio function *alone*, and an nForce chipset alongside ASPEED
graphics, each reported `gpu_available=True` **and actually ran**
`pip install torch … --index-url …/cu118`.

**Two changes, and the second is the important one:**

1. `lspci` is now matched on PCI **device class**, not the vendor string:
   `lspci -nn | grep -Ei '\[03[0-9a-f]{2}\]:.*\[10de:'`. Base class 03 covers
   `0300` VGA and `0302` 3D, which is how A100/H100 enumerate; vendor id
   `10de` matches even when `pci.ids` is too old to name the card.
2. The CUDA install now gates on a **new** `nvidia_driver_present`, set only by
   `nvidia-smi` and `/proc/driver/nvidia` — the two methods that observe the
   **driver** rather than the bus. A card on the bus may have no driver, have
   nouveau bound, be too old for cu118, or be passed through to a guest. On
   lspci-only evidence clustrix builds the standard VENV2 and says so. The
   duplicate copy of that condition in `enhanced_setup_two_venv_environment`
   was deleted — one definition, not two.

`gpu_count` stays `None` on lspci evidence (SR-IOV/vGPU functions, MIG).
`ls -1` closed the column-wrapping miscount: a wrapper forcing `-C` read 4 GPUs
as 1 before, 4 after. RED against pristine `25640bd`: 8 failed / 16 passed.

**#169**: all five bypasses reproduced at 8/8 green, all five now die. Checks
run against **every** publisher and cover steps, `continue-on-error` at both
levels, `matrix`, `uses:` jobs, `branches-ignore` and bare `pull_request:`.
12 bypasses pinned across 21 tests, with an executable `KNOWN_BLIND_SPOTS`
following the `test_credential_file_permissions.py` precedent.

**Residual, named rather than hidden**: a publisher step that is `exit 0`; one
appending `|| true`; an aggregator omitting a job from `needs`; a `runs-on`
label nobody provides; a third-party action of unknown behaviour. Two more have
no document to plant and live in the docstring — repository state (Actions or
the workflow disabled, a fork awaiting "Approve and run") and the required
contexts drifting from `REQUIRED_CONTEXTS`. **Only a real docs-only PR settles
any of it**, so #169 still must not be closed on a green suite.

# MERGE RUNBOOK (executable; supersedes every earlier merge note)

Every step below was rehearsed in an isolated clone and produced the stated
result twice. Branch tips move — re-check them before starting.

## 0. Preconditions
- All agents finished; every worktree `git status --short` empty.
- Interpreter: `/private/tmp/rt4venv/bin/python` (3.11.16). Sphinx:
  `/private/tmp/clustrix-docs-venv/bin/python -m sphinx`. **Never** the system
  `python3` (3.9.13, below the project floor, has the deps, runs a subset).
- Rehearse in `<scratchpad>/mergesim` (a clone), never in a real worktree.

## 1. Merge order — measured, not guessed
```
base work/priorities-and-docs
  <- work/silent-failures
  <- work/widget-apply
  <- work/named-env
  <- work/leftovers
  <- (work/fixes merged INTO work/credential-gate first, then that)
```
**Do NOT rebase the gate onto fixes.** It branched at `7f82333`, fixes has
moved since, and the rebase dies on the gate's first commit (`1bf4654`),
replaying 14 commits through the same conflict. Merging fixes → gate costs one
reconciliation (6 files, 13 regions) instead.

## 2. Conflicts and their decided resolutions

| Step | File | Resolution |
|-|-|-|
| + silent-failures | `tests/unit/test_known_hosts_atomicity.py` | take **ours** (base); drop the orphaned `import time` if it appears |
| + widget-apply | `clustrix/config.py` | one `typing` line — widget-apply's is a strict superset, take **theirs** |
| + named-env | `clustrix/config.py` | **semantic merge, NOT take-one-side** — keep HEAD's structure and graft named-env's `conda_env_name` validation in beside the `cluster_type` one at 8-space indent, inside `with _DEFAULT_CONFIG_LOCK:`, before `target = _config`. A careless "keep ours" **silently drops `conda_env_name` validation**, moving the refusal to submission time with the job directory already created and the pickle uploaded. |
| + named-env | `tests/unit/test_known_hosts_atomicity.py` | take **theirs**; the tree must end with exactly ONE helper (`_wait_until_the_writer_has_written`) |
| fixes → gate | 6 files, 13 regions | `auth_methods.py`, `config.py`, `notebook_magic_widget.py`, `profile_manager.py`, `test_auth_fallbacks.py`, `test_a_cloned_repository_cannot_take_your_password.py` |
| + gate | `CHANGELOG.md` | take the **draft**, not a merge of both — the gate carries a partial section (blob `22860cc`) the draft supersedes |

## 3. Merge-time actions that are NOT conflicts — the merge FAILS without them

1. **Delete the stale allowlist entry.** `TRACKED_DEFECTS` in
   `tests/unit/test_no_silent_swallows.py` contains
   `("notebook_magic_config.py", "load_config_from_file")`. `work/fixes`
   `4e76040` fixed that site, so `test_the_allowlists_have_no_stale_entries`
   fails. Delete the entry in the merge commit and re-count the audit.
   **Verified by inspection, not assumed.**
2. **Reconcile the #167 narrative.** `work/fixes` numbers commits
   "Round 11..16"; `work/credential-gate` numbers "route 3/5/6/7/9/13", and
   both use "route N" in prose with different schemes. They also fix #167 by
   different strategies (per-route + write-path provenance vs one choke point +
   read-path derivation). Both are sound; the merged comments and docs must
   read as one story.
3. **Check `_config_under_test` vs `split_config_kwargs`.** The gate built the
   former because the latter does not exist on its branch; after widget-apply
   merges, both are present. Collapse them if they overlap.
4. **Fold #168, #171, #172, #169 into the CHANGELOG draft** — all landed after
   it was written.

## 4. Gates, in order, all from scratch on the merged tree
```
pytest tests/ -m "not real_world" --ignore=tests/real_world --ignore=tests/integration
black --check clustrix/ tests/     # MUST be 26.3.1; PATH black is 25.11.0 and disagrees
flake8 clustrix/ tests/
mypy clustrix/
cd docs && sphinx -W -b html source build/html     # note: build/, not _build/
python scripts/check_docs_markup.py                # expects docs/build/html
PYTHONPATH=<tree> python scripts/check_docs_examples.py   # refuses to run if an
                                                          # editable install shadows the checkout
pytest tests/unit/test_check_for_secrets.py
pre-commit run --all-files
```
Then the **history** secret scan — the working-tree scanner uses `git ls-files`
and excludes `.git`, so it cannot see a credential committed and later removed.
Drive its own `TOKEN_PATTERNS`/`PEM_BODY_LINE` over `git log -p master..HEAD`.

## 5. Then
Re-run `docs/source/notebooks/local_parallel_comparison.ipynb` at the merged
tip; push; open the PR; **open a docs-only PR to settle #169**.

## 6. Closing set
Close with evidence: #152 #153 #157 #158 #164 #165 #166 #167 #168 #171 #172,
then roll up #159.
**Leave open:** #111, #117, #122, #151, **#169** (unprovable locally),
**#170** (a design decision, not a defect).

## #171 closed (`3bfa452` on `work/fixes`)

2007 passed / 0 failed (baseline 2002, +5); black 26.3.1, flake8, mypy clean;
sphinx clean.

**Policy: refuse.** Decided, not defaulted. `_on_config_name_change` is a
`Text` observer firing on the **keystream**, which eliminates the other two
options the issue offered: a modal has nowhere to appear and would arrive once
per character, and auto-suffixing would silently name a configuration something
the user never typed — the same "accepted the instruction, did something else,
reported success" shape as the overwrite it replaces. It reports through
`status_output`, the channel every other handler in this widget uses, naming
both profiles.

**A test was asserting the destruction.**
`test_renaming_onto_a_name_that_came_off_a_disk_does_not_inherit_it` asserted
the rename *went through*, and its own docstring recorded that as "left alone
here" for #171 — the bug was encoded in the suite. Rewritten to assert the same
security property on the refused path, with the rewrite stated in both the
docstring and the commit message. That is the correct handling: say so
explicitly and rewrite deliberately, never quietly relax.

Tests assert on **contents, not counts** — against pristine `4e76040`,
`configs["SSH Remote Server"]` came back as `cluster_type: huggingface` and
"HuggingFace Jobs" was gone from the dropdown. All four mutants die, including
M4 (collision checked against `DEFAULT_CONFIGS` instead of `self.configs`),
which kills all six.

Provenance on the refused path: nothing moves — `config_source_map`,
`config_source_host_map` and `config_file_map` all stay keyed as they were,
asserted in both directions.

### Residual: a CHANGELOG "Known limitation", NOT a new issue

Because the refusal deliberately does not reset the name box, it can show a
name the profile does not hold until the user types on or selects elsewhere.
That is a documented trade-off in the handler's docstring — resetting the field
would fight the keystream — and there is no data loss. Filing an issue for a
deliberate, documented trade-off would work against ending with a clean issue
list. **Add it to the CHANGELOG's Known limitations section instead.**

## The gate does NOT hold: a full compromise chain, proven on the wire (`19a5026`)

Baseline confirmed 2045 passed / 2089 collected. This is the most serious
finding since route 13 itself, and it is a *chain*, not four separate bugs.

**F1 — an eighth discovery path: OpenSSH reads `~/.ssh/config`.**
`deploy_public_key`'s `ssh-copy-id` passes no `-F`, and OpenSSH resolves its own
home directory from the **passwd database**, so redirecting `HOME` does not
move it. An `IdentityFile` supplied by that config is loaded as **"explicit"**,
which means the `IdentitiesOnly=yes` added last round does not filter it.
Measured with otherwise identical flags: `-F /dev/null` → `rc=255, auths=[]`;
add a `Host * / IdentityFile` stanza → `rc=0, [('victim','publickey')]`.

**F2 — `ssh_host_key_policy` weaponised. This is the compromise.**
A `./clustrix.yml` naming **only** a host plus `ssh_host_key_policy: auto_add`,
carrying **no credential at all**: the gate refuses, and `deploy_public_key`
still returns True with the server logging two `('victim','publickey')`
authentications — F1 supplies the identity, F2 removes the host-key barrier.
**And it persists**: process 1 writes 8 entries into the global `known_hosts`;
process 2 — fresh, no attacker file, default `reject` — finds the host already
trusted for all three algorithms.

This is exactly the question I asked the reviewer to settle rather than leave
as a caveat, and the answer is that the previously-recorded
"`ssh_host_key_policy` is an ordinary declared field" note was understating a
live compromise.

**F3 — a planted leaker survived the full suite.** A module doing
`os.environ.get("SSH_PASSWORD")` → `paramiko.connect(hostname=…)` with no gate
call: 2045 passed. `_is_environ_lookup` exempts **literal** keys, and
`SECRET_SURFACES` only checks that declared surfaces still exist — it never
finds new ones.

**F4 — `hf_image` chooses the container that receives the token.** An ordinary
field, so an untrusted yml picks the image that gets `CLUSTRIX_HF_TOKEN` as a
job secret; its `hf_hub_download` lives inside a *string* of generated remote
code, so rule 7 cannot see it.

**P5-P8 — the admitted AST blind spots are exploitable.** `rsync`, a command
built into a variable, `shell=True`, and a list built across functions each
**survived and wire-authenticated**. An admitted limitation that is
demonstrably exploitable is a defect, not a caveat.

**Confirmed sound:** the default-identity half is correct — `-o IdentityFile`
*replaces* the five passwd-DB defaults, measured rather than assumed; the HF
token and endpoint are pinned in `staging`, `hf_jobs` and `cli_credentials`; no
git/curl/wget paths; the flat package means `glob("*.py")` has no subdirectory
gap; and paramiko never reads `ssh_config`.

Fix round dispatched, with the instruction that F1 must not be fixed by always
passing `-F /dev/null` — a user's ssh_config legitimately carries `ProxyJump`,
`Port`, `User` and `HostName`, and discarding it would break real deployments.

## Audit of the F2 class: which declared fields make a security decision

F2 (`ssh_host_key_policy`) and F4 (`hf_image`) share a shape — *a
security-relevant setting is an ordinary declared field, so an untrusted
configuration sets it as easily as any other*. Rather than wait for a third, I
audited all **64** `ClusterConfig` fields. 25 are security-relevant by name.
Results:

**No escalation via field-mixing. `_load_default_config` does not merge.** The
candidate loop has exactly one `break` (AST-verified): the first existing
candidate wins **outright**, and config-dir candidates are ordered before
working-directory ones. So an untrusted `./clustrix.yml` cannot override
`pre_execution_commands`, `module_loads`, `venv_post_install_commands` or
`environment_variables` while a *trusted* file supplies the host. One file wins
entirely — which is why **F2 works only because the attacker's file supplies
both the host and the policy**. That bounds the class rather than widening it.

Note the documented sharp edge in that function: `~/.clustrix/clustrix.yml` is
**not** a candidate (only `config.yml` is), so a user file with that name loses
to `./clustrix.yml`.

**Local-effect fields are better defended than expected:**
- `python_executable` is remote-only and passes through
  `validate_shell_fragment`; nothing runs it locally via `subprocess`.
- `local_cache_dir`'s deletion path (`_discard_local_cache`) removes only
  `<local_cache_dir>/data-packages/<package_id>` — keyed by an id nothing else
  uses — and explicitly declines when the resolved cache equals the caller's
  own `local_root`. Its docstring records the real past bug that motivated the
  guard: `materialize(dest="~/myproject")` followed by `delete()` removed the
  project.
- `local_work_dir` only redirects local filesystem *reads*
  (`filesystem.py:275`).

**So the exposures are the two already found**, not a family of them.

### A merge detail this turned up
`_load_default_config`'s candidate loop on the **gate** branch still contains
`except Exception: continue` — the silent-swallow shape #123 exists to remove.
`work/silent-failures` removes it. **At the `config.py` reconciliation, take
#123's error handling and the gate's provenance**; do not carry the gate's
`except Exception: continue` forward.

## #123 round four: `d1db83c` — 1890 passed / 0 failed

**B1 — position no longer exempts a count.** Every count sentence anywhere in
the file must now lie inside a family span, checked with the same span function
the per-family test uses; combined with "exactly one per family" the arithmetic
is total. Both of the reviewer's bypasses now fail (`line 1290` above family A,
`line 1137` in the header narrative), and two legitimate occurrences in the
module's own prose were **reworded rather than exempted** — the right direction.

**The author caught a hole in their own first draft**, which is the discipline
this campaign has been trying to instil: blanking `#` and `\n` character by
character left the `:` of `#:`, so the check saw only 8 of the 12 counts —
*precisely the four wrapped ones it exists for*. Fixed by blanking `#:` as a
unit; the `#:`-wrapped bypass above family A now fails too. The symmetric hole
(a family stating no count) was verified rather than assumed.

**B2 — detected, not merely recorded.** The package does none of the four
(grep empty), so this was a blind spot rather than a live defect — but it is
now caught anyway: hook assignment matched by attribute name alone, so
`import sys as s; s.excepthook = …` is seen, plus `logging.disable` /
`warnings.simplefilter` / `filterwarnings` through aliases and from-imports.
A live `sys.excepthook = lambda *a: None` planted in `clustrix/config.py` makes
the package scan fail. Re-enabling spellings (`disable(NOTSET)`,
`simplefilter("error")`, `sys.__excepthook__`) are exempt and tested.

**B3 — the flaky test was deleted, with the measurement recorded.** Against a
tree with `load_config`'s lock removed, the race test passed **3 of 8** runs
while the scheduled test failed **8 of 8**. It asserted nothing the scheduled
test does not, and the scheduled one also pins the mechanism. Rationale lives
in the survivor's docstring.

**Final arithmetic**: A6 B1 C1 D1 E1 F1 G1 H7 I1 J4 K1 **L3 = 28** entries,
**twelve** root causes, families A..L contiguous. Guards-lost numbers unchanged
at five — no guard was lost this round.

Family L is the global suppression the name-based check cannot spell:
`setattr(sys, "excepthook", …)`; `logging.getLogger().disabled = True` (the
name `disabled` cannot be added because `modern_notebook_widget.py` assigns
`button.disabled` six times); `warnings.filters.insert(…)`. The fourth
red-team is asked whether that last justification is sound or whether the check
could be qualified by receiver.

## DECISION: what `_load_default_config` does with a widget profile bundle

**The problem, verified pre-existing (not caused by the merge).** The widget's
Save writes `~/.clustrix/config.yml` as a *bundle* of named profiles.
`_load_default_config` expects a single flat configuration there. On
`work/fixes` the mismatch is swallowed (`except Exception: continue`); with
#123's stricter loading it raises `ConfigFileError`, so **pressing Save bricks
the next `import clustrix`**. Reproduced on the pre-merge tree `cae8d8a` and
absent on `origin/work/fixes`. Three route-12 tests fail in the merged tree for
this one reason.

The rehearsal was right not to guess. The three options were: (a) teach the
loader to recognise a bundle, (b) change what the widget writes, (c) accept the
raise.

**Decision: (a) — recognise the bundle shape, decline to adopt it, and say so.**

Reasoning:
- **(c) is worse than the defect it replaces.** Save-then-restart raising on
  `import clustrix` breaks the widget's own documented workflow. A crash on
  import is not an acceptable answer to a file the project itself wrote.
- **(b) breaks existing users.** The filename is user-visible and already on
  disk in people's `~/.clustrix`; changing it strands saved profiles.
- **(a) preserves today's *effective* behaviour** — the bundle is not adopted,
  exactly as the swallow left it — while removing the swallow, which is the
  whole point of #123. It reports instead of discarding.

Adopting one profile out of N automatically was considered and rejected: it
picks for the user among several equally-named candidates, which is the
"accepted the instruction, did something else" shape this campaign exists to
remove. Apply, not import, is how a profile is chosen.

So: detect the bundle shape deliberately, skip it, and emit a message naming
the file, saying it holds N named profiles, that clustrix does not adopt one
automatically, and how to load one. That is strictly better than the status
quo, does not change what is adopted, and is reversible.

**Where it lands:** `work/silent-failures` owns the strictness, so the fix
belongs there — queued behind round five. The merge patch is otherwise
complete.

## Merge rehearsal complete — patch captured

`<scratchpad>/mergesim-a56d54/step5-fixes-resolved.patch` (diff vs pre-merge
`cae8d8a`; `.format-patch` alongside). **2535 passed, 3 failed** — the three
being the bundle issue above, nothing else. black 26.3.1, flake8, mypy clean.

`TRACKED_DEFECTS`: the predicted failure was **reproduced first**, then the
entry deleted, leaving `TRACKED_DEFECTS: dict = {}`. My prediction confirmed
empirically, not just by inspection.

Notable resolutions: `configure()` keeps #123's lock and
`_ensure_default_config_loaded` plus #167's `DECLARED_FIELD_NAMES`, `_`-prefix
refusal and `cluster_host` normalise check; `_load_default_config` keeps #123's
`ConfigFileError` (**no** `except…continue`) with #167's
`config_built_from_file` and all three warnings. One unmarked hazard git
introduced silently: it auto-merged `set_config_source(_config, RUNTIME)` into
`split_config_kwargs` as **dead code after a `return`**; the rehearsal moved it
into `configure()`. That would not have been flagged as a conflict.
