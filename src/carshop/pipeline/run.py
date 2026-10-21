"""Run entry points: one run through the compiled agent graph (requirements-mas.md §4).

Admission, fan-out with bounded concurrency, the dealer subgraph, compare, publish, notify,
and backup are graph nodes in `carshop.agents`. `execute_run` starts a run; `resume_run`
continues an interrupted one from its checkpoint, or abandons it and starts a fresh run when
the code changed since the checkpoint was written (§5 invariant 6).
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

from carshop.agents.supervisor import RunRejected, model_client, prepare_run
from carshop.config.loaders import Registry, load_registry
from carshop.config.settings import Settings
from carshop.domain.codes import DealerStatus, ReasonCode, RunStatus, Trigger
from carshop.fetch.http import HttpFetcher
from carshop.graph.build import build_run_graph
from carshop.graph.checkpoint import open_checkpointer, prune_checkpoints, thread_config
from carshop.graph.context import RunContext
from carshop.graph.state import Deltas, RunMeta, RunState
from carshop.observability.tracing import span
from carshop.pipeline.compare import CompareSummary
from carshop.pipeline.notify import deliver_digest
from carshop.storage import repos
from carshop.storage.db import read_conn, write_tx
from carshop.util import civil_date, git_sha, new_run_id, parse_iso, utcnow

__all__ = [
    "RunRejected",
    "RunReport",
    "execute_run",
    "resume_run",
    "run_sync",
    "status_summary",
]

log = structlog.get_logger(__name__)

NodeObserver = Callable[[str, Mapping[str, Any]], None]
_MODES: list[StreamMode] = ["updates", "values"]


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


def _context(
    engine: Engine,
    settings: Settings,
    *,
    trigger: Trigger,
    run_id: str,
    snapshot_date: str,
    now: datetime,
    requested_now: datetime | None,
    dealer_ids: list[str] | None,
    notify: bool,
    rerun_of: str | None,
    label: str | None,
    use_browser: bool,
    registry: Registry | None,
) -> RunContext:
    return RunContext(
        engine=engine,
        settings=settings,
        trigger=trigger,
        run_id=run_id,
        snapshot_date=snapshot_date,
        now=now,
        requested_now=requested_now,
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
    ctx = _context(
        engine,
        settings,
        trigger=trigger,
        run_id=new_run_id(started, trigger.value),
        snapshot_date=snapshot_date or civil_date(started, settings.schedule_tz).isoformat(),
        now=started,
        requested_now=now,
        dealer_ids=dealer_ids,
        notify=notify,
        rerun_of=rerun_of,
        label=label,
        use_browser=use_browser,
        registry=registry,
    )
    start: RunState = {}
    return await _drive(ctx, start, on_update)


async def resume_run(
    engine: Engine,
    settings: Settings,
    run_id: str,
    *,
    registry: Registry | None = None,
    on_update: NodeObserver | None = None,
) -> RunReport:
    """Continues an interrupted RUNNING run from its checkpoint.

    Dealers that finished keep their stored result; unfinished dealers run again from the
    start (the store is idempotent). A checkpoint written by another git SHA is abandoned:
    the old run is marked FAILED and a fresh run re-does it.
    """
    settings.ensure_dirs()
    async with open_checkpointer(settings) as saver:
        app = build_run_graph().compile(checkpointer=saver)
        snap = await app.aget_state(thread_config(run_id, max_concurrency=1))
        values = cast(RunState, snap.values or {})
        meta = values.get("run")
        if meta is None:
            raise RunRejected(f"no checkpoint for run {run_id}")
        with read_conn(engine) as conn:
            row = repos.get_run(conn, run_id)
        if row is not None and row["status"] != RunStatus.RUNNING.value:
            raise RunRejected(f"run {run_id} is {row['status']}, not RUNNING")
        current = git_sha()
        abandon = meta["git_sha"] != current
        if abandon:
            log.warning(
                "checkpoint from another git SHA; starting a fresh run",
                run_id=run_id,
                checkpoint_sha=meta["git_sha"],
                current_sha=current,
            )
            await saver.adelete_thread(run_id)
    if abandon:
        return await _restart(engine, settings, meta, row is not None, registry, on_update)

    reg = registry or load_registry(settings.registry_path)
    ctx = _context(
        engine,
        settings,
        trigger=Trigger(meta["trigger"]),
        run_id=run_id,
        snapshot_date=meta["snapshot_date"],
        now=parse_iso(meta["started_at"]),
        requested_now=None,
        dealer_ids=meta["dealer_ids"],
        notify=meta["notify"],
        rerun_of=meta["rerun_of"],
        label=meta["label"],
        use_browser=meta["use_browser"],
        registry=reg,
    )
    queue = {t["dealer_id"] for t in values.get("dealer_queue", [])}
    if queue:
        targets = [d for d in reg.dealers if d.dealer_id in queue]
        browser = ctx.use_browser and "dealer_branch" in snap.next
        await prepare_run(ctx, model_client(settings), targets, browser=browser)
    log.info("resuming run", run_id=run_id, next=list(snap.next))
    return await _drive(ctx, None, on_update)


async def _restart(
    engine: Engine,
    settings: Settings,
    meta: RunMeta,
    admitted: bool,
    registry: Registry | None,
    on_update: NodeObserver | None,
) -> RunReport:
    if admitted:
        with write_tx(engine) as conn:
            repos.fail_interrupted_run(
                conn,
                meta["run_id"],
                now=utcnow(),
                label="abandoned_checkpoint",
                reason="checkpoint written by another git SHA; a fresh run re-does it",
            )
    trigger = Trigger(meta["trigger"])
    if trigger in (Trigger.CRON, Trigger.CATCHUP):
        trigger = Trigger.MANUAL_DATE  # the abandoned run already holds the date's scheduled slot
    return await execute_run(
        engine,
        settings,
        trigger=trigger,
        dealer_ids=meta["dealer_ids"],
        snapshot_date=meta["snapshot_date"],
        notify=meta["notify"],
        rerun_of=meta["run_id"],
        registry=registry,
        use_browser=meta["use_browser"],
        label=meta["label"],
        on_update=on_update,
    )


async def _drive(
    ctx: RunContext, start: RunState | None, on_update: NodeObserver | None
) -> RunReport:
    settings = ctx.settings
    config = thread_config(ctx.run_id, max_concurrency=settings.concurrency_dealers)
    final: RunState = {}
    with span("run", run_id=ctx.run_id, trigger=ctx.trigger.value):
        try:
            async with open_checkpointer(settings) as saver:
                app = build_run_graph().compile(checkpointer=saver)
                async for part in app.astream(
                    start, config, context=ctx, stream_mode=_MODES, subgraphs=True
                ):
                    namespace, mode, chunk = cast(tuple[tuple[str, ...], StreamMode, Any], part)
                    if mode == "values":
                        if not namespace:
                            final = chunk
                    elif on_update is not None:
                        for node, update in chunk.items():
                            if node != "__metadata__":
                                on_update(node, update or {})
                try:
                    await prune_checkpoints(saver, ctx.engine)
                except Exception:
                    log.exception("checkpoint pruning failed")
        except RunRejected:
            raise
        except Exception:
            if not ctx.run_created:
                raise
            log.exception("run failed", run_id=ctx.run_id)
            try:
                with write_tx(ctx.engine) as conn:
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
    empty: Deltas = {"by_dealer": {}, "transfer_suspected": []}
    deltas = state.get("deltas", empty)
    report = RunReport(
        run_id=ctx.run_id,
        snapshot_date=ctx.snapshot_date,
        status=RunStatus(published["status"]),
        model_status=published["model_status"],
        model_spend_usd=published["model_spend_usd"],
        wall_clock_s=published["wall_clock_s"],
        transfer_suspected=list(deltas["transfer_suspected"]),
    )
    for dealer_id, outcome in state.get("outcomes", {}).items():
        if outcome["stored"]:
            report.dealers[dealer_id] = DealerStatus(outcome["status"])
            report.reasons[dealer_id] = ReasonCode(outcome["reason"]) if outcome["reason"] else None
            report.eligible[dealer_id] = outcome["eligible_count"]
        else:  # the Recorder's explicit non-report
            report.dealers[dealer_id] = DealerStatus.NOT_REPORTED
            report.reasons[dealer_id] = ReasonCode.PARSER_FAILED
            report.eligible[dealer_id] = 0
    for dealer_id, counts in deltas["by_dealer"].items():
        report.compare[dealer_id] = CompareSummary(**counts)
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
