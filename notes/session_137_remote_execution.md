# Session notes — PR #137, remote execution made to work

**Date:** 2026-08-18 · **Branch:** `epic/production-readiness` · **PR:** #137 (open, not draft)
**Tip:** `41d72b6` · 20 commits this session · all 15 CI checks green.

## Where it landed

`@cluster` had never executed a function on any real backend. It now runs 18
distinct use cases correctly on five:

```
slurm-1  PASSED   18/18 correct
slurm-2  PASSED   18/18 correct
gpu-1    PASSED   18/18 correct
gpu-2    PASSED   18/18 correct
hf       PASSED   18/18 correct
```

Reproduce with `python scripts/verify_cluster_usecases.py`; full transcript in
`docs/evidence/usecase-matrix.txt`. Requires VPN access to the test clusters, and
`HF_TOKEN` for the HuggingFace target. Unreachable targets are reported as SKIPPED
and exit non-zero, so a skipped run cannot be read as a pass.

## The one idea worth carrying forward

Nearly every bug this session was **an asymmetry between a writer and a
reader**, and each was invisible to the type checker, the linter and the test
suite:

| Writer | Reader | Symptom |
|-|-|-|
| args dumped by value (dill) | loaded with stdlib pickle | `Can't get attribute 'Point'` |
| result written with dill | read with stdlib pickle | `isinstance()` false against the defining class |
| widget collects 26 fields | restored ~12 | switching profiles silently erased the rest |
| Apply writes config | never read config back | widget contradicted the library |
| freeze captures 185 packages | installer used a 9-package whitelist | `ModuleNotFoundError` on the worker |
| one pre-commit hook formats | another formats differently | no commit could satisfy both |

**When you touch one half of a round trip, test the round trip.** Several of
these were introduced *by the fix* for the previous one.

## What is verified, and how

- **Remoteness** is proven per target before any correctness claim: a
  `provenance` case returns the worker's hostname, PID and platform, and the
  target is abandoned unless all three differ from the caller. Added because
  red-teaming showed the harness reported 9/9 with `@cluster` replaced by an
  identity decorator.
- **Correctness** is against a locally computed expected value, never a
  hardcoded one.
- **Cases** cover closures, module-level globals and helpers, user-defined
  classes in both directions, a package living only in the working tree, a
  library outside any whitelist, disk I/O, 100k-element arguments, 50k-element
  returns, `None`, keyword-only arguments, and exception type propagation.

## Still unverified (do not claim otherwise)

- Cloud backends (AWS, Azure, GCP, Lambda) — the `KeyError` on their first
  line is fixed, but no credentials or instances were exercised.
- PBS and SGE against real hardware. Their scripts are now *correct* where
  PBS's previously ran a file that has never existed; that is not "tested".
- Kubernetes execution.
- Function flattening: it engages only when source analysis has already
  failed, and emits a parameterless script, so any function using its own
  arguments raises `NameError`. It then falls back to ordinary serialization
  and the job runs. Recorded rather than half-fixed.
- The wider suite still has failures outside these paths — #114.

## Gotchas for the next session

- **The environment cache key covers the install policy**, not just the
  requirements (`ENVIRONMENT_RECIPE_VERSION` in `utils.py`). Bump it whenever
  you change *what* gets installed, or clusters serve a stale environment and
  the change appears to do nothing. Upgrading a local package also changes the
  key, so the next matrix run rebuilds every cluster environment — slow, but
  correct.
- **Tests must not read `~/.clustrix`.** Profiles now persist there; the
  widget tests use an autouse fixture that redirects the store but still
  honours an explicit `config_dir`, because tests that build a store
  deliberately need to reach it.
- **HF GPU flavors bill by the second.** `hf_allow_gpu_flavors` defaults False
  and the built-in GPU profile leaves it False on purpose.
- **`tests/integration/` provisions real billable AWS resources** and needs
  `CLUSTRIX_ALLOW_BILLABLE=1`. The guard reads `config.args`, not
  `config.invocation_params.args` — do not "simplify" it.
- Credentials live outside the repo in `~/.clustrix-dev-credentials` (0700).

## Red-teaming that paid off

Four subagent passes, each finding things the suite could not:

1. Harness correctness — the identity-decorator false pass.
2. Harness safety — `SECRET_FIELDS` was hand-listed and would stop covering
   the config the day a cloud credential field was added.
3. Local-module serialization — a silent fallback that shipped exactly the
   payload the feature existed to prevent; a thread race on cloudpickle's
   global registry; an O(n) walk over every argument element.
4. Widget — nine findings including a placeholder key file written as a real
   value, which made password auth unreachable.

One caution: an agent's *reproduction* was wrong once (a lock the function
never touched, which modern cloudpickle embeds fine). The fix was still right;
the justification was not. Verify the repro, not just the conclusion.
