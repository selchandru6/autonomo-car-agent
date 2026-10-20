"""Source approval and URL allowlisting (requirements 7.4, F24).

After every redirect the final URL MUST: match the dealer allowlist in punycode, use https,
contain no userinfo, and sit under the dealer's path prefix (or be an allowlisted sticker
host). Fails closed with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from carshop.domain.codes import ReasonCode
from carshop.domain.models import Dealer


@dataclass(frozen=True, slots=True)
class UrlDecision:
    allowed: bool
    reason: ReasonCode | None = None
    detail: str = ""


def _punycode(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return host.lower()


def check_url(url: str, dealer: Dealer, *, allow_sticker: bool = False) -> UrlDecision:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        return UrlDecision(False, ReasonCode.OFF_DOMAIN, f"scheme {parts.scheme!r} is not https")
    if parts.username or parts.password or "@" in parts.netloc:
        return UrlDecision(False, ReasonCode.OFF_DOMAIN, "userinfo in URL")
    host = _punycode(parts.hostname or "")
    if not host:
        return UrlDecision(False, ReasonCode.OFF_DOMAIN, "no host")
    dealer_host = _punycode(dealer.hostname)
    if host == dealer_host:
        path = parts.path or "/"
        # A prefix "/x/" also admits the bare page "/x" (Cardenas 2026-10-20: the start URL
        # itself has no trailing slash).
        if not dealer.path_prefixes or any(
            path.startswith(p) or path == p.rstrip("/") for p in dealer.path_prefixes
        ):
            return UrlDecision(True)
        if path in ("/robots.txt",):
            return UrlDecision(True)
        return UrlDecision(
            False,
            ReasonCode.OFF_DOMAIN,
            f"path {path} outside prefixes {list(dealer.path_prefixes)}",
        )
    if allow_sticker and host in {_punycode(h) for h in dealer.sticker_hosts}:
        return UrlDecision(True)
    return UrlDecision(
        False,
        ReasonCode.OFF_DOMAIN,
        f"host {host} not {dealer_host} or an allowlisted sticker host",
    )
