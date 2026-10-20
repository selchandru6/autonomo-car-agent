"""Single browser render (requirements 7.4 step 3).

One Chromium context per run. Navigation only: no form submission, no clicks on chat,
schedule, or check-availability controls. Redirect targets are validated like HTTP.
See doc/adr/0002-browser-identity.md for the user-agent decision.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from types import TracebackType
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from carshop.config.settings import Settings
from carshop.domain.codes import ReasonCode
from carshop.domain.models import Dealer
from carshop.safety.allowlist import check_url

_CAPTCHA_RE = re.compile(
    r"(captcha|cf-challenge|challenge-platform|Attention Required!|Just a moment\.\.\.|verify you are human|Access Denied)",
    re.I,
)


@dataclass(slots=True)
class RenderResult:
    url: str
    final_url: str | None
    status: int | None
    html: str | None
    latency_ms: int
    error: ReasonCode | None
    detail: str = ""


class BrowserRenderer:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None

    async def __aenter__(self) -> BrowserRenderer:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True, channel=self._s.browser_channel or None
        )
        ua = self.user_agent(self._browser.version)
        headers = self.identity_headers()
        self._ctx = await self._browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id=self._s.schedule_tz,
            user_agent=ua,
            extra_http_headers=headers or None,
            java_script_enabled=True,
            service_workers="block",
        )
        # Never load images or media: not stored, not needed (7.5).
        await self._ctx.route(
            re.compile(r".*\.(png|jpe?g|gif|webp|svg|mp4|woff2?|ttf)(\?.*)?$"),
            lambda route: route.abort(),
        )
        return self

    def user_agent(self, browser_version: str) -> str | None:
        """Honest browser identity (requirements v1.3 §Compliance; ADR 0002).

        The UA names the Chrome build that is actually running, with the "Headless" token
        dropped, and in "identified" mode carries the fetcher's product token as well.
        """
        mode = self._s.browser_ua_mode
        if mode == "native":
            return None
        major = browser_version.split(".", maxsplit=1)[0]
        base = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        )
        if mode == "identified":
            return f"{base} {self._s.browser_product_token}"
        return base

    def identity_headers(self) -> dict[str, str]:
        """Request headers that name the fetcher and the project on every browser request."""
        if self._s.browser_ua_mode != "identified":
            return {}
        h = {"X-Carshop": self._s.http_user_agent}
        if self._s.contact_email:
            h["From"] = self._s.contact_email
        return h

    async def __aexit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def render(
        self,
        url: str,
        dealer: Dealer,
        *,
        wait_selector: str | None = None,
        scroll: bool = False,
    ) -> RenderResult:
        """One page render. `scroll=True` walks to the bottom a few times so SRPs that mount
        cards lazily (DealerOn, Temple 2026-10-20: 5 of 15 cards without scrolling) are
        complete; it stops as soon as the document stops growing."""
        assert self._ctx is not None
        start = time.monotonic()
        page = await self._ctx.new_page()
        try:
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=self._s.browser_timeout_s * 1000
            )
            final = page.url
            decision = check_url(final, dealer)
            if not decision.allowed:
                return RenderResult(
                    url,
                    final,
                    resp.status if resp else None,
                    None,
                    _ms(start),
                    decision.reason,
                    decision.detail,
                )
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=self._s.browser_settle_ms)
                except Exception:
                    pass
            await page.wait_for_timeout(min(self._s.browser_settle_ms, 4000))
            if scroll:
                last_h = -1
                for _ in range(8):
                    h = await page.evaluate(
                        "() => { window.scrollTo(0, document.body.scrollHeight);"
                        " return document.body.scrollHeight; }"
                    )
                    if h == last_h:
                        break
                    last_h = h
                    await page.wait_for_timeout(700)
                await page.evaluate("() => window.scrollTo(0, 0)")
                await page.wait_for_timeout(300)
            html = await page.content()
            status = resp.status if resp else None
            title = await page.title()
            if status == 403 or _CAPTCHA_RE.search(title or ""):
                code = (
                    ReasonCode.CAPTCHA
                    if re.search(
                        r"captcha|challenge|Just a moment|verify you are human",
                        (title or "") + html[:3000],
                        re.I,
                    )
                    else ReasonCode.HTTP_403
                )
                return RenderResult(url, final, status, html, _ms(start), code, f"blocked: {title}")
            if status == 404:
                return RenderResult(
                    url, final, status, html, _ms(start), ReasonCode.HTTP_404, "not found"
                )
            if status is not None and status >= 500:
                return RenderResult(
                    url, final, status, html, _ms(start), ReasonCode.TIMEOUT, f"http {status}"
                )
            return RenderResult(url, final, status, html, _ms(start), None)
        except Exception as e:
            return RenderResult(
                url,
                None,
                None,
                None,
                _ms(start),
                ReasonCode.TIMEOUT,
                f"{type(e).__name__}: {str(e)[:200]}",
            )
        finally:
            await page.close()

    async def render_paginated(
        self,
        url: str,
        dealer: Dealer,
        *,
        next_selector: str,
        card_selector: str,
        state_selector: str,
        max_pages: int,
    ) -> list[RenderResult]:
        """Render page 1, then advance with the site's own pager (a dispatched click on the
        `rel=next` link; no form submission). Stops when the pager stops advancing or at max_pages.
        The first result carries page 1; subsequent results carry the post-navigation URL."""
        assert self._ctx is not None
        results: list[RenderResult] = []
        start = time.monotonic()
        page = await self._ctx.new_page()
        try:
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=self._s.browser_timeout_s * 1000
            )
            decision = check_url(page.url, dealer)
            if not decision.allowed:
                return [
                    RenderResult(
                        url,
                        page.url,
                        resp.status if resp else None,
                        None,
                        _ms(start),
                        decision.reason,
                        decision.detail,
                    )
                ]
            status = resp.status if resp else None
            title = await page.title()
            if status == 403 or _CAPTCHA_RE.search(title or ""):
                html = await page.content()
                code = (
                    ReasonCode.CAPTCHA
                    if re.search(
                        r"captcha|challenge|Just a moment|verify you are human",
                        (title or "") + html[:3000],
                        re.I,
                    )
                    else ReasonCode.HTTP_403
                )
                return [
                    RenderResult(url, page.url, status, html, _ms(start), code, f"blocked: {title}")
                ]
            if status == 404:
                return [
                    RenderResult(
                        url,
                        page.url,
                        status,
                        await page.content(),
                        _ms(start),
                        ReasonCode.HTTP_404,
                        "not found",
                    )
                ]
            try:
                await page.wait_for_selector(
                    f"{card_selector}, {state_selector}", timeout=self._s.browser_settle_ms * 2
                )
            except Exception:
                pass
            await page.wait_for_timeout(2500)
            results.append(
                RenderResult(url, page.url, status, await page.content(), _ms(start), None)
            )
            js_first = f"() => {{ const c = document.querySelector({card_selector!r}); return c ? (c.getAttribute('data-vehicle-vin') || c.textContent.slice(0, 200)) : ''; }}"
            js_state = f"() => {{ const s = document.querySelector({state_selector!r}); return s ? s.textContent.trim() : ''; }}"
            js_next = (
                f"() => {{ const a = document.querySelector({next_selector!r}); if (!a) return false;"
                " const p = a.closest('.pagination-next, [class*=pagination-next]'); if (p && /disable/.test(p.className)) return false;"
                " a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); return true; }"
            )
            for _ in range(1, max_pages):
                prev_first = await page.evaluate(js_first)
                prev_state = await page.evaluate(js_state)
                page_start = time.monotonic()
                if not await page.evaluate(js_next):
                    break
                advanced = False
                for _tick in range(30):
                    await page.wait_for_timeout(500)
                    if (
                        await page.evaluate(js_first) != prev_first
                        or await page.evaluate(js_state) != prev_state
                    ):
                        advanced = True
                        break
                if not advanced:
                    break
                await page.wait_for_timeout(1500)
                decision = check_url(page.url, dealer)
                if not decision.allowed:
                    results.append(
                        RenderResult(
                            page.url,
                            page.url,
                            None,
                            None,
                            _ms(page_start),
                            decision.reason,
                            decision.detail,
                        )
                    )
                    break
                results.append(
                    RenderResult(
                        page.url, page.url, 200, await page.content(), _ms(page_start), None
                    )
                )
            return results
        except Exception as e:
            results.append(
                RenderResult(
                    url,
                    None,
                    None,
                    None,
                    _ms(start),
                    ReasonCode.TIMEOUT,
                    f"{type(e).__name__}: {str(e)[:200]}",
                )
            )
            return results
        finally:
            await page.close()

    async def fetch_text_via_page(
        self, page_url: str, request_url: str, *, timeout_ms: int | None = None
    ) -> tuple[int | None, str | None, str]:
        """Open `page_url`, then issue one same-origin GET for `request_url` from inside the page
        (the page's own XHR path, requirements 7.4 step 2). Returns (status, body, detail).

        Used for the BMW USA locator, whose edge rejects non-browser TLS clients. Both URLs
        must be https and share a host; the caller is responsible for robots.
        """
        assert self._ctx is not None
        p, r = urlsplit(page_url), urlsplit(request_url)
        if p.scheme != "https" or r.scheme != "https" or p.hostname != r.hostname:
            return None, None, "page and request must be https on the same host"
        page = await self._ctx.new_page()
        try:
            resp = await page.goto(
                page_url,
                wait_until="domcontentloaded",
                timeout=timeout_ms or self._s.browser_timeout_s * 1000,
            )
            if resp is None or resp.status >= 400:
                return (resp.status if resp else None), None, "page load failed"
            res = await page.evaluate(
                "async (u) => { const r = await fetch(u, {method: 'GET', credentials: 'omit'});"
                " return [r.status, await r.text()]; }",
                request_url,
            )
            status, body = int(res[0]), str(res[1])
            return status, (body if status == 200 else None), f"in-page GET {status}"
        except Exception as e:
            return None, None, f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            await page.close()


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
