"""F25: discover each dealer's new-X5 inventory start URL and platform.

From the locator hostname, read the dealer's public homepage and sitemap (GET only, same host)
to find the new-inventory page filtered to X5 where the site allows it, and detect the
platform. Every candidate is verified live before it is accepted. A dealer with no
discoverable inventory page gets a `DiscoveryReason` and becomes `NO_SOURCE`; it stays in the
coverage denominator. An operator override (registry/build.py) takes precedence over all of
this and is never re-discovered.

Only the same host is read. If the locator host's homepage redirects to another host (a dealer
that rebranded: advantagebmwhouston.com -> bmwofhoustonmidtown.com), that redirect target
becomes the registry hostname and the redirect is recorded as evidence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import structlog
from selectolax.lexbor import LexborHTMLParser

from carshop.adapters.detect import adapter_for, detect_platform
from carshop.config.settings import Settings
from carshop.domain.codes import DiscoveryReason, Platform, ReasonCode, TermsStatus
from carshop.domain.models import Dealer, RawListing
from carshop.fetch.browser import BrowserRenderer
from carshop.fetch.http import HttpFetcher
from carshop.fetch.politeness import HostGate
from carshop.safety import robots as robots_mod

log = structlog.get_logger(__name__)

_NEW_PATH_RE = re.compile(
    r"(/new-vehicles?/|/new-inventory/|/inventory/new\b|/searchnew\.aspx|/new-cars?/|/newinventory"
    r"|/new/?$|/inventory\?[^#]*(?:condition|type)=new|/new-[a-z0-9-]+(?:/|\?|$))",
    re.I,
)
_X5_RE = re.compile(r"(?<![a-z0-9])x5(?![a-z0-9])", re.I)
# A link that narrows X5 to one variant or widens it to other models is not the X5 page:
# /new-vehicles/x5-xdrive50e/, ?model=X5%20PHEV&model=330e, _dFR[trim][0]=sDrive40i, ...
_X5_BAD_RE = re.compile(
    r"x5[ _-]?m\b|x5m\b|x5[ _%20-]*(?:xdrive|sdrive|m60|m50|phev|50e|40i|45e)"
    r"|\btrim\b|\[trim\]|specials?|lease|finance|compare|review|\bvs\b|under_1000|demo"
    r"|(?<![a-z0-9])(?:x1|x2|x3|x4|x6|x7|xm|i4|i5|i7|ix|m[2345]|z4|[2345678][- ]?series|330e|750e)(?![a-z0-9])",
    re.I,
)
_USED_RE = re.compile(r"used|pre-owned|preowned|certified|cpo|service|parts|specials", re.I)
_CLEAN_X5_PATH_RE = re.compile(r"/x5/?$", re.I)
_CLEAN_X5_QUERY_RE = re.compile(r"^(?:[a-z_]*model(?:AndTrim)?=X5)$", re.I)
_BENIGN_PARAMS = {"make", "compositetype", "condition", "type", "supermodel", "model",
                  "modelandtrim", "_dfr[make][0]", "_dfr[model][0]", "_dfr[type][0]"}  # fmt: skip
# Year and trim filters hide in-scope cars (Lubbock year=2027, Wichita Falls sDrive40i). `cy`
# is a year only when the value is four digits; a ZIP such as cy=77845 stays (review G4).
_YEAR_VALUE = re.compile(r"(?:19|20)\d{2}")
_SCOPE_KEY = re.compile(r"(?:^|\[)(?:year|trim|gvtrim)(?:\]|$)")
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_BLOCK_TITLE_RE = re.compile(
    r"(Access Denied|Just a moment|Attention Required|captcha|verify you are human)", re.I
)

# Conventional X5-filtered new-inventory URLs by platform (verified live before use).
_CONVENTIONS: dict[Platform, tuple[str, ...]] = {
    Platform.DEALER_INSPIRE: ("/new-vehicles/x5/", "/new-vehicles/?_model=X5"),
    Platform.DEALER_COM: ("/new-inventory/index.htm?model=X5",),
    Platform.CDK: ("/new-inventory/index.htm?model=X5",),
    Platform.DEALERON: ("/searchnew.aspx?Model=X5", "/searchnew.aspx?ModelAndTrim=X5"),
    Platform.DEALERFIRE: ("/new-bmw-x5", "/new-vehicles/?model=X5"),
    Platform.OTHER: ("/inventory/new/bmw/x5", "/inventory/new?model=X5", "/new-vehicles/x5/"),
    Platform.UNKNOWN: ("/new-vehicles/x5/", "/new-inventory/index.htm?model=X5"),
}
# Path prefixes that keep the fetcher on new-car listing and detail pages per platform (7.1).
_PREFIXES: dict[Platform, tuple[str, ...]] = {
    Platform.DEALER_INSPIRE: ("/new-vehicles/", "/inventory/"),
    Platform.DEALER_COM: ("/new-inventory/", "/new/"),
    Platform.CDK: ("/new-inventory/", "/new/", "/inventory/"),
    Platform.DEALERON: ("/searchnew.aspx", "/new-", "/inventory/"),
    Platform.DEALERFIRE: ("/new-", "/vehicle-details/", "/inventory/"),
    Platform.OTHER: ("/inventory/", "/new/", "/viewdetails/new/", "/new-vehicles/"),
    Platform.UNKNOWN: ("/inventory/", "/new-vehicles/", "/new-inventory/", "/new/"),
}


@dataclass(slots=True)
class Discovery:
    hostname: str | None
    start_url: str | None
    path_prefixes: tuple[str, ...]
    platform: Platform
    reason: DiscoveryReason
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    robots_sha256: str | None = None
    redirected_from: str | None = None


@dataclass(slots=True)
class _Page:
    status: int | None
    html: str | None
    final_url: str | None
    error: ReasonCode | None
    renderer: str


def probe_dealer(hostname: str, dealer_id: str = "probe") -> Dealer:
    """A registry-shaped row that allows any https path on `hostname` (discovery only)."""
    return Dealer(
        dealer_id=dealer_id, center_number=None, name=dealer_id, city="", hostname=hostname,
        start_url=None, path_prefixes=(), platform=Platform.UNKNOWN,
        terms_status=TermsStatus.OWNER_ACCEPTED_RISK,
    )  # fmt: skip


def _scopes_inventory(query: str) -> bool:
    """True when the query filters year or trim. Model-only filters are not scope filters."""
    for raw_key, raw_value in parse_qsl(query, keep_blank_values=True):
        key, value = unquote(raw_key), unquote(raw_value)
        low = key.lower()
        if low in {"year", "trim", "gvtrim"} or _SCOPE_KEY.search(low):
            return True
        if low == "cy" and _YEAR_VALUE.fullmatch(value):
            return True
        if low == "modelandtrim" and ":" in value:
            return True
    return False


def strip_inventory_filters(url: str) -> str:
    """Drop year and trim filters. `ModelAndTrim=X5:sDrive40i` becomes `Model=X5`."""
    parts = urlsplit(url)
    kept: list[tuple[str, str]] = []
    for raw_key, raw_value in parse_qsl(parts.query, keep_blank_values=True):
        key, value = unquote(raw_key), unquote(raw_value)
        low = key.lower()
        if low in {"year", "trim", "gvtrim"} or _SCOPE_KEY.search(low):
            continue
        if low == "cy" and _YEAR_VALUE.fullmatch(value):
            continue
        if low == "modelandtrim" and ":" in value:
            kept.append(("Model", value.split(":", 1)[0]))
            continue
        kept.append((key, value))
    query = urlencode(kept, quote_via=quote)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def x5_link_score(url: str, text: str = "") -> int | None:
    """Pure: how well a same-host link names *the* new-X5 page. None = not an X5 page.

    0: clean (`/new-vehicles/x5/`, `?model=X5`); 1: X5 plus benign filters (make, type);
    2: mentions X5 somewhere else (anchor text only, odd params). Variant / multi-model links
    (xDrive50e, PHEV, X3+X5, a year or trim filter, demo/under-1000-miles) are rejected.
    """
    parts = urlsplit(url)
    path_q = parts.path + ("?" + unquote(parts.query) if parts.query else "")
    if not _X5_RE.search(path_q) and not _X5_RE.search(text):
        return None
    if _scopes_inventory(parts.query):
        return None
    if _X5_BAD_RE.search(path_q) or (not _X5_RE.search(path_q) and _X5_BAD_RE.search(text)):
        return None
    if not parts.query and _CLEAN_X5_PATH_RE.search(parts.path):
        return 0
    if parts.query and _CLEAN_X5_QUERY_RE.match(unquote(parts.query)):
        return 0
    if parts.query:
        keys = [unquote(k).lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
        model_vals = [v for k, v in parse_qsl(unquote(parts.query)) if "model" in k.lower()]
        if len(set(model_vals)) > 1:
            return None
        if keys and all(k in _BENIGN_PARAMS for k in keys) and _X5_RE.search(path_q):
            return 1
    return 2


def rank_homepage_links(html: str, base_url: str) -> tuple[list[tuple[int, str]], list[str]]:
    """Pure: ([(score, X5 new-inventory link)...] best first, [unfiltered new-inventory links]),
    same host only, de-duplicated. Used by tests and by `discover`."""
    host = (urlsplit(base_url).hostname or "").lower()
    tree = LexborHTMLParser(html)
    filtered: list[tuple[int, str]] = []
    unfiltered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href).split("#", 1)[0]
        parts = urlsplit(full)
        if parts.scheme != "https" or (parts.hostname or "").lower() != host:
            continue
        path_q = parts.path + ("?" + parts.query if parts.query else "")
        if not _NEW_PATH_RE.search(path_q) or _USED_RE.search(parts.path):
            continue
        cleaned = strip_inventory_filters(full)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        text = re.sub(r"\s+", " ", a.text() or "").strip()
        score = x5_link_score(cleaned, text)
        if score is not None:
            filtered.append((score, cleaned))
        elif not _X5_RE.search(unquote(path_q)) and not _X5_RE.search(text):
            # Plain "New Vehicles"/"New Inventory" landing pages: short paths rank first.
            unfiltered.append((len(urlsplit(cleaned).path) + len(urlsplit(cleaned).query), cleaned))
    filtered.sort(key=lambda t: (t[0], len(t[1])))
    unfiltered.sort()
    return filtered, [u for _, u in unfiltered]


def sitemap_candidates(xml: str, host: str) -> tuple[list[str], list[str]]:
    """Pure: (X5 new-inventory URLs, nested sitemap URLs) from one sitemap body."""
    urls = [u for u in _LOC_RE.findall(xml) if (urlsplit(u).hostname or "").lower() == host]
    nested = [u for u in urls if u.lower().endswith(".xml")]
    x5 = []
    for u in urls:
        cleaned = strip_inventory_filters(u)
        path = urlsplit(cleaned).path
        if (
            _NEW_PATH_RE.search(path)
            and _X5_RE.search(cleaned)
            and not _X5_BAD_RE.search(cleaned)
            and not _scopes_inventory(urlsplit(cleaned).query)
            and not _USED_RE.search(path)
        ):
            x5.append(cleaned)
    return x5, nested


def _homepage_reason(error: ReasonCode | None) -> DiscoveryReason:
    """Map a homepage transport failure to the discovery reason the coverage screen shows."""
    if error is ReasonCode.CONNECT_FAILED:
        return DiscoveryReason.HOMEPAGE_CONNECT_FAILED
    if error is ReasonCode.TLS_FAILED:
        return DiscoveryReason.HOMEPAGE_TLS_FAILED
    if error is ReasonCode.TIMEOUT:
        return DiscoveryReason.HOMEPAGE_TIMEOUT
    return DiscoveryReason.HOMEPAGE_UNREACHABLE


def prefixes_for(platform: Platform, start_url: str) -> tuple[str, ...]:
    base = list(_PREFIXES.get(platform, _PREFIXES[Platform.UNKNOWN]))
    path = urlsplit(start_url).path or "/"
    seg = "/" + path.strip("/").split("/")[0] + "/" if path.strip("/") else None
    if seg and seg != "//" and not any(path.startswith(p) for p in base):
        base.insert(0, seg if not path.endswith(".aspx") else path)
    return tuple(dict.fromkeys(base))


class Discoverer:
    def __init__(
        self,
        settings: Settings,
        http: HttpFetcher,
        browser: BrowserRenderer | None,
        gate: HostGate,
    ) -> None:
        self._s = settings
        self._http = http
        self._browser = browser
        self._gate = gate

    # -- fetch helpers -------------------------------------------------------------------
    async def _get(self, url: str, dealer: Dealer, *, render_if_shell: bool = True) -> _Page:
        async with await self._gate(url):
            h = await self._http.get(url, dealer)
        if h.error in (ReasonCode.OFF_DOMAIN, ReasonCode.SSRF_BLOCKED):
            return _Page(h.status, None, h.final_url, h.error, "http")
        if (
            h.status == 200
            and h.body
            and len(h.body) > 2000
            and not _BLOCK_TITLE_RE.search(h.body[:3000])
        ):
            return _Page(200, h.body, h.final_url, None, "http")
        if self._browser is None or not render_if_shell:
            return _Page(
                h.status, None, h.final_url, h.error or ReasonCode.UNRENDERED_SHELL, "http"
            )
        async with await self._gate(url):
            r = await self._browser.render(url, dealer)
        return _Page(r.status, r.html if r.error is None else None, r.final_url, r.error, "browser")

    # -- main ----------------------------------------------------------------------------
    async def discover(self, hostname: str | None, dealer_id: str = "probe") -> Discovery:
        if not hostname:
            return Discovery(None, None, (), Platform.UNKNOWN, DiscoveryReason.NO_HOSTNAME,
                             "locator lists no website")  # fmt: skip
        host = hostname.lower()
        ev: list[str] = []
        redirected_from: str | None = None
        dealer = probe_dealer(host, dealer_id)

        # 1. Homepage (HTTP, then one render). One cross-host redirect is followed as a rename.
        home = await self._get(f"https://{host}/", dealer)
        ev.append(
            f"GET https://{host}/ -> {home.status} via {home.renderer} {home.error or ''}".strip()
        )
        if home.error is ReasonCode.OFF_DOMAIN and home.final_url:
            new_host = (urlsplit(home.final_url).hostname or "").lower()
            if new_host and new_host != host and urlsplit(home.final_url).scheme == "https":
                redirected_from, host = host, new_host
                dealer = probe_dealer(host, dealer_id)
                home = await self._get(f"https://{host}/", dealer)
                ev.append(f"redirect -> https://{host}/ -> {home.status} via {home.renderer}")
        if home.error in (ReasonCode.HTTP_403, ReasonCode.CAPTCHA):
            return Discovery(host, None, (), Platform.UNKNOWN, DiscoveryReason.HOMEPAGE_BLOCKED,
                             f"homepage {home.error.value}", ev, None, redirected_from)  # fmt: skip
        if home.html is None:
            return Discovery(host, None, (), Platform.UNKNOWN, _homepage_reason(home.error),
                             f"homepage status={home.status} error={home.error}", ev, None, redirected_from)  # fmt: skip
        platform = detect_platform(home.html)
        base_url = f"https://{host}/"

        # 2. robots for this host (the run re-fetches it every day; here it filters candidates).
        async with await self._gate(f"https://{host}/robots.txt"):
            rres = await self._http.get(f"https://{host}/robots.txt", dealer, sleep=False)
        robots_body = rres.body if rres.status == 200 else None
        robots_sha = robots_mod.evaluate(
            robots_body, rres.status, base_url, self._s.http_user_agent
        ).sha256

        def allowed(u: str) -> bool:
            return robots_mod.evaluate(robots_body, rres.status, u, self._s.http_user_agent).allowed

        # 3. Candidates, cleanest first: clean homepage X5 link, platform convention, homepage
        #    X5 link with benign filters, sitemap, weaker homepage mentions, unfiltered new page.
        x5_links, new_links = rank_homepage_links(home.html, base_url)
        candidates: list[tuple[str, DiscoveryReason]] = []

        def add(u: str, reason: DiscoveryReason) -> None:
            if u not in {c for c, _ in candidates}:
                candidates.append((u, reason))

        for score, u in x5_links:
            if score == 0:
                add(u, DiscoveryReason.HOMEPAGE_LINK)
        for conv in _CONVENTIONS.get(platform, ()):
            add(urljoin(base_url, conv), DiscoveryReason.PLATFORM_CONVENTION)
        for score, u in x5_links:
            if score == 1:
                add(u, DiscoveryReason.HOMEPAGE_LINK)
        if not any(sc <= 1 for sc, _ in x5_links):
            for u in await self._sitemap_x5(host, dealer, allowed):
                add(u, DiscoveryReason.SITEMAP)
            for score, u in x5_links:
                if score >= 2:
                    add(u, DiscoveryReason.HOMEPAGE_LINK)
        for u in new_links[:1]:
            add(u, DiscoveryReason.UNFILTERED_NEW)

        robots_blocked = 0
        tried = 0
        for url, reason in candidates:
            if tried >= 4:
                break
            if not allowed(url):
                robots_blocked += 1
                ev.append(f"robots disallows {url}")
                continue
            tried += 1
            page = await self._get(url, dealer)
            ev.append(f"GET {url} -> {page.status} via {page.renderer} {page.error or ''}".strip())
            if page.html is None or page.status != 200:
                continue
            final_host = (urlsplit(page.final_url or url).hostname or "").lower()
            if final_host != host:
                ev.append(f"candidate left host: {page.final_url}")
                continue
            # The inventory page is the authority on the platform (a homepage may carry a
            # different vendor's assets); fall back to the homepage verdict when it is unknown.
            plat = detect_platform(page.html)
            if plat is Platform.UNKNOWN:
                plat = platform
            if not self._looks_like_inventory(page.html, plat, page.final_url or url):
                continue
            start = page.final_url or url
            return Discovery(host, start, prefixes_for(plat, start), plat, reason,
                             f"verified {start}", ev, robots_sha, redirected_from)  # fmt: skip
        if candidates and robots_blocked == len(candidates):
            return Discovery(host, None, (), platform, DiscoveryReason.ROBOTS_DISALLOWED,
                             "robots disallows every inventory candidate", ev, robots_sha, redirected_from)  # fmt: skip
        return Discovery(host, None, (), platform, DiscoveryReason.NO_INVENTORY_LINK,
                         f"{len(candidates)} candidate(s), none verified", ev, robots_sha, redirected_from)  # fmt: skip

    async def _sitemap_x5(self, host: str, dealer: Dealer, allowed: object) -> list[str]:
        assert callable(allowed)
        url = f"https://{host}/sitemap.xml"
        if not allowed(url):
            return []
        async with await self._gate(url):
            r = await self._http.get(url, dealer)
        if r.status != 200 or not r.body:
            return []
        x5, nested = sitemap_candidates(r.body, host)
        for n in nested[:3]:
            if x5:
                break
            if not allowed(n):
                continue
            async with await self._gate(n):
                rn = await self._http.get(n, dealer)
            if rn.status == 200 and rn.body:
                x5.extend(sitemap_candidates(rn.body, host)[0])
        return x5[:2]

    @staticmethod
    def _looks_like_inventory(html: str, platform: Platform, url: str) -> bool:
        """A candidate counts when the platform adapter parses cards that are mostly X5 (an
        "X5" link whose filter the site ignores shows the whole new lot and would cost ten
        extra pages a day), or an explicit empty state, or (no adapter) when the page is a
        real document that talks about X5 inventory."""
        if _BLOCK_TITLE_RE.search(html[:3000]):
            return False
        adapter = adapter_for(platform)
        if adapter is not None:
            parsed = adapter.parse_listing_page(html, url, "discovery")
            if parsed.listings:
                return x5_share(parsed.listings) >= 0.5
            if parsed.explicit_empty:
                return True
        low = html.lower()
        return len(html) > 20_000 and "x5" in low and ("vin" in low or "inventory" in low)


def x5_share(listings: Sequence[RawListing]) -> float:
    """Fraction of parsed cards whose model (or title text) says X5. Unknown models count
    against the candidate: a filtered page should be unambiguous."""
    if not listings:
        return 0.0
    hits = 0
    for li in listings:
        text = f"{li.model or ''} {li.full_text[:200]}".lower()
        if re.search(r"\bx5\b", text):
            hits += 1
    return hits / len(listings)
