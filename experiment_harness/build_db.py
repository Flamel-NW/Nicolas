"""
Nicolas Semantic DB Builder
===========================
Loads Semantic JSON files into a SQLite database with 6 tables.
Also computes the per-turn token overhead of the Nicolas LLM Manual
and writes it to materials/manual_tokens.json for use by run_experiment.py.

Usage:
    python build_db.py
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
DB_PATH = MATERIALS_DIR / "semantic.db"
JSON_DIR = MATERIALS_DIR / "condition_C" / "t7"
MANUAL_PATH = MATERIALS_DIR / "nicolas_llm_manual_v1.md"
MANUAL_TOKENS_PATH = MATERIALS_DIR / "manual_tokens.json"
MODEL = "claude-sonnet-4-5"

# ---------------------------------------------------------------------------
# Load .env (same strategy as run_experiment.py)
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
SCHEMA = """
CREATE TABLE IF NOT EXISTS modules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    source         TEXT,
    intent         TEXT,
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
"""

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def insert_module(conn: sqlite3.Connection, data: dict) -> None:
    module_name = data["module"]

    conn.execute(
        "INSERT OR REPLACE INTO modules (name, source, intent, schema_version) VALUES (?, ?, ?, ?)",
        (module_name, data.get("source"), data.get("intent"), data.get("schema_version")),
    )

    for imp in data.get("imports", []):
        conn.execute(
            "INSERT INTO imports (module_name, imported_module) VALUES (?, ?)",
            (module_name, imp),
        )

    provides = data.get("provides", {})

    for t in provides.get("types", []):
        conn.execute(
            "INSERT INTO types (module_name, name, visibility, repr) VALUES (?, ?, ?, ?)",
            (module_name, t["name"], t.get("visibility"), t.get("repr")),
        )

    for fn in provides.get("functions", []):
        conn.execute(
            "INSERT INTO functions (module_name, name, signature, visibility) VALUES (?, ?, ?, ?)",
            (module_name, fn["name"], fn.get("signature"), fn.get("visibility")),
        )
        for eff in fn.get("effects", []):
            conn.execute(
                "INSERT INTO effects (module_name, function_name, effect, scope) VALUES (?, ?, ?, 'function')",
                (module_name, fn["name"], eff),
            )

    for eff in data.get("effects", []):
        conn.execute(
            "INSERT INTO effects (module_name, function_name, effect, scope) VALUES (?, NULL, ?, 'module')",
            (module_name, eff),
        )

    for ex in data.get("examples", []):
        conn.execute(
            "INSERT INTO examples (module_name, example_id, path) VALUES (?, ?, ?)",
            (module_name, ex["id"], ex.get("path")),
        )


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_manual_tokens(manual_content: str) -> int:
    """
    Count the tokens contributed by the manual when used as a system prompt.
    Returns the difference between (system=manual + minimal user msg) and
    (no system + same user msg), which isolates the manual's token count.
    Falls back to a character-based estimate if the API call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  WARNING: ANTHROPIC_API_KEY not set — using character estimate for token count.")
        return len(manual_content) // 3

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
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
        # Older SDK without count_tokens; fall back to beta endpoint
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
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
    print(f"Building Semantic DB: {DB_PATH}")

    if DB_PATH.exists():
        DB_PATH.unlink()
        print("  Removed existing semantic.db")

    conn = sqlite3.connect(str(DB_PATH))
    create_schema(conn)

    json_files = sorted(JSON_DIR.glob("*.json"))
    if not json_files:
        print(f"ERROR: no JSON files found in {JSON_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"  Loading {len(json_files)} JSON files from {JSON_DIR.relative_to(HARNESS_DIR)}")
    for json_path in json_files:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        insert_module(conn, data)
        print(f"    {json_path.name}  →  module={data['module']}")

    conn.commit()

    # Row counts
    print("\n  Table row counts:")
    for table in ["modules", "imports", "types", "functions", "effects", "examples"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table:12s}: {count} rows")

    # Validation queries
    print("\n  [Validation] Functions with reads_clock effect:")
    rows = conn.execute(
        "SELECT module_name, function_name FROM effects "
        "WHERE effect='reads_clock' AND scope='function' ORDER BY module_name"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]}.{row[1]}")

    print("\n  [Validation] All function-level effects (for get_profile call chain):")
    rows = conn.execute(
        "SELECT module_name, function_name, effect FROM effects "
        "WHERE scope='function' ORDER BY module_name, function_name, effect"
    ).fetchall()
    for row in rows:
        print(f"    {row[0]}.{row[1]:30s} → {row[2]}")

    conn.close()
    print(f"\n  semantic.db written to {DB_PATH.relative_to(HARNESS_DIR)}")

    # Manual token count
    if not MANUAL_PATH.exists():
        print("\n  WARNING: nicolas_llm_manual_v1.md not found — skipping token count.")
        return

    print(f"\n  Computing manual token count ({MANUAL_PATH.name})...")
    manual_content = MANUAL_PATH.read_text(encoding="utf-8")
    manual_tokens = count_manual_tokens(manual_content)

    token_data = {
        "manual_file": "nicolas_llm_manual_v1.md",
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
