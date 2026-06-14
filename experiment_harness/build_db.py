"""
Nicolas Semantic DB Builder
===========================
Builds two SQLite databases from Semantic JSON files:

  sem_trusted.db  — machine-derived structural facts (tool-guaranteed)
  sem_soft.db     — LLM-authored semantic content

Tables:
  trusted: modules, imports, types, functions, effects, examples,
           propagated_effects
  soft:    module_intent

propagated_effects are computed mechanically from the import graph +
trusted.effects after all modules are loaded (BFS, minimum-depth dedup).

NOTE (technical debt): trusted.effects and trusted.propagated_effects are
currently populated from .nico spec declarations, not from Rust static
analysis. Rust-AST-based derivation is T2 work. The data lives in the
trusted schema because that is the architectural target; the derivation
tool is not yet implemented.

Also computes the per-turn token overhead of the Nicolas LLM Manual
and writes it to materials/manual_tokens.json for use by run_experiment.py.

Usage:
    python build_db.py
"""

import json
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
TRUSTED_DB_PATH = MATERIALS_DIR / "sem_trusted.db"
SOFT_DB_PATH = MATERIALS_DIR / "sem_soft.db"
JSON_DIR = MATERIALS_DIR / "condition_C" / "t7"
MANUAL_PATH = MATERIALS_DIR / "nicolas_llm_manual_v3.md"
MANUAL_TOKENS_PATH = MATERIALS_DIR / "manual_tokens.json"
MODEL = "claude-sonnet-4-5"

# ---------------------------------------------------------------------------
# Load .env. API credentials are managed there and read by SDK clients.
# ---------------------------------------------------------------------------
dotenv_path = find_dotenv(usecwd=False, raise_error_if_not_found=False)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    workspace_env = HARNESS_DIR.parent.parent / ".env"
    if workspace_env.exists():
        load_dotenv(workspace_env)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
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
"""

SOFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS module_intent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL UNIQUE,
    intent      TEXT
);
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def create_trusted_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TRUSTED_SCHEMA)


def create_soft_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SOFT_SCHEMA)


def insert_module(
    trusted: sqlite3.Connection,
    soft: sqlite3.Connection,
    data: dict,
) -> None:
    module_name = data["module"]

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

    provides = data.get("provides", {})

    for t in provides.get("types", []):
        trusted.execute(
            "INSERT INTO types (module_name, name, visibility, repr) VALUES (?, ?, ?, ?)",
            (module_name, t["name"], t.get("visibility"), t.get("repr")),
        )

    for fn in provides.get("functions", []):
        trusted.execute(
            "INSERT INTO functions (module_name, name, signature, visibility) VALUES (?, ?, ?, ?)",
            (module_name, fn["name"], fn.get("signature"), fn.get("visibility")),
        )
        for eff in fn.get("effects", []):
            trusted.execute(
                "INSERT INTO effects (module_name, function_name, effect, scope) VALUES (?, ?, ?, 'function')",
                (module_name, fn["name"], eff),
            )
        for call in fn.get("calls", []):
            trusted.execute(
                "INSERT INTO call_graph (caller_module, caller_fn, callee_module, callee_fn) VALUES (?, ?, ?, ?)",
                (module_name, fn["name"], call["callee_module"], call["callee_fn"]),
            )

    for eff in data.get("effects", []):
        trusted.execute(
            "INSERT INTO effects (module_name, function_name, effect, scope) VALUES (?, NULL, ?, 'module')",
            (module_name, eff),
        )

    for ex in data.get("examples", []):
        trusted.execute(
            "INSERT INTO examples (module_name, example_id, path) VALUES (?, ?, ?)",
            (module_name, ex["id"], ex.get("path")),
        )

    for pe in data.get("propagated_effects", []):
        trusted.execute(
            "INSERT INTO propagated_effects "
            "(module_name, effect, source_module, depth) VALUES (?, ?, ?, ?)",
            (module_name, pe["effect"], pe["source_module"], pe["depth"]),
        )


# ---------------------------------------------------------------------------
# propagated_effects computation (BFS, minimum-depth dedup)
# ---------------------------------------------------------------------------

def compute_propagated_effects(trusted: sqlite3.Connection) -> None:
    """
    Traverse the import graph and write propagated_effects.

    For each module M, follow transitive imports via BFS. For every
    dependency D encountered at depth d, insert one row per module-level
    effect of D into M's propagated_effects — unless a row for that
    (module_name, effect) pair already exists at a smaller depth.
    """
    # Build import adjacency: module -> list of direct imports
    adj: dict[str, list[str]] = defaultdict(list)
    for row in trusted.execute("SELECT module_name, imported_module FROM imports"):
        adj[row[0]].append(row[1])

    # Collect module-level effects per module
    module_effects: dict[str, list[str]] = defaultdict(list)
    for row in trusted.execute(
        "SELECT module_name, effect FROM effects WHERE scope='module'"
    ):
        module_effects[row[0]].append(row[1])

    all_modules = [row[0] for row in trusted.execute("SELECT name FROM modules")]

    rows_to_insert: list[tuple] = []

    for module in all_modules:
        seen: dict[str, int] = {}  # effect -> minimum depth already recorded

        queue: deque[tuple[str, int]] = deque()
        for dep in adj[module]:
            queue.append((dep, 1))

        visited_at: dict[str, int] = {}  # dep -> minimum depth visited

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

    # Deduplicate: keep only minimum-depth row per (module_name, effect)
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
# Token counting
# ---------------------------------------------------------------------------

def count_manual_tokens(manual_content: str) -> int:
    try:
        import anthropic
        client = anthropic.Anthropic()
        minimal_msg = [{"role": "user", "content": "x"}]

        r_with = client.messages.count_tokens(
            model=MODEL,
            system=manual_content,
            messages=minimal_msg,
        )
        r_without = client.messages.count_tokens(
            model=MODEL,
            messages=minimal_msg,
        )
        return r_with.input_tokens - r_without.input_tokens
    except AttributeError:
        try:
            import anthropic
            client = anthropic.Anthropic()
            minimal_msg = [{"role": "user", "content": "x"}]
            r_with = client.beta.messages.count_tokens(
                model=MODEL,
                system=manual_content,
                messages=minimal_msg,
                betas=["token-counting-2024-11-01"],
            )
            r_without = client.beta.messages.count_tokens(
                model=MODEL,
                messages=minimal_msg,
                betas=["token-counting-2024-11-01"],
            )
            return r_with.input_tokens - r_without.input_tokens
        except Exception as e:
            print(f"  WARNING: token count API failed ({e}) — using character estimate.")
            return len(manual_content) // 3
    except Exception as e:
        print(f"  WARNING: token count API failed ({e}) — using character estimate.")
        return len(manual_content) // 3


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Building Semantic DBs:")
    print(f"  trusted → {TRUSTED_DB_PATH}")
    print(f"  soft    → {SOFT_DB_PATH}")

    for path in (TRUSTED_DB_PATH, SOFT_DB_PATH):
        if path.exists():
            path.unlink()
            print(f"  Removed existing {path.name}")

    trusted = sqlite3.connect(str(TRUSTED_DB_PATH))
    soft = sqlite3.connect(str(SOFT_DB_PATH))
    create_trusted_schema(trusted)
    create_soft_schema(soft)

    json_files = sorted(JSON_DIR.glob("*.json"))
    if not json_files:
        print(f"ERROR: no JSON files found in {JSON_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Loading {len(json_files)} JSON files from {JSON_DIR.relative_to(HARNESS_DIR)}")
    for json_path in json_files:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        insert_module(trusted, soft, data)
        print(f"    {json_path.name}  →  module={data['module']}")

    pe_count = trusted.execute("SELECT COUNT(*) FROM propagated_effects").fetchone()[0]
    if pe_count == 0:
        print("\n  Computing propagated_effects (BFS fallback — not in JSON)...")
        compute_propagated_effects(trusted)
    else:
        print(f"\n  propagated_effects: read from JSON ({pe_count} rows already inserted).")

    trusted.commit()
    soft.commit()

    # Row counts
    print("\n  sem_trusted.db row counts:")
    for table in ["modules", "imports", "types", "functions", "effects", "examples", "propagated_effects", "call_graph"]:
        count = trusted.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table:25s}: {count} rows")

    print("\n  sem_soft.db row counts:")
    count = soft.execute("SELECT COUNT(*) FROM module_intent").fetchone()[0]
    print(f"    {'module_intent':25s}: {count} rows")

    # Validation queries
    print("\n  [Validation] Functions with reads_clock effect (trusted.effects):")
    rows = trusted.execute(
        "SELECT module_name, function_name FROM effects "
        "WHERE effect='reads_clock' AND scope='function' ORDER BY module_name"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]}.{row[1]}")

    print("\n  [Validation] All function-level effects (for get_profile call chain):")
    rows = trusted.execute(
        "SELECT module_name, function_name, effect FROM effects "
        "WHERE scope='function' ORDER BY module_name, function_name, effect"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]}.{row[1]:30s} → {row[2]}")

    print("\n  [Validation] propagated_effects sample:")
    rows = trusted.execute(
        "SELECT module_name, effect, source_module, depth FROM propagated_effects "
        "ORDER BY module_name, depth, effect"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]:35s} ← {row[2]} (depth={row[3]}) : {row[1]}")

    print("\n  [Validation] call_graph (all edges):")
    rows = trusted.execute(
        "SELECT caller_module, caller_fn, callee_module, callee_fn FROM call_graph "
        "ORDER BY caller_module, caller_fn, callee_module, callee_fn"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]}.{row[1]:30s} → {row[2]}.{row[3]}")

    trusted.close()
    soft.close()
    print(f"\n  sem_trusted.db and sem_soft.db written to {MATERIALS_DIR.relative_to(HARNESS_DIR)}/")

    # Manual token count
    if not MANUAL_PATH.exists():
        print(f"\n  WARNING: {MANUAL_PATH.name} not found — skipping token count.")
        return

    print(f"\n  Computing manual token count ({MANUAL_PATH.name})...")
    manual_content = MANUAL_PATH.read_text(encoding="utf-8")
    manual_tokens = count_manual_tokens(manual_content)

    token_data = {
        "manual_file": MANUAL_PATH.name,
        "manual_tokens_per_turn": manual_tokens,
        "note": (
            "Tokens contributed by the Nicolas LLM Manual to each API call's "
            "input_tokens when used as the system prompt. Subtract "
            "manual_tokens_per_turn * turns from total_input_tokens to get "
            "task_input_tokens (task cost excluding fixed infrastructure overhead)."
        ),
    }
    MANUAL_TOKENS_PATH.write_text(json.dumps(token_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  manual_tokens_per_turn = {manual_tokens}")
    print(f"  Saved to {MANUAL_TOKENS_PATH.name}")
    print("\nDone.")


if __name__ == "__main__":
    main()
