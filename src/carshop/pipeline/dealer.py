"""One dealer branch: robots -> listing pages -> classify -> detail enrichment -> gates.

Writes nothing to the database; returns a DealerResult that the supervisor stores in one
transaction. Every exit path yields a terminal status with a reason (requirements 5, 7.3).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

import structlog

from carshop.adapters.base import Adapter, DetailParse, PageParse
from carshop.adapters.detect import adapter_for, detect_platform, has_dedicated_adapter
from carshop.config.settings import Settings
from carshop.domain.classify import ClassifyContext, classify
from carshop.domain.codes import (
    DealerStatus,
    ExclusionCode,
    ExtractionMethod,
    Feature,
    Flag,
    ReasonCode,
    SourceKind,
    Tri,
)
from carshop.domain.features import evaluate_features, merge_feature_sets
from carshop.domain.models import (
    Dealer,
    DealerResult,
    Exclusion,
    FetchAttempt,
    Observation,
    PriceComponentRecord,
)
from carshop.domain.pricing import build_price_set
from carshop.extract.llm import PackageResolver
from carshop.fetch.artifacts import ArtifactStore
from carshop.fetch.browser import BrowserRenderer, RenderResult
from carshop.fetch.http import HttpFetcher
from carshop.fetch.politeness import HostGate
from carshop.observability.tracing import span
from carshop.safety import robots as robots_mod
from carshop.util import utcnow

log = structlog.get_logger(__name__)
_VIN_RE = re.compile(r"\b5UX[A-HJ-NPR-Z0-9]{14}\b")  # BMW SAV WMI; detail-page VIN cross-check

_BLOCK_CODES = {
    ReasonCode.HTTP_403,
    ReasonCode.CAPTCHA,
    ReasonCode.HTTP_404,
    ReasonCode.TIMEOUT,
    ReasonCode.HTTP_429,
}


@dataclass(slots=True)
class BranchDeps:
    settings: Settings
    http: HttpFetcher
    browser: BrowserRenderer | None
    artifacts: ArtifactStore
    gate: HostGate
    resolver: PackageResolver
    run_id: str
    prior_eligible_count: int | None = None  # last REPORTED count, for the silent-zero rule


@dataclass(slots=True)
class _Acc:
    attempts: list[FetchAttempt] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    exclusions: list[Exclusion] = field(default_factory=list)
    review: list[tuple[str, str]] = field(default_factory=list)
    pages_expected: int = 0
    pages_read: int = 0
    pages_failed: int = 0
    raw_count: int = 0
    details_failed: int = 0
    reason: ReasonCode | None = None
    robots_hash: str | None = None
    used_browser: bool = False


async def run_dealer(dealer: Dealer, deps: BranchDeps) -> DealerResult:
    start = time.monotonic()
    acc = _Acc()
    adapter = adapter_for(dealer.platform)
    log_ = log.bind(dealer=dealer.dealer_id, run_id=deps.run_id)

    def finish(
        status: DealerStatus, reason: ReasonCode | None, *, anomaly: bool = False
    ) -> DealerResult:
        return DealerResult(
            dealer_id=dealer.dealer_id, status=status, reason=reason, pages_expected=acc.pages_expected,
            pages_read=acc.pages_read, pages_failed=acc.pages_failed, raw_count=acc.raw_count,
            eligible_count=len(acc.observations), duration_ms=int((time.monotonic() - start) * 1000),
            cost_usd=0.0, anomaly=anomaly, observations=tuple(acc.observations), exclusions=tuple(acc.exclusions),
            attempts=tuple(acc.attempts), review_reasons=tuple(acc.review), robots_hash=acc.robots_hash,
            adapter_version=adapter.version if adapter else None,
        )  # fmt: skip

    with span("dealer", dealer_id=dealer.dealer_id, platform=dealer.platform.value):
        if not dealer.fetchable:
            # v1.3: no approval gate. A registry dealer without a start URL had no discoverable
            # public inventory page (F25); it is NO_SOURCE and stays in the denominator.
            return finish(DealerStatus.NO_SOURCE, ReasonCode.NO_INVENTORY_URL)
        assert dealer.start_url is not None
        if adapter is None:
            return finish(DealerStatus.NOT_REPORTED, ReasonCode.LAYOUT_UNKNOWN)
        branch_deadline = start + deps.settings.branch_timeout_s

        # --- robots (7.4) -------------------------------------------------------------
        robots_url = f"https://{dealer.hostname}/robots.txt"
        async with await deps.gate(robots_url):
            rres = await deps.http.get(robots_url, dealer, sleep=False)
        decision = robots_mod.evaluate(
            rres.body if rres.status == 200 else None,
            rres.status,
            dealer.start_url,
            deps.settings.http_user_agent,
        )
        acc.robots_hash = decision.sha256
        acc.attempts.append(
            _attempt(
                robots_url,
                rres.final_url,
                rres.status,
                rres.latency_ms,
                decision.detail[:40],
                None,
                None,
                rres.error,
                "http",
            )
        )
        if not decision.allowed:
            return finish(DealerStatus.NOT_REPORTED, ReasonCode.ROBOTS_DISALLOWED)

        # --- listing pages (within the branch budget, 7.4) -----------------------------------
        # Pages already parsed survive a timeout: the dealer ends PARTIAL / TIMEOUT, not empty.
        pages: list[PageParse] = []
        chosen: list[Adapter] = [adapter]
        try:
            await asyncio.wait_for(
                _fetch_listing_pages(dealer, adapter, deps, acc, pages, chosen),
                timeout=max(1.0, branch_deadline - time.monotonic()),
            )
        except TimeoutError:
            acc.reason = ReasonCode.TIMEOUT
            if acc.pages_read:
                acc.pages_failed += 1
                acc.pages_expected = max(acc.pages_expected, acc.pages_read + 1)
        adapter = chosen[0]  # the inventory page is the authority on the platform
        if (
            acc.reason in _BLOCK_CODES
            or acc.reason
            in (
                ReasonCode.UNRENDERED_SHELL,
                ReasonCode.LAYOUT_UNKNOWN,
                ReasonCode.OFF_DOMAIN,
                ReasonCode.SSRF_BLOCKED,
            )
        ) and acc.pages_read == 0:
            return finish(DealerStatus.NOT_REPORTED, acc.reason)

        explicit_empty = bool(pages) and all(p.explicit_empty for p in pages)
        fetched_at = utcnow()
        ctx = ClassifyContext(
            dealer_id=dealer.dealer_id, fetched_at=fetched_at, adapter_version=adapter.version,
            model_year=deps.settings.model_year, target_ag=deps.settings.target_ag_code,
            odometer_max=deps.settings.new_odometer_max_miles, band_min_cents=deps.settings.price_band_min_cents,
            band_max_cents=deps.settings.price_band_max_cents,
        )  # fmt: skip
        seen: dict[str, Observation] = {}
        alternates: dict[str, set[str]] = {}
        excluded_vins: set[str] = set()
        for page in pages:
            for raw in page.listings:
                acc.raw_count += 1
                cl = classify(raw, ctx)
                if cl.exclusion:
                    if cl.exclusion.vin and cl.exclusion.vin in excluded_vins:
                        continue  # the same card repeated on another page: one exclusion row
                    if cl.exclusion.vin:
                        excluded_vins.add(cl.exclusion.vin)
                    acc.exclusions.append(cl.exclusion)
                    if cl.exclusion.code.value in ("VIN_CHECKSUM_FAIL", "CLASSIFICATION_CONFLICT"):
                        acc.review.append((cl.exclusion.vin or "", cl.exclusion.code.value))
                    continue
                assert cl.observation is not None
                obs = cl.observation
                if (
                    obs.vin in seen
                ):  # F13: one observation per (run, dealer, vin); keep alternate URLs
                    alternates.setdefault(obs.vin, set()).add(obs.detail_url or obs.source_url)
                    continue
                seen[obs.vin] = obs
                if cl.needs_review:
                    acc.review.append((obs.vin, cl.needs_review))

        # --- detail enrichment (F12) ----------------------------------------------------------
        enriched: list[Observation] = []
        detail_deadline = min(time.monotonic() + deps.settings.detail_timeout_s, branch_deadline)
        for vin, obs in seen.items():
            if time.monotonic() > detail_deadline:
                acc.details_failed += 1
                enriched.append(obs)
                if time.monotonic() > branch_deadline:
                    acc.reason = ReasonCode.TIMEOUT  # branch budget, not just the detail budget
                continue
            try:
                obs2 = await asyncio.wait_for(
                    _enrich(dealer, adapter, deps, acc, obs, ctx),
                    timeout=max(1.0, detail_deadline - time.monotonic()),
                )
            except TimeoutError:
                acc.details_failed += 1
                enriched.append(obs)
                if time.monotonic() >= branch_deadline:
                    acc.reason = ReasonCode.TIMEOUT
                continue
            # Hard rules re-checked with detail evidence: a card may omit the odometer that the
            # detail page shows (El Paso 2026-10-19: "Mileage: 9,546" on a "new" X5).
            if (
                obs2.odometer_miles is not None
                and obs2.odometer_miles > deps.settings.new_odometer_max_miles
            ):
                acc.exclusions.append(
                    Exclusion(
                        vin=vin,
                        url=obs2.detail_url or obs2.source_url,
                        dealer_id=dealer.dealer_id,
                        code=ExclusionCode.DEMO_MILEAGE,
                        quote=f"Odometer {obs2.odometer_miles} miles (detail page)",
                    )
                )
                continue
            alts = tuple(sorted(alternates.get(vin, set())))
            enriched.append(obs2.model_copy(update={"alternate_urls": alts}) if alts else obs2)
        acc.observations = enriched

        # --- completeness gates (F19) -----------------------------------------------------
        if acc.pages_read == 0 and not explicit_empty:
            return finish(DealerStatus.NOT_REPORTED, acc.reason or ReasonCode.PARSER_FAILED)
        if acc.reason is ReasonCode.TIMEOUT:
            return finish(DealerStatus.PARTIAL, ReasonCode.TIMEOUT)
        if acc.pages_expected and acc.pages_read < acc.pages_expected:
            return finish(DealerStatus.PARTIAL, ReasonCode.PAGINATION_INCOMPLETE)
        if acc.pages_failed:
            return finish(DealerStatus.PARTIAL, acc.reason or ReasonCode.PARSER_FAILED)
        anomaly = False
        if deps.prior_eligible_count and deps.prior_eligible_count > 0 and not acc.observations:
            anomaly = True  # silent zero: legal, but flagged (section 4)
        reason = ReasonCode.DETAILS_FAILED if acc.details_failed else None
        log_.info(
            "dealer done",
            status="REPORTED",
            eligible=len(acc.observations),
            raw=acc.raw_count,
            pages=acc.pages_read,
            browser=acc.used_browser,
        )
        return finish(DealerStatus.REPORTED, reason, anomaly=anomaly)


def _attempt(url: str, final: str | None, status: int | None, latency: int, robots: str, chash: str | None, uri: str | None,
             err: ReasonCode | None, renderer: str, attempt: int = 1) -> FetchAttempt:  # fmt: skip
    return FetchAttempt(url=url, final_url=final, http_status=status, attempt=attempt, latency_ms=latency, robots_decision=robots,
                        content_hash=chash, storage_uri=uri, error_code=err, renderer=renderer)  # fmt: skip


def _readapt(adapter: Adapter, html: str, chosen: list[Adapter]) -> Adapter:
    """When the registry's platform has no dedicated adapter but the fetched page's markup
    does (Cardenas 2026-10-20: registry `unknown`, page DealerFire), switch to it."""
    if has_dedicated_adapter(adapter.platform):
        return adapter
    plat = detect_platform(html)
    if plat is not adapter.platform and has_dedicated_adapter(plat):
        better = adapter_for(plat)
        if better is not None:
            log.info("adapter switched", from_=adapter.platform.value, to=plat.value)
            chosen[0] = better
            return better
    return adapter


async def _fetch_listing_pages(
    dealer: Dealer,
    adapter: Adapter,
    deps: BranchDeps,
    acc: _Acc,
    pages: list[PageParse],
    chosen: list[Adapter] | None = None,
) -> None:
    """HTTP first; a shell or a block escalates to one browser pass (7.4).

    Parsed pages are appended to `pages` as they arrive so a branch timeout keeps them.
    `chosen[0]` receives the adapter actually used when the page's platform overrides the
    registry's."""
    assert dealer.start_url
    s = deps.settings
    cap = s.page_cap
    chosen = chosen if chosen is not None else [adapter]

    # 1. HTTP attempt on the start URL.
    async with await deps.gate(dealer.start_url):
        hres = await deps.http.get(dealer.start_url, dealer)
    if hres.error in (ReasonCode.OFF_DOMAIN, ReasonCode.SSRF_BLOCKED, ReasonCode.ROBOTS_DISALLOWED):
        acc.attempts.append(
            _attempt(
                dealer.start_url,
                hres.final_url,
                hres.status,
                hres.latency_ms,
                "allowed",
                None,
                None,
                hres.error,
                "http",
                hres.attempts,
            )
        )
        acc.reason = hres.error
        return
    http_ok = hres.status == 200 and hres.body is not None
    if http_ok:
        assert hres.body is not None
        adapter = _readapt(adapter, hres.body, chosen)
        art = deps.artifacts.put(deps.run_id, dealer.dealer_id, "p1", hres.body)
        parsed = adapter.parse_listing_page(
            hres.body, hres.final_url or dealer.start_url, art.sha256
        )
        acc.attempts.append(
            _attempt(
                dealer.start_url,
                hres.final_url,
                hres.status,
                hres.latency_ms,
                "allowed",
                art.sha256,
                art.storage_uri,
                None,
                "http",
                hres.attempts,
            )
        )
        if parsed.listings or parsed.explicit_empty:
            pages.append(parsed)
            acc.pages_read = 1
            acc.pages_expected = min(parsed.total_pages or 1, cap)
            if adapter.pagination == "url":
                n = 1
                visited = {dealer.start_url, hres.final_url or ""}
                nxt = parsed.next_url
                while nxt and n < cap and nxt not in visited:  # a repeating "next" is a loop
                    visited.add(nxt)
                    n += 1
                    async with await deps.gate(nxt):
                        r = await deps.http.get(nxt, dealer)
                    if r.status != 200 or r.body is None:
                        acc.pages_failed += 1
                        acc.attempts.append(
                            _attempt(
                                nxt,
                                r.final_url,
                                r.status,
                                r.latency_ms,
                                "allowed",
                                None,
                                None,
                                r.error or ReasonCode.PARSER_FAILED,
                                "http",
                                r.attempts,
                            )
                        )
                        acc.reason = r.error or ReasonCode.PAGINATION_INCOMPLETE
                        break
                    a2 = deps.artifacts.put(deps.run_id, dealer.dealer_id, f"p{n}", r.body)
                    p2 = adapter.parse_listing_page(r.body, r.final_url or nxt, a2.sha256)
                    acc.attempts.append(
                        _attempt(
                            nxt,
                            r.final_url,
                            r.status,
                            r.latency_ms,
                            "allowed",
                            a2.sha256,
                            a2.storage_uri,
                            None,
                            "http",
                            r.attempts,
                        )
                    )
                    pages.append(p2)
                    acc.pages_read += 1
                    nxt = p2.next_url
                if nxt and n >= cap:
                    acc.pages_expected = cap
                return
            # in_page pagination cannot run over HTTP; fall through to the browser for pages 2+.
            if (parsed.total_pages or 1) <= 1:
                return
            if deps.browser is None:
                # Page 1 is real, pages 2+ unreachable without the site's pager: PARTIAL.
                acc.reason = ReasonCode.PAGINATION_INCOMPLETE
                return
        else:
            # HTTP returned a shell (200 but nothing parseable): escalate once.
            acc.reason = ReasonCode.UNRENDERED_SHELL
        pages.clear()
        acc.pages_read = 0
    else:
        acc.attempts.append(
            _attempt(
                dealer.start_url,
                hres.final_url,
                hres.status,
                hres.latency_ms,
                "allowed",
                None,
                None,
                hres.error,
                "http",
                hres.attempts,
            )
        )
        acc.reason = hres.error or ReasonCode.UNRENDERED_SHELL

    # 2. One browser render (with the site's own pager when the adapter needs it).
    if deps.browser is None:
        return
    acc.used_browser = True
    async with await deps.gate(dealer.start_url):
        if adapter.pagination == "in_page":
            renders = await deps.browser.render_paginated(
                dealer.start_url,
                dealer,
                next_selector=adapter.next_selector,
                card_selector=adapter.card_selector,
                state_selector=adapter.state_selector,
                max_pages=cap,
            )
        else:
            renders = [
                await deps.browser.render(
                    dealer.start_url, dealer, wait_selector=adapter.wait_selector, scroll=True
                )
            ]
    renders_all: list[RenderResult] = list(renders)
    if renders and renders[0].error is None and renders[0].html:
        adapter = _readapt(adapter, renders[0].html, chosen)
    if adapter.pagination == "url" and renders and renders[0].error is None and renders[0].html:
        first = adapter.parse_listing_page(
            renders[0].html, renders[0].final_url or dealer.start_url, "pending"
        )
        n = 1
        visited = {dealer.start_url, renders[0].final_url or ""}
        nxt = first.next_url
        while nxt and n < cap and nxt not in visited:  # a repeating "next" is a loop
            visited.add(nxt)
            n += 1
            async with await deps.gate(nxt):
                renders_all.append(
                    await deps.browser.render(
                        nxt, dealer, wait_selector=adapter.wait_selector, scroll=True
                    )
                )
            nxt = None
            last = renders_all[-1]
            if last.error is None and last.html:
                nxt = adapter.parse_listing_page(
                    last.html, last.final_url or last.url, "pending"
                ).next_url
    for i, rr in enumerate(renders_all, start=1):
        if rr.error is not None or not rr.html:
            acc.pages_failed += 1
            acc.attempts.append(
                _attempt(
                    rr.url,
                    rr.final_url,
                    rr.status,
                    rr.latency_ms,
                    "allowed",
                    None,
                    None,
                    rr.error or ReasonCode.PARSER_FAILED,
                    "browser",
                )
            )
            acc.reason = rr.error or ReasonCode.PARSER_FAILED
            continue
        art = deps.artifacts.put(deps.run_id, dealer.dealer_id, f"p{i}", rr.html)
        parsed = adapter.parse_listing_page(rr.html, rr.final_url or rr.url, art.sha256)
        acc.attempts.append(
            _attempt(
                rr.url,
                rr.final_url,
                rr.status,
                rr.latency_ms,
                "allowed",
                art.sha256,
                art.storage_uri,
                None,
                "browser",
            )
        )
        if not parsed.listings and not parsed.explicit_empty:
            if i == 1:
                plat = detect_platform(rr.html)
                acc.reason = (
                    ReasonCode.UNRENDERED_SHELL
                    if parsed.layout_ok and plat == adapter.platform
                    else ReasonCode.LAYOUT_UNKNOWN
                )
                acc.pages_failed += 1
                continue
            acc.pages_failed += 1
            acc.reason = ReasonCode.PARSER_FAILED
            continue
        pages.append(parsed)
        acc.pages_read += 1
        if i == 1:
            acc.pages_expected = min(parsed.total_pages or 1, cap)
    if pages and acc.pages_read >= 1:
        acc.reason = None if not acc.pages_failed else acc.reason


async def _enrich(
    dealer: Dealer,
    adapter: Adapter,
    deps: BranchDeps,
    acc: _Acc,
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
    html: str | None = None
    renderer = "http"
    async with await deps.gate(url):
        if acc.used_browser and deps.browser is not None:
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
        acc.details_failed += 1
        acc.attempts.append(
            _attempt(
                url,
                final,
                status,
                lat,
                "allowed",
                None,
                None,
                err or ReasonCode.DETAILS_FAILED,
                renderer,
            )
        )
        return obs
    art = deps.artifacts.put(deps.run_id, dealer.dealer_id, f"vdp_{obs.vin}", html)
    acc.attempts.append(
        _attempt(url, final, status, lat, "allowed", art.sha256, art.storage_uri, None, renderer)
    )
    det: DetailParse = adapter.parse_detail_page(html, final or url)
    if not det.layout_ok:
        acc.details_failed += 1
        return obs
    # The detail page must be about this VIN. A redirect to a "similar vehicles" or sold page
    # names other VINs; merging their prices or packages would corrupt this observation.
    vins_on_page = {m.upper() for m in _VIN_RE.findall(html)}
    if vins_on_page and obs.vin not in vins_on_page:
        acc.details_failed += 1
        log.warning(
            "detail page VIN mismatch",
            dealer=dealer.dealer_id,
            vin=obs.vin,
            page_vins=sorted(vins_on_page)[:3],
        )
        return obs
    return await _merge_detail(obs, det, final or url, art.sha256, deps, ctx)


async def _merge_detail(
    obs: Observation,
    det: DetailParse,
    url: str,
    page_hash: str,
    deps: BranchDeps,
    ctx: ClassifyContext,
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
    if det.price_pairs:
        dps = build_price_set(det.price_pairs)
        if adv is None and dps.advertised_price_cents is not None:
            adv = dps.advertised_price_cents
            comparison = dps.comparison_price_cents(ctx.band_min_cents, ctx.band_max_cents)
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
        text, source_kind=SourceKind.DETAIL, url=url, page_hash=page_hash
    )
    feats = merge_feature_sets(list(obs.features), detail_feats)
    method = obs.extraction_method
    unknown = [f.feature for f in feats if f.status is Tri.UNKNOWN]
    if unknown and det.package_text and deps.resolver.enabled:
        llm_feats = await deps.resolver.resolve(
            det.package_text, unknown, url=url, page_hash=page_hash, source_kind=SourceKind.DETAIL
        )
        if llm_feats:
            feats = merge_feature_sets(feats, llm_feats)
            method = ExtractionMethod.LLM
    dap = next(f.status for f in feats if f.feature is Feature.DAP)
    for fl in (Flag.DAP_YES, Flag.DAP_NO, Flag.DAP_UNKNOWN):
        flags.discard(fl)
    flags.add({Tri.YES: Flag.DAP_YES, Tri.NO: Flag.DAP_NO, Tri.UNKNOWN: Flag.DAP_UNKNOWN}[dap])
    m_sport = any(f.feature is Feature.M_SPORT and f.status is Tri.YES for f in feats)
    if m_sport:
        flags.add(Flag.PKG_M_SPORT)
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
            "prices": tuple(prices),
            "features": tuple(feats),
            "dap_status": dap,
            "m_sport": m_sport,
            "flags": frozenset(flags),
            "extraction_method": method,
            "availability": availability,
            "in_transit": in_transit,
            "sticker_url": obs.sticker_url or det.sticker_url,
            "detail_url": url,
        }
    )
