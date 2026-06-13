"""
Section extraction helpers for Nicolas `.nico` sources.

The extractor is intentionally lightweight: it only needs the stable v0 module
shape used by the experiment materials, not a full Nicolas parser.
"""

from __future__ import annotations

import re


SECTION_PATTERNS = {
    "surface": re.compile(r"\bspec\s*\{"),
    "checks": re.compile(r"\bchecks\s*\{"),
    "implementation": re.compile(r"\bimplementation\s+rust\s*\{"),
}


class NicoSectionError(ValueError):
    """Raised when a requested `.nico` section cannot be extracted."""


def extract_nico_section(source: str, section: str) -> str:
    """Return one top-level `.nico` section from source text.

    Supported sections:
      - surface: the full `spec { ... }` block
      - checks: the full `checks { ... }` block
      - implementation: the full `implementation rust { ... }` block

    Braces inside strings and comments are ignored while matching block bounds.
    """
    pattern = SECTION_PATTERNS.get(section)
    if pattern is None:
        allowed = ", ".join(sorted(SECTION_PATTERNS))
        raise NicoSectionError(f"unknown section '{section}'. Expected one of: {allowed}")

    masked = mask_non_code(source)
    match = pattern.search(masked)
    if match is None:
        raise NicoSectionError(f"section '{section}' not found")

    open_brace = masked.find("{", match.start(), match.end())
    if open_brace < 0:
        raise NicoSectionError(f"section '{section}' has no opening brace")

    close_brace = find_matching_brace(masked, open_brace)
    if close_brace < 0:
        raise NicoSectionError(f"section '{section}' has no matching closing brace")

    return source[match.start():close_brace + 1].strip()


def find_matching_brace(masked_source: str, open_brace: int) -> int:
    if open_brace >= len(masked_source) or masked_source[open_brace] != "{":
        return -1

    depth = 0
    for index in range(open_brace, len(masked_source)):
        ch = masked_source[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def mask_non_code(source: str) -> str:
    """Replace comments and string/char literals with spaces.

    Newlines are preserved so diagnostics and extracted slices keep stable line
    positions. The scanner covers the constructs present in current `.nico`
    materials, including Rust-style comments, ordinary strings, raw strings,
    and char literals.
    """
    chars = list(source)
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""

        if ch == "/" and nxt == "/":
            i = _mask_line_comment(chars, i)
            continue
        if ch == "/" and nxt == "*":
            i = _mask_block_comment(chars, i)
            continue
        if ch == "r":
            raw_end = _raw_string_end(chars, i)
            if raw_end is not None:
                _mask_range(chars, i, raw_end)
                i = raw_end
                continue
        if ch == "\"":
            i = _mask_quoted(chars, i, "\"")
            continue
        if ch == "'":
            i = _mask_quoted(chars, i, "'")
            continue
        i += 1
    return "".join(chars)


def _mask_line_comment(chars: list[str], start: int) -> int:
    i = start
    while i < len(chars) and chars[i] != "\n":
        chars[i] = " "
        i += 1
    return i


def _mask_block_comment(chars: list[str], start: int) -> int:
    i = start
    chars[i] = " "
    chars[i + 1] = " "
    i += 2
    while i < len(chars):
        if chars[i] == "*" and i + 1 < len(chars) and chars[i + 1] == "/":
            chars[i] = " "
            chars[i + 1] = " "
            return i + 2
        if chars[i] != "\n":
            chars[i] = " "
        i += 1
    return i


def _mask_quoted(chars: list[str], start: int, quote: str) -> int:
    i = start
    chars[i] = " "
    i += 1
    escaped = False
    while i < len(chars):
        ch = chars[i]
        if ch != "\n":
            chars[i] = " "
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote:
            return i + 1
        i += 1
    return i


def _raw_string_end(chars: list[str], start: int) -> int | None:
    i = start + 1
    hashes = 0
    while i < len(chars) and chars[i] == "#":
        hashes += 1
        i += 1
    if i >= len(chars) or chars[i] != "\"":
        return None

    terminator = "\"" + ("#" * hashes)
    j = i + 1
    while j < len(chars):
        if "".join(chars[j:j + len(terminator)]) == terminator:
            return j + len(terminator)
        j += 1
    return len(chars)


def _mask_range(chars: list[str], start: int, end: int) -> None:
    for i in range(start, min(end, len(chars))):
        if chars[i] != "\n":
            chars[i] = " "
