"""Registry v1.3: locator parsing, discovery ranking, registry composition (offline)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from carshop.adapters.detect import adapter_for, detect_platform
from carshop.adapters.generic_jsonld import GenericJsonLdAdapter
from carshop.config.loaders import Registry
from carshop.domain.codes import DiscoveryReason, Platform, TermsStatus
from carshop.domain.models import Dealer
from carshop.registry.build import Override, compose, slug_for
from carshop.registry.discovery import (
    Discovery,
    prefixes_for,
    rank_homepage_links,
    sitemap_candidates,
    x5_link_score,
)
from carshop.registry.locator import LocatorSnapshot, normalize_host, parse_locator_json

PAGES = Path(__file__).resolve().parents[1] / "golden" / "pages"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------------------- locator
def test_locator_parse_keeps_texas_new_vehicle_dealers_deduplicated() -> None:
    body = (PAGES / "locator_tx_sample.json").read_text()
    dealers, total = parse_locator_json([body], "TX")
    assert total == 5  # 4 distinct + 1 duplicate object
    names = [d.name for d in dealers]
    assert names == ["Cardenas BMW", "BMW of Plano", "BMW of Tyler"]  # sorted by city
    assert "Jackie Cooper Imports" not in names  # Oklahoma
    by = {d.name: d for d in dealers}
    assert by["BMW of Tyler"].hostname == "www.bmwoftyler.com"  # scheme and slash stripped
    assert by["Cardenas BMW"].hostname == "www.cardenasbmw.com"  # bare host accepted
    assert by["BMW of Plano"].hostname is None and by["BMW of Plano"].website is None
    assert by["BMW of Plano"].center_id == "50923"


@pytest.mark.parametrize(
    ("raw", "host"),
    [
        ("https://www.bmwoftyler.com/", "www.bmwoftyler.com"),
        ("www.cardenasbmw.com", "www.cardenasbmw.com"),
        ("  ", None),
        ("", None),
        (None, None),
        ("not a host", None),
        ("HTTP://Www.Example.COM/path?x=1", "www.example.com"),
    ],
)
def test_normalize_host(raw: str | None, host: str | None) -> None:
    assert normalize_host(raw) == host


# ----------------------------------------------------------------------------- discovery
def test_x5_link_scoring_rejects_variants_and_prefers_clean_pages() -> None:
    assert x5_link_score("https://h/new-vehicles/x5/") == 0
    assert x5_link_score("https://h/new-inventory/index.htm?model=X5") == 0
    assert x5_link_score("https://h/searchnew.aspx?ModelAndTrim=X5") == 0
    assert x5_link_score("https://h/new-inventory/index.htm?year=2026&make=BMW&model=X5") is None
    assert x5_link_score("https://h/new-vehicles/x5-xdrive50e/") is None
    assert x5_link_score("https://h/new-vehicles/x5m/") is None
    assert x5_link_score("https://h/new-inventory/index.htm?model=X5%20PHEV&model=330e") is None
    assert x5_link_score("https://h/new-vehicles/?_dFR[model][0]=X5&_dFR[model][1]=X3") is None
    assert (
        x5_link_score("https://h/new-vehicles/?_dFR[model][0]=X5&_dFR[trim][0]=sDrive40i") is None
    )
    assert x5_link_score("https://h/new-vehicles/x3/") is None
    assert x5_link_score("https://h/new-vehicles/", "X5") == 2  # anchor text only


def test_homepage_ranking_same_host_clean_first() -> None:
    html = (PAGES / "homepage_dealer_inspire.html").read_text()
    x5, new = rank_homepage_links(html, "https://www.bmwoftest.com/")
    assert x5[0] == (0, "https://www.bmwoftest.com/new-vehicles/x5/")
    urls = [u for _, u in x5]
    assert "https://www.bmwoftest.com/new-vehicles/x5-xdrive50e/" not in urls
    assert "https://www.bmwoftest.com/new-vehicles/x5m/" not in urls
    assert "https://www.bmwoftest.com/used-vehicles/x5/" not in urls
    assert not any("sister-brand" in u for u in urls)
    assert not any("specials" in u for u in urls)
    assert new[0] == "https://www.bmwoftest.com/new-vehicles/"
    assert detect_platform(html) is Platform.DEALER_INSPIRE


def test_sitemap_candidates_same_host_x5_new_only() -> None:
    xml = """<urlset><url><loc>https://www.d.com/new-vehicles/x5/</loc></url>
    <url><loc>https://www.d.com/used-vehicles/x5/</loc></url>
    <url><loc>https://www.other.com/new-vehicles/x5/</loc></url>
    <url><loc>https://www.d.com/new-vehicles/x5-xdrive50e/</loc></url>
    <sitemap><loc>https://www.d.com/sitemap-inventory.xml</loc></sitemap></urlset>"""
    x5, nested = sitemap_candidates(xml, "www.d.com")
    assert x5 == ["https://www.d.com/new-vehicles/x5/"]
    assert nested == ["https://www.d.com/sitemap-inventory.xml"]


def test_prefixes_keep_fetcher_on_new_car_paths() -> None:
    assert prefixes_for(Platform.DEALER_INSPIRE, "https://h/new-vehicles/x5/") == (
        "/new-vehicles/",
        "/inventory/",
    )
    assert "/new-bmw/" in prefixes_for(Platform.DEALER_COM, "https://h/new-bmw/west.htm?model=X5")
    assert all(
        not p.startswith("/used")
        for p in prefixes_for(Platform.OTHER, "https://h/inventory/new/bmw/x5")
    )


def test_platform_detection_prefers_inventory_markup_over_theme_assets() -> None:
    dealeron = "<html><script src='/dealeron/x.js'></script><a href='/searchnew.aspx'>New</a> dealerinspire-font</html>"
    assert detect_platform(dealeron) is Platform.DEALERON
    assert (
        detect_platform("<div class='result-wrap' data-vehicle='{}'></div>")
        is Platform.DEALER_INSPIRE
    )
    assert detect_platform("<dl class='pricing-detail'></dl>") is Platform.DEALER_COM
    assert (
        detect_platform("<html>viewdetails/new/5ux… <a href='/viewdetails/new/x'>v</a></html>")
        is Platform.OTHER
    )
    assert detect_platform("<html>plain</html>") is Platform.UNKNOWN


# ----------------------------------------------------------------------------- generic adapter
def test_generic_jsonld_adapter_reads_vehicle_blocks_and_item_lists() -> None:
    html = (PAGES / "generic_jsonld_page1.html").read_text()
    adapter = GenericJsonLdAdapter()
    page = adapter.parse_listing_page(
        html, "https://www.groupstore-test.com/inventory/new/bmw/x5", "h"
    )
    vins = sorted(r.vin or "" for r in page.listings)
    assert vins == ["5UX13EU07T9000014", "5UX23EU03T9529691", "5UX43EU05T9000011"]
    first = next(r for r in page.listings if r.vin == "5UX23EU03T9529691")
    assert first.year == 2026 and first.model == "X5" and first.trim == "xDrive40i"
    assert first.condition_text == "New"
    assert first.exterior_raw == "Skyscraper Grey Metallic"
    assert first.price_pairs == [("Price", "$78,450")]
    assert "Driving Assistance Professional" in first.full_text
    assert first.detail_url and first.detail_url.endswith("/2026-bmw-x5-sport-utility")
    assert page.next_url == "https://www.groupstore-test.com/inventory/new/bmw/x5?page=2"
    assert adapter_for(Platform.OTHER) is not None and adapter_for(Platform.DEALERON) is not None


def test_dealeron_adapter_reads_card_attributes_itemlist_and_pager() -> None:
    """BMW of Temple 2026-10-20: cards carry the unit as data-* attributes; the JSON-LD
    ItemList lists VINs the lazy grid has not mounted; `data-price="0"` means MSRP only."""
    from carshop.adapters.dealeron import DealerOnAdapter

    html = (PAGES / "dealeron_page1.html").read_text()
    assert detect_platform(html) is Platform.DEALERON
    adapter = DealerOnAdapter()
    assert adapter_for(Platform.DEALERON) is not None
    url = "https://www.dealeron-test.com/new-vehicles/x5/"
    page = adapter.parse_listing_page(html, url, "h")
    assert page.layout_ok and not page.explicit_empty
    assert page.total_count == 15 and page.total_pages == 2
    assert page.next_url == "https://www.dealeron-test.com/new-vehicles/x5/?pt=2"
    assert adapter.page_url(url, 3) == url + "?pt=3" and adapter.page_url(url + "?pt=3", 1) == url
    vins = [li.vin for li in page.listings]
    assert vins == ["5UX23EU05T9488934", "5UX43EU07T9328353", "5UX13EU03T9508884"]  # deduped
    a, b, c = page.listings
    assert (a.year, a.model, a.trim, a.condition_text) == (2026, "X5", "xDrive40i", "New")
    assert a.odometer_miles == 12 and a.stock == "43890" and a.availability_text == "In Stock"
    assert a.exterior_raw == "Carbon Black Metallic" and a.interior_raw == "Silverstone Sensafin"
    assert ("MSRP", "$81,025") in a.price_pairs and ("Selling Price", "$79,995") in a.price_pairs
    assert not any(
        lbl == "Final Price" for lbl, _ in a.price_pairs
    )  # MSRP + doc fee is not a price
    assert (
        a.detail_url
        == "https://www.dealeron-test.com/new-Temple-2026-BMW-X5-xDrive40i-5UX23EU05T9488934"
    )
    assert a.package_text and "Driving Assistance Professional" in a.package_text
    assert b.price_pairs == [("MSRP", "$86,575")]  # data-price="0": no advertised price
    assert b.odometer_miles == 4413
    assert c.source_kind.value == "jsonld" and c.trim == "sDrive40i" and c.price_pairs == []
    empty = adapter.parse_listing_page(
        "<html><body><div class='srp-results-showing'>Showing 0 vehicles</div>"
        "<div id='srp-inventory'></div></body></html>",
        url,
        "h",
    )
    assert empty.explicit_empty
    shell = adapter.parse_listing_page("<html><body><p>loading</p></body></html>", url, "h")
    assert not shell.listings and not shell.layout_ok


def test_dealerfire_adapter_reads_cta_attributes_and_price_rows() -> None:
    """Cardenas BMW 2026-10-20: units live on the `a[data-vin]` CTA; the price stack is
    `.price_name`/`.price_value` rows passed through verbatim for the domain layer."""
    from carshop.adapters.dealerfire import DealerFireAdapter

    html = (PAGES / "dealerfire_page1.html").read_text()
    assert detect_platform(html) is Platform.DEALERFIRE
    page = DealerFireAdapter().parse_listing_page(
        html, "https://www.cardenasbmw.com/new-bmw-x5-harlingen-tx", "h"
    )
    assert page.layout_ok and page.total_count == 2 and page.next_url is None
    a, b = page.listings
    assert (a.vin, a.year, a.model, a.trim, a.condition_text) == (
        "5UX23EU05T9488934",
        2026,
        "X5",
        "xDrive40i",
        "New",
    )
    assert a.odometer_miles == 7 and a.stock == "BW26101" and a.availability_text == "In Stock"
    assert a.price_pairs == [
        ("MSRP", "$78,100"), ("Doc Fee", "+ $225"), ("Cardenas Price", "$76,325")
    ]  # fmt: skip
    assert a.detail_url and a.detail_url.endswith(
        "/vehicle-details/new-2026-bmw-x5-xdrive40i-brownsville-tx-id-65097881"
    )
    assert a.image_url == "https://cdn-ds.com/stock/x/1.jpg"
    assert b.year == 2027 and b.price_pairs == [("MSRP", "$88,820")]
    shell = DealerFireAdapter().parse_listing_page(
        "<html><body>x</body></html>", "https://h/x", "h"
    )
    assert not shell.listings and not shell.layout_ok
    # The CTA is a late-injected widget: without it the VIN comes from the photo path and the
    # name from the title link (second Cardenas render, 2026-10-20).
    no_cta = re.sub(r'<a href="#" class="stratosLaunchButton[^>]*>Buy Online</a>', "", html)
    no_cta = no_cta.replace(
        '<h3><a href="/vehicle-details/new-2026-bmw-x5-xdrive40i-brownsville-tx-id-65097881">',
        '<img src="//cdn-ds.com/stock/2026-BMW-X5/seo/DS1-5UX23EU05T9488934/sz_1/a.jpg">'
        '<h3><a class="vehicle-title" href="/vehicle-details/new-2026-bmw-x5-xdrive40i-brownsville-tx-id-65097881">',
    )
    page2 = DealerFireAdapter().parse_listing_page(no_cta, "https://www.cardenasbmw.com/x", "h")
    assert [li.vin for li in page2.listings] == ["5UX23EU05T9488934"]  # the 2027 card lost its VIN
    li = page2.listings[0]
    assert (li.year, li.make, li.model, li.trim, li.condition_text) == (
        2026,
        "BMW",
        "X5",
        "xDrive40i",
        "New",
    )
    assert li.price_pairs[-1] == ("Cardenas Price", "$76,325") and li.odometer_miles is None


def test_generic_adapter_next_link_is_unescaped_absolute_and_never_self() -> None:
    """bmwpb.com 2026-10-20: the rel=next href was `...&amp;page=2`; left escaped, the site
    served page 1 thirty times. Relative hrefs resolve; a self-link is not a next page."""
    html = (PAGES / "generic_jsonld_page1.html").read_text()
    base = "https://www.groupstore-test.com/inventory/new/bmw/x5"
    esc = html.replace(
        'href="https://www.groupstore-test.com/inventory/new/bmw/x5?page=2"',
        'href="/inventory/new/bmw/x5?instock=true&amp;page=2"',
    )
    page = GenericJsonLdAdapter().parse_listing_page(esc, base, "h")
    assert page.next_url == f"{base}?instock=true&page=2"
    selfy = html.replace("x5?page=2", "x5")
    assert GenericJsonLdAdapter().parse_listing_page(selfy, base, "h").next_url is None


def test_generic_adapter_without_vehicles_is_layout_unknown_not_empty() -> None:
    page = GenericJsonLdAdapter().parse_listing_page(
        "<html><body><h1>Inventory</h1><p>loading…</p></body></html>", "https://h/x", "h"
    )
    assert not page.listings and not page.explicit_empty and not page.layout_ok
    empty = GenericJsonLdAdapter().parse_listing_page(
        "<html><body><p>No vehicles found matching your criteria.</p></body></html>",
        "https://h/x",
        "h",
    )
    assert empty.explicit_empty
    # Beaumont 2026-10-21: RideMotive ships that sentence inside a translation script.
    # It is not an empty inventory, so the browser still gets a turn.
    catalog = GenericJsonLdAdapter().parse_listing_page(
        '<html><body><script>{"none":"No vehicles found."}</script><div id="inventory"></div></body></html>',
        "https://bmwofbeaumont.com/cars/new-inventory",
        "h",
    )
    assert not catalog.explicit_empty and not catalog.layout_ok
    # The rendered Beaumont page names RideMotive and also a vendor widget. Stay on the
    # generic reader, which is what finds the schema.org vehicles.
    assert (
        detect_platform(
            "<html><body><script>dealeron widget</script><footer>ridemotive</footer></body></html>"
        )
        is Platform.OTHER
    )


# ----------------------------------------------------------------------------- compose
def _snapshot() -> LocatorSnapshot:
    body = (PAGES / "locator_tx_sample.json").read_text()
    dealers, total = parse_locator_json([body], "TX")
    return LocatorSnapshot(fetched_at="2026-10-20T10:00:00+00:00", source_urls=["u"], sha256="abc123def",
                           dealers=dealers, total_seen=total, storage_uri="/x.json")  # fmt: skip


def _disc(
    host: str, start: str | None, reason: DiscoveryReason, plat: Platform = Platform.DEALER_INSPIRE
) -> Discovery:
    return Discovery(host, start, prefixes_for(plat, start) if start else (), plat, reason, "t")


def test_compose_every_locator_dealer_gets_a_row_no_approval_gate() -> None:
    snap = _snapshot()
    disc = {
        "43552": _disc(
            "www.bmwoftyler.com",
            "https://www.bmwoftyler.com/new-vehicles/x5/",
            DiscoveryReason.HOMEPAGE_LINK,
        ),
        "10022": _disc(
            "www.cardenasbmw.com", None, DiscoveryReason.NO_INVENTORY_LINK, Platform.OTHER
        ),
        "50923": Discovery(
            None, None, (), Platform.UNKNOWN, DiscoveryReason.NO_HOSTNAME, "no website"
        ),
    }
    doc, res = compose(snap, disc, {}, None, NOW)
    rows = {r["dealer_id"]: r for r in doc["dealers"]}
    assert set(rows) == {"bmw-tyler", "cardenas-bmw", "bmw-plano"}
    assert all(r["terms_status"] == TermsStatus.OWNER_ACCEPTED_RISK.value for r in rows.values())
    assert all(r["active"] for r in rows.values())
    assert rows["bmw-tyler"]["start_url"] == "https://www.bmwoftyler.com/new-vehicles/x5/"
    assert rows["bmw-tyler"]["discovery_reason"] == "homepage_link"
    assert rows["bmw-plano"]["start_url"] is None and rows["bmw-plano"]["hostname"] == ""
    assert res.no_source == {"cardenas-bmw": "no_inventory_link", "bmw-plano": "no_hostname"}
    assert doc["locator_snapshot"]["dealer_count"] == 3
    reg = res.registry
    assert isinstance(reg, Registry) and len(reg.active()) == 3
    assert not reg.get("bmw-plano").fetchable and reg.get("bmw-tyler").fetchable


def test_compose_override_wins_and_is_recorded_as_privileged_edit() -> None:
    snap = _snapshot()
    o = Override(center_number="50923", operator="owner", set_at="2026-10-20T00:00:00+00:00",
                 hostname="www.group-site.com", start_url="https://www.group-site.com/new-vehicles/bmw/x5/?store=plano",
                 path_prefixes=("/new-vehicles/bmw/", "/inventory/"), platform=Platform.DEALER_INSPIRE,
                 notes="store filter")  # fmt: skip
    doc, res = compose(snap, {}, {"50923": o}, None, NOW)
    plano = next(r for r in doc["dealers"] if r["dealer_id"] == "bmw-plano")
    assert plano["start_url"].endswith("store=plano")
    assert plano["source"] == "override" and plano["discovery_reason"] == "override"
    assert plano["path_prefixes"] == ["/new-vehicles/bmw/", "/inventory/"]
    assert "override by owner at 2026-10-20" in plano["notes"] and "store filter" in plano["notes"]
    assert "bmw-plano" not in res.no_source


def test_compose_keeps_ids_stable_and_deactivates_dealers_that_left_the_locator() -> None:
    prev = Registry(
        version="old", config_hash="x",
        dealers=(
            Dealer(dealer_id="bmw-tyler-legacy-id", center_number="43552", name="BMW of Tyler", city="Tyler",
                   hostname="www.bmwoftyler.com", start_url="https://www.bmwoftyler.com/new-vehicles/x5/",
                   path_prefixes=("/new-vehicles/",), platform=Platform.DEALER_INSPIRE,
                   terms_status=TermsStatus.OWNER_ACCEPTED_RISK),
            Dealer(dealer_id="bmw-gone", center_number="99999", name="BMW of Gone", city="Nowhere",
                   hostname="www.gone.com", start_url="https://www.gone.com/new-vehicles/x5/",
                   path_prefixes=("/new-vehicles/",), platform=Platform.DEALER_INSPIRE,
                   terms_status=TermsStatus.OWNER_ACCEPTED_RISK),
        ),
    )  # fmt: skip
    doc, res = compose(_snapshot(), {}, {}, prev, NOW)
    rows = {r["dealer_id"]: r for r in doc["dealers"]}
    assert (
        "bmw-tyler-legacy-id" in rows and "bmw-tyler" not in rows
    )  # id preserved via center number
    assert rows["bmw-tyler-legacy-id"]["start_url"] is not None  # previous discovery kept
    assert rows["bmw-gone"]["active"] is False and "left the locator" in rows["bmw-gone"]["notes"]
    assert res.deactivated == ["bmw-gone"]
    assert set(res.added) == {"cardenas-bmw", "bmw-plano"}


def test_slug_rules() -> None:
    assert slug_for("BMW of Arlington") == "bmw-arlington"
    assert slug_for("BMW of the Permian Basin") == "bmw-permian-basin"
    assert slug_for("Autobahn Motorcars") == "autobahn-motorcars"
    assert slug_for("Sewell BMW of Plano") == "sewell-bmw-plano"


def test_discovery_rejects_x5_link_whose_filter_the_site_ignores() -> None:
    """A Dealer.com page reached via `?superModel=X5` that shows the whole new lot must not
    be accepted as the X5 start URL; the convention `?model=X5` is tried instead."""
    from carshop.registry.discovery import Discoverer, x5_share

    dc = (PAGES / "dealer_com_page1.html").read_text()
    url = "https://www.dc-test.com/new-inventory/index.htm?superModel=X5"
    assert Discoverer._looks_like_inventory(dc, Platform.DEALER_COM, url)  # all-X5 fixture
    unfiltered = dc.replace("BMW X5", "BMW X3").replace('"model":"X5"', '"model":"X3"')
    unfiltered = unfiltered.replace("X5 xDrive40i", "X3 xDrive30i").replace("X5 M60i", "X3 M50")
    assert not Discoverer._looks_like_inventory(unfiltered, Platform.DEALER_COM, url)
    assert x5_share([]) == 0.0
