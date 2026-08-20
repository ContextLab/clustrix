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
