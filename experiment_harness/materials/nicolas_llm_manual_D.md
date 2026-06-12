# Nicolas LLM Manual (Condition D — Annotated Rust)

## What is this codebase

This is a Rust codebase where each module's semantic metadata has been
captured as structured `@nico-*` annotations in the file's doc-comment
header. A parser has extracted these annotations into a Semantic DB
(SQLite) with the same schema used by the Nicolas toolchain.

## Annotation format (in each .rs file header)

```
// @nico-module: <module.name>
// @nico-intent: <description>
// @nico-imports: <m1>, <m2>
// @nico-module-effects: <e1>, <e2>
// @nico-type: <Name> | <visibility> | <repr>
// @nico-fn: <name> | <signature> | effects=<e1,e2> | calls=<m1::f1,m2::f2>
```

The DB was generated from these annotations. The annotation data is
hand-authored based on the module's documented interface.

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

### `trusted.*` — annotation-derived structural facts (authoritative)

These tables contain facts extracted from the `@nico-*` annotations.
**Do not cross-verify `trusted.*` data against .rs files. The DB is
the authoritative source for all facts in this schema.**

| Table | Columns | Description |
|---|---|---|
| `trusted.modules` | `name`, `source`, `schema_version` | One row per module |
| `trusted.imports` | `module_name`, `imported_module` | Module-level dependencies |
| `trusted.types` | `module_name`, `name`, `visibility`, `repr` | Public types |
| `trusted.functions` | `module_name`, `name`, `signature`, `visibility` | Public functions |
| `trusted.effects` | `module_name`, `function_name`, `effect`, `scope` | Side effects at two granularities: `scope='module'` (module-level union) and `scope='function'` (per-function) |
| `trusted.examples` | `module_name`, `example_id`, `path` | Executable usage example paths (empty in this codebase) |
| `trusted.propagated_effects` | `module_name`, `effect`, `source_module`, `depth` | Effects transitively propagated from dependencies; `depth` is the number of import hops |
| `trusted.call_graph` | `caller_module`, `caller_fn`, `callee_module`, `callee_fn` | Direct cross-module function call edges from `@calls` annotations |

**Using `trusted.call_graph`:** Use this table to trace the full call
chain from a function to where a specific effect originates. Boundary
modules (like `time.clock` for `reads_clock`) appear as `callee_module`
with no further outgoing edges in this table.

### `soft.*` — human-authored semantic content

| Table | Columns | Description |
|---|---|---|
| `soft.module_intent` | `module_name`, `intent` | Natural-language description of each module's purpose |

All tables join on `module_name`.

## Trust Policy

When answering questions about module structure, dependencies, effects,
call chains, or public APIs, query `trusted.*` tables first. The
`trusted.*` schema is the authoritative source for these facts. You do
not need to open .rs files to verify what the DB reports.

Use `read_file` only when you need the full implementation body or type
definitions that are not captured in the DB.

## Available Tools

- `run_sql(query)` — execute a SELECT statement against the Semantic DB
- `read_file(path)` — read a `.rs` source file; provide the filename
  (e.g. `clock.rs`, `kv.rs`, `profile_service.rs`, `store.rs`, `types.rs`)
