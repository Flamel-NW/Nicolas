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

from nico_sections import (
    NicoSectionError,
    extract_nested_block_span,
    extract_nico_section_span,
    find_matching_brace,
    mask_non_code,
)
from task_workspace import TaskWorkspace, WorkspaceError, resolve_workspace_file


VALID_OPS = {
    "replace_text",
    "insert_before",
    "insert_after",
    "insert_before_section_end",
    "replace_identifier",
    "insert_interface_item",
    "update_module_imports",
    "update_module_effects",
    "insert_implementation_item",
}
VALID_SECTIONS = {"file", "surface", "checks", "implementation"}
INSERT_OPS = {"insert_before", "insert_after", "insert_before_section_end"}
STRUCTURAL_OPS = {
    "insert_interface_item",
    "update_module_imports",
    "update_module_effects",
    "insert_implementation_item",
}
MAX_DIFF_CHARS = 6000
MAX_CONTEXT_CHARS = 800
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

    if op in STRUCTURAL_OPS:
        return _apply_structural_edit(source, edit, index, op)

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
        changed, count = _replace_text(scoped, target, replacement, expected_count, section)
    elif op == "insert_before":
        target = _non_empty_string(edit, "target", index)
        text = _string_value(edit, "text", index)
        changed, count = _insert_near(
            scoped, target, text, before=True, expected_count=expected_count, section=section
        )
    elif op == "insert_after":
        target = _non_empty_string(edit, "target", index)
        text = _string_value(edit, "text", index)
        changed, count = _insert_near(
            scoped, target, text, before=False, expected_count=expected_count, section=section
        )
    elif op == "insert_before_section_end":
        text = _string_value(edit, "text", index)
        changed, count = _insert_before_section_end(scoped, text)
    elif op == "replace_identifier":
        target = _identifier_value(edit, "target", index)
        replacement = _identifier_value(edit, "replacement", index)
        changed, count = _replace_identifier(scoped, target, replacement, expected_count, section)
    else:
        raise NicoEditError(f"edit #{index}: unhandled op '{op}'")

    updated = source[:start] + changed + source[end:]
    return updated, {
        "index": index,
        "op": op,
        "section": section,
        "matches": count,
    }


def _apply_structural_edit(source: str, edit: dict[str, Any], index: int, op: str) -> tuple[str, dict[str, Any]]:
    if op == "insert_interface_item":
        text = _string_value(edit, "text", index)
        start, end = extract_nested_block_span(source, "surface", "interface")
        scoped = source[start:end]
        changed, count = _insert_before_section_end(scoped, text)
        section = "surface.interface"
    elif op == "insert_implementation_item":
        text = _string_value(edit, "text", index)
        start, end = extract_nico_section_span(source, "implementation")
        scoped = source[start:end]
        changed, count = _insert_before_section_end(scoped, text)
        section = "implementation"
    elif op == "update_module_imports":
        imports = _string_list(edit, "imports", index)
        mode = str(edit.get("mode", "merge")).strip() or "merge"
        if mode not in {"merge", "replace"}:
            raise NicoEditError(f"edit #{index}: mode must be 'merge' or 'replace'")
        start, end = extract_nico_section_span(source, "surface")
        scoped = source[start:end]
        changed, count = _update_module_imports(scoped, imports, mode)
        section = "surface.imports"
    elif op == "update_module_effects":
        effects = _string_list(edit, "effects", index)
        mode = str(edit.get("mode", "merge")).strip() or "merge"
        if mode not in {"merge", "replace"}:
            raise NicoEditError(f"edit #{index}: mode must be 'merge' or 'replace'")
        start, end = extract_nico_section_span(source, "surface")
        scoped = source[start:end]
        changed, count = _update_module_effects(scoped, effects, mode)
        section = "surface.effects"
    else:
        raise NicoEditError(f"edit #{index}: unhandled structural op '{op}'")

    return source[:start] + changed + source[end:], {
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
    section: str,
) -> tuple[str, int]:
    count = scoped.count(target)
    _check_count(count, expected_count, "target", scoped, target, section)
    return scoped.replace(target, replacement), count


def _insert_near(
    scoped: str,
    target: str,
    text: str,
    before: bool,
    expected_count: int,
    section: str,
) -> tuple[str, int]:
    count = scoped.count(target)
    _check_count(count, expected_count, "target", scoped, target, section)
    replacement = text + target if before else target + text
    return scoped.replace(target, replacement), count


def _insert_before_section_end(scoped: str, text: str) -> tuple[str, int]:
    insert_at = _section_end_insert_offset(scoped)
    insert_text = _normalized_insert_text(scoped, insert_at, text)
    return scoped[:insert_at] + insert_text + scoped[insert_at:], 1


def _replace_identifier(
    scoped: str,
    target: str,
    replacement: str,
    expected_count: int,
    section: str,
) -> tuple[str, int]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(target)}(?![A-Za-z0-9_])")
    count = len(pattern.findall(scoped))
    _check_count(count, expected_count, "identifier", scoped, target, section)
    return pattern.sub(replacement, scoped), count


def _update_module_effects(scoped: str, requested_effects: list[str], mode: str) -> tuple[str, int]:
    scoped = _remove_absence_comments(scoped, "effects")
    span = _top_level_list_span(scoped, "effects")
    if span is None:
        replacement = f"    effects [{', '.join(requested_effects)}]"
        insert_at = _module_effects_insert_offset(scoped)
        return _insert_top_level_item(scoped, insert_at, replacement), 1

    effects_start, effects_end, current_effects = span
    if mode == "replace":
        next_effects = requested_effects
    else:
        next_effects = list(current_effects)
        for effect in requested_effects:
            if effect not in next_effects:
                next_effects.append(effect)
    replacement = f"effects [{', '.join(next_effects)}]"
    return scoped[:effects_start] + replacement + scoped[effects_end:], 1


def _update_module_imports(scoped: str, requested_imports: list[str], mode: str) -> tuple[str, int]:
    scoped = _remove_absence_comments(scoped, "imports")
    span = _top_level_list_span(scoped, "imports")
    if span is None:
        replacement = f"    imports [{', '.join(requested_imports)}]"
        insert_at = _module_imports_insert_offset(scoped)
        return _insert_top_level_item(scoped, insert_at, replacement), 1

    imports_start, imports_end, current_imports = span
    if mode == "replace":
        next_imports = requested_imports
    else:
        next_imports = list(current_imports)
        for import_name in requested_imports:
            if import_name not in next_imports:
                next_imports.append(import_name)
    replacement = f"imports [{', '.join(next_imports)}]"
    return scoped[:imports_start] + replacement + scoped[imports_end:], 1


def _module_effects_span(scoped: str) -> tuple[int, int, list[str]]:
    span = _top_level_list_span(scoped, "effects")
    if span is not None:
        return span
    raise NicoEditError(
        "module-level effects list not found; section=surface; "
        f"section_tail_context:\n{_tail_context(scoped)}"
    )


def _top_level_list_span(scoped: str, list_name: str) -> tuple[int, int, list[str]] | None:
    masked = mask_non_code(scoped)
    spec_match = re.search(r"\bspec\s*\{", masked)
    if spec_match is None:
        raise NicoEditError(f"module-level {list_name} list not found; section=surface; reason=missing spec block")
    open_brace = masked.find("{", spec_match.start(), spec_match.end())
    close_brace = find_matching_brace(masked, open_brace)
    if close_brace < 0:
        raise NicoEditError(f"module-level {list_name} list not found; section=surface; reason=unclosed spec block")

    depth = 1
    index = open_brace + 1
    while index < close_brace:
        ch = masked[index]
        if ch == "{":
            depth += 1
            index += 1
            continue
        if ch == "}":
            depth -= 1
            index += 1
            continue
        if depth == 1 and _word_at(masked, index, list_name):
            bracket_start = masked.find("[", index, close_brace)
            if bracket_start < 0:
                raise NicoEditError(f"module-level {list_name} list has no opening bracket; section=surface")
            bracket_end = masked.find("]", bracket_start, close_brace)
            if bracket_end < 0:
                raise NicoEditError(f"module-level {list_name} list has no closing bracket; section=surface")
            current = _parse_comma_list(scoped[bracket_start + 1:bracket_end])
            return index, bracket_end + 1, current
        index += 1

    return None


def _module_imports_insert_offset(scoped: str) -> int:
    block_start = _top_level_block_line_start(scoped, "interface")
    if block_start is not None:
        return block_start
    return _spec_close_line_start(scoped)


def _module_effects_insert_offset(scoped: str) -> int:
    block_end = _top_level_block_end(scoped, "interface")
    if block_end is not None:
        return block_end
    return _spec_close_line_start(scoped)


def _top_level_block_line_start(scoped: str, block_name: str) -> int | None:
    span = _top_level_block_span(scoped, block_name)
    if span is None:
        return None
    start, _ = span
    return scoped.rfind("\n", 0, start) + 1


def _top_level_block_end(scoped: str, block_name: str) -> int | None:
    span = _top_level_block_span(scoped, block_name)
    if span is None:
        return None
    _, end = span
    return end


def _top_level_block_span(scoped: str, block_name: str) -> tuple[int, int] | None:
    masked = mask_non_code(scoped)
    spec_match = re.search(r"\bspec\s*\{", masked)
    if spec_match is None:
        raise NicoEditError(f"nested block '{block_name}' not found; section=surface; reason=missing spec block")
    open_brace = masked.find("{", spec_match.start(), spec_match.end())
    close_brace = find_matching_brace(masked, open_brace)
    if close_brace < 0:
        raise NicoEditError(f"nested block '{block_name}' not found; section=surface; reason=unclosed spec block")

    depth = 1
    index = open_brace + 1
    while index < close_brace:
        ch = masked[index]
        if ch == "{":
            depth += 1
            index += 1
            continue
        if ch == "}":
            depth -= 1
            index += 1
            continue
        if depth == 1 and _word_at(masked, index, block_name):
            block_open = masked.find("{", index, close_brace)
            if block_open < 0:
                raise NicoEditError(f"nested block '{block_name}' has no opening brace; section=surface")
            block_close = find_matching_brace(masked, block_open)
            if block_close < 0:
                raise NicoEditError(f"nested block '{block_name}' has no closing brace; section=surface")
            return index, block_close + 1
        index += 1
    return None


def _spec_close_line_start(scoped: str) -> int:
    masked = mask_non_code(scoped)
    spec_match = re.search(r"\bspec\s*\{", masked)
    if spec_match is None:
        raise NicoEditError("spec block not found; section=surface")
    open_brace = masked.find("{", spec_match.start(), spec_match.end())
    close_brace = find_matching_brace(masked, open_brace)
    if close_brace < 0:
        raise NicoEditError("spec block has no closing brace; section=surface")
    return scoped.rfind("\n", 0, close_brace) + 1


def _word_at(source: str, index: int, word: str) -> bool:
    if not source.startswith(word, index):
        return False
    before = source[index - 1] if index > 0 else ""
    after_index = index + len(word)
    after = source[after_index] if after_index < len(source) else ""
    return not _is_word_char(before) and not _is_word_char(after)


def _is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == "_")


def _parse_comma_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _remove_absence_comments(scoped: str, kind: str) -> str:
    pattern = re.compile(
        rf"^[ \t]*//[^\n]*(?:无\s*{re.escape(kind)}|no\s+{re.escape(kind)})[^\n]*(?:\n|$)",
        re.IGNORECASE | re.MULTILINE,
    )
    return pattern.sub("", scoped)


def _insert_top_level_item(scoped: str, insert_at: int, text: str) -> str:
    insert_text = text.rstrip()
    if insert_at > 0 and scoped[insert_at - 1] != "\n":
        insert_text = "\n" + insert_text
    if not insert_text.endswith("\n"):
        insert_text += "\n"
    if insert_at < len(scoped) and scoped[insert_at:insert_at + 1] != "\n":
        insert_text += "\n"
    return scoped[:insert_at] + insert_text + scoped[insert_at:]


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


def _normalized_insert_text(scoped: str, insert_at: int, text: str) -> str:
    insert_text = text
    if insert_at > 0 and scoped[insert_at - 1] != "\n" and not insert_text.startswith("\n"):
        insert_text = "\n" + insert_text
    if insert_text and not insert_text.endswith("\n"):
        insert_text += "\n"
    return insert_text


def _check_count(actual: int, expected: int, label: str, scoped: str, target: str, section: str) -> None:
    if actual != expected:
        raise NicoEditError(_match_diagnostic(label, actual, expected, scoped, target, section))


def _match_diagnostic(label: str, actual: int, expected: int, scoped: str, target: str, section: str) -> str:
    lines = [
        f"{label} matched {actual} time(s), expected {expected}",
        f"section: {section}",
        f"match_count: {actual}",
        f"expected_count: {expected}",
    ]
    nearest = _nearest_anchors(scoped, target)
    if nearest:
        lines.append("nearest_anchors:")
        lines.extend(f"- {anchor}" for anchor in nearest)
    tail = _tail_context(scoped)
    if tail:
        lines.append("section_tail_context:")
        lines.append(tail)
    return "\n".join(lines)


def _nearest_anchors(scoped: str, target: str) -> list[str]:
    candidates = [_shorten(line.strip(), 140) for line in scoped.splitlines() if line.strip()]
    if not candidates:
        return []
    matches = difflib.get_close_matches(_shorten(target.strip(), 140), candidates, n=5, cutoff=0.35)
    if matches:
        return matches

    words = [word for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", target)]
    anchors: list[str] = []
    for line in candidates:
        if any(word in line for word in words):
            anchors.append(line)
        if len(anchors) == 5:
            break
    return anchors


def _tail_context(scoped: str) -> str:
    return scoped[-MAX_CONTEXT_CHARS:].strip()


def _shorten(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."


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


def _string_list(edit: dict[str, Any], key: str, index: int) -> list[str]:
    if key not in edit:
        raise NicoEditError(f"edit #{index}: missing required field '{key}'")
    value = edit[key]
    if not isinstance(value, list) or not value:
        raise NicoEditError(f"edit #{index}: field '{key}' must be a non-empty string list")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise NicoEditError(f"edit #{index}: field '{key}' must be a non-empty string list")
        cleaned = item.strip()
        if any(ch in cleaned for ch in "[],"):
            raise NicoEditError(f"edit #{index}: effect values must not contain brackets or commas")
        result.append(cleaned)
    return result


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
