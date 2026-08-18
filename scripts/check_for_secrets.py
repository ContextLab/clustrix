#!/usr/bin/env python
"""Fail if a credential looks like it has been committed.

The check this replaces grepped the source for the *words* "password",
"secret", "key" and "token":

    ! grep -r "password\\|secret\\|key\\|token" clustrix/ --include="*.py" \\
        | grep -v "def\\|class\\|#\\|test"

That flagged 1138 lines, among them ``kwargs: Function keyword arguments`` --
"keyword" contains "key" -- so it could never pass on a codebase that handles
credentials at all, which is exactly the codebase worth checking. It also
could not have caught a real secret, because a leaked token does not contain
the word "token".

This looks for the shapes credentials actually take: key material, provider
token formats, and long literal strings assigned to credential-named
variables. Placeholders are not findings; a value has to look usable.

Usage::

    python scripts/check_for_secrets.py [path ...]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple

#: Provider formats, each anchored on a prefix the provider actually issues.
TOKEN_PATTERNS = [
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("HuggingFace token", re.compile(r"\bhf_[A-Za-z0-9]{34,}\b")),
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
]

#: `password = "hunter2"` and friends. Long enough to be usable, and not one
#: of the obvious stand-ins below.
ASSIGNMENT = re.compile(r"""(?ix)
    \b(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key|
       client[_-]?secret|auth)\b
    \s* [:=] \s*
    (?P<quote>['"])(?P<value>[^'"\n]{8,})(?P=quote)
    """)

#: A PEM block is only interesting if it carries a real body. Test fixtures
#: and docs write the header around a stand-in like MOCK_KEY_CONTENT; a usable
#: key has base64 lines, which are long and have no spaces.
PEM_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----(?P<body>.*?)-----END",
    re.S,
)
PEM_BODY_LINE = re.compile(r"^[A-Za-z0-9+/=]{40,}$")

#: Credentials that providers publish *as* examples. Flagging AWS's own
#: documentation key teaches people to ignore this check.
KNOWN_EXAMPLES = {
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}

#: Values that are telling you what to put there, not a credential.
PLACEHOLDER = re.compile(r"""(?ix)
    ^(
        <.*>                      # <redacted>, <your-token>
      | \{.*\}                    # {token}, format placeholders
      | \$\{?[A-Z_]+\}?           # $TOKEN, ${TOKEN}
      | (your|my|some|example|dummy|fake|sample|placeholder|changeme|
         test|todo|xxx+|\.\.\.|none|null|redacted)[-_a-z0-9]*
      | [x*]{8,}                  # xxxxxxxx, ********
      | (password|secret|token|api_key|access_key|key)[-_a-z0-9]*
    )$
    """)

#: Words that only appear in values written to be thrown away. A real
#: credential containing one of these is possible but not worth the noise of
#: every fixture in the suite; the provider patterns above still catch the
#: shapes that matter.
#: Underscores are word characters, so \b does not separate them: \bwrong\b
#: never matched "wrong_password". Separators are spelled out instead.
FIXTURE_WORDS = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(mock|fake|dummy|example|sample|placeholder|invalid|wrong|bogus|"
    r"changeme|notreal|xxx|content)"
    r"(?:[^a-z0-9]|$)"
)

#: Never scanned: caches, vendored code, and this file, whose whole content is
#: patterns that look like the thing it hunts for.
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "htmlcov",
}
SKIP_NAMES = {"check_for_secrets.py", "test_check_for_secrets.py"}
TEXT_SUFFIXES = {
    ".py",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".sh",
    ".md",
    ".rst",
    ".txt",
    ".ipynb",
    ".env",
}


class Finding(NamedTuple):
    path: Path
    line: int
    kind: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.excerpt}"


def _is_placeholder(value: str) -> bool:
    value = value.strip()
    return (
        bool(PLACEHOLDER.match(value))
        or bool(FIXTURE_WORDS.search(value))
        or value in KNOWN_EXAMPLES
    )


def scan_line(text: str) -> List[str]:
    """Kinds of credential this line appears to contain."""
    kinds = []
    for name, pattern in TOKEN_PATTERNS:
        match = pattern.search(text)
        if match and not _is_placeholder(match.group(0)):
            kinds.append(name)
    for match in ASSIGNMENT.finditer(text):
        if not _is_placeholder(match.group("value")):
            kinds.append("assigned credential")
    return kinds


def scan_key_blocks(content: str) -> List[int]:
    """Line numbers of PEM blocks that carry usable key material."""
    lines = []
    for match in PEM_BLOCK.finditer(content):
        body = match.group("body").replace("\\n", "\n")
        if any(PEM_BODY_LINE.match(line.strip()) for line in body.splitlines()):
            lines.append(content[: match.start()].count("\n") + 1)
    return lines


def tracked_files(roots: Iterable[Path]) -> List[Path]:
    """Files to scan: what git tracks, so build output is never flagged."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", *[str(r) for r in roots]],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
        candidates = [Path(name) for name in listed if name]
    except (subprocess.CalledProcessError, FileNotFoundError):
        candidates = [p for root in roots for p in Path(root).rglob("*")]

    return [
        path
        for path in candidates
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and path.name not in SKIP_NAMES
        and not SKIP_PARTS.intersection(path.parts)
    ]


def scan(roots: Iterable[Path]) -> List[Finding]:
    findings: List[Finding] = []
    for path in tracked_files(roots):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number in scan_key_blocks(content):
            findings.append(
                Finding(path, number, "private key material", "PEM block with a body")
            )
        for number, line in enumerate(content.splitlines(), 1):
            for kind in scan_line(line):
                excerpt = line.strip()
                findings.append(
                    Finding(
                        path,
                        number,
                        kind,
                        excerpt[:100] + ("…" if len(excerpt) > 100 else ""),
                    )
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["."], type=Path)
    args = parser.parse_args()

    findings = scan(args.paths or [Path(".")])
    if not findings:
        print("No committed credentials found.")
        return 0

    print(f"Possible committed credentials ({len(findings)}):\n")
    for finding in findings:
        print(f"  {finding}")
    print(
        "\nIf one of these is a placeholder, make it look like one "
        "(<redacted>, $TOKEN, your-key-here). If it is real, rotate it."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
