"""HTTP fetcher: GET/HEAD only, manual redirect hops validated against the allowlist and SSRF
rules, retries only on 429/503/timeout with Retry-After and 30s/90s backoff (requirements 7.4).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from carshop.domain.codes import ReasonCode
from carshop.domain.models import Dealer
from carshop.safety.allowlist import check_url
from carshop.safety.ssrf import SsrfBlocked, resolve_and_check

_MAX_REDIRECTS = 5
_RETRY_STATUSES = {429, 503}


@dataclass(slots=True)
class HttpResult:
    url: str
    final_url: str | None
    status: int | None
    body: str | None
    latency_ms: int
    attempts: int
    error: ReasonCode | None
    detail: str = ""


class HttpFetcher:
    def __init__(
        self, *, user_agent: str, timeout_s: float, backoff_s: tuple[int, int], max_retries: int = 2
    ) -> None:
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_s),
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
            http2=False,
        )
        self._backoff = backoff_s
        self._max_retries = max_retries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self, url: str, dealer: Dealer, *, allow_sticker: bool = False, sleep: bool = True
    ) -> HttpResult:
        start = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            res = await self._single(url, dealer, allow_sticker=allow_sticker)
            retryable = res.error is ReasonCode.TIMEOUT or (res.status in _RETRY_STATUSES)
            if not retryable or attempt > self._max_retries:
                res.latency_ms = int((time.monotonic() - start) * 1000)
                res.attempts = attempt
                if res.status == 429 and res.error is None:
                    res.error = ReasonCode.HTTP_429
                return res
            wait = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
            if res.detail.startswith("retry-after="):
                try:
                    wait = max(wait, int(res.detail.split("=", 1)[1]))
                except ValueError:
                    pass
            if sleep:
                await asyncio.sleep(wait)

    async def _single(self, url: str, dealer: Dealer, *, allow_sticker: bool) -> HttpResult:
        current = url
        start = time.monotonic()
        for _hop in range(_MAX_REDIRECTS + 1):
            decision = check_url(current, dealer, allow_sticker=allow_sticker)
            if not decision.allowed:
                return HttpResult(
                    url, current, None, None, _ms(start), 1, decision.reason, decision.detail
                )
            try:
                resolve_and_check(current)
            except SsrfBlocked as e:
                return HttpResult(
                    url, current, None, None, _ms(start), 1, ReasonCode.SSRF_BLOCKED, str(e)
                )
            try:
                resp = await self._client.get(current)
            except httpx.TimeoutException as e:
                return HttpResult(
                    url, current, None, None, _ms(start), 1, ReasonCode.TIMEOUT, str(e)
                )
            except httpx.HTTPError as e:
                return HttpResult(
                    url, current, None, None, _ms(start), 1, ReasonCode.TIMEOUT, f"transport: {e}"
                )
            if resp.is_redirect and resp.headers.get("location"):
                current = (
                    str(resp.next_request.url)
                    if resp.next_request
                    else httpx.URL(current).join(resp.headers["location"]).__str__()
                )
                continue
            status = resp.status_code
            if status == 403:
                return HttpResult(
                    url, current, status, resp.text, _ms(start), 1, ReasonCode.HTTP_403, "forbidden"
                )
            if status == 404:
                return HttpResult(
                    url, current, status, resp.text, _ms(start), 1, ReasonCode.HTTP_404, "not found"
                )
            if status in _RETRY_STATUSES:
                ra = resp.headers.get("retry-after", "")
                return HttpResult(
                    url,
                    current,
                    status,
                    None,
                    _ms(start),
                    1,
                    None,
                    f"retry-after={ra}" if ra.isdigit() else "retryable",
                )
            if status >= 400:
                return HttpResult(
                    url,
                    current,
                    status,
                    resp.text,
                    _ms(start),
                    1,
                    ReasonCode.PARSER_FAILED,
                    f"http {status}",
                )
            return HttpResult(url, current, status, resp.text, _ms(start), 1, None)
        return HttpResult(
            url, current, None, None, _ms(start), 1, ReasonCode.OFF_DOMAIN, "too many redirects"
        )


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
