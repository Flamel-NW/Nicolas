"""
Nicolas Experiment Evaluator
=============================
Human-assisted scoring script. Reads result JSONs from results/, shows each
response alongside the golden reference, and prompts for a score.

Usage:
    python evaluate.py --task T0
    python evaluate.py --task T7 --condition A
    python evaluate.py --task T0 --list          # list result files only
"""

import argparse
import json
import os
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).parent
RESULTS_DIR = HARNESS_DIR / "results"
GOLDEN_DIR = HARNESS_DIR / "materials" / "golden_reference"
CHANGESET_FILENAME = "changeset.json"

SCORES = {"0": 0.0, "0.5": 0.5, "1": 1.0, "s": "skip"}
REQUIRED_SCORED_FIELDS = (
    "task_success",
    "boundary_violation",
    "auditability",
    "notes",
    "compile_rate",
    "compile_rate_method",
)


def load_golden(task: str) -> str:
    path = GOLDEN_DIR / f"{task.lower()}.md"
    return path.read_text(encoding="utf-8") if path.exists() else "(golden reference not found)"


def result_version(path: Path, record: dict) -> str:
    if "_v3_" in path.name or record.get("mode") == "tool_use":
        return "v3"
    return "v2"


def is_fully_scored(record: dict) -> bool:
    if record.get("scoring_status") == "unscored":
        return False
    if not all(field in record for field in REQUIRED_SCORED_FIELDS):
        return False
    return (
        record.get("task_success") is not None
        and record.get("boundary_violation") is not None
        and record.get("auditability") is not None
        and record.get("compile_rate_method") is not None
    )


def list_results(
    task: str,
    condition: str | None,
    version: str | None = None,
    batch_id: str | None = None,
) -> list[Path]:
    cond_pat = "*" if condition is None else condition
    if version == "v3":
        pattern = f"{task}_{cond_pat}_v3_run*.json"
        files = sorted(RESULTS_DIR.glob(pattern))
    elif version == "v2":
        # v2 results don't have _v3_ in their names
        all_files = sorted(RESULTS_DIR.glob(f"{task}_{cond_pat}_run*.json"))
        files = [f for f in all_files if "_v3_" not in f.name]
    else:
        # All results for this task/condition
        pattern = f"{task}_{cond_pat}_*run*.json"
        files = sorted(RESULTS_DIR.glob(pattern))

    if batch_id is None:
        return sorted(files)
    filtered = []
    for path in files:
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("batch_id") == batch_id:
            filtered.append(path)
    return sorted(filtered)


def duplicate_result_groups(files: list[Path]) -> dict[tuple, list[Path]]:
    groups: dict[tuple, list[Path]] = {}
    for path in files:
        rec = json.loads(path.read_text(encoding="utf-8"))
        key = (rec.get("task"), rec.get("condition"), result_version(path, rec), rec.get("run"))
        groups.setdefault(key, []).append(path)
    return {key: paths for key, paths in groups.items() if len(paths) > 1}


def print_duplicate_error(duplicates: dict[tuple, list[Path]]) -> None:
    print("ERROR: duplicate result candidates found for the same (task, condition, version, run).")
    print("Specify --batch-id to select a rerun batch before listing, scoring, or summarizing.")
    for key, paths in sorted(duplicates.items()):
        print(f"  {key}:")
        for path in paths:
            rec = json.loads(path.read_text(encoding="utf-8"))
            print(f"    {path.name}  batch_id={rec.get('batch_id')!r}  timestamp={rec.get('timestamp')}")


def print_separator(char="=", width=72):
    print(char * width)


def print_tool_calls(record: dict) -> None:
    """Print a summary of tool calls for tool_use mode results."""
    tool_calls = record.get("tool_calls")
    if not tool_calls:
        return
    turns = record.get("turns", "?")
    count = record.get("tool_call_count", len(tool_calls))
    print_separator("-")
    print(f"TOOL CALLS ({count} calls across {turns} turns):")
    for i, tc in enumerate(tool_calls, 1):
        tool = tc.get("tool", "?")
        inp = tc.get("input", {})
        preview = tc.get("output_preview", "")
        turn = tc.get("turn", "?")
        # Format input compactly
        if tool == "run_sql":
            inp_str = inp.get("query", "")[:80].replace("\n", " ")
        else:
            inp_str = inp.get("path", str(inp))
        print(f"  [{i}] turn={turn}  {tool}({inp_str!r})")
        print(f"       → {preview[:120]}")


def build_scoring_input_from_record(record: dict) -> dict:
    """Return the evaluator-facing scoring context for old and new result JSON."""
    existing = record.get("scoring_input")
    if isinstance(existing, dict):
        return existing

    workspace_record = record.get("workspace") if isinstance(record.get("workspace"), dict) else None
    changeset = record.get("workspace_changeset")
    if not isinstance(changeset, dict) and workspace_record:
        changeset = workspace_record.get("changeset")
    if not isinstance(changeset, dict) and workspace_record:
        changeset = load_workspace_changeset(workspace_record)

    write_tool_calls = record.get("write_tool_calls")
    if not isinstance(write_tool_calls, list):
        write_tool_calls = fallback_write_tool_calls(record.get("tool_calls") or [])

    validation_summary = record.get("validation_summary")
    if not isinstance(validation_summary, dict):
        validation_summary = None

    return {
        "schema": "evaluator-fallback-scoring-input-v1",
        "final_answer": record.get("response", ""),
        "workspace_path": workspace_record.get("path") if workspace_record else None,
        "changed_files": changeset.get("changed_files", []) if isinstance(changeset, dict) else [],
        "changeset_summary": changeset.get("summary") if isinstance(changeset, dict) else None,
        "compact_diffs": changeset.get("diffs", []) if isinstance(changeset, dict) else [],
        "write_tool_calls": write_tool_calls,
        "validation_summary": validation_summary,
    }


def load_workspace_changeset(workspace_record: dict) -> dict | None:
    workspace_path = workspace_record.get("path")
    if not workspace_path:
        return None
    path = Path(workspace_path)
    if not path.is_absolute():
        path = HARNESS_DIR / path
    changeset_path = path / CHANGESET_FILENAME
    if not changeset_path.exists():
        return None
    try:
        return json.loads(changeset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def fallback_write_tool_calls(tool_calls: list[dict]) -> list[dict]:
    write_calls = []
    for index, call in enumerate(tool_calls, start=1):
        if call.get("tool") != "edit_nico":
            continue
        tool_input = call.get("input") if isinstance(call.get("input"), dict) else {}
        output = str(call.get("full_output", ""))
        parsed = parse_tool_output_header(output)
        edits = tool_input.get("edits")
        status = parsed.get("status")
        if status is None:
            status = "error" if output.startswith("Error:") else "unknown"
        write_calls.append({
            "call_index": index,
            "turn": call.get("turn"),
            "path": parsed.get("path") or tool_input.get("path"),
            "dry_run": truthy_tool_input(tool_input.get("dry_run", False)),
            "edit_count": len(edits) if isinstance(edits, list) else None,
            "result_status": status,
            "diff_truncated": parse_bool(parsed.get("diff_truncated")),
            "output_preview": call.get("output_preview", output[:300]),
        })
    return write_calls


def parse_tool_output_header(output: str) -> dict[str, str]:
    parsed = {}
    for line in output.splitlines():
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"status", "path", "diff_truncated"}:
            parsed[key] = value.strip()
    return parsed


def parse_bool(value: object) -> bool | None:
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


def truthy_tool_input(value: object) -> bool:
    parsed = parse_bool(value)
    return False if parsed is None else parsed


def print_scoring_context(record: dict) -> None:
    scoring_input = build_scoring_input_from_record(record)
    workspace_path = scoring_input.get("workspace_path")
    changed_files = scoring_input.get("changed_files") or []
    write_tool_calls = scoring_input.get("write_tool_calls") or []
    compact_diffs = scoring_input.get("compact_diffs") or []
    validation_summary = scoring_input.get("validation_summary")
    changeset_summary = scoring_input.get("changeset_summary")

    if not any([workspace_path, changed_files, write_tool_calls, compact_diffs, validation_summary]):
        return

    print_separator("-")
    print("SCORING INPUT / WORKSPACE AUDIT:")
    if workspace_path:
        print(f"Workspace: {workspace_path}")
    if changeset_summary is not None:
        print(f"Changeset summary: {changeset_summary}")
    if changed_files:
        print(f"Changed files: {', '.join(changed_files)}")
    else:
        print("Changed files: (none)")

    print("\nWRITE TOOL CALLS:")
    if write_tool_calls:
        for call in write_tool_calls:
            print(
                f"  [{call.get('call_index', '?')}] turn={call.get('turn', '?')} "
                f"status={call.get('result_status')} path={call.get('path')} "
                f"edits={call.get('edit_count')} dry_run={call.get('dry_run')} "
                f"diff_truncated={call.get('diff_truncated')}"
            )
            preview = str(call.get("output_preview") or "")
            if preview:
                print(f"       -> {preview[:160]}")
    else:
        print("  (none)")

    if compact_diffs:
        print("\nCOMPACT DIFFS:")
        for diff in compact_diffs:
            path = diff.get("path", "?")
            status = diff.get("status", "?")
            truncated = diff.get("diff_truncated")
            print_separator(".")
            print(f"{path}  status={status}  diff_truncated={truncated}")
            print(diff.get("diff") or "(no textual diff)")

    if validation_summary:
        print("\nVALIDATION SUMMARY:")
        for key, value in validation_summary.items():
            if key == "warnings":
                continue
            print(f"  {key}: {value}")
        warnings = validation_summary.get("warnings") or []
        if warnings:
            print("  warnings:")
            for warning in warnings:
                print(f"    - {warning}")


def score_result(path: Path, golden: str, idx: int, total: int) -> dict | None:
    record = json.loads(path.read_text(encoding="utf-8"))
    task = record["task"]
    condition = record["condition"]
    run = record["run"]
    model = record["model"]
    mode = record.get("mode", "direct")
    input_tok = record["input_tokens"]
    output_tok = record["output_tokens"]
    task_input_tok = record.get("task_input_tokens", input_tok)
    manual_overhead = record.get("manual_overhead_total", 0)
    response = record["response"]

    print_separator()
    print(f"[{idx}/{total}] Task={task}  Condition={condition}  Run={run}  Model={model}  Mode={mode}")
    if mode == "tool_use":
        true_tok = record.get("true_task_tokens", "n/a")
        print(f"Input tokens: {input_tok} total  "
              f"({task_input_tok} task + {manual_overhead} manual overhead)  "
              f"true_task={true_tok}  Output: {output_tok}  Turns: {record.get('turns', '?')}")
        print_tool_calls(record)
        print_scoring_context(record)
    else:
        print(f"Input tokens: {input_tok}   Output tokens: {output_tok}")
    print_separator("-")
    print("GOLDEN REFERENCE:")
    print_separator("-")
    print(golden)
    print_separator("-")
    print("LLM RESPONSE:")
    print_separator("-")
    print(response)
    print_separator()

    while True:
        raw = input("Score [0 / 0.5 / 1 / s=skip / q=quit]: ").strip().lower()
        if raw == "q":
            return None
        if raw in SCORES:
            score = SCORES[raw]
            if score == "skip":
                print("Skipped.\n")
                return {"skipped": True, **record}
            break
        print("  Invalid input. Enter 0, 0.5, 1, s, or q.")

    auditable = ""
    while auditable not in ("strong", "weak", "s"):
        auditable = input("Reasoning auditability [strong / weak / s=skip]: ").strip().lower()

    notes = input("Notes (optional, press Enter to skip): ").strip()

    boundary_raw = ""
    while boundary_raw not in ("0", "1", "s"):
        boundary_raw = input("Boundary violation [0 / 1 / s=skip/unknown]: ").strip().lower()

    compile_raw = ""
    while compile_raw not in ("0", "1", "n"):
        compile_raw = input("Compile rate [0 / 1 / n=not_run]: ").strip().lower()

    if compile_raw == "n":
        compile_rate = None
        compile_rate_method = "not_run"
    else:
        compile_rate = float(compile_raw)
        compile_rate_method = "manual"

    result = {
        **record,
        "scoring_status": "scored",
        "task_success": score,
        "boundary_violation": None if boundary_raw == "s" else int(boundary_raw),
        "auditability": auditable if auditable != "s" else None,
        "notes": notes or None,
        "compile_rate": compile_rate,
        "compile_rate_method": compile_rate_method,
    }
    print(f"  Recorded: task_success={score}\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="Nicolas experiment evaluator")
    parser.add_argument("--task", required=True, choices=("T0", "T7", "E1", "E2", "E3", "E4", "E5", "E6"))
    parser.add_argument("--condition", choices=("A", "C", "D"), default=None,
                        help="Filter by condition (default: both)")
    parser.add_argument("--version", choices=("v2", "v3"), default=None,
                        help="Filter by experiment version: v2 (direct) or v3 (tool_use). "
                             "Default: show all versions.")
    parser.add_argument("--batch-id", default=None,
                        help="Filter by result batch_id. Required when duplicate run candidates exist.")
    parser.add_argument("--list", action="store_true", help="List result files and exit")
    args = parser.parse_args()

    files = list_results(args.task, args.condition, version=args.version, batch_id=args.batch_id)
    if not files:
        print(f"No result files found for task={args.task} condition={args.condition or 'any'}")
        return

    duplicates = duplicate_result_groups(files)
    if duplicates and args.batch_id is None:
        print_duplicate_error(duplicates)
        sys.exit(1)

    if args.list:
        print(f"Found {len(files)} result file(s):")
        for f in files:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if rec.get("scoring_status") == "unscored":
                status = "[not scored]"
            elif is_fully_scored(rec):
                status = "[scored-complete]"
            elif "task_success" in rec:
                status = "[scored-incomplete]"
            else:
                status = "[not scored]"
            print(f"  {f.name}  {status}  batch_id={rec.get('batch_id')!r}")
        return

    golden = load_golden(args.task)
    scored_results = []
    total = len(files)

    print(f"\nEvaluating {total} result(s) for task={args.task}")
    print("Press q at any prompt to save progress and quit.\n")

    for idx, path in enumerate(files, 1):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if is_fully_scored(rec):
            print(f"[{idx}/{total}] Already scored: {path.name} (task_success={rec['task_success']}) — skipping")
            scored_results.append(rec)
            continue
        if "task_success" in rec:
            missing = [field for field in REQUIRED_SCORED_FIELDS if field not in rec]
            print(f"[{idx}/{total}] Scored but incomplete: {path.name} missing={missing} — rescoring")

        result = score_result(path, golden, idx, total)
        if result is None:
            print("Quit. Progress saved for already-scored files.")
            break
        if not result.get("skipped"):
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Saved scores to {path.name}")
        scored_results.append(result)

    # Summary
    completed = [r for r in scored_results if "task_success" in r and not r.get("skipped")]
    if completed:
        print("\n" + "=" * 72)
        print("SUMMARY")
        print("=" * 72)
        by_condition: dict[str, list] = {}
        for r in completed:
            by_condition.setdefault(r["condition"], []).append(r)
        for cond, runs in sorted(by_condition.items()):
            scores = [r["task_success"] for r in runs]
            avg = sum(scores) / len(scores)
            tokens_in = [r["input_tokens"] for r in runs]
            tokens_out = [r["output_tokens"] for r in runs]
            avg_in = sum(tokens_in) / len(tokens_in)
            avg_out = sum(tokens_out) / len(tokens_out)
            mode = runs[0].get("mode", "direct")
            line = (f"Condition {cond}: task_success={scores}  avg={avg:.2f}  "
                    f"avg_input_tokens={avg_in:.0f}  avg_output_tokens={avg_out:.0f}")
            if mode == "tool_use":
                task_toks = [r.get("task_input_tokens", r["input_tokens"]) for r in runs]
                true_toks = [r.get("true_task_tokens", r["input_tokens"]) for r in runs]
                avg_task = sum(task_toks) / len(task_toks)
                avg_true = sum(true_toks) / len(true_toks)
                avg_turns = sum(r.get("turns", 1) for r in runs) / len(runs)
                line += (f"  avg_task_input_tokens={avg_task:.0f}"
                         f"  avg_true_task_tokens={avg_true:.0f}"
                         f"  avg_turns={avg_turns:.1f}")
            print(line)


if __name__ == "__main__":
    main()
