"""
Task-scoped workspaces for condition C experiments.

The workspace is a writable copy of the task materials used by a single run.
Baseline materials and repo sources stay read-only during the experiment.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
NICOLAS_ROOT = HARNESS_DIR.parent

MANIFEST_FILENAME = "manifest.before.json"
CHANGESET_FILENAME = "changeset.json"
METADATA_FILENAMES = {MANIFEST_FILENAME, CHANGESET_FILENAME}
DIFF_MAX_CHARS = 12000


class WorkspaceError(ValueError):
    """Raised when workspace preparation or path resolution fails."""


@dataclass
class TaskWorkspace:
    root: Path
    source_root: Path
    source_kind: str
    task: str
    condition: str
    run_id: str
    batch_id: str | None
    copy_policy: str
    manifest_before: dict | None = None


def _display_path(path: Path) -> str:
    return os.path.relpath(path, HARNESS_DIR)


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_workspace_root(workspace_root: Path, source_root: Path) -> None:
    root = _resolved(Path(workspace_root))
    forbidden_roots = [
        MATERIALS_DIR,
        NICOLAS_ROOT / "src",
        source_root,
    ]
    for forbidden in forbidden_roots:
        forbidden_resolved = _resolved(forbidden)
        if root == forbidden_resolved or _path_is_under(root, forbidden_resolved):
            raise WorkspaceError(
                "workspace_root must not be inside task materials, public src, "
                f"or the selected source root: '{workspace_root}'"
            )


def _is_safe_relative_request(path: Path) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def _safe_path_segment(value: str | None, default: str) -> str:
    if not value:
        return default
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value))
    return cleaned if cleaned and cleaned not in {".", ".."} else default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_root(workspace: TaskWorkspace | Path) -> Path:
    return workspace.root if isinstance(workspace, TaskWorkspace) else Path(workspace)


def _available_files(root: Path, suffix: str) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob(f"*{suffix}")
        if p.is_file() and not p.is_symlink()
    )


def _select_condition_c_source(task: str) -> tuple[Path, str, str]:
    mat_dir = MATERIALS_DIR / "condition_C" / task.lower()
    if _available_files(mat_dir, ".nico"):
        return mat_dir, "task_materials", "all_regular_files"

    src_root = NICOLAS_ROOT / "src"
    if not _available_files(src_root, ".nico"):
        raise FileNotFoundError(
            f"No condition C .nico materials found for task={task}, and no fallback src/**/*.nico files exist."
        )
    return src_root, "fallback_src", "nico_files_only"


def _source_record(source_root: Path, source_kind: str, copy_policy: str) -> dict:
    return {
        "kind": source_kind,
        "path": _display_path(source_root),
        "copy_policy": copy_policy,
    }


def plan_task_workspace(
    task: str,
    condition: str,
    run_id: str,
    batch_id: str | None,
    workspace_root: Path,
) -> dict:
    """Return the workspace plan without creating directories or files."""
    if condition != "C":
        raise WorkspaceError("isolated task workspaces are currently only defined for condition C")

    source_root, source_kind, copy_policy = _select_condition_c_source(task)
    _validate_workspace_root(workspace_root, source_root)
    batch_segment = _safe_path_segment(batch_id, "unbatched")
    run_segment = _safe_path_segment(run_id, "run")
    root = Path(workspace_root) / batch_segment / run_segment
    return {
        "path": _display_path(root),
        "source": _source_record(source_root, source_kind, copy_policy),
    }


def prepare_task_workspace(
    task: str,
    condition: str,
    run_id: str,
    batch_id: str | None,
    workspace_root: Path,
) -> TaskWorkspace:
    """Create a writable workspace for one condition C experiment run."""
    if condition != "C":
        raise WorkspaceError("isolated task workspaces are currently only defined for condition C")

    source_root, source_kind, copy_policy = _select_condition_c_source(task)
    _validate_workspace_root(workspace_root, source_root)
    batch_segment = _safe_path_segment(batch_id, "unbatched")
    run_segment = _safe_path_segment(run_id, "run")
    root = Path(workspace_root) / batch_segment / run_segment
    if root.exists():
        raise FileExistsError(f"workspace already exists: {root}")

    root.mkdir(parents=True)
    suffix_filter = ".nico" if copy_policy == "nico_files_only" else None
    for src in sorted(source_root.rglob("*")):
        if src.is_symlink() or not src.is_file():
            continue
        if suffix_filter and src.suffix != suffix_filter:
            continue
        rel = src.relative_to(source_root)
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    workspace = TaskWorkspace(
        root=root,
        source_root=source_root,
        source_kind=source_kind,
        task=task,
        condition=condition,
        run_id=run_id,
        batch_id=batch_id,
        copy_policy=copy_policy,
    )
    manifest = snapshot_manifest(workspace)
    workspace.manifest_before = manifest
    _write_json(root / MANIFEST_FILENAME, manifest)
    return workspace


def snapshot_manifest(workspace: TaskWorkspace | Path) -> dict:
    """Return a content hash manifest for regular workspace files."""
    root = _workspace_root(workspace)
    files = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.name in METADATA_FILENAMES:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        files.append({
            "path": rel,
            "bytes": size,
            "sha256": _sha256(path),
            "utf8_text": _read_text_for_diff(path),
        })

    manifest = {
        "schema": "task-workspace-manifest-v1",
        "root": _display_path(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }
    if isinstance(workspace, TaskWorkspace):
        manifest.update({
            "task": workspace.task,
            "condition": workspace.condition,
            "run_id": workspace.run_id,
            "batch_id": workspace.batch_id,
            "source": _source_record(workspace.source_root, workspace.source_kind, workspace.copy_policy),
        })
    return manifest


def resolve_workspace_file(
    workspace: TaskWorkspace | Path,
    requested_path: str,
    suffix: str = ".nico",
) -> Path:
    """Resolve a safe file request inside a task workspace."""
    root = _workspace_root(workspace)
    raw = requested_path.strip()
    requested = Path(raw)
    if not _is_safe_relative_request(requested):
        raise WorkspaceError(f"unsafe path rejected: '{requested_path}'")
    if requested.suffix != suffix:
        raise WorkspaceError(f"only {suffix} files are accessible in this workspace (got '{requested_path}')")

    candidate_rels = [requested]
    if requested.parts and requested.parts[0] == "src" and len(requested.parts) > 1:
        candidate_rels.append(Path(*requested.parts[1:]))

    seen: set[str] = set()
    for rel in candidate_rels:
        rel_key = rel.as_posix()
        if rel_key in seen:
            continue
        seen.add(rel_key)
        candidate = root / rel
        resolved = _safe_existing_file(candidate, root, suffix)
        if resolved is not None:
            return resolved

    if len(requested.parts) == 1:
        matches = []
        for candidate in sorted(root.rglob(requested.name)):
            resolved = _safe_existing_file(candidate, root, suffix, raise_on_symlink=True)
            if resolved is not None:
                matches.append(resolved)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            rel_matches = [m.relative_to(root).as_posix() for m in matches]
            raise WorkspaceError(f"ambiguous file name '{requested.name}'. Use one of: {rel_matches}")

    available = [
        p.relative_to(root).as_posix()
        for p in sorted(root.rglob(f"*{suffix}"))
        if p.is_file() and not p.is_symlink()
    ]
    raise WorkspaceError(f"file not found: '{requested_path}'. Available files: {available}")


def _safe_existing_file(candidate: Path, root: Path, suffix: str, raise_on_symlink: bool = True) -> Path | None:
    if not candidate.exists():
        return None
    if candidate.is_symlink():
        target = candidate.resolve()
        if raise_on_symlink or not _path_is_under(target, root):
            raise WorkspaceError(f"symlink path rejected: '{candidate.relative_to(root).as_posix()}'")
        return None
    if not candidate.is_file() or candidate.suffix != suffix:
        return None
    if not _path_is_under(candidate, root):
        raise WorkspaceError(f"workspace path escapes root: '{candidate}'")
    return candidate


def compute_changeset(workspace: TaskWorkspace | Path) -> dict:
    """Compare the current workspace files with manifest.before.json."""
    root = _workspace_root(workspace)
    before_manifest = _load_before_manifest(workspace)
    after_manifest = snapshot_manifest(workspace)
    before = {entry["path"]: entry for entry in before_manifest.get("files", [])}
    after = {entry["path"]: entry for entry in after_manifest.get("files", [])}

    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(
        path for path in (set(before) & set(after))
        if before[path]["sha256"] != after[path]["sha256"]
    )

    changes = (
        [{"path": path, "status": "added"} for path in added]
        + [{"path": path, "status": "modified"} for path in modified]
        + [{"path": path, "status": "deleted"} for path in deleted]
    )
    changes = sorted(changes, key=lambda item: item["path"])
    diffs = [_file_diff(root, before_manifest, change["path"], change["status"]) for change in changes]

    changeset = {
        "schema": "task-workspace-changeset-v1",
        "root": _display_path(root),
        "summary": {
            "added": len(added),
            "modified": len(modified),
            "deleted": len(deleted),
        },
        "changed_files": [change["path"] for change in changes],
        "changes": changes,
        "diffs": diffs,
    }
    _write_json(root / CHANGESET_FILENAME, changeset)
    return changeset


def _load_before_manifest(workspace: TaskWorkspace | Path) -> dict:
    if isinstance(workspace, TaskWorkspace) and workspace.manifest_before is not None:
        return workspace.manifest_before
    root = _workspace_root(workspace)
    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _source_root_from_manifest(manifest: dict) -> Path:
    source_path = manifest.get("source", {}).get("path")
    if not source_path:
        raise WorkspaceError("manifest is missing source.path")
    return (HARNESS_DIR / source_path).resolve()


def _file_diff(root: Path, before_manifest: dict, rel_path: str, status: str) -> dict:
    source_root = _source_root_from_manifest(before_manifest)
    before_path = source_root / rel_path
    after_path = root / rel_path
    before_by_path = {
        entry.get("path"): entry
        for entry in before_manifest.get("files", [])
        if isinstance(entry, dict)
    }
    before_entry = before_by_path.get(rel_path) or {}
    before_text = "" if status == "added" else before_entry.get("utf8_text")
    if before_text is None and status != "added":
        before_text = _read_text_for_diff(before_path)
    after_text = "" if status == "deleted" else _read_text_for_diff(after_path)

    if before_text is None or after_text is None:
        return {
            "path": rel_path,
            "status": status,
            "diff": "(binary or non-UTF-8 file diff omitted)",
            "diff_truncated": False,
        }

    diff = "".join(difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after_text.splitlines(keepends=True),
        fromfile=f"before/{rel_path}",
        tofile=f"after/{rel_path}",
    ))
    truncated = len(diff) > DIFF_MAX_CHARS
    if truncated:
        diff = diff[:DIFF_MAX_CHARS] + "\n... diff truncated ...\n"
    return {
        "path": rel_path,
        "status": status,
        "diff": diff,
        "diff_truncated": truncated,
    }


def _read_text_for_diff(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
