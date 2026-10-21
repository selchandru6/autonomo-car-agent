"""The parent run graph (requirements-mas.md §4.1).

The run is published before the notifier runs: the digest and the backup only ever describe a
committed run, as in the plain pipeline. A digest that went out for a run that then failed to
publish would be worse than a late digest.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from carshop.agents.comparator import comparator
from carshop.agents.dealer_branch import dealer_branch
from carshop.agents.matcher import matcher
from carshop.agents.notifier import notifier
from carshop.agents.registry import registry_check, registry_route, registry_subgraph
from carshop.agents.supervisor import (
    fan_out,
    supervisor_admit,
    supervisor_cutoff,
    supervisor_plan,
    supervisor_publish,
)
from carshop.graph.context import RunContext
from carshop.graph.state import RunState


def build_run_graph() -> StateGraph[RunState, RunContext, RunState, RunState]:
    g: StateGraph[RunState, RunContext, RunState, RunState] = StateGraph(
        RunState, context_schema=RunContext
    )
    g.add_node("supervisor_admit", supervisor_admit)
    g.add_node("registry_check", registry_check)
    g.add_node("registry_subgraph", registry_subgraph)
    g.add_node("supervisor_plan", supervisor_plan)
    g.add_node("dealer_branch", dealer_branch)  # type: ignore[call-overload]
    g.add_node("supervisor_cutoff", supervisor_cutoff)
    g.add_node("comparator", comparator)
    g.add_node("supervisor_publish", supervisor_publish)
    g.add_node("matcher", matcher)
    g.add_node("notifier", notifier)

    g.add_edge(START, "supervisor_admit")
    g.add_edge("supervisor_admit", "registry_check")
    g.add_conditional_edges(
        "registry_check", registry_route, ["registry_subgraph", "supervisor_plan"]
    )
    g.add_edge("registry_subgraph", "supervisor_plan")
    g.add_conditional_edges("supervisor_plan", fan_out, ["dealer_branch", "supervisor_cutoff"])
    g.add_edge("dealer_branch", "supervisor_cutoff")
    g.add_edge("supervisor_cutoff", "comparator")
    g.add_edge("comparator", "supervisor_publish")
    g.add_edge("supervisor_publish", "matcher")
    g.add_edge("matcher", "notifier")
    g.add_edge("notifier", END)
    return g
