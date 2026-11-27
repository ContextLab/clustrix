# HANDOFF — start here

**Last updated:** 2026-11-27 · branch `ccr-d2f64557-m5wit9` · tracking issue **#176**

Everything else in `notes/` is dated history: useful for why a decision was
made, not for what is true now. What is true now is in `CLAUDE.md`, the
`AGENTS.md` files and `CHANGELOG.md` (one `0.2.0 — unreleased` entry; there is
no separate release-notes file).

## Where things stand

`0.2.0` is unreleased. Version strings already say `0.2.0` in
`pyproject.toml`, `clustrix/__init__.py` and `docs/source/conf.py`.

Built and exercised for real (local, a real `sshd`, a single-node SLURM):

- loop division — `clustrix/parallelize.py`, `clustrix/loop_workers.py`
- automatic input staging — `clustrix/auto_stage.py`, `clustrix/staging.py`
- `@cluster(gpus=N)` request path
- the notebook widget — `clustrix/modern_notebook_widget.py`

Not done, and documented as not done (see "Known limitations" in the
changelog): any computation on a real GPU, SLURM job arrays, bringing output
files back, staging for `async_submit` and HuggingFace Jobs.

## Before a release

1. Run both suites at one commit and commit the evidence:
   `scripts/verify_parallel_workloads.py local ssh slurm` writes
   `docs/evidence/parallel-workloads.txt`.
2. Run `@cluster(gpus=1)` on a machine with a GPU, over ssh and on a SLURM
   with real `gres`, then correct the GPU paragraphs in `CLAUDE.md` and
   `docs/source/parallel_loops.rst`.
3. Decide the default `cluster_type`: it is `"slurm"`, which runs locally
   when no host is set. `"local"` would say what happens.

## The test cluster

`scripts/setup_local_slurm.sh` builds it: `sshd` on `127.0.0.1:2222`, user
`clx`, one SLURM node with two placeholder `gpu` resources. The real-world
tests read `CLUSTRIX_TEST_SSH_HOST`, `_PORT`, `_USER` and `_KEY`.

It is shared by every test run on the machine. Never clean it up wholesale
(`scancel -u clx`, `pkill -f ...`): cancel the job ids and stop the pids you
started. Two review runs did otherwise and invalidated each other's results.
