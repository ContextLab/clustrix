# tests/real_world/ — CREDENTIALED TESTING

Every test here talks to real infrastructure: SSH hosts, a SLURM cluster, or the HuggingFace API. Nothing is mocked; an unreachable target is a failure or an explicit skip, never a fabricated pass.

## STRUCTURE

```
real_world/
├── conftest.py            # auto-marks EVERY collected item real_world; registers
│                          # visual/ssh_required/aws_required/... markers locally
├── validation/            # config + credential validation against live targets
├── cluster_validation/    # 15 modules — per-backend job submission on real clusters
├── api_validation/        # external API contract checks (e.g. validate_slurm_job_submission)
└── test_*.py              # ssh, slurm, huggingface, gpu, config, executor, notebook magic
```

## CONVENTIONS

- Credentials come from the environment or `~/.clustrix-dev-credentials/`: `CLUSTRIX_TEST_SSH_HOST`, `CLUSTRIX_TEST_SSH_HOST_2`, `CLUSTRIX_TEST_SLURM_HOST`, `CLUSTRIX_TEST_USERNAME`, `CLUSTRIX_SLURM_PASSWORD`, `CLUSTRIX_GPU_PASSWORD`, `HF_TOKEN`. Tests must read these — never hardcode a host, user, or secret.
- These tests are excluded from CI and from the default local run (`-m "not real_world" --ignore=tests/real_world`). They run via `python scripts/run_real_world_tests.py`, the pre-push hook when credentials exist, and the manual/weekly `real-world-tests.yml` workflow.
- A test that cannot reach its target must say so loudly (skip with reason or fail) — never return early with a pass.
- Submitted jobs must clean up after themselves; evidence-producing runs write under `docs/evidence/` only through `scripts/verify_cluster_usecases.py` / `collect_execution_evidence.py`, not from tests.

## ANTI-PATTERNS

- No `unittest.mock` here at all — this suite is the anti-mock boundary.
- No new marker without an `addinivalue_line` in `conftest.py` (--strict-markers is on).
- Never relax a timeout to make a slow cluster pass; bump the specific test's budget with a comment instead.
