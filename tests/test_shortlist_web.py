"""requirements-shortlist.md §11 checks 1 and 10: saving from the screen, and the write guards.

Runs on a copy of the local database (the worked example is real 2026-10-20 data), so the
owner's own shortlist is never touched.
"""

from __future__ import annotations

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
from carshop.shortlist import saved as sl
from carshop.storage import schema as s
from carshop.storage.db import make_engine, read_conn
from carshop.web.app import create_app, is_loopback
from tests.test_buyer_vocabulary import leaks

VIN = "5UX23EU0XT9462524"  # Skyscraper Grey / Silverstone at BMW of Arlington
ORIGIN = "http://testserver"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ALLOWED_WRITES = {
    ("POST", "/api/shortlist/cars"),
    ("PATCH", "/api/shortlist/cars/{vin}"),
    ("DELETE", "/api/shortlist/cars/{vin}"),
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
    assert "No saved cars yet. Use ☆ Save on any car." in client.get("/shortlist?tab=saved").text


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
    # The only module behind the write endpoints writes saved_cars and nothing else.
    src = (Path(sl.__file__)).read_text()
    targets = set(re.findall(r"update\(s\.(\w+)\)", src)) | set(
        re.findall(r"s\.(\w+)\.(?:insert|delete)\(", src)
    )
    assert targets == {"saved_cars"}
    before = _counts(data_dir)
    h = _headers(client)
    assert client.post("/api/shortlist/cars", json={"vin": VIN}, headers=h).status_code == 200
    assert (
        client.patch(f"/api/shortlist/cars/{VIN}", json={"note": "n"}, headers=h).status_code == 200
    )
    assert client.delete(f"/api/shortlist/cars/{VIN}", headers=h).status_code == 200
    assert _counts(data_dir) == before


def test_a_busy_database_gets_the_try_again_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*_a: object, **_k: object) -> None:
        raise OperationalError("INSERT", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(sl, "save_car", locked)
    r = client.post("/api/shortlist/cars", json={"vin": VIN}, headers=_headers(client))
    assert r.status_code == 503 and r.json()["error"] == sl.BUSY
