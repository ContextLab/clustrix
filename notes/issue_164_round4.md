# Issue #164, round four (worktree `clustrix-env`, branch `work/named-env`)

Base: `1422862` (r3). Interpreter: Python 3.12.10 (also checked on 3.11.16).

## What round four changed

### M13 / M3 / M7 -- the untested submission seam
`tests/unit/test_submission_invariants.py` is new. It drives real submissions
through `SchedulerManager.submit_slurm_job` and `submit_ssh_job` against the
in-process SSH server (`tests/ssh_server.py`), with a fixture `conda.sh` and a
fixture `sbatch` in the account, and asserts the *invariant*: whatever the job
script contains, VENV2's interpreter is never VENV1's. All three round-three
survivors now die.

Two hazards found while writing it, both recorded in the module docstring:
* `PATH` must be curated, never inherited. The first draft leaked the
  developer's own conda and really created two `clustrix_venv*` environments
  inside `~/opt/anaconda3` (deleted). The fixture now asserts no other conda
  is reachable before installing its own.
* Standalone scripts must not be used for this; only pytest, whose
  `isolate_home` fixture keeps `~/.ssh/known_hosts` out of reach.

### NEW HIGH: the SSH conda probe was dead shell (r3 regression)
`setup_two_venv_environment` joined the two `_CONDA_SHELL_HELPERS` function
definitions with a space, producing `... } _clustrix_conda_works() { ...` --
a bash syntax error. The probe died before it looked anywhere, on every
cluster, so `conda_setup_prefix` was always `""` and every `conda create` in
the two-venv setup ran in a shell where conda had never been initialised.
Introduced by r3's N6. The search is now one shared implementation,
`_conda_search_lines()`, emitted as separate lines by both callers.

### Finding 1 -- probe ordering
The probe searched before asking whether conda works, so a working site conda
on `PATH` lost to `~/miniconda3`. Same ordering as the generated script now,
from the same function.

### Finding 2 -- interpreter version check on the named path
`named_environment_version_guard()` is emitted into the job script, before
anything runs, for both named branches. Decision: in-script rather than at
submission, because on this path clustrix has not located conda at all (that
is what the discovery block is for), the login node is often not the compute
node's image, and the job can answer for free on the machine where the answer
counts. Fails with both versions, the environment name, and the two settings.

### Finding 3 -- `conda info --base` whitespace
`_clustrix_conda_base` strips a trailing CR and surrounding whitespace via
`tr -d` plus IFS word splitting (`"$*"` keeps a path containing a space).

### Finding 4 -- "refused at config time" is now true
`validate_conda_env_name()` in `config.py`, called from `__post_init__`,
`configure()` and `load_config()`. Was only refused by
`resolve_named_environment` at submission -- after the job directory, the
signing key and the pickle were already on the cluster.

### The flaky test
`test_a_killed_writer_never_leaves_a_broken_file` waited
`random.uniform(1.0, 2.0)` and then killed the writer. Measured: first append
lands at ~0.45s idle, up to 3.2s with the machine 32 ways oversubscribed (152
of 160 sampled starts over 1.0s). Past 1.0s the child is killed before writing
and the test fails on its own "test is vacuous" guard. Reproduced 3/6 failing;
green after the fix under the same load. It now waits for the first append and
then kills, which is deterministic *and* stronger -- the kill always
interrupts a running write loop. No production race: nothing there was an
assertion about `ssh_security`.

### tkinter
`tests/test_auth_fallbacks.py::TestGetPasswordGui` fails on a CPython built
without `_tkinter` (Homebrew python@3.12 without python-tk@3.12) because
`@patch("tkinter.Tk")` imports tkinter to resolve its target. Class-level
`skipif`; assertions untouched, and they run on 3.11/3.10 and in CI.

## Goldens
11 named goldens (two new: `ssh_named_two_venv_plain`, and the guard changed
all of them) plus two new replication goldens
(`slurm_python_executable`, `slurm_two_venv_conda_python_executable`)
generated from the *pre-#164* generator at `4126a03` and byte-identical today.
One line -- `_want = (major, minor)` -- is normalised in the comparison
because it depends on the submitting interpreter; its value is asserted
separately.
