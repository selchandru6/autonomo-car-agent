"""A10 Matcher: after publish, evaluate saved searches against committed rows.

No model. Its own transaction: a failure leaves the run PUBLISHED and is recorded so the
digest can say matches could not be computed (requirements-shortlist.md §6).
"""

from __future__ import annotations

import structlog
from langgraph.runtime import Runtime

from carshop.config.loaders import load_locator_coords
from carshop.graph.context import RunContext
from carshop.graph.state import MatchSummary, RunState
from carshop.shortlist.searches import Geo, evaluate_run, mark_matcher_failed
from carshop.storage.db import write_tx

log = structlog.get_logger(__name__)


async def matcher(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    try:
        with write_tx(ctx.engine) as conn:
            geo = Geo(conn, load_locator_coords(ctx.settings.registry_dir))
            counts = evaluate_run(conn, ctx.run_id, geo)
        summary: MatchSummary = {
            "searches": int(counts["searches"]),
            "match": int(counts["match"]),
            "possible": int(counts["possible"]),
            "new": int(counts["new"]),
            "error": counts.get("error"),
        }
        return {"matches": summary}
    except Exception as e:
        log.exception("matcher failed")
        try:
            with write_tx(ctx.engine) as conn:
                mark_matcher_failed(conn, ctx.run_id, str(e))
        except Exception:
            log.exception("could not record matcher failure")
        failed: MatchSummary = {
            "searches": 0,
            "match": 0,
            "possible": 0,
            "new": 0,
            "error": str(e)[:300],
        }
        return {"matches": failed}
