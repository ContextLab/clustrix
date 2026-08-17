# Handoff: fix #130 — pytest.ini is dead config shadowing pyproject.toml

> ## ✅ EXECUTED AND MERGED — 2026-08-17. Do not run this plan again.
>
> Landed as PR #134, squashed to `760432c` on `master`. Issue #130 is closed.
> Outcome, corrections, and what this plan got wrong: `session_130_pytest_config_execution.md`.
>
> **This document is kept for its analysis, not as a to-do list.** Two of its
> instructions were wrong and were reversed during execution:
>
> 1. **Step 1's core recommendation is unsafe.** It says to switch the #109
>    guard to `config.invocation_params.args`. Doing so opens real
>    money-safety bypasses (`PYTEST_ADDOPTS=`, `-o testpaths=`), because what
>    the operator types is not what pytest collects. The guard reads
>    `config.args` on `master` today; that is safe only because Step 2's
>    `testpaths = ["tests"]` also landed. Do not "restore" Step 1 as written.
> 2. **Step 0's baseline was not clean.** It had 6 collection errors that had
>    to be fixed before Step 4's gate could mean anything.
>
> Its §8 "Things that will trip you up" is still accurate and still useful.

**Written:** 2026-08-17 · **For:** a fresh session picking this up cold
**Issue:** ContextLab/clustrix#130 · **Parent:** #108 · **Related:** #110, #113, #115, #117

Everything below was verified on `master` at `a9393b7`. Commands are copy-pasteable.
Where a fact is stated, the command that established it is given so you can re-verify
rather than trust this document.

---

## 1. What is wrong

`pytest.ini` line 1 is `[tool:pytest]`. That section header is valid **only in
`setup.cfg`**. In a file named `pytest.ini`, pytest requires `[pytest]`.

pytest still *selects* `pytest.ini` as its config file — and having selected one, it
**stops searching**. So `pyproject.toml`'s `[tool.pytest.ini_options]` is never read
either.

Net result: two config files, **zero** effective configuration.

```bash
head -1 pytest.ini                                    # -> [tool:pytest]
pytest tests/unit/ --co 2>&1 | grep configfile        # -> configfile: pytest.ini
pytest --markers | grep -c '^@pytest.mark.real_world' # -> 0   (nothing registered)
```

Strict-marker check (should error if strict mode were live, but does not):

```bash
printf 'import pytest\n@pytest.mark.bogus_xyz\ndef test_x(): assert True\n' > tests/unit/test_probe_tmp.py
pytest tests/unit/test_probe_tmp.py --co -q            # -> "1 test collected"
rm tests/unit/test_probe_tmp.py
```

### Inert today

| Setting | Declared in | Effect today |
|-|-|-|
| `addopts` | both | none |
| `testpaths` | both | none — bare `pytest` walks the whole repo |
| `markers` | both | **none registered** |
| `filterwarnings` | both | none |
| `--strict-markers` | pytest.ini `addopts` | inert |

---

## 2. Why it matters

1. **It invalidates a stated premise of #110.** #110 says `pip install -e ".[dev]"`
   yields an env where pytest cannot start because `addopts` requires `pytest-xdist`.
   That is true only where `pytest.ini` is *absent* — the closed epic branch deletes it.
   On `master`, `addopts` never applies at all, and `master`'s pyproject `addopts` is
   just `-v --tb=short` (no `-n 4`). Update #110 to say which config was live.
2. **It is why #109's billable-test gate could not use markers.** Applying
   `pytest.mark.expensive` today emits `PytestUnknownMarkWarning` and is not a usable
   selector. The gate uses `collect_ignore_glob` + a `pytest_configure` guard instead.
3. **Inert `--strict-markers` hides typos.** One is already in the tree:
   `tests/real_world/test_kubernetes_performance_benchmarks.py:1038` uses
   `@pytest.mark.cleanup`, which is declared in **neither** file and does nothing.

---

## 3. ⚠️ The landmine — read before changing anything

**Naively fixing this breaks `pytest` completely.** Verified:

```bash
mv pytest.ini /tmp/ && pytest --co -q ; mv /tmp/pytest.ini .
```
```
ERROR: Refusing to run 'tests/integration': tests/integration provisions real,
billable cloud resources (AWS EKS/EC2). Set CLUSTRIX_ALLOW_BILLABLE=1 ...
```

Why: `master`'s pyproject has

```toml
testpaths = ["tests/unit", "tests/integration"]
```

When no paths are given on the command line, pytest populates `config.args` **from
`testpaths`**. The #109 guard in `tests/conftest.py` inspects `config.args` and refuses
any run targeting `tests/integration`. So the moment pyproject becomes live, bare
`pytest` aborts.

### The clean fix

The guard's *intent* is "refuse when the user explicitly asks for these tests". That
means it should key off what the user typed, not off resolved testpaths.
`config.invocation_params.args` is exactly that. Verified:

| invocation | `config.args` | `config.invocation_params.args` |
|-|-|-|
| `pytest` (testpaths live) | `['tests/unit', 'tests/integration']` | *(no path entries)* |
| `pytest tests/unit/` | `['tests/unit/']` | `['tests/unit/', ...flags]` |

Note `invocation_params.args` includes flags, so skip entries beginning with `-`.

Do **both** of these:
- switch the guard to `config.invocation_params.args`, and
- set `testpaths = ["tests"]` (integration stays excluded by `collect_ignore_glob`).

Belt and braces: `collect_ignore_glob` in `tests/integration/conftest.py` continues to
handle directory traversal regardless.

---

## 4. Current state, measured

### Marker declarations disagree between the two files

| Source | Markers declared |
|-|-|
| `pytest.ini` (inert) | 14 |
| `pyproject.toml` (shadowed) | **4** — `real_world`, `slow`, `unit`, `integration` |

### Markers actually used in `tests/` (non-builtin)

```
real_world          224 uses
dartmouth_network    11 uses   4 files   <- NOT in pyproject
slow                  6 uses
expensive             5 uses   5 files   <- NOT in pyproject
performance           4 uses   4 files   <- NOT in pyproject
integration           2 uses
cleanup               1 use    1 file    <- NOT in either file; likely a typo
```

Regenerate with:
```bash
python3 - <<'PY'
import re, pathlib, tomllib
d = tomllib.load(open("pyproject.toml","rb"))
declared = {m.split(":")[0].strip() for m in d["tool"]["pytest"]["ini_options"]["markers"]}
used = set()
for p in pathlib.Path("tests").rglob("*.py"):
    used |= set(re.findall(r"@pytest\.mark\.([a-z_]+)", p.read_text(errors="replace")))
builtin = {"parametrize","skip","skipif","xfail","usefixtures","filterwarnings","timeout","asyncio"}
print("gap:", sorted(used - declared - builtin))
PY
```
(needs Python 3.11+ for `tomllib`; on this machine use `/opt/homebrew/bin/python3.12`)

### Dependency note

`pytest-xdist` is in the `[test]` extra, **not `[dev]`**. `master`'s pyproject `addopts`
does not use `-n`, so activating pyproject does **not** require xdist. Do not add `-n`
to `addopts` without also moving xdist into `[dev]` — that is the #110 trap.

---

## 5. Plan

Land as **one PR**, but in the commit order below, verifying after each step. Do not
combine steps: each one changes what the next one measures.

### Step 0 — baseline
```bash
git checkout master && git pull
pytest tests/ -m "not real_world" --co -q -o addopts= 2>&1 | tail -1   # record this number
pytest tests/unit/ -o addopts= -q 2>&1 | tail -1                        # expect: 70 passed
```
Record both. Every later step must not reduce them.

### Step 1 — defuse the landmine (must be first)
- In `tests/conftest.py`, change `pytest_configure` to iterate
  `config.invocation_params.args`, skipping entries starting with `-`.
- Keep the existing message and the `CLUSTRIX_ALLOW_BILLABLE` opt-in.
- Update the docstring: it currently says "args", which will no longer be accurate.

Verify — all of these must still hold:
```bash
pytest tests/integration/test_timeout_mechanism.py --co -q   # refused
pytest tests/integration/ --co -q                            # refused
pytest tests/ -m "not real_world" --co -q -o addopts= | grep -c '^tests/integration/'   # 0
pytest tests/unit/ -o addopts= -q | tail -1                  # still 70 passed
```

### Step 2 — pick one config source
- **Delete `pytest.ini`.** Keep `pyproject.toml` (modern convention, single source).
- Set `testpaths = ["tests"]`.
- Do **not** add `--strict-markers` yet.

Verify:
```bash
pytest --co 2>&1 | grep configfile     # -> pyproject.toml
pytest --co -q 2>&1 | tail -1          # bare pytest works, collects, does not abort
pytest tests/unit/ -o addopts= -q | tail -1
```

### Step 3 — reconcile the markers
Add to pyproject's `markers`: `expensive`, `dartmouth_network`, `performance`.
Decide on `cleanup` (`test_kubernetes_performance_benchmarks.py:1038`) — either declare
it or delete the marker. It has one use and no meaning today; deleting is likely right,
but check with the author first.

Verify:
```bash
pytest --markers | grep -cE '^@pytest.mark.(real_world|slow|unit|integration|expensive|dartmouth_network|performance):'   # -> 7
```

### Step 4 — enable `--strict-markers` LAST
Add it to pyproject `addopts`.

Verify:
```bash
pytest tests/ --co -q -o addopts="--strict-markers" 2>&1 | tail -3   # 0 errors
printf 'import pytest\n@pytest.mark.bogus_xyz\ndef test_x(): assert True\n' > tests/unit/test_probe_tmp.py
pytest tests/unit/test_probe_tmp.py --co -q                          # MUST now error
rm tests/unit/test_probe_tmp.py
```

### Step 5 — regression guard
Add a test asserting the active config is the expected file, so a stray config can never
silently shadow it again:

```python
def test_pytest_reads_the_intended_config(pytestconfig):
    assert pytestconfig.inipath is not None, "no config file loaded at all"
    assert pytestconfig.inipath.name == "pyproject.toml", (
        f"pytest loaded {pytestconfig.inipath}; a stray config file is shadowing "
        "pyproject.toml (see #130)"
    )


def test_project_markers_are_registered(pytestconfig):
    # getini("markers") also returns plugin-provided markers such as
    # "timeout(timeout, method=None, ...): ..." -- strip the argspec as well as
    # the description before comparing names.
    registered = {
        entry.split(":")[0].split("(")[0].strip()
        for entry in pytestconfig.getini("markers")
    }
    for required in (
        "real_world",
        "expensive",
        "integration",
        "dartmouth_network",
        "performance",
        "slow",
    ):
        assert required in registered, f"marker {required!r} is not registered"
```
Put it in `tests/unit/` — that is the only directory CI currently executes (#113).

**Both APIs were verified against pytest 8.4.2 on this repo**, so the snippet is known
to work rather than assumed:

| state | `pytestconfig.inipath` | project markers in `getini("markers")` |
|-|-|-|
| today (broken) | `/Users/jmanning/clustrix/pytest.ini` | absent — only plugin markers |
| after Step 2 | `/Users/jmanning/clustrix/pyproject.toml` | `real_world`, `slow`, `unit`, `integration` present |

So `test_pytest_reads_the_intended_config` fails today and passes after the fix — write
it first and watch it fail, per the project's TDD rule.

### Step 6 — full check + docs
```bash
python scripts/pre_push_check.py     # black, flake8, mypy, pytest — repeat until clean
```
Then update:
- **#110** — correct the `addopts`/xdist premise; state which config was live.
- **MIGRATION.md:80** — quotes a `[tool.pytest.ini_options]` block that no longer matches.
- **`.claude/commands/testing/prime.md:95`** — says `config_file: pytest.ini`.
- **#130** — close with before/after evidence.

---

## 6. Rollback

Each step is a separate commit, so `git revert` the offending one. Step 2 is the only
destructive step (deleting `pytest.ini`); its content is reproduced verbatim in the issue
body and recoverable via `git show master~N:pytest.ini`.

---

## 7. Definition of done

- [ ] `pytest --co 2>&1 | grep configfile` reports `pyproject.toml`
- [ ] Exactly one pytest config file exists in the repo
- [ ] Bare `pytest` runs without aborting
- [ ] `pytest tests/integration/<anything>` is still refused (the #109 guarantee holds)
- [ ] `pytest tests/ -m "not real_world"` collects no `tests/integration/` node IDs
- [ ] All project markers registered; `--strict-markers` active; suite collects with 0 marker errors
- [ ] `tests/unit/` still passes (≥70 tests)
- [ ] Regression test added that fails if a config file shadows pyproject again
- [ ] #110 corrected

---

## 8. Things that will trip you up

1. **`tomllib` needs Python ≥3.11.** The default interpreter here is 3.9. Use
   `/opt/homebrew/bin/python3.12` for the marker-gap script.
2. **The repo has no usable venv by default** — `import clustrix` fails everywhere
   because `paramiko` is missing (#110). Build a throwaway venv:
   `python3 -m venv /tmp/v && /tmp/v/bin/pip install -e ".[dev]" && /tmp/v/bin/pip install pytest-xdist pytest-timeout`
3. **Pass `-o addopts=` when measuring**, so you compare like with like across steps.
4. **CI's `black` is pinned to 25.1.0** (`pyproject.toml`). Do not "upgrade" it — an
   unbounded pin is what turned CI red before, and 26.5.1 reformats 19 unrelated files.
5. **Do not enable `--strict-markers` before Step 3.** There are 4 undeclared markers in
   use; strict mode turns each into a hard collection error immediately.
6. **`tests/real_world/conftest.py:223` calls `is_dartmouth_network()` at collection
   time** — live DNS plus a `ping` subprocess. If collection suddenly gets slow or hangs
   off-network, that is why. Tracked separately on #117; do not fix it here.
