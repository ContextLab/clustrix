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

Notebooks under ``docs/source`` are published documentation too -- nbsphinx
renders them into the same site -- and until #166 not one of their cells was
looked at. They are discovered by the same walk that finds the ``.rst`` and
``.md`` files, so a notebook added tomorrow is covered tomorrow, and each one
is handled as a unit:

- Every ordinary code cell is compiled, and every module/name it imports is
  checked against the real installed package, exactly as an ``.rst`` block is.
  Keyword arguments passed to ``clustrix`` callables are checked against the
  real package too, which is what catches a parameter the package has since
  removed (``@cluster(queue=...)``, #158).
- A notebook is executed end to end, in a clean kernel, only when it is
  genuinely self-contained: no cell marked ``# cluster-required``, and no
  unmarked cell that would reach a real host or a paid provider. Anything else
  is verified statically and never run.
- An unmarked cell that *would* reach a host is a failure, not a silent skip.
  Marking it ``# cluster-required`` -- the same convention the ``.rst`` blocks
  use -- is the fix.

Known limits, stated rather than papered over:

- Expected doctest output (the ``want`` after a ``>>>`` line) is not compared.
  Blocks are executed and must not raise, which is the same contract every
  ``.rst`` block is held to. Use ``python -m doctest <file>`` to check the
  outputs themselves.
- A notebook's stored output is compared to a fresh run by *shape*, not by
  text: stored tracebacks, an execution-count sequence that is not one clean
  top-to-bottom run, and a change in which kinds of output a cell produces all
  fail. The literal text is not compared, because timings, hostnames, temp
  paths, ``os.cpu_count()``, ``multiprocessing.get_start_method()`` and object
  addresses all legitimately differ between two correct runs on two machines,
  and masking the numbers still leaves ``Darwin``/``Linux`` and
  ``spawn``/``fork`` differing. A check that fails on a correct notebook gets
  switched off, and this project already has three guards that were disabled
  or worked around because they misfired.

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
    #: Notebook cells are addressed by cell index, not by line number.
    cell_index: Optional[int] = None
    #: Anything the extractor had to do to the source to make it Python --
    #: stripping an IPython magic, say. Reported, never hidden.
    note: str = ""

    @property
    def where(self) -> str:
        if self.cell_index is not None:
            return f"cell {self.cell_index}"
        return f"line {self.line_no}"


@dataclass
class TargetFile:
    path: Path
    kind: str  # "md", "rst", "ipynb" or "py" (docstrings)
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
    mode: str  # "runnable", "cluster-required" or "output"
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
    if target.kind == "ipynb":
        return extract_notebook_blocks(target)
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


def verify_static(block: CodeBlock, mode: str = "cluster-required") -> Result:
    try:
        compile(block.content, f"{block.source_file}:{block.line_no}", "exec")
    except SyntaxError as e:
        return Result(block, mode, False, f"SyntaxError: {e}")

    try:
        tree = ast.parse(block.content)
    except SyntaxError as e:
        return Result(block, mode, False, f"SyntaxError: {e}")

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

    problems.extend(check_clustrix_call_keywords(tree))

    if problems:
        return Result(block, mode, False, "; ".join(problems))
    detail = "syntax + imports OK (not executed)"
    if skipped:
        detail += f"; not installed here, unchecked: {', '.join(sorted(set(skipped)))}"
    if block.note:
        detail += f"; {block.note}"
    return Result(block, mode, True, detail)


# ---------------------------------------------------------------------------
# Keyword-argument verification against the real package
# ---------------------------------------------------------------------------

#: ``@cluster`` and ``configure`` both take ``**kwargs``, so a keyword the
#: package has removed is not a ``TypeError`` -- it is accepted and quietly
#: does nothing (``@cluster(queue=...)``, #158) or is rejected by a validator
#: (``configure``). Neither shows up in a signature, so the checker asks the
#: real package instead of keeping its own copy of the answer: it replays the
#: call site's keyword *names* against the installed clustrix and reports
#: whatever clustrix says about them. No mock, no second source of truth.
_CLUSTRIX_KEYWORD_PROBES = ("cluster", "configure")


def _called_name(func: ast.expr) -> Optional[str]:
    """The trailing identifier of a call target: ``a.b.c(...)`` -> ``"c"``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _probe_cluster_keywords(names: List[str]) -> List[str]:
    """Ask the real ``@cluster`` which of these keyword names it recognises."""
    import logging

    import clustrix

    captured: List[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # pragma: no cover - trivial
            captured.append(record.getMessage())

    def _probe():
        return None

    # The probe calls a trivial local function, never the example's own code,
    # and it does so with the configuration pinned to local execution. Without
    # the pin it inherited whatever the surrounding page had already
    # configured -- and on ``ssh_setup.rst`` that was a real host, so the
    # keyword check opened an SSH connection. Pin, probe, restore.
    from clustrix.config import configure, get_config

    config = get_config()
    saved = (config.cluster_type, config.cluster_host)
    handler = _Capture()
    logger = logging.getLogger("clustrix")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        configure(cluster_type="local", cluster_host=None)
        clustrix.cluster(**{name: None for name in names})(_probe)()
    except TypeError as exc:
        return [f"@cluster({', '.join(names)}): {exc}"]
    finally:
        configure(cluster_type=saved[0], cluster_host=saved[1])
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    # Only the unrecognised-option report. clustrix has other "has no effect"
    # warnings -- a stale ``default_queue`` in the developer's own config file
    # emits one -- and reporting those here would blame the documentation for
    # the machine it was checked on.
    return [message for message in captured if "unrecognised option" in message]


def _probe_configure_keywords(names: List[str]) -> List[str]:
    """Ask the real ``configure()`` which of these keyword names it accepts.

    Each name is replayed with the value the live config already holds, so a
    name clustrix accepts is a no-op write and a name it does not accept
    raises exactly the error a reader running the cell would see.
    """
    from clustrix.config import configure, get_config

    config = get_config()
    problems = []
    for name in names:
        try:
            configure(**{name: getattr(config, name, None)})
        except Exception as exc:
            problems.append(f"configure({name}=...): {exc}")
    return problems


def check_clustrix_call_keywords(tree: ast.AST) -> List[str]:
    """Every keyword a call site passes to a clustrix callable, verified.

    Only calls whose trailing identifier names a real clustrix callable are
    probed; the resolution is deliberately by name, because that is how a
    reader reads the page -- ``@cluster(...)`` means clustrix's decorator on
    every documentation page in this repository.
    """
    problems: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node.func)
        if name not in _CLUSTRIX_KEYWORD_PROBES:
            continue
        keywords = [kw.arg for kw in node.keywords if kw.arg is not None]
        if not keywords:
            continue
        if name == "cluster":
            problems.extend(_probe_cluster_keywords(keywords))
        else:
            problems.extend(_probe_configure_keywords(keywords))
    return problems


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
# Notebooks
# ---------------------------------------------------------------------------


#: A notebook cell gets the same budget a prose example does, times four: a
#: tutorial cell legitimately runs a benchmark. The subprocess timeout around
#: the whole file is still the guarantee -- see FILE_TIMEOUT_SECONDS.
NOTEBOOK_CELL_TIMEOUT_SECONDS = 120

#: Whole-notebook budget. Larger than FILE_TIMEOUT_SECONDS because a notebook
#: is many cells and one of them is allowed to be a benchmark.
NOTEBOOK_TIMEOUT_SECONDS = 600

#: The kernel is named and built here rather than borrowed from whatever
#: ``python3`` kernelspec happens to be installed. Borrowing it ran the
#: notebooks against an interpreter that did not have clustrix at all, and
#: every cell "failed" with ModuleNotFoundError -- a checker that reports a
#: broken environment as broken documentation is worse than no checker.
NOTEBOOK_KERNEL_NAME = "clustrix-docs-check"

CELL_MAGIC_RE = re.compile(r"^\s*%%(\S+)")
LINE_MAGIC_RE = re.compile(r"^(\s*)[%!]\S.*$")

#: Cell magics whose body clustrix itself runs as Python (see
#: ``notebook_magic_core.remote``), so the body is real, checkable code.
CLUSTRIX_CELL_MAGICS = {"remote", "clusterfy"}

#: Calls that reach a real machine or a paid provider the moment they run. A
#: cell containing one is never executed; it must be marked
#: ``# cluster-required`` so that its intent is on the page, not inferred.
#:
#: Constructing a ``ClusterExecutor`` is deliberately NOT on this list --
#: ``__init__`` builds sub-managers and returns, and ``complete_api_demo``
#: builds two of them purely to print their methods. Listing it cost that
#: notebook every one of its eighteen executed cells for no safety gain, which
#: is the failure mode this whole check has to avoid.
REMOTE_ACTION_CALLS = {
    "setup_ssh_keys",
    "setup_ssh_keys_with_fallback",
    "connect",
    "_execute_command",
    "submit_job",
}


def _cell_to_python(source: str) -> tuple[str, str]:
    """Return (checkable Python, note) for one notebook cell's source.

    IPython magics and shell escapes are not Python and ``compile()`` rejects
    them. They are removed rather than guessed at, and what was removed is
    returned so it is reported instead of silently dropped.
    """
    lines = source.split("\n")
    notes: List[str] = []

    first_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_index is not None:
        cell_magic = CELL_MAGIC_RE.match(lines[first_index])
        if cell_magic:
            magic_name = cell_magic.group(1)
            if magic_name in CLUSTRIX_CELL_MAGICS:
                notes.append(f"%%{magic_name} body checked as Python")
                lines = lines[first_index + 1 :]
            else:
                return "", (
                    f"cell magic %%{magic_name}: its body is not necessarily "
                    f"Python and is NOT verified"
                )

    stripped = 0
    rewritten = []
    for line in lines:
        line_magic = LINE_MAGIC_RE.match(line)
        if line_magic:
            rewritten.append(f"{line_magic.group(1)}pass")
            stripped += 1
        else:
            rewritten.append(line)
    if stripped:
        notes.append(f"{stripped} IPython magic/shell line(s) not verified")

    return "\n".join(rewritten) + "\n", "; ".join(notes)


#: Magics and escapes that change the machine rather than demonstrate the
#: library. nbclient runs a cell's *original* source, so the ``pass`` the
#: extractor substitutes for compilation would not stop a real ``!pip
#: install`` from running -- a notebook containing one is not executed at all.
#: Ordinary line magics (``%time``, ``%matplotlib``) are left alone: they are
#: safe, they are common, and refusing them would cost real coverage.
SHELL_ESCAPE_RE = re.compile(r"^\s*!")
INSTALLER_MAGIC_RE = re.compile(r"^\s*%(pip|conda)\b")


def _unsafe_magic_reason(source: str) -> Optional[str]:
    """Why this cell must not be handed to a kernel, or None."""
    lines = source.split("\n")
    for line in lines:
        if SHELL_ESCAPE_RE.match(line):
            return f"runs a shell command ({line.strip()[:40]!r})"
        if INSTALLER_MAGIC_RE.match(line):
            return f"installs packages ({line.strip()[:40]!r})"
    first_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return None
    cell_magic = CELL_MAGIC_RE.match(lines[first_index])
    if cell_magic and cell_magic.group(1) not in CLUSTRIX_CELL_MAGICS:
        return f"uses the %%{cell_magic.group(1)} cell magic"
    return None


def _read_notebook(path: Path) -> dict:
    return json.loads(path.read_text())


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else source


def extract_notebook_blocks(target: TargetFile) -> List[CodeBlock]:
    """One block per non-empty code cell, in document order."""
    blocks: List[CodeBlock] = []
    for index, cell in enumerate(_read_notebook(target.path).get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        source = _cell_source(cell)
        if not source.strip():
            continue
        content, note = _cell_to_python(source)
        blocks.append(
            CodeBlock(
                target.path,
                index,
                content,
                cell_index=index,
                note=note,
            )
        )
    return blocks


def _remote_action_reason(block: CodeBlock) -> Optional[str]:
    """Why this cell would reach a real host, or None if it would not."""
    try:
        tree = ast.parse(block.content)
    except SyntaxError:
        return None  # reported by verify_static instead
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node.func)
        if name in REMOTE_ACTION_CALLS:
            return f"calls {name}(), which connects to a real host"
        if name != "configure":
            continue
        for keyword in node.keywords:
            value = keyword.value
            is_none = isinstance(value, ast.Constant) and value.value is None
            if keyword.arg == "cluster_host" and not is_none:
                return (
                    "calls configure(cluster_host=...), which points every "
                    "later cell at a real host"
                )
            if (
                keyword.arg == "cluster_type"
                and isinstance(value, ast.Constant)
                and value.value != "local"
            ):
                return (
                    f"calls configure(cluster_type={value.value!r}), which is "
                    f"not local execution"
                )
    return None


def _is_cluster_required(block: CodeBlock) -> bool:
    lines = block.content.strip().splitlines()
    return bool(lines) and bool(CLUSTER_REQUIRED_RE.match(lines[0]))


def _write_kernelspec(root: Path) -> None:
    """A kernelspec that runs *this* interpreter, so the package under test wins."""
    spec_dir = root / "kernels" / NOTEBOOK_KERNEL_NAME
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "kernel.json").write_text(
        json.dumps(
            {
                "argv": [
                    sys.executable,
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    "{connection_file}",
                ],
                "display_name": NOTEBOOK_KERNEL_NAME,
                "language": "python",
            }
        )
    )


def _execute_notebook(path: Path) -> tuple:
    """Run a notebook in a clean kernel; return (executed copy, failure or None).

    The kernel gets an empty ``CLUSTRIX_CONFIG_DIR``. Without it the notebooks
    pick up whatever is in the developer's ``~/.clustrix/``: one of them
    printed ``Cluster type: slurm`` on this machine and ``local`` in CI, from
    the same source.
    """
    import nbformat
    from nbclient import NotebookClient

    notebook = nbformat.read(str(path), as_version=4)
    with tempfile.TemporaryDirectory(prefix="clustrix_nb_jupyter_") as jupyter_root:
        with tempfile.TemporaryDirectory(prefix="clustrix_nb_run_") as workdir:
            with tempfile.TemporaryDirectory(prefix="clustrix_nb_cfg_") as config_dir:
                _write_kernelspec(Path(jupyter_root))
                os.environ["JUPYTER_PATH"] = jupyter_root
                os.environ["CLUSTRIX_CONFIG_DIR"] = config_dir
                client = NotebookClient(
                    notebook,
                    timeout=NOTEBOOK_CELL_TIMEOUT_SECONDS,
                    kernel_name=NOTEBOOK_KERNEL_NAME,
                    allow_errors=True,
                    resources={"metadata": {"path": workdir}},
                )
                try:
                    client.execute()
                except Exception as exc:
                    # A cell that outruns its budget, or a kernel that dies,
                    # aborts the run. Report it as a failure of this notebook
                    # rather than letting it escape and be reported as "the
                    # checker subprocess failed", which says nothing useful.
                    return notebook, f"{type(exc).__name__}: {exc}"
    return notebook, None


def _output_shape(outputs) -> List[str]:
    """What kinds of output a cell produced, with stream chunking collapsed.

    A kernel is free to split one ``print`` run across several stream
    messages, so the count of stream outputs is noise; which *kinds* of output
    a cell produces is not.
    """
    shape: List[str] = []
    for output in outputs:
        kind = output.get("output_type")
        key = f"stream:{output.get('name')}" if kind == "stream" else str(kind)
        if shape and shape[-1] == key and kind == "stream":
            continue
        shape.append(key)
    return shape


def _stored_output_problems(path: Path) -> List[tuple[int, str]]:
    """Staleness a notebook's *stored* output shows on its own, without running.

    Two signals, both of which are facts about the file rather than
    comparisons against a second run, so neither can differ between machines.
    """
    problems: List[tuple[int, str]] = []
    cells = [
        (index, cell)
        for index, cell in enumerate(_read_notebook(path).get("cells", []))
        if cell.get("cell_type") == "code" and _cell_source(cell).strip()
    ]

    for index, cell in cells:
        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                problems.append(
                    (
                        index,
                        f"stored output is a traceback "
                        f"({output.get('ename')}: {output.get('evalue')}); the "
                        f"published page shows this cell failing",
                    )
                )

    if not any(cell.get("outputs") for _, cell in cells):
        # Nothing is claimed, so nothing can be stale.
        return problems

    counts = [cell.get("execution_count") for _, cell in cells]
    if counts != list(range(1, len(counts) + 1)):
        problems.append(
            (
                cells[0][0] if cells else 0,
                f"stored output did not come from one clean top-to-bottom run: "
                f"execution counts are {counts}, expected "
                f"{list(range(1, len(counts) + 1))}. Restart the kernel, run "
                f"all, and save",
            )
        )
    return problems


def _stale_output_problems(path: Path, executed) -> List[tuple[int, str]]:
    """Stored output versus a fresh run, compared by shape (see module docstring)."""
    stored = {
        index: cell
        for index, cell in enumerate(_read_notebook(path).get("cells", []))
        if cell.get("cell_type") == "code" and _cell_source(cell).strip()
    }
    if not any(cell.get("outputs") for cell in stored.values()):
        return []

    problems: List[tuple[int, str]] = []
    for index, cell in stored.items():
        fresh_cell = executed.cells[index]
        was = _output_shape(cell.get("outputs", []))
        now = _output_shape(fresh_cell.get("outputs", []))
        if was != now:
            problems.append(
                (
                    index,
                    f"stored output is stale: the notebook ships {was or 'no'} "
                    f"output for this cell, a fresh run produces "
                    f"{now or 'none'}",
                )
            )
    return problems


def _ordered(results: List[Result], summary: Result) -> List[Result]:
    """Notebook-level verdict first, then per-cell results in document order."""
    results.sort(key=lambda r: (r.block.cell_index or 0))
    return [summary] + results


def check_notebook(target: TargetFile, blocks: List[CodeBlock]) -> List[Result]:
    """Compile every cell; execute the whole notebook when that is safe."""
    results: List[Result] = []
    by_cell = {block.cell_index: block for block in blocks}
    first_block = blocks[0] if blocks else CodeBlock(target.path, 0, "", cell_index=0)

    def attach(cell_index: Optional[int], mode: str, passed: bool, detail: str) -> None:
        block = (
            by_cell.get(cell_index, first_block)
            if cell_index is not None
            else first_block
        )
        results.append(Result(block, mode, passed, detail))

    static = [verify_static(block) for block in blocks]

    raw = {
        index: _cell_source(cell)
        for index, cell in enumerate(_read_notebook(target.path).get("cells", []))
    }
    unsafe_magic = [
        (block, reason)
        for block in blocks
        if (reason := _unsafe_magic_reason(raw.get(block.cell_index, ""))) is not None
    ]

    marked = [block for block in blocks if _is_cluster_required(block)]
    unmarked_remote = [
        (block, reason)
        for block in blocks
        if not _is_cluster_required(block)
        and (reason := _remote_action_reason(block)) is not None
    ]
    broken = [result for result in static if not result.passed]

    reasons = []
    if target.never_execute:
        reasons.append("inventory only")
    if marked:
        cells = ", ".join(str(block.cell_index) for block in marked)
        reasons.append(
            f"cell(s) {cells} are marked # cluster-required, so a run would "
            f"skip state every later cell depends on"
        )
    if unmarked_remote:
        reasons.append(
            f"{len(unmarked_remote)} unmarked cell(s) would reach a real host"
        )
    if unsafe_magic:
        cells = ", ".join(str(block.cell_index) for block, _ in unsafe_magic)
        reasons.append(
            f"cell(s) {cells} would change this machine rather than demonstrate "
            f"the library ({unsafe_magic[0][1]})"
        )
    if broken:
        reasons.append(f"{len(broken)} cell(s) do not compile or reference dead API")

    for block, reason in unmarked_remote:
        attach(
            block.cell_index,
            "cluster-required",
            False,
            f"{reason}, but the cell is not marked. Add a "
            f"'# cluster-required' first line so it is verified statically "
            f"instead of run",
        )

    if reasons:
        if not (target.never_execute or marked or unmarked_remote or unsafe_magic):
            # Held back only because something in it does not compile or
            # names dead API -- nothing to do with a cluster. Say that.
            for result in static:
                result.mode = "static"
        results.extend(static)
        for cell_index, detail in _stored_output_problems(target.path):
            attach(cell_index, "output", False, detail)
        return _ordered(
            results,
            Result(
                first_block,
                "cluster-required",
                True,
                f"notebook not executed: {'; '.join(reasons)}",
            ),
        )

    executed, failure = _execute_notebook(target.path)
    if failure is not None:
        results.extend(static)
        for cell_index, detail in _stored_output_problems(target.path):
            attach(cell_index, "output", False, detail)
        return _ordered(
            results,
            Result(
                first_block,
                "runnable",
                False,
                f"notebook did not run to completion: {failure}. A cell that "
                f"cannot finish inside {NOTEBOOK_CELL_TIMEOUT_SECONDS}s is "
                f"either not an example or needs marking # cluster-required",
            ),
        )

    for block in blocks:
        cell = executed.cells[block.cell_index]
        errors = [
            output
            for output in cell.get("outputs", [])
            if output.get("output_type") == "error"
        ]
        if errors:
            error = errors[0]
            attach(
                block.cell_index,
                "runnable",
                False,
                f"{error.get('ename')}: {error.get('evalue')}",
            )
        else:
            detail = "executed OK in a clean kernel"
            if block.note:
                detail += f"; {block.note}"
            attach(block.cell_index, "runnable", True, detail)

    for cell_index, detail in _stored_output_problems(target.path):
        attach(cell_index, "output", False, detail)
    for cell_index, detail in _stale_output_problems(target.path, executed):
        attach(cell_index, "output", False, detail)

    return _ordered(
        results,
        Result(
            first_block,
            "runnable",
            True,
            f"notebook executed end to end in a clean kernel "
            f"({len(blocks)} cell(s))",
        ),
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def check_file(
    target: TargetFile, blocks: Optional[List[CodeBlock]] = None
) -> List[Result]:
    never_execute = target.never_execute
    if blocks is None:
        blocks = extract_blocks(target)
    if target.kind == "ipynb":
        return check_notebook(target, blocks)
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

#: Directories under docs/ that are build output, checkpoints or vendored,
#: not sources.
_SKIP_DIRS = {"build", "_build", "_static", "_templates", ".ipynb_checkpoints"}

#: Suffix -> TargetFile.kind. Notebooks are here rather than in a list of
#: their own for the reason the module docstring gives: the seven notebooks
#: this project publishes went unchecked because discovery walked for ``.rst``
#: and ``.md`` and stopped there.
_PROSE_SUFFIXES = {".rst": "rst", ".md": "md", ".ipynb": "ipynb"}


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
        if path.suffix not in _PROSE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        start, end = _SECTION_BOUNDS.get(rel, (None, None))
        found.append(
            TargetFile(
                path,
                _PROSE_SUFFIXES[path.suffix],
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


def _timeout_for(target: TargetFile) -> int:
    return NOTEBOOK_TIMEOUT_SECONDS if target.kind == "ipynb" else FILE_TIMEOUT_SECONDS


def _run_child(payload: str, timeout: int) -> tuple[str, str]:
    """Run the child in its own process group and kill the whole group on timeout.

    A notebook's kernel is a grandchild of this process. Killing only the
    direct child would leave the kernel running and holding whatever the
    hanging cell was waiting on.
    """
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--check-one", payload],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover
            process.kill()
        process.communicate()
        raise


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
    timeout = _timeout_for(target)
    try:
        stdout, stderr = _run_child(payload, timeout)
    except subprocess.TimeoutExpired:
        blocks = extract_blocks(target)
        return [
            Result(
                b,
                "runnable",
                False,
                f"file exceeded {timeout}s; an example is most "
                f"likely waiting on a network call and needs # cluster-required",
            )
            for b in blocks
        ] or [
            Result(
                CodeBlock(target.path, 0, ""),
                "runnable",
                False,
                f"file exceeded {timeout}s",
            )
        ]

    blocks = extract_blocks(target)
    try:
        decoded = json.loads(stdout.strip().splitlines()[-1])
    except Exception:
        return [
            Result(
                b,
                "runnable",
                False,
                f"checker subprocess failed: {stderr.strip()[-200:]}",
            )
            for b in blocks
        ] or [
            Result(
                CodeBlock(target.path, 0, ""),
                "runnable",
                False,
                f"checker subprocess failed: {stderr.strip()[-200:]}",
            )
        ]
    return [
        Result(blocks[d["block"]], d["mode"], d["passed"], d["detail"])
        for d in decoded
        if 0 <= d["block"] < len(blocks)
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
    blocks = extract_blocks(target)
    index_of = {id(block): i for i, block in enumerate(blocks)}
    results = check_file(target, blocks)
    print(
        json.dumps(
            [
                {
                    "block": index_of.get(id(r.block), 0),
                    "mode": r.mode,
                    "passed": r.passed,
                    "detail": r.detail,
                }
                for r in results
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
        suffix = {"py": " (docstrings)", "ipynb": " (notebook)"}.get(target.kind, "")
        unit = "cell" if target.kind == "ipynb" else "block"
        print(f"\n=== {rel}{suffix} ({len(results)} {unit} check(s)) ===")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            tag = f"[{r.mode}]"
            print(f"  {status} {tag} {r.block.where}: {r.detail}")

    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    runnable = sum(1 for r in all_results if r.mode == "runnable")
    output_checks = sum(1 for r in all_results if r.mode == "output")
    static_only = sum(1 for r in all_results if r.mode == "static")
    cluster_required = total - runnable - output_checks - static_only
    notebooks = sum(1 for t in targets if t.kind == "ipynb" and t.path.exists())

    print(
        f"\n{total} check(s) over {len(targets)} file(s), {notebooks} of them "
        f"notebooks: {passed} passed, {failed} failed "
        f"({runnable} executed for real, {cluster_required} statically verified "
        f"as cluster/network-required, {static_only} statically verified for "
        f"another reason, {output_checks} stored-output check(s))."
    )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
