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

What this does not do, on purpose
---------------------------------

* **It never uploads what the user's own context did not put in scope.** A
  path found in code must be inside the project (the git top level, else
  the working directory) after resolving symlinks. A path passed as an
  argument is the user's explicit choice and may be anywhere. Credential
  files are refused in both cases. Nothing the *cluster* says can widen
  this: there is no "the job asked for this file" channel.
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
"""

import logging
import os
import shlex
import subprocess
import types
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
_PATH_LIKE_PARAMETER_WORDS = (
    "path",
    "file",
    "dir",
    "folder",
    "root",
    "input",
    "data",
    "csv",
    "checkpoint",
    "ckpt",
    "weights",
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


@dataclass
class DataPlan:
    """Everything a call's jobs need set up, and an account of it."""

    inputs: List[StagedInput] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def links(self) -> List[Tuple[str, str]]:
        """``(path relative to the job directory, remote target)`` pairs."""
        return [(item.link, item.remote) for item in self.inputs if item.link]

    def explain(self) -> str:
        if not self.inputs and not self.skipped:
            return "no local input files were found for this call"
        lines = []
        for item in self.inputs:
            lines.append(
                f"{item.local} ({_format_bytes(item.size)}, from {item.found_in}): "
                f"{item.how} -> {item.remote}"
            )
        lines.extend(self.skipped)
        return "\n".join(lines)


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
    excluded = [
        Path(os.path.abspath(os.path.expanduser(os.fspath(item)))) for item in exclude
    ]

    def add(written: str, found_in: str, explicit: bool) -> None:
        path = _as_local_path(written)
        if path is None:
            return
        absolute = Path(os.path.abspath(path))
        if any(absolute == out or out in absolute.parents for out in excluded):
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
        hinted = any(word in name.lower() for word in _PATH_LIKE_PARAMETER_WORDS)
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


def _files_under(path: Path) -> List[Tuple[str, Path]]:
    """``(relative posix path, file)`` for ``path`` or everything beneath it."""
    if path.is_file():
        return [("", path)]
    files = []
    for directory, subdirectories, names in os.walk(path, followlinks=False):
        subdirectories.sort()
        for name in sorted(names):
            file_path = Path(directory) / name
            if file_path.is_file() and not file_path.is_symlink():
                files.append((file_path.relative_to(path).as_posix(), file_path))
    return files


def _refusal(candidate: Candidate, root: Path) -> Optional[str]:
    """Why ``candidate`` may not be uploaded, or ``None`` if it may."""
    path = candidate.path
    if _is_sensitive_target(path):
        return f"{path} looks like a credential and is never uploaded"
    if not (path.is_file() or path.is_dir()):
        return f"{path} is not a regular file or directory"
    if not candidate.explicit:
        resolved = Path(os.path.realpath(path))
        if not resolved.is_relative_to(root):
            return (
                f"{path} ({candidate.found_in}) is outside the project at "
                f"{root}; pass it as an argument or list it in data=[...] "
                "if the job should have it"
            )
    return None


def _identity(path: Path, files: List[Tuple[str, Path]]) -> str:
    """A short name for this exact content, stable across calls."""
    if path.is_file():
        stat = path.stat()
        if stat.st_size <= _HASH_LIMIT_BYTES:
            return _digest_file(path)[:20]
        return _digest_bytes(f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}".encode())[
            :20
        ]
    listing = "\n".join(
        f"{relative}|{file.stat().st_size}|{file.stat().st_mtime_ns}"
        for relative, file in files
    )
    return _digest_bytes(listing.encode())[:20]


# --------------------------------------------------------------------------
# Staging: needs the cluster
# --------------------------------------------------------------------------


def _remote_sizes(connection, paths: Sequence[str]) -> Dict[str, Optional[int]]:
    """Size in bytes of each remote path that is a file; ``None`` otherwise.

    A directory reports ``-1``: present, size not comparable. One round trip
    for the whole list.
    """
    if not paths:
        return {}
    script = "; ".join(
        f"if [ -d {quoted} ]; then echo -1; "
        f"elif [ -f {quoted} ]; then wc -c < {quoted}; else echo missing; fi"
        for quoted in (shlex.quote(path) for path in paths)
    )
    output, _ = connection.execute_remote_command(script, check=True)
    sizes: Dict[str, Optional[int]] = {}
    for path, line in zip(paths, output.splitlines()):
        text = line.strip()
        sizes[path] = None if text == "missing" else int(text)
    return sizes


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
    """``value`` with every path in ``replacements`` swapped for its target."""
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
        return {
            key: _rewritten(item, replacements, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewritten(item, replacements, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_rewritten(item, replacements, depth + 1) for item in value)
    return value


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
    candidates = []
    for candidate in discover(func, args, kwargs, extra, exclude):
        refusal = _refusal(candidate, root)
        if refusal:
            # A file the user handed over and clustrix will not send is worth
            # saying; a string in the code that merely resembles a path is not.
            if candidate.explicit:
                logger.warning("Not staging: %s", refusal)
            plan.skipped.append(f"not staged: {refusal}")
            continue
        candidates.append(candidate)
    if not candidates:
        return args, kwargs, plan

    # Already on the cluster at the same path? Then nothing moves.
    present = _remote_sizes(connection, [str(c.path) for c in candidates])
    to_upload: List[Tuple[Candidate, List[Tuple[str, Path]], int]] = []
    for candidate in candidates:
        files = _files_under(candidate.path)
        size = sum(file.stat().st_size for _, file in files)
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
            plan.inputs.append(
                StagedInput(
                    candidate.path,
                    str(candidate.path),
                    size,
                    "in place",
                    candidate.found_in,
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

    store = (
        connection.resolve_remote_path(config.remote_work_dir).rstrip("/")
        + f"/{STORE_DIRECTORY}"
    )
    replacements: Dict[str, str] = {}
    for candidate, files, size in to_upload:
        base = f"{store}/{_identity(candidate.path, files)}/{candidate.path.name}"
        targets = [
            (f"{base}/{relative}" if relative else base, file)
            for relative, file in files
        ]
        already = _remote_sizes(connection, [target for target, _ in targets])
        missing = [
            (target, file)
            for target, file in targets
            if already[target] != file.stat().st_size
        ]
        if missing:
            directories = sorted({str(PurePosixPath(t).parent) for t, _ in missing})
            connection.execute_remote_command(
                "mkdir -p " + " ".join(shlex.quote(d) for d in directories),
                check=True,
            )
            for target, file in missing:
                # Uploaded under a temporary name and renamed, so a job that
                # starts while another call is mid-upload never reads half
                # a file, and an interrupted upload is not mistaken for one
                # that finished.
                partial = f"{target}.part-{os.getpid()}"
                connection.upload_file(str(file), partial)
                connection.execute_remote_command(
                    f"mv -f {shlex.quote(partial)} {shlex.quote(target)}", check=True
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
                "uploaded" if missing else "already uploaded",
                candidate.found_in,
                link=link,
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
            "Input %s (%s): %s%s",
            item.local,
            _format_bytes(item.size),
            item.how,
            "" if item.how == "in place" else f" as {item.remote}",
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
