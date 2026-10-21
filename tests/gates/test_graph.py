# ruff: noqa: N802 - the requirement ids (M1..M5) keep their spelling in the test names.
"""requirements-mas.md §11 Step 1: the run executes through the compiled agent graph (M1-M5)."""

from __future__ import annotations

import asyncio
import typing
from collections.abc import Mapping
from typing import Any

import pytest

from carshop.agents import catalog
from carshop.agents import dealer_branch as branch_mod
from carshop.domain.codes import DealerStatus, Platform, ReasonCode, RunStatus, Trigger
from carshop.graph.build import build_run_graph
from carshop.graph.state import RunState
from carshop.pipeline import run as runmod
from tests.gates.test_gates import (
    DC,
    DI,
    NOSRC,
    T1,
    FakeHttp,
    dc_routes,
    dealer,
    di_routes,
    registry,
)

pytestmark = pytest.mark.gate

NODES = {
    "supervisor_admit",
    "registry_check",
    "registry_subgraph",
    "supervisor_plan",
    "dealer_branch",
    "supervisor_cutoff",
    "comparator",
    "supervisor_publish",
    "notifier",
}
EDGES = {
    ("__start__", "supervisor_admit"),
    ("supervisor_admit", "registry_check"),
    ("registry_check", "registry_subgraph"),
    ("registry_check", "supervisor_plan"),
    ("registry_subgraph", "supervisor_plan"),
    ("supervisor_plan", "dealer_branch"),
    ("supervisor_plan", "supervisor_cutoff"),
    ("dealer_branch", "supervisor_cutoff"),
    ("supervisor_cutoff", "comparator"),
    ("comparator", "supervisor_publish"),
    ("supervisor_publish", "notifier"),
    ("notifier", "__end__"),
}


def _run(env, reg, *, on_update=None, **kw):  # type: ignore[no-untyped-def]
    st, eng, _ = env
    return asyncio.run(
        runmod.execute_run(
            eng,
            st,
            trigger=Trigger.CRON,
            registry=reg,
            use_browser=False,
            now=T1,
            on_update=on_update,
            **kw,
        )
    )


def test_M1_compiled_graph_has_the_section_4_nodes_and_edges() -> None:
    g = build_run_graph().compile().get_graph()
    assert set(g.nodes) - {"__start__", "__end__"} == NODES
    assert {(e.source, e.target) for e in g.edges} == EDGES


def test_M2_agent_registry_declares_every_agent_and_owns_every_state_field() -> None:
    names = [a.name for a in catalog.AGENTS]
    assert names == [
        "Supervisor",
        "Registry",
        "Collector",
        "Extractor",
        "Validator",
        "Enricher",
        "Recorder",
        "Comparator",
        "Notifier",
    ]
    assert [a.id for a in catalog.AGENTS] == [f"A{i}" for i in range(1, 10)]
    assert {a.name for a in catalog.AGENTS if a.mode == "model-assisted"} == {
        "Supervisor",
        "Extractor",
    }
    owners = catalog.field_owner()  # raises on a field with two writers
    assert set(typing.get_type_hints(RunState)) <= set(owners)
    assert set(catalog.writable_by_node()) == NODES


def test_M3_at_most_four_dealer_branches_run_at_once_and_a_timeout_spares_the_others(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, eng, _ = env
    st.concurrency_dealers = 4
    st.branch_timeout_s = 1

    class SlowDetail(FakeHttp):
        async def get(self, url, dealer, *, allow_sticker=False, sleep=True):  # type: ignore[no-untyped-def]
            if "/inventory/" in url:
                await asyncio.sleep(5)
            return await super().get(url, dealer, allow_sticker=allow_sticker, sleep=sleep)

    slow = SlowDetail(routes=di_routes())
    monkeypatch.setattr(runmod, "HttpFetcher", lambda **_: slow)

    in_flight = 0
    peak = 0
    original = branch_mod.run_dealer

    async def counted(d, deps):  # type: ignore[no-untyped-def]
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.2)
            return await original(d, deps)
        finally:
            in_flight -= 1

    monkeypatch.setattr(branch_mod, "run_dealer", counted)
    others = [dealer(f"ns{i}", f"www.ns{i}-test.com", Platform.UNKNOWN, None, ()) for i in range(5)]
    rep = _run((st, eng, slow), registry(DI, *others))
    assert peak == 4
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers["di"] is DealerStatus.PARTIAL
    assert rep.reasons["di"] is ReasonCode.TIMEOUT
    assert all(rep.dealers[d.dealer_id] is DealerStatus.NO_SOURCE for d in others)


def test_M5_every_node_returns_only_fields_its_agent_owns(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import carshop.agents.registry as registry_agent
    import carshop.registry.build as registry_build

    _st, _eng, fake = env
    fake.routes = {**di_routes(), **dc_routes()}
    reg = registry(DI, DC, NOSRC)

    async def no_refresh(*_a: object, **_k: object) -> None:
        return None

    # Exercise the stale-registry branch too, without touching the network.
    monkeypatch.setattr(registry_build, "registry_is_stale", lambda *_a: True)
    monkeypatch.setattr(registry_build, "refresh_registry", no_refresh)
    monkeypatch.setattr(registry_agent, "load_registry", lambda _p: reg)

    seen: list[tuple[str, Mapping[str, Any]]] = []
    rep = _run(env, None, on_update=lambda node, upd: seen.append((node, upd)))
    assert rep.status is RunStatus.PUBLISHED
    assert rep.eligible == {"di": 2, "dc": 1, "nosrc": 0}

    allowed = catalog.writable_by_node()
    assert {node for node, _ in seen} == NODES
    for node, update in seen:
        assert set(update) <= allowed[node], f"{node} wrote {set(update) - allowed[node]}"
