# Nicolas

A language and toolchain for LLM-Oriented Programming.

## What Is Nicolas?

Nicolas is an early-stage language and toolchain project exploring **LLM-Oriented Programming** (LLMOP): a programming approach that treats LLM coding agents as first-class readers, editors, and composers of software.

Most programming languages were designed for humans and compilers. Humans can rely on context, convention, documentation, and experience. Compilers need precise syntax and types. LLMs sit in between: they can generate local code well, but they often struggle to understand module intent, API contracts, side effects, hidden invariants, and cross-file composition rules.

Nicolas explores a different question:

> What would a programming language look like if codebases were designed to be reliably understood and modified by LLM coding agents?

## Core Idea

Nicolas is not an agent workflow DSL. It is closer to a traditional programming language or module language, but with stronger semantic structure at module boundaries.

The project focuses on:

- semantic module interfaces
- explicit public APIs and contracts
- structured effects and capabilities
- checkable examples
- machine-readable project metadata
- reliable code composition under limited context

The goal is to reduce the amount of raw source code an LLM must read before making a change, while also reducing hallucinated API usage and unsafe module composition.

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

Nicolas is currently in the project-definition and research phase.

The first prototype is expected to be small and experimental. It may start with a restricted compilation target and a minimal `.nico` module format, then evolve as the language design becomes clearer.

Nothing in this repository should be considered stable yet.

## Design Principles

- Make module boundaries explicit.
- Prefer compiler-checked structure over informal comments.
- Separate hard-checked facts from advisory metadata.
- Treat examples as executable knowledge where possible.
- Optimize for reliable code understanding and composition, not just code generation.
- Keep the language useful to humans while making it easier for LLMs to navigate.

## License

Nicolas is licensed under the Apache License 2.0.
