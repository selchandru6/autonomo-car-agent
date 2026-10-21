"""Shop and Market pages show no internal codes (requirements-web-ui.md 3.2, acceptance check 10).

Codes are allowed only inside a closed "Technical details" disclosure. Visible text and the
title / aria-label / alt attributes (tooltips and screen-reader text) are scanned.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from selectolax.lexbor import LexborHTMLParser

from carshop.config.loaders import load_preferences
from carshop.config.settings import Settings
from carshop.storage.db import make_engine, read_conn
from carshop.web import shell, views
from carshop.web.app import create_app

CODE = re.compile(
    r"\b(?:REPORTED|NOT[_ ]REPORTED|NO[_ ]SOURCE|PARTIAL|UNKNOWN|VALIDATED|PENDING[_ ]REVIEW"
    r"|run_id|observation_id)\b"
    r"|\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b"  # reason codes and flags: HTTP_403, DAP_UNKNOWN, ON_LOT
    r"|\b\d{8}T\d{6}Z\b"  # run ids
)
BUYER_GROUPS = ("Shop", "Market")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def leaks(html: str) -> set[str]:
    tree = LexborHTMLParser(html)
    for node in tree.css("script, style, aside.sidebar"):
        node.decompose()
    for det in tree.css("details"):
        summary = det.css_first("summary")
        if summary is not None and "Technical details" in summary.text():
            det.decompose()
    body = tree.body
    assert body is not None
    found = {m.group(0) for m in CODE.finditer(body.text(separator=" "))}
    for node in tree.css("[title], [aria-label], [alt]"):
        for attr in ("title", "aria-label", "alt"):
            value = node.attributes.get(attr)
            if value:
                found |= {f"{m.group(0)} in {attr}" for m in CODE.finditer(value)}
    return found


def buyer_pages(client: TestClient) -> list[str]:
    pages = [item.href for item in shell.MENU if item.group in BUYER_GROUPS]
    pages += ["/shortlist?tab=saved", "/shortlist?tab=ranked"]
    dealers = client.get("/dealers").text
    pages += sorted(set(re.findall(r'href="(/dealers/[\w-]+)"', dealers)))
    inventory = client.get("/inventory").text
    pages += sorted({f"/vin/{v}" for v in re.findall(r'href="/vin/(\w{17})"', inventory)})[:40]
    return pages


def test_scanner_catches_codes_and_ignores_technical_details() -> None:
    assert leaks("<body><p>BMW of Austin — NOT_REPORTED (HTTP_403)</p></body>") == {
        "NOT_REPORTED",
        "HTTP_403",
    }
    assert leaks('<body><a title="2026-10-20 REPORTED">x</a></body>') == {"REPORTED in title"}
    assert leaks("<body><td>20261020T052908Z-manual_date-a78f54</td></body>") == {
        "20261020T052908Z"
    }
    hidden = "<body><details><summary>Technical details</summary>HTTP_403 REPORTED</details></body>"
    assert leaks(hidden) == set()


def test_shop_and_market_pages_show_no_internal_codes(client: TestClient) -> None:
    pages = buyer_pages(client)
    assert any(p.startswith("/vin/") for p in pages)
    assert sum(p.startswith("/dealers/") for p in pages) >= 20
    found = {}
    for path in pages:
        response = client.get(path)
        assert response.status_code == 200, path
        hits = leaks(response.text)
        if hits:
            found[path] = sorted(hits)
    assert found == {}


def test_car_counts_agree_between_pages() -> None:
    """Dealers, the dealer page, and Find a car count the same validated cars (requirements 5.5)."""
    settings = Settings()
    prefs = load_preferences()
    with read_conn(make_engine(settings.db_path)) as conn:
        directory = views.dealer_directory(conn)["active"]
        finder = {
            d["id"]: d["cars"] for d in views.buyer_payload(conn, settings.registry_dir)["dealers"]
        }
        checked = 0
        for row in directory:
            if row["cars"] is None:
                continue
            detail = views.dealer_detail(conn, str(row["dealer_id"]), prefs)
            assert detail is not None
            assert row["cars"] == len(detail["units"]), row["name"]
            assert row["cars"] == finder.get(row["dealer_id"]), row["name"]
            checked += 1
    assert checked > 0
