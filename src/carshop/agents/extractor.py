"""A4 Extractor: structured listings from parsed pages, and the model for ambiguous text.

`extractor` hands the Collector's parsed pages on as one listing list. `extractor_packages`
runs after the Enricher: package text the deterministic rules left UNKNOWN goes to the model
through the run's one shared `PackageResolver`, so its budget breaker covers every call
(§7.9, M7). The Validator applies what comes back.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from langgraph.runtime import Runtime

from carshop.domain.codes import Feature, SourceKind
from carshop.domain.models import FeatureEvidence, RawListing
from carshop.graph.context import RunContext
from carshop.graph.state import DealerState
from carshop.observability.tracing import span

log = structlog.get_logger(__name__)


async def extractor(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    listings: list[RawListing] = [raw for page in state["pages"] for raw in page.listings]
    return {"listings": listings}


async def extractor_packages(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    resolver = runtime.context.tools().resolver
    deadline = state["fetch"].deadline
    resolutions: dict[str, list[FeatureEvidence]] = {}
    calls = 0
    with span("extractor_packages", dealer_id=state["task"]["dealer_id"]):
        for vin, amb in state.get("ambiguous_text", {}).items():
            if not resolver.enabled:
                break
            calls += 1
            try:
                feats = await asyncio.wait_for(
                    resolver.resolve(
                        amb.text,
                        [Feature(f) for f in amb.unknown],
                        url=amb.url,
                        page_hash=amb.page_hash,
                        source_kind=SourceKind.DETAIL,
                    ),
                    timeout=max(1.0, deadline - time.monotonic()),
                )
            except TimeoutError:
                log.warning("model call passed the branch budget", vin=vin)
                break
            if feats:
                resolutions[vin] = feats
    return {"resolutions": resolutions, "model_usage": {"calls": calls}}
