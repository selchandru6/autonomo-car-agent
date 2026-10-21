"""requirements-shortlist.md §5.3: matching rules are pure and deterministic."""

from __future__ import annotations

from carshop.shortlist import match as m


def _car(**kw: object) -> dict:
    row = {
        "vin": "5UX23EU01T9541662",
        "features": dict.fromkeys(m.FEATURES, "UNKNOWN"),
        "price": 88_225_00,
        "ext_name": "Dravit Grey",
        "ext_code": "C36",
        "ext_family": "Grey",
        "int_name": "Cognac",
        "int_code": "KPRI",
        "int_family": "Brown",
        "in_transit": False,
        "lat": None,
        "lon": None,
        "no_price_why": None,
    }
    feats = dict(row["features"])  # type: ignore[arg-type]
    feats.update(kw.pop("features", {}))  # type: ignore[arg-type]
    row["features"] = feats
    row.update(kw)
    return row


def _criteria(**kw: object) -> dict:
    c = m.default_criteria()
    c["features"]["DAP"] = "must"
    c["exterior"] = {"families": ["Grey"], "names": []}
    c["interior"] = {"families": ["Light"], "names": []}
    c["base"] = {
        "vin": "5UX23EU0XT9462524",
        "price_cents": 87_949_00,
        "ext_name": "Skyscraper Grey",
        "ext_code": "C4W",
        "int_name": "Silverstone",
        "int_code": "KPA9",
    }
    c["band_cents"] = m.DEFAULT_BAND_CENTS
    c.update(kw)
    return m.normalize(c)


def test_unknown_must_is_possible_never_a_match() -> None:
    c = _criteria()
    unknown = m.evaluate(c, _car(features={"DAP": "UNKNOWN"}))
    yes = m.evaluate(c, _car(features={"DAP": "YES"}))
    no = m.evaluate(c, _car(features={"DAP": "NO"}))
    assert unknown is not None and unknown["tier"] == m.POSSIBLE
    assert yes is not None and yes["tier"] == m.MATCH
    assert no is None


def test_base_car_never_matches_itself() -> None:
    c = _criteria()
    assert m.evaluate(c, _car(vin="5UX23EU0XT9462524", features={"DAP": "YES"})) is None


def test_price_band_excludes_and_plus_276_is_a_reason() -> None:
    c = _criteria()
    peer = m.evaluate(c, _car(features={"DAP": "YES"}))
    far = m.evaluate(c, _car(vin="5UX23EU0XT9439258", price=92_869_00, features={"DAP": "YES"}))
    assert peer is not None and peer["tier"] == m.MATCH
    assert any(x["t"] == "+$276" for x in peer["reasons"])
    assert far is None


def test_same_input_same_score() -> None:
    c = _criteria()
    car = _car(features={"DAP": "YES", "PANORAMIC_ROOF": "YES"})
    a, b = m.evaluate(c, car), m.evaluate(c, car)
    assert a == b and a is not None
    assert 0 <= a["score"] <= 100
