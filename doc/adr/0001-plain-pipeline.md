# ADR 0001 — A plain async pipeline, not an agent framework

Status: accepted · Date: 2026-10-19

## Context

The requirements describe a fixed daily job: for each approved dealer, fetch, parse, classify,
compare, store, then notify. Every step is deterministic except one optional LLM call that
resolves ambiguous package text. The failure semantics are strict (one transaction per dealer,
PARTIAL never emits deltas, a crashed run must be visible), and the system must be auditable
by a single operator.

LangGraph was considered (it is listed as an optional extra) because "agent" is in the project
name and a graph with per-dealer branches maps naturally onto it.

## Decision

Implement the run as plain `asyncio` code in `carshop.pipeline`:

- `run.execute_run` owns admission, the dealer semaphore, the publish step, digest, backup.
- `dealer.run_dealer` is the per-dealer branch: robots → listing pages → classify → detail
  enrichment → completeness gates → `DealerResult`.
- `compare.compare_dealer` turns a REPORTED result into lifecycle and price events.

State lives in SQLite rows, not in a framework checkpoint. The LLM step is a leaf function
(`extract.llm.PackageResolver`) with its own budget breaker and a null implementation.

## Consequences

- Control flow is readable top to bottom and testable with a fake HTTP layer (see
  `tests/gates`). The §13 gates run in about a second.
- No framework upgrade can change retry or ordering semantics underneath the invariants.
- If a future phase needs interactive, multi-step reasoning (for example negotiating with a
  dealer chat), that is a different system; it can import the same domain and storage modules.
- `langgraph` stays an unused optional extra until a concrete need appears; remove it from
  `pyproject.toml` at the next dependency review if none does.
