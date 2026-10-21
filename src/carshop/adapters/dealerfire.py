"""DealerFire (DealerSocket) adapter.

Confirmed 2026-10-20 on cardenasbmw.com (Cardenas BMW, Harlingen).
Listing data: each `div.inventory-item.js-vehicle-item` holds an `a[data-vin]` CTA whose
attributes describe the unit (data-vin, data-year, data-make, data-model, data-trim,
data-mileage, data-msrp, data-price, data-newusedtype, data-certified, data-stocknumber,
data-imageurl) and a price stack of `.price` rows (`.price_name` / `.price_value`), e.g.
"MSRP $88,820 / Doc Fee + $225 / Cardenas Price $89,045". Labels are passed through as
shown; the domain layer classifies them (7.6).
Detail link: `a[href*='/vehicle-details/']`.
Pagination: `.inventory-pagination` next link / `rel=next`; total from "N Results".
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborNode

from carshop.adapters.base import DetailParse, PageParse, clean, parse_int, tree
from carshop.domain.codes import Platform, SourceKind
from carshop.domain.models import RawListing

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_ANY_VIN_RE = re.compile(r"[/-]([A-HJ-NPR-Z0-9]{17})(?=[/\"'?])")
_NAME_RE = re.compile(
    r"^(New|Used|Certified|Pre-Owned)?\s*(\d{4})\s+(\w+)\s+(X\d|\w+)\s*(.*)$", re.I
)
_TOTAL_RE = re.compile(r"(\d[\d,]*)\s+(?:Results?|Vehicles?\s+Found|Matching\s+Vehicles?)\b", re.I)
_EMPTY_RE = re.compile(
    r"(no\s+vehicles?\s+(?:were\s+)?found|we\s+couldn.t\s+find|0\s+results?|0\s+vehicles?)", re.I
)
_STATUS_RE = re.compile(
    r"\b(In[- ]?Transit|In[- ]?Stock|In Production|Arriving Soon|Sale Pending|On Order)\b", re.I
)
_NEXT_RE = re.compile(r"<(?:a|link)[^>]+rel=[\"']next[\"'][^>]*href=[\"']([^\"']+)", re.I)


def _attr(node: LexborNode, name: str) -> str | None:
    v = node.attributes.get(name)
    v = clean(v) if v else ""
    return None if not v or v.lower() == "null" else v


def _price_rows(node: LexborNode) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in node.css(".price"):
        n = row.css_first(".price_name")
        v = row.css_first(".price_value")
        if n is None or v is None:
            continue
        label, value = clean(n.text()), clean(v.text())
        if label and value and (label, value) not in pairs:
            pairs.append((label, value))
    return pairs


class DealerFireAdapter:
    platform: Platform = Platform.DEALERFIRE
    version: str = "dealerfire-2026.10.1"
    wait_selector: str | None = ".inventory-item.js-vehicle-item .price, .no-results"
    per_page: int = 20
    pagination: str = "url"
    next_selector: str = ".inventory-pagination a[rel='next'], a[rel='next']"
    card_selector: str = ".inventory-item.js-vehicle-item"
    state_selector: str = ".inventory-pagination"

    def page_url(self, start_url: str, page_number: int) -> str:
        return start_url if page_number <= 1 else f"{start_url}?page={page_number}"

    def _listing(self, item: LexborNode, url: str, page_hash: str) -> RawListing | None:
        """The `a[data-vin]` CTA is injected by a third-party widget and is sometimes absent
        when the page is captured (Cardenas 2026-10-20, second render). Fall back to the
        server-rendered card: VIN from the photo path (`/seo/DS…-<VIN>/`), name from the
        title link, prices from the `.price` rows."""
        cta = item.css_first("a[data-vin]")

        def a(name: str) -> str | None:
            return _attr(cta, name) if cta is not None else None

        text = clean(item.text())
        vin = (a("data-vin") or "").upper()
        if not _VIN_RE.match(vin):
            m = _ANY_VIN_RE.search(item.html or "")
            vin = m.group(1).upper() if m else ""
        if not _VIN_RE.match(vin):
            return None
        title_node = item.css_first(".vehicle-title, .inventory-item_vehicle-title, h2, h3")
        name = a("data-name") or (clean(title_node.text()) if title_node is not None else "")
        nm = _NAME_RE.match(name)
        kind = (a("data-newusedtype") or "").lower()
        if not kind:
            kind = (nm.group(1) or "").lower() if nm and nm.group(1) else ""
            if not kind and "/vehicle-details/new-" in (item.html or ""):
                kind = "new"
        certified = (a("data-certified") or "").lower() == "true"
        cond = "Certified" if certified else ("New" if kind == "new" else (kind.title() or None))
        pairs = _price_rows(item)
        msrp = parse_int(a("data-msrp"))
        if msrp and not any(lbl.lower() == "msrp" for lbl, _ in pairs):
            pairs.append(("MSRP", f"${msrp:,}"))
        link = item.css_first("a[href*='/vehicle-details/']")
        detail = (
            urljoin(url, html_mod.unescape(link.attributes.get("href") or "")) if link else None
        )
        img = a("data-imageurl")
        if img is None:
            im = item.css_first("img[src*='cdn-ds.com'], img[data-src*='cdn-ds.com']")
            img = (im.attributes.get("src") or im.attributes.get("data-src")) if im else None
        sm = _STATUS_RE.search(text)
        year_s = a("data-year") or (nm.group(2) if nm else None)
        return RawListing(
            url=url,
            page_hash=page_hash,
            source_kind=SourceKind.LISTING,
            vin=vin,
            stock=a("data-stocknumber"),
            year=int(year_s) if year_s and year_s.isdigit() else None,
            make=a("data-make") or (nm.group(3) if nm else None),
            model=a("data-model") or (nm.group(4) if nm else None),
            trim=a("data-trim") or (clean(nm.group(5)) if nm else None),
            condition_text=cond,
            availability_text=sm.group(1) if sm else None,
            exterior_raw=None,
            interior_raw=None,
            odometer_miles=parse_int(a("data-mileage")),
            price_pairs=pairs,
            full_text=f"{name} {text}".strip(),
            detail_url=detail,
            image_url=("https:" + img) if img and img.startswith("//") else img,
            quotes={"vin": f"VIN: {vin}", "name": name},
        )

    def parse_listing_page(self, html: str, url: str, page_hash: str) -> PageParse:
        out = PageParse()
        t = tree(html)
        body_text = clean(t.body.text() if t.body else "")
        seen: dict[str, RawListing] = {}
        for item in t.css(self.card_selector):
            li = self._listing(item, url, page_hash)
            if li is not None and li.vin and li.vin not in seen:
                seen[li.vin] = li
        out.listings = list(seen.values())
        tm = _TOTAL_RE.search(body_text)
        if tm:
            out.total_count = int(tm.group(1).replace(",", ""))
        if not out.listings:
            if _EMPTY_RE.search(body_text) or out.total_count == 0:
                out.explicit_empty = True
            else:
                out.layout_ok = bool(t.css_first(".inventory-listing, .mod-inventory-listing"))
                out.detail = "no inventory items"
            return out
        pager = t.css_first(self.state_selector)
        nxt: str | None = None
        if pager is not None:
            for a in pager.css("a[href]"):
                if a.attributes.get("rel") == "next" or re.search(
                    r"next|\u203a|\u00bb",
                    clean(a.text()) + (a.attributes.get("aria-label") or ""),
                    re.I,
                ):
                    nxt = urljoin(url, html_mod.unescape(a.attributes.get("href") or ""))
                    break
        if nxt is None:
            m = _NEXT_RE.search(html)
            if m:
                nxt = urljoin(url, html_mod.unescape(m.group(1)))
        if nxt and nxt != url and not nxt.endswith("#"):
            out.next_url = nxt
        if out.total_count is not None:
            # per-page size is whatever the page shows; the next link is the authority.
            out.total_pages = max(1, -(-out.total_count // max(1, len(out.listings))))
        return out

    def parse_detail_page(self, html: str, url: str) -> DetailParse:
        t = tree(html)
        d = DetailParse()
        main = t.css_first("main, #content, .vdp, body")
        d.full_text = clean(main.text() if main else "")
        if len(d.full_text) < 200:
            d.layout_ok = False
            return d
        cta = t.css_first("a[data-vin]")
        if cta is not None:
            d.odometer_miles = parse_int(_attr(cta, "data-mileage"))
        d.price_pairs = _price_rows(t.root) if t.root is not None else []
        ex = re.search(
            r"Exterior(?: Color)?:?\s+([A-Za-z /-]{3,40}?)\s+(?:Interior|Engine|Trans|Drive|VIN|Stock)",
            d.full_text,
        )
        im = re.search(
            r"Interior(?: Color)?:?\s+([A-Za-z /-]{3,40}?)\s+(?:Exterior|Engine|Trans|Drive|VIN|Stock|Body)",
            d.full_text,
        )
        d.exterior_raw = ex.group(1).strip() if ex else None
        d.interior_raw = im.group(1).strip() if im else None
        pk = re.search(
            r"((?:Installed |Factory |Optional )?(?:Packages?|Options?|Equipment|Features).{0,6000}?)"
            r"(?:Disclaimer|Specifications|Dealer Comments|$)",
            d.full_text,
        )
        comments = re.search(
            r"(?:Dealer Comments|Description)\s*(.+?)(?:Disclaimer|Specifications|$)", d.full_text
        )
        d.package_text = (
            " ".join(
                filter(None, [pk.group(1) if pk else None, comments.group(1) if comments else None])
            )[:8000]
            or None
        )
        sm = _STATUS_RE.search(d.full_text)
        d.availability_text = sm.group(1) if sm else None
        for a in t.css("a[href]"):
            href = a.attributes.get("href") or ""
            if re.search(r"window.?sticker|monroney", href + clean(a.text()), re.I):
                d.sticker_url = href
                break
        return d
