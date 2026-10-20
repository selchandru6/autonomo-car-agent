"""Dealer.com (DDC) adapter.

Confirmed 2026-10-19 on bmwofdallas.com and bmwofsanantonio.com.
Listing data: JSON-LD CollectionPage -> about.offers.itemOffered[] (Product/Car).
Prices: dl.pricing-detail dt/dd pairs inside the card that shows "VIN: <vin>".
Pagination: ?start=N, 24 per page; total from "inventory of N" in the CollectionPage description.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborNode

from carshop.adapters.base import DetailParse, PageParse, clean, jsonld_blocks, tree
from carshop.domain.codes import Platform, SourceKind
from carshop.domain.models import RawListing

_NAME_RE = re.compile(
    r"^(New|Used|Certified|Pre-Owned)?\s*(\d{4})\s+(\w+)\s+(X5|X3|X7|\w+)\s*(.*)$", re.I
)
_TOTAL_RE = re.compile(r"inventory of\s+(\d+)", re.I)
_EMPTY_RE = re.compile(
    r"(no\s+vehicles?\s+(?:were\s+)?found|we\s+couldn.t\s+find|0\s+vehicles?\s+match)", re.I
)
_STATUS_RE = re.compile(
    r"\b(In[- ]?Transit|In[- ]?Stock|In Production|Arriving Soon|Sale Pending|On Order)\b", re.I
)


def _dl_pairs(dl: LexborNode) -> list[tuple[str, str]]:
    """Walk <dl> children in document order, pairing each <dt> with the <dd> that follows it."""
    pairs: list[tuple[str, str]] = []
    label: str | None = None
    for child in dl.iter():
        if child.tag == "dt":
            label = clean(child.text())
        elif child.tag == "dd" and label is not None:
            value = clean(child.text())
            if value:
                pairs.append((label, value))
            label = None
    return pairs


class DealerComAdapter:
    platform: Platform = Platform.DEALER_COM
    version: str = "dealer_com-2026.10.1"
    wait_selector: str | None = (
        "dl.pricing-detail, .inventory-results, .no-results, [class*='vehicle-card']"
    )
    per_page: int = 24
    pagination: str = "url"
    next_selector: str = "a[rel='next']"
    card_selector: str = "dl.pricing-detail"
    state_selector: str = ".pagination"

    def page_url(self, start_url: str, page_number: int) -> str:
        parts = urlsplit(start_url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "start"]
        if page_number > 1:
            q.append(("start", str((page_number - 1) * self.per_page)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))

    def _items(self, html: str) -> tuple[list[dict[str, Any]], int | None]:
        total: int | None = None
        items: list[dict[str, Any]] = []
        for block in jsonld_blocks(html):
            if block.get("@type") != "CollectionPage":
                continue
            desc = block.get("description") or ""
            m = _TOTAL_RE.search(desc)
            if m:
                total = int(m.group(1))
            offered = (((block.get("about") or {}).get("offers") or {}).get("itemOffered")) or []
            items.extend(
                x for x in offered if isinstance(x, dict) and x.get("vehicleIdentificationNumber")
            )
        return items, total

    def _card_for_vin(self, t: Any, vin: str) -> LexborNode | None:
        best: LexborNode | None = None
        best_len = 10**9
        for node in t.css("li, div.vehicle-card, article, div.h-100"):
            txt = node.text()
            if vin in txt and "pricing-detail" in (node.html or "")[:20000]:
                ln = len(txt)
                if ln < best_len:
                    best, best_len = node, ln
        return best

    def parse_listing_page(self, html: str, url: str, page_hash: str) -> PageParse:
        out = PageParse()
        items, total = self._items(html)
        out.total_count = total
        t = tree(html)
        body_text = clean(t.body.text() if t.body else "")
        if total is not None:
            out.total_pages = max(1, -(-total // self.per_page))
        if not items:
            if _EMPTY_RE.search(body_text) or total == 0:
                out.explicit_empty = True
            else:
                out.layout_ok = bool(
                    t.css_first(".inventory-results, dl.pricing-detail, [class*='vehicle-card']")
                )
                out.detail = "no JSON-LD items"
            return out
        for it in items:
            vin = str(it["vehicleIdentificationNumber"]).strip().upper()
            name = it.get("name") or ""
            nm = _NAME_RE.match(name)
            cond = (
                nm.group(1)
                if nm and nm.group(1)
                else ("New" if "NewCondition" in str(it.get("itemCondition", "")) else None)
            )
            year = it.get("vehicleModelDate") or (int(nm.group(2)) if nm else None)
            model = it.get("model") or (nm.group(4) if nm else None)
            trim = clean(nm.group(5)) if nm else None
            card = self._card_for_vin(t, vin)
            price_pairs: list[tuple[str, str]] = []
            card_text = ""
            if card is not None:
                card_text = clean(card.text())
                dl = card.css_first("dl.pricing-detail")
                if dl is not None:
                    price_pairs = _dl_pairs(dl)
            if not price_pairs:
                offer = it.get("offers") or {}
                if offer.get("price"):
                    # JSON-LD price is the selling price but unlabelled; treat as advertised.
                    price_pairs.append(("Price", f"${int(float(offer['price'])):,}"))
            mileage = (it.get("mileageFromOdometer") or {}).get("value")
            sm = _STATUS_RE.search(card_text)
            avail = (
                sm.group(1)
                if sm
                else (
                    "InStock"
                    if "InStock" in str((it.get("offers") or {}).get("availability", ""))
                    else None
                )
            )
            notes_m = re.search(
                r"Dealer Notes:\s*(.+?)(?:Get pre-approved|Get More Information|$)", card_text
            )
            out.listings.append(
                RawListing(
                    url=url,
                    page_hash=page_hash,
                    source_kind=SourceKind.JSONLD,
                    vin=vin,
                    stock=it.get("sku"),
                    year=int(year) if year else None,
                    make=(it.get("brand") or {}).get("name")
                    if isinstance(it.get("brand"), dict)
                    else None,
                    model=model,
                    trim=trim,
                    condition_text=cond,
                    availability_text=avail,
                    exterior_raw=it.get("color"),
                    interior_raw=it.get("vehicleInteriorColor"),
                    odometer_miles=int(mileage) if isinstance(mileage, int | float) else None,
                    price_pairs=price_pairs,
                    package_text=notes_m.group(1) if notes_m else None,
                    full_text=card_text or clean(name),
                    detail_url=it.get("url") or (it.get("offers") or {}).get("url"),
                    image_url=it.get("image") if isinstance(it.get("image"), str) else None,
                    quotes={"name": name, "vin": f"VIN: {vin}"},
                )
            )
        start = dict(parse_qsl(urlsplit(url).query)).get("start", "0")
        current_page = int(start) // self.per_page + 1 if start.isdigit() else 1
        if out.total_pages and current_page < out.total_pages:
            out.next_url = self.page_url(url, current_page + 1)
        return out

    def parse_detail_page(self, html: str, url: str) -> DetailParse:
        t = tree(html)
        d = DetailParse()
        d.full_text = clean(t.body.text() if t.body else "")
        if len(d.full_text) < 200:
            d.layout_ok = False
            return d
        for block in jsonld_blocks(html):
            types = block.get("@type")
            tl = types if isinstance(types, list) else [types]
            if "Car" in tl or "Vehicle" in tl:
                d.interior_raw = d.interior_raw or block.get("vehicleInteriorColor")
                d.exterior_raw = d.exterior_raw or block.get("color")
                mv = (block.get("mileageFromOdometer") or {}).get("value")
                if isinstance(mv, int | float):
                    d.odometer_miles = int(mv)
        m = re.search(
            r"Interior Color\s+([A-Za-z /-]+?)\s+(?:Body|Seats|Engine|Fuel|Transmission|Drivetrain|VIN)",
            d.full_text,
        )
        if m and not d.interior_raw:
            d.interior_raw = m.group(1).strip()
        dl = t.css_first("dl.pricing-detail")
        if dl is not None:
            d.price_pairs = _dl_pairs(dl)
        pk = re.search(
            r"(Included Packages & Accessories.+?)(?:Highlighted Features|Specifications|Dealer Notes|$)",
            d.full_text,
        )
        notes = re.search(
            r"Dealer Notes:?\s*(.+?)(?:Get pre-approved|Get More Information|Highlighted|$)",
            d.full_text,
        )
        d.package_text = (
            " ".join(
                filter(None, [pk.group(1) if pk else None, notes.group(1) if notes else None])
            )[:8000]
            or None
        )
        sm = _STATUS_RE.search(d.full_text[:4000])
        d.availability_text = sm.group(1) if sm else None
        for a in t.css("a[href]"):
            href = a.attributes.get("href") or ""
            if re.search(
                r"window.?sticker|monroney", href + clean(a.text()), re.I
            ) and not href.endswith(".css"):
                d.sticker_url = href
                break
        return d
