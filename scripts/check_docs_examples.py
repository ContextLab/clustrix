#!/usr/bin/env python
"""Execute (or, for cluster/network-dependent examples, statically verify)
every Python code block in the project's published documentation -- both the
prose files and the docstrings those files publish through ``automodule`` /
``currentmodule``.

This exists because documentation drifts from the real API silently: a
module gets deleted, a function gets renamed, and nobody notices until a
user copy-pastes a broken example. Two documented bugs motivated this
script directly:

- ``MIGRATION.md`` asserted that ``ClusterConfig`` is *not* re-exported from
  ``clustrix/__init__.py``. It is, and always was in this checkout. That file
  documented a repository reorganization rather than the package, and has been
  deleted along with the rest of the version-to-version prose.
- ``docs/PRICING_API_REFERENCE.md`` and ``docs/PRICING_USER_GUIDE.md``
  documented a pricing-client API for the cloud backends. Neither the API
  nor the backends are part of the package, and both files have been
  deleted; the examples in them imported modules that cannot be imported.

Per code block:

- If its first non-blank line is a comment matching ``# cluster-required``
  (case-insensitive, optionally followed by a reason), the block is treated
  as needing real external infrastructure (a live cluster, cloud
  credentials, ...). It is never executed. It is still syntax-checked
  (``compile()``) and every module/name it imports is checked for existence
  against the real, installed ``clustrix`` package (and any other real
  import) via ``importlib``/``hasattr`` -- no mocks, no guessing.
- If its first non-blank line matches ``# some_module.py``, its content is
  written to that filename inside the current documentation file's shared
  scratch directory (so a later block in the same file can
  ``import some_module``), then executed like any other block.
- Otherwise, the block is executed for real with ``exec()``, in a shared
  namespace and shared scratch directory per documentation file (blocks
  within one file run in order, as if pasted into one session; state does
  not leak between different documentation files).

No block is ever mocked. Blocks that hit a live provider API (e.g. AWS/Azure
pricing) run for real and are expected to succeed via that provider's
already-real fallback behavior; a case that additionally needs a private
credential to be meaningful is marked ``# cluster-required`` instead.

Docstrings are published documentation too: ``docs/source/api/*.rst`` renders
them with ``automodule``/``currentmodule``, so a broken example in a docstring
reaches a reader exactly the same way a broken example in an ``.rst`` file
does. Three docstring examples were wrong while every ``.rst`` block passed:
one raised ``AttributeError`` when run, one used a name it never imported, and
one printed a result it does not produce (that last one is caught by ``python
-m doctest``, not by this script -- see the known limit below). Every module
named by an ``.. automodule::`` or ``.. currentmodule::`` directive
anywhere under ``docs/source`` is therefore scanned as well -- derived from the
directives, not hand-listed, so a new API page is covered the day it is added.

Inside a docstring, three things count as a Python example:

- a doctest run (``>>>`` / ``...`` lines). All of one docstring's examples are
  concatenated and executed as a single block, in a fresh copy of the owning
  module's globals -- the namespace ``doctest`` itself would use.
- an explicit ``.. code-block:: python`` directive.
- a literal block introduced by ``Example::``, ``Examples::`` or ``Usage::``.
  Only those three introducers: a literal block introduced by anything else is
  as likely to be shell or YAML, and guessing would manufacture noise rather
  than coverage. Write ``.. code-block:: python`` to have any other block
  checked.

Known limit: expected doctest output (the ``want`` after a ``>>>`` line) is
not compared. Blocks are executed and must not raise, which is the same
contract every ``.rst`` block is held to. Use ``python -m doctest <file>`` to
check the outputs themselves.

Usage::

    python scripts/check_docs_examples.py
"""

from __future__ import annotations

import ast
import contextlib
import doctest
import importlib
import inspect
import io
import json
import os
import signal
import subprocess
import re
import sys
import tempfile
import textwrap
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

CLUSTER_REQUIRED_RE = re.compile(r"^#\s*cluster-required\b\s*:?\s*(.*)$", re.IGNORECASE)
FILE_MARKER_RE = re.compile(r"^#\s*([A-Za-z_][A-Za-z0-9_]*\.py)\s*$")


@dataclass
class CodeBlock:
    source_file: Path
    line_no: int
    content: str


@dataclass
class TargetFile:
    path: Path
    kind: str  # "md", "rst" or "py" (docstrings)
    section_start: Optional[str] = None  # restrict extraction to a section
    section_end: Optional[str] = None
    module: Optional[str] = None  # importable name, for kind == "py"
    # Historical records are read, never run. A session note from a year ago
    # can contain a live cloud call; --include-notes exists to *inventory*
    # broken examples, not to execute whatever they happened to contain.
    never_execute: bool = False


@dataclass
class Result:
    block: CodeBlock
    mode: str  # "runnable" or "cluster-required"
    passed: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _restrict_to_section(
    text: str, start: Optional[str], end: Optional[str]
) -> tuple[str, int]:
    """Return (slice, line_offset) restricted to between two heading markers."""
    if start is None:
        return text, 0
    start_idx = text.index(start)
    if end is not None:
        end_idx = text.index(end, start_idx + len(start))
    else:
        end_idx = len(text)
    slice_text = text[start_idx:end_idx]
    line_offset = text[:start_idx].count("\n")
    return slice_text, line_offset


def extract_markdown_blocks(target: TargetFile) -> List[CodeBlock]:
    text = target.path.read_text()
    slice_text, line_offset = _restrict_to_section(
        text, target.section_start, target.section_end
    )
    blocks = []
    for m in re.finditer(
        r"^( *)```python\n(.*?)^\1```", slice_text, re.DOTALL | re.MULTILINE
    ):
        line_no = line_offset + slice_text[: m.start()].count("\n") + 1
        blocks.append(CodeBlock(target.path, line_no, textwrap.dedent(m.group(2))))
    return blocks


#: An explicit reST directive. Always Python, wherever it appears.
CODE_BLOCK_DIRECTIVE_RE = re.compile(r"^( *)\.\. code-block:: python\s*$")

#: A bare reST literal block. Only these introducers are assumed to be Python;
#: see the module docstring for why guessing on the rest would be worse than
#: not looking.
LITERAL_BLOCK_INTRO_RE = re.compile(r"^( *)(?:Examples?|Usage)::\s*$")


def _consume_indented_body(lines: List[str], i: int, indent: int) -> tuple[str, int]:
    """Return the block indented deeper than ``indent``, and the index after it."""
    # skip blank lines immediately after the introducer
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    raw_body: List[str] = []
    body_indent: Optional[int] = None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            raw_body.append("")
            i += 1
            continue
        cur_indent = len(line) - len(line.lstrip(" "))
        if cur_indent <= indent:
            break
        if body_indent is None:
            body_indent = cur_indent
        raw_body.append(line)
        i += 1
    body_lines = [
        (line[body_indent:] if body_indent and len(line) >= body_indent else line)
        for line in raw_body
    ]
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    return "\n".join(body_lines) + "\n", i


def _scan_rst_code_blocks(
    lines: List[str], include_literal_blocks: bool = False
) -> List[tuple[int, str]]:
    """Find Python blocks in reST text; returns (introducer index, content)."""
    found: List[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = CODE_BLOCK_DIRECTIVE_RE.match(lines[i])
        if m is None and include_literal_blocks:
            m = LITERAL_BLOCK_INTRO_RE.match(lines[i])
        if m is None:
            i += 1
            continue
        introducer = i
        content, i = _consume_indented_body(lines, i + 1, len(m.group(1)))
        found.append((introducer, content))
    return found


def extract_rst_blocks(target: TargetFile) -> List[CodeBlock]:
    text = target.path.read_text()
    slice_text, line_offset = _restrict_to_section(
        text, target.section_start, target.section_end
    )
    lines = slice_text.split("\n")
    return [
        CodeBlock(target.path, line_offset + introducer + 2, content)
        for introducer, content in _scan_rst_code_blocks(lines)
    ]


# ---------------------------------------------------------------------------
# Docstring extraction
# ---------------------------------------------------------------------------

_DOCTEST_PARSER = doctest.DocTestParser()


def _docstring_owners(tree: ast.Module):
    """Every node in a module that can carry a docstring, in source order."""
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            yield node


def extract_docstring_blocks(target: TargetFile) -> List[CodeBlock]:
    """Every Python example in every docstring of one documented module."""
    module = importlib.import_module(target.module or "")
    path = Path(inspect.getsourcefile(module) or module.__file__)
    tree = ast.parse(path.read_text())

    blocks: List[CodeBlock] = []
    for node in _docstring_owners(tree):
        # clean=False: doctest reads __doc__ verbatim, indentation and all.
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        # Line of the opening quote; line 0 of the docstring text sits on it.
        doc_start = node.body[0].lineno

        examples = _DOCTEST_PARSER.get_examples(doc)
        if examples:
            # One block per docstring: doctest runs a docstring's examples in
            # one shared namespace, and splitting them would break any example
            # that builds on the one above it.
            blocks.append(
                CodeBlock(
                    path,
                    doc_start + examples[0].lineno,
                    "".join(example.source for example in examples),
                )
            )

        for introducer, content in _scan_rst_code_blocks(
            doc.split("\n"), include_literal_blocks=True
        ):
            blocks.append(CodeBlock(path, doc_start + introducer + 1, content))

    blocks.sort(key=lambda block: block.line_no)
    return blocks


def extract_blocks(target: TargetFile) -> List[CodeBlock]:
    if target.kind == "md":
        return extract_markdown_blocks(target)
    if target.kind == "py":
        return extract_docstring_blocks(target)
    return extract_rst_blocks(target)


# ---------------------------------------------------------------------------
# Static ("cluster-required") verification
# ---------------------------------------------------------------------------


def _is_ours(module_name: str) -> bool:
    """True for modules this project is responsible for keeping importable.

    The point of this check is to catch OUR api drifting away from the docs --
    a renamed module, a deleted function. It cannot also demand that every
    third-party library an example mentions be installed on the machine running
    the check; a machine-learning example that imports tensorflow is not broken
    documentation just because tensorflow is absent here. Those are reported as
    unchecked rather than silently passed, so the gap is visible.
    """
    return module_name == "clustrix" or module_name.startswith("clustrix.")


def verify_static(block: CodeBlock) -> Result:
    try:
        compile(block.content, f"{block.source_file}:{block.line_no}", "exec")
    except SyntaxError as e:
        return Result(block, "cluster-required", False, f"SyntaxError: {e}")

    try:
        tree = ast.parse(block.content)
    except SyntaxError as e:
        return Result(block, "cluster-required", False, f"SyntaxError: {e}")

    problems = []
    skipped = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    importlib.import_module(alias.name)
                except ModuleNotFoundError:
                    if _is_ours(alias.name):
                        problems.append(f"import {alias.name}: module does not exist")
                    else:
                        skipped.append(alias.name)
                except Exception as e:
                    problems.append(f"import {alias.name}: {e}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import; not resolvable standalone
                continue
            module_name = node.module or ""
            try:
                mod = importlib.import_module(module_name)
            except ModuleNotFoundError:
                if _is_ours(module_name):
                    problems.append(
                        f"from {module_name} import ...: module does not exist"
                    )
                else:
                    skipped.append(module_name)
                continue
            except Exception as e:
                problems.append(f"from {module_name} import ...: {e}")
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if not hasattr(mod, alias.name):
                    problems.append(
                        f"from {module_name} import {alias.name}: "
                        f"{alias.name!r} does not exist on {module_name}"
                    )

    if problems:
        return Result(block, "cluster-required", False, "; ".join(problems))
    detail = "syntax + imports OK (not executed)"
    if skipped:
        detail += f"; not installed here, unchecked: {', '.join(sorted(set(skipped)))}"
    return Result(block, "cluster-required", True, detail)


# ---------------------------------------------------------------------------
# Real execution
# ---------------------------------------------------------------------------


#: A documentation example is meant to demonstrate something, not to run a
#: service. One in an operations guide started a monitoring loop and had to be
#: killed from outside, which stopped the whole check. An example that cannot
#: finish in this long is either not an example or needs marking.
BLOCK_TIMEOUT_SECONDS = 30


class BlockTimeout(Exception):
    """Raised when a documentation example outruns BLOCK_TIMEOUT_SECONDS."""


def _raise_block_timeout(signum, frame):  # pragma: no cover - signal handler
    raise BlockTimeout(
        f"example did not finish within {BLOCK_TIMEOUT_SECONDS}s; if it needs "
        f"a cluster, a service or credentials, mark it # cluster-required"
    )


def run_block(block: CodeBlock, namespace: dict, scratch_dir: Path) -> Result:
    file_marker = (
        FILE_MARKER_RE.match(block.content.strip().splitlines()[0])
        if block.content.strip()
        else None
    )
    if file_marker:
        target_name = file_marker.group(1)
        (scratch_dir / target_name).write_text(block.content)

    old_cwd = os.getcwd()
    stdout_buf = io.StringIO()
    previous_handler = signal.signal(signal.SIGALRM, _raise_block_timeout)
    signal.alarm(BLOCK_TIMEOUT_SECONDS)
    try:
        os.chdir(scratch_dir)
        with contextlib.redirect_stdout(stdout_buf):
            code = compile(
                block.content, f"{block.source_file}:{block.line_no}", "exec"
            )
            exec(code, namespace)
        return Result(block, "runnable", True, "executed OK")
    except SystemExit as e:
        # A documented example that calls sys.exit() would otherwise terminate
        # this checker mid-run, silently skipping every remaining file. That
        # happened: the run stopped after two files with exit status 0, which
        # reads exactly like success. An example is allowed to exit, but only
        # cleanly -- a non-zero status means the example itself failed.
        code_value = e.code if e.code is not None else 0
        if code_value == 0:
            return Result(block, "runnable", True, "executed OK (called sys.exit(0))")
        return Result(
            block, "runnable", False, f"example called sys.exit({code_value!r})"
        )
    except BaseException:
        tb = traceback.format_exc()
        return Result(
            block,
            "runnable",
            False,
            tb.strip().splitlines()[-1] if tb else "unknown error",
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def check_file(target: TargetFile) -> List[Result]:
    never_execute = target.never_execute
    blocks = extract_blocks(target)
    results: List[Result] = []

    # A prose file's blocks share one namespace: they read as one session, and
    # a later block routinely uses a name an earlier one bound. A docstring's
    # examples do not -- doctest gives each docstring a fresh copy of the
    # owning module's globals, and a docstring that only works because some
    # other docstring ran first is not a working example.
    if target.kind == "py":
        module_globals = importlib.import_module(target.module or "").__dict__

        def make_namespace() -> dict:
            return dict(module_globals)

    else:

        shared_namespace: dict = {"__name__": "__main__"}

        def make_namespace() -> dict:
            return shared_namespace

    with tempfile.TemporaryDirectory(prefix="clustrix_docs_check_") as tmp:
        scratch_dir = Path(tmp)
        sys.path.insert(0, str(scratch_dir))
        try:
            for block in blocks:
                first_line = (
                    block.content.strip().splitlines()[0]
                    if block.content.strip()
                    else ""
                )
                if never_execute or CLUSTER_REQUIRED_RE.match(first_line):
                    results.append(verify_static(block))
                else:
                    results.append(run_block(block, make_namespace(), scratch_dir))
        finally:
            sys.path.remove(str(scratch_dir))

    return results


#: Pages that need a narrower window than "the whole file". Keyed by path
#: relative to the repository root.
_SECTION_BOUNDS: dict = {}

#: Directories under docs/ that are build output or vendored, not sources.
_SKIP_DIRS = {"build", "_build", "_static", "_templates"}


def discover_targets() -> List[TargetFile]:
    """Every prose file in the repository that can carry a code example.

    Discovered rather than hand-listed. A hand-maintained list is the reason
    four newly written documentation pages went unchecked the moment they were
    added: nothing failed, because nothing looked. The same mistake has shown
    up three separate times in this project -- a reset fixture that named
    eight of a hundred fields, a secret-field set that named some of the
    credentials, a lint ignore list that had drifted from the shared config.
    Derive the list; do not curate it.
    """
    targets: List[TargetFile] = []
    for name in ("README.md",):
        if (REPO_ROOT / name).exists():
            targets.append(TargetFile(REPO_ROOT / name, "md"))

    # Scope: everything Sphinx publishes, plus the user-facing files at the
    # repository root. That is a principled boundary rather than a curated
    # list -- if a reader can reach it from the built documentation, its
    # examples are a promise and must hold.
    #
    # Deliberately NOT enforced: the development notes elsewhere under docs/
    # (session logs, design analyses, issue write-ups). Those are historical
    # records of what someone believed at the time, and several contain code
    # that never ran. Rewriting them would falsify the record; they are
    # inventoried in the session notes instead. Run with --include-notes to
    # see them.
    docs_root = REPO_ROOT / "docs"
    targets.extend(_discover_under(docs_root / "source"))

    if "--include-notes" in sys.argv:
        # Inventoried, never executed. These files record what someone
        # believed at the time; one of them makes a live AWS API call, and
        # running a year-old example to find out whether it still parses is
        # not a trade worth making.
        for note in _discover_under(docs_root):
            if note.path.is_relative_to(docs_root / "source"):
                continue
            note.never_execute = True
            targets.append(note)

    targets.extend(_discover_documented_modules(docs_root / "source"))
    return targets


#: ``.. automodule:: X`` / ``.. currentmodule:: X`` -- the two directives that
#: put a module's docstrings on a published page.
MODULE_DIRECTIVE_RE = re.compile(
    r"^\s*\.\.\s+(?:auto|current)module::\s+(\S+)\s*$", re.MULTILINE
)


def _discover_documented_modules(scan_root: Path) -> List[TargetFile]:
    """Every module whose docstrings Sphinx publishes, read off the pages.

    Derived from the directives rather than listed, for the same reason the
    prose files are: a hand-maintained list stops covering things silently.
    """
    modules = set()
    for path in sorted(scan_root.rglob("*.rst")):
        if any(part in _SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        modules.update(MODULE_DIRECTIVE_RE.findall(path.read_text()))

    targets: List[TargetFile] = []
    for name in sorted(modules):
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            # A page publishes a module that does not import. Nothing further
            # can be checked and the docs are already broken; say so and stop.
            raise SystemExit(f"documented module {name!r} cannot be imported: {exc}")
        source = inspect.getsourcefile(module)
        if source is None:  # pragma: no cover - namespace/extension modules
            continue
        source_path = Path(source).resolve()
        # The docs in this checkout are only meaningfully checked against the
        # code in this checkout. If `clustrix` imports from somewhere else --
        # a non-editable `pip install .` earlier in the same CI job puts it in
        # site-packages -- then every docstring example read here belongs to a
        # different copy, and a pass would mean nothing. Say so plainly
        # instead of failing 20 frames down inside `Path.relative_to`.
        if not source_path.is_relative_to(REPO_ROOT):
            raise SystemExit(
                f"documented module {name!r} imports from {source_path}, which "
                f"is outside this checkout ({REPO_ROOT}). The examples here "
                f"would be checked against a different copy of the code. "
                f"Install the package editable (pip install -e .) or run this "
                f"before a non-editable install."
            )
        targets.append(TargetFile(source_path, "py", module=name))
    return targets


def _discover_under(scan_root: Path) -> List[TargetFile]:
    found: List[TargetFile] = []
    if not scan_root.exists():
        return found
    for path in sorted(scan_root.rglob("*")):
        if path.suffix not in (".rst", ".md"):
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        start, end = _SECTION_BOUNDS.get(rel, (None, None))
        found.append(
            TargetFile(
                path,
                "rst" if path.suffix == ".rst" else "md",
                section_start=start,
                section_end=end,
            )
        )
    return found


#: How long one documentation file gets to have all its blocks checked. The
#: in-process SIGALRM below is a courtesy; this is the guarantee. paramiko
#: retries through EINTR, so an unmarked example that dials a fictitious host
#: ignored the alarm and hung the whole run indefinitely. A file is checked in
#: its own process so that no example can do that again.
FILE_TIMEOUT_SECONDS = 120


def _check_file_in_subprocess(target: TargetFile) -> List[Result]:
    """Run one file's checks in a child process, so a hang cannot spread."""
    payload = json.dumps(
        {
            "path": str(target.path),
            "kind": target.kind,
            "section_start": target.section_start,
            "section_end": target.section_end,
            "module": target.module,
            "never_execute": target.never_execute,
        }
    )
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--check-one", payload],
            capture_output=True,
            text=True,
            timeout=FILE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        blocks = extract_blocks(target)
        return [
            Result(
                b,
                "runnable",
                False,
                f"file exceeded {FILE_TIMEOUT_SECONDS}s; an example is most "
                f"likely waiting on a network call and needs # cluster-required",
            )
            for b in blocks
        ] or [
            Result(
                CodeBlock(target.path, 0, ""),
                "runnable",
                False,
                f"file exceeded {FILE_TIMEOUT_SECONDS}s",
            )
        ]

    blocks = extract_blocks(target)
    try:
        decoded = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception:
        return [
            Result(
                b,
                "runnable",
                False,
                f"checker subprocess failed: {completed.stderr.strip()[-200:]}",
            )
            for b in blocks
        ]
    return [
        Result(blocks[d["index"]], d["mode"], d["passed"], d["detail"])
        for d in decoded
        if d["index"] < len(blocks)
    ]


def _check_one_entry(payload: str) -> int:
    """Child-process entry point: check one file, print results as JSON."""
    spec = json.loads(payload)
    target = TargetFile(
        Path(spec["path"]),
        spec["kind"],
        section_start=spec["section_start"],
        section_end=spec["section_end"],
        module=spec.get("module"),
        never_execute=spec.get("never_execute", False),
    )
    results = check_file(target)
    print(
        json.dumps(
            [
                {
                    "index": i,
                    "mode": r.mode,
                    "passed": r.passed,
                    "detail": r.detail,
                }
                for i, r in enumerate(results)
            ]
        )
    )
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--check-one":
        return _check_one_entry(sys.argv[2])

    targets = discover_targets()

    all_results: List[Result] = []
    for target in targets:
        if not target.path.exists():
            print(f"SKIP (missing): {target.path}")
            continue
        results = _check_file_in_subprocess(target)
        all_results.extend(results)
        rel = target.path.relative_to(REPO_ROOT)
        label = f"{rel} (docstrings)" if target.kind == "py" else str(rel)
        print(f"\n=== {label} ({len(results)} block(s)) ===")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            tag = "[cluster-required]" if r.mode == "cluster-required" else "[runnable]"
            print(f"  {status} {tag} line {r.block.line_no}: {r.detail}")

    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    runnable = sum(1 for r in all_results if r.mode == "runnable")
    cluster_required = total - runnable

    print(
        f"\n{total} block(s) checked: {passed} passed, {failed} failed "
        f"({runnable} executed for real, {cluster_required} statically verified "
        f"as cluster/network-required)."
    )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
