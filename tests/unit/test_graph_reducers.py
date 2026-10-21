"""requirements-mas.md M4: dealer outcomes merge by dealer id; nothing appends to shared state."""

from __future__ import annotations

import asyncio
import operator
import typing
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from carshop.graph.reducers import add_usage, merge_by_dealer_id
from carshop.graph.state import RunState


def test_merge_by_dealer_id_keeps_every_dealer_and_replaces_only_its_own_key() -> None:
    merged = merge_by_dealer_id({"a": 1}, {"b": 2})
    merged = merge_by_dealer_id(merged, {"c": 3})
    assert merged == {"a": 1, "b": 2, "c": 3}
    assert merge_by_dealer_id(merged, {"b": 9}) == {"a": 1, "b": 9, "c": 3}
    assert merge_by_dealer_id(None, None) == {}


def test_add_usage_sums_counters() -> None:
    assert add_usage({"spend_usd": 0.1}, {"spend_usd": 0.2, "calls": 1}) == {
        "spend_usd": 0.3,
        "calls": 1,
    }


def test_concurrent_branch_updates_for_different_dealers_all_survive() -> None:
    class S(TypedDict, total=False):
        ids: list[str]
        outcomes: Annotated[dict[str, int], merge_by_dealer_id]
        usage: Annotated[dict[str, float], add_usage]

    async def branch(task: dict[str, str]) -> S:
        await asyncio.sleep(0.01)
        return {"outcomes": {task["id"]: len(task["id"])}, "usage": {"spend_usd": 0.5}}

    g = StateGraph(S)
    g.add_node("plan", lambda s: {"ids": [f"d{i}" for i in range(20)]})
    g.add_node("branch", branch)  # type: ignore[arg-type]
    g.add_edge(START, "plan")
    g.add_conditional_edges(
        "plan", lambda s: [Send("branch", {"id": i}) for i in s["ids"]], ["branch"]
    )
    g.add_edge("branch", END)
    out = asyncio.run(g.compile().ainvoke({}, {"max_concurrency": 4}))
    assert set(out["outcomes"]) == {f"d{i}" for i in range(20)}
    assert out["usage"] == {"spend_usd": 10.0}


def test_run_state_never_appends_to_a_shared_list() -> None:
    hints = typing.get_type_hints(RunState, include_extras=True)
    reducers = {
        name: meta for name, hint in hints.items() for meta in getattr(hint, "__metadata__", ())
    }
    assert reducers == {"outcomes": merge_by_dealer_id, "model_usage": add_usage}
    assert operator.add not in reducers.values()
