"""requirements-shortlist.md §11 checks 1, 2, 3, 10, 12, 13: saving, searches, and write guards.

Runs on a copy of the local database (the worked example is real 2026-10-20 data), so the
owner's own shortlist is never touched.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from carshop.config.settings import Settings, get_settings
from carshop.shortlist import match as sm
from carshop.shortlist import saved as sl
from carshop.shortlist import searches as ss
from carshop.storage import schema as s
from carshop.storage.db import make_engine, read_conn
from carshop.web.app import create_app, is_loopback
from tests.test_buyer_vocabulary import leaks

VIN = "5UX23EU0XT9462524"  # Skyscraper Grey / Silverstone at BMW of Arlington
MATCH_VIN = "5UX23EU01T9541662"  # Dravit Grey / Cognac, DAP YES, $88,225
POSSIBLE_VIN = "5UX23EU02T9552461"  # DAP not listed
EXCLUDED_VIN = "5UX23EU0XT9439258"  # $92,869, outside ±$3,000
WISH = {
    "budget": 90_000,
    "near": "any",
    "radius": None,
    "transit": True,
    "colorsOnly": False,
    "sort": "match",
    "ext": ["Skyscraper Grey", "Brooklyn Grey"],
    "int": ["Cognac", "Coffee"],
    "feats": {"DAP": "must", "PANORAMIC_ROOF": "nice"},
}
ORIGIN = "http://testserver"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ALLOWED_WRITES = {
    ("POST", "/api/shortlist/cars"),
    ("PATCH", "/api/shortlist/cars/{vin}"),
    ("DELETE", "/api/shortlist/cars/{vin}"),
    ("POST", "/api/shortlist/searches"),
    ("POST", "/api/shortlist/searches/preview"),
    ("PATCH", "/api/shortlist/searches/{search_id}"),
    ("DELETE", "/api/shortlist/searches/{search_id}"),
    ("POST", "/review/{item_id}"),  # always 403: review decisions go through the CLI
}
PROTECTED = (
    "observations",
    "inventory_staging",
    "review_items",
    "runs",
    "dealer_runs",
    "audit_events",
)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    real = get_settings()
    if not real.db_path.exists():
        pytest.skip("needs the local database")
    d = tmp_path_factory.mktemp("data")
    src, dst = sqlite3.connect(real.db_path), sqlite3.connect(d / "app.db")
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    if real.registry_dir.exists():
        (d / "registry").symlink_to(real.registry_dir.resolve())
    eng = make_engine(d / "app.db")
    with read_conn(eng) as c:
        if sl.latest_validated(c, VIN) is None:
            pytest.skip("worked-example car not in the local data")
    return d


@pytest.fixture(scope="module")
def client(data_dir: Path) -> TestClient:
    return TestClient(create_app(Settings(data_dir=data_dir)))


def _token(html: str) -> str:
    m = re.search(r'<meta name="csrf-token" content="([^"]+)">', html)
    assert m, "no page token"
    return m.group(1)


def _headers(client: TestClient, **extra: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": _token(client.get("/shortlist").text), **extra}


def _button(html: str, vin: str) -> str:
    m = re.search(r'<button[^>]*data-save="' + vin + r'"[^>]*>([^<]*)</button>', html)
    assert m, f"no save control for {vin}"
    return m.group(0)


def _counts(data_dir: Path) -> dict[str, Any]:
    eng = make_engine(data_dir / "app.db")
    with read_conn(eng) as c:
        return {
            t: c.execute(select(func.count()).select_from(s.metadata.tables[t])).scalar()
            for t in PROTECTED
        }


def test_check1_save_on_all_inventory_shows_saved_everywhere(
    client: TestClient, data_dir: Path
) -> None:
    catalog = client.get("/catalog").text
    assert "☆ Save" in _button(catalog, VIN) and "disabled" not in _button(catalog, VIN)
    r = client.post("/api/shortlist/cars", json={"vin": VIN}, headers=_headers(client))
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    assert "★ Saved" in _button(client.get("/catalog").text, VIN)
    assert "★ Saved" in _button(client.get(f"/vin/{VIN}").text, VIN)
    # Find a car draws its cards in the browser from the saved set the page carries.
    state = re.search(r'<script id="shortlistState"[^>]*>(.*?)</script>', client.get("/").text)
    assert state and VIN in state.group(1) and '"writes": true' in state.group(1)
    saved = client.get("/shortlist?tab=saved").text
    assert VIN in saved and "Listed" in saved
    assert leaks(saved) == set()

    eng = make_engine(data_dir / "app.db")
    with read_conn(eng) as c:
        snap = sl.get_saved(c, VIN)["snapshot"]  # type: ignore[index]
    assert {k: v["status"] for k, v in snap["features"].items()} == {
        "DAP": "YES", "M_SPORT": "YES", "MASSAGE": "UNKNOWN", "PANORAMIC_ROOF": "YES",
        "SKY_LOUNGE": "UNKNOWN", "EXECUTIVE": "UNKNOWN", "CLIMATE_COMFORT": "YES", "AIR_SUSPENSION": "UNKNOWN",
    }  # fmt: skip
    assert (snap["exterior_raw"], snap["exterior_code"]) == ("Skyscraper Grey Metallic", "C4W")
    assert (snap["interior_raw"], snap["interior_code"]) == ("Silverstone Sensafin", "KPA9")
    assert snap["msrp_cents"] == 87_125_00 and snap["comparison_price_cents"] == 87_949_00
    assert snap["dealer_id"] == "bmw-arlington" and snap["ag_code"] == "23EU"

    note = client.patch(
        f"/api/shortlist/cars/{VIN}", json={"note": "Ask for the sticker"}, headers=_headers(client)
    )
    assert note.status_code == 200 and note.json()["note"] == "Ask for the sticker"
    gone = client.delete(f"/api/shortlist/cars/{VIN}", headers=_headers(client))
    assert gone.status_code == 200 and gone.json()["removed"] is True
    assert "☆ Save" in _button(client.get("/catalog").text, VIN)
    saved_page = client.get("/shortlist?tab=saved").text
    assert VIN not in saved_page or "☆ Save" in _button(saved_page, VIN)
    eng = make_engine(data_dir / "app.db")
    with read_conn(eng) as c:
        leftover = sl.saved_vins(c)
    assert VIN not in leftover
    if not leftover:
        assert "No saved cars yet. Use ☆ Save on any car." in saved_page


def test_cars_never_read_in_a_run_cannot_be_saved(client: TestClient, data_dir: Path) -> None:
    eng = make_engine(data_dir / "app.db")
    with read_conn(eng) as c:
        vins = [str(v) for v in c.execute(select(s.catalog_vehicles.c.vin)).scalars()]
        unread = sorted(set(vins) - sl.savable_vins(c, vins))
    if not unread:
        pytest.skip("every workbook car was read")
    assert "disabled" in _button(client.get("/catalog").text, unread[0])
    r = client.post("/api/shortlist/cars", json={"vin": unread[0]}, headers=_headers(client))
    assert r.status_code == 404 and "not been read" in r.json()["error"]


def test_check10_cross_origin_and_missing_token_are_refused(
    client: TestClient, data_dir: Path
) -> None:
    before = _counts(data_dir)
    token = _token(client.get("/shortlist").text)
    body = {"vin": VIN}
    cases = [
        {"Origin": "http://evil.example", "X-CSRF-Token": token},
        {"Sec-Fetch-Site": "cross-site", "X-CSRF-Token": token},
        {"X-CSRF-Token": token},  # neither Origin nor Sec-Fetch-Site
        {"Origin": ORIGIN},  # no token
        {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
    ]
    for headers in cases:
        assert client.post("/api/shortlist/cars", json=body, headers=headers).status_code == 403, (
            headers
        )
        assert client.delete(f"/api/shortlist/cars/{VIN}", headers=headers).status_code == 403, (
            headers
        )
        assert (
            client.patch(
                f"/api/shortlist/cars/{VIN}", json={"note": "x"}, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post("/api/shortlist/searches", json={"name": "x"}, headers=headers).status_code
            == 403
        )
        assert (
            client.post(
                "/api/shortlist/searches/preview", json={"criteria": {}}, headers=headers
            ).status_code
            == 403
        )
    ok = client.post(
        "/api/shortlist/cars",
        json=body,
        headers={"Sec-Fetch-Site": "same-origin", "X-CSRF-Token": token},
    )
    assert ok.status_code == 200
    client.delete(f"/api/shortlist/cars/{VIN}", headers={"Origin": ORIGIN, "X-CSRF-Token": token})
    assert _counts(data_dir) == before


def test_check10_writes_are_off_unless_bound_to_loopback(data_dir: Path) -> None:
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.20")
    shared = TestClient(create_app(Settings(data_dir=data_dir, web_host="0.0.0.0")))
    page = shared.get("/catalog").text
    assert "data-save=" not in page and 'name="csrf-token"' not in page
    assert "data-save=" not in shared.get(f"/vin/{VIN}").text
    r = shared.post(
        "/api/shortlist/cars", json={"vin": VIN}, headers={"Origin": ORIGIN, "X-CSRF-Token": ""}
    )
    assert r.status_code == 403
    assert (
        shared.delete(f"/api/shortlist/cars/{VIN}", headers={"Origin": ORIGIN}).status_code == 403
    )


def test_check10_route_inventory_no_write_reaches_run_data(
    client: TestClient, data_dir: Path
) -> None:
    writes = {
        (m, r.path)
        for r in client.app.routes
        if isinstance(r, APIRoute)
        for m in r.methods & WRITE_METHODS
    }
    assert writes == ALLOWED_WRITES
    assert client.post("/review/1").status_code == 403
    # Write-endpoint modules never touch run evidence. Matcher failure markers live in
    # notification_events and are not reachable from these routes.
    def tables(path: Path) -> set[str]:
        src = path.read_text()
        return (
            set(re.findall(r"update\(s\.(\w+)\)", src))
            | set(re.findall(r"s\.(\w+)\.(?:insert|delete)\(", src))
            | set(re.findall(r"delete\(s\.(\w+)\)", src))
        )

    assert tables(Path(sl.__file__)) == {"saved_cars"}
    assert tables(Path(ss.__file__)) <= {"saved_searches", "search_matches", "notification_events"}
    before = _counts(data_dir)
    h = _headers(client)
    assert client.post("/api/shortlist/cars", json={"vin": VIN}, headers=h).status_code == 200
    assert (
        client.patch(f"/api/shortlist/cars/{VIN}", json={"note": "n"}, headers=h).status_code == 200
    )
    assert client.delete(f"/api/shortlist/cars/{VIN}", headers=h).status_code == 200
    preview = client.post("/api/shortlist/searches/preview", json={"criteria": {}}, headers=h)
    assert preview.status_code == 200
    assert _counts(data_dir) == before


def test_a_busy_database_gets_the_try_again_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*_a: object, **_k: object) -> None:
        raise OperationalError("INSERT", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(sl, "save_car", locked)
    r = client.post("/api/shortlist/cars", json={"vin": VIN}, headers=_headers(client))
    assert r.status_code == 503 and r.json()["error"] == sl.BUSY


def _finder_payload(html: str) -> dict[str, Any]:
    m = re.search(r'<script type="application/json" id="finderData">(.*?)</script>', html, re.S)
    assert m, "no finder payload"
    return json.loads(m.group(1))


def test_check2_find_cars_like_this_prefills_and_preview_matches_save(
    client: TestClient, data_dir: Path
) -> None:
    form = client.get(f"/shortlist/searches/new?base={VIN}").text
    assert form.count('name="feat-DAP"') >= 3
    assert re.search(r'name="feat-DAP" value="must"[^>]*checked', form)
    assert re.search(r'name="feat-M_SPORT" value="nice"[^>]*checked', form)
    assert re.search(r'name="feat-PANORAMIC_ROOF" value="nice"[^>]*checked', form)
    assert re.search(r'name="feat-CLIMATE_COMFORT" value="nice"[^>]*checked', form)
    assert 'value="Grey" checked' in form or "value=\"Grey\" checked" in form
    assert "Any grey" in form and "Any light" in form
    assert "Like Skyscraper Grey · Silverstone" in form
    assert "$84,949 to $90,949" in form
    assert leaks(form) == set()

    eng = make_engine(data_dir / "app.db")
    with read_conn(eng) as c:
        saved = sl.get_saved(c, VIN)
        obs = sl.latest_validated(c, VIN)
        assert obs is not None
        snap = saved["snapshot"] if saved else sl.build_snapshot(c, obs)
        _name, criteria = sm.prefill_from_snapshot(snap, obs.get("city"))
    h = _headers(client)
    preview = client.post(
        "/api/shortlist/searches/preview", json={"criteria": criteria}, headers=h
    )
    assert preview.status_code == 200, preview.text
    named = re.search(r'id="sfName"[^>]*value="([^"]+)"', form)
    assert named and named.group(1).startswith("Like Skyscraper Grey · Silverstone")
    saved_search = client.post(
        "/api/shortlist/searches",
        json={"name": named.group(1), "criteria": criteria},
        headers=h,
    )
    assert saved_search.status_code == 200, saved_search.text
    body = saved_search.json()
    assert body["match"] == preview.json()["match"]
    assert body["possible"] == preview.json()["possible"]
    listed = client.get("/shortlist?tab=searches").text
    assert named.group(1) in listed
    assert leaks(listed) == set()
    client.delete(f"/api/shortlist/searches/{body['search_id']}", headers=h)
    client.delete(f"/api/shortlist/cars/{VIN}", headers=h)


def test_check3_worked_example_peers(client: TestClient, data_dir: Path) -> None:
    eng = make_engine(data_dir / "app.db")
    with read_conn(eng) as c:
        if any(sl.latest_validated(c, v) is None for v in (MATCH_VIN, POSSIBLE_VIN, EXCLUDED_VIN)):
            pytest.skip("worked-example peers not in the local data")
        obs = sl.latest_validated(c, VIN)
        assert obs is not None
        snap = sl.build_snapshot(c, obs)
        _name, criteria = sm.prefill_from_snapshot(snap, obs.get("city"))
    h = _headers(client)
    created = client.post(
        "/api/shortlist/searches", json={"name": "Worked example", "criteria": criteria}, headers=h
    )
    assert created.status_code == 200, created.text
    sid = created.json()["search_id"]
    page = client.get(f"/shortlist/searches/{sid}").text
    assert MATCH_VIN in page and "Match" in page and "+$276" in page
    assert POSSIBLE_VIN in page and "Possible match" in page
    assert EXCLUDED_VIN not in page
    assert VIN not in page.split("Current matches")[-1]
    assert leaks(page) == set()
    client.delete(f"/api/shortlist/searches/{sid}", headers=h)
    client.delete(f"/api/shortlist/cars/{VIN}", headers=h)


def test_check12_four_tabs_and_the_menu_badge(client: TestClient) -> None:
    home = client.get("/shortlist").text
    for label in ("New today", "Saved cars", "Saved searches", "Ranked for you"):
        assert label in home
    for tab in ("new", "saved", "searches", "ranked"):
        r = client.get(f"/shortlist?tab={tab}")
        assert r.status_code == 200, tab
        assert f'href="/shortlist?tab={tab}"' in r.text or f"tab={tab}" in r.text
        assert 'aria-current="true"' in r.text
        assert leaks(r.text) == set()
    status = client.get("/api/status").json()
    n = status["new_matches"]
    html = client.get("/shortlist").text
    if n:
        assert f'aria-label="{n} new matches today"' in html
    else:
        assert "new matches today" not in html


def test_check13_wish_list_round_trips_through_find_a_car(client: TestClient) -> None:
    h = _headers(client)
    created = client.post(
        "/api/shortlist/searches", json={"wish": WISH}, headers=h
    )
    assert created.status_code == 200, created.text
    sid = created.json()["search_id"]
    payload = _finder_payload(client.get(f"/?search={sid}").text)
    opened = payload["openSearch"]
    assert opened["id"] == sid
    again = sm.criteria_from_wish(opened["wish"])
    assert again == sm.criteria_from_wish(WISH)
    listed = client.get("/shortlist?tab=searches").text
    assert created.json()["name"] in listed
    client.delete(f"/api/shortlist/searches/{sid}", headers=h)
