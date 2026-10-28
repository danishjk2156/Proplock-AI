---
name: karpathy-guidelines
description: Behavioral guardrails against common LLM coding failure modes (silent assumptions, overengineering, messy edits). Apply when planning, implementing, editing, or refactoring code.
---

# Karpathy Guidelines

> **Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding
*Don't assume. Don't hide confusion. Surface tradeoffs.*

- **Before implementing:** State your assumptions explicitly. If uncertain, ask.
- **Multiple interpretations:** If a requirement can be interpreted in multiple ways, present the options — do not silently pick one.
- **Simpler alternatives:** If a simpler approach exists than what was suggested, point it out. Push back with clear technical reasoning when warranted.
- **When confused:** Stop immediately. Name what is unclear or conflicting and ask for clarification rather than writing guesswork code.

## 2. Simplicity First
*Minimum code that solves the problem. Nothing speculative.*

- **No unrequested features:** Build strictly what was asked, nothing more.
- **No speculative abstractions:** Do not create interfaces, base classes, or plugin mechanisms for single-use code.
- **No unrequested configurability:** Avoid adding premature config flags, environment options, or knobs that were not requested.
- **No impossible error handling:** Do not write speculative error recovery for scenarios that cannot logically occur.
- **Rule of thumb:** If you write 200 lines and it could be done cleanly in 50, rewrite it.

## 3. Surgical Changes
*Touch only what you must. Clean up only your own mess.*

- **Scoped edits:** Keep changes minimal, tight, and focused strictly on the objective.
- **No collateral damage:** Do not reformat, reorganize, or "clean up" adjacent code, comments, or imports unless directly related to the task.
- **Match existing style:** Follow the existing repository's patterns, naming conventions, and idioms.
- **Zero residue:** Leave no commented-out code, temporary debug print statements, or scratch files behind.

## 4. Goal-Driven Execution
*Define success criteria. Loop until verified.*

- **Clear verification criteria:** Translate requirements into concrete, measurable verification steps before writing code.
- **Self-contained test loops:** Run tests, type-checks, linters, or inspect runtime output iteratively until success criteria are proven.
- **Never claim done without evidence:** Verify that the code actually compiles, runs, and passes tests before reporting completion.
