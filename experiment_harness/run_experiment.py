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
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv, find_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 4096
TEMPERATURE = 0        # deterministic; eliminates LLM randomness as a variable
MAX_TURNS = 25         # safety cap for multi-turn tool_use loops

HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
PROMPTS_DIR = HARNESS_DIR / "prompts"
RESULTS_DIR = HARNESS_DIR / "results"
NICOLAS_ROOT = HARNESS_DIR.parent   # Nicolas/ repo root

VALID_TASKS = ("T0", "T7")
VALID_CONDITIONS = ("A", "C")

# ---------------------------------------------------------------------------
# Load .env (search from harness dir upward; finds workspace root .env)
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
               run_num: int, dry_run: bool) -> dict:
    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] System prompt:\n{system}")
        print(f"\n[DRY RUN] User message:\n{user}")
        print(f"{'='*60}\n")
        return {}

    print(f"  Run {run_num}: calling API...", end="", flush=True)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    response_text = response.content[0].text
    print(f" done. input={input_tokens} output={output_tokens} tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "response": response_text,
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
    if condition == "C" and "{{NICOLAS_MANUAL}}" in system:
        manual_path = MATERIALS_DIR / "nicolas_llm_manual_v1.md"
        if not manual_path.exists():
            raise FileNotFoundError(
                f"Nicolas LLM Manual not found: {manual_path}\n"
                "Run build_db.py first to generate it."
            )
        manual = load_text(manual_path).strip()
        system = system.replace("{{NICOLAS_MANUAL}}", manual)
    return system


def load_manual_tokens() -> int:
    """Load per-turn manual token count computed by build_db.py.
    Returns 0 if the file is not found (safe fallback for condition A).
    """
    token_file = MATERIALS_DIR / "manual_tokens.json"
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
            "Read a Nicolas source file (.nico) by path relative to the project root "
            "(e.g. 'src/time/clock.nico', 'src/user/store.nico', 'src/cache/kv.nico'). "
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
            "Tables: modules, imports, types, functions, effects, examples. "
            "Only SELECT statements are allowed."
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
        return [run_sql, read_file_C]
    else:
        raise ValueError(f"Unknown condition: {condition}")


def execute_tool(tool_name: str, tool_input: dict, condition: str, task: str) -> str:
    """Execute a tool call and return the result as a string."""
    if tool_name == "read_file":
        raw_path = tool_input.get("path", "").strip()
        if condition == "A":
            return _read_file_condition_A(raw_path, task)
        elif condition == "C":
            return _read_file_condition_C(raw_path, task)
        else:
            return f"Error: unknown condition '{condition}'"

    elif tool_name == "run_sql":
        if condition != "C":
            return "Error: run_sql is not available in this condition."
        return _run_sql(tool_input.get("query", "").strip())

    else:
        return f"Error: unknown tool '{tool_name}'"


def _read_file_condition_A(path: str, task: str) -> str:
    """Serve .rs files from materials/condition_A/{task}/."""
    # Normalize: accept 'clock.rs', 'src/time/clock.rs', etc. → use basename
    basename = Path(path).name
    if not basename.endswith(".rs"):
        return f"Error: only .rs files are accessible in condition A (got '{basename}')"
    file_path = MATERIALS_DIR / "condition_A" / task.lower() / basename
    if not file_path.exists():
        available = [f.name for f in (MATERIALS_DIR / "condition_A" / task.lower()).iterdir()
                     if f.is_file()] if (MATERIALS_DIR / "condition_A" / task.lower()).exists() else []
        return (
            f"Error: file not found: '{basename}'. "
            f"Available files: {available}"
        )
    return load_text(file_path)


def _read_file_condition_C(path: str, task: str = "") -> str:
    """Serve .nico files for condition C.

    Priority: task-specific materials directory first (e.g. materials/condition_C/t0/),
    then Nicolas/src/. This ensures that experiment-prepared "before" versions of
    .nico files (e.g. clock.nico with microseconds for T0) are served instead of
    the current production source, which may already be in the final state.
    """
    p = Path(path)
    if p.suffix != ".nico":
        # Accept .rs paths and convert to .nico (LLM may derive path from DB source field)
        if p.suffix == ".rs":
            p = p.with_suffix(".nico")
        else:
            return f"Error: only .nico files are accessible in condition C (got '{path}')"

    # 1. Check task-specific materials directory first (experiment-prepared "before" version)
    if task:
        mat_dir = MATERIALS_DIR / "condition_C" / task.lower()
        for candidate in [mat_dir / p.name, mat_dir / p]:
            if candidate.exists() and candidate.suffix == ".nico":
                return load_text(candidate)

    # 2. Fall back to Nicolas/src/ (production source)
    candidates = [
        NICOLAS_ROOT / p,
        NICOLAS_ROOT / "src" / p,
        NICOLAS_ROOT / "src" / p.name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.suffix == ".nico":
            return load_text(candidate)

    return (
        f"Error: .nico file not found for path '{path}'. "
        "Try a path like 'src/time/clock.nico' or 'src/user/store.nico'."
    )


def _run_sql(query: str) -> str:
    """Execute a SELECT query against the Semantic DB."""
    if not query.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed."

    db_path = MATERIALS_DIR / "semantic.db"
    if not db_path.exists():
        return "Error: semantic.db not found. Run build_db.py first."

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query)
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error as e:
        return f"SQL Error: {e}"

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


def run_tool_use(client: anthropic.Anthropic, system: str, task_prompt: str,
                 task: str, condition: str, run_num: int, dry_run: bool,
                 manual_tokens_per_turn: int) -> dict:
    """Run a single experiment in multi-turn tool_use mode."""
    tools = get_tools(condition)
    messages = [{"role": "user", "content": task_prompt}]
    tool_call_log: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    per_turn_input_tokens: list[int] = []
    turns = 0
    final_text = ""

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] System ({condition}):\n{system[:400]}...")
        print(f"\n[DRY RUN] Task:\n{task_prompt}")
        print(f"\n[DRY RUN] Tools: {[t['name'] for t in tools]}")
        print(f"{'='*60}\n")
        return {}

    print(f"  Run {run_num}: ", end="", flush=True)

    while turns < MAX_TURNS:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system,
            tools=tools,
            messages=messages,
        )
        turns += 1
        turn_input = response.usage.input_tokens
        total_input_tokens += turn_input
        total_output_tokens += response.usage.output_tokens
        per_turn_input_tokens.append(turn_input)
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
                    result = execute_tool(block.name, dict(block.input), condition, task)
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
    manual_overhead_total = manual_tokens_per_turn * turns if condition == "C" else 0
    task_input_tokens = total_input_tokens - manual_overhead_total
    true_task_tokens = last_turn_input - manual_tokens_per_turn if condition == "C" else last_turn_input

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
        "last_turn_input_tokens": last_turn_input,
        "manual_tokens_per_turn": manual_tokens_per_turn,
        "manual_overhead_total": manual_overhead_total,
        "task_input_tokens": task_input_tokens,
        "true_task_tokens": true_task_tokens,
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
    parser.add_argument("--model", default=MODEL,
                        help=f"Anthropic model (default: {MODEL})")
    parser.add_argument("--mode", choices=("direct", "tool_use"), default="direct",
                        help="'direct': single-turn v2 mode; 'tool_use': multi-turn v3 mode")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts without calling the API")
    args = parser.parse_args()

    task = args.task
    condition = args.condition
    model = args.model
    mode = args.mode

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
              f"model={model}  mode=direct")
        print(f"Materials ({len(materials)} files): {[f for f, _ in materials]}")

        user_message = build_user_message(task_prompt, materials)

        if args.dry_run:
            run_direct(None, system_prompt, user_message, 1, dry_run=True)
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)

        for run_num in range(1, args.runs + 1):
            result = run_direct(client, system_prompt, user_message, run_num, dry_run=False)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            record = {
                "task": task,
                "condition": condition,
                "run": run_num,
                "model": model,
                "temperature": TEMPERATURE,
                "mode": "direct",
                "materials": [f for f, _ in materials],
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "response": result["response"],
                "timestamp": timestamp,
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

        task_prompt_path = PROMPTS_DIR / f"{task.lower()}_task_v3.txt"
        if not task_prompt_path.exists():
            print(f"Error: v3 task prompt not found: {task_prompt_path}", file=sys.stderr)
            sys.exit(1)
        task_prompt = load_text(task_prompt_path).strip()

        manual_tokens_per_turn = load_manual_tokens() if condition == "C" else 0

        print(f"\nExperiment: task={task}  condition={condition}  runs={args.runs}  "
              f"model={model}  mode=tool_use")
        if condition == "C":
            print(f"Manual overhead: {manual_tokens_per_turn} tokens/turn")

        if args.dry_run:
            run_tool_use(None, system_prompt, task_prompt, task, condition,
                         1, dry_run=True, manual_tokens_per_turn=manual_tokens_per_turn)
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)

        for run_num in range(1, args.runs + 1):
            result = run_tool_use(
                client, system_prompt, task_prompt,
                task, condition, run_num,
                dry_run=False,
                manual_tokens_per_turn=manual_tokens_per_turn,
            )
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            record = {
                "task": task,
                "condition": condition,
                "run": run_num,
                "model": model,
                "temperature": TEMPERATURE,
                "mode": "tool_use",
                # Token fields — three accounting layers
                "input_tokens": result["input_tokens"],           # Layer 1: billing total (all turns summed)
                "output_tokens": result["output_tokens"],
                "per_turn_input_tokens": result["per_turn_input_tokens"],  # raw per-turn breakdown
                "last_turn_input_tokens": result["last_turn_input_tokens"],  # Layer 3 basis: complete accumulated context
                "manual_tokens_per_turn": result["manual_tokens_per_turn"],
                "manual_overhead_total": result["manual_overhead_total"],   # Layer 2: manual cost × turns
                "task_input_tokens": result["task_input_tokens"],           # Layer 2: total minus manual overhead
                "true_task_tokens": result["true_task_tokens"],             # Layer 3: last_turn − manual (no double-counting)
                # Tool-use fields
                "turns": result["turns"],
                "tool_call_count": result["tool_call_count"],
                "tool_calls": result["tool_calls"],
                # Response
                "response": result["response"],
                "timestamp": timestamp,
            }
            out_path = RESULTS_DIR / f"{task}_{condition}_v3_run{run_num:02d}_{timestamp}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Saved: {out_path.name}")

    print(f"\nDone. {args.runs} run(s) saved to {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
