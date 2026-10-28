"""Fail when Python 2-only calls remain in executable code.

Comments and string literals are removed first, so a docstring that quotes a
historical traceback is not a failure. A real call such as sys.exc_clear() is.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    ".pytest_cache",
    "__pycache__",
    ".cursor",
}

# Names and call shapes that exist in Python 2 and are gone, or that mean a
# different thing, in Python 3. print() calls are allowed; print statements
# are not.
_PATTERNS = (
    r"\bsys\.exc_clear\b",
    r"\bsys\.maxint\b",
    r"\.iteritems\s*\(",
    r"\.iterkeys\s*\(",
    r"\.itervalues\s*\(",
    r"\.has_key\s*\(",
    r"\bstring\.letters\b",
    r"\bstring\.lowercase\b",
    r"\bstring\.uppercase\b",
    r"\bos\.getcwdu\b",
    r"\bxrange\s*\(",
    r"\bbasestring\b",
    r"\braw_input\s*\(",
    r"\bexecfile\s*\(",
    r"\bapply\s*\(",
    r"\bunicode\s*\(",
    r"\blong\s*\(",
    r"(?m)(?:^|[^\w.])print\s+(?!\()",
    r"\bprint\s*>>",
    r"\braise\s+[A-Za-z_][A-Za-z0-9_\.]*\s*,",
    r"\bexcept\s+[A-Za-z_][A-Za-z0-9_\.]*\s*,",
)


def _python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        yield path


def _code_text(source):
    """Source with comments and string literals blanked out."""
    pieces = []
    readline = io.StringIO(source).readline
    for token in tokenize.generate_tokens(readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            pieces.append("\n" * token.string.count("\n"))
        elif token.type in (tokenize.NL, tokenize.NEWLINE):
            pieces.append("\n")
        elif token.type in (tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        else:
            pieces.append(token.string)
            pieces.append(" ")
    return "".join(pieces)


def test_python_sources_have_no_python2_only_apis():
    import re

    compiled = [re.compile(pattern) for pattern in _PATTERNS]
    failures = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            failures.append("%s: %s" % (path.relative_to(ROOT), exc))
            continue
        code = _code_text(source)
        for pattern in compiled:
            match = pattern.search(code)
            if match:
                failures.append(
                    "%s: %s" % (path.relative_to(ROOT), match.group(0).strip())
                )
    assert not failures, "Python 2-only APIs still in code:\n" + "\n".join(failures)
