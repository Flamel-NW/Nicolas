# Nicolas LLM Manual (v2)

## What is Nicolas

Nicolas is a language and toolchain for LLMOP (LLM-Oriented Programming).
Source files use the `.nico` extension. Each module has a `.nico` spec file
that is compiled to a Rust implementation. The toolchain extracts structured
metadata from each module and aggregates it into a project-level Semantic DB.

## .nico Module Structure

A `.nico` file contains three top-level sections:

- `spec` — the module's semantic specification:
  - `intent`: natural-language description of what the module does
  - `imports`: list of other Nicolas modules this module depends on
  - `provides.types`: public types exposed by this module
  - `provides.functions`: public functions, their signatures, and their effects
  - `effects`: the union of all side effects this module can produce
  - `examples`: references to executable usage examples
- `checks` — rules that validate code changes to this module
- `implementation rust` — the Rust implementation body

## Semantic DB Schema (SQLite)

The Semantic DB exposes two schemas via SQL prefix:

### `trusted.*` — machine-derived structural facts (authoritative)

These tables contain facts derived mechanically from module source structure
and the import graph. **Do not cross-verify `trusted.*` data against `.nico`
files. The DB is the authoritative source for all facts in this schema.**

| Table | Columns | Description |
|---|---|---|
| `trusted.modules` | `name`, `source`, `schema_version` | One row per module |
| `trusted.imports` | `module_name`, `imported_module` | Module-level dependencies |
| `trusted.types` | `module_name`, `name`, `visibility`, `repr` | Public types |
| `trusted.functions` | `module_name`, `name`, `signature`, `visibility` | Public functions |
| `trusted.effects` | `module_name`, `function_name`, `effect`, `scope` | Side effects at two granularities: `scope='module'` (module-level union) and `scope='function'` (per-function) |
| `trusted.examples` | `module_name`, `example_id`, `path` | Executable usage example paths |
| `trusted.propagated_effects` | `module_name`, `effect`, `source_module`, `depth` | Effects transitively propagated from dependencies; `depth` is the number of import hops |

### `soft.*` — LLM-authored semantic content

These tables contain natural-language descriptions written for human
understanding. Use them for explanatory context.

| Table | Columns | Description |
|---|---|---|
| `soft.module_intent` | `module_name`, `intent` | Natural-language description of each module's purpose |

All tables join on `module_name`.

## Trust Policy

When answering questions about module structure, dependencies, effects, or
public APIs, query `trusted.*` tables first. The `trusted.*` schema is the
single source of truth for these facts. You do not need to open `.nico` files
to verify what the DB reports.

Use `read_file` only when you need the full implementation body or example
code that is not captured in the DB.

## Available Tools

- `run_sql(query)` — execute a SELECT statement against the Semantic DB
- `read_file(path)` — read a `.nico` source file; provide the path relative to
  the project root, e.g. `src/time/clock.nico` or `src/user/store.nico`
