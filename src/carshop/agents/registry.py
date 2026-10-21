"""A2 Registry: load the dealer registry, refreshing it first when stale (v1.3, F1).

A stale registry is never run as-is. A registry handed in by the caller is used unchanged.
"""

from __future__ import annotations

import structlog
from langgraph.runtime import Runtime

from carshop.config.loaders import Registry, load_registry
from carshop.graph.context import RunContext
from carshop.graph.state import RunState

log = structlog.get_logger(__name__)


def _loaded(ctx: RunContext, reg: Registry) -> RunState:
    ctx.registry = reg
    return {
        "registry_version": reg.version,
        "coverage_gaps": sorted(d.dealer_id for d in reg.active() if not d.start_url),
    }


async def registry_check(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    from carshop.registry.build import registry_is_stale

    ctx = runtime.context
    if ctx.registry is not None:
        return {"registry_stale": False, **_loaded(ctx, ctx.registry)}
    if registry_is_stale(ctx.settings, ctx.requested_now):
        return {"registry_stale": True}
    return {"registry_stale": False, **_loaded(ctx, load_registry(ctx.settings.registry_path))}


def registry_route(state: RunState) -> str:
    return "registry_subgraph" if state.get("registry_stale") else "supervisor_plan"


async def registry_subgraph(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    from carshop.registry.build import refresh_registry

    ctx = runtime.context
    log.info("registry missing or older than refresh window; refreshing")
    await refresh_registry(ctx.settings, ctx.engine, only_missing=False)
    return _loaded(ctx, load_registry(ctx.settings.registry_path))
