"""A6 Enricher: detail pages for eligible units only (F12), evidence merged into each unit.

Deterministic. Package text the rules leave UNKNOWN is recorded for the Extractor's model
step; `apply_resolution` folds the model's answer back in exactly as the merge would have.
"""

from __future__ import annotations

import asyncio
import re
import time

import structlog
from langgraph.runtime import Runtime

from carshop.adapters.base import Adapter, DetailParse
from carshop.agents.collector import attempt, robots_ok
from carshop.agents.validator import classify_context
from carshop.domain.classify import ClassifyContext
from carshop.domain.codes import ExtractionMethod, Feature, Flag, ReasonCode, SourceKind, Tri
from carshop.domain.features import evaluate_features, merge_feature_sets, resolved_dap
from carshop.domain.models import Dealer, FeatureEvidence, Observation, PriceComponentRecord
from carshop.domain.pricing import build_price_set
from carshop.graph.context import RunContext, Tools
from carshop.graph.state import (
    AmbiguousText,
    DealerState,
    EnrichedUnit,
    Enrichment,
    FetchRecord,
)
from carshop.observability.tracing import span

log = structlog.get_logger(__name__)
_VIN_RE = re.compile(r"\b5UX[A-HJ-NPR-Z0-9]{14}\b")  # BMW SAV WMI; detail-page VIN cross-check


async def enricher(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    ctx = runtime.context
    dealer = ctx.targets[state["task"]["dealer_id"]]
    rec = state["fetch"]
    assert rec.adapter is not None
    tools = ctx.tools()
    cctx = classify_context(dealer, rec.adapter, ctx.settings)
    enr = Enrichment()
    ambiguous: dict[str, AmbiguousText] = {}
    branch_deadline = rec.deadline
    detail_deadline = min(time.monotonic() + ctx.settings.detail_timeout_s, branch_deadline)
    with span("enricher", dealer_id=dealer.dealer_id):
        for obs in state["eligible"]:
            if time.monotonic() > detail_deadline:
                enr.details_failed += 1
                enr.units.append(EnrichedUnit(obs.vin, obs, detailed=False))
                if time.monotonic() > branch_deadline:
                    enr.timed_out = True  # branch budget, not just the detail budget
                continue
            try:
                obs2 = await asyncio.wait_for(
                    _enrich(dealer, rec.adapter, tools, rec, enr, ambiguous, obs, cctx),
                    timeout=max(1.0, detail_deadline - time.monotonic()),
                )
            except TimeoutError:
                enr.details_failed += 1
                enr.units.append(EnrichedUnit(obs.vin, obs, detailed=False))
                if time.monotonic() >= branch_deadline:
                    enr.timed_out = True
                continue
            enr.units.append(EnrichedUnit(obs.vin, obs2, detailed=True))
    return {"enrichment": enr, "ambiguous_text": ambiguous}


async def _enrich(
    dealer: Dealer,
    adapter: Adapter,
    deps: Tools,
    rec: FetchRecord,
    enr: Enrichment,
    ambiguous: dict[str, AmbiguousText],
    obs: Observation,
    ctx: ClassifyContext,
) -> Observation:
    """Fetch the detail page (same renderer that worked for the listing) and merge evidence."""
    url = obs.detail_url
    if not url:
        return obs
    if url.startswith("/"):
        url = f"https://{dealer.hostname}{url}"
    url = url.replace("http://", "https://", 1)
    if not robots_ok(rec, url, deps.settings.http_user_agent):
        enr.attempts.append(
            attempt(url, None, None, 0, "disallowed", None, None, ReasonCode.ROBOTS_DISALLOWED, "http")
        )  # fmt: skip
        return obs
    html: str | None = None
    renderer = "http"
    async with await deps.gate(url):
        if rec.used_browser and deps.browser is not None:
            renderer = "browser"
            r = await deps.browser.render(url, dealer)
            html, status, final, lat, err = (
                r.html if r.error is None else None,
                r.status,
                r.final_url,
                r.latency_ms,
                r.error,
            )
        else:
            h = await deps.http.get(url, dealer)
            html, status, final, lat, err = (
                (h.body if h.status == 200 else None),
                h.status,
                h.final_url,
                h.latency_ms,
                h.error,
            )
    if html is None:
        enr.details_failed += 1
        enr.attempts.append(
            attempt(url, final, status, lat, "allowed", None, None, err or ReasonCode.DETAILS_FAILED, renderer)
        )  # fmt: skip
        return obs
    art = deps.artifacts.put(deps.run_id, dealer.dealer_id, f"vdp_{obs.vin}", html)
    enr.attempts.append(
        attempt(url, final, status, lat, "allowed", art.sha256, art.storage_uri, None, renderer)
    )
    det: DetailParse = adapter.parse_detail_page(html, final or url)
    if not det.layout_ok:
        enr.details_failed += 1
        return obs
    # The detail page must be about this VIN. A redirect to a "similar vehicles" or sold page
    # names other VINs; merging their prices or packages would corrupt this observation.
    vins_on_page = {m.upper() for m in _VIN_RE.findall(html)}
    if vins_on_page and obs.vin not in vins_on_page:
        enr.details_failed += 1
        log.warning(
            "detail page VIN mismatch",
            dealer=dealer.dealer_id,
            vin=obs.vin,
            page_vins=sorted(vins_on_page)[:3],
        )
        return obs
    return _merge_detail(obs, det, final or url, art.sha256, ctx, ambiguous)


def _merge_detail(
    obs: Observation,
    det: DetailParse,
    url: str,
    page_hash: str,
    ctx: ClassifyContext,
    ambiguous: dict[str, AmbiguousText],
) -> Observation:
    from carshop.reference.data import match_exterior, match_upholstery

    flags = set(obs.flags)
    interior_raw = obs.interior_raw or det.interior_raw
    exterior_raw = obs.exterior_raw or det.exterior_raw
    odometer = obs.odometer_miles if obs.odometer_miles is not None else det.odometer_miles
    interior_code = match_upholstery(interior_raw)
    exterior_code = match_exterior(exterior_raw)
    if (interior_raw and not interior_code) or (exterior_raw and not exterior_code):
        flags.add(Flag.COLOR_UNMAPPED)
    elif Flag.COLOR_UNMAPPED in flags:
        flags.discard(Flag.COLOR_UNMAPPED)

    # Prices: the detail page never silently overwrites the listing. A disagreement is stored
    # as extra components tagged with the detail label, and the listing figure stays primary.
    prices = list(obs.prices)
    adv, msrp, comparison = obs.advertised_price_cents, obs.msrp_cents, obs.comparison_price_cents
    price_basis = obs.price_basis
    if det.price_pairs:
        dps = build_price_set(det.price_pairs)
        if adv is None and dps.advertised_price_cents is not None:
            adv = dps.advertised_price_cents
            comparison = dps.comparison_price_cents(ctx.band_min_cents, ctx.band_max_cents)
            price_basis = dps.price_basis.value
            flags.discard(Flag.PRICE_MISSING)
            flags |= dps.flags
            prices.extend(
                PriceComponentRecord(kind=c.kind, cents=c.cents, label=c.label, quote=c.quote)
                for c in dps.components
            )
        elif (
            adv is not None
            and dps.advertised_price_cents is not None
            and dps.advertised_price_cents != adv
        ):
            prices.extend(
                PriceComponentRecord(
                    kind=c.kind, cents=c.cents, label=f"detail: {c.label}", quote=c.quote
                )
                for c in dps.components
            )
        if msrp is None:
            msrp = dps.msrp_cents

    text = " ".join(filter(None, [det.package_text, det.full_text]))
    detail_feats = evaluate_features(
        text,
        source_kind=SourceKind.DETAIL,
        url=url,
        page_hash=page_hash,
        dap_text=det.package_text or "",
    )
    feats = merge_feature_sets(list(obs.features), detail_feats)
    unknown = [f.feature for f in feats if f.status is Tri.UNKNOWN]
    if unknown and det.package_text:
        ambiguous[obs.vin] = AmbiguousText(
            text=det.package_text,
            unknown=tuple(f.value for f in unknown),
            url=url,
            page_hash=page_hash,
        )
    flags, dap, m_sport = _package_flags(feats, flags)
    availability = obs.availability
    in_transit = obs.in_transit
    if det.availability_text and not obs.in_transit and "transit" in det.availability_text.lower():
        in_transit = True
        availability = "IN_TRANSIT"
        flags.add(Flag.IN_TRANSIT)
    if odometer is not None and odometer > ctx.odometer_max:
        # The listing passed but the detail page says demo mileage: keep the unit, send to review.
        flags.add(Flag.CLASSIFICATION_CONFLICT)
    return obs.model_copy(
        update={
            "interior_raw": interior_raw,
            "interior_code": interior_code,
            "exterior_raw": exterior_raw,
            "exterior_code": exterior_code,
            "odometer_miles": odometer,
            "msrp_cents": msrp,
            "advertised_price_cents": adv,
            "comparison_price_cents": comparison,
            "price_basis": price_basis,
            "prices": tuple(prices),
            "features": tuple(feats),
            "dap_status": dap,
            "m_sport": m_sport,
            "flags": frozenset(flags),
            "availability": availability,
            "in_transit": in_transit,
            "sticker_url": obs.sticker_url or det.sticker_url,
            "detail_url": url,
        }
    )


def _package_flags(feats: list[FeatureEvidence], flags: set[Flag]) -> tuple[set[Flag], Tri, bool]:
    dap = resolved_dap(feats)
    for fl in (Flag.DAP_YES, Flag.DAP_NO, Flag.DAP_UNKNOWN):
        flags.discard(fl)
    flags.add({Tri.YES: Flag.DAP_YES, Tri.NO: Flag.DAP_NO, Tri.UNKNOWN: Flag.DAP_UNKNOWN}[dap])
    m_sport = any(f.feature is Feature.M_SPORT and f.status is Tri.YES for f in feats)
    if m_sport:
        flags.add(Flag.PKG_M_SPORT)
    return flags, dap, m_sport


def apply_resolution(obs: Observation, resolved: list[FeatureEvidence]) -> Observation:
    """Folds the model's package answers into a merged unit (method becomes LLM)."""
    feats = merge_feature_sets(list(obs.features), resolved)
    flags, dap, m_sport = _package_flags(feats, set(obs.flags))
    return obs.model_copy(
        update={
            "features": tuple(feats),
            "dap_status": dap,
            "m_sport": m_sport,
            "flags": frozenset(flags),
            "extraction_method": ExtractionMethod.LLM,
        }
    )
