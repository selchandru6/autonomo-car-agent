from carshop.domain.codes import Flag, PriceEvent, PriceKind
from carshop.domain.pricing import (
    build_price_set,
    classify_price_label,
    fmt_cents,
    parse_cents,
    price_event,
)


def test_parse_cents_is_integer_cents() -> None:
    assert parse_cents("$79,325") == 79_325_00
    assert parse_cents("$79,325.50") == 79_325_50
    assert parse_cents("Call for price") is None
    assert fmt_cents(79_325_00) == "$79,325"
    assert fmt_cents(None) == "—"


def test_labels() -> None:
    assert classify_price_label("MSRP") is PriceKind.MSRP
    assert classify_price_label("Our Price (Excl. tax, gov. fees)") is PriceKind.ADVERTISED
    assert classify_price_label("Selling Price") is PriceKind.ADVERTISED
    assert classify_price_label("Est. monthly payment") is PriceKind.PAYMENT
    assert classify_price_label("BMW Loyalty Credit") is PriceKind.CONDITIONAL_INCENTIVE
    assert classify_price_label("Window Tint") is PriceKind.ADDON


def test_msrp_only_means_price_missing_not_zero() -> None:
    ps = build_price_set([("MSRP", "$79,100")])
    assert ps.msrp_cents == 79_100_00
    assert ps.advertised_price_cents is None
    assert ps.comparison_price_cents(55_000_00, 120_000_00) is None
    assert Flag.PRICE_MISSING in ps.flags


def test_payment_is_never_a_price() -> None:
    ps = build_price_set([("MSRP", "$79,100"), ("Est. payment /mo", "$1,099")])
    assert ps.advertised_price_cents is None
    assert Flag.PRICE_IS_PAYMENT in ps.flags
    assert Flag.PRICE_MISSING not in ps.flags


def test_conditional_incentive_only() -> None:
    ps = build_price_set([("MSRP", "$79,100"), ("Loyalty Credit", "-$1,000")])
    assert ps.advertised_price_cents is None
    assert Flag.CONDITIONAL_ONLY in ps.flags


def test_call_for_price() -> None:
    ps = build_price_set([("Our Price", "Call for Price")])
    assert Flag.CALL_FOR_PRICE in ps.flags
    assert ps.advertised_price_cents is None


def test_addon_fallback_and_first_advertised_wins() -> None:
    ps = build_price_set(
        [
            ("MSRP", "$80,000"),
            ("Our Price", "$78,000"),
            ("Sale Price", "$77,000"),
            ("Window Tint", "$599"),
        ]
    )
    assert ps.advertised_price_cents == 78_000_00
    kinds = [c.kind for c in ps.components]
    assert PriceKind.CONDITIONAL_INCENTIVE in kinds
    assert PriceKind.ADDON in kinds
    assert PriceKind.UNCLASSIFIED not in kinds
    assert Flag.PRICE_LABEL_UNKNOWN not in ps.flags


def test_price_band_flags_implausible() -> None:
    ps = build_price_set([("Our Price", "$7,800")])
    # Outside the band the figure is not used for ranking; it is flagged for review instead.
    assert ps.comparison_price_cents(55_000_00, 120_000_00) is None
    assert Flag.PRICE_IMPLAUSIBLE in ps.flags
    assert ps.advertised_price_cents == 7_800_00


def test_price_events() -> None:
    assert price_event(None, None) is None
    # A price appearing or vanishing is an event with no numeric delta (NULL is not 0).
    assert price_event(None, 100) == (PriceEvent.APPEARED, 0)
    assert price_event(100, None) == (PriceEvent.WITHDRAWN, 0)
    assert price_event(79_100_00, 78_100_00) == (PriceEvent.DROP, -1_000_00)
    assert price_event(79_100_00, 79_100_50) is None  # under $1
    assert price_event(79_100_00, 79_200_00) == (PriceEvent.INCREASE, 100_00)
