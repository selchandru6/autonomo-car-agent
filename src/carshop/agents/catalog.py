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


# Parent-graph fields and dealer-subgraph fields share one namespace, so a name is owned once
# across both graphs. `dealer_branch` is the subgraph's boundary in the parent graph; it hands
# the Recorder's outcome up, or a stored NOT_REPORTED / PARSER_FAILED if the branch crashed.
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
        nodes=("collector",),
        tools=(
            "safety",
            "fetch.http",
            "fetch.browser",
            "fetch.artifacts",
            "fetch.politeness",
            "adapters.detect",
        ),
        writes=("fetch", "pages", "fetch_status", "fetch_reason"),
    ),
    AgentSpec(
        id="A4",
        name="Extractor",
        stage="Extract",
        mode="model-assisted",
        graph="dealer subgraph",
        nodes=("extractor", "extractor_packages"),
        tools=("adapters", "extract.llm"),
        writes=("listings", "model_usage", "resolutions"),
    ),
    AgentSpec(
        id="A5",
        name="Validator",
        stage="Validate",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("validate_listings", "validate_dealer"),
        tools=("domain.vin", "domain.classify", "domain.pricing", "domain.features"),
        writes=("eligible", "exclusions", "staging", "screening", "dealer_status"),
    ),
    AgentSpec(
        id="A6",
        name="Enricher",
        stage="Enrich",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("enricher",),
        tools=("fetch", "adapters", "domain.features"),
        writes=("enrichment", "ambiguous_text"),
    ),
    AgentSpec(
        id="A7",
        name="Recorder",
        stage="Store",
        mode="deterministic",
        graph="dealer subgraph",
        nodes=("recorder", "dealer_branch"),
        tools=("storage.repos", "storage.schema"),
        writes=("stored", "outcome", "outcomes"),
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
    AgentSpec(
        id="A10",
        name="Matcher",
        stage="Match",
        mode="deterministic",
        graph="parent",
        nodes=("matcher",),
        tools=("shortlist.match", "storage.repos"),
        writes=("matches",),
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
