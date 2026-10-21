"""A8 Comparator: deltas after the cutoff (F7, F14, F18) and cross-dealer checks (F13).

Each stored dealer is compared against its last REPORTED run in its own transaction. A dealer
that already has lifecycle or price events for this run was compared before a resume; it is
skipped, so a resumed run never writes the same event twice.
"""

from __future__ import annotations

import structlog
from langgraph.runtime import Runtime
from sqlalchemy import Connection, exists, select

from carshop.domain.codes import DealerStatus
from carshop.graph.context import RunContext
from carshop.graph.state import CompareCounts, RunState
from carshop.observability.tracing import span
from carshop.pipeline.compare import compare_snapshot, flag_cross_dealer
from carshop.storage import schema as s
from carshop.storage.db import write_tx

log = structlog.get_logger(__name__)


def _already_compared(conn: Connection, run_id: str, dealer_id: str) -> bool:
    return any(
        conn.execute(
            select(exists().where(t.c.run_id == run_id, t.c.dealer_id == dealer_id))
        ).scalar()
        for t in (s.listing_lifecycle, s.price_events)
    )


async def comparator(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    by_dealer: dict[str, CompareCounts] = {}
    outcomes = state.get("outcomes", {})
    order = [t["dealer_id"] for t in state.get("dealer_queue", [])]
    for dealer_id in order:
        outcome = outcomes.get(dealer_id)
        if outcome is None or outcome["compare_input"] is None:
            continue
        data = outcome["compare_input"]
        with span("comparator", dealer_id=dealer_id), write_tx(ctx.engine) as conn:
            if _already_compared(conn, ctx.run_id, dealer_id):
                log.info("dealer already compared; skipping", dealer=dealer_id)
                continue
            summary = compare_snapshot(
                conn,
                ctx.run_id,
                dealer_id,
                status=DealerStatus(data["status"]),
                prices=data["prices"],
                held=set(data["held"]),
                baseline_run_id=data["baseline_run_id"],
            )
        by_dealer[dealer_id] = {
            "added": summary.added,
            "missing": summary.missing,
            "relisted": summary.relisted,
            "price_changes": summary.price_changes,
        }
    with write_tx(ctx.engine) as conn:
        flagged = flag_cross_dealer(conn, ctx.run_id)
    return {"deltas": {"by_dealer": by_dealer, "transfer_suspected": flagged}}
