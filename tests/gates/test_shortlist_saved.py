"""requirements-shortlist.md §11 checks 6 (saved-car part), 7 and 8, on fixture pipeline runs."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from carshop.domain.codes import DealerStatus
from carshop.pipeline.notify import build_digest
from carshop.shortlist import saved as sl
from carshop.storage import schema as s
from carshop.storage.db import read_conn, write_tx
from tests.gates.test_gates import DI, DI_PAGE, T1, T2, T3, di_routes, go, registry

pytestmark = pytest.mark.gate

PRICED = "5UX23EU03T9529691"  # $79,325 on the fixture page
MSRP_ONLY = "5UX23EU05T9472216"  # MSRP only, no cash price
PENDING_PAGE = DI_PAGE.replace('"year":"2026"', '"year":"2025"', 1)  # PRICED held for review


def _save(eng, vin: str) -> dict:  # type: ignore[no-untyped-def]
    with write_tx(eng) as c:
        row, created = sl.save_car(c, vin, now=T1)
    assert created
    return row


def _track(eng, vin: str) -> dict:  # type: ignore[no-untyped-def]
    with read_conn(eng) as c:
        return next(t for t in sl.track_all(c) if t["vin"] == vin)


def test_check7_a_1000_drop_is_down_1000_on_saved_cars_and_in_the_digest(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    _save(eng, PRICED)
    fake.routes = di_routes(
        DI_PAGE.replace('<span class="price">$79,325</span>', '<span class="price">$78,325</span>')
    )
    r2 = go(env, registry(DI), now=T2)
    t = _track(eng, PRICED)
    assert t["change"] == {"label": "▼ $1,000", "direction": "down", "delta_cents": -1_000_00}
    assert t["price_now"] == "$78,325" and t["lowest_cents"] == 78_325_00
    assert t["status"] == "Listed" and t["changed"]
    with read_conn(eng) as c:
        body = build_digest(c, r2.run_id).body
    assert "Your saved cars" in body
    assert f"{PRICED} · DI · $79,325 → $78,325 (▼ $1,000)" in body


def test_check7_null_to_value_is_price_appeared_not_a_drop(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    snap = _save(eng, MSRP_ONLY)["snapshot"]
    assert snap["comparison_price_cents"] is None
    msrp_block = '<span class="price">$81,900</span></div>\n'
    assert msrp_block in DI_PAGE
    priced = DI_PAGE.replace(
        msrp_block,
        msrp_block
        + '        <div class="price-block our-price"><span class="price-label">Our Price</span><span class="price">$80,900</span></div>\n',
        1,
    )
    fake.routes = di_routes(priced)
    go(env, registry(DI), now=T2)
    t = _track(eng, MSRP_ONLY)
    assert t["price_now_cents"] == 80_900_00
    assert t["change"]["label"] == "Price appeared"
    assert t["change"]["direction"] is None and t["change"]["delta_cents"] is None


def test_price_change_rules() -> None:
    assert sl.price_change(None, None, 100_00, None)["label"] == "Price appeared"
    assert sl.price_change(100_00, None, None, None)["label"] == "Price withdrawn"
    assert sl.price_change(100_00, "vehicle_only", 101_00, "vehicle_only")["label"] == "▲ $1"
    assert sl.price_change(100_00, "vehicle_only", 90_00, "with_fees")["delta_cents"] is None


def test_check6_a_dealer_not_read_never_moves_a_saved_car_to_missing(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    _save(eng, PRICED)
    fake.routes = di_routes(listing_status=503)
    r2 = go(env, registry(DI), now=T2)
    assert r2.dealers["di"] is DealerStatus.NOT_REPORTED
    t = _track(eng, PRICED)
    assert t["status"] == "Listed"
    assert t["not_read_on"] == "2026-10-20" and t["last_read"] == "2026-10-19"
    assert t["price_now"] == "$79,325" and t["change"]["label"] == "No change"
    with read_conn(eng) as c:
        assert "Your saved cars" not in build_digest(c, r2.run_id).body


def test_check6_a_reported_dealer_without_the_vin_is_missing_unverified_never_sold(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    _save(eng, PRICED)
    fake.routes = di_routes(DI_PAGE.replace(PRICED, "5UX23EU03T9529690"))  # bad checksum: gone
    r2 = go(env, registry(DI), now=T2)
    assert r2.dealers["di"] is DealerStatus.REPORTED
    t = _track(eng, PRICED)
    assert t["status"] == "Missing, unverified"
    assert "sold" not in t["status"].lower() and t["not_read_on"] is None
    with read_conn(eng) as c:
        body = build_digest(c, r2.run_id).body
    assert f"{PRICED} · DI · Missing, unverified (not a sale)" in body
    fake.routes = di_routes()
    go(env, registry(DI), now=T3)
    assert _track(eng, PRICED)["status"] == "Back again"


def test_check8_a_car_held_for_review_cannot_be_saved(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes(PENDING_PAGE)
    go(env, registry(DI), now=T1, notify=False)
    with write_tx(eng) as c, pytest.raises(sl.ShortlistError) as err:
        sl.save_car(c, PRICED)
    assert err.value.status == 409 and "review" in err.value.message
    with read_conn(eng) as c:
        assert sl.savable_vins(c, [PRICED, MSRP_ONLY]) == {MSRP_ONLY}
        assert c.execute(select(func.count()).select_from(s.saved_cars)).scalar() == 0


def test_check8_tracking_ignores_an_observation_held_for_review(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    _save(eng, PRICED)
    # Day 2: the car is held for review, and its held row also shows a lower price.
    fake.routes = di_routes(
        PENDING_PAGE.replace(
            '<span class="price">$79,325</span>', '<span class="price">$70,325</span>'
        )
    )
    go(env, registry(DI), now=T2, notify=False)
    t = _track(eng, PRICED)
    assert t["status"] == "Waiting for a review check"
    assert t["price_now"] == "$79,325" and t["change"]["label"] == "No change"
    assert t["lowest_cents"] == 79_325_00


def test_snapshot_never_changes_and_remove_is_soft(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    first = _save(eng, PRICED)
    with write_tx(eng) as c:
        again, created = sl.save_car(c, PRICED)
        assert not created and again["snapshot"] == first["snapshot"]
        sl.set_note(c, PRICED, "Ask about the floor mats")
        assert sl.remove_car(c, PRICED)
        assert not sl.remove_car(c, PRICED)
        assert sl.saved_vins(c) == set()
        rows = list(c.execute(select(s.saved_cars)).mappings())
    assert len(rows) == 1 and rows[0]["removed_at"] is not None
    assert rows[0]["note"] == "Ask about the floor mats"
    with write_tx(eng) as c:
        _row, created = sl.save_car(c, PRICED)
    assert created  # a removed car can be saved again; the old row stays for the record


def test_limits_note_length_and_count(env, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    monkeypatch.setattr(sl, "MAX_SAVED_CARS", 1)
    with write_tx(eng) as c:
        with pytest.raises(sl.ShortlistError):
            sl.save_car(c, PRICED, note="x" * 281)
        sl.save_car(c, PRICED, note="x" * 280)
        with pytest.raises(sl.ShortlistError) as err:
            sl.save_car(c, MSRP_ONLY)
    assert "holds 1 cars" in err.value.message
