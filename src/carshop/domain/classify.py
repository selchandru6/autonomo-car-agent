"""Eligibility rules (requirements 3, 4, 7.2). Deterministic; every exclusion carries a quote."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from carshop.domain import vin as vinmod
from carshop.domain.codes import (
    ExclusionCode,
    ExtractionMethod,
    Feature,
    Flag,
    Tri,
)
from carshop.domain.features import evaluate_features
from carshop.domain.models import (
    ClassifiedListing,
    Exclusion,
    Observation,
    PriceComponentRecord,
    RawListing,
)
from carshop.domain.pricing import build_price_set
from carshop.reference.data import AG_CODES, match_exterior, match_upholstery

_USED_RE = re.compile(r"\b(used|pre-?owned|certified|cpo)\b", re.I)
_DEMO_RE = re.compile(r"\b(demo|demonstrator|loaner|courtesy|service loaner)\b", re.I)
_PENDING_RE = re.compile(r"\b(sale pending|sold|pending sale|deposit taken)\b", re.I)
_TRANSIT_RE = re.compile(
    r"\b(in[- ]transit|in production|arriving soon|on order|build in progress)\b", re.I
)
_X5M_RE = re.compile(r"\bX5\s*M\b(?!\s*Sport)|\bX5\s*M\s*Competition\b", re.I)
_XDRIVE40I_RE = re.compile(r"xDrive\s?40i", re.I)


@dataclass(frozen=True, slots=True)
class ClassifyContext:
    dealer_id: str
    fetched_at: datetime
    adapter_version: str
    model_year: int
    target_ag: str
    odometer_max: int
    band_min_cents: int
    band_max_cents: int


def _q(text: str, pattern: re.Pattern[str], fallback: str) -> str:
    m = pattern.search(text)
    if not m:
        return fallback[:160]
    return text[max(0, m.start() - 40) : m.end() + 40]


def _excl(
    raw: RawListing, ctx: ClassifyContext, code: ExclusionCode, quote: str
) -> ClassifiedListing:
    return ClassifiedListing(
        observation=None,
        exclusion=Exclusion(
            vin=raw.vin, url=raw.url, dealer_id=ctx.dealer_id, code=code, quote=quote[:300]
        ),
    )


def classify(raw: RawListing, ctx: ClassifyContext) -> ClassifiedListing:
    text = raw.full_text or ""
    header = " ".join(
        filter(None, [raw.condition_text, str(raw.year or ""), raw.make, raw.model, raw.trim])
    )

    # 1. VIN
    if not raw.vin:
        return _excl(raw, ctx, ExclusionCode.VIN_CHECKSUM_FAIL, "VIN not present on page")
    info = vinmod.decode(raw.vin)
    if not info.valid:
        return _excl(raw, ctx, ExclusionCode.VIN_CHECKSUM_FAIL, f"VIN: {raw.vin}")

    # 2. Condition
    cond = raw.condition_text or ""
    if _USED_RE.search(cond) or (not cond and _USED_RE.search(header)):
        return _excl(
            raw, ctx, ExclusionCode.USED_OR_CPO, _q(cond or header, _USED_RE, cond or header)
        )
    if _PENDING_RE.search(raw.availability_text or ""):
        return _excl(raw, ctx, ExclusionCode.SALE_PENDING, raw.availability_text or "")
    if _DEMO_RE.search(cond):
        return _excl(raw, ctx, ExclusionCode.DEMO_MILEAGE, cond)

    # 3. Model / year
    model = (raw.model or "").strip()
    if model and not re.fullmatch(r"X5", model, re.I):
        if _X5M_RE.search(f"{model} {raw.trim or ''}"):
            return _excl(raw, ctx, ExclusionCode.MODEL_X5M, header)
        return _excl(raw, ctx, ExclusionCode.NOT_X5, header)
    if _X5M_RE.search(raw.trim or ""):
        return _excl(raw, ctx, ExclusionCode.MODEL_X5M, header)
    if raw.year is not None and raw.year != ctx.model_year:
        return _excl(raw, ctx, ExclusionCode.NOT_MY2026, header)
    if info.year != ctx.model_year:
        # VIN says a different year than the page or target: conflict, not in scope.
        return _excl(
            raw,
            ctx,
            ExclusionCode.CLASSIFICATION_CONFLICT,
            f"VIN year {info.year} vs page {raw.year}: {header}",
        )

    # 4. Powertrain via AG code, corroborated by an exact listing quote
    ag = info.ag_code
    known = AG_CODES.get(ag)
    if known is None:
        return _excl(raw, ctx, ExclusionCode.POWERTRAIN_OTHER, f"AG {ag}: {header}")
    vehicle, excl = known
    if excl is not None:
        return _excl(raw, ctx, excl, f"AG {ag} = {vehicle}: {header}")
    if not _XDRIVE40I_RE.search(f"{raw.trim or ''} {header} {text[:2000]}"):
        return _excl(
            raw,
            ctx,
            ExclusionCode.CLASSIFICATION_CONFLICT,
            f"AG {ag} but no xDrive40i quote: {header}",
        )

    # 5. Odometer
    if raw.odometer_miles is not None and raw.odometer_miles > ctx.odometer_max:
        return _excl(raw, ctx, ExclusionCode.DEMO_MILEAGE, f"Odometer {raw.odometer_miles} miles")

    # 6. Prices
    ps = build_price_set(raw.price_pairs)
    flags: set[Flag] = set(ps.flags)
    comparison = ps.comparison_price_cents(ctx.band_min_cents, ctx.band_max_cents)
    flags |= ps.flags

    # 7. Availability
    avail_src = f"{raw.availability_text or ''} {raw.condition_text or ''}"
    in_transit = bool(_TRANSIT_RE.search(avail_src))
    if in_transit:
        flags.add(Flag.IN_TRANSIT)
    availability = "IN_TRANSIT" if in_transit else "ON_LOT" if raw.availability_text else "UNKNOWN"

    # 8. Features
    feature_text = " ".join(filter(None, [raw.package_text, text]))
    features = evaluate_features(
        feature_text,
        source_kind=raw.source_kind,
        url=raw.detail_url or raw.url,
        page_hash=raw.page_hash,
    )
    dap = next(f.status for f in features if f.feature is Feature.DAP)
    flags.add({Tri.YES: Flag.DAP_YES, Tri.NO: Flag.DAP_NO, Tri.UNKNOWN: Flag.DAP_UNKNOWN}[dap])
    m_sport = any(f.feature is Feature.M_SPORT and f.status is Tri.YES for f in features)
    if m_sport:
        flags.add(Flag.PKG_M_SPORT)

    # 9. Colours
    ext_code = match_exterior(raw.exterior_raw)
    int_code = match_upholstery(raw.interior_raw)
    if (raw.exterior_raw and ext_code is None) or (raw.interior_raw and int_code is None):
        flags.add(Flag.COLOR_UNMAPPED)

    needs_review: str | None = None
    if Flag.PRICE_IMPLAUSIBLE in flags:
        needs_review = "PRICE_OUTSIDE_BAND"

    obs = Observation(
        vin=info.vin,
        dealer_id=ctx.dealer_id,
        source_url=raw.url,
        detail_url=raw.detail_url,
        page_hash=raw.page_hash,
        fetched_at=ctx.fetched_at,
        stock=raw.stock,
        year=ctx.model_year,
        model="X5",
        trim="xDrive40i",
        ag_code=ag,
        exterior_raw=raw.exterior_raw,
        exterior_code=ext_code,
        interior_raw=raw.interior_raw,
        interior_code=int_code,
        odometer_miles=raw.odometer_miles,
        availability=availability,
        in_transit=in_transit,
        msrp_cents=ps.msrp_cents,
        advertised_price_cents=ps.advertised_price_cents,
        comparison_price_cents=comparison,
        prices=tuple(
            PriceComponentRecord(
                kind=c.kind,
                cents=c.cents,
                label=c.label,
                eligibility_text=c.eligibility_text,
                quote=c.quote,
            )
            for c in ps.components
        ),
        features=tuple(features),
        dap_status=dap,
        m_sport=m_sport,
        flags=frozenset(flags),
        extraction_method=ExtractionMethod.PARSER,
        adapter_version=ctx.adapter_version,
        image_url=raw.image_url,
        sticker_url=raw.sticker_url,
    )
    return ClassifiedListing(observation=obs, exclusion=None, needs_review=needs_review)
