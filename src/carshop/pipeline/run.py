"""Run entry point: executes one run through the compiled agent graph (requirements-mas.md §4).

Admission, fan-out with bounded concurrency, per-dealer storage, compare, publish, notify, and
backup are graph nodes in `carshop.agents`. This module builds the per-run context, invokes
the graph, marks the run FAILED if anything escapes after admission, and returns the report.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

import structlog
from langgraph.types import StreamMode
from sqlalchemy import Engine

from carshop.agents.supervisor import RunRejected
from carshop.config.loaders import Registry
from carshop.config.settings import Settings
from carshop.domain.codes import DealerStatus, ReasonCode, RunStatus, Trigger
from carshop.fetch.http import HttpFetcher
from carshop.graph.build import build_run_graph
from carshop.graph.checkpoint import open_checkpointer, thread_config
from carshop.graph.context import RunContext
from carshop.graph.state import RunState
from carshop.observability.tracing import span
from carshop.pipeline.compare import CompareSummary
from carshop.pipeline.notify import deliver_digest
from carshop.storage import repos
from carshop.storage.db import read_conn, write_tx
from carshop.util import civil_date, new_run_id, utcnow

__all__ = ["RunRejected", "RunReport", "execute_run", "run_sync", "status_summary"]

log = structlog.get_logger(__name__)

NodeObserver = Callable[[str, Mapping[str, Any]], None]


@dataclass(slots=True)
class RunReport:
    run_id: str
    snapshot_date: str
    status: RunStatus
    dealers: dict[str, DealerStatus] = field(default_factory=dict)
    reasons: dict[str, ReasonCode | None] = field(default_factory=dict)
    eligible: dict[str, int] = field(default_factory=dict)
    compare: dict[str, CompareSummary] = field(default_factory=dict)
    transfer_suspected: list[str] = field(default_factory=list)
    notifications: dict[str, str] = field(default_factory=dict)
    model_status: str = "down"
    model_spend_usd: float = 0.0
    backup_path: str | None = None
    wall_clock_s: int = 0


async def execute_run(
    engine: Engine,
    settings: Settings,
    *,
    trigger: Trigger,
    dealer_ids: list[str] | None = None,
    snapshot_date: str | None = None,
    notify: bool = True,
    rerun_of: str | None = None,
    registry: Registry | None = None,
    use_browser: bool = True,
    now: datetime | None = None,
    label: str | None = None,
    on_update: NodeObserver | None = None,
) -> RunReport:
    # Replay reprocesses stored pages. It never sends the digest (ADR 0004).
    if trigger is Trigger.REPLAY:
        notify = False
    settings.ensure_dirs()
    started = now or utcnow()
    civil = snapshot_date or civil_date(started, settings.schedule_tz).isoformat()
    ctx = RunContext(
        engine=engine,
        settings=settings,
        trigger=trigger,
        run_id=new_run_id(started, trigger.value),
        snapshot_date=civil,
        now=started,
        requested_now=now,
        dealer_ids=dealer_ids,
        notify=notify,
        rerun_of=rerun_of,
        label=label,
        use_browser=use_browser,
        wall_start=time.monotonic(),
        http_factory=HttpFetcher,
        deliver_digest=deliver_digest,
        registry=registry,
    )
    graph = build_run_graph()
    config = thread_config(ctx.run_id, max_concurrency=settings.concurrency_dealers)
    start: RunState = {}
    final: RunState = {}
    modes: list[StreamMode] = ["updates", "values"]
    with span("run", run_id=ctx.run_id, trigger=trigger.value):
        try:
            async with open_checkpointer(settings) as saver:
                app = graph.compile(checkpointer=saver)
                async for part in app.astream(start, config, context=ctx, stream_mode=modes):
                    mode, chunk = cast(tuple[StreamMode, Any], part)
                    if mode == "values":
                        final = chunk
                    elif on_update is not None:
                        for node, update in chunk.items():
                            on_update(node, update or {})
        except RunRejected:
            raise
        except Exception:
            if not ctx.run_created:
                raise
            log.exception("run failed", run_id=ctx.run_id)
            try:
                with write_tx(engine) as conn:
                    repos.finish_run(conn, ctx.run_id, RunStatus.FAILED, ctx.spend_usd)
            except Exception:
                log.exception("could not mark run failed", run_id=ctx.run_id)
            raise
        finally:
            await ctx.close_fetchers()
    report = _report(ctx, final)
    log.info(
        "run published",
        run_id=report.run_id,
        dealers={k: v.value for k, v in report.dealers.items()},
        eligible=report.eligible,
    )
    return report


def _report(ctx: RunContext, state: RunState) -> RunReport:
    published = state["published"]
    report = RunReport(
        run_id=ctx.run_id,
        snapshot_date=ctx.snapshot_date,
        status=RunStatus(published["status"]),
        model_status=published["model_status"],
        model_spend_usd=published["model_spend_usd"],
        wall_clock_s=published["wall_clock_s"],
        transfer_suspected=list(state.get("deltas", {}).get("transfer_suspected", [])),
    )
    for dealer_id, outcome in state.get("outcomes", {}).items():
        report.dealers[dealer_id] = DealerStatus(outcome["status"])
        report.reasons[dealer_id] = ReasonCode(outcome["reason"]) if outcome["reason"] else None
        report.eligible[dealer_id] = outcome["eligible_count"]
        if outcome["compare"] is not None:
            report.compare[dealer_id] = CompareSummary(**outcome["compare"])
    notes = state.get("notifications")
    if notes is not None:
        report.notifications = dict(notes["sent"])
        report.backup_path = notes["backup_path"]
    return report


def run_sync(engine: Engine, settings: Settings, **kwargs: object) -> RunReport:
    return asyncio.run(execute_run(engine, settings, **kwargs))  # type: ignore[arg-type]


def status_summary(engine: Engine) -> dict[str, object]:
    with read_conn(engine) as conn:
        latest = repos.latest_published_run(conn)
        return {"latest_run": latest, "coverage": repos.coverage(conn)}


Mode = Literal["live", "replay"]
