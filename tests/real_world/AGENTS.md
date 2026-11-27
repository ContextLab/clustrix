# tests/real_world/ — CREDENTIALED TESTING

Every test here talks to real infrastructure: SSH hosts, a SLURM cluster, or the HuggingFace API. Nothing is mocked; an unreachable target is a failure or an explicit skip, never a fabricated pass.

## STRUCTURE

```
real_world/
├── conftest.py            # auto-marks EVERY collected item real_world
├── credential_manager.py  # credential lookup from ~/.clustrix/.env and the environment
├── test_local_sshd_slurm.py   # @cluster against a real sshd + single-node SLURM
├── test_*_real.py         # same cluster: input staging, GPU requests, signals, job names, paths
└── test_filesystem_real.py    # the filesystem helpers on this machine; needs no cluster
```

## CONVENTIONS

- The cluster is named by `CLUSTRIX_TEST_SSH_HOST`, `CLUSTRIX_TEST_SSH_PORT`, `CLUSTRIX_TEST_SSH_USER`, `CLUSTRIX_TEST_SSH_KEY` (`scripts/setup_local_slurm.sh` creates one and prints them). Tests must read these — never hardcode a host, user, or secret — and skip with the variable names in the reason when they are unset.
- `tests/conftest.py` gives every test an empty `$HOME`, so no host is ever in `known_hosts`: a test that connects must configure `ssh_host_key_policy="auto_add"` for the named host. The tests that connected with the default policy could only fail and were removed.
- These tests are excluded from CI and from the default local run (`-m "not real_world" --ignore=tests/real_world`). They run via `python scripts/run_real_world_tests.py`, the pre-push hook when credentials exist, and the manual/weekly `real-world-tests.yml` workflow.
- A test that cannot reach its target must say so loudly (skip with reason or fail) — never return early with a pass.
- Submitted jobs must clean up after themselves; evidence-producing runs write under `docs/evidence/` only through `scripts/verify_cluster_usecases.py` / `collect_execution_evidence.py`, not from tests.

## ANTI-PATTERNS

- No `unittest.mock` here at all — this suite is the anti-mock boundary.
- No new marker without registering it in `pyproject.toml` (--strict-markers is on).
- Never relax a timeout to make a slow cluster pass; bump the specific test's budget with a comment instead.
