"""
Condition D DB Builder
======================
Parses @nico-* structured annotations from .rs files in
materials/condition_D/t7/ and builds two SQLite databases mirroring
the schema of sem_trusted.db / sem_soft.db:

  sem_d_trusted.db  — module facts derived from hand annotations
  sem_d_soft.db     — intent text from @nico-intent annotations

Annotation format (lines starting with // @nico-):

  // @nico-module: <module.name>
  // @nico-intent: <description>
  // @nico-imports: <m1>, <m2>       (comma-separated, may be empty)
  // @nico-module-effects: <e1>, <e2>  (comma-separated, may be empty)
  // @nico-type: <Name> | <visibility> | <repr>
  // @nico-fn: <name> | <signature> | effects=<e1,e2> | calls=<m1::f1,m2::f2>

All other lines are treated as normal Rust source (preserved as-is in
the .rs file but ignored by this parser).

The schema for sem_d_trusted.db / sem_d_soft.db is identical to the
schema produced by build_db.py so that run_experiment.py can use the
same _run_sql logic for both condition C and condition D.

Usage:
    python parse_condition_d.py
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
SEMANTIC_DB_DIR = MATERIALS_DIR / "semantic_db"
CONDITION_D_DB_ROOT = SEMANTIC_DB_DIR / "condition_D"
D_MANUAL_PATH = MATERIALS_DIR / "nicolas_llm_manual_D.md"
D_MANUAL_TOKENS_PATH = MATERIALS_DIR / "manual_tokens_D.json"
MODEL = "claude-sonnet-4-5"
DB_SCHEMA_VERSION = "0.1.0-draft-D-task-scoped"
VALID_TASKS = ("T7", "E1", "E2", "E3", "E4", "E5", "E6")
SEMANTIC_IMPORT_MODULES = {
    "audit.log",
    "cache.kv",
    "metrics.recorder",
    "session.store",
    "time.clock",
    "user.store",
    "user.types",
}
IGNORED_SIGNATURE_TYPES = {
    "Option",
    "Result",
    "String",
    "Vec",
    "bool",
    "str",
    "u64",
}

dotenv_path = find_dotenv(usecwd=False, raise_error_if_not_found=False)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    workspace_env = HARNESS_DIR.parent.parent / ".env"
    if workspace_env.exists():
        load_dotenv(workspace_env)

# Schema identical to build_db.py TRUSTED_SCHEMA / SOFT_SCHEMA
TRUSTED_SCHEMA = """
CREATE TABLE IF NOT EXISTS modules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    source         TEXT,
    schema_version TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name     TEXT NOT NULL,
    imported_module TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL,
    name        TEXT NOT NULL,
    visibility  TEXT,
    repr        TEXT
);

CREATE TABLE IF NOT EXISTS functions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL,
    name        TEXT NOT NULL,
    signature   TEXT,
    visibility  TEXT
);

CREATE TABLE IF NOT EXISTS effects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name   TEXT NOT NULL,
    function_name TEXT,
    effect        TEXT NOT NULL,
    scope         TEXT NOT NULL CHECK(scope IN ('module', 'function'))
);

CREATE TABLE IF NOT EXISTS examples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL,
    example_id  TEXT NOT NULL,
    path        TEXT
);

CREATE TABLE IF NOT EXISTS propagated_effects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name   TEXT NOT NULL,
    effect        TEXT NOT NULL,
    source_module TEXT NOT NULL,
    depth         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS call_graph (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_module TEXT NOT NULL,
    caller_fn     TEXT NOT NULL,
    callee_module TEXT NOT NULL,
    callee_fn     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SOFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS module_intent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL UNIQUE,
    intent      TEXT
);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Annotation parser
# ---------------------------------------------------------------------------

def parse_annotations(rs_path: Path) -> dict:
    """Parse @nico-* annotations from a .rs file into a module data dict."""
    data = {
        "module": None,
        "intent": None,
        "imports": [],
        "module_effects": [],
        "types": [],
        "functions": [],  # list of {name, signature, visibility, effects, calls}
        "source": rs_path.name,
        "schema_version": "0.1.0-draft-D",
    }

    for raw_line in rs_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("// @nico-"):
            continue
        # Strip prefix
        content = line[len("// @nico-"):]
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()

        if key == "module":
            data["module"] = value

        elif key == "intent":
            data["intent"] = value

        elif key == "imports":
            if value:
                data["imports"] = [m.strip() for m in value.split(",") if m.strip()]

        elif key == "module-effects":
            if value:
                data["module_effects"] = [e.strip() for e in value.split(",") if e.strip()]

        elif key == "type":
            # format: Name | visibility | repr
            parts = [p.strip() for p in value.split("|")]
            if len(parts) >= 3:
                data["types"].append({
                    "name": parts[0],
                    "visibility": parts[1] if parts[1] else "pub",
                    "repr": parts[2],
                })
            elif len(parts) == 2:
                data["types"].append({"name": parts[0], "visibility": parts[1], "repr": "opaque"})

        elif key == "fn":
            # format: name | signature | effects=e1,e2 | calls=m1::f1,m2::f2
            parts = [p.strip() for p in value.split("|")]
            fn_name = parts[0] if parts else ""
            fn_sig = parts[1] if len(parts) > 1 else ""
            fn_effects = []
            fn_calls = []

            for part in parts[2:]:
                if part.startswith("effects="):
                    raw = part[len("effects="):].strip()
                    fn_effects = [e.strip() for e in raw.split(",") if e.strip()]
                elif part.startswith("calls="):
                    raw = part[len("calls="):].strip()
                    for call_str in raw.split(","):
                        call_str = call_str.strip()
                        if "::" in call_str:
                            callee_mod, callee_fn = call_str.rsplit("::", 1)
                            fn_calls.append({
                                "callee_module": callee_mod.strip(),
                                "callee_fn": callee_fn.strip(),
                            })

            # Infer visibility from signature
            visibility = "pub" if fn_sig.startswith("pub ") else "private"

            data["functions"].append({
                "name": fn_name,
                "signature": fn_sig,
                "visibility": visibility,
                "effects": fn_effects,
                "calls": fn_calls,
            })

    return data


def source_module_from_crate_path(first: str, second: str) -> str:
    return f"{first}.{second}"


def source_imports(rs_text: str, current_module: str) -> set[str]:
    imports: set[str] = set()
    for match in re.finditer(r"^\s*use\s+crate::([A-Za-z_]\w*)::([A-Za-z_]\w*)", rs_text, re.MULTILINE):
        imports.add(source_module_from_crate_path(match.group(1), match.group(2)))

    parent = current_module.rsplit(".", 1)[0] if "." in current_module else ""
    if parent:
        for match in re.finditer(r"^\s*use\s+super::([A-Za-z_]\w*)", rs_text, re.MULTILINE):
            imports.add(f"{parent}.{match.group(1)}")

    return imports


def actual_function_signatures(rs_text: str) -> dict[str, str]:
    signatures: dict[str, str] = {}
    pattern = re.compile(r"\bpub\s+fn\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:->\s*[^{\n]+)?")
    for match in pattern.finditer(rs_text):
        signatures[match.group(1)] = " ".join(match.group(0).split())
    return signatures


def signature_type_names(signature: str) -> set[str]:
    names = set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", signature))
    return {name for name in names if name not in IGNORED_SIGNATURE_TYPES}


def validate_annotations(rs_path: Path, data: dict) -> list[str]:
    """Return annotation/source consistency errors for one annotated Rust file."""
    errors: list[str] = []
    rs_text = rs_path.read_text(encoding="utf-8")
    module = data.get("module") or "<missing-module>"

    for type_data in data.get("types", []):
        name = type_data["name"]
        if not re.search(rf"\b(struct|enum|type)\s+{re.escape(name)}\b", rs_text):
            errors.append(f"{rs_path.name}: @nico-type {name!r} does not match any Rust type declaration")

    actual_fns = actual_function_signatures(rs_text)
    for fn_data in data.get("functions", []):
        name = fn_data["name"]
        actual_sig = actual_fns.get(name)
        if actual_sig is None:
            errors.append(f"{rs_path.name}: @nico-fn {name!r} does not match any pub fn")
            continue
        for type_name in signature_type_names(fn_data.get("signature") or ""):
            if type_name not in actual_sig:
                errors.append(
                    f"{rs_path.name}: @nico-fn {name!r} signature mentions {type_name!r}, "
                    "but the Rust signature no longer does"
                )

    annotated_imports = set(data.get("imports", []))
    material_imports = source_imports(rs_text, module) & SEMANTIC_IMPORT_MODULES
    missing_imports = sorted(material_imports - annotated_imports)
    for missing in missing_imports:
        errors.append(f"{rs_path.name}: Rust source imports {missing!r}, but @nico-imports omits it")

    return errors


# ---------------------------------------------------------------------------
# DB population
# ---------------------------------------------------------------------------

def insert_module(trusted: sqlite3.Connection, soft: sqlite3.Connection, data: dict) -> None:
    module_name = data["module"]
    if not module_name:
        return

    trusted.execute(
        "INSERT OR REPLACE INTO modules (name, source, schema_version) VALUES (?, ?, ?)",
        (module_name, data.get("source"), data.get("schema_version")),
    )

    soft.execute(
        "INSERT OR REPLACE INTO module_intent (module_name, intent) VALUES (?, ?)",
        (module_name, data.get("intent")),
    )

    for imp in data.get("imports", []):
        trusted.execute(
            "INSERT INTO imports (module_name, imported_module) VALUES (?, ?)",
            (module_name, imp),
        )

    for t in data.get("types", []):
        trusted.execute(
            "INSERT INTO types (module_name, name, visibility, repr) VALUES (?, ?, ?, ?)",
            (module_name, t["name"], t.get("visibility"), t.get("repr")),
        )

    for fn in data.get("functions", []):
        trusted.execute(
            "INSERT INTO functions (module_name, name, signature, visibility) VALUES (?, ?, ?, ?)",
            (module_name, fn["name"], fn.get("signature"), fn.get("visibility")),
        )
        for eff in fn.get("effects", []):
            trusted.execute(
                "INSERT INTO effects (module_name, function_name, effect, scope) "
                "VALUES (?, ?, ?, 'function')",
                (module_name, fn["name"], eff),
            )
        for call in fn.get("calls", []):
            trusted.execute(
                "INSERT INTO call_graph (caller_module, caller_fn, callee_module, callee_fn) "
                "VALUES (?, ?, ?, ?)",
                (module_name, fn["name"], call["callee_module"], call["callee_fn"]),
            )

    for eff in data.get("module_effects", []):
        trusted.execute(
            "INSERT INTO effects (module_name, function_name, effect, scope) "
            "VALUES (?, NULL, ?, 'module')",
            (module_name, eff),
        )


def insert_metadata(conn: sqlite3.Connection, metadata: dict[str, str]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        sorted(metadata.items()),
    )


# ---------------------------------------------------------------------------
# propagated_effects (identical logic to build_db.py)
# ---------------------------------------------------------------------------

def compute_propagated_effects(trusted: sqlite3.Connection) -> None:
    adj: dict[str, list[str]] = defaultdict(list)
    for row in trusted.execute("SELECT module_name, imported_module FROM imports"):
        adj[row[0]].append(row[1])

    module_effects: dict[str, list[str]] = defaultdict(list)
    for row in trusted.execute(
        "SELECT module_name, effect FROM effects WHERE scope='module'"
    ):
        module_effects[row[0]].append(row[1])

    all_modules = [row[0] for row in trusted.execute("SELECT name FROM modules")]
    rows_to_insert: list[tuple] = []

    for module in all_modules:
        seen: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for dep in adj[module]:
            queue.append((dep, 1))
        visited_at: dict[str, int] = {}

        while queue:
            dep, depth = queue.popleft()
            if dep == module:
                continue
            prev = visited_at.get(dep)
            if prev is not None and prev <= depth:
                continue
            visited_at[dep] = depth
            for effect in module_effects.get(dep, []):
                if seen.get(effect, 999) > depth:
                    seen[effect] = depth
                    rows_to_insert.append((module, effect, dep, depth))
            for transitive_dep in adj[dep]:
                queue.append((transitive_dep, depth + 1))

    best: dict[tuple[str, str], tuple] = {}
    for row in rows_to_insert:
        key = (row[0], row[1])
        if key not in best or row[3] < best[key][3]:
            best[key] = row

    trusted.executemany(
        "INSERT INTO propagated_effects (module_name, effect, source_module, depth) "
        "VALUES (?, ?, ?, ?)",
        best.values(),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build Condition D Semantic DBs from annotated Rust materials")
    parser.add_argument(
        "--task",
        default="T7",
        help="Task materials directory under materials/condition_D (default: T7)",
    )
    args = parser.parse_args()
    task_id = args.task.upper()
    task_key = args.task.lower()
    if task_id not in VALID_TASKS:
        print(f"ERROR: invalid task {args.task!r}; expected one of: {', '.join(VALID_TASKS)}", file=sys.stderr)
        sys.exit(1)

    d_dir = MATERIALS_DIR / "condition_D" / task_key
    rs_files = sorted(d_dir.glob("*.rs")) if d_dir.exists() else []

    if not d_dir.exists():
        print(f"ERROR: task materials directory not found: {d_dir}", file=sys.stderr)
        sys.exit(1)
    if not rs_files:
        print(f"ERROR: no .rs files found in {d_dir}", file=sys.stderr)
        sys.exit(1)

    parsed_modules: list[tuple[Path, dict]] = []
    validation_errors: list[str] = []
    for rs_path in rs_files:
        data = parse_annotations(rs_path)
        if not data["module"]:
            print(f"  WARNING: no @nico-module annotation found in {rs_path.name}, skipping.")
            continue
        validation_errors.extend(validate_annotations(rs_path, data))
        parsed_modules.append((rs_path, data))

    if validation_errors:
        print("ERROR: Condition D annotation/source validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    db_dir = CONDITION_D_DB_ROOT / task_key
    trusted_path = db_dir / "sem_d_trusted.db"
    soft_path = db_dir / "sem_d_soft.db"

    print(f"Building Condition D Semantic DBs:")
    print(f"  trusted → {trusted_path}")
    print(f"  soft    → {soft_path}")
    print(f"  source  → {d_dir}")

    db_dir.mkdir(parents=True, exist_ok=True)

    for path in (trusted_path, soft_path):
        if path.exists():
            path.unlink()
            print(f"  Removed existing {path.name}")

    trusted = sqlite3.connect(str(trusted_path))
    soft = sqlite3.connect(str(soft_path))
    trusted.executescript(TRUSTED_SCHEMA)
    soft.executescript(SOFT_SCHEMA)

    print(f"\n  Parsing {len(parsed_modules)} annotated .rs files from {d_dir.relative_to(HARNESS_DIR)}")
    for rs_path, data in parsed_modules:
        insert_module(trusted, soft, data)
        print(f"    {rs_path.name}  →  module={data['module']}")

    print("\n  Computing propagated_effects...")
    compute_propagated_effects(trusted)

    metadata = {
        "condition": "D",
        "task": task_id,
        "source_dir": str(d_dir.relative_to(HARNESS_DIR)),
        "schema_version": DB_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    insert_metadata(trusted, metadata)
    insert_metadata(soft, metadata)

    trusted.commit()
    soft.commit()

    # Row counts
    print("\n  sem_d_trusted.db row counts:")
    for table in ["modules", "imports", "types", "functions", "effects", "examples",
                  "propagated_effects", "call_graph", "metadata"]:
        count = trusted.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table:25s}: {count} rows")

    print("\n  sem_d_soft.db row counts:")
    count = soft.execute("SELECT COUNT(*) FROM module_intent").fetchone()[0]
    print(f"    {'module_intent':25s}: {count} rows")
    metadata_count = soft.execute("SELECT COUNT(*) FROM metadata").fetchone()[0]
    print(f"    {'metadata':25s}: {metadata_count} rows")

    # Validation
    print("\n  [Validation] call_graph (all edges):")
    rows = trusted.execute(
        "SELECT caller_module, caller_fn, callee_module, callee_fn FROM call_graph "
        "ORDER BY caller_module, caller_fn, callee_module, callee_fn"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]}.{row[1]:35s} → {row[2]}.{row[3]}")

    print("\n  [Validation] propagated_effects:")
    rows = trusted.execute(
        "SELECT module_name, effect, source_module, depth FROM propagated_effects "
        "ORDER BY module_name, depth, effect"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]:35s} ← {row[2]} (depth={row[3]}) : {row[1]}")

    trusted.close()
    soft.close()
    print(f"\n  sem_d_trusted.db and sem_d_soft.db written to {db_dir.relative_to(HARNESS_DIR)}/")

    # Manual token count for condition D
    if not D_MANUAL_PATH.exists():
        print(f"\n  WARNING: {D_MANUAL_PATH.name} not found — skipping token count.")
    else:
        print(f"\n  Computing manual token count ({D_MANUAL_PATH.name})...")
        manual_content = D_MANUAL_PATH.read_text(encoding="utf-8")
        manual_tokens = count_manual_tokens(manual_content)
        token_data = {
            "manual_file": D_MANUAL_PATH.name,
            "manual_tokens_per_turn": manual_tokens,
            "note": (
                "Tokens contributed by the Condition D LLM Manual to each API call's "
                "input_tokens when used as the system prompt."
            ),
        }
        D_MANUAL_TOKENS_PATH.write_text(
            json.dumps(token_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  manual_tokens_per_turn = {manual_tokens}")
        print(f"  Saved to {D_MANUAL_TOKENS_PATH.name}")

    print("\nDone.")


def count_manual_tokens(manual_content: str) -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  WARNING: ANTHROPIC_API_KEY not set — using character estimate for token count.")
        return len(manual_content) // 3
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        minimal_msg = [{"role": "user", "content": "x"}]
        r_with = client.messages.count_tokens(
            model=MODEL, system=manual_content, messages=minimal_msg,
        )
        r_without = client.messages.count_tokens(model=MODEL, messages=minimal_msg)
        return r_with.input_tokens - r_without.input_tokens
    except AttributeError:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            minimal_msg = [{"role": "user", "content": "x"}]
            r_with = client.beta.messages.count_tokens(
                model=MODEL, system=manual_content, messages=minimal_msg,
                betas=["token-counting-2024-11-01"],
            )
            r_without = client.beta.messages.count_tokens(
                model=MODEL, messages=minimal_msg,
                betas=["token-counting-2024-11-01"],
            )
            return r_with.input_tokens - r_without.input_tokens
        except Exception as e:
            print(f"  WARNING: token count API failed ({e}) — using character estimate.")
            return len(manual_content) // 3
    except Exception as e:
        print(f"  WARNING: token count API failed ({e}) — using character estimate.")
        return len(manual_content) // 3


if __name__ == "__main__":
    main()
