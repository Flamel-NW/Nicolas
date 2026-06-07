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

import json
import os
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

HARNESS_DIR = Path(__file__).parent
MATERIALS_DIR = HARNESS_DIR / "materials"
D_DIR = MATERIALS_DIR / "condition_D" / "t7"
D_TRUSTED_PATH = MATERIALS_DIR / "sem_d_trusted.db"
D_SOFT_PATH = MATERIALS_DIR / "sem_d_soft.db"
D_MANUAL_PATH = MATERIALS_DIR / "nicolas_llm_manual_D.md"
D_MANUAL_TOKENS_PATH = MATERIALS_DIR / "manual_tokens_D.json"
MODEL = "claude-sonnet-4-5"

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
"""

SOFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS module_intent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL UNIQUE,
    intent      TEXT
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
    print(f"Building Condition D Semantic DBs:")
    print(f"  trusted → {D_TRUSTED_PATH}")
    print(f"  soft    → {D_SOFT_PATH}")

    for path in (D_TRUSTED_PATH, D_SOFT_PATH):
        if path.exists():
            path.unlink()
            print(f"  Removed existing {path.name}")

    trusted = sqlite3.connect(str(D_TRUSTED_PATH))
    soft = sqlite3.connect(str(D_SOFT_PATH))
    trusted.executescript(TRUSTED_SCHEMA)
    soft.executescript(SOFT_SCHEMA)

    rs_files = sorted(D_DIR.glob("*.rs"))
    if not rs_files:
        print(f"ERROR: no .rs files found in {D_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Parsing {len(rs_files)} annotated .rs files from {D_DIR.relative_to(HARNESS_DIR)}")
    for rs_path in rs_files:
        data = parse_annotations(rs_path)
        if not data["module"]:
            print(f"  WARNING: no @nico-module annotation found in {rs_path.name}, skipping.")
            continue
        insert_module(trusted, soft, data)
        print(f"    {rs_path.name}  →  module={data['module']}")

    print("\n  Computing propagated_effects...")
    compute_propagated_effects(trusted)

    trusted.commit()
    soft.commit()

    # Row counts
    print("\n  sem_d_trusted.db row counts:")
    for table in ["modules", "imports", "types", "functions", "effects", "examples",
                  "propagated_effects", "call_graph"]:
        count = trusted.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table:25s}: {count} rows")

    print("\n  sem_d_soft.db row counts:")
    count = soft.execute("SELECT COUNT(*) FROM module_intent").fetchone()[0]
    print(f"    {'module_intent':25s}: {count} rows")

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
    print(f"\n  sem_d_trusted.db and sem_d_soft.db written to {MATERIALS_DIR.relative_to(HARNESS_DIR)}/")

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
