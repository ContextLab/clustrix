# Data packages (#151, round one) — what was built and why it differs from the issue body

Written 2026-08-19. Read this before re-deriving anything from `notes/data-mover-design.md`
or the #151 issue body: **both are superseded in several places** by @jeremymanning's comment on
#151 and by two follow-up clarifications he gave during implementation. Where they disagree, the
owner's words win, and they are quoted below so the next session does not have to guess.

## The three owner statements this implementation follows

**1. HuggingFace buckets instead of SFTP staging** (comment on #151):

> I'm imagining a multi-step process:
> 1. Prepare a package: configure HF credentials using the cluster config machinery and then provide
> a function for feeding in data or file paths and returning a new object that can be passed to
> cluster-decorated functions (either pass one, or pass a list of such objects). the objects should
> include a local path (where the file(s) exist locally) along with a private remote path linking to
> the HF bucket/path with the dataset(s). OR if the dataset is small (say, less than some default
> (configurable) threshold, or if the data package machinery is called with a force_local flag set
> to True) just package the dataset inside of that same object.
> 2. then, when the cluster command is called, the object needs to be sent (via rcp, scp, or
> similar) to the configured cluster, and then referenced as needed to access the required data on
> demand.

**2. Streaming is deferred** (same comment) — filed as **#155**:

> this is likely more complex than should be attempted in this initial "support data sharing"
> implementation. if so, we should open a new issue to address the streaming functionality
> separately, and defer this part of this issue accordingly.

**3. Deletion is explicit, manual, and never automatic**:

> there should also be a way to *delete* data (clean it up) via the wrapped object

> the deletion is handled *from the data object*, which is created *outside* of the cluster call.
> data is never automatically cleaned up. so the collision case isn't necessary to handle. if the
> dataset needs to remain, just pickle the data object, save locally, and load it later as needed.
> only clean it up if and only if it's not needed any more -- which is a determination to be made by
> the user, not automatically.

## What that changes relative to the issue body

| Issue body says | Built instead | Why |
|-|-|-|
| SFTP staging into `_stage/` on the cluster | Private HF dataset repo | Owner's comment. Also fixes the up/down asymmetry: one store both ends reach. |
| `cluster_put`/`cluster_get` in `filesystem.py` | Neither; `clustrix/staging.py` | `filesystem.py` inspects remote paths; this moves bytes. Also avoided a live collision with concurrent work in that file. |
| `@cluster(inputs=[...], outputs=[...])` | A `DataPackage` passed as an ordinary argument | Owner's design. Needs **zero** decorator changes: the object rides in `function_data.pkl` over the transport that already ships the payload. |
| Content-addressed `_stage/<digest>` + refcounted manifest | One folder per package, keyed by a fresh uuid | Owner ruled the collision case out of scope. Cost: identical content packaged twice is stored twice. |
| Phase 4 reaper, `stage_cache_ttl_days`, `stage_cache_max_bytes`, eviction at submission | **Dead. Not built. Do not build.** | "data is never automatically cleaned up." |
| Section 6's open question (relative vs opaque paths) | Relative paths, but *inside the package*, not the worker CWD | `pkg.path("data/x.csv")` and `pkg.materialize()` preserve relative layout without asserting control over the worker's working directory. Sidesteps the question rather than answering it. |

**Kept from the issue body, deliberately:** explicit declaration and never inference; the three size
bands; digest verification; atomic `.partial`+rename; path confinement with escapes rejected not
clamped; refusal of credential-shaped paths; `0600`/`0700` modes; the whole out-of-scope list (no
sync/mirror/watch, no DAG, no cluster-to-cluster, no S3/GCS in the transfer path, no compression, no
provenance DB, no inference of inputs from source in any phase).

## Trust direction — the property that makes this safe

Digests are computed locally from the user's own files and travel to the worker inside the function
payload, which is a **local-origin, upload-only** artifact. Bytes fetched back out of the remote
store are checked against those digests. The expected digest therefore never passes through the
store, so a tampered store is detectable. This is why staged data does **not** need the HMAC that
`result.pkl` needs: clustrix never deserializes a staged file, it writes it to disk and hands over a
path.

## Public API

`clustrix.data_package(source, *, name, base, config, force_local, allow_sensitive, filename)`
→ `DataPackage`, with `.path()`, `.read_bytes()`, `.materialize()`, `.filenames()`, `.total_bytes`,
`.is_inline`, `.exists()`, `.delete()`. Plus `clustrix.list_data_packages()`,
`clustrix.delete_data_package(package_id)`, `clustrix.materialize_packages(obj)`.

Config fields added to `ClusterConfig`: `hf_data_repo`, `stage_inline_max_bytes` (1 MB),
`stage_warn_bytes` (100 MB), `stage_max_bytes` (5 GB).

`DataPackage` holds only plain data — no HF client, no socket, no file handle, **no credential** —
so `pickle.dump`/`pickle.load` in a fresh interpreter yields an object that still resolves and still
deletes. That is the documented persistence story, and it is tested in a real subprocess.

## `file_packaging.py`

**Superseded, not adopted.** `FilePackager`/`PackageInfo`/`ExecutionContext` build a zip of
*source-inferred* dependencies (`_add_data_files` consumes `dependency_analysis.py`'s heuristic
`data_files`), which is exactly the inference this design rejects. It remains orphaned — zero
importers outside `__init__.py` and its tests — and is still covered by #122. It was left untouched
because removing it is #122's call, not this issue's.

`dependency_analysis.py:249` `_analyze_file_references` was read and left alone. Its third rule
classifies any string with a separator ending in one of 17 extensions as a data file, so it would
"find" `"s3://bucket/notes.log"`. Nothing in `staging.py` calls it.

## Verified against something real

- Real files on real disk; real `@cluster` execution via `local_executor`.
- Real paramiko SSH server (`tests/ssh_server.py`) — real socket, handshake, SFTP — driven through
  the shipped `ConnectionManager.upload_file`/`download_file`.
- Real HuggingFace: upload, download, digest verification, `exists`, `delete`, delete-twice, listing,
  and pickle-then-delete, against a real private `clustrix-data` dataset repo. Kilobytes only.
- Real subprocess for the pickle-survives-a-fresh-interpreter test.

## Not built this round

Reaper (dead by owner's decision), rsync backend, shared-filesystem elision, content-addressed
dedup, streaming (#155), outputs coming *back* from the worker. Outputs remain the asymmetric half
#10 objected to and #151 preserved: nothing comes back that was not asked for, and nothing has been
built to ask for it yet.
