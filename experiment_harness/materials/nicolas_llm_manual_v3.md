# Nicolas LLM Manual (v3)

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
  - `provides.functions`: public functions, their signatures, their effects, and their cross-module calls
  - `effects`: the union of all side effects this module can produce
  - `examples`: references to executable usage examples
- `checks` — rules that validate code changes to this module
- `implementation rust` — the Rust implementation body

## Effect Propagation Rule

Nicolas module-level effects propagate through imports. If module `A` imports
module `B`, then `A` must include every module-level effect declared by `B` in
`A`'s own module-level effects. This rule also applies when the import is used
only for a type, because the dependency is still part of the module boundary.
When a change adds, removes, or changes an import, recompute the propagated
effects for every module that directly or transitively imports the changed
module.

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
| `trusted.call_graph` | `caller_module`, `caller_fn`, `callee_module`, `callee_fn` | Direct cross-module function call edges: which function in which module calls which function in which other module |

**Using `trusted.call_graph`:** This table records every cross-module call edge. You can use it to trace the full call chain from a function to where a specific effect originates. For example, to find what `user.profile_service.get_profile` ultimately calls, join `call_graph` transitively. Boundary modules (like `time.clock` for `reads_clock`, or `user.store` for `db.read`/`db.write`) will appear as `callee_module` with no further outgoing edges in this table.

### `soft.*` — LLM-authored semantic content

These tables contain natural-language descriptions written for human
understanding. Use them for explanatory context.

| Table | Columns | Description |
|---|---|---|
| `soft.module_intent` | `module_name`, `intent` | Natural-language description of each module's purpose |

All tables join on `module_name`.

## Trust Policy

When answering questions about module structure, dependencies, effects, call
chains, or public APIs, query `trusted.*` tables first. The `trusted.*` schema
is the single source of truth for these facts. You do not need to open `.nico`
files to verify what the DB reports.

Prefer `semantic_query` for common trusted lookups before writing custom SQL:
module surfaces, reverse dependencies, type dependents, callers, and effect
chains. Use `run_sql` only when you need a custom SELECT query not covered by
`semantic_query`.

Prefer `read_nico_section` when you need source text that is not captured in
the DB. Read only the needed section:

- `surface` — the full `spec { ... }` block
- `checks` — the full `checks { ... }` block
- `implementation` — the full `implementation rust { ... }` block

Use `read_file` only as a last-resort full-file fallback when a section-level
read is insufficient.

## Available Tools

- `semantic_query(query, ...)` — compact trusted lookups for module surface,
  module dependents, type dependents, function callers, and effect chains
- `read_nico_section(path, section)` — read one section of a `.nico` source
  file; `section` is `surface`, `checks`, or `implementation`
- `run_sql(query)` — execute a SELECT statement against the Semantic DB
- `read_file(path)` — last-resort full `.nico` file read; provide the path
  relative to the project root, e.g. `src/time/clock.nico` or
  `src/user/store.nico`
