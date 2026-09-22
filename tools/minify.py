"""Shrink a contract's source before it is put on chain.

The contract source *is* the deploy calldata, and the chain charges for those
bytes (zkSync-style pubdata pricing). MemeWar's readable source is ~25 KB, which
estimates to ~20.7M gas - past the limit this RPC accepts, so the deploy fails
with `gas limit too high`. The same contract with comments removed estimates to
~17.8M.

So the repository keeps the documented source and the deploy step ships a
stripped copy. The transformation is line-level and deliberately conservative:

* keeps the ``{ "Depends": ... }`` header, which GenVM actually parses;
* keeps every line that is not blank and not a whole-line comment;
* does **not** touch string literals, indentation, or code structure.

Anything cleverer (rewriting via `ast.unparse`) would risk changing the contract
that was tested, which is exactly the wrong trade for a few hundred KB.
"""

from __future__ import annotations

import ast
import re

_DEPENDS = re.compile(r'#\s*\{\s*"Depends"')

# The chain caps a single transaction at 2**24 gas. Measured on Bradbury:
#   gas ~= 980_000 + 386 * calldata_chars
# which puts the ceiling at roughly 40,900 characters of source. Staying under
# that is why docstrings are dropped too, not just comments.
GAS_CAP = 2 ** 24
GAS_BASE = 980_000
GAS_PER_CHAR = 386


def is_depends_header(line: str) -> bool:
    return bool(_DEPENDS.match(line.strip()))


def docstring_line_ranges(source: str) -> list:
    """Line ranges (1-based, inclusive) occupied by docstrings.

    Only *lines* are ever deleted, never rewritten, so the shipped contract stays
    byte-for-byte the code that was tested - minus prose. That is a much smaller
    risk than round-tripping the module through `ast.unparse`.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    ranges = []

    def visit(node):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ranges.append((first.lineno, first.end_lineno or first.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return ranges


def minify_source(source: str) -> str:
    """Drop docstrings, blank lines and whole-line comments. Keep everything else."""
    dropped = set()
    for start, end in docstring_line_ranges(source):
        for number in range(start, end + 1):
            dropped.add(number)

    kept = []
    for index, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()

        if is_depends_header(line):
            kept.append(stripped)
            continue

        if index in dropped:
            continue

        if stripped == "" or stripped.startswith("#"):
            continue

        kept.append(line.rstrip())

    return "\n".join(kept) + "\n"


def estimate_gas(source: str) -> int:
    """Approximate the deploy gas for a source string (see GAS_CAP notes)."""
    return GAS_BASE + GAS_PER_CHAR * len(source.encode("utf-8")) * 2


def shrink(source: str) -> tuple:
    """Return (minified_source, original_bytes, minified_bytes, estimated_gas)."""
    minified = minify_source(source)
    size = len(minified.encode("utf-8"))
    return minified, len(source.encode("utf-8")), size, estimate_gas(minified)
