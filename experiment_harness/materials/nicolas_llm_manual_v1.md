# Nicolas LLM Manual (v1)

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

The Semantic DB contains structured metadata extracted from all `.nico` modules.
Query it using `run_sql`. All tables use `module_name` as the join key.

| Table | Columns | Description |
|---|---|---|
| `modules` | `name`, `source`, `intent`, `schema_version` | One row per module |
| `imports` | `module_name`, `imported_module` | Module-level dependencies |
| `types` | `module_name`, `name`, `visibility`, `repr` | Public types |
| `functions` | `module_name`, `name`, `signature`, `visibility` | Public functions |
| `effects` | `module_name`, `function_name`, `effect`, `scope` | Side effects at two granularities: `scope='module'` (module-level union) and `scope='function'` (per-function) |
| `examples` | `module_name`, `example_id`, `path` | Executable usage examples |

## Available Tools

- `run_sql(query)` — execute a SELECT statement against the Semantic DB
- `read_file(path)` — read a `.nico` source file; provide the path relative to
  the project root, e.g. `src/time/clock.nico` or `src/user/store.nico`
