"""robots.txt handling (requirements 7.4). Re-fetched every run; hash stored."""

from __future__ import annotations

from dataclasses import dataclass

from protego import Protego

from carshop.util import sha256_text


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    sha256: str | None
    detail: str


def evaluate(
    robots_body: str | None, http_status: int | None, url: str, user_agent: str
) -> RobotsDecision:
    """Decide whether `url` may be fetched.

    Missing (404) or unreadable robots is treated as allow-all, per RFC 9309. A 5xx or a
    network failure is treated as allow (RFC 9309 permits either; the fetch itself will then
    record its own outcome). An explicit Disallow matching the path fails closed.
    """
    if robots_body is None or http_status is None or http_status >= 400:
        return RobotsDecision(True, None, f"robots unavailable (status={http_status}); allow-all")
    rp = Protego.parse(robots_body)
    allowed = bool(rp.can_fetch(url, user_agent)) and bool(rp.can_fetch(url, "*"))
    return RobotsDecision(
        allowed, sha256_text(robots_body), "allowed" if allowed else f"Disallow covers {url}"
    )
