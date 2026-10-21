from carshop.domain import vin as vinmod
from tests.conftest import make_vin


def test_known_real_vin_is_valid() -> None:
    info = vinmod.decode("5UX23EU03T9529691")
    assert info.valid
    assert info.year == 2026
    assert info.ag_code == "23EU"


def test_checksum_failure_detected() -> None:
    assert not vinmod.is_valid("5UX23EU00T9529691")  # wrong check digit


def test_rejects_illegal_characters_and_length() -> None:
    assert not vinmod.is_valid("5UX23EU03T952969I")  # I is not allowed
    assert not vinmod.is_valid("5UX23EU03T95296")


def test_year_codes_follow_2010_cycle() -> None:
    assert vinmod.model_year(make_vin(year_code="T")) == 2026
    assert vinmod.model_year(make_vin(year_code="S")) == 2025
    assert vinmod.model_year(make_vin(year_code="V")) == 2027


def test_make_vin_helper_is_valid() -> None:
    for ag in ("23EU", "13EU", "43EU", "33EU"):
        assert vinmod.is_valid(make_vin(ag))
        assert vinmod.ag_code(make_vin(ag)) == ag
