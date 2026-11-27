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

* the same absolute path exists on the cluster **and holds the same data**
  (a shared filesystem, or data already there): it is used **in place**,
  nothing moves. The same data means, for a file, the same size and the
  same SHA-256, computed on the cluster; for a directory, the same files
  with the same sizes and the same SHA-256 each. A path is not evidence:
  where the cluster cannot hash, the input is uploaded; where it holds
  something different, the call stops and says what differs;
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

The store is the owner's alone -- ``0700``, with copies ``0500`` and their
files ``0400`` -- whatever the remote umask is, because a file that is
private here must not become readable by every account on a cluster. A
store left open by an earlier version is closed the next time it is used.

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
  the working directory) after resolving symlinks, and the code must be
  the project's: nothing under ``site-packages`` or ``dist-packages`` is
  searched. The home directory, a directory containing it and ``/`` are
  not projects -- from there, nothing found in code is staged at all. A
  path passed as an argument is the user's explicit choice and may be
  anywhere. Credential files are refused in both cases, judged by the
  path as written and by the real path behind every symbolic link in it,
  **and inside a directory**: a member that looks like a credential is
  left out, named in a warning and in the plan, and the rest of the
  directory is staged. Recognising a credential is a list of names
  (``clustrix.staging.SENSITIVE_PATTERNS``) and one content check; a
  secret in a file with an ordinary name is not recognised. ``no_data=[...]`` removes
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

What it will not link
---------------------

A relative input is linked into each job directory under its own name, and
clustrix writes files of its own there (``result.pkl``, ``job.sh``, ...:
``clustrix.utils.RESERVED_JOB_DIRECTORY_NAMES``). An input whose first
path component is one of those names is refused at submission.

An input is staged read-only whether the function reads it or means to
overwrite it; the two look the same from here. A job that fails writing
to one raises its ``PermissionError`` with that explanation added.

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
import re
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
from .utils import is_reserved_job_directory_name

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

#: Brackets each answer :func:`_remote_listing` and :func:`_remote_hashes`
#: read, for the same reason.
_ANSWER_MARK = "CLUSTRIX-ANSWER"

#: What an input already on the cluster is compared by.
_COMPARED_BY = "sha256"

#: Sets ``$H`` to the cluster's SHA-256 command, or to nothing.
_FIND_REMOTE_HASHER = (
    "if command -v sha256sum >/dev/null 2>&1; then H=sha256sum; "
    'elif command -v shasum >/dev/null 2>&1; then H="shasum -a 256"; '
    "else H=; fi"
)

#: Inputs found identical to what the cluster has at the same path, for the
#: rest of this process: a 1 GB file is hashed on both sides once, not once
#: per call. The key includes the local sizes and modification times, so a
#: file edited here is compared again; a copy changed *on the cluster*
#: between two calls of one session is not noticed.
_SAME_IN_PLACE: Set[tuple] = set()

#: SHA-256 of local files, by path, size and modification time.
_LOCAL_SHA256: Dict[Tuple[str, int, int], str] = {}


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
    #: Why ``how``: what was compared, or why nothing could be.
    why: str = ""


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
                + (f" ({item.why})" if item.why else "")
                + ("" if item.identified_by_content else f" ({_BY_METADATA})")
            )
        lines.extend(self.skipped)
        return "\n".join(lines)


_BY_METADATA = "identified by size and time, not content"

_WILL_NOT_GUESS = (
    "clustrix will not guess which is right: remove or rename one, or "
    "exclude it with no_data=[...] to use the cluster's copy as it is."
)


# --------------------------------------------------------------------------
# Discovery: local and pure
# --------------------------------------------------------------------------


def _too_wide_for_a_project(directory: Path) -> bool:
    """True for the home directory, anything containing it, and ``/``."""
    home = Path(os.path.realpath(Path.home()))
    return (
        directory == home
        or directory in home.parents
        or directory == Path(directory.anchor)
    )


def project_root(start: Optional[Path] = None) -> Optional[Path]:
    """The directory that bounds what code is allowed to name, if there is one.

    The git top level containing the working directory, else the working
    directory -- unless that is the home directory, a directory containing
    it, or the filesystem root. None of those is a project: under a home
    directory are ``~/.ssh``, ``~/.kaggle`` and every package installed with
    ``pip install --user``, and "inside the project" would then describe a
    token file named by a constant in somebody else's library. A git
    checkout that wide (dotfiles kept in ``~``) is narrowed to the working
    directory; a working directory that wide gives ``None``, and then
    nothing found in code is staged -- only what the caller passed as an
    argument or listed in ``data=[...]``.
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
    for directory in (root, here):
        if not _too_wide_for_a_project(directory):
            return directory
    return None


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


#: Code under a directory with one of these names was installed, not
#: written here, whatever directory the installation happens to be under
#: (a virtual environment inside the project, ``~/.local/lib``).
_INSTALLED_CODE_DIRECTORIES = frozenset({"site-packages", "dist-packages"})


def _is_installed_code(value: Any) -> bool:
    """True for a function that came from an installed package."""
    code = getattr(value, "__code__", None)
    if code is None or code.co_filename.startswith("<"):
        return False
    filename = code.co_filename
    try:
        parts = Path(filename).resolve().parts
    except (OSError, ValueError):
        return True  # not somewhere a project's code is
    return not _INSTALLED_CODE_DIRECTORIES.isdisjoint(parts)


def _is_project_function(value: Any, root: Path) -> bool:
    """Whether the paths ``value``'s code names are the user's own words.

    A script, a notebook cell or an ``exec``'d definition is; so is a
    function whose file is inside the project. Installed code is not, even
    when the project directory contains the installation.
    """
    if not isinstance(value, types.FunctionType) or _is_installed_code(value):
        return False
    filename = value.__code__.co_filename
    if value.__module__ == "__main__" or filename.startswith("<"):
        return True
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
    # Without a project there is no code whose constants are the caller's
    # own choice of file, and a decorated library function never was.
    root = project_root()
    if root is None:
        logger.info(
            "No paths are looked for in code: the working directory (%s) is "
            "the home directory or contains it, which is not a project. "
            "Inputs passed as arguments or in data=[...] are still staged.",
            Path.cwd(),
        )
        return list(found.values())
    pending: List[Any] = [] if _is_installed_code(func) else [func]
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


def _too_wide(candidate: Candidate, root: Optional[Path]) -> Optional[str]:
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
    if root is not None and real in root.parents:
        return "a directory that contains the project"
    if real in home.parents:
        return "a directory that contains the home directory"
    if not candidate.explicit and real in here.parents:
        return "a directory that contains the working directory"
    return None


def _refusal(candidate: Candidate, root: Optional[Path]) -> Optional[str]:
    """Why ``candidate`` may not be uploaded, or ``None`` if it may."""
    path = candidate.path
    if _is_sensitive_target(path):
        real = os.path.realpath(path)
        through = "" if real == str(path) else f" (it is {real})"
        return f"{path}{through} looks like a credential and is never uploaded"
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
        if root is None:
            return (
                f"{path} ({candidate.found_in}) was named in code, and there "
                "is no project directory to bound what code may name; pass "
                "it as an argument or list it in data=[...]"
            )
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


def _answers(output: str) -> Dict[int, Tuple[List[str], str]]:
    """Split a marked remote reply into ``{index: (lines, exit status)}``.

    Only an answer with both its opening and closing mark is returned, so a
    reply cut short reads as no answer rather than as a short one.
    """
    answers: Dict[int, Tuple[List[str], str]] = {}
    index: Optional[int] = None
    lines: List[str] = []
    for line in output.splitlines():
        words = line.split()
        if len(words) >= 3 and words[0] == _ANSWER_MARK and words[1].isdigit():
            if words[2] == "BEGIN":
                index, lines = int(words[1]), []
            elif words[2] == "END" and index == int(words[1]) and len(words) == 4:
                answers[index] = (lines, words[3])
                index = None
        elif index is not None:
            lines.append(line)
    return answers


def _prune_never_staged() -> str:
    """``find`` arguments that skip what a staged directory never holds."""
    names = " -o ".join(
        f"-name {shlex.quote(name)}" for name in sorted(_NEVER_STAGED_DIRECTORIES)
    )
    return f"-type d \\( {names} \\) -prune -o -type f"


def _remote_listing(
    connection, directories: Sequence[str]
) -> Dict[str, Optional[Dict[str, int]]]:
    """``{relative path: size}`` of the files under each remote directory.

    ``None`` for a directory whose listing could not be had or read back
    whole -- a member the account cannot read, a file name with a newline in
    it. One round trip for all of them.
    """
    if not directories:
        return {}
    script = "; ".join(
        f"echo {_ANSWER_MARK} {index} BEGIN; "
        f"(cd {shlex.quote(directory)} && find . {_prune_never_staged()} "
        "-exec wc -c {} +) 2>/dev/null; "
        f"echo {_ANSWER_MARK} {index} END $?"
        for index, directory in enumerate(directories)
    )
    output, _ = connection.execute_remote_command(script)
    answers = _answers(output)
    listings: Dict[str, Optional[Dict[str, int]]] = {}
    for index, directory in enumerate(directories):
        lines, status = answers.get(index, ([], "missing"))
        listing: Optional[Dict[str, int]] = {} if status == "0" else None
        for line in lines:
            match = re.match(r"\s*(\d+) (.*)\Z", line)
            if listing is None or (match and match.group(2) == "total"):
                continue
            if not match or not match.group(2).startswith("./"):
                listing = None
                continue
            listing[match.group(2)[2:]] = int(match.group(1))
        listings[directory] = listing
    return listings


def _remote_hashes(
    connection, paths: Sequence[Tuple[str, bool]]
) -> Dict[str, Optional[Dict[str, str]]]:
    """SHA-256 of each remote ``(path, is a directory)``, computed there.

    ``{"": digest}`` for a file, ``{relative path: digest}`` for the files
    under a directory, and ``None`` when the cluster has no SHA-256 command
    or the answer could not be read back whole. One round trip.
    """
    if not paths:
        return {}
    commands = [_FIND_REMOTE_HASHER]
    for index, (path, is_directory) in enumerate(paths):
        quoted = shlex.quote(path)
        hashing = (
            f"(cd {quoted} && find . {_prune_never_staged()} -exec $H {{}} +)"
            if is_directory
            else f"$H {quoted}"
        )
        commands.append(
            f"echo {_ANSWER_MARK} {index} BEGIN; "
            f'if [ -n "$H" ]; then {hashing} 2>/dev/null; else false; fi; '
            f"echo {_ANSWER_MARK} {index} END $?"
        )
    output, _ = connection.execute_remote_command("; ".join(commands))
    answers = _answers(output)
    hashes: Dict[str, Optional[Dict[str, str]]] = {}
    for index, (path, is_directory) in enumerate(paths):
        lines, status = answers.get(index, ([], "missing"))
        found: Optional[Dict[str, str]] = {} if status == "0" else None
        for line in lines:
            # ``<digest>  <name>``; a leading backslash marks an escaped name.
            match = re.match(r"(\\?)([0-9a-f]{64}) [ *](.*)\Z", line)
            if found is None:
                continue
            if not match or match.group(1):
                found = None
                continue
            name = match.group(3)
            if not is_directory:
                found[""] = match.group(2)
            elif name.startswith("./"):
                found[name[2:]] = match.group(2)
            else:
                found = None
        hashes[path] = found
    return hashes


def _local_sha256(file: Path) -> str:
    stat = file.stat()
    key = (str(file), stat.st_size, stat.st_mtime_ns)
    if key not in _LOCAL_SHA256:
        _LOCAL_SHA256[key] = _digest_file(file, _COMPARED_BY)
    return _LOCAL_SHA256[key]


@dataclass
class _Comparison:
    """What comparing a local input with the cluster's same path found."""

    #: ``True`` identical, ``False`` different, ``None`` could not be told.
    same: Optional[bool]
    detail: str


def _compare_in_place(
    connection,
    candidates: Sequence[Tuple[Candidate, List[Tuple[str, Path]], int]],
) -> Dict[Path, _Comparison]:
    """Whether the cluster holds each candidate's data at the same path.

    Every candidate here exists on the cluster at its own absolute path --
    a file of the same size, or a directory. That is where a shared
    filesystem shows up, and also where a different dataset that merely
    lives at the same path does, so the path is not taken as evidence:

    * a file is the same when its SHA-256 is;
    * a directory is the same when it holds the same files -- apart from
      those left out here (credentials, ``no_data``, links), which may be
      there too -- each with the same size and the same SHA-256.

    Sizes are compared before anything is hashed, so a different dataset is
    usually found without reading it. Above ``_HASH_LIMIT_BYTES`` in all
    nothing is hashed, here or there -- the same limit, for the same reason,
    as naming an upload: a 500 GB dataset on a shared filesystem is not read
    twice, once of them on a login node, before every first call. Such an
    input is the same when its names and sizes are, and the plan says that
    is all that was compared. Three round trips at most, however
    many candidates there are, and none for an input already compared in
    this process.
    """
    where = _connection_key(connection)
    verdicts: Dict[Path, _Comparison] = {}
    keys: Dict[Path, tuple] = {}
    for candidate, files, _ in candidates:
        described = [
            (relative, file.stat().st_size, file.stat().st_mtime_ns)
            for relative, file in files
        ]
        keys[candidate.path] = (where, str(candidate.path), tuple(described))
        if keys[candidate.path] in _SAME_IN_PLACE:
            verdicts[candidate.path] = _Comparison(
                True, f"same {_COMPARED_BY}, compared earlier in this session"
            )
    pending = [entry for entry in candidates if entry[0].path not in verdicts]

    directories = [str(c.path) for c, _, _ in pending if c.path.is_dir()]
    listings = _remote_listing(connection, directories)
    to_hash: List[Tuple[Candidate, List[Tuple[str, Path]]]] = []
    for candidate, files, size in pending:
        too_big = size > _HASH_LIMIT_BYTES
        unread = _Comparison(
            True,
            f"same names and sizes; {_format_bytes(size)} is too much to "
            "read, so the contents were not compared",
        )
        if not candidate.path.is_dir():
            if too_big:
                verdicts[candidate.path] = unread
            else:
                to_hash.append((candidate, files))
            continue
        listing = listings[str(candidate.path)]
        if listing is None:
            verdicts[candidate.path] = _Comparison(
                None, "the cluster's copy could not be listed"
            )
            continue
        differences = _listing_differences(
            {relative: file.stat().st_size for relative, file in files},
            listing,
            # Present here and not staged: left out, not different.
            lambda relative: os.path.lexists(candidate.path / relative),
        )
        if differences:
            verdicts[candidate.path] = _Comparison(False, _some(differences))
        elif too_big:
            verdicts[candidate.path] = unread
        else:
            to_hash.append((candidate, files))

    hashes = _remote_hashes(
        connection, [(str(c.path), c.path.is_dir()) for c, _ in to_hash]
    )
    for candidate, files in to_hash:
        remote = hashes[str(candidate.path)]
        if remote is None:
            verdicts[candidate.path] = _Comparison(
                None, f"the cluster could not compute a {_COMPARED_BY} of its copy"
            )
            continue
        differences = [
            f"{relative or candidate.path.name} has different content"
            for relative, file in files
            if remote.get(relative) != _local_sha256(file)
        ]
        if differences:
            verdicts[candidate.path] = _Comparison(False, _some(differences))
        else:
            _SAME_IN_PLACE.add(keys[candidate.path])
            verdicts[candidate.path] = _Comparison(
                True,
                (
                    f"same size and {_COMPARED_BY}"
                    if not candidate.path.is_dir()
                    else f"same {len(files)} file(s) by size and {_COMPARED_BY}"
                ),
            )
    return verdicts


def _listing_differences(
    here: Dict[str, int],
    there: Dict[str, int],
    left_out_here: Callable[[str], bool],
) -> List[str]:
    """How two ``{relative path: size}`` listings of a directory differ.

    A file only on the cluster is a difference unless ``left_out_here``
    says this machine has it too and did not stage it.
    """
    differences = [
        f"{relative} is not there" for relative in sorted(set(here) - set(there))
    ]
    differences += [
        f"{relative} is only there"
        for relative in sorted(set(there) - set(here))
        if not left_out_here(relative)
    ]
    differences += [
        f"{relative} is {_format_bytes(there[relative])} there and "
        f"{_format_bytes(size)} here"
        for relative, size in sorted(here.items())
        if relative in there and there[relative] != size
    ]
    return differences


def _some(differences: List[str], shown: int = 5) -> str:
    more = len(differences) - shown
    return "; ".join(differences[:shown]) + (f"; and {more} more" if more > 0 else "")


def _connection_key(connection) -> tuple:
    """Which account on which cluster a connection reaches."""
    config = getattr(connection, "config", None)
    return (
        getattr(config, "cluster_host", None),
        getattr(config, "cluster_port", None),
        getattr(config, "username", None),
    )


def _private_store(connection, store: str) -> None:
    """Create the store, readable by its owner and nobody else.

    A local file that is ``0600`` must not become world-readable by being
    copied to a cluster, where the other accounts are other people. The
    store is ``0700`` whatever the remote umask says, and a store made by a
    version that left it open is closed -- once: its mode is how that is
    known.
    """
    quoted = shlex.quote(store)
    connection.execute_remote_command(
        f"umask 077; mkdir -p {quoted} && "
        f'{{ [ "$(stat -c %a {quoted} 2>/dev/null)" = 700 ] '
        f"|| chmod -R go-rwx {quoted}; }} && chmod 700 {quoted}",
        check=True,
    )


def _reserved_link(candidate: Candidate) -> Optional[str]:
    """The file of clustrix's own that linking ``candidate`` would replace."""
    link = _relative_link(candidate)
    if link is None:
        return None
    top = PurePosixPath(link).parts[0]
    return top if is_reserved_job_directory_name(top) else None


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
            "umask 077; mkdir -p "
            + " ".join(shlex.quote(d) for d in sorted(directories)),
            check=True,
        )
        created = True
        _send_files(connection, targets)
        connection.execute_remote_command(
            # Read-only and the owner's alone: directories 0500, files 0400.
            f"touch {quoted_incoming}/{_COMPLETE} "
            f"&& chmod -R u=rX,go= {quoted_incoming} || exit 1; "
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

    # Refused before anything is copied: linked into the job directory under
    # one of these names, the input would take the place of a file clustrix
    # writes there, and the job would fail on it with a bare PermissionError.
    reserved = [
        f"{candidate.written!r} ({candidate.found_in}) would be linked into "
        f"the job directory as {_reserved_link(candidate)!r}"
        for candidate, _, _ in candidates
        if _reserved_link(candidate)
    ]
    if reserved:
        raise StagingError(
            "An input's name collides with a file clustrix itself writes in "
            "every job directory: " + "; ".join(reserved) + ". Rename the "
            "input, pass it by an absolute path, or keep it out with "
            "no_data=[...]."
        )

    # Already on the cluster at the same path? Then nothing moves -- once
    # it has been shown to be the same data, not merely the same path.
    present = _remote_sizes(connection, [str(c.path) for c, _, _ in candidates])
    to_upload: List[Tuple[Candidate, List[Tuple[str, Path]], int]] = []
    reasons: Dict[Path, str] = {}
    there: List[Tuple[Candidate, List[Tuple[str, Path]], int]] = []
    for candidate, files, size in candidates:
        remote_size = present[str(candidate.path)]
        if remote_size is None:
            to_upload.append((candidate, files, size))
            reasons[candidate.path] = "not readable at that path on the cluster"
        elif candidate.path.is_file() and remote_size != size:
            raise StagingError(
                f"{candidate.path} exists on the cluster too, but as a "
                f"different file ({_format_bytes(remote_size)} there, "
                f"{_format_bytes(size)} here). {_WILL_NOT_GUESS}"
            )
        elif candidate.path.is_file() != (remote_size != -1):
            raise StagingError(
                f"{candidate.path} is a "
                f"{'file' if candidate.path.is_file() else 'directory'} here "
                f"and a {'directory' if remote_size == -1 else 'file'} on the "
                f"cluster. {_WILL_NOT_GUESS}"
            )
        else:
            there.append((candidate, files, size))
    compared = _compare_in_place(connection, there)
    for candidate, files, size in there:
        verdict = compared[candidate.path]
        if verdict.same is None:
            # No evidence either way is not evidence that it is the same.
            to_upload.append((candidate, files, size))
            reasons[candidate.path] = (
                f"something is at that path on the cluster, but {verdict.detail}"
            )
        elif not verdict.same:
            raise StagingError(
                f"{candidate.path} exists on the cluster too, but it is not "
                f"the same data: {verdict.detail}. {_WILL_NOT_GUESS}"
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
                    why=f"the cluster has it at the same path: {verdict.detail}",
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
        _private_store(connection, store)
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
                why=reasons[candidate.path],
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
            "Input %s (%s): %s%s%s%s",
            item.local,
            _format_bytes(item.size),
            item.how,
            "" if item.how == "in place" else f" as {item.remote}",
            f" ({item.why})" if item.why else "",
            "" if item.identified_by_content else f" ({_BY_METADATA})",
        )
    if replacements:
        args = tuple(_rewritten(value, replacements) for value in args)
        kwargs = {key: _rewritten(value, replacements) for key, value in kwargs.items()}
    return args, kwargs, plan


def staged_read_only(error: PermissionError, plan: DataPlan) -> Optional[str]:
    """What to add to a job's ``PermissionError`` if a staged input caused it.

    A path that exists here and is passed to a function looks the same
    whether the function reads it or is about to overwrite it -- last run's
    output file, say -- and it is staged as an input either way: a
    read-only copy. Writing to it then fails on the cluster with a bare
    ``Permission denied``, which says nothing about why a file the user can
    write at home cannot be written there. Returns the sentence that does,
    or ``None`` when the path in the error is not one this call staged.
    """
    for name in (error.filename, error.filename2):
        if not isinstance(name, (str, os.PathLike)):
            continue
        failed = os.path.normpath(os.fspath(name))
        for item in plan.inputs:
            if item.how == "in place":
                continue  # used where it is: nothing was made read-only
            for staged in filter(None, (item.link, item.remote)):
                absolute = os.path.isabs(staged)
                if (
                    failed == staged
                    or failed.startswith(staged + "/")
                    or (not absolute and failed.endswith("/" + staged))
                    or (not absolute and f"/{staged}/" in failed)
                ):
                    written = item.link or str(item.local)
                    return (
                        f"{written} exists on this machine, so it was staged "
                        "for the job as an input, and staged inputs are "
                        "read-only. If the job writes it, keep it out with "
                        f"@cluster(no_data=[{written!r}])"
                    )
    return None


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
