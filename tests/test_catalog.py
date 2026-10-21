"""Workbook catalog: one stored row per VIN, dealer changes recorded once."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from carshop.catalog.workbook import canonical_dealer, import_workbooks, trim_code
from carshop.storage import migrate, repos
from carshop.storage.db import make_engine, write_tx

PLANO = "5UX23EU01T9505003"
GRAPEVINE = "5UX23EU00T9544231"
DALLAS = "5UX23EU0XT9513990"


def test_canonical_dealer_collapses_one_store() -> None:
    assert canonical_dealer("BMW of Dallas (the car you like)") == "BMW of Dallas"
    assert canonical_dealer("Bert Ogden BMW, McAllen") == "Bert Ogden BMW"
    assert canonical_dealer("BMW of West Houston, Katy") == "BMW of West Houston"
    assert canonical_dealer("BMW of Houston Midtown (Advantage BMW)") == "BMW of Houston Midtown"
    assert canonical_dealer("Sewell BMW of Plano") == "Sewell BMW of Plano"


def test_trim_code() -> None:
    assert trim_code("2026 BMW X5 xDrive40i") == "xDrive40i"
    assert trim_code("X5 sDrive40i") == "sDrive40i"
    assert trim_code("X5 xDrive50e") == "xDrive50e"
    assert trim_code("X5 M60i") == "M60i"
    assert trim_code("X5 M Competition") == "X5 M"


def _book(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    wb = Workbook()
    first = True
    for title, rows in sheets:
        ws = wb.active if first else wb.create_sheet(title)
        first = False
        assert ws is not None
        ws.title = title
        for row in rows:
            ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def test_one_row_per_vin_and_real_transfer(tmp_path: Path) -> None:
    early = tmp_path / "Texas_2026_BMW_X5_Inventory.xlsx"
    later = tmp_path / "Texas_2026_BMW_X5_Oct13_Deal_Analysis.xlsx"
    _book(
        early,
        [
            (
                "Inventory",
                [
                    ["VIN", "Dealer", "Trim", "Packages", "Driving Assistance Professional"],
                    [PLANO, "Sewell BMW of Plano", "X5 xDrive40i", "Premium Package", "Yes"],
                    [DALLAS, "BMW of Dallas (the car you like)", "X5 xDrive40i", "", None],
                ],
            )
        ],
    )
    _book(
        later,
        [
            (
                "Compare",
                [
                    ["VIN", "Dealer", "Trim"],
                    [PLANO, "Some Other BMW", "X5 xDrive40i"],
                ],
            ),
            (
                "Inventory",
                [
                    [
                        "VIN",
                        "Dealer",
                        "City",
                        "Trim",
                        "Advertised",
                        "Packages",
                        "Has the three packages",
                    ],
                    [
                        PLANO,
                        "BMW of Grapevine",
                        "Grapevine",
                        "X5 xDrive40i",
                        87125,
                        "Premium Package",
                        "Yes",
                    ],
                    [DALLAS, "BMW of Dallas, Dallas", "Dallas", "X5 xDrive40i", 80000, None, None],
                    [
                        GRAPEVINE,
                        "BMW of Grapevine",
                        "Grapevine",
                        "X5 M60i",
                        90000,
                        "no massage seats",
                        None,
                    ],
                ],
            ),
        ],
    )
    vehicles = {v.vin: v for v in import_workbooks([early, later])}
    assert set(vehicles) == {PLANO, GRAPEVINE, DALLAS}
    moved = vehicles[PLANO]
    assert moved.dealer_name == "BMW of Grapevine"
    assert moved.prior_dealer == "Sewell BMW of Plano"
    assert moved.features["DAP"] == "YES"
    assert moved.features["PREMIUM"] == "YES"
    assert moved.features["PARKING"] == "YES"
    assert moved.advertised_cents == 8_712_500
    stayed = vehicles[DALLAS]
    assert stayed.dealer_name == "BMW of Dallas"
    assert stayed.prior_dealer is None
    assert "PREMIUM" not in stayed.features
    assert vehicles[GRAPEVINE].features.get("MASSAGE") == "NO"


def test_replace_catalog_is_unique(tmp_path: Path) -> None:
    path = tmp_path / "Texas_2026_BMW_X5_Inventory.xlsx"
    _book(
        path,
        [
            (
                "Inventory",
                [
                    ["VIN", "Dealer", "Trim", "Miles"],
                    [PLANO, "Sewell BMW of Plano", "xDrive40i", 0],
                    [PLANO, "Sewell BMW of Plano", "xDrive40i", 12],
                ],
            )
        ],
    )
    vehicles = import_workbooks([path])
    assert len(vehicles) == 1
    assert vehicles[0].mileage == 12
    eng = make_engine(tmp_path / "app.db")
    migrate.upgrade(eng)
    with write_tx(eng) as conn:
        assert repos.replace_catalog(conn, vehicles, "2026-10-20T12:00:00Z") == 1
    with write_tx(eng) as conn:
        assert repos.replace_catalog(conn, vehicles, "2026-10-20T13:00:00Z") == 1
        stored = repos.catalog_vehicles(conn)
    assert len(stored) == 1
    assert stored[0]["vin"] == PLANO
    assert stored[0]["mileage"] == 12
