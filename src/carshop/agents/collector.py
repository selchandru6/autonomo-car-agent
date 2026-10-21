"""A3 Collector: robots, then listing pages in the order of §7.4 (HTTP, then one browser pass).

The only writer of NOT_REPORTED for a dealer (requirements-mas.md §5 invariant 3): a block,
captcha, robots refusal, off-domain redirect, timeout before any page, or no readable page at
all ends the branch here and goes straight to the Recorder.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from langgraph.runtime import Runtime

from carshop.adapters.base import Adapter, PageParse
from carshop.adapters.detect import adapter_for, detect_platform, has_dedicated_adapter
from carshop.domain.codes import DealerStatus, ReasonCode
from carshop.domain.models import Dealer, FetchAttempt
from carshop.fetch.browser import RenderResult
from carshop.graph.context import RunContext, Tools
from carshop.graph.state import DealerState, DealerVerdict, FetchRecord
from carshop.observability.tracing import span
from carshop.safety import robots as robots_mod
from carshop.util import sha256_text

log = structlog.get_logger(__name__)

_BLOCK_CODES = {
    ReasonCode.HTTP_403,
    ReasonCode.CAPTCHA,
    ReasonCode.HTTP_404,
    ReasonCode.TIMEOUT,
    ReasonCode.HTTP_429,
}
_NO_PAGE_CODES = _BLOCK_CODES | {
    ReasonCode.UNRENDERED_SHELL,
    ReasonCode.LAYOUT_UNKNOWN,
    ReasonCode.OFF_DOMAIN,
    ReasonCode.SSRF_BLOCKED,
}


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def collector(state: DealerState, runtime: Runtime[RunContext]) -> DealerState:
    ctx = runtime.context
    dealer = ctx.targets[state["task"]["dealer_id"]]
    start = time.monotonic()
    rec = FetchRecord(
        adapter=adapter_for(dealer.platform),
        started=start,
        deadline=start + ctx.settings.branch_timeout_s,
    )
    pages: list[PageParse] = []
    with span("collector", dealer_id=dealer.dealer_id, platform=dealer.platform.value):
        verdict = await collect(dealer, ctx.tools(), rec, pages)
    return {
        "fetch": rec,
        "pages": pages,
        "fetch_status": verdict,
        "fetch_reason": rec.reason.value if rec.reason else None,
    }


def collector_route(state: DealerState) -> str:
    """A dealer the Collector already settled goes straight to the Recorder."""
    return "recorder" if state.get("fetch_status") else "extractor"


async def collect(
    dealer: Dealer, deps: Tools, rec: FetchRecord, pages: list[PageParse]
) -> DealerVerdict | None:
    """Fills `rec` and `pages`. Returns a verdict only when the dealer cannot be read."""

    def stop(status: DealerStatus, reason: ReasonCode | None) -> DealerVerdict:
        return DealerVerdict(
            status=status.value,
            reason=reason.value if reason else None,
            anomaly=False,
            duration_ms=elapsed_ms(rec.started),
        )

    if not dealer.fetchable:
        # v1.3: no approval gate. A registry dealer without a start URL had no discoverable
        # public inventory page (F25); it is NO_SOURCE and stays in the denominator.
        return stop(DealerStatus.NO_SOURCE, ReasonCode.NO_INVENTORY_URL)
    assert dealer.start_url is not None
    adapter = rec.adapter
    if adapter is None:
        return stop(DealerStatus.NOT_REPORTED, ReasonCode.LAYOUT_UNKNOWN)

    # --- robots (7.4) -------------------------------------------------------------------
    robots_url = f"https://{dealer.hostname}/robots.txt"
    async with await deps.gate(robots_url):
        rres = await deps.http.get(robots_url, dealer, sleep=False)
    rec.robots_body = rres.body if rres.status == 200 else None
    rec.robots_status = rres.status
    decision = robots_mod.evaluate(
        rec.robots_body, rres.status, dealer.start_url, deps.settings.http_user_agent
    )
    rec.robots_hash = decision.sha256
    rec.attempts.append(
        attempt(robots_url, rres.final_url, rres.status, rres.latency_ms, decision.detail[:40],
                None, None, rres.error, "http")
    )  # fmt: skip
    if not decision.allowed:
        return stop(DealerStatus.NOT_REPORTED, ReasonCode.ROBOTS_DISALLOWED)

    # --- listing pages (within the branch budget, 7.4) -----------------------------------
    # Pages already parsed survive a timeout: the dealer ends PARTIAL / TIMEOUT, not empty.
    chosen: list[Adapter] = [adapter]
    try:
        await asyncio.wait_for(
            _fetch_listing_pages(dealer, adapter, deps, rec, pages, chosen),
            timeout=max(1.0, rec.deadline - time.monotonic()),
        )
    except TimeoutError:
        rec.reason = ReasonCode.TIMEOUT
        if rec.pages_read:
            rec.pages_failed += 1
            rec.pages_expected = max(rec.pages_expected, rec.pages_read + 1)
    rec.adapter = chosen[0]  # the inventory page is the authority on the platform
    if rec.reason in _NO_PAGE_CODES and rec.pages_read == 0:
        return stop(DealerStatus.NOT_REPORTED, rec.reason)
    explicit_empty = bool(pages) and all(p.explicit_empty for p in pages)
    if rec.pages_read == 0 and not explicit_empty:
        return stop(DealerStatus.NOT_REPORTED, rec.reason or ReasonCode.PARSER_FAILED)
    return None


def attempt(url: str, final: str | None, status: int | None, latency: int, robots: str, chash: str | None, uri: str | None,
            err: ReasonCode | None, renderer: str, attempt: int = 1) -> FetchAttempt:  # fmt: skip
    return FetchAttempt(url=url, final_url=final, http_status=status, attempt=attempt, latency_ms=latency, robots_decision=robots,
                        content_hash=chash, storage_uri=uri, error_code=err, renderer=renderer)  # fmt: skip


def robots_ok(rec: FetchRecord, url: str, user_agent: str) -> bool:
    return robots_mod.evaluate(rec.robots_body, rec.robots_status, url, user_agent).allowed


def _vins_of(parsed: PageParse) -> set[str]:
    return {raw.vin for raw in parsed.listings if raw.vin}


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
    deps: Tools,
    acc: FetchRecord,
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
            attempt(dealer.start_url, hres.final_url, hres.status, hres.latency_ms, "allowed", None, None,
                    hres.error, "http", hres.attempts)
        )  # fmt: skip
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
            attempt(dealer.start_url, hres.final_url, hres.status, hres.latency_ms, "allowed", art.sha256,
                    art.storage_uri, None, "http", hres.attempts)
        )  # fmt: skip
        if parsed.listings or parsed.explicit_empty:
            pages.append(parsed)
            acc.pages_read = 1
            acc.pages_expected = parsed.total_pages or 1
            seen_hashes = {art.sha256}
            seen_vins = _vins_of(parsed)
            if adapter.pagination == "url":
                n = 1
                visited = {dealer.start_url, hres.final_url or ""}
                nxt = parsed.next_url
                while nxt and n < cap and nxt not in visited:  # a repeating "next" is a loop
                    if not robots_ok(acc, nxt, deps.settings.http_user_agent):
                        acc.truncated = True
                        acc.reason = ReasonCode.ROBOTS_DISALLOWED
                        acc.attempts.append(
                            attempt(nxt, None, None, 0, "disallowed", None, None,
                                    ReasonCode.ROBOTS_DISALLOWED, "http")
                        )  # fmt: skip
                        nxt = None
                        break
                    visited.add(nxt)
                    n += 1
                    async with await deps.gate(nxt):
                        r = await deps.http.get(nxt, dealer)
                    if r.status != 200 or r.body is None:
                        acc.pages_failed += 1
                        acc.attempts.append(
                            attempt(nxt, r.final_url, r.status, r.latency_ms, "allowed", None, None,
                                    r.error or ReasonCode.PARSER_FAILED, "http", r.attempts)
                        )  # fmt: skip
                        acc.reason = r.error or ReasonCode.PAGINATION_INCOMPLETE
                        break
                    a2 = deps.artifacts.put(deps.run_id, dealer.dealer_id, f"p{n}", r.body)
                    if a2.sha256 in seen_hashes:
                        # Same bytes again. A next link we have not already followed means the
                        # site still claims another page; a link back to a fetched page is the end.
                        p2 = adapter.parse_listing_page(r.body, r.final_url or nxt, a2.sha256)
                        acc.attempts.append(
                            attempt(nxt, r.final_url, r.status, r.latency_ms, "allowed", a2.sha256,
                                    a2.storage_uri, None, "http", r.attempts)
                        )  # fmt: skip
                        if p2.next_url and p2.next_url not in visited:
                            acc.truncated = True
                            acc.reason = ReasonCode.PAGINATION_INCOMPLETE
                        else:
                            pages.append(p2)
                            acc.pages_read += 1
                        break
                    seen_hashes.add(a2.sha256)
                    p2 = adapter.parse_listing_page(r.body, r.final_url or nxt, a2.sha256)
                    acc.attempts.append(
                        attempt(nxt, r.final_url, r.status, r.latency_ms, "allowed", a2.sha256,
                                a2.storage_uri, None, "http", r.attempts)
                    )  # fmt: skip
                    vins = _vins_of(p2)
                    if vins and vins <= seen_vins:
                        if p2.next_url or (p2.total_pages or 0) > acc.pages_read:
                            acc.truncated = True
                            acc.reason = ReasonCode.PAGINATION_INCOMPLETE
                        break
                    seen_vins.update(vins)
                    pages.append(p2)
                    acc.pages_read += 1
                    nxt = p2.next_url
                if nxt and n >= cap:
                    acc.truncated = True
                    acc.reason = ReasonCode.PAGINATION_INCOMPLETE
                    acc.pages_expected = max(acc.pages_expected, acc.pages_read + 1)
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
            attempt(dealer.start_url, hres.final_url, hres.status, hres.latency_ms, "allowed", None, None,
                    hres.error, "http", hres.attempts)
        )  # fmt: skip
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
        seen_hashes = {sha256_text(renders[0].html)}
        seen_vins = _vins_of(first)
        while nxt and n < cap and nxt not in visited:  # a repeating "next" is a loop
            if not robots_ok(acc, nxt, deps.settings.http_user_agent):
                acc.truncated = True
                acc.reason = ReasonCode.ROBOTS_DISALLOWED
                acc.attempts.append(
                    attempt(nxt, None, None, 0, "disallowed", None, None,
                            ReasonCode.ROBOTS_DISALLOWED, "browser")
                )  # fmt: skip
                nxt = None
                break
            visited.add(nxt)
            n += 1
            async with await deps.gate(nxt):
                last = await deps.browser.render(
                    nxt, dealer, wait_selector=adapter.wait_selector, scroll=True
                )
            if last.html and sha256_text(last.html) in seen_hashes:
                parsed_dup = adapter.parse_listing_page(
                    last.html, last.final_url or last.url, "pending"
                )
                acc.attempts.append(
                    attempt(nxt, last.final_url, last.status, last.latency_ms, "allowed",
                            sha256_text(last.html), None, None, "browser")
                )  # fmt: skip
                if parsed_dup.next_url and parsed_dup.next_url not in visited:
                    acc.truncated = True
                    acc.reason = ReasonCode.PAGINATION_INCOMPLETE
                else:
                    renders_all.append(last)
                break
            renders_all.append(last)
            nxt = None
            if last.error is None and last.html:
                seen_hashes.add(sha256_text(last.html))
                parsed_n = adapter.parse_listing_page(
                    last.html, last.final_url or last.url, "pending"
                )
                nxt = parsed_n.next_url
                vins = _vins_of(parsed_n)
                if vins and vins <= seen_vins:
                    if nxt or (parsed_n.total_pages or 0) > n:
                        acc.truncated = True
                        acc.reason = ReasonCode.PAGINATION_INCOMPLETE
                    nxt = None
                else:
                    seen_vins.update(vins)
        if nxt and n >= cap:
            acc.truncated = True
            acc.reason = ReasonCode.PAGINATION_INCOMPLETE
            acc.pages_expected = max(acc.pages_expected, n)
    for i, rr in enumerate(renders_all, start=1):
        if rr.error is not None or not rr.html:
            acc.pages_failed += 1
            acc.attempts.append(
                attempt(rr.url, rr.final_url, rr.status, rr.latency_ms, "allowed", None, None,
                        rr.error or ReasonCode.PARSER_FAILED, "browser")
            )  # fmt: skip
            acc.reason = rr.error or ReasonCode.PARSER_FAILED
            continue
        art = deps.artifacts.put(deps.run_id, dealer.dealer_id, f"p{i}", rr.html)
        parsed = adapter.parse_listing_page(rr.html, rr.final_url or rr.url, art.sha256)
        acc.attempts.append(
            attempt(rr.url, rr.final_url, rr.status, rr.latency_ms, "allowed", art.sha256,
                    art.storage_uri, None, "browser")
        )  # fmt: skip
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
            acc.pages_expected = max(acc.pages_expected, parsed.total_pages or 1)
    if pages and acc.pages_read >= 1 and not acc.truncated:
        acc.reason = None if not acc.pages_failed else acc.reason
