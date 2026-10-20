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
        for prefix, (status, body) in self.routes.items():
            if url.startswith(prefix):
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
        center_number=None,
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
