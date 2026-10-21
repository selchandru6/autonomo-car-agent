"""A5 Validator: `validate_listings` (classification and the filter) and `validate_dealer`
(detail-evidence hard rules, completeness gates F19, REPORTED / PARTIAL, silent-zero anomaly).

The only writer of classification, REPORTED / PARTIAL, and staging status (§5 invariant 3).
"""

from __future__ import annotations

import structlog
from langgraph.runtime import Runtime

from carshop.adapters.base import Adapter
from carshop.agents.collector import elapsed_ms
from carshop.config.settings import Settings
from carshop.domain.classify import ClassifyContext, classify
from carshop.domain.codes import DealerStatus, ExclusionCode, ReasonCode
from carshop.domain.models import Dealer, Exclusion, Observation
from carshop.graph.context import RunContext
from carshop.graph.state import DealerState, DealerVerdict, Enrichment, Screening
from carshop.observability.tracing import span
from carshop.util import utcnow

log = structlog.get_logger(__name__)


def classify_context(dealer: Dealer, adapter: Adapter, settings: Settings) -> ClassifyContext:
    return ClassifyContext(
        dealer_id=dealer.dealer_id, fetched_at=utcnow(), adapter_version=adapter.version,
        model_year=settings.model_year, target_ag=settings.target_ag_code,
        odometer_max=settings.new_odometer_max_miles, band_min_cents=settings.price_band_min_cents,
        band_max_cents=settings.price_band_max_cents,
    )  # fmt: skip


async def validate_listings(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    ctx = runtime.context
    dealer = ctx.targets[state["task"]["dealer_id"]]
    adapter = state["fetch"].adapter
    assert adapter is not None
    cctx = classify_context(dealer, adapter, ctx.settings)
    screening = Screening()
    exclusions: list[Exclusion] = []
    review: list[tuple[str, str]] = []
    seen: dict[str, Observation] = {}
    excluded_vins: set[str] = set()
    with span("validate_listings", dealer_id=dealer.dealer_id):
        for raw in state["listings"]:
            cl = classify(raw, cctx)
            if cl.parse_failed:
                screening.parse_failures += 1
                continue
            if cl.exclusion:
                if cl.exclusion.vin and cl.exclusion.vin in excluded_vins:
                    continue  # the same card repeated on another page: one exclusion row
                if cl.exclusion.vin:
                    excluded_vins.add(cl.exclusion.vin)
                exclusions.append(cl.exclusion)
                if cl.exclusion.code is ExclusionCode.VIN_CHECKSUM_FAIL:
                    review.append((cl.exclusion.vin or "", cl.exclusion.code.value))
                continue
            assert cl.observation is not None
            obs = cl.observation
            if obs.vin in seen:  # F13: one observation per (run, dealer, vin); keep alternate URLs
                screening.alternates.setdefault(obs.vin, set()).add(
                    obs.detail_url or obs.source_url
                )
                continue
            seen[obs.vin] = obs
            if cl.needs_review:
                review.append((obs.vin, cl.needs_review))
    return {
        "eligible": list(seen.values()),
        "exclusions": exclusions,
        "staging": review,
        "screening": screening,
    }


def listings_route(state: DealerState) -> str:
    """Only eligible units are enriched (F12)."""
    return "enricher" if state["eligible"] else "validate_dealer"


async def validate_dealer(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    from carshop.agents.enricher import apply_resolution

    ctx = runtime.context
    settings = ctx.settings
    task = state["task"]
    dealer_id = task["dealer_id"]
    rec = state["fetch"]
    enr = state.get("enrichment") or Enrichment()
    screening = state["screening"]
    resolutions = state.get("resolutions", {})
    exclusions = list(state["exclusions"])
    observations: list[Observation] = []
    for unit in enr.units:
        if not unit.detailed:
            observations.append(unit.observation)
            continue
        obs = unit.observation
        if unit.vin in resolutions:
            obs = apply_resolution(obs, resolutions[unit.vin])
        # Hard rules re-checked with detail evidence: a card may omit the odometer that the
        # detail page shows (El Paso 2026-10-19: "Mileage: 9,546" on a "new" X5).
        if obs.odometer_miles is not None and obs.odometer_miles > settings.new_odometer_max_miles:
            exclusions.append(
                Exclusion(
                    vin=unit.vin,
                    url=obs.detail_url or obs.source_url,
                    dealer_id=dealer_id,
                    code=ExclusionCode.DEMO_MILEAGE,
                    quote=f"Odometer {obs.odometer_miles} miles (detail page)",
                )
            )
            continue
        alts = tuple(sorted(screening.alternates.get(unit.vin, set())))
        observations.append(obs.model_copy(update={"alternate_urls": alts}) if alts else obs)

    def verdict(
        status: DealerStatus, reason: ReasonCode | None, *, anomaly: bool = False
    ) -> DealerState:
        return {
            "eligible": observations,
            "exclusions": exclusions,
            "dealer_status": DealerVerdict(
                status=status.value,
                reason=reason.value if reason else None,
                anomaly=anomaly,
                duration_ms=elapsed_ms(rec.started),
            ),
        }

    # --- completeness gates (F19) -----------------------------------------------------------
    reason = ReasonCode.TIMEOUT if enr.timed_out else rec.reason
    if reason is ReasonCode.TIMEOUT:
        return verdict(DealerStatus.PARTIAL, ReasonCode.TIMEOUT)
    if rec.truncated or (rec.pages_expected and rec.pages_read < rec.pages_expected):
        return verdict(DealerStatus.PARTIAL, reason or ReasonCode.PAGINATION_INCOMPLETE)
    if screening.parse_failures:
        return verdict(DealerStatus.PARTIAL, ReasonCode.PARSER_FAILED)
    if rec.pages_failed:
        return verdict(DealerStatus.PARTIAL, reason or ReasonCode.PARSER_FAILED)
    prior = task["prior_eligible_count"]
    anomaly = bool(prior and prior > 0 and not observations)  # silent zero: legal, but flagged
    log.info(
        "dealer done",
        dealer=dealer_id,
        status="REPORTED",
        eligible=len(observations),
        raw=len(state["listings"]),
        pages=rec.pages_read,
        browser=rec.used_browser,
    )
    return verdict(
        DealerStatus.REPORTED,
        ReasonCode.DETAILS_FAILED if enr.details_failed else None,
        anomaly=anomaly,
    )
