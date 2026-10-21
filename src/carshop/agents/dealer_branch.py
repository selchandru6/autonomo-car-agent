"""The dealer subgraph (requirements-mas.md §4.2) and its boundary in the parent graph.

collector → (settled) recorder, or → extractor → validate_listings → (no eligible units)
validate_dealer, or → enricher → extractor_packages → validate_dealer → recorder.

The subgraph is never checkpointed: a resumed run re-runs an unfinished dealer from the start,
and the Recorder's idempotent store keeps that safe. An exception escaping any node is caught
here and stored as NOT_REPORTED / PARSER_FAILED, so every dealer ends in a terminal status
and one dealer never fails the run (requirements §5 items 1 and 6).
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from carshop.agents.collector import collector, collector_route
from carshop.agents.enricher import enricher
from carshop.agents.extractor import extractor, extractor_packages
from carshop.agents.recorder import crash_result, non_report, outcome, recorder, store
from carshop.agents.validator import listings_route, validate_dealer, validate_listings
from carshop.graph.context import RunContext
from carshop.graph.state import DealerOutcome, DealerState, DealerTask, RunState

log = structlog.get_logger(__name__)


def build_dealer_graph() -> StateGraph[DealerState, RunContext, DealerState, DealerState]:
    g: StateGraph[DealerState, RunContext, DealerState, DealerState] = StateGraph(
        DealerState, context_schema=RunContext
    )
    g.add_node("collector", collector)
    g.add_node("extractor", extractor)
    g.add_node("validate_listings", validate_listings)
    g.add_node("enricher", enricher)
    g.add_node("extractor_packages", extractor_packages)
    g.add_node("validate_dealer", validate_dealer)
    g.add_node("recorder", recorder)
    g.add_edge(START, "collector")
    g.add_conditional_edges("collector", collector_route, ["recorder", "extractor"])
    g.add_edge("extractor", "validate_listings")
    g.add_conditional_edges("validate_listings", listings_route, ["enricher", "validate_dealer"])
    g.add_edge("enricher", "extractor_packages")
    g.add_edge("extractor_packages", "validate_dealer")
    g.add_edge("validate_dealer", "recorder")
    g.add_edge("recorder", END)
    return g


_compiled: dict[str, CompiledStateGraph[Any, Any, Any, Any]] = {}


def dealer_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    if "graph" not in _compiled:
        _compiled["graph"] = build_dealer_graph().compile(checkpointer=False)
    return _compiled["graph"]


async def dealer_branch(
    state: DealerState, config: RunnableConfig, runtime: Runtime[RunContext]
) -> RunState:
    ctx = runtime.context
    task = state["task"]
    dealer_id = task["dealer_id"]
    assert ctx.sem is not None
    async with ctx.sem:
        try:
            final = await dealer_graph().ainvoke({"task": task}, config, context=ctx)
            result: DealerOutcome = final["outcome"]
        except Exception as e:
            log.exception("dealer branch crashed", dealer=dealer_id)
            result = _crashed(ctx, task, e)
    return {"outcomes": {dealer_id: result}}


def _crashed(ctx: RunContext, task: DealerTask, error: Exception) -> DealerOutcome:
    dealer_id = task["dealer_id"]
    crash = crash_result(dealer_id, error)
    dealer = ctx.targets.get(dealer_id)
    if dealer is None:
        non_report(ctx, dealer_id, f"crash: {type(error).__name__}")
        return outcome(crash, task["baseline_run_id"], error=type(error).__name__, model_calls=0)
    stored_error = store(ctx, dealer, crash, task["baseline_run_id"])
    return outcome(crash, task["baseline_run_id"], error=stored_error, model_calls=0)
