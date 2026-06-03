# Nicolas

A language and toolchain for LLM-Oriented Programming.

## What Is Nicolas?

Nicolas is an early-stage language and toolchain project exploring **LLM-Oriented Programming** (LLMOP): a programming approach that treats LLM coding agents as first-class readers, editors, and composers of software.

More specifically, Nicolas treats LLM agents as the primary readers and writers of source code. Humans remain essential, but their main role is to provide intent, review semantic changes, and supervise correctness.

The design priority is:

```text
LLM reads > LLM writes > human reads > human writes
```

Most programming languages were designed for humans and compilers. Humans can rely on context, convention, documentation, and experience. Compilers need precise syntax and types. LLMs sit in between: they can generate local code well, but they often struggle to understand module intent, API contracts, side effects, hidden invariants, and cross-file composition rules.

Nicolas explores a different question:

> What would a programming language look like if codebases were designed to be read, written, modified, and explained by LLM coding agents first?

LLM-first does not mean LLM-trusted. Nicolas treats LLM agents as primary operators, but also as the main objects of constraint, audit, and skepticism. The language and toolchain should help LLMs work, while constantly checking and limiting what they do.

## LLMOP Workflow

In the intended Nicolas workflow, humans usually do not hand-write most `.nico` code. A typical loop should look more like this:

1. A human describes a goal or asks a question.
2. An LLM agent queries the project semantic index.
3. The agent reads the relevant `.nico` modules.
4. The agent explains the current code or edits the modules.
5. The Nicolas toolchain checks types, effects, capabilities, contracts, examples, and generated metadata.
6. The agent reports what changed, why it changed, and which semantic obligations were checked.
7. The human reviews the semantic diff and final behavior.

This makes human readability important, but mainly as reviewability. Nicolas should help humans quickly inspect intent, public interfaces, effects, required capabilities, contracts, examples, and behavior changes produced by an LLM.

## Core Idea

Nicolas is not an agent workflow DSL. It is closer to a traditional programming language or module language, but with stronger semantic structure at module boundaries.

The project focuses on:

- semantic module interfaces
- explicit public APIs and contracts
- structured effects and capabilities
- executable usage examples
- rules for semantic change validation
- agent-readable and agent-writable source structure
- machine-readable project metadata
- reliable code composition under limited context

The goal is to reduce the amount of raw source code an LLM must infer from before making a change, while also reducing hallucinated API usage, unauthorized side effects, and unsafe module composition.

## Rules, Examples, And Tests

Nicolas separates three concepts that are often mixed together in current AI-assisted coding workflows:

```text
Rules protect the change process.
Examples are executable usage tests that teach correct use.
Tests verify business behavior.
```

**Rules** are executable semantic policies. They validate LLM-generated changes using compiler facts, Semantic DB queries, semantic diffs, dependency graphs, effects, capabilities, and project policies. Rules are meant to catch things like unauthorized side effects, forbidden dependencies, missing examples for public APIs, unsafe data flow, and edits outside the allowed task scope.

**Examples** are not documentation snippets. They are a special kind of test: representative, runnable usage scenarios that show correct module use and typical calling patterns. Examples must compile, run, and pass. Their main job is to provide trusted usage templates for LLM agents.

**Tests** are for business correctness. They should verify domain behavior, edge cases, and regressions. Nicolas should avoid using tests as a dumping ground for agent guardrails, architecture policies, effect checks, capability checks, or semantic-diff validation. Those concerns belong in rules.

This separation is part of the LLMOP design: keep tests focused, make examples reliable as teaching material, and move AI-specific change validation into a first-class rules system.

Rules may have different check modes:

- **Deterministic rules** can be decided by the toolchain from compiler facts, Semantic DB records, semantic diffs, graphs, examples, tests, or task scope.
- **Heuristic rules** compute risk signals but should not pretend to be perfect.
- **LLM-assisted rules** may ask an LLM to analyze evidence, but the LLM's answer is treated as a judgment, not as a hard fact.

For subjective concerns such as API naming, design simplicity, abstraction quality, example representativeness, or whether a change preserves product intent, Nicolas should produce evidence packages, risk assessments, suggested fixes, or recommendations to ask the user rather than fake certainty. If a rule cannot be objectively checked, it should not pretend to be an objective gate.

## Effects And Formal Verification

In Nicolas, side effects are not comments or informal annotations. They are structured declarations that the toolchain can check mechanically.

Nicolas enforces a key constraint: an `implementation` block may only produce side effects by calling other Nicolas modules' public functions, not by calling system APIs directly. For example, only the `time.clock` module may call `std::time::SystemTime::now()`. Any other module that wants the current time must call `time.clock.now()`, which declares the `reads_clock` effect. This makes the effect dependency graph of the entire project traceable and verifiable without asking an LLM.

Given this constraint, effect annotations in module interfaces are not declarations that require trust. They are mechanical facts that the toolchain can derive: a function's effects equal the union of the effects of the Nicolas module functions it calls. The toolchain checks that declared effects match computed effects. Discrepancies are hard errors.

This is part of a broader design principle: wherever structural properties of a module can be derived from the implementation code by a program, Nicolas derives them rather than trusting LLM declarations. Side effects, public API shapes, imports, and type structures can all be computed mechanically. Only genuinely semantic properties — design intent, representative usage examples, business tests, and change-process rules — require human or LLM authorship.

## Semantic DB

Nicolas plans to generate structured semantic artifacts as part of the toolchain.

At the module level, Nicolas may emit **Semantic JSON**: compiler-generated metadata for a single module, including public APIs, types, effects, capabilities, examples, dependencies, and check results.

At the project level, Nicolas may aggregate those artifacts into a **Semantic DB**: a queryable project database that helps tools and LLM agents answer questions such as:

- Which modules provide this capability?
- Which public APIs have database or filesystem effects?
- Which examples show the intended usage of this module?
- Which modules depend on this type or contract?
- Which interfaces are stable, and which details are internal?

Semantic DB is a means to support LLMOP, not the entire purpose of Nicolas. The larger goal is to design software so LLM agents can understand and compose it more reliably.

## Status

Nicolas is in an early prototype phase.

The `rust-prototype` branch contains the first Rust skeleton: a minimal Cargo project with the `time.clock` module (`Timestamp`, `Duration`, `now()`). This skeleton validates the module structure and serves as the baseline for early evaluation experiments. Function bodies are stubs; the full implementation is pending.

The `.nico` source format, the compilation pipeline, the Semantic JSON schema, and the Semantic DB schema are all still being designed. Language design decisions are guided by controlled evaluation: comparing LLM task performance with raw Rust source versus Nicolas semantic artifacts.

Nothing in this repository should be considered stable yet.

## Design Principles

- Treat LLM agents as the primary readers and writers of source code.
- Make module boundaries explicit.
- Prefer explicit structure over clever shorthand.
- Prefer compiler-checked structure over informal comments.
- **Derive structural module properties from code; do not trust LLM declarations for facts that can be formally computed.** Imports, public API shapes, effects, and type structures should be mechanically derived from the implementation, not authored in a spec section and taken on faith.
- Separate hard-checked facts from advisory metadata.
- Treat rules as first-class semantic policies for LLM-generated changes.
- Treat LLM agents as useful but untrusted operators.
- Distinguish deterministic facts from heuristic and LLM-assisted judgments.
- Prefer deterministic and heuristic checks over LLM-assisted checks. Involve LLMs in validation only when mechanical analysis is genuinely insufficient.
- Treat examples as mandatory executable usage tests.
- Keep tests focused on business logic correctness.
- Optimize for reliable code understanding, generation, modification, and explanation.
- Make human review semantic: intent, APIs, effects, capabilities, contracts, examples, and diffs should be easy to inspect.
- Keep manual authoring possible, but do not optimize it ahead of LLM reading and writing.

## License

Nicolas is licensed under the Apache License 2.0.
