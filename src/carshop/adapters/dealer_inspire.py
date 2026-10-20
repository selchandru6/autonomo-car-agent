"""Dealer Inspire (WordPress + Algolia) adapter.

Confirmed 2026-10-19 on bmwofarlington.com, bmwofaustin.com, bmwoftyler.com, bmwofelpaso.com.
Cards: div.result-wrap[data-vehicle] with a JSON blob; price stack .price-block > .price-label/.price;
pagination .pagination-state "Page 1 of N" and ?_p=N.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborNode

from carshop.adapters.base import DetailParse, PageParse, clean, parse_int, tree
from carshop.domain.codes import Platform, SourceKind
from carshop.domain.models import RawListing
from carshop.domain.pricing import classify_price_label

_PAGE_STATE_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)
_COUNT_RE = re.compile(r"(\d[\d,]*)\s+(?:New\s+)?Vehicles?\s+for\s+Sale", re.I)
_EMPTY_RE = re.compile(
    r"(no\s+(?:matching\s+)?vehicles?\s+(?:were\s+)?found|0\s+Vehicles?\s+for\s+Sale|we\s+couldn.t\s+find\s+any\s+vehicles)",
    re.I,
)
_STATUS_RE = re.compile(
    r"\b(In[- ]?Transit|In[- ]?Stock|In Production|Arriving Soon|Sale Pending|On Order)\b", re.I
)
_SPEC_RE = re.compile(
    r"^(Interior|Exterior|Mileage|Odometer|Stock|Engine|Transmission|Drivetrain|VIN)\s*:\s*(.+)$",
    re.I,
)


def _price_blocks(node: LexborNode) -> list[tuple[str, str]]:
    """(label, value) pairs from .price-block children.

    When the text label is a disclaimer ("Excl. tax, gov. fees") but the block carries the
    platform's `our-price` class, the label is normalised to "Our Price" so the classifier can
    recognise it as the dealer's advertised figure. The quote keeps the original text.
    """
    pairs: list[tuple[str, str]] = []
    for pb in node.css(".price-block"):
        label_node = pb.css_first(".price-label")
        value_node = pb.css_first(".price")
        label = clean(label_node.text()) if label_node else ""
        value = clean(value_node.text()) if value_node else ""
        if not (label or value):
            continue
        cls = pb.attributes.get("class") or ""
        if "our-price" in cls and classify_price_label(label) is None:
            label = f"Our Price ({label})" if label else "Our Price"
        pairs.append((label, value))
    return pairs


class DealerInspireAdapter:
    platform: Platform = Platform.DEALER_INSPIRE
    version: str = "dealer_inspire-2026.10.1"
    wait_selector: str | None = (
        "div.result-wrap[data-vehicle], .results-pagination, .no-results, .ais-Hits"
    )
    per_page: int = 20
    # ?_p=N alone drops the page's preset model filter (confirmed 2026-10-19), so pagination
    # must go through the site's own pager, which carries the Algolia filter state.
    pagination: str = "in_page"
    next_selector: str = "a[data-testid='pagination-next-link'], .pagination-next a.go-to-page"
    card_selector: str = "div.result-wrap[data-vehicle]"
    state_selector: str = ".pagination-state"

    def page_url(self, start_url: str, page_number: int) -> str:
        parts = urlsplit(start_url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "_p"]
        if page_number > 1:
            q.append(("_p", str(page_number)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))

    def parse_listing_page(self, html: str, url: str, page_hash: str) -> PageParse:
        t = tree(html)
        out = PageParse()
        body_text = clean(t.body.text() if t.body else "")
        cards = [
            c
            for c in t.css("div.result-wrap[data-vehicle]")
            if "inline-banner" not in (c.attributes.get("class") or "")
        ]
        state = t.css_first(".results-pagination .pagination-state, .pagination-state")
        m = _PAGE_STATE_RE.search(clean(state.text())) if state is not None else None
        current_page = int(dict(parse_qsl(urlsplit(url).query)).get("_p", "1") or 1)
        if m:
            out.total_pages = int(m.group(2))
            current_page = int(m.group(1))
        cm = _COUNT_RE.search(body_text)
        if cm:
            out.total_count = parse_int(cm.group(1))
        if not cards:
            if _EMPTY_RE.search(body_text) or (cm and out.total_count == 0):
                out.explicit_empty = True
                return out
            # Rendered page, but neither cards nor an explicit empty: layout we do not know.
            out.layout_ok = bool(
                t.css_first(".results-pagination, .ais-Hits, #inventory-results, .inventory")
            )
            out.detail = "no cards and no explicit empty state"
            return out
        for c in cards:
            try:
                data = json.loads(c.attributes.get("data-vehicle") or "{}")
            except json.JSONDecodeError:
                data = {}
            card_text = clean(c.text())
            vin = (
                data.get("vin") or c.attributes.get("data-vehicle-vin") or ""
            ).strip().upper() or None
            price_pairs = _price_blocks(c)
            if not price_pairs and data.get("price"):
                price_pairs.append(("Price", f"${int(data['price']):,}"))
                if data.get("msrp"):
                    price_pairs.append(("MSRP", f"${int(data['msrp']):,}"))
            detail = next(
                (
                    a.attributes.get("href")
                    for a in c.css("a[href*='/inventory/']")
                    if a.attributes.get("href")
                ),
                None,
            )
            sm = _STATUS_RE.search(card_text)
            status_class = c.css_first(
                "[class*='vehicle-status'], [class*='in-transit'], .hit-badge"
            )
            availability = (
                sm.group(1) if sm else (clean(status_class.text()) if status_class else None)
            )
            cond = data.get("type") or (
                "New" if "new-vehicle" in (c.attributes.get("class") or "") else None
            )
            quotes = {"vin": f"VIN: {vin}" if vin else "", "header": card_text[:160]}
            out.listings.append(
                RawListing(
                    url=url,
                    page_hash=page_hash,
                    source_kind=SourceKind.LISTING,
                    vin=vin,
                    stock=data.get("stock"),
                    year=parse_int(str(data.get("year", ""))),
                    make=data.get("make"),
                    model=data.get("model"),
                    trim=data.get("trim"),
                    condition_text=cond,
                    availability_text=availability,
                    exterior_raw=data.get("exterior_color"),
                    interior_raw=data.get("interior_color"),
                    odometer_miles=parse_int(str(data.get("mileage", "")))
                    if data.get("mileage")
                    else None,
                    price_pairs=price_pairs,
                    full_text=card_text,
                    detail_url=detail,
                    quotes=quotes,
                )
            )
        if out.total_pages and current_page < out.total_pages:
            out.next_url = self.page_url(url, current_page + 1)
        return out

    def parse_detail_page(self, html: str, url: str) -> DetailParse:
        t = tree(html)
        d = DetailParse()
        main = t.css_first("#vdp, .vdp, main, #content, body")
        d.full_text = clean(main.text() if main else (t.body.text() if t.body else ""))
        if not d.full_text or len(d.full_text) < 200:
            d.layout_ok = False
            return d
        for li in t.css("li, .detail-item, .basic-info-item, p"):
            txt = clean(li.text())
            if len(txt) > 120:
                continue
            m = _SPEC_RE.match(txt)
            if not m:
                continue
            key, val = m.group(1).lower(), m.group(2).strip()
            if (
                key == "interior"
                and d.interior_raw is None
                and not val.lower().startswith(("accents", "lock"))
            ):
                d.interior_raw = val
            elif key == "exterior" and d.exterior_raw is None:
                d.exterior_raw = val
            elif key in ("mileage", "odometer") and d.odometer_miles is None:
                d.odometer_miles = parse_int(val)
        stack = t.css_first(".price-stack, .advanced-pricing-stack") or t.body
        if stack is not None:
            d.price_pairs = _price_blocks(stack)
        sm = _STATUS_RE.search(d.full_text[:3000])
        d.availability_text = sm.group(1) if sm else None
        desc = t.css_first(
            ".vehicle-description, #vehicle-description, .description, [class*='comments']"
        )
        pkg_parts: list[str] = []
        if desc:
            pkg_parts.append(clean(desc.text()))
        for h in t.css("h2, h3, h4, strong"):
            if re.search(r"package|option|equipment|feature", clean(h.text()), re.I):
                nxt = h.next
                if nxt is not None:
                    pkg_parts.append(clean(nxt.text())[:2000])
        d.package_text = " ".join(pkg_parts)[:8000] or None
        for a in t.css("a[href]"):
            href = a.attributes.get("href") or ""
            if re.search(r"window.?sticker|monroney", href + clean(a.text()), re.I):
                d.sticker_url = href
                break
        return d
