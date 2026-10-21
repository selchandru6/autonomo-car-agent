"""A3-A7 for one dealer: Collector, Extractor, Validator, Enricher, Recorder.

For now one node wraps the existing `run_dealer` and its store transaction unchanged. A
crash becomes NOT_REPORTED / PARSER_FAILED and a store failure is recorded on that dealer,
so one dealer never fails the run (requirements §5 item 6).
"""

from __future__ import annotations

from dataclasses import asdict

import structlog
from langgraph.runtime import Runtime

from carshop.domain.codes import DealerStatus, ReasonCode
from carshop.domain.models import DealerResult
from carshop.graph.context import RunContext
from carshop.graph.state import CompareCounts, DealerOutcome, DealerTask, RunState
from carshop.observability.tracing import span
from carshop.pipeline.compare import compare_dealer
from carshop.pipeline.dealer import BranchDeps, run_dealer
from carshop.storage import repos
from carshop.storage.db import write_tx

log = structlog.get_logger(__name__)


def _outcome(result: DealerResult, *, stored: bool, compare: CompareCounts | None) -> DealerOutcome:
    return {
        "status": result.status.value,
        "reason": result.reason.value if result.reason else None,
        "pages_read": result.pages_read,
        "raw_count": result.raw_count,
        "eligible_count": result.eligible_count,
        "duration_ms": result.duration_ms,
        "cost_usd": result.cost_usd,
        "stored": stored,
        "compare": compare,
    }


async def dealer_branch(task: DealerTask, runtime: Runtime[RunContext]) -> RunState:
    try:
        return await _branch(task, runtime.context)
    except Exception as e:
        log.error("dealer branch escaped", dealer=task["dealer_id"], error=type(e).__name__)
        return {}


async def _branch(task: DealerTask, ctx: RunContext) -> RunState:
    dealer = ctx.targets[task["dealer_id"]]
    assert ctx.http and ctx.artifacts and ctx.gate and ctx.resolver and ctx.sem
    deps = BranchDeps(settings=ctx.settings, http=ctx.http, browser=ctx.browser, artifacts=ctx.artifacts, gate=ctx.gate,
                      resolver=ctx.resolver, run_id=ctx.run_id, prior_eligible_count=task["prior_eligible_count"])  # fmt: skip
    async with ctx.sem:
        try:
            result = await run_dealer(dealer, deps)
        except Exception as e:
            log.exception("dealer branch crashed", dealer=dealer.dealer_id)
            result = DealerResult(dealer_id=dealer.dealer_id, status=DealerStatus.NOT_REPORTED, reason=ReasonCode.PARSER_FAILED,
                                  pages_expected=0, pages_read=0, pages_failed=0, raw_count=0, eligible_count=0, duration_ms=0,
                                  cost_usd=0.0, anomaly=False, observations=(), exclusions=(), attempts=(), review_reasons=((("", f"crash: {type(e).__name__}"),)))  # fmt: skip
    usage = {"spend_usd": result.cost_usd}
    # Storage: one transaction per dealer. A failure here must not cancel the other dealers.
    try:
        with span("store", dealer_id=dealer.dealer_id), write_tx(ctx.engine) as conn:
            baseline = task["baseline_run_id"]
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
            summary = compare_dealer(conn, ctx.run_id, result, baseline)
        counts: CompareCounts = asdict(summary)  # type: ignore[assignment]
        return {
            "outcomes": {dealer.dealer_id: _outcome(result, stored=True, compare=counts)},
            "model_usage": usage,
        }
    except Exception as e:
        log.exception("dealer store failed", dealer=dealer.dealer_id)
        try:
            with write_tx(ctx.engine) as conn:
                repos.mark_dealer_failed(
                    conn, ctx.run_id, dealer.dealer_id, f"store failed: {type(e).__name__}"
                )
        except Exception:
            log.exception("could not record dealer store failure", dealer=dealer.dealer_id)
        failed: DealerOutcome = {
            **_outcome(result, stored=False, compare=None),
            "status": DealerStatus.NOT_REPORTED.value,
            "reason": ReasonCode.PARSER_FAILED.value,
            "eligible_count": 0,
        }
        return {"outcomes": {dealer.dealer_id: failed}, "model_usage": usage}
