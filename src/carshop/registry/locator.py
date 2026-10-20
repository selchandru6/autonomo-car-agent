"""BMW USA dealer locator snapshot (requirements 7.1, F1).

The locator page https://www.bmwusa.com/dealer-locator.html loads its results with one GET to
`/bin/dealerLocatorServlet?getdealerdetailsByRadius/{zip}/{radius}?includeSatelliteDealers=true`.
That path is not under the site's `Disallow: /api` rule (checked every refresh). The BMW USA
edge rejects non-browser TLS clients, so the GET is issued from inside the locator page in the
same Chromium context the pipeline already uses (7.4 step 2: "the inventory JSON request that
the public page itself makes").

A snapshot is the raw JSON of every seed query plus the parsed Texas list, written to
`data/registry/locator-YYYYMMDD.json`. Parsing is a pure function so it is testable offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from carshop.config.settings import Settings
from carshop.fetch.browser import BrowserRenderer
from carshop.safety import robots as robots_mod
from carshop.util import iso, parse_iso, sha256_text, utcnow

log = structlog.get_logger(__name__)

LOCATOR_PAGE = "https://www.bmwusa.com/dealer-locator.html"
LOCATOR_ROBOTS = "https://www.bmwusa.com/robots.txt"
_SERVLET = "https://www.bmwusa.com/bin/dealerLocatorServlet?getdealerdetailsByRadius/{zip}/{radius}?includeSatelliteDealers=true"


@dataclass(frozen=True, slots=True)
class LocatorDealer:
    center_id: str
    ag_code: str | None
    name: str
    city: str
    state: str
    zipcode: str
    website: str | None  # as printed by the locator (may lack a scheme)
    hostname: str | None  # normalized host, None when the locator lists no website
    latitude: float | None
    longitude: float | None
    phone: str | None
    market_name: str | None


@dataclass(slots=True)
class LocatorSnapshot:
    fetched_at: str
    source_urls: list[str]
    sha256: str  # over the concatenated raw bodies
    dealers: list[LocatorDealer]  # Texas, new-vehicle-sales, de-duplicated by center id
    total_seen: int  # every dealer object across every seed query, any state
    storage_uri: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "fetched_at": self.fetched_at,
                "source_urls": self.source_urls,
                "sha256": self.sha256,
                "total_seen": self.total_seen,
                "dealers": [asdict(d) for d in self.dealers],
            },
            indent=1,
        )

    @classmethod
    def from_json(cls, text: str) -> LocatorSnapshot:
        d = json.loads(text)
        return cls(
            fetched_at=d["fetched_at"],
            source_urls=list(d["source_urls"]),
            sha256=d["sha256"],
            dealers=[LocatorDealer(**x) for x in d["dealers"]],
            total_seen=int(d.get("total_seen", 0)),
        )


def normalize_host(website: str | None) -> str | None:
    """`www.cardenasbmw.com` and `https://www.bmwoftyler.com/` both become the bare host."""
    if not website:
        return None
    w = website.strip()
    if not w:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", w, re.I):
        w = "https://" + w
    host = (urlsplit(w).hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None
    return host


def _float(v: object) -> float | None:
    try:
        return float(str(v)) if v not in (None, "") else None
    except ValueError:
        return None


def parse_locator_json(bodies: list[str], state: str = "TX") -> tuple[list[LocatorDealer], int]:
    """Pure parse of one or more `getdealerdetailsByRadius` bodies.

    Keeps dealers that sell new vehicles in `state`, de-duplicated by center id (the first
    occurrence wins, so seed order is deterministic). Returns (dealers, total objects seen).
    """
    out: dict[str, LocatorDealer] = {}
    total = 0
    for body in bodies:
        data = json.loads(body)
        objs: list[dict[str, Any]] = (
            (data.get("dataContent") or {}).get("dealerDetails") or {}
        ).get("dealerDetailsObjects") or []
        for o in objs:
            total += 1
            nvs = o.get("newVehicleSales") or []
            if not nvs:
                continue
            n = nvs[0]
            addr = (n.get("address") or [{}])[0]
            if str(addr.get("state", "")).upper() != state.upper():
                continue
            center = str(o.get("centerID") or n.get("agCode") or "").strip()
            if not center or center in out:
                continue
            website = (n.get("dealerURL") or "").strip() or None
            out[center] = LocatorDealer(
                center_id=center,
                ag_code=(str(n.get("agCode")).strip() or None) if n.get("agCode") else None,
                name=str(n.get("dealerName") or "").strip(),
                city=str(addr.get("city") or "").strip(),
                state=str(addr.get("state") or "").strip().upper(),
                zipcode=str(addr.get("zipcode") or "").strip(),
                website=website,
                hostname=normalize_host(website),
                latitude=_float(n.get("latitude")),
                longitude=_float(n.get("longitude")),
                phone=(str(n.get("phoneNumber")).strip() or None) if n.get("phoneNumber") else None,
                market_name=(str(n.get("marketName")).strip() or None)
                if n.get("marketName")
                else None,
            )
    dealers = sorted(out.values(), key=lambda d: (d.city, d.name, d.center_id))
    return dealers, total


class LocatorBlocked(Exception):
    pass


async def fetch_snapshot(settings: Settings, browser: BrowserRenderer) -> LocatorSnapshot:
    """Fetch robots, then one servlet GET per seed zip, from inside the locator page."""
    status, robots_body, _detail = await browser.fetch_text_via_page(LOCATOR_PAGE, LOCATOR_ROBOTS)
    probe = _SERVLET.format(zip=settings.locator_seed_zips[0], radius=settings.locator_radius_miles)
    decision = robots_mod.evaluate(robots_body, status, probe, settings.http_user_agent)
    if not decision.allowed:
        raise LocatorBlocked(f"bmwusa.com robots disallows the locator servlet: {decision.detail}")
    bodies: list[str] = []
    urls: list[str] = []
    for z in settings.locator_seed_zips:
        url = _SERVLET.format(zip=z, radius=settings.locator_radius_miles)
        st, body, det = await browser.fetch_text_via_page(LOCATOR_PAGE, url)
        urls.append(url)
        if st != 200 or body is None:
            log.warning("locator query failed", zip=z, status=st, detail=det)
            continue
        bodies.append(body)
    if not bodies:
        raise LocatorBlocked("every locator query failed")
    dealers, total = parse_locator_json(bodies, settings.locator_state)
    snap = LocatorSnapshot(
        fetched_at=iso(utcnow()),
        source_urls=urls,
        sha256=sha256_text("".join(bodies)),
        dealers=dealers,
        total_seen=total,
    )
    settings.ensure_dirs()
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    raw_path = settings.registry_dir / f"locator-{stamp}.raw.json"
    raw_path.write_text(json.dumps({"urls": urls, "bodies": bodies}), encoding="utf-8")
    path = settings.registry_dir / f"locator-{stamp}.json"
    snap.storage_uri = str(path)
    path.write_text(snap.to_json(), encoding="utf-8")
    log.info("locator snapshot", dealers=len(dealers), total_seen=total, path=str(path))
    return snap


def latest_snapshot(settings: Settings) -> LocatorSnapshot | None:
    files = sorted(settings.registry_dir.glob("locator-*.json"))
    files = [f for f in files if not f.name.endswith(".raw.json")]
    if not files:
        return None
    snap = LocatorSnapshot.from_json(files[-1].read_text(encoding="utf-8"))
    snap.storage_uri = str(files[-1])
    return snap


def snapshot_age_days(snap: LocatorSnapshot, now: datetime | None = None) -> float:
    now = now or utcnow()
    return (now - parse_iso(snap.fetched_at)).total_seconds() / 86400.0


def snapshot_path_for(settings: Settings, stamp: str) -> Path:
    return settings.registry_dir / f"locator-{stamp}.json"
