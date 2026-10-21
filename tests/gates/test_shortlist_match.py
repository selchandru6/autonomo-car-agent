"""requirements-shortlist.md §11 checks 4, 5, 6 (matcher), 8 (matcher) and 9."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carshop.config.loaders import load_locator_coords
from carshop.domain.codes import DealerStatus, RunStatus
from carshop.pipeline.notify import build_digest, deliver_digest
from carshop.shortlist import match as m
from carshop.shortlist.searches import Geo, create_search, evaluate_run
from carshop.storage import schema as s
from carshop.storage.db import read_conn, write_tx
from tests.conftest import make_vin
from tests.gates.test_gates import DI, DI_PAGE, T1, T2, di_routes, go, registry
from tests.gates.test_shortlist_saved import PENDING_PAGE, PRICED

pytestmark = pytest.mark.gate

NEW = make_vin(serial="529700")


def _grey_dap() -> dict:
    c = m.default_criteria()
    c["features"]["DAP"] = "must"
    c["exterior"] = {"families": ["Grey"], "names": []}
    return m.normalize(c)


def _geo(conn, st):  # type: ignore[no-untyped-def]
    return Geo(conn, load_locator_coords(st.registry_dir))


def _create(eng, st, name: str = "Grey DAP") -> int:  # type: ignore[no-untyped-def]
    with write_tx(eng) as c:
        found, _counts = create_search(c, name, _grey_dap(), _geo(c, st))
    return int(found["search_id"])


def _added_grey(page: str, vin: str) -> str:
    marker = f'data-vehicle=\'{{"vin":"{PRICED}"'
    i = page.find(marker)
    assert i > 0
    start = page.rfind("<div class=\"result-wrap", 0, i)
    end = page.find("<div class=\"result-wrap", i + 1)
    card = page[start:end]
    clone = card.replace(PRICED, vin).replace("T9529691", vin[-8:])
    return page[:end] + clone + page[end:]


def test_check4_baseline_is_not_new_and_a_later_vin_is_announced_once(
    env,
) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    sid = _create(eng, st)
    with read_conn(eng) as c:
        rows = list(c.execute(select(s.search_matches).where(s.search_matches.c.search_id == sid)).mappings())
    assert rows and all(r["baseline"] and not r["is_new"] for r in rows)
    assert PRICED in {r["vin"] for r in rows}

    fake.routes = di_routes(_added_grey(DI_PAGE, NEW))
    r2 = go(env, registry(DI), now=T2, notify=False)
    with read_conn(eng) as c:
        fresh = list(
            c.execute(
                select(s.search_matches).where(
                    s.search_matches.c.search_id == sid, s.search_matches.c.run_id == r2.run_id
                )
            ).mappings()
        )
        body = build_digest(c, r2.run_id).body
    news = [r for r in fresh if r["is_new"]]
    assert [r["vin"] for r in news] == [NEW]
    assert "New for your saved searches" in body
    assert NEW in body and PRICED not in body.split("New for your saved searches")[-1].split("Floor")[0]
    first = deliver_digest(eng, st, r2.run_id)
    assert first
    second = deliver_digest(eng, st, r2.run_id)
    assert set(second.values()) == {"already-sent"}


def test_check5_a_sticker_turning_dap_yes_is_now_a_full_match(
    env,
) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    fake.routes = di_routes(vdp=None)
    go(env, registry(DI), now=T1)
    sid = _create(eng, st)
    with read_conn(eng) as c:
        before = {
            r["vin"]: r["tier"]
            for r in c.execute(select(s.search_matches).where(s.search_matches.c.search_id == sid)).mappings()
        }
    assert before.get(PRICED) == m.POSSIBLE
    fake.routes = di_routes()
    r2 = go(env, registry(DI), now=T2)
    with read_conn(eng) as c:
        after = list(
            c.execute(
                select(s.search_matches).where(
                    s.search_matches.c.search_id == sid, s.search_matches.c.run_id == r2.run_id
                )
            ).mappings()
        )
    hit = next(r for r in after if r["vin"] == PRICED)
    assert hit["tier"] == m.MATCH and hit["became_full_match"] and not hit["is_new"]


def test_check6_a_dealer_not_reported_produces_no_new_match(
    env,
) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    _create(eng, st)
    fake.routes = di_routes(listing_status=503)
    r2 = go(env, registry(DI), now=T2)
    assert r2.dealers["di"] is DealerStatus.NOT_REPORTED
    with read_conn(eng) as c:
        news = list(
            c.execute(
                select(s.search_matches).where(
                    s.search_matches.c.run_id == r2.run_id, s.search_matches.c.is_new.is_(True)
                )
            ).mappings()
        )
        body = build_digest(c, r2.run_id).body
    assert news == []
    assert "New for your saved searches" not in body


def test_check8_a_pending_review_car_is_never_a_match(
    env,
) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    fake.routes = di_routes(PENDING_PAGE)
    go(env, registry(DI), now=T1, notify=False)
    sid = _create(eng, st)
    with read_conn(eng) as c:
        vins = {
            r["vin"]
            for r in c.execute(select(s.search_matches).where(s.search_matches.c.search_id == sid)).mappings()
        }
    assert PRICED not in vins


def test_check9_matcher_failure_leaves_the_run_published(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    import carshop.agents.matcher as matcher_mod

    st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    _create(eng, st)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("matcher exploded")

    monkeypatch.setattr(matcher_mod, "evaluate_run", boom)
    r2 = go(env, registry(DI), now=T2)
    assert r2.status is RunStatus.PUBLISHED
    with read_conn(eng) as c:
        body = build_digest(c, r2.run_id).body
    assert "Saved-search matches could not be computed today" in body

    with write_tx(eng) as c:
        counts = evaluate_run(c, r2.run_id, _geo(c, st))
    assert counts["searches"] == 1 and counts["error"] is None
    with read_conn(eng) as c:
        recovered = build_digest(c, r2.run_id).body
        rows = list(
            c.execute(select(s.search_matches).where(s.search_matches.c.run_id == r2.run_id)).mappings()
        )
    assert "Saved-search matches could not be computed today" not in recovered
    assert any(r["vin"] == PRICED and r["tier"] == m.MATCH for r in rows)
