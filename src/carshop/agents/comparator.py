"""A8 Comparator: cross-dealer checks after the cutoff (F13).

Per-dealer deltas are computed inside each dealer's store transaction; this node adds the
run-wide `TRANSFER_SUSPECTED` flags.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from carshop.graph.context import RunContext
from carshop.graph.state import RunState
from carshop.pipeline.compare import flag_cross_dealer
from carshop.storage.db import write_tx


async def comparator(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    with write_tx(ctx.engine) as conn:
        flagged = flag_cross_dealer(conn, ctx.run_id)
    return {"deltas": {"transfer_suspected": flagged}}
