"""A1 Supervisor: admission, plan, fan-out, cutoff, publish (requirements 7.9, 7.10, F6, F10).

The admission transaction is unchanged from the plain pipeline: it stays one atomic write in
`supervisor_plan`, after the registry is loaded. `supervisor_admit` only fails fast on a
scheduled run that already exists, so a duplicate cron never refreshes the registry.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from langgraph.runtime import Runtime
from langgraph.types import Send
from sqlalchemy import Connection, desc, select, update

from carshop.config.settings import Settings
from carshop.domain.codes import DealerStatus, RunStatus, Trigger
from carshop.domain.models import Dealer
from carshop.extract.llm import ModelClient, OpenAIModel, PackageResolver
from carshop.fetch.artifacts import ArtifactStore
from carshop.fetch.browser import BrowserRenderer
from carshop.fetch.politeness import HostGate
from carshop.graph.context import RunContext
from carshop.graph.state import DealerTask, RunState
from carshop.storage import repos
from carshop.storage import schema as s
from carshop.storage.db import read_conn, write_tx
from carshop.util import git_sha, iso

log = structlog.get_logger(__name__)


class RunRejected(Exception):
    pass


def model_client(settings: Settings) -> ModelClient | None:
    if not settings.model_enabled:
        return None
    assert settings.model_api_key is not None
    try:
        return OpenAIModel(settings.model_api_key.get_secret_value(), settings.model_name)
    except ImportError:
        log.warning("openai extra not installed; model disabled")
        return None


def prior_count(conn: Connection, dealer_id: str) -> int | None:
    r = conn.execute(
        select(s.dealer_runs.c.eligible_count)
        .join(s.runs, s.runs.c.run_id == s.dealer_runs.c.run_id)
        .where(s.dealer_runs.c.dealer_id == dealer_id, s.dealer_runs.c.status == DealerStatus.REPORTED.value)
        .order_by(desc(s.runs.c.started_at)).limit(1)
    ).first()  # fmt: skip
    return int(r[0]) if r else None


def _scheduled(ctx: RunContext) -> bool:
    return ctx.trigger in (Trigger.CRON, Trigger.CATCHUP)


async def supervisor_admit(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    if _scheduled(ctx):
        with read_conn(ctx.engine) as conn:
            if repos.scheduled_run_exists(conn, ctx.snapshot_date):
                raise RunRejected(f"a scheduled run already exists for {ctx.snapshot_date}")
    return {
        "run": {
            "run_id": ctx.run_id,
            "snapshot_date": ctx.snapshot_date,
            "trigger": ctx.trigger.value,
            "started_at": iso(ctx.now),
            "git_sha": git_sha(),
            "dealer_ids": ctx.dealer_ids,
            "notify": ctx.notify,
            "rerun_of": ctx.rerun_of,
            "label": ctx.label,
            "use_browser": ctx.use_browser,
        },
        "phase": "admit",
    }


async def prepare_run(
    ctx: RunContext, model: ModelClient | None, targets: list[Dealer], *, browser: bool
) -> None:
    """Builds the run's fetchers and the shared model breaker. Also used on resume."""
    settings = ctx.settings
    ctx.run_created = True
    ctx.resolver = PackageResolver(model, run_budget_usd=settings.model_run_budget_usd)
    ctx.http = ctx.http_factory(
        user_agent=settings.http_user_agent,
        timeout_s=settings.http_timeout_s,
        backoff_s=settings.retry_backoff_s,
    )
    ctx.gate = HostGate(settings.per_host_min_pause_s, settings.per_host_max_pause_s)
    ctx.artifacts = ArtifactStore(settings.snapshots_dir)
    ctx.sem = asyncio.Semaphore(settings.concurrency_dealers)
    ctx.targets = {d.dealer_id: d for d in targets}
    if browser:
        ctx.browser = await ctx.stack.enter_async_context(BrowserRenderer(settings))


async def supervisor_plan(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    settings = ctx.settings
    reg = ctx.registry
    assert reg is not None, "registry is loaded before planning"
    targets = [d for d in reg.active() if ctx.dealer_ids is None or d.dealer_id in ctx.dealer_ids]
    if ctx.dealer_ids is not None and len(targets) != len(set(ctx.dealer_ids)):
        raise RunRejected(
            f"unknown dealer id(s): {sorted(set(ctx.dealer_ids) - {d.dealer_id for d in targets})}"
        )

    # --- admission (7.10, F10) -------------------------------------------------------
    model = model_client(settings)
    with write_tx(ctx.engine) as conn:
        # A resume that died after this transaction committed finds its run already admitted.
        if repos.get_run(conn, ctx.run_id) is None:
            # A run can never legitimately outlive every branch timeout run back to back.
            waves = -(-max(1, len(reg.active())) // max(1, settings.concurrency_dealers))
            max_age = settings.branch_timeout_s * waves + 900
            for stale in repos.reclaim_stale_runs(conn, now=ctx.now, max_age_s=max_age):
                log.warning("reclaimed stale RUNNING run", run_id=stale)
            if _scheduled(ctx) and repos.scheduled_run_exists(conn, ctx.snapshot_date):
                raise RunRejected(f"a scheduled run already exists for {ctx.snapshot_date}")
            for d in targets:
                busy = repos.running_run_for_dealer(conn, d.dealer_id)
                if busy:
                    raise RunRejected(f"dealer {d.dealer_id} is already in RUNNING run {busy}")
            repos.upsert_dealers(conn, reg.dealers)
            repos.create_run(conn, run_id=ctx.run_id, snapshot_date=ctx.snapshot_date, trigger=ctx.trigger.value,
                             config_version=f"{reg.version}:{reg.config_hash}", model_status="ready" if model else "down",
                             rerun_of=ctx.rerun_of, label=ctx.label, started_at=iso(ctx.now))  # fmt: skip
            for d in targets:
                repos.claim_dealer_run(conn, ctx.run_id, d.dealer_id)
        prior_counts = {d.dealer_id: prior_count(conn, d.dealer_id) for d in targets}
        baselines = {d.dealer_id: repos.last_reported_run(conn, d.dealer_id) for d in targets}
    if ctx.http is None:
        await prepare_run(ctx, model, targets, browser=ctx.use_browser)

    # Unhealthy dealers first (7.9): a dealer whose last status was not REPORTED runs early.
    targets.sort(key=lambda d: (prior_counts[d.dealer_id] is not None, d.dealer_id))
    queue: list[DealerTask] = [
        {
            "dealer_id": d.dealer_id,
            "prior_eligible_count": prior_counts[d.dealer_id],
            "baseline_run_id": baselines[d.dealer_id],
        }
        for d in targets
    ]
    return {"dealer_queue": queue, "phase": "collect"}


def fan_out(state: RunState) -> list[Send] | str:
    """One `Send` per planned dealer, in attempt order. An empty plan goes straight to cutoff."""
    queue = state.get("dealer_queue", [])
    if not queue:
        return "supervisor_cutoff"
    return [Send("dealer_branch", {"task": task}) for task in queue]


async def supervisor_cutoff(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    """Every branch has returned (each enforces its own timeout). Release the fetchers."""
    await runtime.context.close_fetchers()
    return {"phase": "compare"}


async def supervisor_publish(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    assert ctx.resolver is not None
    usage = ctx.resolver.usage
    with write_tx(ctx.engine) as conn:
        repos.finish_run(conn, ctx.run_id, RunStatus.PUBLISHED, usage.spend_usd)
        conn.execute(
            update(s.runs).where(s.runs.c.run_id == ctx.run_id).values(model_status=usage.status)
        )
    wall_clock_s = int(time.monotonic() - ctx.wall_start)
    if wall_clock_s > ctx.settings.run_wall_clock_s:
        # Reported, never hidden (section 4 Freshness): the digest and screen show data age.
        log.warning(
            "run exceeded wall-clock target",
            wall_clock_s=wall_clock_s,
            target_s=ctx.settings.run_wall_clock_s,
        )
    return {
        "published": {
            "status": RunStatus.PUBLISHED.value,
            "model_status": usage.status,
            "model_spend_usd": usage.spend_usd,
            "wall_clock_s": wall_clock_s,
        },
        "phase": "publish",
    }
