"""Generic adapter for `other` / `unknown` / not-yet-adapted platforms (requirements 7.1, 7.4).

Order for an unknown platform is: JSON-LD already in the page, then the page's own inventory
JSON (adapter configuration, none here), then one browser render. This adapter reads
schema.org `Vehicle` / `Car` / `Product` objects that carry a `vehicleIdentificationNumber`,
wherever they sit (top level, `@graph`, `ItemList.itemListElement[].item`,
`CollectionPage.about.offers.itemOffered[]`). Pagination follows `rel=next` only.

Confirmed 2026-10-20 on bmwpb.com (Sewell group platform): one `Vehicle` block per card with
url, model, vehicleConfiguration (trim), vehicleModelDate, itemCondition and offers.price.
A page with no vehicles and no explicit empty state is `layout_ok=False` -> LAYOUT_UNKNOWN,
which is the honest signal that a real adapter is needed (section 7.1).
"""

from __future__ import annotations

import html as html_mod
import re
from typing import Any
from urllib.parse import urljoin

from carshop.adapters.base import DetailParse, PageParse, clean, jsonld_blocks, parse_int, tree
from carshop.domain.codes import Platform, SourceKind
from carshop.domain.models import RawListing

_VEHICLE_TYPES = {"Vehicle", "Car", "Product", "MotorizedVehicle"}
_EMPTY_RE = re.compile(
    r"(no\s+vehicles?\s+(?:were\s+)?found|we\s+couldn.t\s+find|0\s+(?:vehicles?|results?)\s+(?:match|found)|no\s+results)",
    re.I,
)
_STATUS_RE = re.compile(
    r"\b(In[- ]?Transit|In[- ]?Stock|In Production|Arriving Soon|Sale Pending|On Order)\b", re.I
)
_NEXT_RE = re.compile(r"<(?:a|link)[^>]+rel=[\"']next[\"'][^>]*href=[\"']([^\"']+)", re.I)
_NEXT_RE2 = re.compile(r"<(?:a|link)[^>]+href=[\"']([^\"']+)[\"'][^>]*rel=[\"']next[\"']", re.I)
_VIN_RE = re.compile(r"\b5UX[A-HJ-NPR-Z0-9]{14}\b")


def _types(d: dict[str, Any]) -> set[str]:
    t = d.get("@type")
    if isinstance(t, list):
        return {str(x) for x in t}
    return {str(t)} if t else set()


def _walk(obj: Any, out: list[dict[str, Any]], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(obj, dict):
        if obj.get("vehicleIdentificationNumber") and (_types(obj) & _VEHICLE_TYPES or depth):
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out, depth + 1)


def vehicles_in(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in jsonld_blocks(html):
        _walk(block, out)
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for v in out:
        vin = str(v.get("vehicleIdentificationNumber", "")).strip().upper()
        if vin and vin not in seen:
            seen.add(vin)
            uniq.append(v)
    return uniq


def _s(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return _s(v.get("name") or v.get("value") or v.get("@id"))
    s = clean(str(v))
    return s or None


def _offers(v: dict[str, Any]) -> list[tuple[str, str]]:
    offers = v.get("offers")
    items = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
    pairs: list[tuple[str, str]] = []
    for o in items:
        if not isinstance(o, dict):
            continue
        price = o.get("price") or (o.get("priceSpecification") or {}).get("price")
        if price not in (None, "", 0, "0"):
            try:
                pairs.append(("Price", f"${int(float(str(price).replace(',', ''))):,}"))
            except ValueError:
                pairs.append(("Price", str(price)))
    return pairs


class GenericJsonLdAdapter:
    platform: Platform = Platform.OTHER
    version: str = "generic_jsonld-2026.10.1"
    wait_selector: str | None = "script[type='application/ld+json']"
    per_page: int = 0
    pagination: str = "url"
    next_selector: str = "a[rel='next']"
    card_selector: str = "[data-vin]"
    state_selector: str = ".pagination"

    def page_url(self, start_url: str, page_number: int) -> str:
        return start_url

    def parse_listing_page(self, html: str, url: str, page_hash: str) -> PageParse:
        out = PageParse()
        vehicles = vehicles_in(html)
        t = tree(html)
        body_text = clean(t.body.text() if t.body else "")
        if not vehicles:
            if _EMPTY_RE.search(body_text):
                out.explicit_empty = True
                return out
            out.layout_ok = False
            out.detail = "no schema.org vehicles in JSON-LD and no explicit empty state"
            return out
        for v in vehicles:
            vin = str(v["vehicleIdentificationNumber"]).strip().upper()
            cond_raw = _s(v.get("itemCondition")) or ""
            cond = (
                "New"
                if "new" in cond_raw.lower()
                else "Used"
                if "used" in cond_raw.lower()
                else None
            )
            name = _s(v.get("name")) or ""
            trim = _s(v.get("vehicleConfiguration")) or _s(v.get("trim"))
            desc = _s(v.get("description")) or ""
            year = parse_int(_s(v.get("vehicleModelDate")) or _s(v.get("modelDate")) or "")
            if year is None:
                ym = re.search(r"\b(20\d{2})\b", name)
                year = int(ym.group(1)) if ym else None
            odo = v.get("mileageFromOdometer")
            odo_val = parse_int(_s(odo)) if odo else None
            full_text = " ".join(filter(None, [cond, name, trim, desc]))
            sm = _STATUS_RE.search(full_text)
            out.listings.append(
                RawListing(
                    url=url,
                    page_hash=page_hash,
                    source_kind=SourceKind.JSONLD,
                    vin=vin,
                    stock=_s(v.get("sku")),
                    year=year,
                    make=_s(v.get("brand")) or _s(v.get("manufacturer")),
                    model=_s(v.get("model")),
                    trim=trim,
                    condition_text=cond,
                    availability_text=sm.group(1) if sm else None,
                    exterior_raw=_s(v.get("color")),
                    interior_raw=_s(v.get("vehicleInteriorColor")),
                    odometer_miles=odo_val,
                    price_pairs=_offers(v),
                    full_text=full_text,
                    detail_url=_s(v.get("url")),
                    quotes={"vin": f"VIN: {vin}", "header": name[:160]},
                    image_url=_s(v.get("image")),
                )
            )
        out.total_count = len(out.listings)
        m = _NEXT_RE.search(html) or _NEXT_RE2.search(html)
        if m:
            # hrefs are HTML-escaped (`&amp;`) and may be relative; a next link that points
            # back at the page we are on is not a next page.
            nxt = urljoin(url, html_mod.unescape(m.group(1)))
            out.next_url = nxt if nxt != url else None
        return out

    def parse_detail_page(self, html: str, url: str) -> DetailParse:
        t = tree(html)
        d = DetailParse()
        main = t.css_first("main, #content, body")
        d.full_text = clean(main.text() if main else "")
        if len(d.full_text) < 200 and not _VIN_RE.search(html):
            d.layout_ok = False
            return d
        for v in vehicles_in(html):
            d.price_pairs.extend(_offers(v))
            d.exterior_raw = d.exterior_raw or _s(v.get("color"))
            d.interior_raw = d.interior_raw or _s(v.get("vehicleInteriorColor"))
            odo = v.get("mileageFromOdometer")
            if odo and d.odometer_miles is None:
                d.odometer_miles = parse_int(_s(odo))
            desc = _s(v.get("description"))
            if desc:
                d.package_text = desc[:8000]
        sm = _STATUS_RE.search(d.full_text[:3000])
        d.availability_text = sm.group(1) if sm else None
        for a in t.css("a[href]"):
            href = a.attributes.get("href") or ""
            if re.search(r"window.?sticker|monroney", href + clean(a.text()), re.I):
                d.sticker_url = href
                break
        return d
