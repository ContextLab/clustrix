"""Data packages: hand a ``@cluster`` function the data it needs.

Clustrix ships a function and its arguments. It has never shipped the *data*
those arguments point at, so a function that opens ``"data/subjects.h5"``
worked locally and failed on the worker. This module closes that gap in the
narrow, explicit way the design calls for.

The unit is a :class:`DataPackage`. You build one from data or from file paths,
you pass it to a ``@cluster``-decorated function as an ordinary argument, and
on the worker you dereference it to get local paths back::

    import clustrix

    subjects = clustrix.data_package("data/subjects.h5")

    @clustrix.cluster(cores=8)
    def fit(pkg):
        with open(pkg.path("subjects.h5"), "rb") as handle:
            ...

    fit(subjects)

Nothing is inferred. A file moves because it was named, never because it was
mentioned in the source -- ``dependency_analysis.py`` will happily classify the
string ``"s3://bucket/notes.log"`` as a data file, and silently uploading on
that basis is the worst failure mode available here.

Two places the bytes can live
-----------------------------

**Inline.** Below ``stage_inline_max_bytes`` (or with ``force_local=True``),
the file contents are carried inside the package object itself. The object is
pickled with the function's other arguments and travels over the transport that
already ships the payload -- SFTP for ``ssh``/``slurm``, the HF Jobs payload
channel for ``huggingface``. No second transport, no remote store, nothing to
clean up.

**A private HuggingFace repo.** Above that threshold the contents go to a
private dataset repo under the caller's namespace, and the package carries only
the coordinates plus a digest per file. This is the same store, and the same
credential path, that ``hf_jobs.py`` already uses for oversized payloads.

Two consequences of that worth knowing *before* it happens rather than after:

* **Clustrix will create a repo in your HuggingFace account.** The first
  package that does not fit inline calls ``create_repo(..., private=True,
  exist_ok=True)`` for ``<namespace>/clustrix-data``, where the namespace comes
  from ``hf_namespace``, then ``hf_username``, then whatever the token's
  ``whoami()`` reports. Set ``hf_data_repo`` to choose a different one.
* **Deleting packages never deletes the repo**, only the folders inside it. An
  account with every package removed still has an empty ``clustrix-data``
  dataset in it, which you can remove by hand. This is deliberate: a user who
  pointed ``hf_data_repo`` at a repo they own and care about would not thank us
  for removing it because the last package went away.

The trust direction is worth being explicit about. Digests are computed
*locally*, from the user's own files, and travel to the worker inside the
function payload -- which is a local-origin, upload-only artifact. Bytes fetched
back out of the remote store are checked against those digests. So a tampered
store is caught, because the expected digest never went through it.

Deletion
--------

A package owns the remote copy it created, and the handle that created it can
end it::

    pkg.delete()          # removes the remote copy; local files untouched
    clustrix.list_data_packages()          # what is in the store
    clustrix.delete_data_package("<id>")   # a way out without the object

**Nothing is ever cleaned up automatically.** There is no TTL, no reaper, no
eviction, and no deletion when a job finishes. ``cleanup_on_success`` governs
the job directory and does not touch staged data. Whether a dataset is still
needed is the user's call, and the only way it goes away is an explicit
:meth:`DataPackage.delete` or :func:`delete_data_package`. There is
deliberately no context-manager form, because a ``with`` block that quietly
deleted the upload on the way out would be exactly the automatic cleanup this
design rejects.

``delete`` never touches the files you packaged. It removes the package's
folder in the remote store and any copy clustrix itself materialised into its
own cache. An upload is a single atomic commit, so there is no half-finished
package to clean up in the first place. Deleting something that is already gone
is not an error; failing to delete something that is there raises.

The package object is the durable handle. It is plain data -- no client, no
socket, no credential -- so the way to keep a dataset across sessions is to
pickle the object and load it later::

    import pickle
    pickle.dump(pkg, open("subjects.pkl", "wb"))
    # ... a week later, a different interpreter ...
    pkg = pickle.load(open("subjects.pkl", "rb"))
    pkg.path("subjects.h5")   # still resolves
    pkg.delete()              # still deletes

Each package gets its own remote folder, keyed by a fresh identifier, so two
packages never share a stored blob and deleting one cannot pull data out from
under another. The cost is that identical content packaged twice is stored
twice. Content-addressed deduplication would need a refcounted manifest and is
deliberately not built.
"""

import fnmatch
import hashlib
import json
import logging
import os
import pickle
import re
import shutil
import stat as stat_module
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: Repo, under the caller's namespace, holding staged data packages.
DATA_REPO_NAME = "clustrix-data"

#: Prefix inside that repo. Kept distinct from ``hf_jobs.py``'s ``payloads/``
#: so the two features cannot delete each other's objects.
PACKAGE_PREFIX = "packages"

#: Written last, once every file is up. Its presence is what makes a package
#: complete; an interrupted upload leaves files but no manifest.
MANIFEST_NAME = "manifest.json"

#: Read in 1 MiB blocks so hashing a large file does not read it into memory.
_HASH_CHUNK = 1024 * 1024

#: Paths that are almost certainly a credential rather than a dataset. Matched
#: against the file name and against the full path, case-insensitively. An
#: explicit ``allow_sensitive=True`` overrides, because a legitimate use --
#: staging a keypair you deliberately want on the worker -- does exist.
SENSITIVE_PATTERNS: Tuple[str, ...] = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keytab",
    "*.ppk",
    ".env",
    ".env.*",
    ".netrc",
    "_netrc",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials",
    "credentials.*",
    ".git-credentials",
    "kubeconfig",
    "*.kubeconfig",
    ".npmrc",
    ".pypirc",
    ".htpasswd",
    ".dockercfg",
    "secrets",
    "secrets.*",
    "*.kdbx",
    "*/.ssh/*",
    "*/.aws/*",
    "*/.gnupg/*",
    "*/.kube/*",
    "*/.docker/*",
    "*/.config/gcloud/*",
    # A git config carries the remote URL, and a remote URL is one of the
    # commonest places a personal access token ends up on disk.
    "*/.git/config",
)

#: A package id is a ``uuid4().hex`` and nothing else. Anything that is not one
#: is refused before it can reach a delete call: the id becomes a path in the
#: remote store, so ``".."`` traverses out of the package prefix and ``""``
#: addresses the prefix itself -- which is every package at once.
_PACKAGE_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")


class StagingError(RuntimeError):
    """Anything that stops a package being built, moved, or removed."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _digest_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def _digest_file(path: Union[str, Path]) -> str:
    hasher = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as handle:
        while True:
            block = handle.read(_HASH_CHUNK)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def _is_sensitive(path: Path) -> bool:
    """Whether a path looks like a credential rather than data."""
    name = path.name.lower()
    full = str(path).lower().replace(os.sep, "/")
    for pattern in SENSITIVE_PATTERNS:
        if "/" in pattern:
            if fnmatch.fnmatch(full, pattern):
                return True
        elif fnmatch.fnmatch(name, pattern):
            return True
    return False


def _validate_package_id(package_id: Any) -> str:
    """Return ``package_id`` if it is one, or refuse it.

    A package id is opaque: ``uuid.uuid4().hex``, thirty-two lowercase hex
    digits. It is also a path component in the remote store, which is why
    anything else has to be refused *before* it reaches a delete call.
    ``""`` addresses the whole ``packages/`` prefix -- deleting every package
    in the account -- and ``"../README.md"`` addresses a file outside it.
    Neither is a typo we can guess the intent of.
    """
    if not isinstance(package_id, str) or not _PACKAGE_ID_RE.match(package_id):
        raise StagingError(
            f"{package_id!r} is not a data package id. An id is the 32-character "
            "hex string on DataPackage.package_id, which list_data_packages() "
            "also reports. Nothing was deleted."
        )
    return package_id


def _validate_path_in_repo(path_in_repo: Any) -> str:
    """Return a package's remote folder if it is one, or refuse it.

    The same argument as :func:`_validate_package_id`, one level up: this
    string is handed to ``delete_folder``, so it decides what gets removed.
    """
    if isinstance(path_in_repo, str):
        head, _, tail = path_in_repo.partition("/")
        if head == PACKAGE_PREFIX and _PACKAGE_ID_RE.match(tail):
            return path_in_repo
    raise StagingError(
        f"{path_in_repo!r} is not a data package location. It must be "
        f"'{PACKAGE_PREFIX}/<package id>'. Nothing was deleted."
    )


def _kind_of(mode: int) -> str:
    """A human name for a stat mode, for messages that refuse a file."""
    for predicate, label in (
        (stat_module.S_ISFIFO, "named pipe"),
        (stat_module.S_ISCHR, "character device"),
        (stat_module.S_ISBLK, "block device"),
        (stat_module.S_ISSOCK, "socket"),
        (stat_module.S_ISDIR, "directory"),
        (stat_module.S_ISREG, "regular file"),
    ):
        if predicate(mode):
            return label
    return "special file"


def _stat_followed(path: Path, label: str) -> os.stat_result:
    """``os.stat`` -- following symlinks -- with failures named, not raw.

    A dangling symlink and an unreadable parent both arrive here as ``OSError``
    and both need to say which file and which package, not surface an errno
    from four frames down.
    """
    try:
        return os.stat(path)
    except OSError as exc:
        raise StagingError(f"Cannot stage {label}: {exc}.") from exc


def _require_stageable(path: Path, label: str, allow_dir: bool) -> os.stat_result:
    """Refuse anything that is not a regular file (or, optionally, a directory).

    Reading a fifo or ``/dev/zero`` does not fail, it *blocks* -- packaging one
    hangs with no output and no timeout, which is the least debuggable failure
    in this module. A refusal costs the caller one message.
    """
    info = _stat_followed(path, label)
    if stat_module.S_ISREG(info.st_mode):
        return info
    if allow_dir and stat_module.S_ISDIR(info.st_mode):
        return info
    raise StagingError(
        f"Refusing to stage {label}: it is a {_kind_of(info.st_mode)}, not a "
        "regular file"
        + (" or directory" if allow_dir else "")
        + ". Reading one can block forever, so clustrix refuses it rather "
        "than hanging."
    )


def _read_verified(path: Path, entry: "PackagedFile", package_name: str) -> bytes:
    """Read a source file and check it is still what was hashed.

    Hashing and reading are two passes over the same file, and a file that
    grew between them yields a payload that does not match its own recorded
    size and digest. That mismatch is real and is caught eventually -- on the
    worker, hours later, where nobody can see the writer that caused it. Catch
    it here instead.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StagingError(
            f"Could not read {path} for data package {package_name!r}: {exc}."
        ) from exc
    if len(data) != entry.size or _digest_bytes(data) != entry.digest:
        raise StagingError(
            f"{path} changed while data package {package_name!r} was being "
            f"built: {entry.size} bytes when it was hashed, {len(data)} bytes "
            "when it was read. Nothing has been staged. Package it again once "
            "it has stopped changing."
        )
    return data


def _safe_relpath(relpath: str) -> PurePosixPath:
    """Validate a package-relative path, or refuse it.

    A package's file names decide where bytes land when it is materialised. A
    name of ``../../.ssh/authorized_keys`` would land them outside the
    destination directory -- the Zip-Slip class of bug. Escapes are rejected,
    not clamped: a caller who wrote one meant something we are not going to
    guess at.
    """
    if not relpath or relpath in (".", ".."):
        raise StagingError(f"Invalid path in data package: {relpath!r}")
    normalised = relpath.replace(os.sep, "/")
    pure = PurePosixPath(normalised)
    if pure.is_absolute() or normalised.startswith("/"):
        raise StagingError(
            f"Data package paths must be relative, got {relpath!r}. "
            "Use the 'base' argument to choose what they are relative to."
        )
    if any(part == ".." for part in pure.parts):
        raise StagingError(
            f"Data package path {relpath!r} escapes the package root. "
            "Paths containing '..' are rejected, not clamped."
        )
    return pure


def _confine(root: Path, relpath: str) -> Path:
    """Resolve ``relpath`` under ``root``, refusing anything that escapes it.

    ``_safe_relpath`` rejects the obvious escapes syntactically; this is the
    second check, after resolution, which is what catches a symlink in the
    destination pointing somewhere else.
    """
    pure = _safe_relpath(relpath)
    root_resolved = root.resolve()
    target = (root_resolved / pure).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise StagingError(
            f"Refusing to write {relpath!r}: it resolves outside {root_resolved}."
        )
    return target


def _atomic_write(target: Path, data: bytes, mode: int = 0o600) -> None:
    """Write ``data`` to ``target`` via a ``.partial`` file and a rename.

    An interrupted write leaves a ``.partial`` behind and never a truncated
    file at the real name, so a reader either sees the whole thing or nothing.
    """
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = target.with_name(target.name + ".partial")
    try:
        fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(partial), str(target))
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def _format_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


# ---------------------------------------------------------------------------
# HuggingFace access -- one credential path, shared with hf_jobs.py
# ---------------------------------------------------------------------------


def _hf_token(config) -> str:
    """Resolve a HuggingFace token, or say exactly how to supply one.

    Same order, and the same CLI-cache reader, as ``hf_jobs.py``: there is one
    credential path for HuggingFace in this project, not two.
    """
    from clustrix.hf_jobs import _token_from_hf_cli_cache

    token = getattr(config, "hf_token", None) or os.environ.get("HF_TOKEN")
    if not token:
        token = _token_from_hf_cli_cache()
    if not token:
        raise StagingError(
            "No HuggingFace token configured, so a data package cannot be "
            "staged or dereferenced. Set hf_token in your clustrix config, "
            "export HF_TOKEN, or run `hf auth login`. On a worker, the token "
            "must be present in the job environment -- clustrix deliberately "
            "does not pickle your token into the job payload."
        )
    return str(token)


def _hf_api(config):
    """An authenticated ``HfApi``."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise StagingError(
            "huggingface_hub is not installed, so data packages cannot use "
            "the remote store. Install it with `pip install huggingface_hub`, "
            "or build packages with force_local=True."
        ) from exc
    return HfApi(token=_hf_token(config))


def _hf_repo(config) -> str:
    """The private repo data packages live in."""
    configured = getattr(config, "hf_data_repo", None)
    if configured:
        return str(configured)
    namespace = getattr(config, "hf_namespace", None) or getattr(
        config, "hf_username", None
    )
    if not namespace:
        try:
            namespace = _hf_api(config).whoami().get("name")
        except StagingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StagingError(
                "Could not determine a HuggingFace namespace for the data "
                f"repo: {exc}. Set hf_namespace or hf_data_repo in your config."
            ) from exc
    if not namespace:
        raise StagingError(
            "Could not determine a HuggingFace namespace for the data repo. "
            "Set hf_namespace or hf_data_repo in your config."
        )
    return f"{namespace}/{DATA_REPO_NAME}"


# ---------------------------------------------------------------------------
# the package
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackagedFile:
    """One file in a package: where it sits inside it, how big, and its digest."""

    relpath: str
    size: int
    digest: str

    def as_dict(self) -> Dict[str, Any]:
        return {"relpath": self.relpath, "size": self.size, "digest": self.digest}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PackagedFile":
        return cls(
            relpath=str(raw["relpath"]),
            size=int(raw["size"]),
            digest=str(raw["digest"]),
        )


@dataclass
class DataPackage:
    """A named bundle of files that can travel to a worker and be read there.

    Build one with :func:`data_package`; the constructor here is the low-level
    form and does no staging of its own.

    Attributes:
        name: A label, used in messages and in the materialisation directory.
        package_id: Unique per package. Also the folder name in the remote
            store, which is what keeps two packages from sharing a blob.
        files: One :class:`PackagedFile` per file, in declaration order.
        local_root: Where the files are on the machine that built the package.
            ``None`` for a package built from in-memory data. Used as a
            zero-cost source when the same machine dereferences the package.
        repo_id / path_in_repo: The private remote location, or ``None`` for
            an inline package.
        inline: ``relpath -> bytes`` when the payload rides inside the object.

    Every attribute is plain data -- strings, ints, bytes. No HF client, no
    socket, no file handle, so the object pickles cleanly and a copy loaded in
    a fresh interpreter still reaches the same remote data. Credentials are
    deliberately *not* among those attributes: a saved package must not be a
    token sitting on disk, so it re-authenticates from the ordinary config path
    whenever it is used.
    """

    name: str
    package_id: str
    files: Tuple[PackagedFile, ...]
    local_root: Optional[str] = None
    repo_id: Optional[str] = None
    path_in_repo: Optional[str] = None
    inline: Optional[Dict[str, bytes]] = None
    _materialised: Optional[str] = field(default=None, repr=False, compare=False)

    # -- description ----------------------------------------------------

    @property
    def is_inline(self) -> bool:
        """Whether the bytes ride inside this object rather than in the store."""
        return self.inline is not None

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files)

    def filenames(self) -> List[str]:
        """The package-relative paths, in declaration order."""
        return [entry.relpath for entry in self.files]

    def _entry(self, relpath: Optional[str]) -> PackagedFile:
        if relpath is None:
            if len(self.files) != 1:
                raise StagingError(
                    f"Data package {self.name!r} holds {len(self.files)} files, "
                    "so path() needs one of them by name: "
                    f"{', '.join(self.filenames())}"
                )
            return self.files[0]
        wanted = str(relpath).replace(os.sep, "/")
        for entry in self.files:
            if entry.relpath == wanted:
                return entry
        raise StagingError(
            f"{relpath!r} is not in data package {self.name!r}. It holds: "
            f"{', '.join(self.filenames())}"
        )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        where = "inline" if self.is_inline else f"{self.repo_id}/{self.path_in_repo}"
        return (
            f"DataPackage(name={self.name!r}, files={len(self.files)}, "
            f"bytes={self.total_bytes}, at={where})"
        )

    # -- dereferencing --------------------------------------------------

    def path(self, relpath: Optional[str] = None, config=None) -> str:
        """Local filesystem path to one file, fetching it if it is not here yet.

        Called with no argument on a single-file package. This is the "on
        demand" half: nothing is fetched until something asks for it, and a
        package dereferenced on the machine that built it reads the original
        files without copying them.
        """
        entry = self._entry(relpath)
        local = self._local_source(entry)
        if local is not None:
            return str(local)
        root = Path(self.materialize(config=config))
        return str(root / entry.relpath)

    def read_bytes(self, relpath: Optional[str] = None, config=None) -> bytes:
        """The contents of one file, verified against its recorded digest."""
        entry = self._entry(relpath)
        if self.inline is not None:
            data = self.inline[entry.relpath]
        else:
            with open(self.path(entry.relpath, config=config), "rb") as handle:
                data = handle.read()
        actual = _digest_bytes(data)
        if actual != entry.digest:
            raise StagingError(
                f"Digest mismatch reading {entry.relpath!r} from data package "
                f"{self.name!r}: expected {entry.digest}, got {actual}."
            )
        return data

    def _local_source(self, entry: PackagedFile) -> Optional[Path]:
        """The original file, if this machine still has exactly what we packaged.

        The shortcut is only sound if the file at ``local_root`` *is* the
        packaged content, and the only thing that establishes that is the
        digest. This used to compare sizes, which is not the same claim at all:
        a file edited in place at identical size passed it, and so did a
        completely unrelated file that happened to be the same size at the same
        absolute path on another machine -- the shared-home cluster case, where
        ``local_root`` exists on the worker and holds something else. Both made
        ``path()`` serve bytes that ``read_bytes()`` would have rejected, so
        one package gave two answers.

        Nothing weaker closes this. ``(size, mtime_ns)`` is restorable with
        ``os.utime``; an inode number means nothing across machines; a
        recorded hostname would not catch the same-machine in-place edit. So:
        hash it. A mismatch is not an error -- it means this machine does not
        have the file after all, and the bytes come from the package instead,
        which is the same answer every other machine gives.

        The cost is a hash of the file per dereference. That is the price of
        ``path()`` and ``read_bytes()`` agreeing; call ``materialize()`` once
        and use the returned root if you are reading in a loop.
        """
        if self.local_root is None:
            return None
        candidate = Path(self.local_root) / entry.relpath
        try:
            if not candidate.is_file() or candidate.stat().st_size != entry.size:
                # Cheap pre-filter only. A different size implies a different
                # digest, so this changes no answer -- it just skips the hash.
                return None
            if _digest_file(candidate) != entry.digest:
                return None
        except OSError:
            return None
        return candidate

    def materialize(self, dest: Optional[str] = None, config=None) -> str:
        """Put every file on local disk and return the directory holding them.

        Files keep the relative paths they had when the package was built, so a
        function that opened ``"data/x.csv"`` opens ``"data/x.csv"`` under the
        returned root on either machine.
        """
        if dest is None:
            if self._materialised is not None and Path(self._materialised).is_dir():
                return self._materialised
            dest = str(self._default_dest(config))
        root = Path(dest)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

        for entry in self.files:
            target = _confine(root, entry.relpath)
            if target.is_file() and target.stat().st_size == entry.size:
                if _digest_file(target) == entry.digest:
                    continue
            _atomic_write(target, self._fetch(entry, config))

        self._materialised = str(root)
        return str(root)

    def _default_dest(self, config) -> Path:
        cache = getattr(config or _config(), "local_cache_dir", "~/.clustrix/cache")
        base = Path(os.path.expanduser(str(cache))) / "data-packages"
        return base / self.package_id

    def _fetch(self, entry: PackagedFile, config) -> bytes:
        """The bytes of one file, from wherever they are, digest-verified."""
        if self.inline is not None:
            data = self.inline[entry.relpath]
        else:
            local = self._local_source(entry)
            if local is not None:
                data = local.read_bytes()
            else:
                data = self._fetch_remote(entry, config)
        actual = _digest_bytes(data)
        if actual != entry.digest:
            raise StagingError(
                f"Digest mismatch for {entry.relpath!r} in data package "
                f"{self.name!r}: expected {entry.digest}, got {actual}. "
                "The stored copy does not match what was packaged; it is not "
                "being written to disk."
            )
        return data

    def _fetch_remote(self, entry: PackagedFile, config) -> bytes:
        if not self.repo_id or not self.path_in_repo:
            raise StagingError(
                f"Data package {self.name!r} has neither inline contents nor a "
                "remote location, so it cannot be dereferenced."
            )
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise StagingError(
                "huggingface_hub is not installed on this machine, so the "
                f"data package {self.name!r} cannot be fetched."
            ) from exc
        cfg = config if config is not None else _config()
        cached = hf_hub_download(
            repo_id=self.repo_id,
            filename=f"{self.path_in_repo}/files/{entry.relpath}",
            repo_type="dataset",
            token=_hf_token(cfg),
        )
        with open(cached, "rb") as handle:
            return handle.read()

    # -- lifecycle ------------------------------------------------------

    def exists(self, config=None) -> bool:
        """Whether the remote copy is still there. Inline packages: always."""
        if self.is_inline:
            return True
        cfg = config if config is not None else _config()
        api = _hf_api(cfg)
        try:
            return bool(
                api.file_exists(
                    repo_id=self.repo_id,
                    filename=f"{self.path_in_repo}/{MANIFEST_NAME}",
                    repo_type="dataset",
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise StagingError(
                f"Could not check whether data package {self.name!r} still "
                f"exists in {self.repo_id}: {exc}"
            ) from exc

    def __getstate__(self) -> Dict[str, Any]:
        """Pickle without the materialisation path.

        Where this package was last unpacked is true of one machine at one
        moment. Carrying it into a pickle means a worker -- or this machine a
        week later -- can find a stale directory at that path and reuse it.
        Everything else here is durable; this one field is not.
        """
        state = dict(self.__dict__)
        state["_materialised"] = None
        return state

    def delete(self, config=None) -> bool:
        """Remove the remote copy and any clustrix-owned local cache of it.

        Returns whether anything was actually removed remotely. Calling this
        twice, or on a package that was already cleaned up elsewhere, is not an
        error -- but a remote copy that is present and cannot be deleted raises,
        because a warning here would leave the caller paying for storage they
        believe they released.

        Nothing calls this for you. There is no TTL and no reaper: whether a
        dataset is still needed is the user's judgement, not clustrix's.

        **The files you packaged are never touched.** ``local_root`` points at
        the user's own data; deleting that because a transfer was cleaned up
        would be indefensible. Only the remote folder, and the copy clustrix
        wrote into its own cache, are removed.

        **A directory you named yourself is never touched either.** If you
        called ``materialize(dest=...)``, that directory is yours -- it may
        hold anything, and clustrix has no way to know what it created there
        versus what was already in it. It used to be removed recursively,
        which deleted whatever else the caller kept alongside the data. Clear
        it yourself if you want it gone.

        **The repo itself is never touched either**, only the package's folder
        inside it. ``hf_data_repo`` may well point at a repo the user owns and
        cares about. An account whose last package is deleted keeps an empty
        ``clustrix-data`` dataset, which is theirs to remove.
        """
        cfg = config if config is not None else _config()
        self._discard_local_cache(cfg)
        if self.is_inline or not self.repo_id or not self.path_in_repo:
            return False

        path_in_repo = _validate_path_in_repo(self.path_in_repo)
        api = _hf_api(cfg)
        try:
            api.delete_folder(
                path_in_repo=path_in_repo,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"clustrix: delete data package {self.name}",
            )
        except Exception as exc:  # noqa: BLE001
            if _is_missing(exc):
                logger.debug(
                    "Data package %s was already gone from %s",
                    self.package_id,
                    self.repo_id,
                )
                return False
            raise StagingError(
                f"Could not delete data package {self.name!r} from "
                f"{self.repo_id}/{self.path_in_repo}: {exc}"
            ) from exc
        logger.info(
            "Deleted data package %s from %s/%s",
            self.name,
            self.repo_id,
            self.path_in_repo,
        )
        return True

    def _discard_local_cache(self, config=None) -> None:
        """Remove the cache directory clustrix created, and nothing else.

        Exactly one directory qualifies: ``<local_cache_dir>/data-packages/
        <package_id>``. Clustrix creates it, it is keyed by an id nothing else
        uses, and nothing else can be in it.

        A ``dest`` the caller passed to :meth:`materialize` does **not**
        qualify, however recently this package was unpacked into it. This used
        to recurse into ``self._materialised`` and delete whatever was there:
        ``materialize(dest="~/myproject")`` followed by ``delete()`` removed
        the project. Recording which files clustrix wrote would not rescue the
        idea either -- ``materialize`` skips a file that is already present and
        correct, so "clustrix wrote it" and "clustrix should remove it" are not
        the same set. The safe direction is to leave the caller's directory
        alone.
        """
        cache = self._default_dest(config)
        if self.local_root and cache.resolve() == Path(self.local_root).resolve():
            # Only reachable if a caller aimed local_cache_dir at their own
            # data. Their files win over our cache.
            self._materialised = None
            return
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
        self._materialised = None


def _is_rate_limited(exc: Exception) -> bool:
    """Whether a hub error is "too many commits this hour"."""
    return getattr(getattr(exc, "response", None), "status_code", None) == 429


def _is_missing(exc: Exception) -> bool:
    """Whether a hub error means "already gone" rather than "failed"."""
    try:
        from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError

        if isinstance(exc, (EntryNotFoundError, RepositoryNotFoundError)):
            return True
    except ImportError:  # pragma: no cover - depends on extras
        pass
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 404


# ---------------------------------------------------------------------------
# building a package
# ---------------------------------------------------------------------------


def _config():
    from clustrix.config import get_config

    return get_config()


def _collect(
    source: Union[str, Path, Sequence[Union[str, Path]]],
    base: Optional[Union[str, Path]],
    allow_sensitive: bool,
) -> Tuple[List[Tuple[str, Path]], Optional[Path]]:
    """Expand the caller's declaration into ``(relpath, absolute path)`` pairs.

    Directories expand to the files under them; a list may mix files and
    directories. The common ancestor of everything named becomes the package
    root unless ``base`` says otherwise, which is what preserves the relative
    paths the function already uses.
    """
    if isinstance(source, (str, Path)):
        items: List[Path] = [Path(source)]
    else:
        items = [Path(item) for item in source]
    if not items:
        raise StagingError("A data package needs at least one file.")

    resolved: List[Path] = []
    for item in items:
        path = _named_path(item)
        _require_stageable(path, str(item), allow_dir=True)
        resolved.append(path)

    if base is not None:
        root: Optional[Path] = Path(os.path.expanduser(str(base))).resolve()
    elif len(resolved) == 1 and resolved[0].is_dir():
        root = resolved[0]
    else:
        parents = [p if p.is_dir() else p.parent for p in resolved]
        common = os.path.commonpath([str(p) for p in parents])
        root = Path(common)

    collected: List[Tuple[str, Path]] = []
    for path in resolved:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                # rglob does not descend into symlinked directories, so a
                # symlink loop cannot hang this walk.
                if child.is_symlink():
                    # Included, at the *link's* own place in the tree, with the
                    # target's bytes -- what `cp -L` does. Dropping it silently
                    # gave the worker a different tree than the one named, and
                    # a function that opened it got FileNotFoundError from a
                    # package that reported success.
                    _require_stageable(child, str(child), allow_dir=False)
                    collected.append((_relative(child, root), child))
                    continue
                info = child.lstat()
                if stat_module.S_ISDIR(info.st_mode):
                    continue
                if not stat_module.S_ISREG(info.st_mode):
                    raise StagingError(
                        f"Refusing to stage {child}: it is a "
                        f"{_kind_of(info.st_mode)}, not a regular file. "
                        "Reading one can block forever, so clustrix refuses "
                        "the whole package rather than hanging on it."
                    )
                collected.append((_relative(child, root), child))
        else:
            collected.append((_relative(path, root), path))

    if not collected:
        raise StagingError(f"No files found under {source!r}.")

    if not allow_sensitive:
        offenders = [str(p) for _, p in collected if _is_sensitive_target(p)]
        if offenders:
            raise StagingError(
                "Refusing to stage what looks like a credential rather than "
                "data: " + ", ".join(sorted(offenders)[:5]) + ". Pass "
                "allow_sensitive=True if you really mean to move these."
            )

    return _dedupe(collected), root


def _named_path(item: Union[str, Path]) -> Path:
    """The absolute path of something the caller named, link and all.

    Everything above the last component is resolved, so ``..`` and a symlinked
    parent normalise the way they must for confinement checks. The last
    component is *not*, because that is the thing the caller named. Resolving
    it made an explicitly named symlink take its target's identity: naming
    ``data/link.csv`` produced a package holding ``other/z.csv``, and dragged
    the package root out to the target's directory along with it. A file moves
    because it was named; it keeps the name it was given.
    """
    absolute = Path(os.path.abspath(os.path.expanduser(str(item))))
    if absolute.name in ("", ".", ".."):  # pragma: no cover - "/" and friends
        return Path(os.path.realpath(str(absolute)))
    return absolute.parent.resolve() / absolute.name


def _is_sensitive_target(path: Path) -> bool:
    """Credential check that a symlink cannot route around.

    ``data/notes`` -> ``~/.ssh/id_rsa`` is credential-shaped at the far end and
    innocuous at the near one, so both ends are tested.
    """
    if _is_sensitive(path):
        return True
    if path.is_symlink():
        return _is_sensitive(Path(os.path.realpath(str(path))))
    return False


def _dedupe(collected: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    """Collapse repeats and refuse genuine collisions.

    Naming the same file twice -- ``[dir, dir/file]`` -- is harmless, so it
    collapses. Two *different* files landing on one name is not: one would
    silently overwrite the other on the worker, and the run would produce a
    wrong answer rather than an error.
    """
    seen: Dict[str, Path] = {}
    deduped: List[Tuple[str, Path]] = []
    for relpath, path in collected:
        _safe_relpath(relpath)
        previous = seen.get(relpath)
        if previous is not None:
            if previous == path:
                continue
            raise StagingError(
                f"Two different files in this package would both be at "
                f"{relpath!r}: {previous} and {path}. Pass an explicit 'base', "
                "or package them separately."
            )
        seen[relpath] = path
        deduped.append((relpath, path))
    return deduped


def _relative(path: Path, root: Optional[Path]) -> str:
    """Where ``path`` sits inside the package.

    A file outside ``base`` is an error rather than a silent fall back to its
    bare name: the fall back is how two unrelated files quietly collide on one
    name, and the caller who passed ``base`` had something specific in mind.
    """
    if root is None:
        return path.name
    try:
        return str(path.relative_to(root)).replace(os.sep, "/")
    except ValueError:
        raise StagingError(
            f"{path} is not under base={root}, so it has no place in this "
            "package. Choose a base that contains every file, or leave base "
            "unset to use their common ancestor."
        ) from None


def _check_size(total: int, config, biggest: Optional[Tuple[str, int]]) -> None:
    """Three bands: quiet, warn, refuse.

    A refusal beats a silent multi-hour transfer that looks like a hang, so the
    top band raises and names the file, the threshold, the config key, and what
    to do instead.
    """
    warn_at = int(getattr(config, "stage_warn_bytes", 100 * 1024 * 1024))
    max_at = int(getattr(config, "stage_max_bytes", 5 * 1024 * 1024 * 1024))
    if total >= max_at:
        name, size = biggest or ("<package>", total)
        raise StagingError(
            f"Refusing to stage {_format_bytes(total)} "
            f"(largest file {name}, {_format_bytes(size)}): at or above "
            f"stage_max_bytes ({_format_bytes(max_at)}). Either put the data "
            "on storage the worker can already reach, or raise "
            "stage_max_bytes in your clustrix config if you really want this "
            "moved over the network."
        )
    if total >= warn_at:
        logger.warning(
            "Staging %s to the remote store; this is above stage_warn_bytes "
            "(%s) and may take a while.",
            _format_bytes(total),
            _format_bytes(warn_at),
        )


def data_package(
    source: Union[str, Path, bytes, Sequence[Union[str, Path]]],
    *,
    name: Optional[str] = None,
    base: Optional[Union[str, Path]] = None,
    config=None,
    force_local: bool = False,
    allow_sensitive: bool = False,
    filename: str = "data.bin",
) -> DataPackage:
    """Package data or files into an object a ``@cluster`` function can read.

    Args:
        source: A path, a list of paths, or raw ``bytes``. Directories expand
            to the files beneath them.
        name: Label for messages; defaults to the source's basename.
        base: What the package's relative paths are relative to. Defaults to
            the common ancestor of everything named, which is what lets a
            function keep using the paths it already uses.
        config: A ``ClusterConfig``; the global one by default.
        force_local: Carry the contents inside the object regardless of size.
            Nothing is uploaded and there is nothing to clean up.
        allow_sensitive: Permit paths that look like credentials.
        filename: The name raw ``bytes`` get inside the package.

    Returns:
        A :class:`DataPackage`. Pass it -- or a list of them -- to a
        ``@cluster``-decorated function as an ordinary argument.

    Raises:
        StagingError: on a missing file, a credential-shaped path, a path that
            escapes the package root, or a package at or above
            ``stage_max_bytes``.
    """
    cfg = config if config is not None else _config()

    if isinstance(source, (bytes, bytearray)):
        payload = bytes(source)
        relpath = str(_safe_relpath(filename))
        entries: Tuple[PackagedFile, ...] = (
            PackagedFile(
                relpath=relpath, size=len(payload), digest=_digest_bytes(payload)
            ),
        )
        _check_size(len(payload), cfg, (relpath, len(payload)))
        package = DataPackage(
            name=name or relpath,
            package_id=uuid.uuid4().hex,
            files=entries,
            local_root=None,
        )
        contents = {relpath: payload}
        if not _inline_if_it_fits(package, contents, len(payload), cfg, force_local):
            _upload(package, contents, cfg)
        return package

    collected, root = _collect(source, base, allow_sensitive)
    label = name or (
        Path(str(source)).name if isinstance(source, (str, Path)) else "data"
    )
    entries = tuple(_describe(relpath, path, label) for relpath, path in collected)
    total = sum(entry.size for entry in entries)
    biggest = max(((e.relpath, e.size) for e in entries), key=lambda pair: pair[1])
    _check_size(total, cfg, biggest)

    package = DataPackage(
        name=label,
        package_id=uuid.uuid4().hex,
        files=entries,
        local_root=str(root) if root else None,
    )

    if force_local or total < _inline_limit(cfg):
        by_relpath = {entry.relpath: entry for entry in package.files}
        contents = {
            relpath: _read_verified(path, by_relpath[relpath], label)
            for relpath, path in collected
        }
        if _inline_if_it_fits(package, contents, total, cfg, force_local):
            return package
        del contents

    _upload(package, {relpath: path for relpath, path in collected}, cfg)
    return package


def _describe(relpath: str, path: Path, package_name: str) -> PackagedFile:
    """Size and digest of one source file, with failures named.

    A file that is missing or unreadable at packaging time used to surface as a
    bare ``FileNotFoundError`` or ``PermissionError`` from inside a generator
    expression, which says nothing about which package was being built or that
    building it is what failed.
    """
    try:
        return PackagedFile(
            relpath=relpath, size=path.stat().st_size, digest=_digest_file(path)
        )
    except OSError as exc:
        raise StagingError(
            f"Could not read {path} while building data package "
            f"{package_name!r}: {exc}. Every named file has to be readable "
            "now; one that is not would fail on the worker instead, where it "
            "is far harder to diagnose."
        ) from exc


def _inline_if_it_fits(
    package: DataPackage,
    contents: Dict[str, bytes],
    total: int,
    config,
    force_local: bool,
) -> bool:
    """Attach ``contents`` to the package inline, if that is within the limit.

    ``force_local`` is the caller saying "inline it regardless", and it does.

    Otherwise the threshold is measured against the **serialized package** --
    the bytes that actually ride inside the job payload -- and not against the
    sum of the file sizes. Those are nowhere near each other for many small
    files: ten thousand four-byte files are forty kilobytes of data and a
    1.09 MB pickle, because every file also carries a relative path and a
    64-character digest. Measured on the data alone, a package a megabyte over
    ``stage_inline_max_bytes`` called itself inline.
    """
    if force_local:
        package.inline = contents
        return True
    limit = _inline_limit(config)
    if total >= limit:
        return False
    package.inline = contents
    if len(pickle.dumps(package, protocol=pickle.HIGHEST_PROTOCOL)) < limit:
        return True
    package.inline = None
    return False


def _inline_limit(config) -> int:
    return int(getattr(config, "stage_inline_max_bytes", 1024 * 1024))


def _upload(
    package: DataPackage,
    payloads: Mapping[str, Union[bytes, Path]],
    config,
) -> None:
    """Put a package's files in the private remote store, in one commit.

    Every file and the manifest go up as a single commit, which buys two
    things. Either the whole package becomes visible or none of it does, so
    there is no half-listed package to clean up -- and it costs one commit
    rather than one per file, which matters because the Hub rate-limits commits
    per hour and a package of a thousand files would otherwise exhaust that on
    its own.

    "Nothing is uploaded until the commit" is not quite true and the error
    messages here must not claim it: ``huggingface_hub`` runs
    ``preupload_lfs_files`` before it posts the commit, so LFS-tracked content
    is already on the Hub by the time a commit can be refused. Those objects
    belong to no commit, appear in no listing, and are collected by the Hub.

    The manifest is what marks a package complete; anything without one is
    reported as incomplete by :func:`list_data_packages`.
    """
    from huggingface_hub import CommitOperationAdd

    repo_id = _hf_repo(config)
    prefix = f"{PACKAGE_PREFIX}/{package.package_id}"
    api = _hf_api(config)

    _require_private_repo(api, repo_id)

    operations = [
        CommitOperationAdd(
            path_in_repo=f"{prefix}/files/{entry.relpath}",
            path_or_fileobj=(
                payloads[entry.relpath]
                if isinstance(payloads[entry.relpath], bytes)
                else str(payloads[entry.relpath])
            ),
        )
        for entry in package.files
    ]
    manifest = json.dumps(
        {
            "name": package.name,
            "package_id": package.package_id,
            "files": [entry.as_dict() for entry in package.files],
            "total_bytes": package.total_bytes,
        },
        indent=2,
    ).encode()
    operations.append(
        CommitOperationAdd(
            path_in_repo=f"{prefix}/{MANIFEST_NAME}", path_or_fileobj=manifest
        )
    )

    try:
        api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=f"clustrix data package {package.name}",
        )
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limited(exc):
            raise StagingError(
                f"HuggingFace is rate-limiting commits to {repo_id}, so data "
                f"package {package.name!r} was not staged. The Hub allows a "
                "fixed number of commits per hour per account; wait for the "
                "window to roll over and try again. No package folder was "
                "created, so nothing is listed and there is nothing to delete. "
                "Note that huggingface_hub uploads LFS-tracked files -- which "
                "is most content over a megabyte -- *before* it posts the "
                "commit, so those bytes may already be on the Hub as objects "
                "no commit references. They are not part of any package and "
                "clustrix cannot address them; the Hub garbage-collects them."
            ) from exc
        raise StagingError(
            f"Could not stage data package {package.name!r} to {repo_id}: {exc}"
        ) from exc

    _verify_sources_unchanged(package, payloads, api, repo_id, prefix)

    package.repo_id = repo_id
    package.path_in_repo = prefix
    logger.info(
        "Staged data package %s (%d file(s), %s) at %s/%s",
        package.name,
        len(package.files),
        _format_bytes(package.total_bytes),
        repo_id,
        prefix,
    )


def _require_private_repo(api, repo_id: str) -> None:
    """Create the data repo if it is missing, and refuse it if it is public.

    ``create_repo(private=True, exist_ok=True)`` creates a private repo but
    does **not** make an existing public one private -- ``exist_ok`` returns
    the repo as it is. So a ``hf_data_repo`` that already existed and was
    public took the upload and published it, while the docstring promised
    private.

    Clustrix refuses instead of flipping the setting. A repo may be public
    deliberately, that is the owner's decision to make, and silently changing
    someone's visibility is its own incident. Refusing costs a message.
    """
    try:
        api.create_repo(
            repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True
        )
    except Exception as exc:  # noqa: BLE001
        raise StagingError(
            f"Could not create or reach the data repo {repo_id}: {exc}. Check "
            "that your HuggingFace token has write access to that namespace, "
            "or set hf_data_repo to a repo you can write to."
        ) from exc

    try:
        info = api.repo_info(repo_id=repo_id, repo_type="dataset")
    except Exception as exc:  # noqa: BLE001
        raise StagingError(
            f"Could not check whether the data repo {repo_id} is private: "
            f"{exc}. Nothing has been uploaded -- clustrix will not stage data "
            "into a repo whose visibility it could not confirm."
        ) from exc

    if not getattr(info, "private", False):
        raise StagingError(
            f"The data repo {repo_id} is PUBLIC. Staging into it would publish "
            "your data to anyone. Nothing has been uploaded. clustrix will not "
            "change the setting for you -- a repo can be public on purpose, "
            "and that is yours to decide. Either make it private at "
            f"https://huggingface.co/datasets/{repo_id}/settings, or point "
            "hf_data_repo at a private repo."
        )


def _verify_sources_unchanged(
    package: DataPackage,
    payloads: Mapping[str, Union[bytes, Path]],
    api,
    repo_id: str,
    prefix: str,
) -> None:
    """Re-hash the staged files and refuse the package if any of them moved.

    Files go up by path, so the bytes on the wire are whatever the file held at
    upload time, which is not necessarily what was hashed a moment earlier. A
    file appended to in between produces a package whose digest describes bytes
    that were never uploaded -- detected on the worker, hours later, as an
    unexplained digest mismatch. Hashing again here costs one more read and
    turns that into an error at the point of the mistake, naming the file.

    The just-created folder is removed before raising, so a package that cannot
    be trusted is not left occupying the store.
    """
    changed: List[str] = []
    for entry in package.files:
        source = payloads[entry.relpath]
        if not isinstance(source, Path):
            continue
        try:
            if _digest_file(source) != entry.digest:
                changed.append(str(source))
        except OSError:
            changed.append(str(source))
    if not changed:
        return

    try:
        api.delete_folder(
            path_in_repo=prefix,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message="clustrix: discard package built from changing files",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not remove the untrustworthy package at %s/%s: %s. "
            "Delete it by hand with clustrix.delete_data_package(%r).",
            repo_id,
            prefix,
            exc,
            package.package_id,
        )

    raise StagingError(
        f"These files changed while data package {package.name!r} was being "
        "staged, so what was uploaded does not match what was hashed: "
        + ", ".join(sorted(changed)[:5])
        + ". The package has been removed from the store. Stage it again once "
        "the files have stopped changing."
    )


# ---------------------------------------------------------------------------
# finding and removing packages without the object
# ---------------------------------------------------------------------------


def list_data_packages(config=None) -> List[Dict[str, Any]]:
    """Every package clustrix has staged in the remote store.

    A user who lost the handle still needs a way to see what is costing them
    storage, so the object is not the only key to its own deletion. Returns a
    list of manifests; incomplete uploads appear with ``"complete": False``.
    """
    cfg = config if config is not None else _config()
    repo_id = _hf_repo(cfg)
    api = _hf_api(cfg)
    try:
        names = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    except Exception as exc:  # noqa: BLE001
        if _is_missing(exc):
            return []
        raise StagingError(f"Could not list data packages in {repo_id}: {exc}") from exc

    ids = sorted(
        {
            parts[1]
            for parts in (name.split("/") for name in names)
            if len(parts) > 2 and parts[0] == PACKAGE_PREFIX
        }
    )
    found: List[Dict[str, Any]] = []
    for package_id in ids:
        manifest_path = f"{PACKAGE_PREFIX}/{package_id}/{MANIFEST_NAME}"
        record: Dict[str, Any] = {
            "package_id": package_id,
            "repo_id": repo_id,
            "path_in_repo": f"{PACKAGE_PREFIX}/{package_id}",
            "complete": manifest_path in names,
        }
        if record["complete"]:
            record.update(_read_manifest(api, repo_id, manifest_path, cfg))
        found.append(record)
    return found


def _read_manifest(api, repo_id: str, path: str, config) -> Dict[str, Any]:
    """Read a stored manifest.

    Remote-origin data, so: fixed-schema ``json.load`` and nothing else. Never
    pickle, never eval. A manifest that does not parse is reported as such
    rather than crashing the listing.
    """
    from huggingface_hub import hf_hub_download

    try:
        local = hf_hub_download(
            repo_id=repo_id, filename=path, repo_type="dataset", token=_hf_token(config)
        )
        with open(local) as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("manifest is not an object")
        return {
            "name": str(raw.get("name", "")),
            "total_bytes": int(raw.get("total_bytes", 0)),
            "file_count": len(raw.get("files", []) or []),
        }
    except Exception as exc:  # noqa: BLE001
        return {"manifest_error": str(exc)}


def delete_data_package(package_id: str, config=None) -> bool:
    """Delete a staged package by id, without needing the object.

    The counterpart to :func:`list_data_packages`. Returns whether anything was
    removed; a package that is already gone is not an error, a package that is
    there and will not delete raises.

    The id is validated first, before anything reaches the Hub. It becomes a
    path in the store, so an id that is not one is a deletion aimed somewhere
    else: ``""`` addressed the whole ``packages/`` prefix and removed every
    package in the account, and ``"../README.md"`` climbed out of the prefix
    and removed a file that was never a package.
    """
    package_id = _validate_package_id(package_id)
    cfg = config if config is not None else _config()
    repo_id = _hf_repo(cfg)
    prefix = f"{PACKAGE_PREFIX}/{package_id}"
    api = _hf_api(cfg)
    try:
        api.delete_folder(
            path_in_repo=prefix,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"clustrix: delete data package {package_id}",
        )
    except Exception as exc:  # noqa: BLE001
        if _is_missing(exc):
            return False
        raise StagingError(
            f"Could not delete data package {package_id} from {repo_id}: {exc}"
        ) from exc
    return True


def materialize_packages(value: Any, config=None) -> Any:
    """Walk a structure and materialise every :class:`DataPackage` in it.

    Convenience for a worker that would rather have paths than handles::

        roots = clustrix.materialize_packages(packages)

    Lists, tuples, and dicts are walked; anything else is returned unchanged.
    """
    if isinstance(value, DataPackage):
        return value.materialize(config=config)
    if isinstance(value, list):
        return [materialize_packages(item, config) for item in value]
    if isinstance(value, tuple):
        return tuple(materialize_packages(item, config) for item in value)
    if isinstance(value, dict):
        return {k: materialize_packages(v, config) for k, v in value.items()}
    return value
