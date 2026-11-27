# tests/ — TEST SUITES

Three suites with different cost profiles, plus root-level modules from before the split.

## STRUCTURE

```
tests/
├── unit/            # fast, no credentials; what CI runs plus root modules
├── real_world/      # need real clusters/credentials (see real_world/AGENTS.md); test_local_sshd_slurm.py runs against a local sshd + single-node SLURM (scripts/setup_local_slurm.sh)
├── integration/     # provision BILLABLE AWS resources
├── comprehensive/   # edge cases, performance benchmarks, failure recovery (real, not mocked)
├── infrastructure/  # Docker-based local SSH/SLURM test servers
├── reference_workflows/  # end-to-end workflow snapshots
└── test_*.py        # root modules from before the split; some have _real twins
```

## CONVENTIONS

- **Mocking policy** — CLAUDE.md states it once and that statement governs. Condensed:
  1. Real first: a capability is not working until it has run against the real thing.
  2. A mock is a cost-control measure after a real call verified the contract, never a correctness argument.
  3. A mock may never be a fallback; if the real thing is unavailable the test fails.
  4. Production code must never know it is being tested.
  5. Never weaken a test to make it pass.
  Some modules still use `unittest.mock` (issue #117). Recount before quoting:
  `grep -lE "unittest\.mock|Mock\(|MagicMock\(|@patch" $(find tests -name "test_*.py") | wc -l`
- `pytest --strict-markers` — every marker must be registered in pyproject.toml (6 are). `visual`, `ssh_required`, `aws_required`, etc. are registered locally by `tests/real_world/conftest.py` via `addinivalue_line`, NOT in pyproject.
- `pyproject.toml [tool.pytest.ini_options]` is the ONLY pytest config — never add pytest.ini/tox.ini/setup.cfg (#130). Enforced by `tests/unit/test_pytest_config.py`.
- `tests/integration/conftest.py` refuses to run without `CLUSTRIX_ALLOW_BILLABLE=1` and auto-marks everything `expensive` (#109). `testpaths` must never name `tests/integration`.
- Coverage floor `fail_under = 66` (measured 68%); raise it only as coverage actually rises (#115).
- `tests/conftest.py` provides shared fixtures; `tests/ssh_server.py` is an in-process SSH server for tests that need a real protocol exchange.
- Root `test_x.py` + `test_x_real.py` pairs: the plain one is the older mocked version, the `_real` one its migration target. Prefer extending the `_real` module.

## ANTI-PATTERNS

- Never weaken or delete a failing test to go green — fix the code, or rewrite the assertion with a stated reason.
- Never add `tests/integration` to any default selection (CI, testpaths, pre-push).
- Never introduce a new marker without registering it (pyproject or the real_world conftest).
- Never commit credentials, hosts, or usernames — tests read them from env (`CLUSTRIX_TEST_*`, `CLUSTRIX_SLURM_PASSWORD`, `HF_TOKEN`).
