"""Shared fixtures. Network is blocked by pytest-socket (see pyproject addopts)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from carshop.domain import vin as vinmod
from carshop.domain.classify import ClassifyContext
from carshop.domain.codes import Platform, TermsStatus
from carshop.domain.models import Dealer

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def make_vin(ag: str = "23EU", year_code: str = "T", serial: str = "529691") -> str:
    """Build a checksum-valid BMW VIN with the given AG code and model-year letter.

    Layout: WMI(3) AG(4) VDS(1) check(1) year(1) plant(1) serial(6). The check digit is computed.
    """
    body = f"5UX{ag}00{year_code}9{serial}"
    assert len(body) == 17
    cd = vinmod.check_digit(body)
    return body[:8] + cd + body[9:]


@pytest.fixture
def ctx() -> ClassifyContext:
    return ClassifyContext(
        dealer_id="bmw-test",
        fetched_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
        adapter_version="test-1",
        model_year=2026,
        target_ag="23EU",
        odometer_max=500,
        band_min_cents=55_000_00,
        band_max_cents=120_000_00,
    )


@pytest.fixture
def dealer() -> Dealer:
    return Dealer(
        dealer_id="bmw-test",
        center_number=None,
        name="BMW of Test",
        city="Austin",
        hostname="www.bmwoftest.com",
        start_url="https://www.bmwoftest.com/new-vehicles/x5/",
        path_prefixes=("/new-vehicles/", "/inventory/"),
        platform=Platform.DEALER_INSPIRE,
        terms_status=TermsStatus.OWNER_ACCEPTED_RISK,
        sticker_hosts=("sticker.example.com",),
    )
