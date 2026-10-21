# ruff: noqa: N802 - the requirement ids (M1..M7) keep their spelling in the test names.
"""requirements-mas.md §11 Steps 1-2: the run executes through the compiled agent graph."""

from __future__ import annotations

import asyncio
import json
import typing
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import func, select

from carshop.agents import catalog
from carshop.agents import collector as collector_mod
from carshop.agents import supervisor as supervisor_mod
from carshop.agents import validator as validator_mod
from carshop.agents.dealer_branch import build_dealer_graph
from carshop.domain.codes import DealerStatus, Platform, ReasonCode, RunStatus, Trigger
from carshop.graph.build import build_run_graph
from carshop.graph.checkpoint import open_checkpointer, thread_ids
from carshop.graph.state import DealerState, RunState
from carshop.pipeline import run as runmod
from carshop.storage import repos
from carshop.storage import schema as s
from carshop.storage.db import read_conn
from tests.gates.test_gates import (
    DC,
    DI,
    DI_PAGE,
    DI_VDP,
    NOSRC,
    ROBOTS,
    T1,
    T2,
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
    "matcher",
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
    ("supervisor_publish", "matcher"),
    ("matcher", "notifier"),
    ("notifier", "__end__"),
}
DEALER_NODES = {
    "collector",
    "extractor",
    "validate_listings",
    "enricher",
    "extractor_packages",
    "validate_dealer",
    "recorder",
}
DEALER_EDGES = {
    ("__start__", "collector"),
    ("collector", "recorder"),
    ("collector", "extractor"),
    ("extractor", "validate_listings"),
    ("validate_listings", "enricher"),
    ("validate_listings", "validate_dealer"),
    ("enricher", "extractor_packages"),
    ("extractor_packages", "validate_dealer"),
    ("validate_dealer", "recorder"),
    ("recorder", "__end__"),
}


class Killed(BaseException):
    """Stands in for the process dying: not an Exception, so nothing in the run catches it."""


def _run(env, reg, *, on_update=None, **kw):  # type: ignore[no-untyped-def]
    st, eng, _ = env
    kw.setdefault("now", T1)
    kw.setdefault("trigger", Trigger.CRON)
    return asyncio.run(
        runmod.execute_run(eng, st, registry=reg, use_browser=False, on_update=on_update, **kw)
    )


def _graph_shape(graph: Any) -> tuple[set[str], set[tuple[str, str]]]:
    g = graph.compile().get_graph()
    return set(g.nodes) - {"__start__", "__end__"}, {(e.source, e.target) for e in g.edges}


def test_M1_compiled_graphs_have_the_section_4_nodes_and_edges() -> None:
    assert _graph_shape(build_run_graph()) == (NODES, EDGES)
    assert _graph_shape(build_dealer_graph()) == (DEALER_NODES, DEALER_EDGES)


def test_M2_agent_registry_declares_every_agent_and_owns_every_state_field() -> None:
    assert [a.name for a in catalog.AGENTS] == [
        "Supervisor",
        "Registry",
        "Collector",
        "Extractor",
        "Validator",
        "Enricher",
        "Recorder",
        "Comparator",
        "Notifier",
        "Matcher",
    ]
    assert [a.id for a in catalog.AGENTS] == [f"A{i}" for i in range(1, 11)]
    assert {a.name for a in catalog.AGENTS if a.mode == "model-assisted"} == {
        "Supervisor",
        "Extractor",
    }
    owners = catalog.field_owner()  # raises on a field with two writers
    fields = set(typing.get_type_hints(RunState)) | set(typing.get_type_hints(DealerState))
    assert fields - {"task"} == set(owners)  # `task` is the Send payload, not a node output
    assert set(catalog.writable_by_node()) == NODES | DEALER_NODES


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
    original = collector_mod.collect

    async def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.2)
            return await original(*args, **kwargs)
        finally:
            in_flight -= 1

    monkeypatch.setattr(collector_mod, "collect", counted)
    others = [dealer(f"ns{i}", f"www.ns{i}-test.com", Platform.UNKNOWN, None, ()) for i in range(5)]
    rep = _run((st, eng, slow), registry(DI, *others))
    assert peak == 4
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers["di"] is DealerStatus.PARTIAL
    assert rep.reasons["di"] is ReasonCode.TIMEOUT
    assert all(rep.dealers[d.dealer_id] is DealerStatus.NO_SOURCE for d in others)


def _blocked_dc() -> dict[str, tuple[int, str | None]]:
    return {
        "https://www.dc-test.com/robots.txt": (404, None),
        "https://www.dc-test.com/new-inventory/": (403, None),
    }


def test_M5_every_node_returns_only_fields_its_agent_owns(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import carshop.agents.registry as registry_agent
    import carshop.registry.build as registry_build

    _st, _eng, fake = env
    reg = registry(DI, DC, NOSRC)

    async def no_refresh(*_a: object, **_k: object) -> None:
        return None

    # Exercise the stale-registry branch too, without touching the network.
    monkeypatch.setattr(registry_build, "registry_is_stale", lambda *_a: True)
    monkeypatch.setattr(registry_build, "refresh_registry", no_refresh)
    monkeypatch.setattr(registry_agent, "load_registry", lambda _p: reg)

    seen: list[tuple[str, Mapping[str, Any]]] = []
    fake.routes = {**di_routes(), **dc_routes()}
    rep = _run(env, None, on_update=lambda node, upd: seen.append((node, upd)))
    assert rep.status is RunStatus.PUBLISHED
    assert rep.eligible == {"di": 2, "dc": 1, "nosrc": 0}
    fake.routes = {**di_routes(), **_blocked_dc()}
    rep = _run(env, None, now=T2, on_update=lambda node, upd: seen.append((node, upd)))
    assert rep.dealers["dc"] is DealerStatus.NOT_REPORTED

    allowed = catalog.writable_by_node()
    assert {node for node, _ in seen} == NODES | DEALER_NODES
    for node, update in seen:
        assert set(update) <= allowed[node], f"{node} wrote {set(update) - allowed[node]}"
    # §5 invariant 3 on values: only the Collector settles NOT_REPORTED / NO_SOURCE, and the
    # Validator settles only REPORTED / PARTIAL.
    settled = {
        (node, verdict.status)
        for node, update in seen
        for key in ("fetch_status", "dealer_status")
        if (verdict := update.get(key)) is not None
    }
    assert ("collector", "NOT_REPORTED") in settled
    assert {st for node, st in settled if node == "validate_dealer"} <= {"REPORTED", "PARTIAL"}
    assert {node for node, _ in settled} == {"collector", "validate_dealer"}


def test_a_crash_inside_the_dealer_branch_ends_not_reported_not_running(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _st, eng, fake = env
    fake.routes = {**di_routes(), **dc_routes()}
    original = validator_mod.classify

    def boom(raw, ctx):  # type: ignore[no-untyped-def]
        if ctx.dealer_id == "dc":
            raise RuntimeError("classifier bug")
        return original(raw, ctx)

    monkeypatch.setattr(validator_mod, "classify", boom)
    rep = _run(env, registry(DI, DC))
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers == {"di": DealerStatus.REPORTED, "dc": DealerStatus.NOT_REPORTED}
    assert rep.reasons["dc"] is ReasonCode.PARSER_FAILED
    with read_conn(eng) as conn:
        rows = {r["dealer_id"]: r for r in repos.dealer_runs_for_run(conn, rep.run_id)}
        issues = conn.execute(
            select(s.review_items.c.issue).where(s.review_items.c.run_id == rep.run_id)
        ).scalars()
        assert "crash: RuntimeError" in set(issues)
    assert rows["dc"]["status"] == "NOT_REPORTED"
    assert rows["dc"]["reason_code"] == "PARSER_FAILED"
    assert rows["di"]["status"] == "REPORTED"


def _kill_dc_after_di_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    original = collector_mod.collect
    killed: list[str] = []

    async def collect(dealer_, deps, rec, pages):  # type: ignore[no-untyped-def]
        if dealer_.dealer_id == "dc" and not killed:
            await asyncio.sleep(0.5)  # di finishes and is checkpointed first
            killed.append("dc")
            raise Killed
        return await original(dealer_, deps, rec, pages)

    monkeypatch.setattr(collector_mod, "collect", collect)


def _running_run(eng) -> str:  # type: ignore[no-untyped-def]
    with read_conn(eng) as conn:
        return str(
            conn.execute(
                select(s.runs.c.run_id).where(s.runs.c.status == RunStatus.RUNNING.value)
            ).scalar_one()
        )


def test_M6_kill_mid_branch_then_resume_no_duplicate_rows_or_emails(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, eng, fake = env
    st.digest_recipients = []
    fake.routes = {**di_routes(), **dc_routes()}
    reg = registry(DI, DC)
    _kill_dc_after_di_is_stored(monkeypatch)
    with pytest.raises(Killed):
        _run(env, reg)
    run_id = _running_run(eng)
    with read_conn(eng) as conn:
        rows = {r["dealer_id"]: r["status"] for r in repos.dealer_runs_for_run(conn, run_id)}
    assert rows == {"di": "REPORTED", "dc": "RUNNING"}
    di_listing = "https://www.di-test.com/new-vehicles/x5/"
    assert fake.calls.count(di_listing) == 1

    rep = asyncio.run(runmod.resume_run(eng, st, run_id, registry=reg))
    assert rep.run_id == run_id
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers == {"di": DealerStatus.REPORTED, "dc": DealerStatus.REPORTED}
    assert fake.calls.count(di_listing) == 1  # di was not collected again
    with read_conn(eng) as conn:
        obs = repos.observations_for_run(conn, run_id, validated_only=False)
        keys = [(o["dealer_id"], o["vin"]) for o in obs]
        assert len(keys) == len(set(keys)) == 3
        lifecycle = repos.lifecycle_for_run(conn, run_id)
        assert len(lifecycle) == len({(r["dealer_id"], r["vin"]) for r in lifecycle})
        digests = conn.execute(
            select(func.count())
            .select_from(s.notification_events)
            .where(s.notification_events.c.run_id == run_id)
        ).scalar()
    assert digests == 1
    with pytest.raises(runmod.RunRejected):
        asyncio.run(runmod.resume_run(eng, st, run_id, registry=reg))


def test_M6_resume_under_another_git_sha_abandons_the_checkpoint_and_starts_fresh(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, eng, fake = env
    fake.routes = {**di_routes(), **dc_routes()}
    reg = registry(DI, DC)
    _kill_dc_after_di_is_stored(monkeypatch)
    with pytest.raises(Killed):
        _run(env, reg)
    old = _running_run(eng)

    monkeypatch.setattr(runmod, "git_sha", lambda: "a-different-sha")
    rep = asyncio.run(runmod.resume_run(eng, st, old, registry=reg))
    assert rep.run_id != old
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers == {"di": DealerStatus.REPORTED, "dc": DealerStatus.REPORTED}
    with read_conn(eng) as conn:
        old_row = repos.get_run(conn, old)
        new_row = repos.get_run(conn, rep.run_id)
        old_dealers = {r["dealer_id"]: r["status"] for r in repos.dealer_runs_for_run(conn, old)}
    assert old_row is not None and new_row is not None
    assert (old_row["status"], old_row["label"]) == ("FAILED", "abandoned_checkpoint")
    assert old_dealers["dc"] == "NOT_REPORTED"
    assert (new_row["trigger"], new_row["rerun_of_run_id"]) == ("manual_date", old)

    async def threads() -> list[str]:
        async with open_checkpointer(st) as saver:
            return await thread_ids(saver)

    assert old not in asyncio.run(threads())


def test_M6_checkpoints_older_than_the_last_published_run_are_pruned(env) -> None:  # type: ignore[no-untyped-def]
    st, _eng, fake = env
    fake.routes = {**di_routes(), **dc_routes()}
    first = _run(env, registry(DI, DC))
    second = _run(env, registry(DI, DC), now=T2)

    async def threads() -> list[str]:
        async with open_checkpointer(st) as saver:
            return await thread_ids(saver)

    assert asyncio.run(threads()) == [second.run_id]
    assert first.run_id != second.run_id
    assert st.checkpoint_db_path.name == "checkpoints.sqlite"


def test_M7_without_an_api_key_the_run_publishes_with_the_model_down(env) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    assert not st.model_enabled
    fake.routes = {**di_routes(), **dc_routes()}
    rep = _run(env, registry(DI, DC))
    assert rep.status is RunStatus.PUBLISHED
    assert rep.model_status == "down"
    with read_conn(eng) as conn:
        assert repos.get_run(conn, rep.run_id)["model_status"] == "down"  # type: ignore[index]


def test_M7_a_model_charging_past_the_budget_stops_further_calls_and_the_run_publishes(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    st, eng, fake = env
    st.concurrency_dealers = 1  # one call at a time, so the breaker is the only thing that stops
    di2 = dealer(
        "di2",
        "www.di2-test.com",
        Platform.DEALER_INSPIRE,
        "https://www.di2-test.com/new-vehicles/x5/",
        ("/new-vehicles/", "/inventory/"),
    )
    fake.routes = {
        **di_routes(),
        "https://www.di2-test.com/robots.txt": (200, ROBOTS),
        "https://www.di2-test.com/new-vehicles/x5/": (
            200,
            DI_PAGE.replace("www.di-test.com", "www.di2-test.com"),
        ),
        "https://www.di2-test.com/inventory/": (200, DI_VDP),
    }
    calls: list[list[str]] = []

    class Pricey:
        name = "fake"

        async def complete_json(self, system: str, user: str) -> tuple[str, int, int]:
            calls.append(json.loads(user)["features"])
            return json.dumps({"items": []}), 1_000_000, 100_000  # $0.15 in + $0.06 out

    monkeypatch.setattr(supervisor_mod, "model_client", lambda _s: Pricey())
    rep = _run(env, registry(DI, di2))
    assert rep.status is RunStatus.PUBLISHED
    assert len(calls) == 1  # the second dealer's ambiguous text never reaches the model
    assert rep.model_spend_usd > st.model_run_budget_usd
    assert rep.dealers == {"di": DealerStatus.REPORTED, "di2": DealerStatus.REPORTED}
    with read_conn(eng) as conn:
        assert repos.get_run(conn, rep.run_id)["model_spend_usd"] > 0.15  # type: ignore[index]
