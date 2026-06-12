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
    return all(field in record for field in REQUIRED_SCORED_FIELDS)


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
            if is_fully_scored(rec):
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
