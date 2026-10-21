# ruff: noqa: N802
"""Re-review 2026-10-20: N1 price labels, N2 ADR status, N3 catch-up exit."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from carshop.domain.codes import Flag, PriceKind
from carshop.domain.pricing import build_price_set, classify_price_label
from carshop.pipeline.run import RunRejected

ROOT = Path(__file__).resolve().parents[2]
BAND = (55_000_00, 120_000_00)


@pytest.mark.parametrize(
    ("label", "kind"),
    [
        ("Window Tint", PriceKind.ADDON),
        ("Dealer Accessories", PriceKind.ADDON),
        ("Paint Protection", PriceKind.ADDON),
        ("Appearance Package", PriceKind.ADDON),
        ("Dealer Package", PriceKind.ADDON),
        ("Sales Service Fee", PriceKind.FEE),
        ("Dealer Fee", PriceKind.FEE),
    ],
)
def test_N1_common_addon_and_fee_labels_are_classified(label: str, kind: PriceKind) -> None:
    assert classify_price_label(label) is kind
    ps = build_price_set([("MSRP", "$80,000"), ("Our Price", "$80,000"), (label, "$599")])
    assert Flag.PRICE_LABEL_UNKNOWN not in ps.flags
    assert ps.comparison_price_cents(*BAND) == 80_000_00


def test_N1_factory_package_is_not_an_addon() -> None:
    assert classify_price_label("M Sport Package") is None


def test_N2_price_basis_adr_waits_for_the_owner() -> None:
    text = (ROOT / "doc" / "adr" / "0005-price-basis.md").read_text()
    assert text.startswith("# ADR 0005")
    assert "accepted pending owner confirmation" in text.splitlines()[2]


def test_N3_catchup_exits_zero_when_the_morning_run_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    from carshop.cli import app

    monkeypatch.setattr("carshop.cli.migrate.is_current", lambda _eng: True)

    def _engine() -> tuple[object, object]:
        return object(), object()

    def _rejected(*_a: object, **_k: object) -> None:
        raise RunRejected("a scheduled run already exists for 2026-10-20")

    monkeypatch.setattr("carshop.cli._engine", _engine)
    monkeypatch.setattr("carshop.pipeline.run.run_sync", _rejected)
    skipped = CliRunner().invoke(app, ["run", "--trigger", "catchup"])
    assert skipped.exit_code == 0
    assert "catch-up skipped" in skipped.output
    failed = CliRunner().invoke(app, ["run", "--trigger", "cron"])
    assert failed.exit_code == 2
