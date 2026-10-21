# ruff: noqa: N802
"""Review 2026-10-20 registry gaps: G1-G4."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path

import httpx
import pytest
import respx

from carshop.config.settings import Settings
from carshop.domain.codes import DiscoveryReason, Platform, ReasonCode, SourceKind
from carshop.domain.models import Dealer, RawListing
from carshop.fetch.http import HttpFetcher, _is_tls
from carshop.fetch.politeness import HostGate
from carshop.registry.build import compose
from carshop.registry.discovery import (
    Discoverer,
    Discovery,
    probe_dealer,
    rank_homepage_links,
    strip_inventory_filters,
    x5_link_score,
    x5_share,
)
from carshop.storage import migrate, repos
from carshop.storage.db import make_engine, write_tx
from carshop.storage.schema import dealer_runs, dealers, observations, runs, units
from carshop.web.views import locator_unreachable
from tests.unit.test_registry import NOW, _snapshot

PAGES = Path(__file__).resolve().parents[1] / "golden" / "pages"


def test_G1_connect_error_is_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    slept: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("carshop.fetch.http.resolve_and_check", lambda url: ["1.1.1.1"])
    monkeypatch.setattr("carshop.fetch.http.asyncio.sleep", _no_sleep)

    async def run() -> tuple[object, Discovery]:
        dealer = probe_dealer("dead.example")
        with respx.mock:
            respx.get("https://dead.example/").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            http = HttpFetcher(user_agent="carshop/0.1", timeout_s=5, backoff_s=(30, 90))
            try:
                result = await http.get("https://dead.example/", dealer)
                found = await Discoverer(
                    Settings(data_dir=tmp_path), http, None, HostGate(0, 0)
                ).discover("dead.example")
            finally:
                await http.aclose()
        return result, found

    result, found = asyncio.run(run())
    assert result.error is ReasonCode.CONNECT_FAILED  # type: ignore[attr-defined]
    assert result.attempts == 1  # type: ignore[attr-defined]
    assert "connection refused" in result.detail  # type: ignore[attr-defined]
    assert slept == []
    assert found.reason is DiscoveryReason.HOMEPAGE_CONNECT_FAILED


def test_G1_tls_failure_is_its_own_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("carshop.fetch.http.resolve_and_check", lambda url: ["1.1.1.1"])

    def boom(request: httpx.Request) -> None:
        # respx replaces __cause__, so the message has to carry the TLS text the way
        # a live httpx.ConnectError does ("certificate verify failed").
        raise httpx.ConnectError("certificate verify failed") from ssl.SSLError(
            "certificate verify failed"
        )

    async def run() -> object:
        with respx.mock:
            respx.get("https://dead.example/").mock(side_effect=boom)
            http = HttpFetcher(user_agent="carshop/0.1", timeout_s=5, backoff_s=(30, 90))
            try:
                return await http.get("https://dead.example/", probe_dealer("dead.example"))
            finally:
                await http.aclose()

    chained = httpx.ConnectError("handshake failed")
    chained.__cause__ = ssl.SSLError("certificate verify failed")
    assert _is_tls(chained)
    result = asyncio.run(run())
    assert result.error is ReasonCode.TLS_FAILED  # type: ignore[attr-defined]
    assert result.attempts == 1  # type: ignore[attr-defined]
    assert "certificate verify failed" in result.detail  # type: ignore[attr-defined]


def test_G2_consecutive_homepage_failures_raise_the_coverage_warning() -> None:
    snap = _snapshot()
    disc = {
        "50923": Discovery(
            "www.bmwofbrazosvalley.com",
            None,
            (),
            Platform.UNKNOWN,
            DiscoveryReason.HOMEPAGE_CONNECT_FAILED,
            "connection refused",
        )
    }
    doc, res = compose(snap, disc, {}, None, NOW)
    plano = next(r for r in doc["dealers"] if r["center_number"] == "50923")
    assert plano["discovery_reason"] == "homepage_connect_failed"
    assert plano["discovery_fail_streak"] == 1
    doc2, _ = compose(snap, disc, {}, res.registry, NOW)
    plano2 = next(r for r in doc2["dealers"] if r["center_number"] == "50923")
    assert plano2["discovery_fail_streak"] == 2
    warning = locator_unreachable(
        [
            {
                "active": True,
                "name": "BMW of Plano",
                "discovery_reason": "homepage_connect_failed",
                "discovery_fail_streak": 2,
            }
        ]
    )
    assert warning is not None
    assert warning["message"] == (
        "locator website unreachable for 2 consecutive refreshes — operator override needed (§16.3)"
    )


def test_G3_centerless_locator_rows_are_retired(tmp_path: Path) -> None:
    eng = make_engine(tmp_path / "app.db")
    migrate.upgrade(eng)
    with write_tx(eng) as conn:
        conn.execute(
            dealers.insert().values(
                dealer_id="bmw-plano",
                center_number="50923",
                name="BMW of Plano",
                city="Plano",
                state="TX",
                hostname="www.bmwofplano.com",
                path_prefixes="[]",
                platform="dealer_inspire",
                terms_status="owner_accepted_risk",
                sticker_hosts="[]",
                active=True,
                source="locator",
            )
        )
        conn.execute(
            dealers.insert().values(
                dealer_id="sewell-bmw-plano",
                center_number=None,
                name="Sewell BMW of Plano",
                city="Plano",
                state="TX",
                hostname="www.sewell.com",
                path_prefixes="[]",
                platform="unknown",
                terms_status="owner_accepted_risk",
                sticker_hosts="[]",
                active=False,
                source="locator",
            )
        )
        conn.execute(
            dealers.insert().values(
                dealer_id="sewell-bmw-grapevine",
                center_number=None,
                name="Sewell BMW of Grapevine",
                city="Grapevine",
                state="TX",
                hostname="www.sewell.com",
                path_prefixes="[]",
                platform="unknown",
                terms_status="owner_accepted_risk",
                sticker_hosts="[]",
                active=False,
                source="locator",
            )
        )
        conn.execute(
            runs.insert().values(
                run_id="r1",
                snapshot_date="2026-10-01",
                trigger="manual",
                status="PUBLISHED",
                started_at="2026-10-01T12:00:00+00:00",
                config_version="t",
                parser_version="t",
                model_status="down",
                git_sha="abc",
            )
        )
        conn.execute(
            dealer_runs.insert().values(
                run_id="r1", dealer_id="sewell-bmw-grapevine", status="NOT_REPORTED"
            )
        )
        conn.execute(
            dealers.insert().values(
                dealer_id="kept-seed",
                center_number=None,
                name="Sewell BMW of Plano historic",
                city="Plano",
                state="TX",
                hostname="www.sewell.com",
                path_prefixes="[]",
                platform="unknown",
                terms_status="owner_accepted_risk",
                sticker_hosts="[]",
                active=True,
                source="locator",
            )
        )
        conn.execute(
            units.insert().values(
                vin="5UXTESTVIN0000001",
                model="X5",
                year=2026,
                ag_code="23EU",
                first_seen_at="2026-10-01T12:00:00+00:00",
                last_seen_at="2026-10-01T12:00:00+00:00",
            )
        )
        conn.execute(
            observations.insert().values(
                run_id="r1",
                dealer_id="kept-seed",
                vin="5UXTESTVIN0000001",
                year=2026,
                model="X5",
                trim="xDrive40i",
                availability="on_lot",
                dap_status="UNKNOWN",
                extraction_method="html",
                adapter_version="t",
                git_sha="abc",
                page_hash="h",
                source_url="https://www.sewell.com/x",
                fetched_at="2026-10-01T12:00:00+00:00",
            )
        )
        repos.retire_centerless_dealers(conn)
        rows = {r["dealer_id"]: r for r in repos.list_dealers(conn)}
    assert "sewell-bmw-plano" not in rows
    assert "sewell-bmw-grapevine" not in rows
    assert rows["bmw-plano"]["center_number"] == "50923"
    kept = rows["kept-seed"]
    assert kept["active"] is False or kept["active"] == 0
    assert kept["source"] == "seed"
    assert kept["notes"] == "superseded by center 50923"
    for row in rows.values():
        if row["source"] == "locator":
            assert row["center_number"]
    with pytest.raises(ValueError, match="no center number"), write_tx(eng) as conn:
        repos.upsert_dealers(
            conn,
            [
                Dealer(
                    dealer_id="orphan",
                    center_number=None,
                    name="Orphan BMW",
                    city="Austin",
                    hostname="www.example.com",
                    start_url=None,
                    path_prefixes=(),
                    platform=Platform.UNKNOWN,
                    source="locator",
                )
            ],
        )


def test_G4_homepage_fixtures_drop_year_and_trim_filters() -> None:
    lubbock, _ = rank_homepage_links(
        (PAGES / "homepage_lubbock_year_filter.html").read_text(),
        "https://www.bmwoflubbock.com/",
    )
    wichita, _ = rank_homepage_links(
        (PAGES / "homepage_wichita_falls_trim_filter.html").read_text(),
        "https://www.bmwwichitafalls.com/",
    )
    lubbock_urls = [u for _, u in lubbock]
    wichita_urls = [u for _, u in wichita]
    assert lubbock_urls
    assert wichita_urls
    assert all("year=" not in u and "2027" not in u for u in lubbock_urls)
    assert all("sDrive40i" not in u and "ModelAndTrim" not in u for u in wichita_urls)
    assert any("model=X5" in u for u in lubbock_urls)
    assert any("Model=X5" in u for u in wichita_urls)
    assert (
        x5_link_score(
            "https://www.bmwoflubbock.com/new-inventory/index.htm?year=2027&make=BMW&model=X5"
        )
        is None
    )
    assert (
        x5_link_score(
            "https://www.bmwwichitafalls.com/searchnew.aspx?Make=BMW&ModelAndTrim=X5:sDrive40i"
        )
        is None
    )
    kept = strip_inventory_filters(
        "https://www.bmwofcollegestation.com/search/x/?cy=77845&md=38&tp=new"
    )
    assert "cy=77845" in kept
    dropped = strip_inventory_filters("https://h/search/?cy=2026&model=X5")
    assert "cy=" not in dropped and "model=X5" in dropped
    listing = RawListing(
        url="https://h/v",
        page_hash="h",
        source_kind=SourceKind.LISTING,
        model="X5",
        year=2027,
        full_text="2027 BMW X5",
    )
    assert x5_share([listing]) == 1.0
