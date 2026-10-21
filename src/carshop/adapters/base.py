"""Adapter protocol. Known platforms are configuration; a new platform is code plus fixtures."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from selectolax.lexbor import LexborHTMLParser

from carshop.domain.codes import Platform
from carshop.domain.models import RawListing

_WS = re.compile(r"\s+")
_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def jsonld_blocks(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _LD_RE.finditer(html):
        try:
            d = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(d, list):
            out.extend(x for x in d if isinstance(x, dict))
        elif isinstance(d, dict):
            out.append(d)
    return out


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else None


@dataclass(slots=True)
class PageParse:
    listings: list[RawListing] = field(default_factory=list)
    total_pages: int | None = None
    total_count: int | None = None
    next_url: str | None = None
    explicit_empty: bool = False
    layout_ok: bool = True
    detail: str = ""


@dataclass(slots=True)
class DetailParse:
    interior_raw: str | None = None
    exterior_raw: str | None = None
    odometer_miles: int | None = None
    availability_text: str | None = None
    package_text: str | None = None
    price_pairs: list[tuple[str, str]] = field(default_factory=list)
    sticker_url: str | None = None
    full_text: str = ""
    conflicts: list[str] = field(default_factory=list)
    layout_ok: bool = True


class Adapter(Protocol):
    platform: Platform
    version: str
    wait_selector: str | None
    per_page: int
    # "url": page N has its own URL (page_url). "in_page": the site's pager must be used; the
    # renderer dispatches a click on next_selector and waits for card_selector to change.
    pagination: str
    next_selector: str
    card_selector: str
    state_selector: str

    def page_url(self, start_url: str, page_number: int) -> str: ...
    def parse_listing_page(self, html: str, url: str, page_hash: str) -> PageParse: ...
    def parse_detail_page(self, html: str, url: str) -> DetailParse: ...


def tree(html: str) -> LexborHTMLParser:
    return LexborHTMLParser(html)
