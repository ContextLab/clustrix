# tests/ — TEST SUITES

Three suites with different cost profiles, plus root-level modules from before the split.

## STRUCTURE

```
tests/
├── unit/            # 48 modules — fast, no credentials; what CI runs plus root modules
├── real_world/      # 45+ modules — need real clusters/credentials (see real_world/AGENTS.md)
├── integration/     # 27 modules — provision BILLABLE AWS resources
├── comprehensive/   # edge cases, performance benchmarks, failure recovery (real, not mocked)
├── infrastructure/  # Docker-based local SSH/SLURM test servers
├── reference_workflows/  # end-to-end workflow snapshots
└── test_*.py        # ~41 root modules; many have _real twins (mocked vs real)
```

## CONVENTIONS

- **Mocking policy (issue #117)** — the five rules, condensed:
  1. No mock as a fallback for an unavailable real resource — fail instead.
  2. Mocks only for deterministic doubles (fake SSH server) or error-injection.
  3. Mock at system boundaries (subprocess, socket, HTTP), never at clustrix's own functions.
  4. Production code must never know it is being tested.
  5. New tests default to real; every new mock needs a written justification.
  Fresh count: 21 of 166 test modules still use `unittest.mock`. Recount before quoting:
  `grep -lE "unittest\.mock|Mock\(|MagicMock\(|@patch" $(find tests -name "test_*.py") | wc -l`
- `pytest --strict-markers` — every marker must be registered in pyproject.toml (6 are). `visual`, `ssh_required`, `aws_required`, etc. are registered locally by `tests/real_world/conftest.py` via `addinivalue_line`, NOT in pyproject.
- `pyproject.toml [tool.pytest.ini_options]` is the ONLY pytest config — never add pytest.ini/tox.ini/setup.cfg (#130). Enforced by `tests/unit/test_pytest_config.py`.
- `tests/integration/conftest.py` refuses to run without `CLUSTRIX_ALLOW_BILLABLE=1` and auto-marks everything `expensive` (#109). `testpaths` must never name `tests/integration`.
- Coverage floor `fail_under = 66` (measured 68%); raise it only as coverage actually rises (#115).
- `tests/conftest.py` provides shared fixtures; `tests/ssh_server.py` is the local fake SSH boundary.
- Root `test_x.py` + `test_x_real.py` pairs: the plain one is the older mocked version, the `_real` one its migration target. Prefer extending the `_real` module.

## ANTI-PATTERNS

- Never weaken or delete a failing test to go green — fix the code, or rewrite the assertion with a stated reason.
- Never add `tests/integration` to any default selection (CI, testpaths, pre-push).
- Never introduce a new marker without registering it (pyproject or the real_world conftest).
- Never commit credentials, hosts, or usernames — tests read them from env (`CLUSTRIX_TEST_*`, `CLUSTRIX_SLURM_PASSWORD`, `HF_TOKEN`).
