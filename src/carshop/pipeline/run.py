"""Supervisor: deterministic scheduler (requirements 9, 7.9, 7.10, F6, F10).

Creates the run, freezes config, attempts every fetchable dealer with bounded concurrency,
stores each dealer in its own transaction as it finishes, compares, publishes, notifies,
backs up. One dealer's failure never blocks the others.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import structlog
from sqlalchemy import Engine, select

from carshop.config.loaders import Registry, load_registry
from carshop.config.settings import Settings
from carshop.domain.codes import DealerStatus, ReasonCode, RunStatus, Trigger
from carshop.domain.models import Dealer, DealerResult
from carshop.extract.llm import ModelClient, OpenAIModel, PackageResolver
from carshop.fetch.artifacts import ArtifactStore
from carshop.fetch.browser import BrowserRenderer
from carshop.fetch.http import HttpFetcher
from carshop.fetch.politeness import HostGate
from carshop.observability.tracing import span
from carshop.pipeline.compare import CompareSummary, compare_dealer, flag_cross_dealer
from carshop.pipeline.dealer import BranchDeps, run_dealer
from carshop.pipeline.notify import deliver_digest
from carshop.storage import repos
from carshop.storage import schema as s
from carshop.storage.backup import backup, verify_restore
from carshop.storage.db import read_conn, write_tx
from carshop.storage.retention import prune_backups, prune_snapshots
from carshop.util import civil_date, iso, new_run_id, utcnow

log = structlog.get_logger(__name__)


class RunRejected(Exception):
    pass


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


def _model_client(settings: Settings) -> ModelClient | None:
    if not settings.model_enabled:
        return None
    assert settings.model_api_key is not None
    try:
        return OpenAIModel(settings.model_api_key.get_secret_value(), settings.model_name)
    except ImportError:
        log.warning("openai extra not installed; model disabled")
        return None


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
) -> RunReport:
    # Replay reprocesses stored pages. It never sends the digest (ADR 0004).
    if trigger is Trigger.REPLAY:
        notify = False
    settings.ensure_dirs()
    if registry is None:
        # v1.3 (F1): the registry is the monthly locator snapshot plus discovery. Build or
        # refresh it before the first dealer is touched; a stale registry is never run as-is.
        from carshop.registry.build import refresh_registry, registry_is_stale

        if registry_is_stale(settings, now):
            log.info("registry missing or older than refresh window; refreshing")
            await refresh_registry(settings, engine, only_missing=False)
        reg = load_registry(settings.registry_path)
    else:
        reg = registry
    now = now or utcnow()
    wall_start = time.monotonic()
    civil = snapshot_date or civil_date(now, settings.schedule_tz).isoformat()
    run_id = new_run_id(now, trigger.value)
    targets: list[Dealer] = [
        d for d in reg.active() if dealer_ids is None or d.dealer_id in dealer_ids
    ]
    if dealer_ids is not None and len(targets) != len(set(dealer_ids)):
        raise RunRejected(
            f"unknown dealer id(s): {sorted(set(dealer_ids) - {d.dealer_id for d in targets})}"
        )

    # --- admission (7.10, F10) -------------------------------------------------------
    with write_tx(engine) as conn:
        # A run can never legitimately outlive every branch timeout run back to back.
        waves = -(-max(1, len(reg.active())) // max(1, settings.concurrency_dealers))
        max_age = settings.branch_timeout_s * waves + 900
        for stale in repos.reclaim_stale_runs(conn, now=now, max_age_s=max_age):
            log.warning("reclaimed stale RUNNING run", run_id=stale)
        if trigger in (Trigger.CRON, Trigger.CATCHUP) and repos.scheduled_run_exists(conn, civil):
            raise RunRejected(f"a scheduled run already exists for {civil}")
        for d in targets:
            busy = repos.running_run_for_dealer(conn, d.dealer_id)
            if busy:
                raise RunRejected(f"dealer {d.dealer_id} is already in RUNNING run {busy}")
        repos.upsert_dealers(conn, reg.dealers)
        model = _model_client(settings)
        repos.create_run(conn, run_id=run_id, snapshot_date=civil, trigger=trigger.value, config_version=f"{reg.version}:{reg.config_hash}",
                         model_status="ready" if model else "down", rerun_of=rerun_of, label=label, started_at=iso(now))  # fmt: skip
        for d in targets:
            repos.claim_dealer_run(conn, run_id, d.dealer_id)
        prior_counts = {d.dealer_id: _prior_count(conn, d.dealer_id) for d in targets}
        baselines = {d.dealer_id: repos.last_reported_run(conn, d.dealer_id) for d in targets}

    report = RunReport(run_id=run_id, snapshot_date=civil, status=RunStatus.RUNNING)
    resolver = PackageResolver(model, run_budget_usd=settings.model_run_budget_usd)
    http = HttpFetcher(
        user_agent=settings.http_user_agent,
        timeout_s=settings.http_timeout_s,
        backoff_s=settings.retry_backoff_s,
    )
    gate = HostGate(settings.per_host_min_pause_s, settings.per_host_max_pause_s)
    artifacts = ArtifactStore(settings.snapshots_dir)
    sem = asyncio.Semaphore(settings.concurrency_dealers)

    # Unhealthy dealers first (7.9): a dealer whose last status was not REPORTED runs early.
    targets.sort(key=lambda d: (prior_counts[d.dealer_id] is not None, d.dealer_id))

    async def branch(dealer: Dealer, browser: BrowserRenderer | None) -> None:
        deps = BranchDeps(settings=settings, http=http, browser=browser, artifacts=artifacts, gate=gate, resolver=resolver,
                          run_id=run_id, prior_eligible_count=prior_counts[dealer.dealer_id])  # fmt: skip
        async with sem:
            try:
                result = await run_dealer(dealer, deps)
            except Exception as e:
                log.exception("dealer branch crashed", dealer=dealer.dealer_id)
                result = DealerResult(dealer_id=dealer.dealer_id, status=DealerStatus.NOT_REPORTED, reason=ReasonCode.PARSER_FAILED,
                                      pages_expected=0, pages_read=0, pages_failed=0, raw_count=0, eligible_count=0, duration_ms=0,
                                      cost_usd=0.0, anomaly=False, observations=(), exclusions=(), attempts=(), review_reasons=((("", f"crash: {type(e).__name__}"),)))  # fmt: skip
        # Storage: one transaction per dealer. A failure here must not cancel the other dealers.
        try:
            with span("store", dealer_id=dealer.dealer_id), write_tx(engine) as conn:
                baseline = baselines[dealer.dealer_id]
                repos.store_dealer_result(conn, run_id, result, baseline_run_id=baseline)
                if result.robots_hash is not None or result.status is not DealerStatus.NO_SOURCE:
                    repos.record_robots(
                        conn,
                        run_id,
                        dealer.dealer_id,
                        dealer.hostname,
                        result.robots_hash,
                        "disallowed"
                        if result.reason is ReasonCode.ROBOTS_DISALLOWED
                        else "allowed",
                    )
                report.compare[dealer.dealer_id] = compare_dealer(conn, run_id, result, baseline)
            report.dealers[dealer.dealer_id] = result.status
            report.reasons[dealer.dealer_id] = result.reason
            report.eligible[dealer.dealer_id] = result.eligible_count
        except Exception as e:
            log.exception("dealer store failed", dealer=dealer.dealer_id)
            report.dealers[dealer.dealer_id] = DealerStatus.NOT_REPORTED
            report.reasons[dealer.dealer_id] = ReasonCode.PARSER_FAILED
            report.eligible[dealer.dealer_id] = 0
            try:
                with write_tx(engine) as conn:
                    repos.mark_dealer_failed(
                        conn, run_id, dealer.dealer_id, f"store failed: {type(e).__name__}"
                    )
            except Exception:
                log.exception("could not record dealer store failure", dealer=dealer.dealer_id)

    with span("run", run_id=run_id, trigger=trigger.value):
        try:
            try:
                if use_browser:
                    async with BrowserRenderer(settings) as browser:
                        outcomes = await asyncio.gather(
                            *(branch(d, browser) for d in targets), return_exceptions=True
                        )
                else:
                    outcomes = await asyncio.gather(
                        *(branch(d, None) for d in targets), return_exceptions=True
                    )
                for outcome in outcomes:
                    if isinstance(outcome, Exception):
                        log.error("dealer branch escaped", error=type(outcome).__name__)
            finally:
                await http.aclose()

            with write_tx(engine) as conn:
                report.transfer_suspected = flag_cross_dealer(conn, run_id)
                report.model_status = resolver.usage.status
                report.model_spend_usd = resolver.usage.spend_usd
                repos.finish_run(conn, run_id, RunStatus.PUBLISHED, resolver.usage.spend_usd)
                from sqlalchemy import update

                conn.execute(
                    update(s.runs)
                    .where(s.runs.c.run_id == run_id)
                    .values(model_status=resolver.usage.status)
                )
            report.status = RunStatus.PUBLISHED
            report.wall_clock_s = int(time.monotonic() - wall_start)
            if report.wall_clock_s > settings.run_wall_clock_s:
                # Reported, never hidden (section 4 Freshness): the digest and screen show data age.
                log.warning(
                    "run exceeded wall-clock target",
                    wall_clock_s=report.wall_clock_s,
                    target_s=settings.run_wall_clock_s,
                )

            if notify:
                report.notifications = deliver_digest(engine, settings, run_id)
            try:
                path = backup(settings.db_path, settings.backups_dir)
                verify_restore(path)
                report.backup_path = str(path)
                prune_backups(settings.backups_dir)
                prune_snapshots(settings.snapshots_dir, settings.snapshot_retention_days)
            except Exception:
                log.exception("backup failed")
        except Exception:
            log.exception("run failed", run_id=run_id)
            try:
                with write_tx(engine) as conn:
                    repos.finish_run(conn, run_id, RunStatus.FAILED, resolver.usage.spend_usd)
            except Exception:
                log.exception("could not mark run failed", run_id=run_id)
            report.status = RunStatus.FAILED
            raise
    log.info(
        "run published",
        run_id=run_id,
        dealers={k: v.value for k, v in report.dealers.items()},
        eligible=report.eligible,
    )
    return report


def _prior_count(conn: object, dealer_id: str) -> int | None:
    from sqlalchemy import Connection, desc

    assert isinstance(conn, Connection)
    r = conn.execute(
        select(s.dealer_runs.c.eligible_count)
        .join(s.runs, s.runs.c.run_id == s.dealer_runs.c.run_id)
        .where(s.dealer_runs.c.dealer_id == dealer_id, s.dealer_runs.c.status == DealerStatus.REPORTED.value)
        .order_by(desc(s.runs.c.started_at)).limit(1)
    ).first()  # fmt: skip
    return int(r[0]) if r else None


def run_sync(engine: Engine, settings: Settings, **kwargs: object) -> RunReport:
    return asyncio.run(execute_run(engine, settings, **kwargs))  # type: ignore[arg-type]


def status_summary(engine: Engine) -> dict[str, object]:
    with read_conn(engine) as conn:
        latest = repos.latest_published_run(conn)
        return {"latest_run": latest, "coverage": repos.coverage(conn)}


Mode = Literal["live", "replay"]
