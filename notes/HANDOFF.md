# HANDOFF — start here

**Last updated:** 2026-08-17 · **`master` @ `760432c`** · Read this before anything else in `notes/`.

---

## 1. Where things stand

The package is mid-way through the production-readiness effort tracked by the
master plan **#108**. The audit behind it is in
`audit_and_master_plan_2026-08-17.md`.

Two things landed today:

| Issue | State | Result |
|-|-|-|
| **#109** billable tests cannot run by accident | closed earlier | guard + opt-in, now hardened further |
| **#130** `pytest.ini` was dead config shadowing `pyproject.toml` | **closed** | PR #134 → `760432c` |

Three issues were **filed** today and are open: **#133**, **#135**, and the
correction comments on **#110** and **#114**.

### What #130 actually changed

`pytest.ini` used `[tool:pytest]`, a header valid only in `setup.cfg`. pytest
selected the file anyway and stopped searching, so `pyproject.toml` was never
read either — two config files, **zero** effective configuration. `addopts`,
`testpaths`, `filterwarnings`, every marker and `--strict-markers` were all
declared and inert.

Now: `pyproject.toml` is the single source, `testpaths = ["tests"]`,
`--strict-markers` is live, 7 markers registered, `filterwarnings` restored.
`pytest.ini` is deleted and `tests/unit/test_pytest_config.py` fails if any
config file ever shadows `pyproject.toml` again.

---

## 2. Read these in this order

1. **This file.**
2. `session_130_pytest_config_execution.md` — what was done, what was measured,
   and a red-team section listing **two bugs that the fix itself introduced**.
   Its "Final verified state" block is the current truth.
3. `handoff_130_pytest_config.md` — **already executed, do not run again.** It
   carries a banner saying so. Kept for its analysis; two of its instructions
   were wrong and were reversed.
4. `audit_and_master_plan_2026-08-17.md` — the #108 background.

---

## 3. Environment — read before running anything

**The repo has no usable venv and `import clustrix` fails everywhere by
default** (`paramiko` missing). This is #110 and it is still open. Build a
throwaway one:

```bash
python3 -m venv /tmp/v && /tmp/v/bin/pip install -e ".[dev]"
/tmp/v/bin/pytest tests/unit/ -q          # expect: 79 passed
```

`[dev]` is now sufficient to collect the whole tree (`numpy`/`pandas` were
added). Add `pytest-xdist pytest-timeout` only if you need them.

Gotchas that will cost you time:

- **`scripts/pre_push_check.py` shells out to bare `pytest`/`mypy`/`flake8`
  from `PATH`.** Run it as
  `PATH="/tmp/v/bin:$PATH" /tmp/v/bin/python scripts/pre_push_check.py`.
  Using the venv interpreter alone fails with `ModuleNotFoundError: paramiko`.
- **That script cannot exit 0** — flake8 reports 91 findings on `master`. This
  is #133, not something you broke. CI runs the same flake8 command with
  `--exit-zero`, so it has never been green anywhere.
- **`tomllib` needs Python ≥3.11.** Default interpreter here is 3.9; use
  `/opt/homebrew/bin/python3.12`.
- **Pass `-o addopts=`** when comparing collection counts across changes.
- **`tests/real_world/conftest.py:223`** calls `is_dartmouth_network()` at
  collection time — live DNS plus a `ping`. If collection hangs off-network,
  that is why. Tracked as #117.
- **CI's `black` is pinned to 25.1.0.** Do not "upgrade" it (see #110).
- GitHub's API returned 503s repeatedly today; `gh pr edit` also fails with a
  Projects-classic GraphQL deprecation error. Use
  `gh api -X PATCH repos/ContextLab/clustrix/pulls/<n> -F body=@file.md`.

---

## 4. ⚠️ The one thing not to break

`tests/integration/` provisions **real, billable** AWS EKS/EC2 resources. Two
layers stop it:

1. `tests/conftest.py::pytest_configure` refuses any run whose targets resolve
   under `tests/integration`.
2. `tests/integration/conftest.py` sets `collect_ignore_glob` so pytest never
   imports anything there.

Opt in deliberately with `CLUSTRIX_ALLOW_BILLABLE=1`.

**The guard reads `config.args`, not `config.invocation_params.args`. Do not
"simplify" this.** `invocation_params.args` is only what the operator typed;
`testpaths`, `-o testpaths=` and `PYTEST_ADDOPTS` all feed `config.args`
without appearing in it. Narrowing the guard opened two working money-safety
bypasses during this PR, caught only by red-teaming.

Reading `config.args` is safe **only because `testpaths = ["tests"]`**. If you
ever put `tests/integration` back into `testpaths`, a bare `pytest` will be
refused and the whole suite becomes unrunnable. The two settings are coupled;
`tests/unit/test_billable_safety.py` (60 tests) enforces the coupling.

---

## 5. Suggested next steps, in priority order

1. **#121 — P0 security.** Unauthenticated pickle of remote results (RCE) plus
   disabled SSH host-key verification. The only P0 that is a live exploit
   rather than hygiene. Start here unless told otherwise.
2. **#114 — re-baseline the failures.** Now genuinely actionable for the first
   time: collection used to abort on a SyntaxError, so its "127 failures /
   8 collection errors" was measured while 6 modules could not be imported.
   Collection errors are now **0**. A partial run showed 13 failures in the
   first 59 tests; the real number is unknown. Re-derive, then triage.
3. **#135 + #133 together.** Both are "a gate that silently does nothing" —
   the same failure mode as #130. `fast_ci.yml` is invalid YAML and has never
   run a single job (on `master` too); `pre_push_check.py` can never pass.
   Sequence #135 against #120, since its integration job runs real `@cluster`
   execution and may fail legitimately.
4. **#110 — finish it.** Two of its acceptance criteria are met (f-string
   SyntaxError fixed; `numpy`/`pandas` declared). Still open: `pytest-xdist`
   placement, the `requires-python` floor, a documented bootstrap, and a CI job
   that installs only `[dev]` and asserts collection succeeds.

---

## 6. Working agreements established in these sessions

- **Do not hand PRs back for review.** The instruction on record is: *"this is
  too early for a serious review; you should review both PRs and merge or edit
  as you see fit. use subagents to red-team and do this carefully. then suggest
  next steps."* Red-team, fix, merge, then propose what is next.
- **Red-teaming is not optional and it works.** Four agents found two bugs the
  fix itself had introduced, including a money-safety regression. Budget for it.
- **Do not take subagent findings on trust.** Of four reports, one claim was
  wrong (it inspected the wrong venv) and one misattributed a cause. Verify
  before acting.
- **Fix what you encounter, but do not smuggle it into an unrelated PR.** The
  f-string SyntaxError and the `numpy`/`pandas` gap were fixed because they
  blocked the #130 gate. The dead `fast_ci.yml` workflow and the 91 flake8
  findings were filed instead, because repairing them has unpredictable blast
  radius.

---

## 7. Loose ends in the working tree

- `.omc/` — tooling state, untracked, ignore it.
- `coverage_detailed_report.txt` — stale, untracked, **nothing in the repo
  generates it**. Safe to delete; left in place rather than presume.
- `/tmp/v130` and `/tmp/fresh130` — throwaway venvs from today. `fresh130` is
  `[dev]`-only and useful for checking what a clean contributor install does.
  Both are disposable.
