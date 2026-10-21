# ruff: noqa: N802 - finding ids in the test name are the review's required spelling.
"""Requirements §13 gate tests: whole pipeline on a temp DB with a fake HTTP layer, no browser.

Each test name states the gate. These must pass before any release.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text

from carshop.config.loaders import Registry
from carshop.config.settings import Settings
from carshop.domain.codes import (
    DealerStatus,
    LifecycleState,
    Platform,
    ReasonCode,
    RunStatus,
    TermsStatus,
    Trigger,
)
from carshop.domain.models import Dealer
from carshop.fetch.http import HttpResult
from carshop.pipeline import run as runmod
from carshop.storage import migrate, repos
from carshop.storage import schema as s
from carshop.storage.db import make_engine, read_conn
from tests.conftest import GOLDEN

pytestmark = pytest.mark.gate

PAGES = GOLDEN / "pages"
DI_MULTI = (
    PAGES / "dealer_inspire_page1.html"
).read_text()  # "Page 1 of 2": needs the site's pager
DI_PAGE = DI_MULTI.replace("Page 1 of 2", "Page 1 of 1")  # single page: complete over plain HTTP
DI_VDP = (PAGES / "dealer_inspire_vdp.html").read_text()
DC_PAGE = (PAGES / "dealer_com_page1.html").read_text()
ROBOTS = "User-agent: *\nDisallow: /secret/\n"


@dataclass
class FakeHttp:
    """Serves canned responses by URL prefix; records every request. GET only, like the real one."""

    routes: dict[str, tuple[int, str | None]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def get(
        self, url: str, dealer: Dealer, *, allow_sticker: bool = False, sleep: bool = True
    ) -> HttpResult:
        self.calls.append(url)
        matches = [(prefix, pair) for prefix, pair in self.routes.items() if url.startswith(prefix)]
        if matches:
            _prefix, (status, body) = max(matches, key=lambda item: len(item[0]))
            err = (
                None
                if status < 400
                else (
                    ReasonCode.HTTP_403
                    if status == 403
                    else ReasonCode.HTTP_404
                    if status == 404
                    else ReasonCode.TIMEOUT
                )
            )
            return HttpResult(
                url=url,
                final_url=url,
                status=status,
                body=body,
                latency_ms=1,
                attempts=1,
                error=err,
            )
        return HttpResult(
            url=url,
            final_url=url,
            status=404,
            body=None,
            latency_ms=1,
            attempts=1,
            error=ReasonCode.HTTP_404,
        )

    async def aclose(self) -> None:
        return None


def dealer(
    did: str, host: str, platform: Platform, start: str | None, prefixes: tuple[str, ...]
) -> Dealer:
    return Dealer(
        dealer_id=did,
        center_number=did,
        name=did.upper(),
        city="Austin",
        hostname=host,
        start_url=start,
        path_prefixes=prefixes,
        platform=platform,
        terms_status=TermsStatus.OWNER_ACCEPTED_RISK if start else TermsStatus.PENDING,
    )


DI = dealer(
    "di",
    "www.di-test.com",
    Platform.DEALER_INSPIRE,
    "https://www.di-test.com/new-vehicles/x5/",
    ("/new-vehicles/", "/inventory/"),
)
DC = dealer(
    "dc",
    "www.dc-test.com",
    Platform.DEALER_COM,
    "https://www.dc-test.com/new-inventory/index.htm?model=X5",
    ("/new-inventory/",),
)
NOSRC = dealer("nosrc", "www.sewell-test.com", Platform.UNKNOWN, None, ())


def registry(*dealers: Dealer) -> Registry:
    return Registry(version="test-1", dealers=tuple(dealers), config_hash="testhash")


def di_routes(
    page: str = DI_PAGE, *, vdp: str | None = DI_VDP, listing_status: int = 200
) -> dict[str, tuple[int, str | None]]:
    r: dict[str, tuple[int, str | None]] = {
        "https://www.di-test.com/robots.txt": (200, ROBOTS),
        "https://www.di-test.com/new-vehicles/x5/": (
            listing_status,
            page if listing_status == 200 else None,
        ),
    }
    if vdp is not None:
        r["https://www.di-test.com/inventory/"] = (200, vdp)
    return r


def dc_routes() -> dict[str, tuple[int, str | None]]:
    return {
        "https://www.dc-test.com/robots.txt": (404, None),
        "https://www.dc-test.com/new-inventory/": (200, DC_PAGE),
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    st = Settings(
        data_dir=tmp_path / "data",
        per_host_min_pause_s=0.0,
        per_host_max_pause_s=0.0,
        concurrency_dealers=2,
        digest_recipients=[],
    )
    st.ensure_dirs()
    eng = make_engine(st.db_path)
    migrate.upgrade(eng)
    fake = FakeHttp()
    monkeypatch.setattr(runmod, "HttpFetcher", lambda **_: fake)
    return st, eng, fake


def go(
    env,
    reg: Registry,
    *,
    now: datetime,
    trigger: Trigger = Trigger.CRON,
    dealer_ids: list[str] | None = None,
    notify: bool = True,
) -> runmod.RunReport:  # type: ignore[no-untyped-def]
    st, eng, _ = env
    return asyncio.run(
        runmod.execute_run(
            eng,
            st,
            trigger=trigger,
            dealer_ids=dealer_ids,
            notify=notify,
            registry=reg,
            use_browser=False,
            now=now,
        )
    )


T1 = datetime(2026, 10, 19, 11, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
T3 = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)


def test_gate_happy_path_publishes_and_persists(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = {**di_routes(), **dc_routes()}
    rep = go(env, registry(DI, DC, NOSRC), now=T1)
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers == {
        "di": DealerStatus.REPORTED,
        "dc": DealerStatus.REPORTED,
        "nosrc": DealerStatus.NO_SOURCE,
    }
    assert rep.eligible == {"di": 2, "dc": 1, "nosrc": 0}
    with read_conn(eng) as c:
        obs = repos.observations_for_run(c, rep.run_id)
        assert {o["vin"] for o in obs} == {
            "5UX23EU03T9529691",
            "5UX23EU05T9472216",
            "5UX23EU0XT9513990",
        }
        by = {o["vin"]: o for o in obs}
        assert by["5UX23EU03T9529691"]["comparison_price_cents"] == 79_325_00
        assert by["5UX23EU03T9529691"]["dap_status"] == "YES"  # from the detail page
        assert by["5UX23EU03T9529691"]["m_sport"] == 1
        assert by["5UX23EU05T9472216"]["comparison_price_cents"] is None  # MSRP-only stays NULL
        assert by["5UX23EU0XT9513990"]["comparison_price_cents"] == 78_400_00
        excl = {e["reason_code"] for e in repos.exclusions_for_run(c, rep.run_id)}
        assert {"POWERTRAIN_OTHER", "NOT_MY2026", "USED_OR_CPO", "NOT_XDRIVE40I"} <= excl
        lc = repos.lifecycle_for_run(c, rep.run_id)
        assert all(e["to_state"] == LifecycleState.ACTIVE.value for e in lc) and len(lc) == 3
        # Every attempt was a GET and robots was fetched for every attempted dealer.
        assert all(a["method"] == "GET" for a in c.execute(select(s.fetch_attempts)).mappings())
        assert {r["dealer_id"] for r in c.execute(select(s.robots_snapshots)).mappings()} == {
            "di",
            "dc",
        }
    assert any(u.endswith("/robots.txt") for u in fake.calls)


def test_gate_no_evasion_no_post_no_blocked_paths(env) -> None:  # type: ignore[no-untyped-def]
    _st, _eng, fake = env
    robots = "User-agent: *\nDisallow: /new-vehicles/\n"
    fake.routes = {**di_routes(), "https://www.di-test.com/robots.txt": (200, robots)}
    rep = go(env, registry(DI), now=T1)
    assert rep.dealers["di"] is DealerStatus.NOT_REPORTED
    assert rep.reasons["di"] is ReasonCode.ROBOTS_DISALLOWED
    # robots said no: the listing URL was never requested.
    assert not any("/new-vehicles/x5/" in u for u in fake.calls)


def test_gate_partial_read_keeps_page_one_but_emits_no_missing(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    # Day 2: the page says "1 of 2" and in-page paging needs the browser, which is off here.
    fake.routes = di_routes(DI_MULTI)
    r2 = go(env, registry(DI), now=T2)
    assert r2.dealers["di"] is DealerStatus.PARTIAL
    assert r2.reasons["di"] is ReasonCode.PAGINATION_INCOMPLETE
    with read_conn(eng) as c:
        dr = repos.dealer_runs_for_run(c, r2.run_id)[0]
        assert dr["pages_read"] == 1 and dr["pages_expected"] == 2
        assert len(repos.observations_for_run(c, r2.run_id)) == 2  # page 1 rows are kept
        assert repos.lifecycle_for_run(c, r2.run_id) == []  # but PARTIAL never emits missing/added
        assert repos.price_events_for_run(c, r2.run_id) == []


def test_gate_dealer_failure_is_gap_not_zero_and_never_emits_missing(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    r1 = go(env, registry(DI), now=T1)
    assert r1.eligible["di"] == 2
    fake.routes = di_routes(listing_status=503)
    r2 = go(env, registry(DI), now=T2)
    assert r2.status is RunStatus.PUBLISHED  # run publishes; the dealer is a gap
    assert r2.dealers["di"] is DealerStatus.NOT_REPORTED
    with read_conn(eng) as c:
        assert (
            repos.lifecycle_for_run(c, r2.run_id) == []
        )  # no MISSING_UNVERIFIED from a failed read
        assert repos.observations_for_run(c, r2.run_id) == []
        dr = repos.dealer_runs_for_run(c, r2.run_id)[0]
        assert dr["eligible_count"] == 0 and dr["status"] == "NOT_REPORTED"
        # Trend series shows a point for day 1 and a gap (no REPORTED row) for day 2.
        series = repos.series_by_dealer(c, 30)
        assert [x["status"] for x in series] == ["REPORTED", "NOT_REPORTED"]


def test_gate_missing_is_unverified_and_relist_restores(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    r1 = go(env, registry(DI), now=T1)
    # Day 2: the first car is gone from the page (explicit, dealer REPORTED).
    gone = DI_PAGE.replace("5UX23EU03T9529691", "5UX23EU03T9529690")  # bad checksum -> excluded
    fake.routes = di_routes(gone)
    r2 = go(env, registry(DI), now=T2)
    assert r2.dealers["di"] is DealerStatus.REPORTED
    with read_conn(eng) as c:
        lc = {e["vin"]: e for e in repos.lifecycle_for_run(c, r2.run_id)}
        assert lc["5UX23EU03T9529691"]["to_state"] == "MISSING_UNVERIFIED"
        assert lc["5UX23EU03T9529691"]["reason"] != "sold"
        assert r2.compare["di"].missing == 1 and r2.compare["di"].added == 0
    # Day 3: it is back -> RELISTED, not "added".
    fake.routes = di_routes()
    r3 = go(env, registry(DI), now=T3)
    with read_conn(eng) as c:
        lc = {e["vin"]: e for e in repos.lifecycle_for_run(c, r3.run_id)}
        assert lc["5UX23EU03T9529691"]["to_state"] == "RELISTED"
    assert r3.compare["di"].relisted == 1 and r3.compare["di"].added == 0
    assert r1.run_id != r2.run_id != r3.run_id


def test_gate_price_change_same_vin_same_kind_only(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    cheaper = DI_PAGE.replace(
        '<span class="price">$79,325</span>', '<span class="price">$78,325</span>'
    )
    fake.routes = di_routes(cheaper)
    r2 = go(env, registry(DI), now=T2)
    with read_conn(eng) as c:
        pe = repos.price_events_for_run(c, r2.run_id)
        assert len(pe) == 1
        assert (
            pe[0]["vin"] == "5UX23EU03T9529691"
            and pe[0]["event"] == "DROP"
            and pe[0]["delta_cents"] == -1_000_00
        )
    # MSRP-only VIN never produced a price event (NULL vs NULL).
    assert all(p["vin"] != "5UX23EU05T9472216" for p in pe)


def test_gate_silent_zero_is_anomaly_not_zero(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    # Page renders with the inventory container but no cards and no explicit empty text.
    silent = "<html><body><div class='inventory'><div class='results-pagination'></div></div></body></html>"
    fake.routes = di_routes(silent, vdp=None)
    r2 = go(env, registry(DI), now=T2)
    assert r2.dealers["di"] is not DealerStatus.REPORTED or r2.eligible["di"] == 0
    with read_conn(eng) as c:
        dr = repos.dealer_runs_for_run(c, r2.run_id)[0]
        assert dr["status"] != "REPORTED" or dr["anomaly"] == 1
        assert (
            repos.lifecycle_for_run(c, r2.run_id) == []
        )  # a suspected anomaly emits no missing events


def test_gate_idempotent_rerun_and_admission(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    go(env, registry(DI), now=T1)
    with pytest.raises(runmod.RunRejected):
        go(env, registry(DI), now=T1.replace(hour=12))  # second scheduled run same civil day
    r_manual = go(
        env, registry(DI), now=T1.replace(hour=12), trigger=Trigger.MANUAL_DEALER, dealer_ids=["di"]
    )
    assert r_manual.status is RunStatus.PUBLISHED
    with pytest.raises(runmod.RunRejected):
        go(env, registry(DI), now=T1, dealer_ids=["does-not-exist"])
    with read_conn(eng) as c:
        n = c.execute(
            text("select count(*) from observations where vin='5UX23EU03T9529691'")
        ).scalar()
        assert n == 2  # one per run; no duplicate rows within a run


def test_gate_crashed_run_is_reclaimed_not_a_permanent_block(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    # Simulate a process that died mid-run yesterday: a RUNNING row with no end.
    from carshop.storage.db import write_tx
    from carshop.util import iso

    with write_tx(eng) as c:
        repos.create_run(
            c,
            run_id="dead-run",
            snapshot_date="2026-10-18",
            trigger="cron",
            config_version="x",
            model_status="down",
            started_at=iso(T1.replace(day=18)),
        )
    rep = go(env, registry(DI), now=T1)
    assert rep.status is RunStatus.PUBLISHED
    with read_conn(eng) as c:
        dead = repos.get_run(c, "dead-run")
        assert (
            dead is not None and dead["status"] == "FAILED" and dead["label"] == "reclaimed_stale"
        )
        audits = list(
            c.execute(
                select(s.audit_events).where(s.audit_events.c.entity_id == "dead-run")
            ).mappings()
        )
        assert len(audits) == 1 and audits[0]["operator_id"] == "system"
    # A run that is genuinely still in progress (started seconds ago) is NOT reclaimed.
    with write_tx(eng) as c:
        repos.create_run(
            c,
            run_id="live-run",
            snapshot_date="2026-10-20",
            trigger="cron",
            config_version="x",
            model_status="down",
            started_at=iso(T2),
        )
        repos.claim_dealer_run(c, "live-run", "di")
    with pytest.raises(runmod.RunRejected):
        go(
            env,
            registry(DI),
            now=T2.replace(minute=5),
            trigger=Trigger.MANUAL_DEALER,
            dealer_ids=["di"],
        )


def test_gate_unknown_is_not_no_and_digest_written_once(env) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    fake.routes = di_routes(vdp=None)  # detail page 404 -> features stay UNKNOWN
    rep = go(env, registry(DI), now=T1)
    with read_conn(eng) as c:
        o = {x["vin"]: x for x in repos.observations_for_run(c, rep.run_id)}
        assert o["5UX23EU03T9529691"]["dap_status"] == "UNKNOWN"
        feats = repos.features_for_observations(c, [int(o["5UX23EU03T9529691"]["observation_id"])])
        assert all(f["status"] in ("UNKNOWN", "YES") for fl in feats.values() for f in fl)
        notes = list(c.execute(select(s.notification_events)).mappings())
        assert len(notes) == 1 and notes[0]["sent_at"] is not None
    digests = list((st.data_dir / "digests").glob("*.txt"))
    assert len(digests) == 1
    body = digests[0].read_text()
    assert "missing" in body.lower() or "no change" in body.lower() or "added" in body.lower()
    assert "sold" not in body.lower()


def test_gate_detail_page_odometer_excludes_demo(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    # The card shows no odometer for VIN ...691; the detail page says 9,546 miles.
    card_no_miles = DI_PAGE.replace(',"mileage":"6"', "").replace("<li>Mileage: 6</li>", "")
    vdp_demo = DI_VDP.replace("Mileage: 6", "Mileage: 9,546")
    fake.routes = di_routes(card_no_miles, vdp=vdp_demo)
    rep = go(env, registry(DI), now=T1)
    assert rep.dealers["di"] is DealerStatus.REPORTED
    with read_conn(eng) as c:
        vins = {o["vin"] for o in repos.observations_for_run(c, rep.run_id)}
        assert "5UX23EU03T9529691" not in vins
        excl = {e["vin"]: e for e in repos.exclusions_for_run(c, rep.run_id)}
        assert excl["5UX23EU03T9529691"]["reason_code"] == "DEMO_MILEAGE"
        assert "9546" in excl["5UX23EU03T9529691"]["evidence_quote"]


def test_gate_backup_verified_and_snapshot_stored(env) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    fake.routes = di_routes()
    rep = go(env, registry(DI), now=T1)
    assert rep.backup_path and Path(rep.backup_path).exists()
    snaps = list((st.data_dir / "snapshots" / rep.run_id).glob("*.html.gz"))
    assert snaps, "claim-check HTML snapshots must be written"
    with read_conn(eng) as c:
        assert all(
            a["storage_uri"]
            for a in c.execute(
                select(s.fetch_attempts).where(s.fetch_attempts.c.http_status == 200)
            ).mappings()
            if "robots" not in a["url"]
        )


def test_gate_v13_no_source_is_counted_in_the_denominator(env) -> None:  # type: ignore[no-untyped-def]
    """v1.3 §4: a locator dealer with no discoverable inventory URL is NO_SOURCE with a reason,
    is never fetched, and still counts against the statewide coverage claim."""
    from carshop.pipeline.notify import build_digest

    _st, eng, fake = env
    fake.routes = di_routes()
    rep = go(env, registry(DI, NOSRC), now=T1)
    assert rep.dealers["nosrc"] is DealerStatus.NO_SOURCE
    assert rep.reasons["nosrc"] is ReasonCode.NO_INVENTORY_URL
    assert not any("sewell-test" in u for u in fake.calls)  # nothing fetched for it
    with read_conn(eng) as c:
        d = build_digest(c, rep.run_id)
    assert "REPORTED: 1 of 2 on the registry (50%" in d.body
    assert "NOT statewide complete" in d.body
    assert "NO SOURCE" in d.body and "NOSRC" in d.body


def test_gate_v13_branch_timeout_keeps_pages_read_and_is_partial(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """§7.4: the per-dealer branch timeout keeps the pages already read; the dealer ends
    PARTIAL / TIMEOUT, not NOT_REPORTED, and emits no removal deltas."""
    st, eng, _fake = env

    class SlowHttp(FakeHttp):
        async def get(self, url, dealer, *, allow_sticker=False, sleep=True):  # type: ignore[no-untyped-def]
            if "/inventory/" in url:  # detail pages hang past the branch budget
                await asyncio.sleep(5)
            return await super().get(url, dealer, allow_sticker=allow_sticker, sleep=sleep)

    slow = SlowHttp(routes=di_routes())
    monkeypatch.setattr(runmod, "HttpFetcher", lambda **_: slow)
    st.branch_timeout_s = 1
    rep = go((st, eng, slow), registry(DI), now=T1)
    assert rep.dealers["di"] is DealerStatus.PARTIAL
    assert rep.reasons["di"] is ReasonCode.TIMEOUT
    with read_conn(eng) as c:
        dr = repos.dealer_runs_for_run(c, rep.run_id)[0]
        assert dr["pages_read"] == 1
        assert len(repos.observations_for_run(c, rep.run_id)) == 2  # page-1 units kept
        assert repos.lifecycle_for_run(c, rep.run_id) == []


def test_gate_url_pagination_stops_on_a_repeating_next_link_and_dedupes_rows(env) -> None:  # type: ignore[no-untyped-def]
    """bmwpb.com 2026-10-20: every page's rel=next pointed at the same URL, so the branch read
    page 1 thirty times and wrote each exclusion thirty times. A repeating next is a loop:
    stop, and record one row per VIN."""
    _st, eng, fake = env
    gen = (PAGES / "generic_jsonld_page1.html").read_text()
    start = "https://www.gen-test.com/inventory/new/bmw/x5"
    page = gen.replace("https://www.groupstore-test.com", "https://www.gen-test.com")
    page1 = page.replace("x5?page=2", "x5?page=2")  # 1 -> 2
    page2 = page  # 2 -> 2 again (self link)
    fake.routes = {
        "https://www.gen-test.com/robots.txt": (404, None),
        start + "?page=2": (200, page2),
        start: (200, page1),
        "https://www.gen-test.com/viewdetails/": (
            200,
            "<html><body><main>" + "x" * 300 + "</main></body></html>",
        ),
    }
    gd = dealer("gen", "www.gen-test.com", Platform.OTHER, start, ("/inventory/",))
    rep = go(env, registry(gd), now=T1)
    assert rep.dealers["gen"] is DealerStatus.REPORTED
    listing_calls = [u for u in fake.calls if "/inventory/new/bmw/x5" in u]
    assert listing_calls == [start, start + "?page=2"]  # not thirty
    with read_conn(eng) as c:
        dr = repos.dealer_runs_for_run(c, rep.run_id)[0]
        assert dr["pages_read"] == 2
        excl = repos.exclusions_for_run(c, rep.run_id)
        assert len({e["vin"] for e in excl}) == len(excl)  # one exclusion row per VIN
        assert len(repos.observations_for_run(c, rep.run_id)) == 1  # ...691 xDrive40i once


def test_gate_page_platform_overrides_registry_platform(env) -> None:  # type: ignore[no-untyped-def]
    """Cardenas 2026-10-20: the registry said `unknown` (homepage markup), the inventory page
    is DealerFire. The page is the authority: the dedicated adapter is used and the dealer is
    REPORTED instead of LAYOUT_UNKNOWN."""
    _st, eng, fake = env
    df = (PAGES / "dealerfire_page1.html").read_text()
    start = "https://www.df-test.com/new-bmw-x5-harlingen-tx"
    fake.routes = {
        "https://www.df-test.com/robots.txt": (404, None),
        start: (200, df),
        "https://www.df-test.com/vehicle-details/": (
            200,
            "<html><body><main>" + "x" * 300 + "</main></body></html>",
        ),
    }
    d = dealer(
        "df",
        "www.df-test.com",
        Platform.UNKNOWN,
        start,
        ("/new-bmw-x5-harlingen-tx/", "/vehicle-details/"),
    )
    rep = go(env, registry(d), now=T1)
    assert rep.dealers["df"] is DealerStatus.REPORTED
    assert rep.eligible == {"df": 1}  # the xDrive40i; the 2027 unit is NOT_MY2026
    with read_conn(eng) as c:
        dr = repos.dealer_runs_for_run(c, rep.run_id)[0]
        assert dr["adapter_version"].startswith("dealerfire-")
        obs = repos.observations_for_run(c, rep.run_id)[0]
        assert obs["vin"] == "5UX23EU05T9488934"
        assert (
            obs["comparison_price_cents"] == 76_325_00
        )  # "Cardenas Price" is the advertised figure


def _dc_page(vin: str, total: int) -> str:
    return f"""<!doctype html><html><head>
<script type="application/ld+json">
{{"@type":"CollectionPage","description":"Browse our inventory of {total} new BMW X5 vehicles for sale.",
 "about":{{"offers":{{"itemOffered":[{{"@type":"Car","name":"New 2026 BMW X5 xDrive40i",
 "vehicleIdentificationNumber":"{vin}","vehicleModelDate":2026,"model":"X5",
 "itemCondition":"https://schema.org/NewCondition","offers":{{"price":"78000"}}}}]}}}}}}
</script></head><body><div class="inventory-results"><ul><li class="vehicle-card">
<h2>New 2026 BMW X5 xDrive40i</h2><div>VIN: {vin}</div>
<dl class="pricing-detail"><dt>Selling Price</dt><dd>$78,000</dd></dl>
</li></ul></div></body></html>"""


class _PagedHttp(FakeHttp):
    def __init__(self, total: int, *, identical: bool) -> None:
        super().__init__()
        self.total = total
        self.identical = identical

    async def get(
        self, url: str, dealer: Dealer, *, allow_sticker: bool = False, sleep: bool = True
    ) -> HttpResult:
        from urllib.parse import parse_qsl, urlsplit

        from tests.conftest import make_vin

        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return HttpResult(url, url, 404, None, 1, 1, ReasonCode.HTTP_404)
        start = dict(parse_qsl(urlsplit(url).query)).get("start", "0")
        page = int(start) // 24 + 1 if start.isdigit() else 1
        vin = make_vin(serial="100001") if self.identical else make_vin(serial=f"{page:06d}")
        return HttpResult(url, url, 200, _dc_page(vin, self.total), 1, 1, None)


def test_C2_page_cap_is_partial(env, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    http = _PagedHttp(total=31 * 24, identical=False)
    monkeypatch.setattr(runmod, "HttpFetcher", lambda **_: http)
    rep = go(env, registry(DC), now=T1, dealer_ids=["dc"])
    listing = [url for url in http.calls if not url.endswith("/robots.txt")]
    assert len(listing) == 30
    assert rep.dealers["dc"] is DealerStatus.PARTIAL
    assert rep.reasons["dc"] is ReasonCode.PAGINATION_INCOMPLETE


def test_C3_identical_pages_stop_after_two_requests(env, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    http = _PagedHttp(total=31 * 24, identical=True)
    monkeypatch.setattr(runmod, "HttpFetcher", lambda **_: http)
    rep = go(env, registry(DC), now=T1, dealer_ids=["dc"])
    listing = [url for url in http.calls if not url.endswith("/robots.txt")]
    assert len(listing) <= 2
    assert rep.dealers["dc"] is DealerStatus.PARTIAL
    assert rep.reasons["dc"] is ReasonCode.PAGINATION_INCOMPLETE


def test_H2_robots_5xx_is_not_reported(env) -> None:  # type: ignore[no-untyped-def]
    _st, _eng, fake = env
    fake.routes = {
        "https://www.dc-test.com/robots.txt": (503, None),
        "https://www.dc-test.com/new-inventory/": (200, DC_PAGE),
    }
    rep = go(env, registry(DC), now=T1, dealer_ids=["dc"])
    assert rep.dealers["dc"] is DealerStatus.NOT_REPORTED
    assert rep.reasons["dc"] is ReasonCode.ROBOTS_DISALLOWED


def test_H2_disallowed_detail_skips_enrichment(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes()
    fake.routes["https://www.di-test.com/robots.txt"] = (
        200,
        "User-agent: *\nDisallow: /inventory/\n",
    )
    rep = go(env, registry(DI), now=T1, dealer_ids=["di"])
    assert rep.dealers["di"] is DealerStatus.REPORTED
    assert rep.eligible["di"] > 0
    assert not [url for url in fake.calls if "/inventory/" in url]
    with read_conn(eng) as conn:
        decisions = [
            row[0]
            for row in conn.execute(
                select(s.fetch_attempts.c.robots_decision).where(
                    s.fetch_attempts.c.run_id == rep.run_id
                )
            )
        ]
    assert "disallowed" in decisions


def test_H3_store_failure_does_not_abort_the_run(env, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = {**di_routes(), **dc_routes()}

    original = repos.store_dealer_result

    def boom(
        conn: object, run_id: str, result: object, *, baseline_run_id: str | None
    ) -> dict[str, int]:
        from carshop.domain.models import DealerResult

        assert isinstance(result, DealerResult)
        if result.dealer_id == "dc":
            raise RuntimeError("store failed")
        return original(
            conn,
            run_id,
            result,
            baseline_run_id=baseline_run_id,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(runmod.repos, "store_dealer_result", boom)
    rep = go(env, registry(DI, DC), now=T1)
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers["di"] is DealerStatus.REPORTED
    assert rep.dealers["dc"] is DealerStatus.NOT_REPORTED
    with read_conn(eng) as conn:
        vins = {
            row["vin"] for row in repos.observations_for_run(conn, rep.run_id, validated_only=False)
        }
        assert vins
        status = conn.execute(
            select(s.dealer_runs.c.status).where(
                s.dealer_runs.c.run_id == rep.run_id, s.dealer_runs.c.dealer_id == "dc"
            )
        ).scalar()
    assert status == DealerStatus.NOT_REPORTED.value


def test_H4_in_flight_dealer_stays_locked(env) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import update

    from carshop.pipeline.run import RunRejected
    from carshop.storage.db import write_tx
    from carshop.util import iso

    _st, eng, _fake = env
    locked_a = dealer(
        "lock-a",
        "www.a-test.com",
        Platform.DEALER_COM,
        "https://www.a-test.com/new-inventory/index.htm",
        ("/new-inventory/",),
    )
    locked_b = dealer(
        "lock-b",
        "www.b-test.com",
        Platform.DEALER_COM,
        "https://www.b-test.com/new-inventory/index.htm",
        ("/new-inventory/",),
    )
    other = dealer("lock-c", "www.c-test.com", Platform.UNKNOWN, None, ())
    with write_tx(eng) as conn:
        repos.upsert_dealers(conn, (locked_a, locked_b, other))
        repos.create_run(
            conn,
            run_id="stuck-run",
            snapshot_date="2026-10-20",
            trigger="manual_date",
            config_version="t",
            model_status="down",
            started_at=iso(T2),
        )
        repos.claim_dealer_run(conn, "stuck-run", "lock-a")
        repos.claim_dealer_run(conn, "stuck-run", "lock-b")
        conn.execute(
            update(s.dealer_runs)
            .where(s.dealer_runs.c.run_id == "stuck-run", s.dealer_runs.c.dealer_id == "lock-a")
            .values(status=DealerStatus.REPORTED.value)
        )
    with pytest.raises(RunRejected):
        go(env, registry(locked_a, locked_b), now=T2, dealer_ids=["lock-b"])
    rep = go(env, registry(other), now=T2, dealer_ids=["lock-c"])
    assert rep.status is RunStatus.PUBLISHED
    assert rep.dealers["lock-c"] is DealerStatus.NO_SOURCE


def test_H8_conflict_is_pending_until_approved(env) -> None:  # type: ignore[no-untyped-def]
    from carshop.config.loaders import load_preferences
    from carshop.review.decisions import approve
    from carshop.storage.db import write_tx
    from carshop.web.views import load_units

    _st, eng, fake = env
    page = DI_PAGE.replace('"year":"2026"', '"year":"2025"', 1)
    fake.routes = di_routes(page)
    rep = go(env, registry(DI), now=T1, dealer_ids=["di"], notify=False)
    vin = "5UX23EU03T9529691"
    with read_conn(eng) as conn:
        held = {
            row["vin"] for row in repos.observations_for_run(conn, rep.run_id, validated_only=False)
        }
        shown = {row["vin"] for row in repos.observations_for_run(conn, rep.run_id)}
        item = conn.execute(
            select(s.review_items.c.item_id).where(
                s.review_items.c.run_id == rep.run_id, s.review_items.c.vin == vin
            )
        ).scalar()
        staging = conn.execute(
            select(s.inventory_staging.c.status).where(
                s.inventory_staging.c.run_id == rep.run_id, s.inventory_staging.c.vin == vin
            )
        ).scalar()
    assert vin in held and vin not in shown
    assert staging == "PENDING_REVIEW"
    assert item is not None
    with write_tx(eng) as conn:
        approve(conn, int(item), operator_id="owner", reason="page year confirmed against the VIN")
        units = load_units(conn, rep.run_id, load_preferences())
    assert vin in {unit.row["vin"] for unit in units}


def test_M1_M3_replay_does_not_duplicate_and_correct_versions(env) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from carshop.domain.classify import ClassifyContext, classify
    from carshop.domain.models import DealerResult, FetchAttempt
    from carshop.review.decisions import correct
    from carshop.storage.db import write_tx
    from tests.unit.test_classify import raw

    _st, eng, _fake = env
    ctx = ClassifyContext(
        "di", datetime(2026, 10, 19, tzinfo=UTC), "t", 2026, "23EU", 500, 55_000_00, 120_000_00
    )
    obs = classify(raw(), ctx).observation
    assert obs is not None
    bad = classify(raw(vin="5UX23EU00T9529691"), ctx).exclusion
    assert bad is not None
    attempt = FetchAttempt(
        url="https://www.di-test.com/new-vehicles/x5/",
        final_url=None,
        http_status=200,
        attempt=1,
        latency_ms=1,
        robots_decision="allowed",
        content_hash="abc",
        storage_uri=None,
        error_code=None,
    )
    result = DealerResult(
        dealer_id="di",
        status=DealerStatus.REPORTED,
        reason=None,
        pages_expected=1,
        pages_read=1,
        pages_failed=0,
        raw_count=2,
        eligible_count=1,
        duration_ms=1,
        cost_usd=0.0,
        anomaly=False,
        observations=(obs,),
        exclusions=(bad,),
        attempts=(attempt,),
        review_reasons=((bad.vin or "", bad.code.value),),
    )
    with write_tx(eng) as conn:
        repos.upsert_dealers(conn, (DI,))
        repos.create_run(
            conn,
            run_id="replay-run",
            snapshot_date="2026-10-19",
            trigger="replay",
            config_version="t",
            model_status="down",
        )
        repos.store_dealer_result(conn, "replay-run", result, baseline_run_id=None)
        repos.store_dealer_result(conn, "replay-run", result, baseline_run_id=None)
        counts = {
            name: conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
            for name in ("observations", "exclusions", "review_items", "fetch_attempts")
        }
        new_id = correct(
            conn,
            run_id="replay-run",
            dealer_id="di",
            vin=obs.vin,
            field="comparison_price_cents",
            new_value="7700000",
            operator_id="owner",
            reason="fee was inside the figure",
            evidence="window sticker",
        )
        versions = conn.execute(
            select(s.observations.c.version, s.observations.c.comparison_price_cents).where(
                s.observations.c.run_id == "replay-run", s.observations.c.vin == obs.vin
            )
        ).all()
        shown = repos.observations_for_run(conn, "replay-run")
    assert counts == {"observations": 1, "exclusions": 1, "review_items": 1, "fetch_attempts": 1}
    assert new_id
    assert {(row[0], row[1]) for row in versions} == {(1, 78_325_00), (2, 77_000_00)}
    assert shown[0]["comparison_price_cents"] == 77_000_00
    assert shown[0]["extraction_method"] == "human"


def test_M2_flags_are_not_written_onto_the_observation(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    fake.routes = di_routes(DI_PAGE)
    rep = go(env, registry(DI), now=T1, dealer_ids=["di"], notify=False)
    from carshop.storage.db import write_tx

    vin = "5UX23EU03T9529691"
    with read_conn(eng) as conn:
        before = conn.execute(
            select(s.observations.c.flags).where(
                s.observations.c.run_id == rep.run_id, s.observations.c.vin == vin
            )
        ).scalar()
    with write_tx(eng) as conn:
        repos.add_flag_to_observations(conn, rep.run_id, vin, "TRANSFER_SUSPECTED")
    with read_conn(eng) as conn:
        after = conn.execute(
            select(s.observations.c.flags).where(
                s.observations.c.run_id == rep.run_id, s.observations.c.vin == vin
            )
        ).scalar()
        visible = repos.observations_for_run(conn, rep.run_id, validated_only=False)
    assert after == before
    flagged = next(row for row in visible if row["vin"] == vin)
    assert "TRANSFER_SUSPECTED" in flagged["flags"]


def test_M4_pending_price_emits_no_price_or_missing_event(env) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from carshop.domain.classify import ClassifyContext, classify
    from carshop.domain.models import DealerResult
    from carshop.pipeline.compare import compare_dealer
    from carshop.storage.db import write_tx
    from tests.unit.test_classify import raw

    _st, eng, _fake = env
    ctx = ClassifyContext(
        "di", datetime(2026, 10, 19, tzinfo=UTC), "t", 2026, "23EU", 500, 55_000_00, 120_000_00
    )
    first = classify(raw(), ctx).observation
    assert first is not None
    second = classify(raw(price_pairs=[("Our Price", "$7,832")]), ctx)
    assert second.observation is not None and second.needs_review == "PRICE_OUTSIDE_BAND"
    base = DealerResult(
        dealer_id="di",
        status=DealerStatus.REPORTED,
        reason=None,
        pages_expected=1,
        pages_read=1,
        pages_failed=0,
        raw_count=1,
        eligible_count=1,
        duration_ms=1,
        cost_usd=0.0,
        anomaly=False,
        observations=(first,),
        exclusions=(),
        attempts=(),
        review_reasons=(),
    )
    held = DealerResult(
        dealer_id="di",
        status=DealerStatus.REPORTED,
        reason=None,
        pages_expected=1,
        pages_read=1,
        pages_failed=0,
        raw_count=1,
        eligible_count=1,
        duration_ms=1,
        cost_usd=0.0,
        anomaly=False,
        observations=(second.observation,),
        exclusions=(),
        attempts=(),
        review_reasons=((second.observation.vin, second.needs_review),),
    )
    with write_tx(eng) as conn:
        repos.upsert_dealers(conn, (DI,))
        repos.create_run(
            conn,
            run_id="price-a",
            snapshot_date="2026-10-19",
            trigger="manual_date",
            config_version="t",
            model_status="down",
        )
        repos.store_dealer_result(conn, "price-a", base, baseline_run_id=None)
        repos.finish_run(conn, "price-a", RunStatus.PUBLISHED, 0)
        compare_dealer(conn, "price-a", base, None)
        repos.create_run(
            conn,
            run_id="price-b",
            snapshot_date="2026-10-20",
            trigger="manual_date",
            config_version="t",
            model_status="down",
        )
        summary = compare_dealer(conn, "price-b", held, "price-a")
        prices = conn.execute(text("SELECT COUNT(*) FROM price_events")).scalar()
        missing = conn.execute(
            text("SELECT COUNT(*) FROM listing_lifecycle WHERE to_state = 'MISSING_UNVERIFIED'")
        ).scalar()
    assert summary.price_changes == 0
    assert summary.missing == 0
    assert prices == 0
    assert missing == 0


def test_M8_missing_vin_is_partial_not_checksum(env) -> None:  # type: ignore[no-untyped-def]
    _st, eng, fake = env
    page = DI_PAGE.replace('"vin":"5UX23EU03T9529691"', '"vin":""', 1)
    fake.routes = di_routes(page)
    rep = go(env, registry(DI), now=T1, dealer_ids=["di"], notify=False)
    assert rep.dealers["di"] is DealerStatus.PARTIAL
    assert rep.reasons["di"] is ReasonCode.PARSER_FAILED
    with read_conn(eng) as conn:
        reasons = [
            row[0]
            for row in conn.execute(
                select(s.exclusions.c.reason_code).where(s.exclusions.c.run_id == rep.run_id)
            )
        ]
    assert "VIN_CHECKSUM_FAIL" not in reasons


def test_L2_replay_never_sends_the_digest(env, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    sent: list[str] = []
    monkeypatch.setattr(runmod, "deliver_digest", lambda *_a, **_k: sent.append("sent") or {})
    rep = go(env, registry(NOSRC), now=T1, trigger=Trigger.REPLAY, notify=True)
    assert rep.status is RunStatus.PUBLISHED
    assert sent == []
