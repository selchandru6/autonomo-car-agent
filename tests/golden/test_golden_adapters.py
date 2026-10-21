"""Golden fixtures: adapters + classifier on captured-shape pages, fully offline."""

from __future__ import annotations

import json

import pytest

from carshop.adapters.dealer_com import DealerComAdapter
from carshop.adapters.dealer_inspire import DealerInspireAdapter
from carshop.domain.classify import ClassifyContext, classify
from carshop.domain.codes import ExclusionCode, Flag, Tri
from tests.conftest import GOLDEN

PAGES = GOLDEN / "pages"
EXPECTED = GOLDEN / "expected"


def _classify_all(listings, ctx):  # type: ignore[no-untyped-def]
    out = {"eligible": {}, "excluded": {}}
    for raw in listings:
        c = classify(raw, ctx)
        if c.observation is not None:
            o = c.observation
            out["eligible"][o.vin] = {
                "comparison_price_cents": o.comparison_price_cents,
                "msrp_cents": o.msrp_cents,
                "exterior_code": o.exterior_code,
                "interior_code": o.interior_code,
                "in_transit": o.in_transit,
                "dap": o.dap_status.value,
                "m_sport": o.m_sport,
                "flags": sorted(f.value for f in o.flags),
            }
        else:
            assert c.exclusion is not None
            out["excluded"][c.exclusion.vin or "?"] = c.exclusion.code.value
    return out


def test_dealer_inspire_listing_page(ctx: ClassifyContext) -> None:
    html = (PAGES / "dealer_inspire_page1.html").read_text()
    url = "https://www.bmwoftest.com/new-vehicles/x5/"
    page = DealerInspireAdapter().parse_listing_page(html, url, "h1")
    assert page.layout_ok and not page.explicit_empty
    assert len(page.listings) == 6  # inline banner skipped
    assert page.total_pages == 2 and page.total_count == 6  # not the stray "Page 1 of 44"
    assert page.next_url == url + "?_p=2"
    first = page.listings[0]
    assert first.vin == "5UX23EU03T9529691"
    assert first.detail_url == "/inventory/new-2026-bmw-x5-xdrive40i-5ux23eu03t9529691/"
    assert first.availability_text == "In Stock"
    # The disclaimer label on the our-price block is normalised so it classifies as advertised.
    assert first.price_pairs == [
        ("MSRP", "$79,100"),
        ("Our Price (Excl. tax, gov. fees)", "$79,325"),
    ]

    result = _classify_all(page.listings, ctx)
    expected = json.loads((EXPECTED / "dealer_inspire_page1.json").read_text())
    assert result == expected


def test_dealer_inspire_detail_page() -> None:
    html = (PAGES / "dealer_inspire_vdp.html").read_text()
    d = DealerInspireAdapter().parse_detail_page(html, "https://www.bmwoftest.com/inventory/x/")
    assert d.layout_ok
    assert d.exterior_raw == "Skyscraper Grey Metallic"
    assert d.interior_raw == "Coffee Sensafin"
    assert d.odometer_miles == 6
    assert d.availability_text == "In Stock"
    assert ("Est. monthly payment", "$1,099/mo") in d.price_pairs
    assert d.package_text and "Driving Assistance Professional" in d.package_text
    assert d.sticker_url == "https://sticker.example.com/5UX23EU03T9529691.pdf"


def test_dealer_com_listing_page(ctx: ClassifyContext) -> None:
    html = (PAGES / "dealer_com_page1.html").read_text()
    url = "https://www.bmwofdc.com/new-inventory/index.htm?model=X5"
    page = DealerComAdapter().parse_listing_page(html, url, "h2")
    assert len(page.listings) == 2
    assert page.total_count == 2 and page.total_pages == 1 and page.next_url is None
    x5 = page.listings[0]
    assert x5.vin == "5UX23EU0XT9513990"
    # dt/dd pairing must survive the misaligned "Dealer Discount" row.
    assert x5.price_pairs == [
        ("MSRP", "$80,250"),
        ("Dealer Discount", "-$1,850"),
        ("Selling Price", "$78,400"),
    ]
    result = _classify_all(page.listings, ctx)
    expected = json.loads((EXPECTED / "dealer_com_page1.json").read_text())
    assert result == expected


def test_dealer_com_page_url_pagination() -> None:
    a = DealerComAdapter()
    assert (
        a.page_url("https://h/new-inventory/index.htm?model=X5", 1)
        == "https://h/new-inventory/index.htm?model=X5"
    )
    assert (
        a.page_url("https://h/new-inventory/index.htm?model=X5&start=24", 3)
        == "https://h/new-inventory/index.htm?model=X5&start=48"
    )


@pytest.mark.parametrize("adapter", [DealerInspireAdapter(), DealerComAdapter()])
def test_empty_or_unknown_layout_is_not_zero(adapter) -> None:  # type: ignore[no-untyped-def]
    unknown = adapter.parse_listing_page(
        "<html><body><p>Hello</p></body></html>", "https://h/x", "h"
    )
    assert not unknown.listings and not unknown.explicit_empty and not unknown.layout_ok
    empty = adapter.parse_listing_page(
        "<html><body><div class='inventory'>No vehicles found matching your criteria</div></body></html>",
        "https://h/x",
        "h",
    )
    assert empty.explicit_empty


def test_golden_classification_codes(ctx: ClassifyContext) -> None:
    """Sanity on the fixture semantics independent of the JSON snapshot."""
    html = (PAGES / "dealer_inspire_page1.html").read_text()
    page = DealerInspireAdapter().parse_listing_page(
        html, "https://www.bmwoftest.com/new-vehicles/x5/", "h"
    )
    codes = {
        c.exclusion.vin: c.exclusion.code
        for c in (classify(r, ctx) for r in page.listings)
        if c.exclusion
    }
    assert codes["5UX43EU05T9000011"] is ExclusionCode.POWERTRAIN_OTHER
    assert codes["5UX23EU04V9000012"] is ExclusionCode.NOT_MY2026
    assert codes["5UX23EU01S9000013"] is ExclusionCode.USED_OR_CPO
    assert codes["5UX13EU07T9000014"] is ExclusionCode.NOT_XDRIVE40I
    obs = {
        c.observation.vin: c.observation
        for c in (classify(r, ctx) for r in page.listings)
        if c.observation
    }
    assert obs["5UX23EU05T9472216"].comparison_price_cents is None  # MSRP-only is NULL, not MSRP
    assert Flag.PRICE_MISSING in obs["5UX23EU05T9472216"].flags
    assert obs["5UX23EU05T9472216"].in_transit
    assert obs["5UX23EU03T9529691"].dap_status is Tri.UNKNOWN
