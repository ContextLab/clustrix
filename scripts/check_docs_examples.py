#!/usr/bin/env python
"""Execute (or, for cluster/network-dependent examples, statically verify)
every Python code block in a fixed set of documentation files.

This exists because documentation drifts from the real API silently: a
module gets deleted, a function gets renamed, and nobody notices until a
user copy-pastes a broken example. Two documented bugs motivated this
script directly:

- ``MIGRATION.md`` claimed ``from clustrix import ClusterConfig`` works.
  It doesn't; ``ClusterConfig`` is not re-exported from ``clustrix/__init__.py``.
- ``docs/PRICING_API_REFERENCE.md`` and ``docs/PRICING_USER_GUIDE.md``
  documented ``clustrix.pricing_clients.performance_monitor`` and
  ``.resilience``, both since deleted as unused code.

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

Usage::

    python scripts/check_docs_examples.py
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import io
import os
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
    kind: str  # "md" or "rst"
    section_start: Optional[str] = None  # restrict extraction to a section
    section_end: Optional[str] = None


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


def extract_rst_blocks(target: TargetFile) -> List[CodeBlock]:
    text = target.path.read_text()
    slice_text, line_offset = _restrict_to_section(
        text, target.section_start, target.section_end
    )
    lines = slice_text.split("\n")
    blocks = []
    i = 0
    directive_re = re.compile(r"^( *)\.\. code-block:: python\s*$")
    while i < len(lines):
        m = directive_re.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        start_line = i + 1
        i += 1
        # skip blank lines immediately after the directive
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        raw_body = []
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
        # trim trailing blank lines
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        content = "\n".join(body_lines) + "\n"
        line_no = line_offset + start_line + 1
        blocks.append(CodeBlock(target.path, line_no, content))
    return blocks


def extract_blocks(target: TargetFile) -> List[CodeBlock]:
    if target.kind == "md":
        return extract_markdown_blocks(target)
    return extract_rst_blocks(target)


# ---------------------------------------------------------------------------
# Static ("cluster-required") verification
# ---------------------------------------------------------------------------


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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    importlib.import_module(alias.name)
                except Exception as e:
                    problems.append(f"import {alias.name}: {e}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import; not resolvable standalone
                continue
            module_name = node.module or ""
            try:
                mod = importlib.import_module(module_name)
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
    return Result(block, "cluster-required", True, "syntax + imports OK (not executed)")


# ---------------------------------------------------------------------------
# Real execution
# ---------------------------------------------------------------------------


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
    try:
        os.chdir(scratch_dir)
        with contextlib.redirect_stdout(stdout_buf):
            code = compile(
                block.content, f"{block.source_file}:{block.line_no}", "exec"
            )
            exec(code, namespace)
        return Result(block, "runnable", True, "executed OK")
    except Exception:
        tb = traceback.format_exc()
        return Result(
            block,
            "runnable",
            False,
            tb.strip().splitlines()[-1] if tb else "unknown error",
        )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def check_file(target: TargetFile) -> List[Result]:
    blocks = extract_blocks(target)
    results: List[Result] = []

    with tempfile.TemporaryDirectory(prefix="clustrix_docs_check_") as tmp:
        scratch_dir = Path(tmp)
        sys.path.insert(0, str(scratch_dir))
        namespace: dict = {"__name__": "__main__"}
        try:
            for block in blocks:
                first_line = (
                    block.content.strip().splitlines()[0]
                    if block.content.strip()
                    else ""
                )
                if CLUSTER_REQUIRED_RE.match(first_line):
                    results.append(verify_static(block))
                else:
                    results.append(run_block(block, namespace, scratch_dir))
        finally:
            sys.path.remove(str(scratch_dir))

    return results


def main() -> int:
    targets = [
        TargetFile(REPO_ROOT / "MIGRATION.md", "md"),
        TargetFile(
            REPO_ROOT / "docs" / "source" / "tutorials" / "usage_patterns.rst", "rst"
        ),
        TargetFile(
            REPO_ROOT / "docs" / "source" / "tutorials" / "kubernetes_tutorial.rst",
            "rst",
            section_start="Auto-Provisioning a Cluster\n----",
            section_end="Configuration Options\n---",
        ),
        TargetFile(REPO_ROOT / "docs" / "PRICING_API_REFERENCE.md", "md"),
        TargetFile(REPO_ROOT / "docs" / "PRICING_USER_GUIDE.md", "md"),
    ]

    all_results: List[Result] = []
    for target in targets:
        if not target.path.exists():
            print(f"SKIP (missing): {target.path}")
            continue
        results = check_file(target)
        all_results.extend(results)
        rel = target.path.relative_to(REPO_ROOT)
        print(f"\n=== {rel} ({len(results)} block(s)) ===")
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
