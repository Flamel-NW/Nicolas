"""
Structured edit helpers for Nicolas `.nico` experiment workspaces.

These helpers intentionally operate on exact anchors inside a task workspace.
They are not a full Nicolas parser; the goal is to give condition C a compact,
auditable way to apply small source edits without rewriting whole modules.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from nico_sections import NicoSectionError, extract_nico_section_span
from task_workspace import TaskWorkspace, WorkspaceError, resolve_workspace_file


VALID_OPS = {
    "replace_text",
    "insert_before",
    "insert_after",
    "insert_before_section_end",
    "replace_identifier",
}
VALID_SECTIONS = {"file", "surface", "checks", "implementation"}
INSERT_OPS = {"insert_before", "insert_after", "insert_before_section_end"}
MAX_DIFF_CHARS = 6000
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NicoEditError(ValueError):
    """Raised when a structured `.nico` edit request is invalid."""


def apply_nico_edits(
    workspace: TaskWorkspace,
    path: str,
    edits: list[dict[str, Any]],
    dry_run: bool = False,
) -> str:
    """Apply a batch of exact-anchor edits to one workspace `.nico` file."""
    if workspace is None:
        return "Error: edit_nico requires a condition C task workspace."

    try:
        target_path = resolve_workspace_file(workspace, path, suffix=".nico")
        before = target_path.read_text(encoding="utf-8")
        after, summaries = _apply_edit_batch(before, edits)
        if not dry_run and after != before:
            target_path.write_text(after, encoding="utf-8")
    except (WorkspaceError, NicoEditError, NicoSectionError, UnicodeDecodeError) as e:
        return f"Error: {e}"

    return _format_edit_result(workspace, target_path, before, after, summaries, dry_run=dry_run)


def _apply_edit_batch(source: str, edits: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(edits, list) or not edits:
        raise NicoEditError("edits must be a non-empty list")

    updated = source
    summaries: list[dict[str, Any]] = []
    for index, raw_edit in enumerate(edits, start=1):
        if not isinstance(raw_edit, dict):
            raise NicoEditError(f"edit #{index} must be an object")
        updated, summary = _apply_one_edit(updated, raw_edit, index)
        summaries.append(summary)
    return updated, summaries


def _apply_one_edit(source: str, edit: dict[str, Any], index: int) -> tuple[str, dict[str, Any]]:
    op = _string_value(edit, "op", index).strip()
    if op not in VALID_OPS:
        allowed = ", ".join(sorted(VALID_OPS))
        raise NicoEditError(f"edit #{index}: unknown op '{op}'. Expected one of: {allowed}")

    section = str(edit.get("section", "file")).strip() or "file"
    if section not in VALID_SECTIONS:
        allowed = ", ".join(sorted(VALID_SECTIONS))
        raise NicoEditError(f"edit #{index}: unknown section '{section}'. Expected one of: {allowed}")
    if section == "file" and op in INSERT_OPS:
        raise NicoEditError(f"edit #{index}: {op} must target a named section, not section=file")

    expected_count = _expected_count(edit.get("expected_count"), index)
    start, end = _section_span(source, section)
    scoped = source[start:end]

    if op == "replace_text":
        target = _non_empty_string(edit, "target", index)
        replacement = _string_value(edit, "replacement", index)
        changed, count = _replace_text(scoped, target, replacement, expected_count)
    elif op == "insert_before":
        target = _non_empty_string(edit, "target", index)
        text = _string_value(edit, "text", index)
        changed, count = _insert_near(scoped, target, text, before=True, expected_count=expected_count)
    elif op == "insert_after":
        target = _non_empty_string(edit, "target", index)
        text = _string_value(edit, "text", index)
        changed, count = _insert_near(scoped, target, text, before=False, expected_count=expected_count)
    elif op == "insert_before_section_end":
        text = _string_value(edit, "text", index)
        changed, count = _insert_before_section_end(scoped, text)
    elif op == "replace_identifier":
        target = _identifier_value(edit, "target", index)
        replacement = _identifier_value(edit, "replacement", index)
        changed, count = _replace_identifier(scoped, target, replacement, expected_count)
    else:
        raise NicoEditError(f"edit #{index}: unhandled op '{op}'")

    updated = source[:start] + changed + source[end:]
    return updated, {
        "index": index,
        "op": op,
        "section": section,
        "matches": count,
    }


def _section_span(source: str, section: str) -> tuple[int, int]:
    if section == "file":
        return 0, len(source)
    return extract_nico_section_span(source, section)


def _replace_text(
    scoped: str,
    target: str,
    replacement: str,
    expected_count: int,
) -> tuple[str, int]:
    count = scoped.count(target)
    _check_count(count, expected_count, "target")
    return scoped.replace(target, replacement), count


def _insert_near(
    scoped: str,
    target: str,
    text: str,
    before: bool,
    expected_count: int,
) -> tuple[str, int]:
    count = scoped.count(target)
    _check_count(count, expected_count, "target")
    replacement = text + target if before else target + text
    return scoped.replace(target, replacement), count


def _insert_before_section_end(scoped: str, text: str) -> tuple[str, int]:
    insert_at = _section_end_insert_offset(scoped)
    return scoped[:insert_at] + text + scoped[insert_at:], 1


def _replace_identifier(
    scoped: str,
    target: str,
    replacement: str,
    expected_count: int,
) -> tuple[str, int]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(target)}(?![A-Za-z0-9_])")
    count = len(pattern.findall(scoped))
    _check_count(count, expected_count, "identifier")
    return pattern.sub(replacement, scoped), count


def _section_end_insert_offset(scoped: str) -> int:
    close_index = scoped.rfind("}")
    if close_index < 0:
        raise NicoEditError("section has no closing brace")
    line_start = scoped.rfind("\n", 0, close_index) + 1
    indent = scoped[line_start:close_index]
    if indent.strip():
        indent = ""
    if close_index > 0 and scoped[close_index - 1] != "\n":
        return close_index
    return line_start


def _check_count(actual: int, expected: int, label: str) -> None:
    if actual != expected:
        raise NicoEditError(f"{label} matched {actual} time(s), expected {expected}")


def _expected_count(value: Any, index: int) -> int:
    if value is None:
        return 1
    if isinstance(value, bool):
        raise NicoEditError(f"edit #{index}: expected_count must be an integer")
    try:
        count = int(value)
    except (TypeError, ValueError) as e:
        raise NicoEditError(f"edit #{index}: expected_count must be an integer") from e
    if count < 1:
        raise NicoEditError(f"edit #{index}: expected_count must be >= 1")
    return count


def _string_value(edit: dict[str, Any], key: str, index: int) -> str:
    if key not in edit:
        raise NicoEditError(f"edit #{index}: missing required field '{key}'")
    value = edit[key]
    if not isinstance(value, str):
        raise NicoEditError(f"edit #{index}: field '{key}' must be a string")
    return value


def _non_empty_string(edit: dict[str, Any], key: str, index: int) -> str:
    value = _string_value(edit, key, index)
    if not value:
        raise NicoEditError(f"edit #{index}: field '{key}' must be non-empty")
    return value


def _identifier_value(edit: dict[str, Any], key: str, index: int) -> str:
    value = _non_empty_string(edit, key, index)
    if not IDENTIFIER_RE.fullmatch(value):
        raise NicoEditError(f"edit #{index}: field '{key}' must be a Rust-like identifier")
    return value


def _format_edit_result(
    workspace: TaskWorkspace,
    target_path: Path,
    before: str,
    after: str,
    summaries: list[dict[str, Any]],
    dry_run: bool,
) -> str:
    root = workspace.root
    rel_path = target_path.relative_to(root).as_posix()
    diff, truncated = _diff_preview(rel_path, before, after)
    status = "dry_run" if dry_run else "applied"
    if before == after:
        status = "no_change_dry_run" if dry_run else "no_change"

    lines = [
        f"status: {status}",
        f"path: {rel_path}",
        f"edits: {len(summaries)}",
    ]
    for summary in summaries:
        lines.append(
            f"- edit #{summary['index']}: {summary['op']} "
            f"section={summary['section']} matches={summary['matches']}"
        )
    lines.append(f"diff_truncated: {str(truncated).lower()}")
    lines.append("diff:")
    lines.append(diff if diff else "(no textual changes)")
    return "\n".join(lines)


def _diff_preview(rel_path: str, before: str, after: str) -> tuple[str, bool]:
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"before/{rel_path}",
        tofile=f"after/{rel_path}",
    ))
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS] + "\n... diff truncated ...\n"
    return diff, truncated
