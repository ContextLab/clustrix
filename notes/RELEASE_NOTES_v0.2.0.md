# Clustrix v0.2.0 — remote execution actually works

**Status: draft.** This is an honest beta. It says what works, what does not,
and how each claim was checked.

## The short version

Before this release, `@cluster` had never successfully executed a function on
any real backend. Not "worked sometimes" — never. Five independent defects sat
in the same seam, each of them alone fatal, and the test suite could not see
any of them because it mocked the parts that mattered.

All five are fixed. The same function now runs on a SLURM scheduler, an SSH
GPU host and a HuggingFace Jobs container, and there is a script in the repo
that reproduces it.

## Evidence

`python scripts/collect_execution_evidence.py` submits one identical function
to every backend and prints what comes back. Nothing is mocked; a target that
cannot be reached is reported as skipped, never as passing, and a result whose
hostname matches the caller's is treated as a failure.

Run from an arm64 macOS laptop on 2026-08-18:

```
========================================================================
slurm: SLURM scheduler (discovery.dartmouth.edu)
========================================================================
Conda available on remote system (/optnfs/common/miniconda3/etc/profile.d/conda.sh)
Reusing existing conda environments (py312_fab7c2f690ab)
RESULT (62s): {
  "host": "s07.hpcc.dartmouth.edu",
  "machine": "x86_64",
  "python": "3.12.13",
  "slurm_job_id": "9219882",
  "slurm_nodelist": "s07",
  "sum": 499500,
  "system": "Linux"
}

========================================================================
gpu: SSH + GPU host (tensor01.dartmouth.edu)
========================================================================
Conda available on remote system (/home/f002d6b/miniforge3/etc/profile.d/conda.sh)
GPU detected (8 devices), setting up GPU-enabled VENV2...
RESULT (65s): {
  "gpus": "NVIDIA RTX A6000, 49140 MiB  (x8)",
  "host": "tensor01.dartmouth.edu",
  "machine": "x86_64",
  "python": "3.12.13",
  "sum": 499500,
  "system": "Linux"
}

========================================================================
hf: HuggingFace Jobs (container)
========================================================================
RESULT (10s): {
  "host": "j-contextlab-6a83e58ce55292eada79bec8-avnc3f5x-8c50a-7k464",
  "machine": "x86_64",
  "python": "3.12.14",
  "sum": 499500,
  "system": "Linux"
}

========================================================================
SUMMARY
========================================================================
slurm  PASSED  s07.hpcc.dartmouth.edu   python 3.12.13  62.1s
gpu    PASSED  tensor01.dartmouth.edu   python 3.12.13  65.5s
hf     PASSED  j-contextlab-...         python 3.12.14   9.7s
```

The full transcript is in `docs/evidence/execution-evidence.txt`. Notebook
widget screenshots, light and dark, are in `docs/evidence/widget/`.

## Why it never worked

Five defects, all in the handoff between the caller and the worker.

**1. The two-venv handoff used stdlib pickle.** VENV1 deserialized the function
with dill and then re-serialized it for VENV2 with `pickle`, which serializes
functions by qualified name. Every function defined in the caller's `__main__`
— that is, every realistic `@cluster` target — failed with
`Can't pickle <function f>: attribute lookup f on __main__ failed`, and the
truncated file gave VENV2 `EOFError: Ran out of input`. Both venvs already had
dill installed; the handoff now uses it.

**2. Both conda environments were pinned to `python=3.9`** regardless of the
caller, while the function computed `local_python_version` and never used it.
dill embeds CPython bytecode, which is not portable across minor versions, so a
3.12 caller got `RuntimeError: unknown opcode`.

**3. `remote_work_dir` defaulted to `/tmp/clustrix`**, which is node-local on
SLURM, PBS and SGE. The environment built on the login node is simply absent
when the job runs, so the script died at its first `source .../activate` with
exit 127 — before it could write any output, leaving an empty directory and no
diagnostic. The default is now `~/.clustrix/jobs`, and `~` is resolved against
the remote `$HOME` because SFTP does not expand it.

**4. conda was invisible to clustrix on clusters that need it most.** paramiko's
`exec_command` starts a non-interactive, non-login shell that never sources the
profile, and at many sites `conda` is a wrapper that refuses to work until
`conda.sh` has been sourced. Clustrix concluded conda was absent and fell back
to building two virtualenvs with pip over NFS — minutes of work that then timed
out. It now locates `conda.sh` and sources it in both the setup batch and the
generated job script.

**5. Any host in the same domain was mistaken for the cluster.**
`ClusterFilesystem` matched hostnames by substring and by shared institution
domain, so a laptop on the VPN
(`vpn-two-factor-general-229-128-226.dartmouth.edu`) was judged to *be*
`discovery.dartmouth.edu`. Clustrix then looked for the job's result on the
laptop and reported the job's status as unknown while it ran fine on the
cluster. The test is now factual: this host is the target host **and** the
remote working directory is visible here.

## New: HuggingFace Jobs backend

`cluster_type="huggingface"` runs functions in a container. It needs no cluster
reservation, no VPN and no institutional SSH credentials, which is what makes
it usable as an integration-test substrate — the previous "real-world" tests
depended on hosts CI cannot reach and had failed on all of their last 60 runs.

Three things about it are deliberate:

* **Results are verified before they are deserialized.** Unpickling is code
  execution, and the result is recovered from a log stream. Each job gets a
  fresh key passed as a HuggingFace secret; the container emits an HMAC-SHA256
  over what it wrote, and the caller refuses anything whose tag fails a
  constant-time compare. (The SSH and scheduler paths now do the
  same thing — see "Security" below.)
* **The image tracks the caller's Python minor version**, for the same reason
  defect 2 existed.
* **GPU flavors require `hf_allow_gpu_flavors=True`.** The gate is a prefix
  test — anything not `cpu-*` — rather than a list of GPU names, because a
  denylist fails open on hardware added after it was written. The first draft
  named `h100`, which HuggingFace does not offer, while `a100x8`, `h200x8` and
  `rtx-pro-6000x8` were ungated.

## Notebook widget

Rebuilt against a design that inherits JupyterLab's own theme tokens, so it
follows the user's light/dark theme, uses the notebook's font, and no longer
pulls Lexend Deca from Google Fonts (which failed on air-gapped login nodes).

Two of its controls were not doing what they said:

* **Apply did not apply.** It saved a profile and printed "Applied
  configuration"; the source noted "This integration point would need to be
  connected to the main config system", and it never was. Every setting typed
  into the widget was invisible to `@cluster`. It now calls `configure()`.
* **"Test job submission" could not fail.** It printed four ticks and "All 4
  test jobs executed and cleaned up properly" without calling an executor —
  reporting success with an empty host, zero cores and a memory string of
  "banana". It now submits a real job and reports what happened.

Also: `import clustrix` no longer paints a widget into the notebook
(`CLUSTRIX_AUTO_WIDGET=1` restores it), seven of the eight offered profiles no
longer fail on selection because they never existed, and HuggingFace Jobs is
configurable from the UI.

The magic is now `%%remote`. `%%clusterfy` still works and warns.

## Also fixed

* Conda environments were named per job directory, so every call built two new
  ones (~10 minutes each on a shared filesystem) and left them behind. They are
  now content-addressed on the Python version and requirement set, and reused.
* An empty `squeue` result no longer means "finished" — it is also what you get
  for a few seconds after `sbatch`. `sacct` is consulted, so a queued job is no
  longer abandoned about fifteen seconds in.
* A job that leaves no result and no error now reports `sacct`'s verdict
  instead of "completion status unknown", with an explicit explanation for exit
  code 127.
* Running the test suite no longer writes into the developer's real
  `~/.clustrix` — `CLUSTRIX_CONFIG_DIR` overrides it, which containers and CI
  need anyway.
* `pip install -e ".[dev]"` now pulls ipywidgets, ipython and pytest-timeout.
  44 widget tests were failing on a missing optional dependency that read like
  a code defect; CI never saw it because CI installed the widget extra
  explicitly.
* Clustrix's own helper functions can now be shipped to workers that do not
  have clustrix installed (`utils.make_portable_function`). dill pickles a
  module-level function by reference, so the GPU-detection probe was submitting
  a job per `@cluster` call that always died with
  `ModuleNotFoundError: No module named 'clustrix'`.

## Security: results are verified before they are deserialized

Loading a pickle executes arbitrary code, so `result.pkl` -- a file fetched
from a remote host -- was a remote-to-local code execution path on every
backend. It is now checked first, everywhere.

At submission the job directory is created `0700` with a 32-byte random key
inside it at `0600`. The job script reads the key from that file (rather than
having it baked into `job.sh`, which is world-readable on some shared
filesystems) and writes an HMAC-SHA256 over exactly the bytes it wrote. The
caller compares in constant time before unpickling, and refuses an absent,
truncated or mismatched signature.

Demonstrated on tensor01 by overwriting a finished job's `result.pkl` in place
and leaving the original signature -- what someone with write access to the
job directory would do:

```
result.pkl overwritten with an unsigned payload
REFUSED: Job ssh_1787030156 result failed its integrity check.
         Refusing to deserialize it.
```

This bounds the trust to whoever can already read the job directory. It is
deliberately not claimed as a defence against a wholly compromised remote
host, which runs your function anyway.

## Also fixed since the first draft

* **PBS did not work at all.** Its script ended with
  `python execute_function.py`, and that filename appears exactly once in the
  codebase -- on that line. Nothing has ever created it. SGE carried its own
  copy of the older single-venv script and so silently missed every fix made
  to the SLURM one. All three schedulers now share one execution body; a test
  compares them directly.
* **`"16GB"` is not a Kubernetes quantity.** It went into the pod manifest
  verbatim, so clustrix's own `default_memory` produced a manifest the API
  server rejects. Memory is now rendered per scheduler: `16Gi` for Kubernetes,
  `16G` for SLURM, `16gb` for PBS, `16G` for SGE.
* **The cloud script's first line was a `KeyError`.** It read
  `func_data['func']`, a key `serialize_function()` has never produced.
* **The widget accepted anything** -- `cores=0`, `memory="banana"`,
  `time="soon"`, `port=99999`, an empty host -- and reported success. It now
  reports every problem at once and refuses to apply or submit.
* **Kubernetes had no widget fields at all**, so only the shipped defaults were
  reachable. It has a section now, as does HuggingFace Jobs.
* **The test suite wrote into the developer's home and repository** -- an
  `integration_test` profile appended to the real `~/.clustrix/clustrix.yml`,
  plus stray `test.yml` and `test_config.yml`. Config paths are anchored, and a
  session fixture points `CLUSTRIX_CONFIG_DIR` at a throwaway.
* **An unreachable host hung instead of erroring.** SSH connections had no
  timeout at all, so paramiko fell back to the OS default.

## On the "~5,100 lines of dead code" figure

It does not reproduce. Every module under `clustrix/` is imported by
something; counting only production imports leaves four files, three of which
are a declared entry point, the 1Password integration, and code with a live
consumer. `vulture --min-confidence 90` finds thirteen items across the whole
package: two genuinely unused imports (removed here) and eleven unused
variables that are mostly `__exit__` parameters the protocol requires.

Two files -- `enhanced_notebook_widget.py` and `validation_alerts.py`, 1,232
lines together -- are plausible deletions, but each has exactly one consumer,
so removing them means removing those too. That is a scope decision, not a
cleanup, and it is not being smuggled in behind a number that turned out to be
wrong.

## What is still broken

* **The cloud backends (AWS, Azure, GCP, Lambda) are not verified.** The
  `KeyError` on their first line is fixed, but reaching that code needs
  credentials and provisioned instances that were not exercised. #119's other
  findings -- the provider interface mismatch, the placeholder hostnames --
  are untouched.
* **PBS and SGE have not been run against real hardware.** There is none to
  hand. Their scripts are now correct where before PBS's could not possibly
  have worked, which is a different claim from "tested".
* **Kubernetes execution is unverified**, though it is now configurable and
  its memory quantities are valid.
* **The suite still has failures** outside the paths exercised here: 159
  failed / 1402 passed / 36 errors, against 211 / 1194 / 72 at the start of
  this branch. Re-baselining it properly is #114.
* **The result-signing scheme does not defend against a compromised remote
  host.** Nothing that runs your code for you can.

## Reproducing the evidence

```bash
pip install -e ".[dev]"
python scripts/collect_execution_evidence.py
```

The Dartmouth hosts are split-DNS internal names and need the VPN; without it
they are reported as skipped. HuggingFace Jobs needs `HF_TOKEN` and a namespace
on a plan that can run jobs.
