"""
Nicolas Experiment Runner
=========================
Direct Anthropic API experiment harness. No Cursor workspace context, no boot rules.
Token counts are exact values reported by the API (usage.input_tokens / output_tokens).

Usage:
    # v2-style single-turn (direct mode, backward compatible):
    python run_experiment.py --task T0 --condition A --runs 3
    python run_experiment.py --task T7 --condition C --runs 1 --dry-run

    # v3-style multi-turn tool_use:
    python run_experiment.py --task T7 --condition C --runs 3 --mode tool_use
    python run_experiment.py --task T0 --condition A --runs 1 --mode tool_use --dry-run
    python run_experiment.py --task E1 --condition D --runs 3 --batch-id r6-fix-YYYYMMDD

E1-E6 default to tool_use mode when --mode is omitted. T0/T7 keep the
backward-compatible direct-mode default.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv, find_dotenv

from nico_edits import apply_nico_edits, validate_nico_source_structure
from nico_sections import NicoSectionError, extract_nico_section
from semantic_queries import run_semantic_query
from task_workspace import (
    TaskWorkspace,
    WorkspaceError,
    compute_changeset,
    plan_task_workspace,
    prepare_task_workspace,
    resolve_workspace_file,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-5"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_THINKING = {"type": "disabled"}
MAX_TOKENS = 16384
TEMPERATURE = 0        # deterministic; eliminates LLM randomness as a variable
MAX_TURNS = 25         # safety cap for multi-turn tool_use loops
RESULT_SCHEMA_VERSION = "t3-scoring-input-1"

HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
PROMPTS_DIR = HARNESS_DIR / "prompts"
RESULTS_DIR = HARNESS_DIR / "results"
WORKSPACES_DIR = HARNESS_DIR / "workspaces"
NICOLAS_ROOT = HARNESS_DIR.parent   # Nicolas/ repo root
SEMANTIC_DB_DIR = MATERIALS_DIR / "semantic_db"

VALID_TASKS = ("T0", "T7", "E1", "E2", "E3", "E4", "E5", "E6")
VALID_CONDITIONS = ("A", "C", "D")
VALID_PROVIDERS = ("anthropic", "deepseek")
RUST_SOURCE_ALIASES = {
    "src/audit/log.rs": "log.rs",
    "src/cache/kv.rs": "kv.rs",
    "src/config/loader.rs": "loader.rs",
    "src/metrics/recorder.rs": "recorder.rs",
    "src/rate/limiter.rs": "rate_limiter.rs",
    "src/session/service.rs": "session_service.rs",
    "src/session/store.rs": "session_store.rs",
    "src/session/types.rs": "session_types.rs",
    "src/time/clock.rs": "clock.rs",
    "src/user/admin_service.rs": "admin_service.rs",
    "src/user/profile_service.rs": "profile_service.rs",
    "src/user/store.rs": "store.rs",
    "src/user/types.rs": "types.rs",
}

# ---------------------------------------------------------------------------
# Load .env (search from harness dir upward; finds workspace root .env).
# API credentials are managed there and read by SDK clients.
# ---------------------------------------------------------------------------
dotenv_path = find_dotenv(usecwd=False, raise_error_if_not_found=False)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    workspace_env = HARNESS_DIR.parent.parent / ".env"
    if workspace_env.exists():
        load_dotenv(workspace_env)


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def display_path(path: Path) -> str:
    return os.path.relpath(path, HARNESS_DIR)


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_safe_relative_request(path: Path) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def create_api_client(provider: str) -> anthropic.Anthropic:
    """Create the API client without credential preflight checks.

    The workspace root .env is loaded at module startup. Credential presence is
    intentionally left to the SDK/API call path so protocol.md remains the
    single source for credential management.
    """
    if provider == "anthropic":
        return anthropic.Anthropic()
    if provider == "deepseek":
        return anthropic.Anthropic(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=DEEPSEEK_ANTHROPIC_BASE_URL,
        )
    raise ValueError(f"Unknown provider: {provider}")


def effective_model(provider: str, model: str | None) -> str:
    if model:
        return model
    if provider == "deepseek":
        return DEEPSEEK_MODEL
    return MODEL


def api_format(provider: str) -> str:
    if provider in VALID_PROVIDERS:
        return "anthropic"
    raise ValueError(f"Unknown provider: {provider}")


def provider_request_options(provider: str) -> dict:
    if provider == "deepseek":
        return {"thinking": DEEPSEEK_THINKING}
    return {}


def returned_model(response: object) -> str | None:
    value = getattr(response, "model", None)
    return str(value) if value is not None else None


def task_materials_dir(condition: str, task: str) -> Path:
    return MATERIALS_DIR / f"condition_{condition}" / task.lower()


def condition_d_db_paths(task: str) -> tuple[Path, Path]:
    db_dir = SEMANTIC_DB_DIR / "condition_D" / task.lower()
    return db_dir / "sem_d_trusted.db", db_dir / "sem_d_soft.db"


def result_db_paths(condition: str, task: str) -> dict[str, str]:
    if condition == "C":
        return {
            "trusted": display_path(MATERIALS_DIR / "sem_trusted.db"),
            "soft": display_path(MATERIALS_DIR / "sem_soft.db"),
        }
    if condition == "D":
        trusted_path, soft_path = condition_d_db_paths(task)
        return {
            "trusted": display_path(trusted_path),
            "soft": display_path(soft_path),
        }
    return {}


def result_db_task(condition: str, task: str) -> str | None:
    if condition == "C":
        return "T7"
    if condition == "D":
        return task
    return None


def result_db_scope(condition: str, task: str) -> str | None:
    if condition == "C":
        return "project_semantic_db_t7"
    if condition == "D":
        return f"task_scoped_condition_d:{task}"
    return None


def materials_scope(condition: str, task: str, mode: str) -> dict[str, str]:
    if mode == "direct":
        return {
            "type": "direct_materials",
            "materials_dir": display_path(task_materials_dir(condition, task)),
        }
    if condition == "C":
        mat_dir = task_materials_dir(condition, task)
        task_specific_files = available_material_files(mat_dir, ".nico")
        return {
            "type": "tool_use_c",
            "task_materials_dir": display_path(task_materials_dir(condition, task)),
            "fallback_dir": display_path(NICOLAS_ROOT / "src"),
            "fallback_policy": (
                "disabled_when_task_specific_nico_materials_exist"
                if task_specific_files else
                "enabled_for_legacy_tasks_without_task_specific_nico_materials"
            ),
        }
    if condition in ("A", "D"):
        return {
            "type": f"tool_use_{condition.lower()}",
            "materials_dir": display_path(task_materials_dir(condition, task)),
        }
    return {"type": "unknown"}


# ---------------------------------------------------------------------------
# Direct-mode helpers (v2, backward-compatible)
# ---------------------------------------------------------------------------

def collect_materials(task: str, condition: str) -> list[tuple[str, str]]:
    """Returns list of (filename, content) pairs for the given task/condition."""
    mat_dir = MATERIALS_DIR / f"condition_{condition}" / task.lower()
    if not mat_dir.exists():
        raise FileNotFoundError(f"Materials directory not found: {mat_dir}")
    files = sorted(mat_dir.iterdir())
    return [(f.name, load_text(f)) for f in files if f.is_file()]


def build_user_message(task_prompt: str, materials: list[tuple[str, str]]) -> str:
    parts = [task_prompt.strip(), "\n\n--- Source Materials ---\n"]
    for filename, content in materials:
        parts.append(f"\n=== {filename} ===\n{content.strip()}\n=== end of {filename} ===\n")
    return "\n".join(parts)


def run_direct(client: anthropic.Anthropic, system: str, user: str,
               run_num: int, dry_run: bool, model: str, provider: str) -> dict:
    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] System prompt:\n{system}")
        print(f"\n[DRY RUN] User message:\n{user}")
        print(f"{'='*60}\n")
        return {}

    print(f"  Run {run_num}: calling API...", end="", flush=True)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": user}],
        **provider_request_options(provider),
    )
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    response_text = response.content[0].text
    print(f" done. input={input_tokens} output={output_tokens} tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "response": response_text,
        "returned_model": returned_model(response),
    }


# ---------------------------------------------------------------------------
# Tool-use mode helpers (v3)
# ---------------------------------------------------------------------------

def load_system_prompt_v3(condition: str) -> str:
    """Load and prepare the v3 system prompt for the given condition.
    For condition C, injects the Nicolas LLM Manual in place of {{NICOLAS_MANUAL}}.
    """
    prompt_file = PROMPTS_DIR / f"system_prompt_v3_{condition}.txt"
    system = load_text(prompt_file).strip()
    if condition in ("C", "D") and "{{NICOLAS_MANUAL}}" in system:
        manual_file = "nicolas_llm_manual_D.md" if condition == "D" else "nicolas_llm_manual_v3.md"
        manual_path = MATERIALS_DIR / manual_file
        if not manual_path.exists():
            raise FileNotFoundError(
                f"Nicolas LLM Manual not found: {manual_path}\n"
                "Run build_db.py / parse_condition_d.py first to ensure materials are up to date."
            )
        manual = load_text(manual_path).strip()
        system = system.replace("{{NICOLAS_MANUAL}}", manual)
    return system


def load_task_prompt_v3(task: str, condition: str) -> str:
    """Load the v3 task prompt, with condition-specific protocol overlays."""
    task_prompt_path = PROMPTS_DIR / f"{task.lower()}_task_v3.txt"
    if not task_prompt_path.exists():
        raise FileNotFoundError(f"v3 task prompt not found: {task_prompt_path}")

    task_prompt = load_text(task_prompt_path).strip()
    if condition == "C":
        protocol_path = PROMPTS_DIR / "condition_c_protocol_v1.txt"
        if not protocol_path.exists():
            raise FileNotFoundError(f"Condition C protocol prompt not found: {protocol_path}")
        protocol = load_text(protocol_path).strip()
        task_prompt = f"{task_prompt}\n\n--- Condition C Protocol Override ---\n{protocol}"
    return task_prompt


def load_manual_tokens(condition: str = "C") -> int:
    """Load per-turn manual token count for the given condition.
    Condition C: manual_tokens.json (written by build_db.py).
    Condition D: manual_tokens_D.json (written by parse_condition_d.py).
    Returns 0 if the file is not found (safe fallback for condition A).
    """
    token_file_name = "manual_tokens_D.json" if condition == "D" else "manual_tokens.json"
    token_file = MATERIALS_DIR / token_file_name
    if not token_file.exists():
        return 0
    data = json.loads(token_file.read_text(encoding="utf-8"))
    return data.get("manual_tokens_per_turn", 0)


def get_tools(condition: str) -> list[dict]:
    """Return the tool definitions for the given condition."""
    read_file_A = {
        "name": "read_file",
        "description": (
            "Read a Rust source file by filename "
            "(e.g. 'clock.rs', 'profile_service.rs', 'store.rs', 'kv.rs', 'types.rs'). "
            "Only .rs files are accessible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filename or path of the .rs file to read."}
            },
            "required": ["path"],
        },
    }
    read_file_C = {
        "name": "read_file",
        "description": (
            "Last-resort full-file fallback: read an entire Nicolas source file (.nico) "
            "by path relative to the project root "
            "(e.g. 'src/time/clock.nico', 'src/user/store.nico', 'src/cache/kv.nico'). "
            "Prefer semantic_query for trusted structure and read_nico_section for partial source reads. "
            "Only .nico files are accessible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .nico file relative to the project root."}
            },
            "required": ["path"],
        },
    }
    run_sql = {
        "name": "run_sql",
        "description": (
            "Execute a SELECT query against the Nicolas Semantic DB (SQLite). "
            "Two schemas are available via prefixes: "
            "trusted.* (machine-derived structural facts — authoritative, no cross-verification needed): "
            "trusted.modules, trusted.imports, trusted.types, trusted.functions, "
            "trusted.effects, trusted.examples, trusted.propagated_effects, trusted.call_graph. "
            "trusted.call_graph columns: caller_module, caller_fn, callee_module, callee_fn — "
            "records every direct cross-module function call edge. "
            "soft.* (LLM-authored semantic content): soft.module_intent. "
            "All tables join on module_name. Only SELECT statements are allowed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A SELECT SQL query."}
            },
            "required": ["query"],
        },
    }
    read_nico_section = {
        "name": "read_nico_section",
        "description": (
            "Read one section from a Nicolas source file without loading the full file. "
            "Use surface for the full spec block, checks for the checks block, and "
            "implementation for the implementation rust block. Accepts .nico paths and "
            "DB source paths ending in .rs by mapping them to .nico."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the .nico file, or a DB source path ending in .rs.",
                },
                "section": {
                    "type": "string",
                    "enum": ["surface", "checks", "implementation"],
                    "description": "The source section to read.",
                },
            },
            "required": ["path", "section"],
        },
    }
    semantic_query = {
        "name": "semantic_query",
        "description": (
            "Run a compact high-level query over trusted Nicolas Semantic DB facts. "
            "Use this before SQL for common structure, dependency, caller, and effect-chain lookups. "
            "Supported query values: module_surface, module_dependents, type_dependents, "
            "function_callers, effect_chain, affected_modules."
            " For affected_modules, pass function when the requested effect update is"
            " scoped to a specific function."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "enum": [
                        "module_surface",
                        "module_dependents",
                        "type_dependents",
                        "function_callers",
                        "effect_chain",
                        "affected_modules",
                    ],
                    "description": "Which high-level trusted query to run.",
                },
                "module": {
                    "type": "string",
                    "description": "Module name, e.g. cache.kv or user.profile_service.",
                },
                "function": {
                    "type": "string",
                    "description": "Function name for function_callers or effect_chain.",
                },
                "type_name": {
                    "type": "string",
                    "description": "Type name for type_dependents, e.g. UserProfile.",
                },
                "effect": {
                    "type": "string",
                    "description": "Optional effect filter for effect_chain or affected_modules, e.g. reads_clock.",
                },
                "transitive": {
                    "type": "boolean",
                    "description": "Whether graph queries should include transitive paths.",
                },
            },
            "required": ["query"],
        },
    }
    edit_nico = {
        "name": "edit_nico",
        "description": (
            "Apply exact-anchor structured edits to a Nicolas source file in the "
            "condition C task workspace. The path may be a workspace .nico path or a "
            "DB source path ending in .rs; .rs paths are normalized to the matching "
            ".nico workspace file. Use this to make small audited source changes "
            "instead of rewriting complete modules in the final answer. Supports these "
            "ops: replace_text, insert_before, insert_after, insert_before_section_end, "
            "replace_identifier, insert_interface_item, replace_interface_item, "
            "update_interface_function_effects, update_module_imports, update_module_effects, "
            "insert_implementation_item, replace_implementation_function. "
            "Exact-anchor edits are scoped to surface, checks, "
            "implementation, or file. Structural ops locate the interface, module imports/effects, "
            "interface function effects, or implementation function for you. The entire edit "
            "batch is atomic: any failed check or "
            "source structure validation error leaves the file unchanged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the workspace .nico file, or a DB source path ending in .rs.",
                },
                "edits": {
                    "type": "array",
                    "description": "Ordered exact-anchor edits to apply atomically.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "replace_text",
                                    "insert_before",
                                    "insert_after",
                                    "insert_before_section_end",
                                    "replace_identifier",
                                    "insert_interface_item",
                                    "replace_interface_item",
                                    "update_interface_function_effects",
                                    "update_module_imports",
                                    "update_module_effects",
                                    "insert_implementation_item",
                                    "replace_implementation_function",
                                ],
                            },
                            "section": {
                                "type": "string",
                                "enum": ["surface", "checks", "implementation", "file"],
                                "description": "Source section to edit; file is allowed only for replacement ops.",
                            },
                            "target": {
                                "type": "string",
                                "description": "Exact anchor text or identifier to match.",
                            },
                            "item_kind": {
                                "type": "string",
                                "enum": ["type", "fn"],
                                "description": "Interface item kind for replace_interface_item.",
                            },
                            "name": {
                                "type": "string",
                                "description": "Interface item name for replace_interface_item.",
                            },
                            "function": {
                                "type": "string",
                                "description": "Function name for update_interface_function_effects or replace_implementation_function.",
                            },
                            "replacement": {
                                "type": "string",
                                "description": "Replacement text for replace_text, replace_identifier, replace_interface_item, or replace_implementation_function.",
                            },
                            "text": {
                                "type": "string",
                                "description": "Text to insert for insertion ops; replacement is accepted as a compatibility alias.",
                            },
                            "expected_count": {
                                "type": "integer",
                                "description": "Required match count for target-based ops; defaults to 1.",
                            },
                            "effects": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Effects for update_module_effects, e.g. ['reads_clock', 'metrics.write'].",
                            },
                            "imports": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Imports for update_module_imports, e.g. ['time.clock', 'metrics.recorder'].",
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["merge", "replace"],
                                "description": "For update_module_imports or update_module_effects: merge appends missing items; replace rewrites the list.",
                            },
                        },
                        "required": ["op"],
                    },
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, preview the diff without writing the workspace file.",
                },
            },
            "required": ["path", "edits"],
        },
    }

    read_file_D = {
        "name": "read_file",
        "description": (
            "Read a Rust source file by filename "
            "(e.g. 'clock.rs', 'profile_service.rs', 'store.rs', 'kv.rs', 'types.rs'). "
            "Only .rs files are accessible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filename or path of the .rs file to read."}
            },
            "required": ["path"],
        },
    }
    run_sql_D = dict(run_sql)
    run_sql_D = {
        "name": "run_sql",
        "description": (
            "Execute a SELECT query against the Condition D Semantic DB (SQLite). "
            "Two schemas are available via prefixes: "
            "trusted.* (annotation-derived structural facts — authoritative, no cross-verification needed): "
            "trusted.modules, trusted.imports, trusted.types, trusted.functions, "
            "trusted.effects, trusted.examples, trusted.propagated_effects, trusted.call_graph. "
            "trusted.call_graph columns: caller_module, caller_fn, callee_module, callee_fn — "
            "records every direct cross-module function call edge from @nico-fn annotations. "
            "soft.* (human-authored semantic content): soft.module_intent. "
            "All tables join on module_name. Only SELECT statements are allowed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A SELECT SQL query."}
            },
            "required": ["query"],
        },
    }

    if condition == "A":
        return [read_file_A]
    elif condition == "C":
        return [semantic_query, read_nico_section, edit_nico, run_sql, read_file_C]
    elif condition == "D":
        return [run_sql_D, read_file_D]
    else:
        raise ValueError(f"Unknown condition: {condition}")


def execute_tool(
    tool_name: str,
    tool_input: dict,
    condition: str,
    task: str,
    workspace: TaskWorkspace | None = None,
) -> str:
    """Execute a tool call and return the result as a string."""
    if tool_name == "read_file":
        raw_path = tool_input.get("path", "").strip()
        if condition == "A":
            return _read_file_condition_A(raw_path, task)
        elif condition == "C":
            return _read_file_condition_C(raw_path, task, workspace=workspace)
        elif condition == "D":
            return _read_file_condition_D(raw_path, task)
        else:
            return f"Error: unknown condition '{condition}'"

    elif tool_name == "run_sql":
        if condition == "C":
            return _run_sql(tool_input.get("query", "").strip())
        elif condition == "D":
            return _run_sql_condition_D(tool_input.get("query", "").strip(), task)
        else:
            return "Error: run_sql is not available in this condition."

    elif tool_name == "read_nico_section":
        if condition == "C":
            return _read_nico_section_condition_C(
                tool_input.get("path", "").strip(),
                tool_input.get("section", "").strip(),
                task,
                workspace=workspace,
            )
        return "Error: read_nico_section is only available in condition C."

    elif tool_name == "semantic_query":
        if condition == "C":
            return _semantic_query_condition_C(tool_input)
        return "Error: semantic_query is only available in condition C."

    elif tool_name == "edit_nico":
        if condition == "C":
            return _edit_nico_condition_C(tool_input, workspace=workspace)
        return "Error: edit_nico is only available in condition C."

    else:
        return f"Error: unknown tool '{tool_name}'"


def summarize_write_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Return a compact, auditable summary of edit_nico calls."""
    write_calls = []
    for index, call in enumerate(tool_calls, start=1):
        if call.get("tool") != "edit_nico":
            continue
        tool_input = call.get("input") if isinstance(call.get("input"), dict) else {}
        output = str(call.get("full_output", ""))
        parsed = _parse_tool_output_header(output)
        edits = tool_input.get("edits")
        result_status = parsed.get("status")
        if result_status is None:
            result_status = "error" if output.startswith("Error:") else "unknown"
        output_preview = call.get("output_preview")
        if output_preview is None:
            output_preview = output[:300] + ("..." if len(output) > 300 else "")
        write_calls.append({
            "call_index": index,
            "turn": call.get("turn"),
            "path": parsed.get("path") or tool_input.get("path"),
            "dry_run": _truthy_tool_input(tool_input.get("dry_run", False)),
            "edit_count": len(edits) if isinstance(edits, list) else None,
            "result_status": result_status,
            "diff_truncated": _parse_bool(parsed.get("diff_truncated")),
            "output_preview": output_preview,
        })
    return write_calls


def build_validation_summary(
    workspace: TaskWorkspace | None,
    changeset: dict | None,
    write_tool_calls: list[dict],
    response: str | None = None,
    turns: int | None = None,
    max_turns: int | None = None,
) -> dict | None:
    """Build mechanical validation metadata for scoring/audit.

    This summary intentionally does not claim compile/test success.
    """
    if workspace is None and changeset is None and not write_tool_calls:
        return None

    changed_files = changeset.get("changed_files", []) if changeset else []
    diffs = changeset.get("diffs", []) if changeset else []
    warnings = []
    audit_risk_flags = []
    error_calls = [call for call in write_tool_calls if call.get("result_status") == "error"]
    applied_calls = [call for call in write_tool_calls if call.get("result_status") == "applied"]
    dry_run_calls = [
        call for call in write_tool_calls
        if call.get("dry_run") or call.get("result_status") in {"dry_run", "no_change_dry_run"}
    ]

    if workspace is not None and changeset is None:
        warnings.append("workspace exists but no changeset was recorded")
        audit_risk_flags.append("missing_changeset")
    if changed_files and not write_tool_calls:
        warnings.append("workspace files changed without any recorded edit_nico call")
        audit_risk_flags.append("workspace_changed_without_edit_tool")
    if applied_calls and not changed_files:
        warnings.append("edit_nico reported applied edits but changeset has no changed files")
        audit_risk_flags.append("applied_edit_without_changed_files")
    if error_calls:
        warnings.append("one or more edit_nico calls returned an error")
        audit_risk_flags.append("edit_nico_error")
    if any("source_structure_invalid" in str(call.get("output_preview", "")) for call in error_calls):
        warnings.append("one or more edit_nico calls failed source structure validation")
        audit_risk_flags.append("source_structure_invalid")
    if dry_run_calls and not applied_calls and not changed_files:
        warnings.append("only dry-run edit_nico calls were recorded; no source change was applied")
        audit_risk_flags.append("dry_run_only_no_change")
    source_validation_errors = _changed_nico_source_validation_errors(workspace, changed_files)
    if source_validation_errors:
        warnings.extend(source_validation_errors)
        audit_risk_flags.append("source_validation_error")
        if any("source_structure_invalid" in error for error in source_validation_errors):
            audit_risk_flags.append("source_structure_invalid")
    if max_turns is not None and turns is not None and turns >= max_turns:
        warnings.append("max_turns reached before clean final answer")
        audit_risk_flags.append("max_turns_reached")
    missing_sections = _missing_c_final_sections(response)
    if missing_sections:
        warnings.append(f"final answer missing protocol sections: {', '.join(missing_sections)}")
        audit_risk_flags.append("final_answer_protocol_missing")
    if changed_files and _claims_no_workspace_changes(response):
        warnings.append("final answer claims no source edits, but workspace files changed")
        audit_risk_flags.append("final_answer_workspace_mismatch")

    return {
        "workspace_created": workspace is not None,
        "changeset_written": changeset is not None,
        "edit_nico_call_count": len(write_tool_calls),
        "applied_edit_count": sum(
            call.get("edit_count") or 0
            for call in write_tool_calls
            if call.get("result_status") == "applied"
        ),
        "changed_files_count": len(changed_files),
        "diffs_truncated": [
            diff.get("path")
            for diff in diffs
            if diff.get("diff_truncated")
        ],
        "audit_risk": bool(audit_risk_flags),
        "audit_risk_flags": _dedupe_preserving_order(audit_risk_flags),
        "warnings": warnings,
    }


def _changed_nico_source_validation_errors(
    workspace: TaskWorkspace | None,
    changed_files: list[str],
) -> list[str]:
    if workspace is None:
        return []
    errors: list[str] = []
    for rel_path in changed_files:
        if not isinstance(rel_path, str) or not rel_path.endswith(".nico"):
            continue
        path = workspace.root / rel_path
        if not path.exists():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"source_validation_error: {rel_path} is not valid UTF-8")
            continue
        structure_errors = validate_nico_source_structure(source)
        if structure_errors:
            errors.append(
                f"source_structure_invalid: {rel_path}: {'; '.join(structure_errors)}"
            )
    return errors


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _missing_c_final_sections(response: str | None) -> list[str]:
    if not response or not response.strip():
        return []
    required = {
        "Summary": "summary",
        "Evidence": "evidence",
        "Boundary/effects check": "boundary/effects check",
        "Validation status": "validation status",
    }
    found = _c_final_section_headings(response)
    return [label for label, normalized in required.items() if normalized not in found]


def _c_final_section_headings(response: str) -> set[str]:
    headings: set[str] = set()
    for line in response.splitlines():
        normalized = _normalize_c_final_heading(line)
        if normalized:
            headings.add(normalized)
    return headings


def _normalize_c_final_heading(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    text = re.sub(r"^#{1,6}\s+", "", text).strip()
    text = re.sub(r"^[-*]\s+", "", text).strip()
    text = re.sub(r"^\*\*(.*?)\*\*:?\s*$", r"\1", text).strip()
    text = re.sub(r"^__(.*?)__:?\s*$", r"\1", text).strip()
    text = text.rstrip(":").strip().lower()
    aliases = {
        "summary": "summary",
        "evidence": "evidence",
        "boundary/effects check": "boundary/effects check",
        "boundary / effects check": "boundary/effects check",
        "boundary and effects check": "boundary/effects check",
        "validation status": "validation status",
    }
    return aliases.get(text)


def _claims_no_workspace_changes(response: str | None) -> bool:
    if not response:
        return False
    lowered = response.lower()
    phrases = [
        "no workspace source changes were applied",
        "no source edits were applied",
        "no source changes were applied",
    ]
    return any(phrase in lowered for phrase in phrases)


def build_scoring_input(
    response: str,
    workspace_record: dict | None,
    changeset: dict | None,
    write_tool_calls: list[dict],
    validation_summary: dict | None,
) -> dict:
    """Build the compact evidence object consumed by evaluate.py."""
    return {
        "schema": "t3-scoring-input-v1",
        "final_answer": response,
        "workspace_path": workspace_record.get("path") if workspace_record else None,
        "changed_files": changeset.get("changed_files", []) if changeset else [],
        "changeset_summary": changeset.get("summary") if changeset else None,
        "compact_diffs": changeset.get("diffs", []) if changeset else [],
        "write_tool_calls": write_tool_calls,
        "validation_summary": validation_summary,
    }


def _parse_tool_output_header(output: str) -> dict[str, str]:
    parsed = {}
    for line in output.splitlines():
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"status", "path", "diff_truncated"}:
            parsed[key] = value.strip()
    return parsed


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    return None


def _truthy_tool_input(value: object) -> bool:
    parsed = _parse_bool(value)
    return False if parsed is None else parsed


def available_material_files(mat_dir: Path, suffix: str) -> list[str]:
    if not mat_dir.exists():
        return []
    return sorted(str(p.relative_to(mat_dir)) for p in mat_dir.rglob(f"*{suffix}") if p.is_file())


def _read_material_file(condition: str, path: str, task: str, suffix: str) -> str:
    p = Path(path)
    if not is_safe_relative_request(p):
        return f"Error: unsafe path rejected: '{path}'"
    if p.suffix != suffix:
        return f"Error: only {suffix} files are accessible in condition {condition} (got '{path}')"

    mat_dir = task_materials_dir(condition, task)
    candidate_paths = [mat_dir / p]
    if p.parts and p.parts[0] == "src":
        candidate_paths.append(mat_dir / Path(*p.parts[1:]))
    alias = RUST_SOURCE_ALIASES.get(p.as_posix())
    if alias:
        candidate_paths.append(mat_dir / alias)
    candidate_paths.append(mat_dir / p.name)

    for candidate in candidate_paths:
        if candidate.exists() and candidate.is_file() and candidate.suffix == suffix and path_is_under(candidate, mat_dir):
            return load_text(candidate)

    matches = [
        candidate for candidate in sorted(mat_dir.rglob(p.name))
        if candidate.is_file() and candidate.suffix == suffix and path_is_under(candidate, mat_dir)
    ] if mat_dir.exists() else []
    if len(matches) == 1:
        return load_text(matches[0])
    if len(matches) > 1:
        rel_matches = [str(m.relative_to(mat_dir)) for m in matches]
        return f"Error: ambiguous file name '{p.name}'. Use one of: {rel_matches}"

    return (
        f"Error: file not found: '{path}'. "
        f"Available files: {available_material_files(mat_dir, suffix)}"
    )


def _read_file_condition_A(path: str, task: str) -> str:
    """Serve .rs files from materials/condition_A/{task}/."""
    return _read_material_file("A", path, task, ".rs")


def _read_file_condition_C(path: str, task: str = "", workspace: TaskWorkspace | None = None) -> str:
    """Serve .nico files for condition C.

    Priority: task-specific materials directory first (e.g. materials/condition_C/t0/),
    then Nicolas/src/. This ensures that experiment-prepared "before" versions of
    .nico files (e.g. clock.nico with microseconds for T0) are served instead of
    the current production source, which may already be in the final state.
    """
    try:
        return load_text(_resolve_condition_c_nico_path(path, task, workspace=workspace))
    except (WorkspaceError, FileNotFoundError) as e:
        return f"Error: {e}"


def _read_nico_section_condition_C(
    path: str,
    section: str,
    task: str = "",
    workspace: TaskWorkspace | None = None,
) -> str:
    try:
        source_path = _resolve_condition_c_nico_path(path, task, workspace=workspace)
        section_text = extract_nico_section(load_text(source_path), section)
    except (WorkspaceError, FileNotFoundError, NicoSectionError) as e:
        return f"Error: {e}"
    return f"path: {display_path(source_path)}\nsection: {section}\n\n{section_text}"


def _semantic_query_condition_C(tool_input: dict) -> str:
    return run_semantic_query(tool_input, MATERIALS_DIR / "sem_trusted.db")


def _edit_nico_condition_C(tool_input: dict, workspace: TaskWorkspace | None = None) -> str:
    dry_run = tool_input.get("dry_run", False)
    if not isinstance(dry_run, bool):
        dry_run = str(dry_run).strip().lower() in {"1", "true", "yes", "y"}
    return apply_nico_edits(
        workspace,
        str(tool_input.get("path", "")).strip(),
        tool_input.get("edits", []),
        dry_run=dry_run,
    )


def _normalize_condition_c_nico_request(path: str) -> Path:
    p = Path(path.strip())
    if not is_safe_relative_request(p):
        raise WorkspaceError(f"unsafe path rejected: '{path}'")
    if p.suffix == ".rs":
        p = p.with_suffix(".nico")
    elif p.suffix != ".nico":
        raise WorkspaceError(f"only .nico files are accessible in condition C (got '{path}')")
    return p


def _resolve_condition_c_nico_path(
    path: str,
    task: str = "",
    workspace: TaskWorkspace | None = None,
) -> Path:
    p = _normalize_condition_c_nico_request(path)

    if workspace is not None:
        return resolve_workspace_file(workspace, p.as_posix(), suffix=".nico")

    # 1. Check task-specific materials directory first (experiment-prepared "before" version)
    if task:
        mat_dir = task_materials_dir("C", task)
        task_specific_files = available_material_files(mat_dir, ".nico")
        candidate_paths = [mat_dir / p]
        if p.parts and p.parts[0] == "src":
            candidate_paths.append(mat_dir / Path(*p.parts[1:]))
        candidate_paths.append(mat_dir / p.name)
        for candidate in candidate_paths:
            if (
                candidate.exists()
                and candidate.is_file()
                and candidate.suffix == ".nico"
                and path_is_under(candidate, mat_dir)
            ):
                return candidate
        if task_specific_files:
            raise FileNotFoundError(
                f".nico file not found in task-specific condition C materials for task {task}: "
                f"'{path}'. Available files: {task_specific_files}"
            )

    # 2. Fall back to Nicolas/src/ (production source)
    src_root = NICOLAS_ROOT / "src"
    candidates = [
        NICOLAS_ROOT / p if p.parts and p.parts[0] == "src" else src_root / p,
        src_root / p.name,
    ]
    for candidate in candidates:
        if (
            candidate.exists()
            and candidate.is_file()
            and candidate.suffix == ".nico"
            and path_is_under(candidate, src_root)
        ):
            return candidate

    matches = [
        candidate for candidate in sorted(src_root.rglob(p.name))
        if candidate.is_file() and candidate.suffix == ".nico" and path_is_under(candidate, src_root)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        rel_matches = [str(m.relative_to(src_root)) for m in matches]
        raise WorkspaceError(f"ambiguous .nico file name '{p.name}'. Use one of: {rel_matches}")

    raise FileNotFoundError(
        f".nico file not found for path '{path}'. "
        "Try a path like 'src/time/clock.nico' or 'src/user/store.nico'."
    )


def format_sql_rows(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "Query returned 0 rows."

    headers = list(rows[0].keys())
    col_widths = [max(len(h), max((len(str(r[h]) if r[h] is not None else "NULL") for r in rows), default=0))
                  for h in headers]
    sep = "-+-".join("-" * w for w in col_widths)
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))

    lines = [header_line, sep]
    for row in rows:
        lines.append(" | ".join(
            (str(row[h]) if row[h] is not None else "NULL").ljust(w)
            for h, w in zip(headers, col_widths)
        ))
    lines.append(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''} returned)")
    return "\n".join(lines)


def _run_sql(query: str) -> str:
    """Execute a SELECT query against the Semantic DB (trusted + soft via ATTACH)."""
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed."

    trusted_path = MATERIALS_DIR / "sem_trusted.db"
    soft_path = MATERIALS_DIR / "sem_soft.db"
    if not trusted_path.exists():
        return "Error: sem_trusted.db not found. Run build_db.py first."
    if not soft_path.exists():
        return "Error: sem_soft.db not found. Run build_db.py first."

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute(f"ATTACH DATABASE '{trusted_path}' AS trusted")
        conn.execute(f"ATTACH DATABASE '{soft_path}' AS soft")
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query)
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error as e:
        return f"SQL Error: {e}"

    return format_sql_rows(rows)


def _read_file_condition_D(path: str, task: str) -> str:
    """Serve .rs files from materials/condition_D/{task}/."""
    return _read_material_file("D", path, task, ".rs")


def validate_condition_d_db(task: str) -> tuple[bool, str | None]:
    trusted_path, soft_path = condition_d_db_paths(task)
    if not trusted_path.exists():
        return False, (
            f"sem_d_trusted.db not found for task={task}. "
            f"Run parse_condition_d.py --task {task} first. Expected: {display_path(trusted_path)}"
        )
    if not soft_path.exists():
        return False, (
            f"sem_d_soft.db not found for task={task}. "
            f"Run parse_condition_d.py --task {task} first. Expected: {display_path(soft_path)}"
        )
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute(f"ATTACH DATABASE '{trusted_path}' AS trusted")
        conn.execute(f"ATTACH DATABASE '{soft_path}' AS soft")
        trusted_task = conn.execute("SELECT value FROM trusted.metadata WHERE key='task'").fetchone()
        trusted_condition = conn.execute("SELECT value FROM trusted.metadata WHERE key='condition'").fetchone()
        soft_task = conn.execute("SELECT value FROM soft.metadata WHERE key='task'").fetchone()
        soft_condition = conn.execute("SELECT value FROM soft.metadata WHERE key='condition'").fetchone()
        conn.close()
    except sqlite3.Error as e:
        return False, f"Condition D DB metadata error for task={task}: {e}"

    expected_task = task.upper()
    metadata_ok = (
        trusted_task and trusted_task[0] == expected_task
        and soft_task and soft_task[0] == expected_task
        and trusted_condition and trusted_condition[0] == "D"
        and soft_condition and soft_condition[0] == "D"
    )
    if not metadata_ok:
        return False, f"Condition D DB metadata mismatch for task={task}."
    return True, None


def _run_sql_condition_D(query: str, task: str) -> str:
    """Execute a SELECT query against the Condition D Semantic DB."""
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed."

    ok, error = validate_condition_d_db(task)
    if not ok:
        return f"Error: {error}"

    trusted_path, soft_path = condition_d_db_paths(task)

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute(f"ATTACH DATABASE '{trusted_path}' AS trusted")
        conn.execute(f"ATTACH DATABASE '{soft_path}' AS soft")
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query)
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error as e:
        return f"SQL Error: {e}"

    return format_sql_rows(rows)


def run_tool_use(client: anthropic.Anthropic, system: str, task_prompt: str,
                 task: str, condition: str, run_num: int, dry_run: bool,
                 manual_tokens_per_turn: int, model: str,
                 provider: str,
                 workspace: TaskWorkspace | None = None) -> dict:
    """Run a single experiment in multi-turn tool_use mode."""
    tools = get_tools(condition)
    messages = [{"role": "user", "content": task_prompt}]
    tool_call_log: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    per_turn_input_tokens: list[int] = []
    per_turn_returned_models: list[str | None] = []
    turns = 0
    final_text = ""

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] System ({condition}):\n{system[:400]}...")
        print(f"\n[DRY RUN] Task:\n{task_prompt}")
        print(f"\n[DRY RUN] Tools: {[t['name'] for t in tools]}")
        if workspace is not None:
            print(f"\n[DRY RUN] Workspace: {workspace.root}")
        print(f"{'='*60}\n")
        return {}

    print(f"  Run {run_num}: ", end="", flush=True)

    while turns < MAX_TURNS:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system,
            tools=tools,
            messages=messages,
            **provider_request_options(provider),
        )
        turns += 1
        turn_input = response.usage.input_tokens
        total_input_tokens += turn_input
        total_output_tokens += response.usage.output_tokens
        per_turn_input_tokens.append(turn_input)
        per_turn_returned_models.append(returned_model(response))
        print(f"T{turns}(in={turn_input},out={response.usage.output_tokens})",
              end=" ", flush=True)

        # Accumulate any text blocks from this response
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, dict(block.input), condition, task, workspace=workspace)
                    preview = result[:300] + ("..." if len(result) > 300 else "")
                    tool_call_log.append({
                        "turn": turns,
                        "tool": block.name,
                        "input": dict(block.input),
                        "output_preview": preview,
                        "full_output": result,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            print(f"[unexpected stop_reason={response.stop_reason}]", end=" ")
            break

    if turns >= MAX_TURNS:
        print(f"\n  WARNING: max_turns ({MAX_TURNS}) reached — loop may be stuck.")

    # --- Token accounting (three layers) ---
    #
    # Layer 1: input_tokens (billing total)
    #   Sum of all turns' input_tokens. This is what the API charges.
    #   Each turn re-sends the full context, so earlier turns are counted multiple times.
    #
    # Layer 2: task_input_tokens (billing total minus manual overhead)
    #   Subtracts the manual's fixed per-turn cost (condition C only).
    #   Still double-counts conversation history but isolates task vs infra cost.
    #
    # Layer 3: true_task_tokens (unique information exchanged, no double-counting)
    #   = last_turn_input_tokens − manual_tokens_per_turn
    #   Reasoning: last_turn_input is the complete accumulated context snapshot.
    #   Sum of all turns telescopes to last_turn_input (each turn's delta cancels out).
    #   Subtracting manual_tokens removes the infra overhead; remaining is unique task content.
    #   For condition A (no manual), true_task_tokens ≈ last_turn_input (minimal system prompt).

    last_turn_input = per_turn_input_tokens[-1] if per_turn_input_tokens else 0
    has_manual = condition in ("C", "D")
    if provider == "deepseek":
        # DeepSeek's Anthropic-compatible endpoint reports usage with a
        # different accounting basis from Anthropic Messages. Keep the raw
        # provider usage numeric, but do not apply the Anthropic L2/L3 manual
        # subtraction formula because it can produce invalid negative values.
        manual_overhead_total = 0
        task_input_tokens = total_input_tokens
        true_task_tokens = last_turn_input
        token_accounting_method = "deepseek_provider_reported_usage_no_manual_subtraction"
        token_accounting_comparable_with_baseline = False
        token_accounting_note = (
            "DeepSeek Anthropic-compatible usage is not interpreted as the "
            "Anthropic cumulative-context token basis used by existing L2/L3 "
            "baselines; raw provider token fields are retained for diagnostics."
        )
    else:
        manual_overhead_total = manual_tokens_per_turn * turns if has_manual else 0
        task_input_tokens = total_input_tokens - manual_overhead_total
        true_task_tokens = last_turn_input - manual_tokens_per_turn if has_manual else last_turn_input
        token_accounting_method = "anthropic_cumulative_context_l1_l2_l3"
        token_accounting_comparable_with_baseline = True
        token_accounting_note = None

    print(
        f"done. total_input={total_input_tokens} last_turn_input={last_turn_input} "
        f"output={total_output_tokens} tool_calls={len(tool_call_log)} turns={turns}"
    )
    return {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "response": final_text.strip(),
        "mode": "tool_use",
        "tool_calls": tool_call_log,
        "tool_call_count": len(tool_call_log),
        "turns": turns,
        "per_turn_input_tokens": per_turn_input_tokens,
        "per_turn_returned_models": per_turn_returned_models,
        "returned_model": next((m for m in reversed(per_turn_returned_models) if m), None),
        "last_turn_input_tokens": last_turn_input,
        "manual_tokens_per_turn": manual_tokens_per_turn,
        "manual_overhead_total": manual_overhead_total,
        "task_input_tokens": task_input_tokens,
        "true_task_tokens": true_task_tokens,
        "token_accounting_method": token_accounting_method,
        "token_accounting_comparable_with_baseline": token_accounting_comparable_with_baseline,
        "token_accounting_note": token_accounting_note,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Nicolas experiment runner")
    parser.add_argument("--task", required=True, choices=VALID_TASKS)
    parser.add_argument("--condition", required=True, choices=VALID_CONDITIONS)
    parser.add_argument("--runs", type=int, default=3,
                        help="Number of runs (default 3)")
    parser.add_argument("--provider", choices=VALID_PROVIDERS, default="anthropic",
                        help="API provider (default: anthropic). DeepSeek uses the Anthropic-compatible API.")
    parser.add_argument("--model", default=None,
                        help=f"Model name (defaults: anthropic={MODEL}, deepseek={DEEPSEEK_MODEL})")
    parser.add_argument("--mode", choices=("direct", "tool_use"), default=None,
                        help="'direct': single-turn v2 mode; 'tool_use': multi-turn v3 mode. "
                             "Default: tool_use for E1-E6, direct for T0/T7.")
    parser.add_argument("--batch-id", default=None,
                        help="Optional batch identifier written to result JSON for rerun grouping.")
    parser.add_argument("--workspace-root", default=str(WORKSPACES_DIR),
                        help=f"Root directory for condition C task workspaces (default: {WORKSPACES_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts without calling the API")
    args = parser.parse_args()

    task = args.task
    condition = args.condition
    provider = args.provider
    model = effective_model(provider, args.model)
    mode = args.mode or ("tool_use" if task.startswith("E") else "direct")
    workspace_root = Path(args.workspace_root)

    RESULTS_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Direct mode (v2 — backward compatible)
    # ------------------------------------------------------------------
    if mode == "direct":
        system_prompt = load_text(PROMPTS_DIR / "system_prompt.txt").strip()
        task_prompt = load_text(PROMPTS_DIR / f"{task.lower()}_task.txt").strip()
        try:
            materials = collect_materials(task, condition)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"\nExperiment: task={task}  condition={condition}  runs={args.runs}  "
              f"provider={provider}  model={model}  mode=direct")
        print(f"Materials ({len(materials)} files): {[f for f, _ in materials]}")

        user_message = build_user_message(task_prompt, materials)

        if args.dry_run:
            run_direct(None, system_prompt, user_message, 1, dry_run=True, model=model, provider=provider)
            return

        client = create_api_client(provider)

        for run_num in range(1, args.runs + 1):
            result = run_direct(client, system_prompt, user_message, run_num, dry_run=False, model=model, provider=provider)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            write_tool_calls = []
            validation_summary = None
            scoring_input = build_scoring_input(
                result["response"], None, None, write_tool_calls, validation_summary
            )
            record = {
                "result_schema_version": RESULT_SCHEMA_VERSION,
                "task": task,
                "condition": condition,
                "run": run_num,
                "batch_id": args.batch_id,
                "model": model,
                "provider": provider,
                "requested_model": model,
                "returned_model": result.get("returned_model"),
                "per_turn_returned_models": [result.get("returned_model")],
                "api_format": api_format(provider),
                "thinking": DEEPSEEK_THINKING if provider == "deepseek" else None,
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
                "max_turns": None,
                "mode": "direct",
                "db_task": result_db_task(condition, task),
                "db_scope": result_db_scope(condition, task),
                "db_paths": result_db_paths(condition, task),
                "materials_scope": materials_scope(condition, task, mode),
                "materials": [f for f, _ in materials],
                "workspace_changeset": None,
                "write_tool_calls": write_tool_calls,
                "validation_summary": validation_summary,
                "scoring_input": scoring_input,
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "response": result["response"],
                "timestamp": timestamp,
                "scoring_status": "unscored",
                "task_success": None,
                "boundary_violation": None,
                "auditability": None,
                "notes": None,
                "compile_rate": None,
                "compile_rate_method": "not_run",
            }
            out_path = RESULTS_DIR / f"{task}_{condition}_run{run_num:02d}_{timestamp}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Saved: {out_path.name}")

    # ------------------------------------------------------------------
    # Tool-use mode (v3)
    # ------------------------------------------------------------------
    elif mode == "tool_use":
        try:
            system_prompt = load_system_prompt_v3(condition)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            task_prompt = load_task_prompt_v3(task, condition)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        manual_tokens_per_turn = load_manual_tokens(condition) if condition in ("C", "D") else 0
        if condition == "D":
            ok, error = validate_condition_d_db(task)
            if not ok:
                print(f"Error: {error}", file=sys.stderr)
                sys.exit(1)

        print(f"\nExperiment: task={task}  condition={condition}  runs={args.runs}  "
              f"provider={provider}  model={model}  mode=tool_use")
        if condition in ("C", "D"):
            print(f"Manual overhead: {manual_tokens_per_turn} tokens/turn")
        if condition in ("C", "D"):
            print(f"DB paths: {result_db_paths(condition, task)}")

        if args.dry_run:
            if condition == "C":
                run_id = f"{task}_{condition}_v3_run01_DRY_RUN"
                try:
                    workspace_plan = plan_task_workspace(task, condition, run_id, args.batch_id, workspace_root)
                except (WorkspaceError, FileNotFoundError) as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
                print("\n[DRY RUN] Workspace plan:")
                print(json.dumps(workspace_plan, ensure_ascii=False, indent=2))
            run_tool_use(None, system_prompt, task_prompt, task, condition,
                         1, dry_run=True, manual_tokens_per_turn=manual_tokens_per_turn,
                         model=model, provider=provider)
            return

        client = create_api_client(provider)

        for run_num in range(1, args.runs + 1):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            result_basename = f"{task}_{condition}_v3_run{run_num:02d}_{timestamp}"
            workspace = None
            if condition == "C":
                try:
                    workspace = prepare_task_workspace(
                        task, condition, result_basename, args.batch_id, workspace_root
                    )
                except (WorkspaceError, FileExistsError, FileNotFoundError) as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
                print(f"  Workspace: {display_path(workspace.root)}")

            result = run_tool_use(
                client, system_prompt, task_prompt,
                task, condition, run_num,
                dry_run=False,
                manual_tokens_per_turn=manual_tokens_per_turn,
                model=model,
                provider=provider,
                workspace=workspace,
            )
            workspace_record = None
            changed_files = []
            changeset = None
            if workspace is not None:
                changeset = compute_changeset(workspace)
                workspace_record = {
                    "path": display_path(workspace.root),
                    "source": workspace.manifest_before.get("source") if workspace.manifest_before else None,
                    "manifest_before": workspace.manifest_before,
                    "changeset": changeset,
                }
                changed_files = changeset.get("changed_files", [])
            write_tool_calls = summarize_write_tool_calls(result["tool_calls"])
            validation_summary = build_validation_summary(
                workspace,
                changeset,
                write_tool_calls,
                response=result["response"],
                turns=result.get("turns"),
                max_turns=MAX_TURNS,
            )
            scoring_input = build_scoring_input(
                result["response"], workspace_record, changeset, write_tool_calls, validation_summary
            )

            record = {
                "result_schema_version": RESULT_SCHEMA_VERSION,
                "task": task,
                "condition": condition,
                "run": run_num,
                "batch_id": args.batch_id,
                "model": model,
                "provider": provider,
                "requested_model": model,
                "returned_model": result.get("returned_model"),
                "per_turn_returned_models": result.get("per_turn_returned_models", []),
                "api_format": api_format(provider),
                "thinking": DEEPSEEK_THINKING if provider == "deepseek" else None,
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
                "max_turns": MAX_TURNS,
                "mode": "tool_use",
                "db_task": result_db_task(condition, task),
                "db_scope": result_db_scope(condition, task),
                "db_paths": result_db_paths(condition, task),
                "materials_scope": materials_scope(condition, task, mode),
                "workspace": workspace_record,
                "changed_files": changed_files,
                "workspace_changeset": changeset,
                "write_tool_calls": write_tool_calls,
                "validation_summary": validation_summary,
                "scoring_input": scoring_input,
                # Token fields — three accounting layers
                "input_tokens": result["input_tokens"],           # Layer 1: billing total (all turns summed)
                "output_tokens": result["output_tokens"],
                "per_turn_input_tokens": result["per_turn_input_tokens"],  # raw per-turn breakdown
                "last_turn_input_tokens": result["last_turn_input_tokens"],  # Layer 3 basis: complete accumulated context
                "manual_tokens_per_turn": result["manual_tokens_per_turn"],
                "manual_overhead_total": result["manual_overhead_total"],   # Layer 2: manual cost × turns
                "task_input_tokens": result["task_input_tokens"],           # Layer 2: total minus manual overhead
                "true_task_tokens": result["true_task_tokens"],             # Layer 3: last_turn − manual (no double-counting)
                "token_accounting_method": result["token_accounting_method"],
                "token_accounting_comparable_with_baseline": result["token_accounting_comparable_with_baseline"],
                "token_accounting_note": result["token_accounting_note"],
                # Tool-use fields
                "turns": result["turns"],
                "tool_call_count": result["tool_call_count"],
                "tool_calls": result["tool_calls"],
                # Response
                "response": result["response"],
                "timestamp": timestamp,
                "scoring_status": "unscored",
                "task_success": None,
                "boundary_violation": None,
                "auditability": None,
                "notes": None,
                "compile_rate": None,
                "compile_rate_method": "not_run",
            }
            out_path = RESULTS_DIR / f"{result_basename}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Saved: {out_path.name}")

    print(f"\nDone. {args.runs} run(s) saved to {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
