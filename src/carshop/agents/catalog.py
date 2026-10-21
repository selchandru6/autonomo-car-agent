"""The agent registry (requirements-mas.md §3, M2).

One entry per agent: who it is, which graph nodes it runs, which existing modules it uses
as tools, and which state fields it alone may write. The one-writer test and the
architecture screen both read this table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["deterministic", "model-assisted"]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    id: str
    name: str
    stage: str
    mode: Mode
    graph: str
    nodes: tuple[str, ...]
    tools: tuple[str, ...]
    writes: tuple[str, ...]


# Until the dealer branch is split into its own subgraph, all five per-dealer agents share the
# single `dealer_branch` node. Each still owns only its own fields.
AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="A1",
        name="Supervisor",
        stage="Plan",
        mode="model-assisted",
        graph="parent",
        nodes=("supervisor_admit", "supervisor_plan", "supervisor_cutoff", "supervisor_publish"),
        tools=("pipeline.run", "config.loaders", "storage.repos"),
        writes=("run", "dealer_queue", "phase", "published"),
    ),
    AgentSpec(
        id="A2",
        name="Registry",
        stage="Discover",
        mode="deterministic",
        graph="registry subgraph",
        nodes=("registry_check", "registry_subgraph"),
        tools=("registry.locator", "registry.discovery", "registry.build"),
        writes=("registry_stale", "registry_version", "coverage_gaps"),
    ),
    AgentSpec(
        id="A3",
        name="Collector",
        stage="Collect",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("dealer_branch",),
        tools=(
            "safety",
            "fetch.http",
            "fetch.browser",
            "fetch.artifacts",
            "fetch.politeness",
            "adapters.detect",
        ),
        writes=("pages", "fetch_status", "fetch_reason"),
    ),
    AgentSpec(
        id="A4",
        name="Extractor",
        stage="Extract",
        mode="model-assisted",
        graph="dealer subgraph",
        nodes=("dealer_branch",),
        tools=("adapters", "extract.llm"),
        writes=("listings", "model_usage"),
    ),
    AgentSpec(
        id="A5",
        name="Validator",
        stage="Validate",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("dealer_branch",),
        tools=("domain.vin", "domain.classify", "domain.pricing", "domain.features"),
        writes=("eligible", "exclusions", "dealer_status", "staging"),
    ),
    AgentSpec(
        id="A6",
        name="Enricher",
        stage="Enrich",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("dealer_branch",),
        tools=("fetch", "adapters", "domain.features"),
        writes=("enrichment", "feature_evidence"),
    ),
    AgentSpec(
        id="A7",
        name="Recorder",
        stage="Store",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("dealer_branch",),
        tools=("storage.repos", "storage.schema"),
        writes=("stored", "outcomes"),
    ),
    AgentSpec(
        id="A8",
        name="Comparator",
        stage="Compare",
        mode="deterministic",
        graph="parent",
        nodes=("comparator",),
        tools=("pipeline.compare", "domain.lifecycle"),
        writes=("deltas",),
    ),
    AgentSpec(
        id="A9",
        name="Notifier",
        stage="Publish",
        mode="deterministic",
        graph="parent",
        nodes=("notifier",),
        tools=("pipeline.notify", "storage.backup"),
        writes=("notifications",),
    ),
)


def field_owner() -> dict[str, str]:
    """State field -> the one agent allowed to write it."""
    owners: dict[str, str] = {}
    for agent in AGENTS:
        for name in agent.writes:
            if name in owners:
                raise ValueError(f"{name} has two writers: {owners[name]} and {agent.name}")
            owners[name] = agent.name
    return owners


def writable_by_node() -> dict[str, frozenset[str]]:
    """Graph node -> the state fields it may return."""
    allowed: dict[str, set[str]] = {}
    for agent in AGENTS:
        for node in agent.nodes:
            allowed.setdefault(node, set()).update(agent.writes)
    return {node: frozenset(keys) for node, keys in allowed.items()}
