"""DealerOn adapter.

Confirmed 2026-10-20 on garlynsheltonbmwoftemple.com (BMW of Temple).
Listing data: `.vehicle-card[data-vin]` cards carry the unit as data attributes
(data-vin, data-year, data-make, data-model, data-trim, data-extcolor, data-intcolor,
data-msrp, data-price, data-odometer, data-vehicletype, data-cpo, data-stocknum, data-instock,
data-intransit, data-inproduction, data-fueltype, data-name). The page also ships a JSON-LD
ItemList whose items carry the VIN as `identifier`; it is used only to list VINs the
lazily-mounted card grid has not rendered yet (those rows have no price and are completed from
the detail page).
Prices: `data-msrp` is the MSRP; `data-price` is the advertised selling price and is "0" when
the dealer shows MSRP only (MSRP is never substituted, requirements 7.6). The visible pricing
pane is scanned for labelled amounts as a cross-check.
Pagination: `?pt=N`, 12 per page, `<link rel="next">`; total from "Showing all N vehicles" /
"N vehicles".
"""

from __future__ import annotations

import html as html_mod
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborNode

from carshop.adapters.base import DetailParse, PageParse, clean, jsonld_blocks, parse_int, tree
from carshop.domain.codes import Platform, SourceKind
from carshop.domain.models import RawListing

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
# Only the results header counts: a loose "N vehicles" matched "2025 vehicles" in body copy
# (Temple 2026-10-20) and produced 169 expected pages.
_TOTAL_RE = re.compile(
    r"Showing\s+(?:all\s+)?(\d[\d,]*)\s+vehicles?|(\d[\d,]*)\s+(?:vehicles?|results?)\s+(?:found|match|available)",
    re.I,
)
_EMPTY_RE = re.compile(
    r"(no\s+vehicles?\s+(?:were\s+)?found|we\s+couldn.t\s+find|0\s+vehicles?\s+match|"
    r"no\s+results|showing\s+0\s+vehicles)",
    re.I,
)
_NEXT_RE = re.compile(r"<(?:a|link)[^>]+rel=[\"']next[\"'][^>]*href=[\"']([^\"']+)", re.I)
_NAME_RE = re.compile(
    r"^(New|Used|Certified|Pre-Owned)?\s*(\d{4})\s+(\w+)\s+(X\d|\w+)\s*(.*)$", re.I
)
_PANE_PRICE_RE = re.compile(
    r"\b(Sale Price|Selling Price|Internet Price|Our Price|Dealer Price|Your Price|Final Price|"
    r"MSRP|Total Price|Net Price)\b[:\s]*\$\s?([\d,]{4,})",
    re.I,
)
_PANE_PRICE_RE_AFTER = re.compile(
    r"\$\s?([\d,]{4,})\s+(MSRP|Sale Price|Selling Price|Internet Price|Our Price|Dealer Price)\b",
    re.I,
)
_STATUS_RE = re.compile(
    r"\b(In[- ]?Transit|In[- ]?Stock|In Production|Arriving Soon|Sale Pending|On Order)\b", re.I
)


def _attr(node: LexborNode, name: str) -> str | None:
    v = node.attributes.get(name)
    v = clean(v) if v else ""
    return v or None


def _dollars(text: str | None) -> str | None:
    n = parse_int(text)
    return f"${n:,}" if n else None


class DealerOnAdapter:
    platform: Platform = Platform.DEALERON
    version: str = "dealeron-2026.10.1"
    wait_selector: str | None = (
        ".vehicle-card[data-vin], .srp-results-showing, .srp-results-count, .no-results"
    )
    per_page: int = 12
    pagination: str = "url"
    next_selector: str = "a[rel='next'], a[aria-label='Next Page']"
    card_selector: str = ".vehicle-card[data-vin]"
    state_selector: str = ".srp-pagination"

    def page_url(self, start_url: str, page_number: int) -> str:
        parts = urlsplit(start_url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "pt"]
        if page_number > 1:
            q.append(("pt", str(page_number)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))

    @staticmethod
    def _itemlist_vins(html: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for block in jsonld_blocks(html):
            if block.get("@type") != "ItemList":
                continue
            for el in block.get("itemListElement") or []:
                if not isinstance(el, dict):
                    continue
                vin = str(el.get("identifier") or "").strip().upper()
                if _VIN_RE.match(vin):
                    out.append({"vin": vin, "name": el.get("name") or "", "url": el.get("url")})
        return out

    def _card_listing(self, card: LexborNode, url: str, page_hash: str) -> RawListing | None:
        vin = (_attr(card, "data-vin") or "").upper()
        if not _VIN_RE.match(vin):
            return None
        text = clean(card.text())
        name = _attr(card, "data-name") or ""
        year = parse_int(_attr(card, "data-year"))
        vtype = (_attr(card, "data-vehicletype") or "").lower()
        cpo = (_attr(card, "data-cpo") or "").lower() == "true"
        cond = "Certified" if cpo else ("New" if vtype == "new" else (vtype.title() or None))
        price_pairs: list[tuple[str, str]] = []
        msrp = _dollars(_attr(card, "data-msrp"))
        if msrp:
            price_pairs.append(("MSRP", msrp))
        adv = _dollars(_attr(card, "data-price"))
        if adv:
            price_pairs.append(("Selling Price", adv))
        for m in _PANE_PRICE_RE.finditer(text):
            label, amt = m.group(1).title(), f"${int(m.group(2).replace(',', '')):,}"
            if label.lower() in ("final price",):
                continue  # MSRP plus doc fee, not an advertised price
            if (label, amt) not in price_pairs and not any(
                lbl.lower() == label.lower() for lbl, _ in price_pairs
            ):
                price_pairs.append((label, amt))
        flags: list[str] = []
        for key, word in (
            ("data-intransit", "In Transit"),
            ("data-inproduction", "In Production"),
            ("data-instock", "In Stock"),
        ):
            if (_attr(card, key) or "").lower() == "true":
                flags.append(word)
        sm = _STATUS_RE.search(text)
        avail = sm.group(1) if sm else (flags[0] if flags else None)
        link = card.css_first("a[href*='/new-'], a.hero-carousel__item--viewvehicle, a[href]")
        detail_url = (
            urljoin(url, html_mod.unescape(link.attributes.get("href") or "")) if link else None
        )
        img = card.css_first("img[src*='inventoryphotos'], img[data-src], img[src]")
        image = (img.attributes.get("src") or img.attributes.get("data-src")) if img else None
        notes_m = re.search(r"Dealer Comments\s*(.+?)(?:More\.\.\.|Get A Quote|$)", text)
        return RawListing(
            url=url,
            page_hash=page_hash,
            source_kind=SourceKind.LISTING,
            vin=vin,
            stock=_attr(card, "data-stocknum"),
            year=year,
            make=_attr(card, "data-make"),
            model=_attr(card, "data-model"),
            trim=_attr(card, "data-trim"),
            condition_text=cond,
            availability_text=avail,
            exterior_raw=_attr(card, "data-extcolor"),
            interior_raw=_attr(card, "data-intcolor"),
            odometer_miles=parse_int(_attr(card, "data-odometer")),
            price_pairs=price_pairs,
            package_text=notes_m.group(1)[:4000] if notes_m else None,
            full_text=f"{name} {text}".strip(),
            detail_url=detail_url,
            image_url=image if isinstance(image, str) else None,
            quotes={"name": name, "vin": f"VIN: {vin}"},
        )

    def parse_listing_page(self, html: str, url: str, page_hash: str) -> PageParse:
        out = PageParse()
        t = tree(html)
        body_text = clean(t.body.text() if t.body else "")
        seen: dict[str, RawListing] = {}
        for card in t.css(self.card_selector):
            li = self._card_listing(card, url, page_hash)
            if li is not None and li.vin and li.vin not in seen:
                seen[li.vin] = li
        for it in self._itemlist_vins(html):
            if it["vin"] in seen:
                continue
            nm = _NAME_RE.match(it["name"])
            seen[it["vin"]] = RawListing(
                url=url,
                page_hash=page_hash,
                source_kind=SourceKind.JSONLD,
                vin=it["vin"],
                year=int(nm.group(2)) if nm else None,
                make=nm.group(3) if nm else None,
                model=nm.group(4) if nm else None,
                trim=clean(nm.group(5)) if nm else None,
                condition_text=(nm.group(1) if nm and nm.group(1) else None),
                full_text=clean(it["name"]),
                detail_url=it["url"],
                quotes={"name": it["name"], "vin": f"VIN: {it['vin']}"},
            )
        out.listings = list(seen.values())
        tm = _TOTAL_RE.search(body_text)
        if tm:
            out.total_count = int((tm.group(1) or tm.group(2)).replace(",", ""))
            out.total_pages = max(1, -(-out.total_count // self.per_page))
        if not out.listings:
            if _EMPTY_RE.search(body_text) or out.total_count == 0:
                out.explicit_empty = True
            else:
                out.layout_ok = bool(t.css_first(".srp-inventory, #srp-inventory, .inventory_list"))
                out.detail = "no vehicle cards"
            return out
        m = _NEXT_RE.search(html)
        if m and len(out.listings) >= self.per_page:
            nxt = urljoin(url, html_mod.unescape(m.group(1)))
            if nxt != url:
                out.next_url = nxt
        if out.next_url is None and out.total_pages:
            cur = dict(parse_qsl(urlsplit(url).query)).get("pt", "1")
            current_page = int(cur) if cur.isdigit() else 1
            if current_page < out.total_pages:
                out.next_url = self.page_url(url, current_page + 1)
        return out

    def parse_detail_page(self, html: str, url: str) -> DetailParse:
        t = tree(html)
        d = DetailParse()
        main = t.css_first("main, #content, .vdp, body")
        d.full_text = clean(main.text() if main else "")
        if len(d.full_text) < 200:
            d.layout_ok = False
            return d
        root = t.css_first("[data-vin]")
        if root is not None:
            d.exterior_raw = _attr(root, "data-extcolor")
            d.interior_raw = _attr(root, "data-intcolor")
            d.odometer_miles = parse_int(_attr(root, "data-odometer"))
            msrp = _dollars(_attr(root, "data-msrp"))
            adv = _dollars(_attr(root, "data-price"))
            if msrp:
                d.price_pairs.append(("MSRP", msrp))
            if adv:
                d.price_pairs.append(("Selling Price", adv))
        for m in _PANE_PRICE_RE.finditer(d.full_text):
            label, amt = m.group(1).title(), f"${int(m.group(2).replace(',', '')):,}"
            if label.lower() != "final price" and not any(
                lbl.lower() == label.lower() for lbl, _ in d.price_pairs
            ):
                d.price_pairs.append((label, amt))
        for m in _PANE_PRICE_RE_AFTER.finditer(d.full_text):
            label, amt = m.group(2).title(), f"${int(m.group(1).replace(',', '')):,}"
            if not any(lbl.lower() == label.lower() for lbl, _ in d.price_pairs):
                d.price_pairs.append((label, amt))
        im = re.search(
            r"Interior(?: Color)?:?\s+([A-Za-z /-]{3,40}?)\s+(?:Engine|Trans|Drive|Fuel|VIN|Stock|Exterior)",
            d.full_text,
        )
        if im and not d.interior_raw:
            d.interior_raw = im.group(1).strip()
        om = re.search(r"(?:Mileage|Odometer):?\s*([\d,]+)\s*(?:mi|miles)?", d.full_text, re.I)
        if om and d.odometer_miles is None:
            d.odometer_miles = parse_int(om.group(1))
        pk = re.search(
            r"((?:Installed |Factory |Optional )?(?:Packages?|Options?|Equipment).{0,6000}?)"
            r"(?:Dealer Comments|Disclaimer|Specifications|$)",
            d.full_text,
        )
        comments = re.search(r"Dealer Comments\s*(.+?)(?:Disclaimer|More\.\.\.|$)", d.full_text)
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
