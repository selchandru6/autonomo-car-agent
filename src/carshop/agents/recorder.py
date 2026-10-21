"""A7 Recorder: one transaction per dealer (observations, features, prices, fetch attempts,
staging, robots), or an explicit non-report when that transaction fails. Idempotent at the
database, so a resumed branch never duplicates rows (§5 invariant 4).
"""

from __future__ import annotations

import structlog
from langgraph.runtime import Runtime

from carshop.domain.codes import DealerStatus, ReasonCode
from carshop.domain.models import Dealer, DealerResult
from carshop.graph.context import RunContext
from carshop.graph.state import DealerOutcome, DealerState, Enrichment
from carshop.observability.tracing import span
from carshop.storage import repos
from carshop.storage.db import write_tx

log = structlog.get_logger(__name__)


async def recorder(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    ctx = runtime.context
    task = state["task"]
    dealer = ctx.targets[task["dealer_id"]]
    result = dealer_result(state)
    error = store(ctx, dealer, result, task["baseline_run_id"])
    calls = state.get("model_usage", {}).get("calls", 0)
    return {
        "stored": error is None,
        "outcome": outcome(result, task["baseline_run_id"], error=error, model_calls=calls),
    }


def dealer_result(state: DealerState) -> DealerResult:
    verdict = state.get("fetch_status") or state["dealer_status"]
    rec = state["fetch"]
    enr = state.get("enrichment") or Enrichment()
    observations = state.get("eligible", []) if state.get("dealer_status") else []
    return DealerResult(
        dealer_id=state["task"]["dealer_id"], status=DealerStatus(verdict.status),
        reason=ReasonCode(verdict.reason) if verdict.reason else None, pages_expected=rec.pages_expected,
        pages_read=rec.pages_read, pages_failed=rec.pages_failed, raw_count=len(state.get("listings", [])),
        eligible_count=len(observations), duration_ms=verdict.duration_ms, cost_usd=0.0, anomaly=verdict.anomaly,
        observations=tuple(observations), exclusions=tuple(state.get("exclusions", [])),
        attempts=tuple(rec.attempts + enr.attempts), review_reasons=tuple(state.get("staging", [])),
        robots_hash=rec.robots_hash, adapter_version=rec.adapter.version if rec.adapter else None,
    )  # fmt: skip


def crash_result(dealer_id: str, error: BaseException) -> DealerResult:
    return DealerResult(dealer_id=dealer_id, status=DealerStatus.NOT_REPORTED, reason=ReasonCode.PARSER_FAILED,
                        pages_expected=0, pages_read=0, pages_failed=0, raw_count=0, eligible_count=0, duration_ms=0,
                        cost_usd=0.0, anomaly=False, observations=(), exclusions=(), attempts=(),
                        review_reasons=((("", f"crash: {type(error).__name__}"),)))  # fmt: skip


def store(
    ctx: RunContext, dealer: Dealer, result: DealerResult, baseline: str | None
) -> str | None:
    """Commits the dealer. Returns None, or the error class after recording a non-report."""
    try:
        with span("recorder", dealer_id=dealer.dealer_id), write_tx(ctx.engine) as conn:
            repos.store_dealer_result(conn, ctx.run_id, result, baseline_run_id=baseline)
            if result.robots_hash is not None or result.status is not DealerStatus.NO_SOURCE:
                repos.record_robots(
                    conn,
                    ctx.run_id,
                    dealer.dealer_id,
                    dealer.hostname,
                    result.robots_hash,
                    "disallowed" if result.reason is ReasonCode.ROBOTS_DISALLOWED else "allowed",
                )
        return None
    except Exception as e:
        log.exception("dealer store failed", dealer=dealer.dealer_id)
        non_report(ctx, dealer.dealer_id, f"store failed: {type(e).__name__}")
        return type(e).__name__


def non_report(ctx: RunContext, dealer_id: str, detail: str) -> None:
    """The explicit non-report: the dealer's row ends NOT_REPORTED / PARSER_FAILED, audited."""
    try:
        with write_tx(ctx.engine) as conn:
            repos.mark_dealer_failed(conn, ctx.run_id, dealer_id, detail)
    except Exception:
        log.exception("could not record dealer store failure", dealer=dealer_id)


def outcome(
    result: DealerResult, baseline: str | None, *, error: str | None, model_calls: int
) -> DealerOutcome:
    held = {vin for vin, _issue in result.review_reasons if vin}
    return {
        "status": result.status.value,
        "reason": result.reason.value if result.reason else None,
        "pages_read": result.pages_read,
        "raw_count": result.raw_count,
        "eligible_count": result.eligible_count,
        "duration_ms": result.duration_ms,
        "model_calls": model_calls,
        "stored": error is None,
        "store_error": error,
        "compare_input": None
        if error is not None
        else {
            "status": result.status.value,
            "prices": {
                o.vin: o.comparison_price_cents for o in result.observations if o.vin not in held
            },
            "held": sorted(held),
            "baseline_run_id": baseline,
        },
    }
