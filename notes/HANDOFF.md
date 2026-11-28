# HANDOFF — read once, then delete `notes/`

**Written:** 2026-11-28 · **branch `dev`** (same commit as `ccr-d2f64557-m5wit9`) · tracking issue **#176**

This file hands the 0.2.0 work from a cloud session, which could not reach
the real clusters, to a session on the maintainer's laptop, which can.

**When you have picked this up, delete the whole `notes/` directory** — the
older files in it are dated history that git keeps — and in the same commit
remove what points at it: the `notes/` line in the root `AGENTS.md`
structure listing, `prune notes` in `MANIFEST.in`, and the mention in the
comment at the top of `pyproject.toml`. What is true about the code lives in
`CLAUDE.md`, the `AGENTS.md` files and `CHANGELOG.md`, not here.

## State of the branch

Last full runs, at `0ca233a` (only a script, a template and docs changed
since):

| Suite | Result |
|-|-|
| Unit — `pytest tests/ -m "not real_world" --ignore=tests/real_world` | 3300 passed, 29 skipped |
| Real sshd + SLURM on one machine — `pytest tests/real_world -m real_world` | 147 passed, 6 skipped |
| `scripts/verify_parallel_workloads.py local ssh slurm` (at `1105c3e`) | all equal — `docs/evidence/parallel-workloads.txt` |

Version strings already say `0.2.0`. `CHANGELOG.md` has one `0.2.0 —
unreleased` entry. No pull request is open; do not open one unless asked.

## The job for the laptop session: real hardware

Nothing in 0.2.0's new work has run on a real GPU, on the real SLURM
cluster, or on HuggingFace Jobs. `CLAUDE.md` says so in three places and
must keep saying so until it has.

The procedure is "On your own clusters" in `docs/REAL_WORLD_TESTING.md`.
In short:

```bash
pip install -e ".[dev]"
cp scripts/cluster_test.env.example ~/.clustrix-test.env   # fill in hosts, keys, partition, GPUs
set -a; . ~/.clustrix-test.env; set +a
python scripts/verify_cluster_usecases.py --check          # connects, submits nothing
python scripts/verify_cluster_usecases.py gpu-1
python scripts/verify_cluster_usecases.py slurm-1
python scripts/verify_cluster_usecases.py hf
CLUSTRIX_TEST_HF_GPU_FLAVOR=t4-small python scripts/verify_cluster_usecases.py hf-gpu
```

Things that will matter:

- **Python minor version** must match between the laptop and each cluster;
  `--check` prints what each cluster has. `CLUSTRIX_TEST_*_MODULES` can load
  one.
- **`gpu_computation` needs PyTorch with CUDA on the worker**: `torch`
  installed on the laptop (the environment is mirrored), or an existing
  cluster environment named in `CLUSTRIX_TEST_*_ENV`. Without it the case
  reports NOT ESTABLISHED, which is not a pass.
- **SLURM GPU cases** run only when `CLUSTRIX_TEST_SLURM_GPUS` is set.
- **HuggingFace**: the token tried from the cloud session was valid but was
  refused with `missing permissions: job.write` for both `jeremyrmanning`
  and `contextlab`. Nothing was submitted or billed. Use a token with Jobs
  write on the account to bill, and set `CLUSTRIX_HF_NAMESPACE` if that is
  the organisation. `hf-gpu` rents hardware and runs only when the flavor
  is named.
- The old variable names and `~/.clustrix-dev-credentials` still work.

When a target passes: save the output under `docs/evidence/` (read it first
— it prints hostnames and usernames, never secrets), then correct the
claims it settles: the GPU paragraph in `CLAUDE.md`, the GPU section of
`docs/source/parallel_loops.rst`, "Known limitations" and the
`verify_cluster_usecases` entry in `CHANGELOG.md`, and the "Not done" list
on #176. Change only what a run actually showed.

Also still unrun: the rewritten `real-world-tests` workflow on GitHub
(`workflow_dispatch`, job `local-cluster`).

## Decisions made in the last session, in case they surprise

- `cluster_type` defaults to `"local"`; a `cluster_host` without a
  `cluster_type` raises. Any personal script or `~/.clustrix/config.yml`
  that sets a host and no backend needs `cluster_type: slurm` (or `ssh`).
- Environment variables are two settings: `plain_environment_variables`
  (saved, shown) and `environment_variables` (never saved, masked).
- An input on the cluster at the same path but with different data stops
  the call; `auto_stage_on_mismatch="use_cluster"` runs on the cluster's
  copy with a warning. Above 256 MB the comparison is names, sizes and 64
  sampled 1 MB blocks.
- Ctrl-C or SIGTERM during an awaited call cancels its remote jobs.
- `clustrix.setup_environment` and `tests/integration/` are deleted; 95
  test files that could not run anywhere went with the latter.

## Known gaps, not hidden

- No end-to-end test where a large in-place input differs between laptop
  and cluster (the test hosts share a filesystem); the real clusters are
  the first place that can happen for real.
- `scripts/collect_execution_evidence.py` configures the same targets with
  its own copy of the logic that `verify_cluster_usecases.py` now has in
  `_configure_remote`. Fold it in or delete it.
- Not built: SLURM job arrays, bringing output files back, staging for
  `async_submit` and HuggingFace Jobs.
- Secret environment variables in the widget have no Forget button; a line
  is deleted to forget it.

## The local test cluster

`bash scripts/setup_local_slurm.sh` (Ubuntu 24.04, root) builds a real sshd
on `127.0.0.1:2222` and a one-node SLURM with two placeholder GPUs; the
real-world tests read `CLUSTRIX_TEST_SSH_HOST`, `_PORT`, `_USER`, `_KEY`.
It does not survive a reboot — rerun the script. Never clean it up
wholesale (`scancel -u clx`, `pkill -f`): cancel the job ids and stop the
pids you started.
