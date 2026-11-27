"""Find the local files a call is going to read, and put them where a job can.

A function sent to a cluster takes its code with it but not the files it
opens. ``pd.read_csv("data/subjects.csv")`` works on the laptop and raises
``FileNotFoundError`` on the cluster, and the fix -- copy the file over,
then edit the path -- is exactly the chore clustrix exists to remove.

This module looks for the paths a call names, in the places a path can be
named, and makes each one resolve on the cluster:

``arguments``
    every ``pathlib.Path`` and path-like string among the arguments, at any
    depth inside lists, tuples, sets and dicts.
``the function's code``
    string constants in the function, in the project-local functions it
    calls, and the values of the globals it reads.

A candidate counts only if it **exists on this machine**. What happens next
depends on where it already is:

* the same absolute path exists on the cluster with the same size (a shared
  filesystem, or data already there): it is used **in place**, nothing moves;
* otherwise the file is uploaded once into a content-addressed store under
  ``remote_work_dir`` -- a second call with the same file uploads nothing --
  and then either linked into each job's working directory at the same
  relative path, so the code runs unmodified, or, for an absolute path given
  as an argument, the argument is rewritten to the cluster's copy.

A path written relative to the working directory is linked into each job's
directory in both cases, because a job does not run where the call was made.

The store
---------

Each input lives at ``<remote_work_dir>/data/<identity>/<name>``. The
identity is a digest of the name and the *content* (of every file, for a
directory) up to ``_HASH_LIMIT_BYTES`` in total; above that it is a digest
of names, sizes and modification times, and the plan says so, because a
change that preserves all three would then be served stale.

A copy is uploaded under a private name, made **read-only**, marked with a
``.complete`` file and only then renamed to its identity. So a copy that
is visible under its identity is whole, a job cannot alter what the next
call will read, and two calls preparing the same input cannot see each
other's half-written files. The mark is a file rather than the read-only
bit because ``chmod -R`` clears the top directory's bit first: a recursive
chmod that was interrupted looks finished from the outside. A directory
under an identity without the mark is not trusted and is replaced.
Removing the store by hand needs ``chmod -R u+w`` first.

What this does not do, on purpose
---------------------------------

* **It never uploads what the user's own context did not put in scope.** A
  path found in code must be inside the project (the git top level, else
  the working directory) after resolving symlinks. A path passed as an
  argument is the user's explicit choice and may be anywhere. Credential
  files are refused in both cases, **and inside a directory**: a member
  that looks like a credential is left out, named in a warning and in the
  plan, and the rest of the directory is staged. ``no_data=[...]`` removes
  a path, and everything under it, whether it was named itself or is a
  member of a directory that was. Nothing the *cluster* says can widen
  this: there is no "the job asked for this file" channel.
* **It never stages a whole project.** The project directory, the working
  directory, the home directory and anything containing them are refused
  however they were named (``"."`` and ``"./"`` are ordinary constants in
  code); version-control and cache directories (``.git``,
  ``__pycache__``, ...) are never among a directory's members; and
  symbolic links inside a directory are not followed -- each is named in
  the plan.
* **It never copies large data silently.** Above ``auto_stage_max_bytes``
  in one call it stops and lists the sizes, because the right answer for
  200 GB is to find it on the cluster, not to fill a quota.
* **It does not send data to a third party.** Transfers go over the
  existing SSH connection to the cluster's own filesystem.

What it cannot see
------------------

A path computed at run time -- ``os.path.join(root, f"{subject}.csv")``,
a glob, a path read out of a config file -- names nothing until the code
runs, so it is not found here. Pass such inputs as arguments, list them
with ``@cluster(data=[...])``, or declare them with
:func:`clustrix.staging.data_package`.

Paths are found among arguments inside lists, tuples, sets and dicts, and
a rewritten argument keeps its type. The attributes of a dataclass or any
other object are not searched and not rewritten: pass the path itself.
"""

import copy
import logging
import os
import shlex
import subprocess
import threading
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .staging import (
    StagingError,
    _digest_bytes,
    _digest_file,
    _format_bytes,
    _is_sensitive_target,
)

logger = logging.getLogger(__name__)

#: A bare word passed as an argument is taken for a path only when the
#: parameter it was passed to is named like one. ``mode="train"`` must not
#: upload a directory that happens to be called ``train``.
#: Whole underscore-separated words only -- ``direction`` contains "dir" and
#: ``profile`` contains "file" -- and no word as vague as "data" or "input".
_PATH_LIKE_PARAMETER_WORDS = frozenset(
    {
        "path",
        "paths",
        "file",
        "files",
        "filename",
        "filepath",
        "dir",
        "dirs",
        "directory",
        "folder",
        "csv",
        "checkpoint",
        "ckpt",
        "weights",
    }
)

#: Never among the members of a staged directory.
_NEVER_STAGED_DIRECTORIES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".ipynb_checkpoints"}
)

#: Containers are searched this deep, and this many leaves, for paths.
_MAX_DEPTH = 6
_MAX_LEAVES = 5000

#: How many project-local functions are followed from the decorated one.
_MAX_FUNCTIONS = 60

#: Files up to this size are identified by content; larger ones by name,
#: size and modification time, so a 50 GB file is not read just to name it.
_HASH_LIMIT_BYTES = 256 * 1024 * 1024

#: Directory under ``remote_work_dir`` that holds uploaded inputs.
STORE_DIRECTORY = "data"

#: Written last into an uploaded copy; see "The store" above.
_COMPLETE = ".complete"

#: Prefix of the private name a copy is uploaded under.
_INCOMING = ".incoming-"

#: Starts each line :func:`_remote_sizes` reads, so a login banner or an
#: error message on the same stream is not taken for an answer.
_SIZE_MARK = "CLUSTRIX-SIZE"


@dataclass(frozen=True)
class Candidate:
    """A local path a call names, and where it was named."""

    written: str  # exactly as it appeared
    path: Path  # absolute, not resolved through symlinks
    found_in: str  # "argument 'x'", "the code of f", "global DATA"
    explicit: bool  # True for arguments and data=[...]


@dataclass
class StagedInput:
    """What was done about one candidate."""

    local: Path
    remote: str
    size: int
    how: str  # "in place" | "uploaded" | "already uploaded"
    found_in: str
    #: Path relative to a job's working directory to link at, if any.
    link: Optional[str] = None
    #: False when the copy is named by sizes and times: too big to read.
    identified_by_content: bool = True


@dataclass
class DataPlan:
    """Everything a call's jobs need set up, and an account of it."""

    inputs: List[StagedInput] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def links(self) -> List[Tuple[str, str]]:
        """``(path relative to the job directory, remote target)`` pairs.

        A link that would sit inside another link's directory is left out:
        the directory already provides it, and creating it would write
        *through* the outer link into the store -- or, for an input used
        in place, into the user's own directory.
        """
        linked = [(item.link, item.remote) for item in self.inputs if item.link]
        outer = {PurePosixPath(link) for link, _ in linked}
        return [
            (link, remote)
            for link, remote in linked
            if not outer.intersection(PurePosixPath(link).parents)
        ]

    def explain(self) -> str:
        if not self.inputs and not self.skipped:
            return "no local input files were found for this call"
        lines = []
        for item in self.inputs:
            lines.append(
                f"{item.local} ({_format_bytes(item.size)}, from {item.found_in}): "
                f"{item.how} -> {item.remote}"
                + ("" if item.identified_by_content else f" ({_BY_METADATA})")
            )
        lines.extend(self.skipped)
        return "\n".join(lines)


_BY_METADATA = "identified by size and time, not content"


# --------------------------------------------------------------------------
# Discovery: local and pure
# --------------------------------------------------------------------------


def project_root(start: Optional[Path] = None) -> Path:
    """The directory that bounds what code is allowed to name.

    The git top level containing the working directory, else the working
    directory. A home directory or the filesystem root is too wide to be a
    project, so those are narrowed to the working directory itself.
    """
    here = (start or Path.cwd()).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        root = Path(top.stdout.strip()).resolve() if top.returncode == 0 else here
    except (OSError, subprocess.SubprocessError):
        root = here
    if root in (Path(root.anchor), Path.home().resolve()):
        return here
    return root


def _looks_like_a_path(text: str) -> bool:
    """True for a string written the way a path is, as opposed to a word."""
    if not text or len(text) > 4096 or "\n" in text or "\x00" in text:
        return False
    if "://" in text:  # a URL: s3://, https://, ...
        return False
    return (
        "/" in text
        or os.sep in text
        or text.startswith("~")
        or bool(PurePosixPath(text).suffix)
    )


def _as_local_path(text: str) -> Optional[Path]:
    """``text`` as an absolute path if it names something that exists here."""
    try:
        path = Path(os.path.expanduser(text))
        if not path.is_absolute():
            path = Path.cwd() / path
        return path if path.exists() else None
    except (OSError, ValueError):
        return None


def _paths_in_value(value: Any) -> Iterable[Tuple[Any, bool]]:
    """Every string and path-like inside ``value``: ``(leaf, is_pathlike)``."""
    stack: List[Tuple[Any, int]] = [(value, 0)]
    seen = 0
    while stack and seen < _MAX_LEAVES:
        current, depth = stack.pop()
        if isinstance(current, os.PathLike):
            seen += 1
            yield current, True
        elif isinstance(current, str):
            seen += 1
            yield current, False
        elif depth < _MAX_DEPTH:
            if isinstance(current, dict):
                stack.extend((item, depth + 1) for item in current.values())
            elif isinstance(current, (list, tuple, set, frozenset)):
                stack.extend((item, depth + 1) for item in current)


def _is_project_function(value: Any, root: Path) -> bool:
    if not isinstance(value, types.FunctionType):
        return False
    filename = value.__code__.co_filename
    if value.__module__ == "__main__" or filename.startswith("<"):
        return True  # a script, a notebook cell, an exec'd definition
    try:
        return Path(filename).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _strings_in_code(code: types.CodeType) -> Iterable[str]:
    for constant in code.co_consts:
        if isinstance(constant, str):
            yield constant
        elif isinstance(constant, types.CodeType):
            yield from _strings_in_code(constant)
        elif isinstance(constant, (tuple, frozenset)):
            yield from (item for item in constant if isinstance(item, str))


def _names_in_code(code: types.CodeType) -> Set[str]:
    names = set(code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            names |= _names_in_code(constant)
    return names


def _named_like_a_path(parameter: str) -> bool:
    """Whether a parameter's name says its value is a path."""
    return any(
        word.rstrip("0123456789") in _PATH_LIKE_PARAMETER_WORDS
        for word in parameter.lower().split("_")
    )


def _excluded_paths(exclude: Sequence[Any]) -> List[Path]:
    """``no_data=[...]`` as absolute paths, as written and through links."""
    excluded: List[Path] = []
    for item in exclude:
        written = os.path.abspath(os.path.expanduser(os.fspath(item)))
        excluded.extend({Path(written), Path(os.path.realpath(written))})
    return excluded


def _is_excluded(path: Path, excluded: Sequence[Path]) -> bool:
    """True for a path that is, or is under, one of ``excluded``."""
    if not excluded:
        return False
    spellings = {Path(os.path.abspath(path)), Path(os.path.realpath(path))}
    return any(
        spelling == out or out in spelling.parents
        for spelling in spellings
        for out in excluded
    )


def discover(
    func: Callable,
    args: tuple,
    kwargs: dict,
    extra: Sequence[Any] = (),
    exclude: Sequence[Any] = (),
) -> List[Candidate]:
    """The local paths this call names. Touches nothing but this machine.

    ``extra`` are paths the user listed with ``@cluster(data=[...])``;
    ``exclude`` (``no_data=[...]``) removes a path, and everything under it,
    from consideration -- an output directory left by an earlier run, say.
    """
    import inspect

    found: Dict[Path, Candidate] = {}
    excluded = _excluded_paths(exclude)

    def add(written: str, found_in: str, explicit: bool) -> None:
        path = _as_local_path(written)
        if path is None:
            return
        absolute = Path(os.path.abspath(path))
        if _is_excluded(absolute, excluded):
            return
        # An explicit mention wins over an incidental one for the same file.
        existing = found.get(absolute)
        if existing is None or (explicit and not existing.explicit):
            found[absolute] = Candidate(written, absolute, found_in, explicit)

    for item in extra:
        add(os.fspath(item), "data=[...]", True)

    func = inspect.unwrap(func)
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs).arguments
    except (TypeError, ValueError):
        bound = {f"#{index}": value for index, value in enumerate(args)}
        bound.update(kwargs)
    for name, value in bound.items():
        hinted = _named_like_a_path(name)
        for leaf, is_pathlike in _paths_in_value(value):
            text = os.fspath(leaf)
            if isinstance(text, bytes):
                continue
            if is_pathlike or _looks_like_a_path(text) or (hinted and text.strip()):
                add(text, f"argument {name!r}", True)

    # The function, the project-local functions it calls, and their globals.
    root = project_root()
    pending: List[Any] = [func]
    visited: Set[int] = set()
    while pending and len(visited) < _MAX_FUNCTIONS:
        current = pending.pop()
        if id(current) in visited or not isinstance(current, types.FunctionType):
            continue
        visited.add(id(current))
        where = f"the code of {current.__name__}"
        for text in _strings_in_code(current.__code__):
            if _looks_like_a_path(text):
                add(text, where, False)
        referenced = {
            name: current.__globals__[name]
            for name in _names_in_code(current.__code__)
            if name in current.__globals__
        }
        for cell_name, cell in zip(
            current.__code__.co_freevars, current.__closure__ or ()
        ):
            try:
                referenced[cell_name] = cell.cell_contents
            except ValueError:
                pass
        for name, value in referenced.items():
            if _is_project_function(value, root):
                pending.append(value)
            elif isinstance(value, os.PathLike):
                add(os.fspath(value), f"global {name}", False)
            elif isinstance(value, str) and _looks_like_a_path(value):
                add(value, f"global {name}", False)
    return list(found.values())


# --------------------------------------------------------------------------
# Admission: what may leave this machine
# --------------------------------------------------------------------------


def _files_under(
    path: Path,
    excluded: Sequence[Path] = (),
    left_out: Optional[Dict[str, List[Path]]] = None,
) -> List[Tuple[str, Path]]:
    """``(relative posix path, file)`` for ``path`` or everything beneath it.

    Of a directory's members, these are not returned:

    * anything that is, or is under, one of ``excluded``;
    * version-control and cache directories (``_NEVER_STAGED_DIRECTORIES``);
    * files that look like credentials -- collected in
      ``left_out["credential"]``;
    * symbolic links, to files or directories -- ``left_out["link"]``.
    """
    if path.is_file():
        return [("", path)]
    notes: Dict[str, List[Path]] = {} if left_out is None else left_out
    files = []
    for directory, subdirectories, names in os.walk(path, followlinks=False):
        here = Path(directory)
        descend = []
        for name in sorted(subdirectories):
            member = here / name
            if name in _NEVER_STAGED_DIRECTORIES or _is_excluded(member, excluded):
                continue
            if member.is_symlink():
                notes.setdefault("link", []).append(member)
                continue
            descend.append(name)
        subdirectories[:] = descend
        for name in sorted(names):
            member = here / name
            if _is_excluded(member, excluded):
                continue
            if member.is_symlink():
                notes.setdefault("link", []).append(member)
            elif not member.is_file():
                continue  # a socket, a FIFO, a device
            elif _is_sensitive_target(member):
                notes.setdefault("credential", []).append(member)
            else:
                files.append((member.relative_to(path).as_posix(), member))
    return sorted(files)


def _too_wide(candidate: Candidate, root: Path) -> Optional[str]:
    """What ``candidate`` is, if it is a directory no call means to send."""
    if not candidate.path.is_dir():
        return None
    real = Path(os.path.realpath(candidate.path))
    home = Path(os.path.realpath(Path.home()))
    here = Path(os.path.realpath(os.getcwd()))
    if real == root:
        return "the project directory"
    if real == here:
        return "the working directory"
    if real == home:
        return "the home directory"
    if real in root.parents:
        return "a directory that contains the project"
    if real in home.parents:
        return "a directory that contains the home directory"
    if not candidate.explicit and real in here.parents:
        return "a directory that contains the working directory"
    return None


def _refusal(candidate: Candidate, root: Path) -> Optional[str]:
    """Why ``candidate`` may not be uploaded, or ``None`` if it may."""
    path = candidate.path
    if _is_sensitive_target(path):
        return f"{path} looks like a credential and is never uploaded"
    if not (path.is_file() or path.is_dir()):
        return f"{path} is not a regular file or directory"
    too_wide = _too_wide(candidate, root)
    if too_wide:
        return (
            f"{path} ({candidate.found_in}) is {too_wide}, and clustrix does "
            "not copy one of those to a cluster; name the files or "
            "directories the job needs (as arguments or in data=[...])"
        )
    if not candidate.explicit:
        resolved = Path(os.path.realpath(path))
        if not resolved.is_relative_to(root):
            return (
                f"{path} ({candidate.found_in}) is outside the project at "
                f"{root}; pass it as an argument or list it in data=[...] "
                "if the job should have it"
            )
    return None


def _identity(path: Path, files: List[Tuple[str, Path]]) -> Tuple[str, bool]:
    """A short name for this exact input, and whether it names the content.

    Up to ``_HASH_LIMIT_BYTES`` in all, the name is a digest of what the
    files contain, so content changed behind an unchanged size and
    modification time is still a different input. Above it the files are
    not read: the name is a digest of sizes and times, and the second value
    is ``False`` so the plan can say so.

    The input's own name is part of both, because the copy is stored under
    it: two files with the same bytes and different names are two copies.
    """
    stats = [(relative, file, file.stat()) for relative, file in files]
    if sum(stat.st_size for _, _, stat in stats) <= _HASH_LIMIT_BYTES:
        described = [f"{relative}|{_digest_file(file)}" for relative, file, _ in stats]
        by_content = True
    else:
        described = [
            f"{relative}|{stat.st_size}|{stat.st_mtime_ns}"
            for relative, _, stat in stats
        ]
        by_content = False
    listing = "\n".join([path.name, *described])
    return _digest_bytes(listing.encode())[:20], by_content


# --------------------------------------------------------------------------
# Staging: needs the cluster
# --------------------------------------------------------------------------


def _remote_sizes(connection, paths: Sequence[str]) -> Dict[str, Optional[int]]:
    """Size in bytes of each remote path that is a file; ``None`` otherwise.

    A directory reports ``-1``: present, size not comparable. A file or
    directory the remote account cannot read is ``None``, like one that is
    not there -- a job could not read it either. One round trip for the
    whole list.

    Each path is answered on a line of its own that carries the path's
    index, so an error message, a login banner or a missing answer cannot
    shift every later size onto the wrong path.
    """
    if not paths:
        return {}
    script = "; ".join(
        f"if [ -d {quoted} ] && [ -r {quoted} ] && [ -x {quoted} ]; then s=-1; "
        f"elif [ -f {quoted} ] && [ -r {quoted} ]; "
        f"then s=$(wc -c < {quoted}) || s=missing; "
        f"else s=missing; fi; echo {_SIZE_MARK} {index} $s"
        for index, quoted in enumerate(shlex.quote(path) for path in paths)
    )
    output, _ = connection.execute_remote_command(script, check=True)
    answers: Dict[int, Optional[int]] = {}
    for line in output.splitlines():
        words = line.split()
        if len(words) == 3 and words[0] == _SIZE_MARK and words[1].isdigit():
            size = words[2]
            answers[int(words[1])] = int(size) if size.lstrip("-").isdigit() else None
    unanswered = [path for index, path in enumerate(paths) if index not in answers]
    if unanswered:
        raise StagingError(
            "The cluster did not say whether these paths exist there: "
            + ", ".join(unanswered[:5])
        )
    return {path: answers[index] for index, path in enumerate(paths)}


def _relative_link(candidate: Candidate) -> Optional[str]:
    """Where to link the staged copy inside a job directory, if anywhere.

    Only for a path written relative to the working directory and staying
    inside it: that is the one spelling the job's own working directory can
    reproduce.
    """
    written = os.path.expanduser(candidate.written)
    if os.path.isabs(written):
        return None
    normal = PurePosixPath(os.path.normpath(written).replace(os.sep, "/"))
    if normal.is_absolute() or ".." in normal.parts or str(normal) == ".":
        return None
    return str(normal)


def _rewritten(value: Any, replacements: Dict[str, str], depth: int = 0) -> Any:
    """``value`` with every path in ``replacements`` swapped for its target.

    Copy on change: a value with no such path inside it is returned as the
    very same object, so an argument that has nothing to do with files is
    not rebuilt as something else. A container that did change is rebuilt
    as its own type -- a named tuple stays one, a ``defaultdict`` keeps its
    factory, sets are rewritten too.

    Lists, tuples, sets and dicts (values, not keys) and their subclasses
    are searched, to ``_MAX_DEPTH``. The attributes of other objects --
    dataclass instances included -- are not.
    """
    if isinstance(value, os.PathLike):
        target = replacements.get(os.fspath(value))
        if target is None:
            return value
        return Path(target) if isinstance(value, Path) else target
    if isinstance(value, str):
        return replacements.get(value, value)
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        changed = {}
        for key, item in value.items():
            new = _rewritten(item, replacements, depth + 1)
            if new is not item:
                changed[key] = new
        if not changed:
            return value
        rebuilt = copy.copy(value)
        for key, new in changed.items():
            rebuilt[key] = new
        return rebuilt
    if not isinstance(value, (list, tuple, set, frozenset)):
        return value
    items = list(value)
    new_items = [_rewritten(item, replacements, depth + 1) for item in items]
    if all(new is old for new, old in zip(new_items, items)):
        return value
    if isinstance(value, list):
        rebuilt_list = copy.copy(value)
        rebuilt_list[:] = new_items
        return rebuilt_list
    if isinstance(value, set):
        rebuilt_set = copy.copy(value)
        rebuilt_set.clear()
        rebuilt_set.update(new_items)
        return rebuilt_set
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return type(value)(*new_items)  # a named tuple takes its fields
    try:
        return type(value)(new_items)
    except TypeError as error:
        raise StagingError(
            f"A {type(value).__name__} argument holds a path that was copied "
            "to the cluster, and it cannot be rebuilt with the copy's path "
            f"({error}). Pass the path as an argument of its own."
        ) from error


class _Stopped(Exception):
    """Raised inside a transfer to end it between two packets."""


def _send_files(connection, targets: Sequence[Tuple[str, Path]]) -> None:
    """Copy each ``(remote path, local file)`` over one SFTP session.

    The transfer runs on a thread of its own and this one only waits for
    it, so that Ctrl-C arrives here and never in the middle of an SFTP
    packet. An interrupt that lands inside paramiko's ``put`` leaves half a
    packet on the wire: closing the file then waits for ever for a reply,
    or the server drops the session and the ``KeyboardInterrupt`` is
    replaced by an ``SSHException``. Instead the transfer is asked to stop
    and does so cleanly at its next packet.
    """
    if connection.ssh_client is None:
        raise RuntimeError("SSH client not connected.")
    stop = threading.Event()

    def stop_if_asked(sent: int, total: int) -> None:
        if stop.is_set():
            raise _Stopped()

    def send() -> None:
        sftp = connection.ssh_client.open_sftp()
        try:
            for target, file in targets:
                stop_if_asked(0, 0)
                sftp.put(str(file), target, callback=stop_if_asked)
        finally:
            sftp.close()

    with ThreadPoolExecutor(max_workers=1) as transfer:
        sending = transfer.submit(send)
        try:
            sending.result()
        except BaseException:
            stop.set()  # leaving the ``with`` waits for it to have stopped
            raise


def _upload(
    connection,
    store: str,
    identity: str,
    name: str,
    files: List[Tuple[str, Path]],
    is_directory: bool,
) -> None:
    """Put one input in the store at ``<store>/<identity>/<name>``, whole.

    Everything is written under a private name first. Only when every file
    is there is the copy made read-only, marked complete and renamed to its
    identity -- one rename, so no half-written file is ever visible under
    the final name, to a job or to another call uploading the same input.
    If that other call finished first, its copy stays and this one is
    discarded. Whatever goes wrong, the private copy is removed.
    """
    final = f"{store}/{identity}"
    incoming = f"{store}/{_INCOMING}{identity}-{uuid.uuid4().hex[:12]}"
    payload = f"{incoming}/{name}"
    targets = [
        (f"{payload}/{relative}" if relative else payload, file)
        for relative, file in files
    ]
    directories = {str(PurePosixPath(target).parent) for target, _ in targets}
    directories.add(payload if is_directory else incoming)
    quoted_final, quoted_incoming = shlex.quote(final), shlex.quote(incoming)
    # Tried more than once: after an interrupted transfer the server may
    # still be writing what was already sent when the first attempt runs.
    discard = (
        "for attempt in 1 2 3 4 5; do chmod -R u+w %s 2>/dev/null; "
        "rm -rf %s 2>/dev/null; [ -e %s ] || break; sleep 1; done"
    )
    created = False
    try:
        connection.execute_remote_command(
            "mkdir -p " + " ".join(shlex.quote(d) for d in sorted(directories)),
            check=True,
        )
        created = True
        _send_files(connection, targets)
        connection.execute_remote_command(
            f"touch {quoted_incoming}/{_COMPLETE} && chmod -R a-w {quoted_incoming} "
            "|| exit 1; "
            # A directory here without the mark is not a finished copy.
            f"if [ -e {quoted_final} ] && [ ! -e {quoted_final}/{_COMPLETE} ]; "
            f"then mv {quoted_final} {quoted_incoming}.stale "
            f"&& {discard % ((quoted_incoming + '.stale',) * 3)}; fi; "
            f"[ -e {quoted_final} ] || mv {quoted_incoming} {quoted_final} "
            "2>/dev/null; "
            # Still here: another call put the same input in place first.
            f"if [ -e {quoted_incoming} ]; "
            f"then {discard % ((quoted_incoming,) * 3)}; fi; "
            f"[ -e {quoted_final}/{_COMPLETE} ]",
            check=True,
        )
    except BaseException:
        try:
            # Interrupted while the ``mkdir`` was on its way: that command
            # and this one run in separate shells over there, and removing
            # first would leave what is created a moment later.
            connection.execute_remote_command(
                ("" if created else "sleep 2; ") + discard % ((quoted_incoming,) * 3)
            )
        except Exception as error:  # noqa: BLE001 - the connection may be gone
            logger.warning(
                "Could not remove the unfinished upload %s: %s", incoming, error
            )
        raise


def prepare(
    connection,
    config,
    func: Callable,
    args: tuple,
    kwargs: dict,
    extra: Sequence[Any] = (),
    exclude: Sequence[Any] = (),
) -> Tuple[tuple, dict, DataPlan]:
    """Make the local files this call names available to its jobs.

    Returns the arguments to send -- identical, except that an absolute
    local path that had to be uploaded now names the cluster's copy -- and
    the plan: what was found, what was done, and the links each job
    directory needs.

    Raises :class:`~clustrix.staging.StagingError` when the inputs found are
    larger than ``config.auto_stage_max_bytes`` or the cluster holds a
    different file at the same path.
    """
    plan = DataPlan()
    root = project_root()
    excluded = _excluded_paths(exclude)
    candidates: List[Tuple[Candidate, List[Tuple[str, Path]], int]] = []
    for candidate in discover(func, args, kwargs, extra, exclude):
        refusal = _refusal(candidate, root)
        if refusal:
            # A file the user handed over and clustrix will not send is worth
            # saying; a string in the code that merely resembles a path is not.
            if candidate.explicit:
                logger.warning("Not staging: %s", refusal)
            plan.skipped.append(f"not staged: {refusal}")
            continue
        left_out: Dict[str, List[Path]] = {}
        files = _files_under(candidate.path, excluded, left_out)
        credentials = left_out.get("credential", [])
        if credentials:
            logger.warning(
                "Not staging %d file(s) in %s that look like credentials; the "
                "rest of the directory is staged: %s",
                len(credentials),
                candidate.path,
                ", ".join(str(member) for member in credentials),
            )
            plan.skipped.extend(
                f"not staged: {member} looks like a credential and is never " "uploaded"
                for member in credentials
            )
        links = left_out.get("link", [])
        if links:
            logger.info(
                "Not staging %d symbolic link(s) in %s: %s",
                len(links),
                candidate.path,
                ", ".join(str(member) for member in links),
            )
            plan.skipped.extend(
                f"not staged: {member} is a symbolic link inside a staged "
                "directory; name its target if the job needs it"
                for member in links
            )
        size = sum(file.stat().st_size for _, file in files)
        candidates.append((candidate, files, size))
    if not candidates:
        return args, kwargs, plan

    # Already on the cluster at the same path? Then nothing moves.
    present = _remote_sizes(connection, [str(c.path) for c, _, _ in candidates])
    to_upload: List[Tuple[Candidate, List[Tuple[str, Path]], int]] = []
    for candidate, files, size in candidates:
        remote_size = present[str(candidate.path)]
        if remote_size is None:
            to_upload.append((candidate, files, size))
        elif candidate.path.is_file() and remote_size != size:
            raise StagingError(
                f"{candidate.path} exists on the cluster too, but as a "
                f"different file ({_format_bytes(remote_size)} there, "
                f"{_format_bytes(size)} here). clustrix will not guess which "
                "is right: remove or rename one, or exclude it with "
                "no_data=[...] to use the cluster's copy as it is."
            )
        else:
            # The job does not run in this working directory, so a path
            # written relative to it still needs a link, to where the file
            # already is.
            plan.inputs.append(
                StagedInput(
                    candidate.path,
                    str(candidate.path),
                    size,
                    "in place",
                    candidate.found_in,
                    link=_relative_link(candidate),
                )
            )

    limit = int(getattr(config, "auto_stage_max_bytes", 0) or 0)
    total = sum(size for _, _, size in to_upload)
    if limit and total > limit:
        listing = "\n".join(
            f"  {_format_bytes(size):>10}  {candidate.path}  ({candidate.found_in})"
            for candidate, _, size in sorted(to_upload, key=lambda entry: -entry[2])
        )
        raise StagingError(
            f"This call names {_format_bytes(total)} of local input, more than "
            f"clustrix copies to a cluster automatically "
            f"(auto_stage_max_bytes = {_format_bytes(limit)}):\n{listing}\n"
            "Put large data on the cluster yourself and pass its path there, "
            "raise auto_stage_max_bytes, or exclude a path with no_data=[...]."
        )

    replacements: Dict[str, str] = {}
    identities = [_identity(c.path, files) for c, files, _ in to_upload]
    store = ""
    complete: Dict[str, Optional[int]] = {}
    if to_upload:
        store = (
            connection.resolve_remote_path(config.remote_work_dir).rstrip("/")
            + f"/{STORE_DIRECTORY}"
        )
        complete = _remote_sizes(
            connection,
            [f"{store}/{identity}/{_COMPLETE}" for identity, _ in identities],
        )
    for (candidate, files, size), (identity, by_content) in zip(to_upload, identities):
        base = f"{store}/{identity}/{candidate.path.name}"
        already = complete[f"{store}/{identity}/{_COMPLETE}"] is not None
        if not already:
            _upload(
                connection,
                store,
                identity,
                candidate.path.name,
                files,
                candidate.path.is_dir(),
            )
        link = _relative_link(candidate)
        if link is None:
            replacements[candidate.written] = base
            replacements[str(candidate.path)] = base
        plan.inputs.append(
            StagedInput(
                candidate.path,
                base,
                size,
                "already uploaded" if already else "uploaded",
                candidate.found_in,
                link=link,
                identified_by_content=by_content,
            )
        )
        if link is None and not candidate.explicit:
            logger.warning(
                "%s (%s) was copied to the cluster as %s, but the code names "
                "it by an absolute path that does not exist there. Pass the "
                "path as an argument so it can be pointed at the copy.",
                candidate.path,
                candidate.found_in,
                base,
            )

    for item in plan.inputs:
        logger.info(
            "Input %s (%s): %s%s%s",
            item.local,
            _format_bytes(item.size),
            item.how,
            "" if item.how == "in place" else f" as {item.remote}",
            "" if item.identified_by_content else f" ({_BY_METADATA})",
        )
    if replacements:
        args = tuple(_rewritten(value, replacements) for value in args)
        kwargs = {key: _rewritten(value, replacements) for key, value in kwargs.items()}
    return args, kwargs, plan


def link_into_job_directory(
    connection, remote_job_dir: str, links: Sequence[Tuple[str, str]]
) -> None:
    """Create each ``(relative path, target)`` link inside a job directory."""
    if not links:
        return
    commands = [f"cd {shlex.quote(remote_job_dir)}"]
    for relative, target in links:
        parent = str(PurePosixPath(relative).parent)
        if parent not in ("", "."):
            commands.append(f"mkdir -p {shlex.quote(parent)}")
        commands.append(f"ln -sfn {shlex.quote(target)} {shlex.quote(relative)}")
    connection.execute_remote_command(" && ".join(commands), check=True)
