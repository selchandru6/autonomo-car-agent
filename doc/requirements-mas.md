# AI car shopping — multi-agent architecture and live pipeline screen

**Product:** AI car shopping  
**Revision:** 2.0 (architecture addendum to [`requirements.md`](requirements.md) v1.3)  
**Status:** Implementation baseline  
**Date:** 2026-10-20  
**Supersedes:** `requirements.md` section 9, the "optional LangGraph" item in section 14 phase 4, and [ADR 0001](adr/0001-plain-pipeline.md)

This file changes **how** the system is built. It does not change **what** the system does. Every rule in `requirements.md` sections 1–8 and 10–17 still holds, including the release gates in section 13. Where this file and `requirements.md` disagree, this file wins only on the topics listed under **Supersedes**.

Words **MUST**, **MUST NOT**, and **SHOULD** are normative, as in `requirements.md`.

## 1. Goals

| ID | Goal |
| --- | --- |
| G1 | The daily run is a multi-agent system (MAS) built on LangGraph. Each responsibility in section 3 is an agent with its own node or subgraph, its own tools, and its own fields in graph state. |
| G2 | The operator can open one screen and watch the run live: which agent is working, on which dealer, and how many records each stage has collected, validated, filtered, enriched, stored, and published. |
| G3 | The screen also shows the static architecture: the layers of the project and which modules sit in each layer. |
| G4 | A finished run can be replayed on the same screen from its recorded events. |
| G5 | Nothing in G1–G4 weakens accuracy, safety, cost, or idempotency rules from `requirements.md`. |

## 2. What "agent" means in this project

An agent is a unit of the LangGraph graph with:

1. **One responsibility** from section 3.
2. **A typed input and output** on graph state. It reads any field. It writes only the fields it owns (section 5).
3. **Its own tools**, meaning the Python functions it may call (fetchers, adapters, rules, repositories). An agent does not call another agent's tools directly; it hands work over through state and graph edges.
4. **A reasoning mode**: `deterministic` or `model-assisted`.

Model use stays capped by `requirements.md` section 4 ("Agents" row) and section 7.9: **at most two agents are model-assisted**, the Supervisor (dealer ordering only) and the Extractor (ambiguous package text only). Every other agent is deterministic code. A model-assisted agent MUST produce the same terminal result when the model is off, with the ambiguous fields left `UNKNOWN`. The $0.15 per-run breaker applies across both agents together.

> Rationale: the agents are what make the system readable, observable, and resumable. Language models are not what make them agents. Putting a model in the fetcher or validator would break the evidence and no-inference rules (F3, F5, §7.7).

## 3. Agents

> **Amended by [`requirements-shortlist.md`](requirements-shortlist.md) v2.2:** a tenth agent, **A10 Matcher** (deterministic), runs between `supervisor_publish` and `notifier`. See that file §6.

Nine agents. Stage names in the third column are the words the live screen uses.

| # | Agent | Stage on screen | Mode | Graph | Responsibility | Tools (existing modules) | Writes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | Supervisor | Plan | model-assisted (ordering only) | parent graph | Admission (one scheduled run per Chicago date, F10 rejection), `run_id`, frozen config version, dealer task list and order, fan-out, cutoff, publish. | `pipeline.run` admission logic, `config.loaders`, `storage.repos` (runs) | `run`, `dealer_queue`, `phase`, `published` |
| A2 | Registry | Discover | deterministic | subgraph, runs only when the snapshot is stale (> 30 days) or on `registry refresh` | Locator snapshot, inventory-URL and platform discovery (F1, F25), overrides. | `registry.locator`, `registry.discovery`, `registry.build` | `registry_version`, `coverage_gaps` |
| A3 | Collector | Collect | deterministic | dealer subgraph node | Robots, allowlist, SSRF, then listing pages in the order of §7.4 (JSON-LD → page JSON → one browser render). Writes claim-check artifacts. Writes `NOT_REPORTED` with a reason on block, captcha, robots, timeout, off-domain. | `safety.*`, `fetch.http`, `fetch.browser`, `fetch.artifacts`, `fetch.politeness`, `adapters.detect` | `pages`, `fetch_status`, `fetch_reason` |
| A4 | Extractor | Extract | model-assisted (ambiguous package text only) | dealer subgraph node | Parse each page with the dealer's adapter into structured listings with evidence quotes. Send leftover ambiguous package text to the model under §7.9 rules. | `adapters.*`, `extract.llm.PackageResolver` | `listings`, `model_usage` |
| A5 | Validator | Validate → Filter | deterministic | dealer subgraph, two nodes: `validate_listings` and `validate_dealer` | `validate_listings`: VIN checksum, AG code, year, condition, mileage, price rules, eligibility. Excluded units leave the flow into `exclusions` with a code and quote (this is the **filter** step). `validate_dealer`: completeness gates (F19), `REPORTED` / `PARTIAL`, count anomaly, staging status (§7.9). | `domain.vin`, `domain.classify`, `domain.pricing`, `domain.features` | `eligible`, `exclusions`, `dealer_status`, `staging` |
| A6 | Enricher | Enrich | deterministic | dealer subgraph node, between the two Validator nodes | Detail pages and allowlisted window stickers for eligible units only (F12). Feature evidence for the prioritized packages (§7.7), DAP rules, conflicts stored on both sides. | `fetch.*`, `adapters.*` detail parsers, `domain.features` | `enrichment`, `feature_evidence` |
| A7 | Recorder | Store | deterministic | dealer subgraph node | One transaction per dealer: observations, features, price components, fetch attempts, staging, or an explicit non-report. Idempotent at the database (§9 invariant 4). | `storage.repos`, `storage.schema` | `stored` (counts and ids only) |
| A8 | Comparator | Compare | deterministic | parent graph node, after the cutoff | Deltas against each dealer's last `REPORTED` run (F7), lifecycle (F14), price events (F18), cross-dealer `TRANSFER_SUSPECTED` (F13). | `pipeline.compare`, `domain.lifecycle` | `deltas` |
| A9 | Notifier | Publish | deterministic | parent graph node | Digest render and idempotent send (F9, F21), backup after commit. | `pipeline.notify`, `storage.backup` | `notifications` |

Not agents:

- **Presenter.** The web screen (`web/`) reads committed rows and the event store. It is not in the graph.
- **Review.** The review queue stays outside the graph and off the digest's critical path (`requirements.md` §7.9). The graph MUST NOT use a LangGraph `interrupt` to wait for a human.

## 4. Graph topology

### 4.1 Parent graph (one run)

```
START
  → supervisor_admit
  → registry_check ──(stale)──→ registry_subgraph ─┐
        │ (fresh)                                   │
        └───────────────────────────────────────────┤
  → supervisor_plan                  (order dealers; model optional)
  → fan_out  ──Send("dealer_branch", {dealer_id}) × N──→ dealer_branch (subgraph)
  → supervisor_cutoff                (wait for all branches or the cutoff)
  → comparator
  → supervisor_publish               (commit the run PUBLISHED)
  → notifier                         (digest and backup describe a committed run only)
  → END
```

- Fan-out MUST use LangGraph `Send`, one per active registry dealer, including dealers that will end `NO_SOURCE` or `ROBOTS_DISALLOWED` (they end quickly with that status, so every dealer has a result row).
- At most **4** dealer branches run at once (`requirements.md` §7.4). Enforce it with the graph run's `max_concurrency` or an equivalent semaphore. Per-host politeness stays in `fetch.politeness`, below the graph.
- Each dealer branch has a **10-minute** timeout. On timeout the branch keeps the pages it already read and ends `PARTIAL` / `TIMEOUT`.
- One branch failing MUST NOT fail the parent graph (`requirements.md` §5 item 6). An unexpected exception inside a branch is caught at the branch boundary and becomes `NOT_REPORTED` / `PARSER_FAILED` with the exception class in the reason detail.

### 4.2 Dealer subgraph (one dealer)

```
START
  → collector
      ├─(NOT_REPORTED / NO_SOURCE)──────────────────────→ recorder → END
      └─(pages read)→ extractor
                       → validate_listings
                           ├─(no eligible units)─────────→ validate_dealer
                           └─(eligible units)→ enricher → validate_dealer
                                                            → recorder → END
```

Conditional edges are named and tested. The recorder runs on every path so that every dealer writes a `dealer_runs` row.

### 4.3 State

Parent state (illustrative; field names are the implementer's choice):

```python
class RunState(TypedDict):
    run: RunMeta                       # run_id, snapshot_date, trigger, config/parser/git versions
    dealer_queue: list[str]            # dealer ids in attempt order
    outcomes: Annotated[dict[str, DealerOutcome], merge_by_dealer_id]
    model_usage: Annotated[ModelUsage, add_usage]
    deltas: CompareSummary | None
    notifications: list[NotificationRef]
    phase: Literal["admit", "plan", "collect", "compare", "publish", "done", "failed"]
```

`DealerOutcome` holds status, reason, page counts, raw / eligible / excluded / enriched / stored counts, duration, and cost. It does not hold listings.

Dealer subgraph state holds that dealer's page pointers (`page_id`, URL, SHA-256, storage URI, HTTP status, fetch time), parsed structured listings, exclusions, and feature evidence while the branch runs. It is discarded when the branch returns its `DealerOutcome`.

## 5. Invariants

These replace the five invariants in `requirements.md` §9 and add to them.

1. **No raw HTML or page text in state, checkpoints, events, or traces.** State holds pointers and hashes (§7.5). Evidence quotes are allowed because they are short and required for audit.
2. **Reducer keyed by dealer id.** Parallel dealer branches merge into `outcomes` with a reducer keyed by `dealer_id`. Branches MUST NOT append to one shared list. A second write for the same dealer in the same run replaces the first only if it comes from the same branch execution.
3. **One writer per field.** The **Writes** column in section 3 is exclusive. In particular the Collector is the only writer of `NOT_REPORTED`, and the Validator is the only writer of classification, `REPORTED` / `PARTIAL`, and staging status. A test asserts this from the compiled graph's node outputs.
4. **The database is the source of truth.** Graph replay and checkpoint resume are not a substitute for database idempotency. Re-running a dealer branch MUST NOT duplicate observations, exclusions, or emails (§13 Replay gate).
5. **Separate files.** Business data in the existing SQLite database. LangGraph checkpoints in `data/checkpoints.sqlite`. Pipeline events (section 6) in `data/events.sqlite`. Three files, as `requirements.md` §8 Recovery requires.
6. **Checkpoint resume.** The checkpoint `thread_id` is the `run_id`. If the git SHA at resume differs from the SHA that wrote the checkpoint, abandon it and start a fresh run (`requirements.md` §8). Prune checkpoints older than the last published run.
7. **Determinism outside the two model steps.** With the model off, two runs over the same fixtures produce identical database rows apart from ids and timestamps.
8. **Events never break a run.** Failure to write or stream an event is logged and dropped. It MUST NOT change any dealer's status.

## 6. Pipeline events

The live screen is driven by events the agents emit. Events are the only new data the screen needs.

### 6.1 Emission

- Each agent node emits events through LangGraph's custom stream (`get_stream_writer()` or the equivalent in the pinned LangGraph version). A single sink consumes the run's stream and writes events to `data/events.sqlite`.
- The sink batches writes (at most one transaction every 250 ms) and uses a bounded queue. When the queue is full it drops the oldest `progress` events first, never `start`, `end`, or `error`.

### 6.2 Event record

Table `pipeline_events` in `data/events.sqlite`:

| Column | Meaning |
| --- | --- |
| `run_id` | The run. |
| `seq` | Monotonic per run, assigned by the sink. Unique `(run_id, seq)`. |
| `ts` | UTC timestamp, millisecond precision. |
| `agent` | `supervisor`, `registry`, `collector`, `extractor`, `validator`, `enricher`, `recorder`, `comparator`, `notifier`. |
| `node` | Graph node name, for example `validate_listings`. |
| `dealer_id` | NULL for parent-graph events. |
| `kind` | `start`, `progress`, `handoff`, `end`, `error`. |
| `stage` | `plan`, `discover`, `collect`, `extract`, `validate`, `filter`, `enrich`, `store`, `compare`, `publish`. |
| `counts` | JSON object of integers, for example `{"pages": 3, "listings": 41, "eligible": 9, "excluded": 32}`. |
| `status` | Dealer status or reason code when known (`REPORTED`, `PARTIAL`, `NOT_REPORTED/HTTP_403`, …). |
| `message` | One short human sentence, at most 200 characters, no URLs with query strings, no page text. |
| `ref` | Optional pointer: page hash, observation id, or artifact URI. |

Required events per dealer branch: `start` and `end` for every node it enters, one `handoff` on each edge taken (from-node, to-node, record count carried), and `progress` at least once per page fetched and once per 25 listings processed. `error` carries the reason code.

Retention: 30 days (baseline). Events are operational telemetry, not audit records. The audit trail stays in the business database.

### 6.3 Delivery to the screen

- `GET /api/pipeline/runs` — recent runs with status, start, end, dealer counts.
- `GET /api/pipeline/{run_id}/events?after_seq=N` — events after `N`, at most 500 per page.
- `GET /api/pipeline/{run_id}/stream` — Server-Sent Events. Sends events as they land, a heartbeat every 15 seconds, and supports `Last-Event-ID` resume using `seq`.
- `GET /api/pipeline/{run_id}/snapshot` — the current aggregate (per-agent state, per-dealer state, stage counts), so a page opened mid-run does not need to replay every event.
- `GET /api/architecture` — the static layer map from section 8, generated from code (module list per layer, agent list, graph edges from the compiled graph's `get_graph()`), not hand-maintained.

The web process reads `events.sqlite` read-only. It never writes to the business database because of this screen.

## 7. Live pipeline screen

Moved to [`requirements-web-ui.md`](requirements-web-ui.md) section 8.1 (revision 2.1), which places the Pipeline page inside the single app shell and its left menu. The events and APIs in section 6 of this file remain the data contract for that page.

## 8. Layers

The project has **seven layers** plus two cross-cutting concerns. The layer map is part of the contract: an import from a higher-numbered layer into a lower-numbered one is a defect, enforced by an import-linter (or equivalent) check in CI.

| Layer | Name | Contents | Depends on |
| --- | --- | --- | --- |
| L1 | Sources | External only: BMW USA dealer locator, dealer websites, allowlisted sticker hosts. | — |
| L2 | Access and safety | `safety/` (allowlist, SSRF, robots), `fetch/` (HTTP, browser, politeness, claim-check artifacts). | L1 |
| L3 | Extraction | `adapters/` (platform parsers, detection), `extract/` (model resolver). | L2 |
| L4 | Domain rules | `domain/` (VIN, classify, pricing, features, lifecycle), `reference/` (AG codes, colors). Pure functions, no I/O. | — |
| L5 | Agents and orchestration | `agents/` (new: one module per agent), `graph/` (new: graph builders, state, reducers, checkpointer, event sink), `registry/`. | L2, L3, L4, L6 |
| L6 | Storage | `storage/` (schema, migrations, repos, backup, retention), the event store. | L4 |
| L7 | Delivery | `web/` (screens, Pipeline page, APIs), `pipeline/notify` digest, `export/`, `cli.py`. | L4, L6, and L5 only through `cli.py` to start a run |

Cross-cutting: `config/` (settings, registry, preferences) and `observability/` (logging, OpenTelemetry tracing, now one span per agent node).

The existing `pipeline/run.py` and `pipeline/dealer.py` are split into the agent modules. `run_dealer` (850 lines today) becomes the Collector, Extractor, Validator, Enricher, and Recorder nodes. The plain-code entry point is removed once the graph passes the gates; there is one execution path, not two.

## 9. Functional requirements

| ID | Requirement | Acceptance check |
| --- | --- | --- |
| M1 | Build the run as the LangGraph parent graph and dealer subgraph in section 4. | `carshop run` executes through the compiled graph. A test loads the compiled graph and asserts the node set and edge set match section 4. |
| M2 | Each agent lives in its own module with a declared mode, tools, and owned fields. | A registry of agents (name, stage, mode, owned fields) exists in code. The `/api/architecture` output and the one-writer test both read it. |
| M3 | Fan out one dealer branch per active registry dealer with `Send`, at most 4 in parallel, 10-minute branch timeout. | Gate test with 6 fake dealers sees at most 4 concurrent branches. A branch that sleeps past the timeout ends `PARTIAL` / `TIMEOUT` and the others finish. |
| M4 | Merge dealer outcomes with a reducer keyed by dealer id. | Unit test of the reducer: concurrent updates for different dealers all survive; no list append on shared state. |
| M5 | Enforce one writer per field. | Test fails if any node returns a key it does not own. |
| M6 | Checkpoint to `data/checkpoints.sqlite` with `thread_id = run_id`, and abandon on git SHA mismatch. | Kill a run mid-branch, resume: no duplicate observations or emails. Change the recorded SHA, resume: a fresh run starts. |
| M7 | Keep model use to the Supervisor ordering and the Extractor's ambiguous text, under one shared breaker. | With no API key the run publishes and `model_status = down`. A fake model that charges past $0.15 stops further calls and the run still publishes (§13 drill). |
| M8 | Emit the pipeline events in section 6 to `data/events.sqlite`. | Gate test over the fixture run: every node entered has a `start` and an `end`, every edge taken has a `handoff`, and counts in the final events equal the database counts for that run. |
| M9 | Serve the APIs in section 6.3, including SSE with resume. | API tests for pagination, `after_seq`, `Last-Event-ID`, and the heartbeat. |
| M10 | Show the Pipeline screen in `requirements-web-ui.md` §8.1 with live and replay modes. | Replaying a fixture run shows each agent active in order, every dealer ending in its stored status, and funnel totals equal to the published run's totals. |
| M11 | Show the layer map in section 8, generated from code. | Adding a module under a layer's package shows up on `/api/architecture` with no hand edit. |
| M12 | Enforce the layer dependency rules. | CI fails on an import that breaks the **Depends on** column. |
| M13 | Keep every `requirements.md` §13 gate passing through the graph. | The existing `tests/gates` suite runs against the graph entry point and passes unchanged in its assertions. |

## 10. Non-functional additions

| Area | Requirement |
| --- | --- |
| Overhead | Graph orchestration and event emission add at most 5% to run wall clock on the fixture run. The 60-minute target in `requirements.md` §8 is unchanged. |
| Screen latency | An event appears on an open Pipeline screen within 2 seconds of emission. |
| Event volume | At most 200 events per dealer branch on a normal run. |
| Dependencies | Pin `langgraph` and `langgraph-checkpoint-sqlite` to exact versions in `uv.lock` that provide `Send`, the custom stream writer, and the async SQLite checkpointer. They move from the optional `graph` extra to required dependencies. |
| Observability | One OpenTelemetry trace per run, one span per agent node per dealer, with the same `run_id`, `dealer_id`, and `seq` range as the events, so a trace and the screen can be cross-checked. |
| Tests | Offline only, as today (`pytest-socket`). The graph is tested with the fake HTTP layer already used by `tests/gates`. |

## 11. Delivery steps

| Step | Deliver | Exit |
| --- | --- | --- |
| 1. Graph skeleton | `graph/` and `agents/` packages, state, reducers, checkpointer, agent registry. Existing functions wrapped as nodes with no logic change. | M1–M5 pass. All `tests/gates` pass through the graph. |
| 2. Split the dealer branch | `run_dealer` split into Collector, Extractor, Validator (two nodes), Enricher, Recorder. Old entry point removed. | M6, M7, M13 pass. Golden fixtures produce identical rows to the plain pipeline on the same input. |
| 3. Events | Event sink, `events.sqlite`, retention, APIs. | M8, M9 pass. |
| 4. Pipeline screen | `/pipeline` and `/architecture` as specified in [`requirements-web-ui.md`](requirements-web-ui.md) sections 8.1 and 8.2. | M10, M11 pass. Owner watches one live run end to end. |
| 5. Layer rules | Import-linter contract in CI. ADR 0007 records the move to LangGraph and marks ADR 0001 superseded. | M12 passes. |

## 12. Out of scope

- Language-model agents for fetching, validation, enrichment, storage, comparison, or notification.
- Agents that talk to dealers, fill forms, or chat (`requirements.md` §3).
- Starting, pausing, or editing a run from the screen.
- Distributed execution. The graph runs in the single container in `requirements.md` §15.

## 13. Change log

| Version | Date | Change |
| --- | --- | --- |
| 2.0 | 2026-10-20 | The daily run becomes a nine-agent LangGraph system with a parent graph and a per-dealer subgraph. Adds pipeline events, a live and replay Pipeline screen with animated hand-offs, and a seven-layer map enforced in CI. Model use stays capped at two agents. Supersedes `requirements.md` §9 and ADR 0001. |
