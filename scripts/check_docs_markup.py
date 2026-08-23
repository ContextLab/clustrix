#!/usr/bin/env python
"""Fail the documentation build when reStructuredText inline markup is nested.

Sphinx's inline markup does not nest. A literal written *inside* a strong or
emphasis span::

    **A package at or above ``stage_max_bytes``**, with the largest file named.

does not render as bold-plus-literal. Docutils treats the whole span as plain
text, so the reader sees the backticks::

    <strong>A package at or above ``stage_max_bytes``</strong>

Sphinx's smartquotes transform then curls any quotes inside it
(``cluster_type=”local”``), which is the giveaway that the text was never
parsed as markup at all. ``sphinx -W`` does **not** report this: it is not a
warning, it is a successful build of the wrong thing.

The check runs against the *built HTML* rather than the ``.rst`` sources on
purpose. Three different source kinds land in the same defect and only the
output has all of them in one place:

- ``docs/source/**/*.rst`` -- written as RST directly.
- ``clustrix/**/*.py`` docstrings -- published through ``automodule`` in
  ``docs/source/api/*.rst``.
- ``docs/source/notebooks/*.ipynb`` markdown cells -- nbsphinx converts
  markdown to RST before Sphinx sees it, so a markdown code span inside a
  markdown bold span (``**works with `@cluster`**``) becomes exactly the
  same unparsable RST.

Usage::

    python scripts/check_docs_markup.py docs/build/html

Exits 0 when the build is clean, 1 when any offending span is found.
"""

import argparse
import re
import sys
from pathlib import Path

# An inline span whose *text* still contains a double backtick. The tag set is
# deliberately narrow: these are the elements docutils emits for ``**strong**``
# and ``*emphasis*``. Code blocks are <pre>/<span> and are not matched, so a
# documented example that legitimately shows RST source is not a false hit.
SPAN_RE = re.compile(
    r"<(strong|em)\b[^>]*>(?P<body>(?:(?!</\1>).)*?)</\1>",
    re.DOTALL,
)


def offending_spans(html: str):
    """Yield ``(tag, text)`` for every strong/em span containing ``literals``."""
    for match in SPAN_RE.finditer(html):
        body = match.group("body")
        if "``" in body:
            yield match.group(1), " ".join(body.split())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "build_dir",
        nargs="?",
        default="docs/build/html",
        type=Path,
        help="directory holding the built HTML (default: docs/build/html)",
    )
    args = parser.parse_args(argv)

    build_dir = args.build_dir
    if not build_dir.is_dir():
        print(
            f"ERROR: {build_dir} is not a directory. Build the documentation "
            f"first (cd docs && make html).",
            file=sys.stderr,
        )
        return 1

    pages = sorted(build_dir.rglob("*.html"))
    if not pages:
        print(f"ERROR: no HTML pages under {build_dir}.", file=sys.stderr)
        return 1

    failures = 0
    for page in pages:
        rel = page.relative_to(build_dir)
        html = page.read_text(encoding="utf-8", errors="replace")
        for tag, text in offending_spans(html):
            failures += 1
            print(f"{rel}: nested inline markup: <{tag}>{text}</{tag}>")

    if failures:
        print(
            f"\n{failures} nested-inline-markup instance(s) in "
            f"{len(pages)} built page(s).\n"
            "Sphinx renders these literally. Close the bold/emphasis span "
            "before the literal and reopen it after, e.g.\n"
            "  **A package at or above** ``stage_max_bytes`` -- with the "
            "largest file named."
        )
        return 1

    print(f"OK: no nested inline markup in {len(pages)} built page(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
