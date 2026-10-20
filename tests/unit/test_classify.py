from carshop.domain.classify import ClassifyContext, classify
from carshop.domain.codes import ExclusionCode, Flag, SourceKind, Tri
from carshop.domain.models import RawListing
from tests.conftest import make_vin


def raw(**kw: object) -> RawListing:
    base: dict[str, object] = {
        "url": "https://www.bmwoftest.com/new-vehicles/x5/",
        "page_hash": "abc",
        "source_kind": SourceKind.LISTING,
        "vin": make_vin(),
        "year": 2026,
        "make": "BMW",
        "model": "X5",
        "trim": "xDrive40i",
        "condition_text": "New",
        "exterior_raw": "Skyscraper Grey Metallic",
        "interior_raw": "Coffee Sensafin",
        "price_pairs": [("MSRP", "$79,100"), ("Our Price", "$78,325")],
        "full_text": "New 2026 BMW X5 xDrive40i Skyscraper Grey Metallic",
    }
    base.update(kw)
    return RawListing(**base)  # type: ignore[arg-type]


def test_in_scope_listing(ctx: ClassifyContext) -> None:
    c = classify(raw(), ctx)
    assert c.exclusion is None and c.observation is not None
    o = c.observation
    assert o.comparison_price_cents == 78_325_00
    assert o.msrp_cents == 79_100_00
    assert o.exterior_code == "C4W"
    assert o.interior_code == "KPHF"
    assert o.dap_status is Tri.UNKNOWN
    assert Flag.DAP_UNKNOWN in o.flags
    assert not o.in_transit


def test_bad_vin(ctx: ClassifyContext) -> None:
    c = classify(raw(vin="5UX23EU00T9529691"), ctx)
    assert c.exclusion is not None and c.exclusion.code is ExclusionCode.VIN_CHECKSUM_FAIL


def test_used_and_pending_and_demo(ctx: ClassifyContext) -> None:
    assert (
        classify(raw(condition_text="Certified Pre-Owned"), ctx).exclusion.code
        is ExclusionCode.USED_OR_CPO
    )  # type: ignore[union-attr]
    assert (
        classify(raw(availability_text="Sale Pending"), ctx).exclusion.code
        is ExclusionCode.SALE_PENDING
    )  # type: ignore[union-attr]
    assert (
        classify(raw(condition_text="New Demo"), ctx).exclusion.code is ExclusionCode.DEMO_MILEAGE
    )  # type: ignore[union-attr]
    assert classify(raw(odometer_miles=1200), ctx).exclusion.code is ExclusionCode.DEMO_MILEAGE  # type: ignore[union-attr]


def test_model_and_year(ctx: ClassifyContext) -> None:
    assert classify(raw(model="X3"), ctx).exclusion.code is ExclusionCode.NOT_X5  # type: ignore[union-attr]
    assert (
        classify(raw(model="X5 M", trim="Competition"), ctx).exclusion.code
        is ExclusionCode.MODEL_X5M
    )  # type: ignore[union-attr]
    assert (
        classify(raw(year=2027, trim="40 xDrive", vin=make_vin(year_code="V")), ctx).exclusion.code
        is ExclusionCode.NOT_MY2026
    )  # type: ignore[union-attr]
    # Page says 2026 but VIN says 2025: conflict, not in scope.
    assert (
        classify(raw(vin=make_vin(year_code="S")), ctx).exclusion.code
        is ExclusionCode.CLASSIFICATION_CONFLICT
    )  # type: ignore[union-attr]


def test_powertrain_by_ag_code(ctx: ClassifyContext) -> None:
    assert (
        classify(raw(vin=make_vin("13EU"), trim="sDrive40i"), ctx).exclusion.code
        is ExclusionCode.NOT_XDRIVE40I
    )  # type: ignore[union-attr]
    assert (
        classify(raw(vin=make_vin("43EU"), trim="xDrive50e"), ctx).exclusion.code
        is ExclusionCode.POWERTRAIN_OTHER
    )  # type: ignore[union-attr]
    assert (
        classify(raw(vin=make_vin("33EU"), trim="M60i"), ctx).exclusion.code
        is ExclusionCode.POWERTRAIN_OTHER
    )  # type: ignore[union-attr]
    # Right AG code but the page never says xDrive40i: conflict.
    assert (
        classify(raw(trim="", full_text="New 2026 BMW X5"), ctx).exclusion.code
        is ExclusionCode.CLASSIFICATION_CONFLICT
    )  # type: ignore[union-attr]


def test_in_transit_and_msrp_only(ctx: ClassifyContext) -> None:
    c = classify(raw(availability_text="In Transit", price_pairs=[("MSRP", "$79,100")]), ctx)
    o = c.observation
    assert o is not None and o.in_transit and o.availability == "IN_TRANSIT"
    assert o.comparison_price_cents is None
    assert Flag.PRICE_MISSING in o.flags and Flag.IN_TRANSIT in o.flags


def test_price_outside_band_goes_to_review(ctx: ClassifyContext) -> None:
    c = classify(raw(price_pairs=[("Our Price", "$7,832")]), ctx)
    assert c.observation is not None
    assert c.needs_review == "PRICE_OUTSIDE_BAND"


def test_color_unmapped_flag(ctx: ClassifyContext) -> None:
    c = classify(raw(exterior_raw="Frozen Unicorn Purple"), ctx)
    assert c.observation is not None and Flag.COLOR_UNMAPPED in c.observation.flags
    assert c.observation.exterior_code is None


def test_m_sport_and_dap_from_package_text(ctx: ClassifyContext) -> None:
    c = classify(raw(package_text="M Sport Package; Driving Assistance Professional Package"), ctx)
    o = c.observation
    assert o is not None and o.m_sport and o.dap_status is Tri.YES and Flag.PKG_M_SPORT in o.flags
