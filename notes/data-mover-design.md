# Design: make Clustrix a data mover

Status: proposal. Nothing here is implemented.
Written: 2026-08-19, branch `work/priorities-and-docs`.
Tracking issue: #151
Trigger: the owner's observation that "the 'clustrix is not a data mover'
limitation is in conflict with one of the original design elements. it *should*
be a data mover."

This document does three things: (1) finds the original design element in the
record and quotes it, including the part that *contradicts* the owner's framing;
(2) establishes from the code what already exists; (3) proposes a phased plan.

---

## 1. The record

### 1.1 The design element that does conflict — issue #64 (CLOSED)

`gh api repos/ContextLab/clustrix/issues/64` — "Core Architecture: Function
Serialization and Dependency Management for Remote Execution". Under "Current
Approach Limitations" it lists as a **defect to be fixed**:

> 3. **No local file support** - cannot access local modules, data files, or custom code

and under "Proposed Solution Direction":

> ### Dependency Packaging Approach
> 1. **Dependency Detection**: Analyze function for local imports, file references, and dependencies
> 2. **File Packaging**: Create zip archive of all local dependencies
> 3. **Remote Deployment**: SCP and unpack files on cluster with unique identifier (MD5 hash)
> 4. **Environment Recreation**: Patch import paths and execute function in recreated context

It also names, verbatim, three of the hard problems this document has to solve:

> - How to maintain security while allowing file transfer?
> - **Network transfer overhead for large dependencies**
> - **File system security considerations**

This issue is **closed**. It was closed on the strength of a design document,
`docs/function_serialization_technical_design.md`, whose final revision
(commit `3417b84`, "Update technical design document - IMPLEMENTATION COMPLETE")
claims:

> ### ✅ Phase 2: Dependency Packaging Core (COMPLETE)
> - ✅ Create `FilePackager` for selective file collection
> - ✅ Build package deployment system for remote transfer
> ...
> ### ✅ Phase 4: Integration & Testing (COMPLETE)
> - ✅ Integrate with existing `ClusterExecutor`

Section 2 below shows that the last of those bullets is false: `FilePackager`
has no importer in the execution path. The doc was deleted in `fb373d2`
("Major repository cleanup and reorganization").

So the conflict is real and it is specific: **shipping the function's local data
files was a stated architectural goal, was designed, was partially built, and
was then abandoned mid-wiring** — after which the documentation was written to
describe the abandonment as a deliberate scope boundary.

### 1.2 Supporting element — issue #20 (CLOSED), "Proposed classes"

The original `Job` class was specified to carry file paths as first-class state:

> # `Job`
> - stores a function, arguments, and pointers to the appropriate file paths (e.g., data, results, scratch)

and `Result`:

> - Attributes contain pointers to:
>   - Copy of Job object that produced the results
>   - Any data object related to the computations (these can be actual objects or filepaths, urls, etc.)

"data, results, scratch" as three distinct path roles is the vocabulary this
design should adopt. It is not the same as "ship the bytes", but it does mean
the original model had the job knowing where its data lived, which the current
model does not.

### 1.3 The part of the record that contradicts the owner — issue #10

This must be reported, not buried. In issue #10 ("Things we'd like to see in a
cluster tools package") the owner wrote, verbatim:

> Syncing *code* seems like a great idea-- we want to be able to ensure that the
> user is running what they think they're running, and managing file transfers
> would be fantastic. Syncing *results* or *data* seems like a bad idea; I'm
> imagining that could eat up some serious space on a laptop hard drive that
> might not have enough room.

and Paxton, agreeing:

> Syncing data would take an incredibly long time and eat up space on the local
> machine.

So the original position was **asymmetric**: code up = wanted ("managing file
transfers would be fantastic"); data down = explicitly rejected, on grounds of
local disk and transfer time. Issue #64, three years later, widened "code" to
"local modules, data files, or custom code" without revisiting #10.

The honest synthesis, and the one this design adopts: *the upload direction was
always in scope and was never delivered; the download direction was deliberately
out of scope and the objection to it (laptop disk, transfer time) is still
valid.* The feature is therefore not symmetric, and should not be built as if it
were. Outputs come back by explicit request only, never by inference.

### 1.4 Deep background

The repository's own ancestor is a set of `rsync` scripts —
`auto_backup/rsync-to-kziman.sh` (commit `6112959`, 2016-10-14) and
`rsync_discovery.sh` (`2d6e3ea`) — i.e. this project literally began life as
data-movement tooling for Discovery before it was a job submitter. This is
atmosphere, not a design element; it is recorded here so a future session does
not go looking for it again.

---

## 2. What exists today

Every claim below has a file:line.

### 2.1 Real, wired-in transfer — but only for the payload

- `clustrix/executor_connections.py:146` `upload_file(local_path, remote_path)`
  — `sftp.put`, no chunking, no progress, no resume.
- `clustrix/executor_connections.py:156` `download_file(remote_path, local_path)`
  — `sftp.get`, same.
- Callers: `executor_schedulers.py:99` uploads `function_data.pkl`;
  `executor_core.py:197` downloads `result.pkl`;
  `executor_scheduler_status.py:473` downloads the error pickle;
  `executor_core.py:368,372` are thin `_upload_file`/`_download_file` wrappers.

So the transport exists and is exercised on every remote job. What is missing is
not SFTP; it is everything around it.

### 2.2 Detection exists and is fully orphaned

`clustrix/dependency_analysis.py` builds a `DependencyGraph` with
`file_references: List[FileReference]` (line 95), `filesystem_calls` (96),
`data_files: Set[str]` (line ~99) and `requires_cluster_filesystem` (line ~100).

- `_analyze_file_references` (line 249) finds paths three ways: a string-literal
  first argument to a call named in `self.file_operations = {"open", "read",
  "write", "load", "dump", "save"}` (line 140); any method call named
  `read/write/readline/writelines`, recorded as `path="<unknown>"` (line ~280);
  and any string constant containing a separator and ending in one of a fixed
  extension list `.txt .csv .json .xml .yaml .yml .h5 .hdf5 .pickle .pkl .npy
  .npz .dat .log .conf .cfg .ini` (line ~316).
- `add_file_references` (line 110) promotes every **relative** reference into
  `data_files`.
- `_analyze_filesystem_calls` (line 326) records calls to the nine `cluster_*`
  read-only helpers and sets `requires_cluster_filesystem`.

`clustrix/file_packaging.py` then does the packaging: `_add_data_files`
(line 319) resolves each `data_files` entry against `context.working_directory`,
skips `"<unknown>"`, and `zf.write`s it into `data/<relpath>` inside a zip.
`_add_filesystem_utilities` (line 344) inlines `filesystem.py` into the zip.

**None of this runs.** `grep -rn "FilePackager\|package_function" clustrix/decorator.py
clustrix/executor_core.py clustrix/executor_connections.py clustrix/utils.py`
returns nothing. The only importers of `clustrix/file_packaging.py` are
`clustrix/__init__.py:39` (re-export) and `tests/test_file_packaging.py`.
`analyze_function_dependencies` is likewise imported only by
`file_packaging.py:19`, `__init__.py:28`, and tests. This is the same class of
finding as issue #122 ("Delete ~5,100 lines of orphaned modules").

Consequence: the *analysis half* of a data mover is written and tested; the
*wiring* is absent; and the analysis half, as written, is too loose to trust
(a bare string `"results/2024-01.csv"` in a comment-adjacent literal becomes a
file to upload).

### 2.3 The `cluster_*` API is read-only

`clustrix/filesystem.py:567-660` defines exactly nine public functions:
`cluster_ls`, `cluster_find`, `cluster_stat`, `cluster_exists`, `cluster_isdir`,
`cluster_isfile`, `cluster_glob`, `cluster_du`, `cluster_count_files`. There is
no `cluster_put`, `cluster_get`, `cluster_copy`, or `cluster_rm`. The
`ClusterFilesystem` class holds an SFTP client (`_get_sftp_client`, line 225)
but uses it only for `stat`-style reads.

### 2.4 Shared-filesystem detection already exists (and is nearly right)

`clustrix/filesystem.py:113` `_auto_detect_cluster_location` already answers the
"is the file already visible to the worker?" question, and its docstring/comment
records a real past bug worth preserving:

> Two things are actually sufficient, and both are checkable:
>   * this host IS the target host, by name; or
>   * the remote working directory is visible here, which is what
>     "shared filesystem" means and what the code needs to be true.

It requires `same_host and shared_filesystem` before downgrading to local ops.
For staging we need a *weaker but differently-shaped* test — see §3.5.

### 2.5 Cleanup

`clustrix/config.py:86` `cleanup_on_success: bool = True`. On success,
`executor_core.py:214` runs `rm -rf {remote_dir}` over SSH. The job directory is
the only thing it knows about, so today any staged file placed inside the job
directory is destroyed with it and any staged file placed outside it leaks.

### 2.6 Security posture for inbound bytes

`executor_core.py:131` defines `_verify_result_signature`; it reads
`result.pkl.hmac` off the worker (line 160) and `executor_core.py:204` calls it
*before* `dill.loads` at line 211. The reason is stated in CLAUDE.md: loading a pickle executes code, so a file fetched
from a remote host is a remote-to-local code-execution path.

### 2.7 Prior art inside the repo for out-of-band staging

`clustrix/hf_jobs.py:352-375` already stages an oversized payload to a private
HF dataset repo rather than an env var, with the rationale in the docstring:

> HuggingFace rejects very large environment variables, which capped a
> job's arguments at a few hundred kilobytes -- fine for a function,
> useless for data. Staging lifts that cap without asking the caller to
> restructure their code

That is the same shape of problem, solved once already. Whatever manifest and
addressing scheme we pick should be able to describe that mechanism too.

### 2.8 Summary

| Capability | State |
|-|-|
| SFTP put/get | Exists, wired, used every job (`executor_connections.py:146,156`) |
| Detect files a function references | Exists, orphaned, heuristic (`dependency_analysis.py:249`) |
| Package data files into an archive | Exists, orphaned (`file_packaging.py:319`) |
| Shared-filesystem detection | Exists, wired for `cluster_*` only (`filesystem.py:113`) |
| Explicit user-facing put/get | **Missing** |
| Content-addressed dedup / manifest | **Missing** |
| Size policy, refuse/warn/delegate | **Missing** |
| Return-path (output) staging | **Missing** |
| Resume / integrity on partial transfer | **Missing** |
| Staged-input lifecycle vs `cleanup_on_success` | **Missing** |

---

## 3. Design

### 3.0 The one-line statement of intent

Clustrix should move the data a job needs, when the user says which data, with
the same job-scoped lifecycle and the same integrity guarantees as the function
payload — and it should refuse, loudly and early, the cases where SFTP is the
wrong tool.

### 3.1 Semantics: explicit declaration, not inference

**Decision: explicit declaration. Automatic detection is not offered, even
opt-in, in the first three phases.**

Reasoning, grounded in §2.2: the existing detector's third rule is "any string
constant containing `/` or `\` and ending in one of 17 extensions". Under
automatic staging, a function containing the literal `"s3://bucket/notes.log"`
or `"config/prod.yaml"` (a *remote* path, or a path that does not exist locally,
or a 40 GB file the user never meant to send) triggers a transfer. Silent,
inferred, expensive I/O is the worst possible failure mode for this feature: the
user cannot see it in their source, cannot predict it, and pays for it in wall
clock and quota. The project has already been burned once by an inference-based
shortcut in the sibling detector (`filesystem.py:113`'s comment describes the
laptop-on-VPN misidentification).

The API is therefore a declaration on the decorator:

```python
@cluster(
    cores=8,
    inputs=["data/subjects.h5", "config/model.yaml"],   # staged in, before the job runs
    outputs=["results/*.npz"],                          # fetched back, after it succeeds
)
def fit(subject):
    ...
```

- `inputs`: list of local paths (files, directories, or globs). Each is staged
  to the worker before the job script runs, and is visible to the function under
  a **stable, predictable relative path** — the same relative path it had under
  the local working root. The function's code does not change between local and
  remote execution. This is the property that makes the feature worth having;
  anything that requires the user to rewrite paths is not better than `scp`.
- `outputs`: list of paths **relative to the job's working directory on the
  worker**, optionally globs. Fetched only after the job reports success.
- Both default to `None`, i.e. today's behaviour, unchanged.

A programmatic escape hatch for the cases the decorator cannot express (paths
computed at call time) is the imperative pair, added to `filesystem.py`
alongside the existing nine:

```python
cluster_put(local_path, remote_path, config=None) -> StagedFile
cluster_get(remote_path, local_path, config=None) -> Path
```

These are the primitives; the decorator keywords are sugar over them. Building
the primitives first means the decorator layer can be tested against something
already verified.

**The return path is opt-in and never inferred.** This is issue #10's objection
("could eat up some serious space on a laptop hard drive") honoured as a
standing constraint, not overruled. A function that writes 400 GB of
intermediates and returns a scalar must continue to return only the scalar.

### 3.2 Size: three bands, and the boundaries are configurable

Paramiko's SFTP is a Python-level implementation over a single SSH channel. It
is fine for megabytes and bad for tens of gigabytes. Rather than pretend a
single number exists, define bands with configurable thresholds on
`ClusterConfig`:

| Band | Default bound | Behaviour |
|-|-|-|
| Small | `< stage_warn_bytes` (default 100 MB) | Transfer silently. |
| Large | `< stage_max_bytes` (default 5 GB) | Transfer, but log a warning naming the file, its size, and the measured throughput, and emit an ETA before starting. |
| Too large | `>= stage_max_bytes` | **Refuse** by default. Raise with a message that names the file, the size, the threshold, the config key to raise it, and the two better options (put the file on shared storage; or `stage_backend="rsync"`). |

**Refuse, not warn, past the top band**, because the alternative is a job that
appears to hang for hours with no output. A refusal that names the remedy is
strictly better than a silent multi-hour SFTP. `stage_max_bytes=None` disables
the ceiling for users who know what they are doing.

**Delegation** is a `stage_backend` config field, not automatic:
`"sftp"` (default, always available), `"rsync"` (requires `rsync` on both ends
and a working `ssh` binary; gets us restart, delta transfer, and compression for
free), `"globus"` (explicitly out of scope for the phases below — noted only so
the field's shape does not have to change later). Auto-selecting `rsync` based
on size is tempting and should be rejected in v1: it makes the transport depend
on the data, so a job that worked yesterday takes a different, less-tested code
path today.

### 3.3 Idempotence and caching

**Content hash, with an mtime+size fast path.** Neither alone is sufficient:
mtime+size misses same-size edits and is wrong across filesystems that do not
preserve mtime; a full BLAKE2b of 200 GB on every submission costs minutes of
local I/O even when nothing changed. The rule:

1. Read `(size, mtime_ns, inode)` for the local file. If it matches the local
   cache entry for that path, reuse the cached digest without re-reading.
2. Otherwise hash the file (BLAKE2b-256, streamed) and update the local cache.
3. Ask the remote manifest whether that digest is already present.
4. If present and its recorded size matches, skip the upload entirely.

**Where the content lives on the worker:** a content-addressed store under
`{remote_work_dir}/_stage/<digest[:2]>/<digest>`, *outside* any individual job
directory. Each job directory then contains **symlinks** (falling back to hard
links, then copies, on filesystems that refuse them) at the paths the function
expects. This is what makes "re-running a function does not re-upload the 200 GB
file" true across jobs, not merely within one.

**Where the manifest lives:** two of them.
- Remote: `{remote_work_dir}/_stage/manifest.json`, the authority on what is
  present on that cluster. Written under an exclusive `flock` and an atomic
  rename, because two clustrix processes on the same account will race. Records
  `digest -> {size, first_seen, last_used, refcount}`.
- Local: `~/.clustrix/stage-cache/<host>.json`, purely an optimisation, holding
  the `(path, size, mtime_ns, inode) -> digest` map. Deleting it must only cost
  time, never correctness — so step 3 always consults the remote manifest, and
  step 4 verifies size as well as digest.

Deliberately **not** doing: partial-file/chunk-level dedup, or trusting a remote
digest we did not compute ourselves. Both are large and neither is needed to
make re-runs free.

### 3.4 Deduplication across jobs vs. correctness

A content-addressed store makes two different jobs referencing the same bytes
share one copy. The refcount in the manifest is what allows cleanup (§3.6) to
know when the last referrer is gone. A crashed process that never decrements is
handled by `last_used` aging, not by trusting the refcount alone.

### 3.5 Shared filesystems: skip the transfer

On HPC the input is very often already on `/scratch` or `/dfs`, visible to the
compute node. Uploading it is pure waste and may exceed quota.

The check must be about *the specific file*, not about the host. Extend the
existing idea in `filesystem.py:113` with a per-path probe:

1. If `os.path.realpath(local_path)` is inside a directory the config marks as
   shared (`shared_filesystem_roots: List[str]`, new config field, e.g.
   `["/dfs", "/scratch"]`), and
2. a one-shot remote `stat` of the same absolute path returns a matching size
   and mtime,

then record the file in the job manifest as **already present**, stage nothing,
and point the job at the original absolute path.

Step 2 is not optional and must not be replaced by name matching. The comment at
`filesystem.py:113` documents exactly what happens when this kind of question is
answered by names: a laptop on the VPN was judged to *be* the cluster. The
remote `stat` is one round trip and settles it.

Auto-discovery of shared roots (e.g. parsing `mount` output on the worker) is a
nice later addition and explicitly out of scope for the phases below.

### 3.6 Cleanup

Three lifetimes, and they must be distinguished:

| Thing | Lifetime | Governed by |
|-|-|-|
| Job directory (script, `function_data.pkl`, `result.pkl`, links) | Removed on success | `cleanup_on_success` (today's behaviour, unchanged) |
| Staged input blobs in `_stage/` | Survive the job; reclaimed by age/refcount | new `stage_cache_ttl_days` (default 7) and `stage_cache_max_bytes` |
| Fetched outputs on the *local* machine | Never touched by clustrix | — |

`cleanup_on_success` removing `_stage/` content would defeat the entire point of
§3.3, so it must not. But an unbounded cache silently consuming a user's cluster
quota is exactly the failure the original #10 objection was about, pointed the
other way. Hence: a reaper that runs at submission time, before staging, and
evicts entries with `refcount == 0` and `last_used` older than
`stage_cache_ttl_days`, oldest first, until the store is under
`stage_cache_max_bytes`. It logs what it evicted. There is also an explicit
`clustrix.clear_stage_cache(config)` and a CLI verb, because a user who is over
quota needs a hammer, not a policy.

**Outputs are never deleted by clustrix**, locally or remotely — a
data-destroying default is unacceptable here.

### 3.7 Security

**Downloaded outputs do not get an HMAC, and this is a real decision, not an
oversight.**

The reason `result.pkl` is HMAC-verified (`executor_core.py:131`) is that
clustrix *itself* calls `dill.loads` on those bytes, and loading a pickle
executes code. The remote-to-local code-execution path is created by clustrix's
own deserialization, not by the transfer. A staged output file is written to
disk and handed to the user as a path; clustrix never interprets it. The
attacker who could tamper with an output file is the same attacker who controls
the remote account, who could equally tamper with the input the user asked for.
Adding an HMAC would not change what that attacker can do.

What we do owe the user instead:

1. **Integrity, not authenticity.** Record the digest of every staged input in
   the job manifest and re-verify it on the worker before the job runs; record
   the digest of every fetched output on the worker and verify it locally after
   the fetch. This catches truncation and corruption, which is the realistic
   failure, and costs one hash.
2. **Path confinement.** `outputs` patterns are resolved on the worker and every
   result must be confined to the job directory after `realpath` resolution. A
   pattern or symlink that escapes it (`../../../etc/shadow`) is rejected, not
   clamped. Likewise, an archive/manifest entry may never write outside the
   intended local destination — the Zip-Slip class of bug.
3. **Never widen permissions.** Staged files are created `0600` / directories
   `0700`, matching the care already taken in
   `executor_connections.py::create_remote_file` ("so a secret never exists on
   disk world-readable even briefly").
4. **Refuse to stage credential-shaped paths by default.** A declared input
   matching `~/.ssh/*`, `~/.aws/*`, `*.pem`, `*.key`, `.env` raises unless the
   user passes an explicit override. Users do point staging at their own home
   directory by accident.
5. **The manifest is remote-origin data.** It is parsed with `json.load` into a
   fixed schema with type checks, never `eval`, never pickle. If a future
   version wants to put anything executable in it, that decision inherits the
   full HMAC requirement.

### 3.8 Failure modes and what each must do

| Failure | Required behaviour |
|-|-|
| Partial transfer (connection drop) | Upload to `<digest>.partial`, `fsync`, verify size, then atomic `rename` into place. A `.partial` file is never linked into a job dir and is reaped. Never register a digest that was not fully verified. |
| Disk quota exceeded on the cluster | Detect the SFTP/`rsync` error, run the reaper, retry **once**; on a second failure raise a message naming the store path, its current size, and `clear_stage_cache`. Do not silently fall back to "run the job without the file" — the job would fail later and more confusingly. |
| Permission denied on the remote work dir | Fail at submission, before any bytes move, by probing writability once per session. |
| File changes mid-upload | Hash first, transfer, then re-`stat` locally; if `(size, mtime_ns)` moved, raise. Do not silently register a digest that does not describe the bytes sent. |
| Declared input does not exist locally | Fail at decoration/submission with the path and the resolved absolute path. Never skip silently. |
| Declared output not produced by the job | Warn and continue by default, listing what was missing; `require_outputs=True` turns it into an error. A job can legitimately produce a subset. |
| Symlink in the input set | Follow by default and stage the target's bytes; refuse a link that escapes the local working root unless the target is separately declared. |
| Two clustrix processes staging the same digest concurrently | Per-digest lock file in the store; the loser waits for the winner's atomic rename rather than uploading a second copy. |
| Worker cannot symlink (some parallel FS, container overlay) | Fall back hard link, then copy; log which was used, because a copy doubles quota usage. |

### 3.9 Explicitly out of scope

Naming these is what keeps the feature from becoming a workflow engine:

- No sync/mirroring semantics, no watch mode, no bidirectional reconciliation.
- No DAG, no data-dependency-derived ordering between jobs.
- No cross-cluster transfer (cluster A -> cluster B). Local is always one end.
- No object-store backends (S3/GCS) in the transfer path. If the user's data is
  in S3, their function can read it from S3.
- No compression/format conversion, no chunk-level delta (that is what
  `stage_backend="rsync"` delegates for).
- No provenance database. The manifest records what is present, not what
  produced it.
- No automatic inference of inputs from source (§3.1), in any phase covered here.

---

## 4. Phases

Each phase is independently shippable, independently useful, and has a
definition of done that requires real hardware — per the project standard that
nothing is claimed working until it has run against the real thing. The
available real targets are the verified backends: a real SLURM scheduler, a real
SSH GPU host, and HF Jobs (`docs/evidence/`, regenerated by
`scripts/verify_cluster_usecases.py`).

### Phase 1 — Primitives: `cluster_put` / `cluster_get`

Add the two imperative functions to `clustrix/filesystem.py` alongside the
existing nine, backed by `ClusterFilesystem`'s existing SFTP client
(`filesystem.py:225`) for the remote case and `shutil` for the local case.
Includes: streamed BLAKE2b digest, atomic `.partial` + rename, `0600`/`0700`
modes, path-confinement checks, the size bands of §3.2 with `stage_warn_bytes` /
`stage_max_bytes` on `ClusterConfig`. No manifest, no caching, no decorator
integration.

**Done when:** a file is `cluster_put` to the real SLURM host and to the real
SSH GPU host and its digest verified there by a remote command; `cluster_get`
round-trips it back byte-identical; a file over `stage_max_bytes` raises with
the documented message; a killed transfer leaves only a `.partial` and no
registered file; unit tests cover confinement and mode; evidence committed under
`docs/evidence/`.

### Phase 2 — Content-addressed store, manifest, and dedup

`{remote_work_dir}/_stage/` layout, the locked+atomic remote manifest, the local
`(path,size,mtime,inode) -> digest` cache, and the skip-if-present path of §3.3.
Symlink/hardlink/copy fallback for materialising a blob into a directory.
Per-digest concurrency lock.

**Done when:** on the real SLURM host, staging the same ~1 GB file twice
transfers bytes exactly once (measured, not asserted); deleting the local cache
still results in zero re-upload; two concurrent processes staging the same
digest produce one copy and one manifest entry; a corrupted manifest is detected
and rebuilt from the store's contents rather than crashing.

### Phase 3 — `@cluster(inputs=..., outputs=...)`

Wire the primitives into the decorator and executor: resolve declarations at
submission, stage inputs, materialise them into the job directory at their
original relative paths, run, then collect declared outputs on success and fetch
them. Integrity digests on both directions per §3.7.1. `require_outputs`.

**Done when:** an unmodified function that opens `"data/x.csv"` relatively runs
identically local and on the real SLURM cluster with `inputs=["data/x.csv"]`;
outputs matching a glob come back and verify; a job that fails produces no
output fetch; an output pattern escaping the job dir is rejected on the worker;
`cleanup_on_success` removes the job dir while leaving `_stage/` intact
(verified by a second run that re-uses the blob).

### Phase 4 — Shared-filesystem elision and quota safety

`shared_filesystem_roots` config, the per-path remote `stat` probe of §3.5, the
reaper (`stage_cache_ttl_days`, `stage_cache_max_bytes`),
`clustrix.clear_stage_cache`, the quota-exceeded retry-once path, and the CLI
verb.

**Done when:** on the real SLURM cluster, a file already on `/dfs` (or the
site's shared root) is staged with **zero** bytes transferred and the job reads
the original path; the negative case — same filename, different content, not
actually shared — is correctly *not* elided; a store filled past
`stage_cache_max_bytes` is reaped to below it on the next submission and the
eviction is logged; an induced quota failure produces the documented error, not
a hang.

### Phase 5 — `stage_backend="rsync"` (optional, only if Phase 1-4 hit a wall)

Delegate to `rsync -az --partial --info=progress2` over the existing SSH
identity for entries above `stage_warn_bytes`, keeping the same manifest and the
same digests. Strictly opt-in.

**Done when:** a multi-GB stage to the real SLURM host completes via rsync,
lands the same digest as the SFTP path would, resumes correctly after an induced
interruption, and a host without `rsync` produces a clear error rather than a
fallback.

---

## 5. The documentation, before and after

Do **not** edit `docs/source/introduction.rst` as part of this design — a
separate documentation sweep owns that file. For the record:

**In the meantime** the passage at `introduction.rst:115` is factually correct
about behaviour but wrong about intent, and should say so. Suggested:

> **It does not yet move your data.** Clustrix ships your *code and arguments*,
> not your dataset. If your function needs a 200 GB file, that file has to
> already be reachable from the worker today. The filesystem utilities help you
> inspect and locate remote data, but they are not yet a transfer service for
> bulk inputs. Staging declared inputs and outputs is planned — see issue #151.

**Once Phases 1-4 ship**, it should move out of the "what it is not" list
entirely and become a positive section:

> **It moves the data you declare.** `@cluster(inputs=[...], outputs=[...])`
> stages the files your function names, content-addressed and deduplicated, so
> re-running does not re-upload unchanged inputs and a file already on shared
> storage is not uploaded at all. It is not a sync tool: nothing moves that you
> did not name, and nothing comes back that you did not ask for.

The "not a workflow engine" and "not a low-latency dispatcher" bullets stay as
they are; §3.9 is what keeps them true.

---

## 6. The question the owner has to decide

**Is the stable path contract "the same relative path the file had locally"
(§3.1), or is it "an opaque staged path the function is handed"?**

Everything else in this design follows from that answer. The relative-path
contract is what makes a function run unmodified in both places, which is the
whole value proposition — but it means clustrix is asserting control over the
worker's working directory layout, it breaks for absolute-path inputs, and it
forces the symlink/hardlink/copy materialisation machinery in Phase 2. The
opaque-path contract is far simpler to implement and impossible to get wrong,
but every user rewrites their function to accept a path argument, at which point
they may reasonably ask what clustrix bought them over `scp`.

This design assumes the relative-path contract. If that is wrong, Phase 2 and
Phase 3 both shrink substantially and should be re-scoped before any code is
written.
