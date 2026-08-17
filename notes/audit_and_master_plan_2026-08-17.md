# Clustrix full audit + master plan — 2026-08-17

Session goal: explore the package thoroughly, triage all open GitHub issues, open a master plan
for production readiness plus sub-issues. Five parallel read-only audits were run (source tree,
test suite actually executed, all open issues, docs, security).

## Outcome

- **Master plan: #108** with **19 sub-issues (#109–#127)**, all linked via GitHub sub-issue API.
- **9 issues closed**, **11 issues updated** with evidence-cited corrections.
- Open issues went 29 → 40 (net +11: 9 closed, 20 opened).
- Nothing in the working tree was modified. No code changes made.

## Headline audit findings (all evidence-cited in #108)

| Area | Finding |
|-|-|
| Test run | `127 failed, 1738 passed, 26 skipped, 8 errors in 134.78s` |
| CI | Runs **15 of 2,280 tests** (~0.7%). `tests/unit/` only. |
| Coverage | 4 conflicting numbers: 74% (planning, unreproducible) / 5.69% (stale artifact) / 8.21% (CI scope) / 56.14% (full suite) |
| Mocks | 60/240 test files, **2,513 occurrences**; 3 inside `tests/real_world/` |
| Prod mock-awareness | `executor_scheduler_status.py:89` branches on `isinstance(ssh_client, Mock)`; `notebook_magic_mocks.py` imported by 6 prod modules |
| Dead code | ~5,100 lines (13%) with zero importers |
| Backends | ssh COMPLETE; slurm/sge/k8s/local PARTIAL; **pbs BROKEN**; **all cloud BROKEN/STUB** |
| Security | #107 was a FALSE POSITIVE. Real issue: unauthenticated pickle of remote results = remote→local RCE |
| Git | master was **30 commits ahead of origin** (unpushed epic #98 work) |

### Landmine (highest urgency)
`tests/integration/` has **zero pytest markers**, so `pytest -m "not real_world"` — the documented
unit-test command — selects `test_aws_eks_real_provision.py` ("this WILL create resources and incur
costs!"). Confirmed via `lsof`: ESTABLISHED connections to `ec2-*.compute-1.amazonaws.com:443`.
→ **#109**

### The structural pattern
Nearly every "done" item is half-done the same way: **scaffolding landed, seam did not.**
- closure vars detected + injected (`function_flattening.py:623,654-695`) but never passed (`:749-751`)
- REPL limit documented (`README.md:554`) but code still fails opaquely (`utils.py:118-119`)
- k8s provisioners exist but GCP `_assign_iam_role()` assigns nothing (`gcp_provisioner.py:356`)
- cloud path: `utils.py:151` writes `{"function":...}`, `executor_cloud.py:390` reads `['func']`
  → unconditional KeyError. Proof the path never ran.

## HuggingFace Jobs — VERIFIED WORKING (new capability)

Verified end-to-end today: real container ran, returned `CLUSTRIX_HF_OK 3.12.14 x86_64`.

- Namespace: **`contextlab`** (org, plan `academia`). Personal namespace gives `402 Payment Required`.
- Token must be **fine-grained with `job.write` scoped to the ORG**, not just the user.
  User-only scope → `403 missing permissions: job.write` on the org namespace.
- Diagnostic: `403` = token can't act here; `402` = token fine, nobody's paying.
- Flavors: `cpu-basic … h100x8`.
- `hf jobs run --namespace contextlab --flavor cpu-basic python:3.12-slim python -c "..."`

Strategic point: clustrix targets HF **Spaces** (wrong primitive, long-lived web apps) and it's a
stub. HF **Jobs** matches clustrix's model exactly. → **#118**

## Issue triage performed

| Action | Issues |
|-|-|
| Closed — false positive | #107 |
| Closed — completed | #85 (SGE fully implemented), #82, #72 |
| Closed — superseded/dup | #61 (dup of #98), #86 (dup of #99+#102) |
| Closed — archival | #92, #93, #94 |
| Updated | #66, #68, #88, #89, #90, #91, #95, #98, #99, #100, #101, #102, #103, #104, #105, #106 |

## Recommended order of attack (from #108 §9)

1. **#109 alone first** — only issue with ongoing cost if ignored.
2. #110 → #114 → #113 (runnable → green → CI on). Turning CI on before #114 makes master
   permanently red and invites re-adding `continue-on-error`.
3. #116 before #117 (remove prod mock-awareness before rewriting tests).
4. #122 before #115 (delete dead code before baselining coverage).
5. #118 early — unblocks honest testing for #119, #120, #126.

## Open decisions for the user

- **#112**: what to do with the 30 unpushed commits. Recommended (b): push to
  `epic/test-coverage-90-percent` branch + PR, rather than merging ~2,500 mock occurrences into
  master right before #117 removes them.
- **#111**: rotate HF tokens `hf_Fbf…` / `hf_hSV…` (local-only, never on GitHub — verified 3 ways),
  and enable GitHub secret scanning + push protection (currently disabled on this public repo).

## Verification commands used

```bash
git rev-list --left-right --count origin/master...master     # 0  30
grep -rc "pytest.mark" tests/integration/                    # 0 for every file
gh api repos/ContextLab/clustrix/secret-scanning/alerts      # 404 = disabled
gh api repos/ContextLab/clustrix/issues/108/sub_issues --jq 'length'   # 19
```
