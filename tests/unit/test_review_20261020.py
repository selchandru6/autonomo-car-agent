# ruff: noqa: N802 - finding ids in the test name are the review's required spelling.
"""Regression tests for doc/reviews/2026-10-20-code-review.md. One test per confirmed finding."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from carshop.config.settings import Settings
from carshop.domain.classify import classify
from carshop.domain.codes import (
    DealerStatus,
    ExclusionCode,
    Feature,
    Flag,
    PriceBasis,
    PriceKind,
    SourceKind,
    Sufficiency,
    Tri,
)
from carshop.domain.features import evaluate_features, merge_feature_sets, resolved_dap
from carshop.domain.lifecycle import Snapshot, compute_delta
from carshop.domain.pricing import build_price_set, classify_price_label
from carshop.fetch.browser import BrowserRenderer
from carshop.reference.data import match_exterior, match_upholstery
from carshop.web.views import _index_features
from tests.conftest import make_vin
from tests.unit.test_classify import raw

ROOT = Path(__file__).resolve().parents[2]
BAND = (55_000_00, 120_000_00)


def test_C1_env_example_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("CARSHOP_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=ROOT / ".env.example")
    assert settings.digest_recipients == []
    assert settings.locator_seed_zips == ["76801", "79901", "78520"]
    csv = Settings(
        _env_file=None,
        digest_recipients="a@x.com,b@y.com",  # type: ignore[arg-type]
        locator_seed_zips="",  # type: ignore[arg-type]
    )
    assert csv.digest_recipients == ["a@x.com", "b@y.com"]
    assert csv.locator_seed_zips == []


def test_C4_vehicle_only_basis_subtracts_itemized_extras() -> None:
    sale = build_price_set(
        [
            ("MSRP", "$80,000"),
            ("Window Tint", "$599"),
            ("Doc Fee", "$225"),
            ("Sale Price", "$80,824"),
        ]
    )
    assert sale.comparison_price_cents(*BAND) == 80_000_00
    assert sale.price_basis is PriceBasis.VEHICLE_ONLY
    total = build_price_set(
        [
            ("MSRP", "$80,000"),
            ("Accessories", "$499"),
            ("Service Fee", "$750"),
            ("Doc Fee", "$225"),
            ("Total Price", "$81,474"),
        ]
    )
    assert total.comparison_price_cents(*BAND) == 80_000_00
    assert total.price_basis is PriceBasis.VEHICLE_ONLY
    opaque = build_price_set([("Total Price", "$81,000")])
    assert opaque.comparison_price_cents(*BAND) is None
    assert opaque.price_basis is PriceBasis.INCLUDES_FEES


@pytest.mark.parametrize(
    "label",
    ["Conditional Price", "Final Price", "Price with all rebates", "After incentives"],
)
def test_C5_conditional_labels_are_not_the_comparison_price(label: str) -> None:
    ps = build_price_set([("MSRP", "$80,000"), (label, "$74,995")])
    assert ps.comparison_price_cents(*BAND) is None
    assert Flag.CONDITIONAL_ONLY in ps.flags
    assert classify_price_label(label) is PriceKind.CONDITIONAL_INCENTIVE


def test_H1_payments_and_unknown_labels() -> None:
    monthly = build_price_set([("Price", "$899/mo")])
    assert monthly.components[0].kind is PriceKind.PAYMENT
    assert Flag.PRICE_IS_PAYMENT in monthly.flags
    assert monthly.comparison_price_cents(*BAND) is None
    finance = build_price_set([("Or Finance For", "$2,136")])
    assert finance.components[0].kind is PriceKind.PAYMENT
    special = build_price_set([("Internet Special", "$74,995")])
    assert special.components[0].kind is PriceKind.UNCLASSIFIED
    assert Flag.PRICE_LABEL_UNKNOWN in special.flags
    assert special.comparison_price_cents(*BAND) is None
    transparent = build_price_set([("Transparent PriceNo Hidden Fees", "$77,820")])
    assert transparent.components[0].kind is PriceKind.ADVERTISED
    assert transparent.comparison_price_cents(*BAND) == 77_820_00
    assert transparent.price_basis is PriceBasis.VEHICLE_ONLY


def test_H5_identified_ua_keeps_headless_and_rejects_chrome_mode() -> None:
    import platform

    ua = BrowserRenderer(Settings(browser_ua_mode="identified")).user_agent("131.0.6778.0")
    assert ua is not None
    assert "HeadlessChrome" in ua
    assert "carshop/" in ua
    real = platform.mac_ver()[0].replace(".", "_")
    if platform.system() == "Darwin" and real and real != "10_15_7":
        assert real in ua
        assert "10_15_7" not in ua
    with pytest.raises(ValidationError):
        Settings(browser_ua_mode="chrome")  # type: ignore[arg-type]


def test_H6_and_M11_model_text_defers_to_the_ag_code(ctx: object) -> None:
    from carshop.domain.classify import ClassifyContext

    assert isinstance(ctx, ClassifyContext)
    assert classify(raw(model="X5 xDrive40i"), ctx).observation is not None
    assert classify(raw(model="X5 Sports Activity Vehicle"), ctx).observation is not None
    phev = classify(raw(model="X5 PHEV", vin=make_vin("43EU"), trim="xDrive50e"), ctx)
    assert phev.exclusion is not None and phev.exclusion.code is ExclusionCode.POWERTRAIN_OTHER


def test_H7_vin_year_decides_scope(ctx: object) -> None:
    from carshop.domain.classify import ClassifyContext

    assert isinstance(ctx, ClassifyContext)
    kept = classify(raw(year=2025), ctx)
    assert kept.observation is not None
    assert Flag.CLASSIFICATION_CONFLICT in kept.observation.flags
    assert kept.needs_review == "CLASSIFICATION_CONFLICT"
    dropped = classify(raw(vin=make_vin(year_code="S"), year=None), ctx)
    assert dropped.exclusion is not None and dropped.exclusion.code is ExclusionCode.NOT_MY2026


def test_H9_sticker_absence_is_no_only_for_dap() -> None:
    rows = evaluate_features(
        "Options: 5AS Driving Assistant; 4FM",
        source_kind=SourceKind.STICKER,
        url="s",
        page_hash="h",
        is_parsed_sticker_options=True,
    )
    by = {row.feature: row.status for row in rows}
    assert by[Feature.DAP] is Tri.NO
    assert by[Feature.M_SPORT] is Tri.UNKNOWN
    assert by[Feature.MASSAGE] is Tri.UNKNOWN
    assert by[Feature.PANORAMIC_ROOF] is Tri.UNKNOWN


def test_H10_upsell_and_lowercase_codes_are_not_dap() -> None:
    upsell = evaluate_features(
        "Optional: add Driving Assistance Professional Package for $1,700",
        source_kind=SourceKind.LISTING,
        url="u",
        page_hash="h",
    )
    assert upsell[0].status is Tri.UNKNOWN
    lower = evaluate_features(
        "Option codes: zdy", source_kind=SourceKind.LISTING, url="u", page_hash="h"
    )
    assert lower[0].status is Tri.UNKNOWN
    sport = evaluate_features("zmp", source_kind=SourceKind.LISTING, url="u", page_hash="h")
    assert all(row.status is not Tri.YES for row in sport)
    for quote in [
        "DRIVING ASSISTANCE PROFESSIONAL PACKAGE",
        "Driving Assistant Professional",
        "Driving Assistance Professional Package",
        "M Sport Driving Assistance Professional Package Includes: Driving Assistant Professional",
        "Driving Assistance Professional Package, Driving Assistant Professional",
    ]:
        ev = evaluate_features(quote, source_kind=SourceKind.DETAIL, url="u", page_hash="h")
        assert ev[0].status is Tri.YES


def test_M5_conflict_keeps_both_rows_and_is_not_a_shortlist_match() -> None:
    listing = evaluate_features(
        "Driving Assistance Professional Package",
        source_kind=SourceKind.DETAIL,
        url="l",
        page_hash="h",
    )
    sticker = evaluate_features(
        "5AS only",
        source_kind=SourceKind.STICKER,
        url="s",
        page_hash="h",
        is_parsed_sticker_options=True,
    )
    dap = [row for row in merge_feature_sets(listing, sticker) if row.feature is Feature.DAP]
    assert len(dap) == 2
    assert all(row.sufficiency is Sufficiency.CONFLICT for row in dap)
    assert resolved_dap(dap) is Tri.UNKNOWN
    indexed = _index_features(
        [
            {"feature": "DAP", "status": "YES", "sufficiency": "CONFLICT"},
            {"feature": "DAP", "status": "NO", "sufficiency": "CONFLICT"},
        ]
    )
    assert indexed["DAP"]["status"] == "UNKNOWN"


def test_M6_color_codes_are_not_guessed_from_prefixes() -> None:
    assert [match_exterior(x) for x in ["Black", "M", "Blue"]] == [None, None, None]
    assert [match_upholstery(x) for x in ["Coffee", "Black"]] == [None, None]
    assert match_exterior("Black Sapphire Metallic") == "475"
    assert match_exterior("Skyscraper Grey Metallic") == "C4W"
    assert match_upholstery("Coffee Sensafin") == "KPHF"
    assert match_upholstery("cognac") == "KPRI"


def test_M7_demo_label_follows_the_odometer(ctx: object) -> None:
    from carshop.domain.classify import ClassifyContext

    assert isinstance(ctx, ClassifyContext)
    low = classify(raw(condition_text="Demo", odometer_miles=120), ctx)
    assert low.observation is not None and low.exclusion is None
    high = classify(raw(condition_text="Demo", odometer_miles=501), ctx)
    assert high.exclusion is not None and high.exclusion.code is ExclusionCode.DEMO_MILEAGE


def test_M9_partial_delta_raises() -> None:
    with pytest.raises(ValueError):
        compute_delta(
            Snapshot("r", {}),
            None,
            status=DealerStatus.PARTIAL,
        )


def test_M10_unknown_availability_is_not_on_the_lot(ctx: object) -> None:
    from carshop.domain.classify import ClassifyContext

    assert isinstance(ctx, ClassifyContext)
    obs = classify(raw(availability_text="Call for availability"), ctx).observation
    assert obs is not None and obs.availability == "UNKNOWN"


def test_M12_manual_run_does_not_force_notify() -> None:
    script = (ROOT / "deploy" / "entrypoint.sh").read_text()
    run_arm = script.split("run)", maxsplit=1)[1].split(";;", maxsplit=1)[0]
    assert 'exec carshop run "$@"' in run_arm
    assert "exec carshop run --notify" not in run_arm
    assert "catchup" in script
    assert "catchup" in (ROOT / "deploy" / "crontab").read_text()
