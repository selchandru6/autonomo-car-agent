"""Saved-search criteria and matching rules (requirements-shortlist.md §5). Pure functions, no I/O.

The same code serves the form preview, the baseline at create time, and the A10 Matcher, so a
preview count always equals what is written.
"""

from __future__ import annotations

import math
from typing import Any

from carshop.domain.codes import Feature
from carshop.domain.display import canon_exterior, canon_interior
from carshop.domain.pricing import fmt_cents
from carshop.reference.data import (
    EXTERIOR_COLORS,
    EXTERIOR_FAMILIES,
    INTERIOR_FAMILIES,
    UPHOLSTERY,
    exterior_family,
    interior_family,
)

Row = dict[str, Any]

CRITERIA_VERSION = 1
DEFAULT_BAND_CENTS = 3_000_00
FEATURES = tuple(f.value for f in Feature)
WEIGHTS = ("must", "nice", "ignore")
RADII = (50, 100, 200)
MATCH, POSSIBLE = "MATCH", "POSSIBLE"

SHORT: dict[str, str] = {
    "DAP": "Driving Assist Pro",
    "M_SPORT": "M Sport",
    "MASSAGE": "Massage seats",
    "PANORAMIC_ROOF": "Panoramic roof",
    "SKY_LOUNGE": "Sky Lounge",
    "EXECUTIVE": "Executive",
    "CLIMATE_COMFORT": "Climate Comfort",
    "AIR_SUSPENSION": "Air suspension",
}
EXTERIOR_FAMILY_NAMES = ("Grey", "White", "Black", "Blue", "Green")
INTERIOR_FAMILY_NAMES = ("Light", "Brown", "Black")


class CriteriaError(ValueError):
    """Criteria that cannot be saved; the message is shown to the owner."""


# ---------------------------------------------------------------- criteria


def default_criteria() -> Row:
    return {
        "version": CRITERIA_VERSION,
        "base": None,
        "features": dict.fromkeys(FEATURES, "ignore"),
        "exterior": {"families": [], "names": []},
        "interior": {"families": [], "names": []},
        "colors_only": False,
        "max_price_cents": None,
        "band_cents": None,
        "no_price": "include",
        "location": {"city": None, "radius_mi": None},
        "in_transit": "include",
        "sort": "match",
    }


def _cents(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        cents = int(value)
    except (TypeError, ValueError) as e:
        raise CriteriaError(f"{field} must be a whole number of cents.") from e
    if cents < 0:
        raise CriteriaError(f"{field} cannot be negative.")
    return cents


def _names(values: Any, allowed: tuple[str, ...] | None = None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        name = " ".join(str(v).split())
        if not name:
            continue
        if allowed is not None and name not in allowed:
            raise CriteriaError(f"Unknown color family: {name}.")
        if name not in out:
            out.append(name)
    return out


def normalize(raw: Row | None) -> Row:
    """Fill defaults and reject anything outside the schema. Same input, same output."""
    raw = raw or {}
    if int(raw.get("version", CRITERIA_VERSION)) != CRITERIA_VERSION:
        raise CriteriaError("These criteria were saved by a newer version.")
    c = default_criteria()
    feats = raw.get("features") or {}
    for f in FEATURES:
        w = str(feats.get(f, "ignore")).lower()
        if w not in WEIGHTS and not (f == "M_SPORT" and w == "exclude"):
            raise CriteriaError(f"{SHORT[f]}: choose Must, Nice, or Ignore.")
        c["features"][f] = w
    for key, fams in (("exterior", EXTERIOR_FAMILY_NAMES), ("interior", INTERIOR_FAMILY_NAMES)):
        part = raw.get(key) or {}
        c[key] = {
            "families": _names(part.get("families"), fams),
            "names": _names(part.get("names")),
        }
    c["colors_only"] = bool(raw.get("colors_only", False))
    c["max_price_cents"] = _cents(raw.get("max_price_cents"), "Maximum price")
    c["band_cents"] = _cents(raw.get("band_cents"), "Price band")
    if raw.get("no_price", "include") not in ("include", "exclude"):
        raise CriteriaError("Cars without a cash price: include or exclude.")
    c["no_price"] = raw.get("no_price", "include")
    loc = raw.get("location") or {}
    city = loc.get("city") or None
    radius = loc.get("radius_mi")
    if radius not in (None, "", *RADII):
        raise CriteriaError("Distance must be 50, 100, or 200 miles, or any distance.")
    c["location"] = {
        "city": str(city) if city else None,
        "radius_mi": int(radius) if city and radius else None,
    }
    if raw.get("in_transit", "include") not in ("include", "exclude"):
        raise CriteriaError("Cars on the way to the dealer: include or exclude.")
    c["in_transit"] = raw.get("in_transit", "include")
    c["sort"] = raw.get("sort") if raw.get("sort") in ("match", "price", "near") else "match"
    base = raw.get("base")
    if base:
        c["base"] = {
            "vin": str(base["vin"]),
            "price_cents": _cents(base.get("price_cents"), "Base price"),
            "ext_name": base.get("ext_name"),
            "ext_code": base.get("ext_code"),
            "int_name": base.get("int_name"),
            "int_code": base.get("int_code"),
        }
    if c["band_cents"] is not None and not (c["base"] and c["base"]["price_cents"] is not None):
        raise CriteriaError("A price band needs a base car with a cash price.")
    return c


def prefill_from_snapshot(snap: Row, city: str | None) -> tuple[str, Row]:
    """§5.2: the search "Find cars like this" opens with."""
    c = default_criteria()
    feats = snap.get("features") or {}
    for f in FEATURES:
        yes = (feats.get(f) or {}).get("status") == "YES"
        if f == "DAP":
            c["features"][f] = "must" if yes else "ignore"
        elif f == "M_SPORT":
            c["features"][f] = "nice" if yes or snap.get("m_sport") else "ignore"
        else:
            c["features"][f] = "nice" if yes else "ignore"
    ext_name, _ = canon_exterior(snap.get("exterior_raw"))
    int_name, _ = canon_interior(snap.get("interior_raw"))
    ext_fam = exterior_family(snap.get("exterior_code"))
    int_fam = interior_family(snap.get("interior_code"))
    c["exterior"] = (
        {"families": [ext_fam], "names": []} if ext_fam else {"families": [], "names": [ext_name]}
    )
    c["interior"] = (
        {"families": [int_fam], "names": []} if int_fam else {"families": [], "names": [int_name]}
    )
    price = snap.get("comparison_price_cents")
    c["band_cents"] = DEFAULT_BAND_CENTS if price is not None else None
    c["base"] = {
        "vin": snap["vin"], "price_cents": price,
        "ext_name": ext_name, "ext_code": snap.get("exterior_code"),
        "int_name": int_name, "int_code": snap.get("interior_code"),
    }  # fmt: skip
    name = f"Like {ext_name} · {int_name}" + (f" · {city}" if city else "")
    return name, normalize(c)


# ---------------------------------------------------------------- wish list round trip


def criteria_from_wish(wish: Row) -> Row:
    """The Find a car wish list as criteria (§5.1: one schema for both)."""
    budget = wish.get("budget")
    near = wish.get("near") or "any"
    c = default_criteria()
    c.update(
        features={f: str((wish.get("feats") or {}).get(f) or "ignore") for f in FEATURES},
        exterior={"families": [], "names": list(wish.get("ext") or [])},
        interior={"families": [], "names": list(wish.get("int") or [])},
        colors_only=bool(wish.get("colorsOnly")),
        max_price_cents=int(budget) * 100 if budget is not None else None,
        location={"city": None if near == "any" else near, "radius_mi": wish.get("radius")},
        in_transit="include" if wish.get("transit", True) else "exclude",
        sort=wish.get("sort") or "match",
    )
    return normalize(c)


def _short_paint(code: str) -> str:
    return canon_exterior(EXTERIOR_COLORS[code])[0]


def _short_interior(code: str) -> str:
    return canon_interior(UPHOLSTERY[code])[0]


def wish_from_criteria(criteria: Row) -> Row:
    """Criteria loaded into Find a car. Families become their member colors there."""
    c = normalize(criteria)
    ext = list(c["exterior"]["names"])
    for code, fam in EXTERIOR_FAMILIES.items():
        if fam in c["exterior"]["families"] and _short_paint(code) not in ext:
            ext.append(_short_paint(code))
    inner = list(c["interior"]["names"])
    for code, fam in INTERIOR_FAMILIES.items():
        if fam in c["interior"]["families"] and _short_interior(code) not in inner:
            inner.append(_short_interior(code))
    loc = c["location"]
    return {
        "budget": c["max_price_cents"] // 100 if c["max_price_cents"] is not None else None,
        "near": loc["city"] or "any",
        "radius": loc["radius_mi"],
        "transit": c["in_transit"] == "include",
        "colorsOnly": c["colors_only"],
        "sort": c["sort"],
        "ext": ext,
        "int": inner,
        "feats": {f: w for f, w in c["features"].items() if w in ("must", "nice")},
    }


def summary(criteria: Row) -> list[str]:
    """Short shop-words description for lists."""
    c = criteria
    out: list[str] = []
    must = [SHORT[f] for f, w in c["features"].items() if w == "must"]
    nice = [SHORT[f] for f, w in c["features"].items() if w == "nice"]
    if must:
        out.append("Must: " + ", ".join(must))
    if nice:
        out.append("Nice: " + ", ".join(nice))
    if c["features"].get("M_SPORT") == "exclude":
        out.append("No M Sport")
    for key, word in (("exterior", "paint"), ("interior", "interior")):
        part = c[key]
        names = [f"{f.lower()} {word}" for f in part["families"]] + part["names"]
        if names:
            out.append(("Only " if c["colors_only"] else "Prefer ") + " or ".join(names))
    base = c.get("base") or {}
    if c["band_cents"] is not None and base.get("price_cents") is not None:
        lo, hi = base["price_cents"] - c["band_cents"], base["price_cents"] + c["band_cents"]
        out.append(f"{fmt_cents(lo)} to {fmt_cents(hi)}")
    if c["max_price_cents"] is not None:
        out.append(f"Up to {fmt_cents(c['max_price_cents'])}")
    loc = c["location"]
    if loc["city"]:
        out.append(
            f"Within {loc['radius_mi']} mi of {loc['city']}"
            if loc["radius_mi"]
            else f"Near {loc['city']}"
        )
    else:
        out.append("Anywhere in Texas")
    if c["in_transit"] == "exclude":
        out.append("On the lot only")
    return out


# ---------------------------------------------------------------- matching (§5.3)


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle miles, the same formula Find a car uses."""
    r = 3958.8
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(h))


def _color(car: Row, part: Row, base: Row | None, kind: str) -> tuple[bool, str]:
    """('exact' | 'family' | 'none') for one color criterion."""
    name, code, fam = car[f"{kind}_name"], car[f"{kind}_code"], car[f"{kind}_family"]
    exact = name in part["names"] or bool(base and code and code == base.get(f"{kind}_code"))
    exact = exact or bool(base and not code and name == base.get(f"{kind}_name"))
    if exact:
        return True, "exact"
    if fam and fam in part["families"]:
        return True, "family"
    return False, "none"


def evaluate(criteria: Row, car: Row, centroid: tuple[float, float] | None = None) -> Row | None:
    """Tier, score, and reasons for one car, or None when it is not a match at all."""
    c = criteria
    base = c.get("base")
    if base and car["vin"] == base["vin"]:
        return None
    feats: dict[str, str] = car["features"]
    weights: dict[str, str] = c["features"]
    if weights.get("M_SPORT") == "exclude" and feats.get("M_SPORT") == "YES":
        return None
    if c["in_transit"] == "exclude" and car["in_transit"]:
        return None
    ext_ok, ext_hit = _color(car, c["exterior"], base, "ext")
    int_ok, int_hit = _color(car, c["interior"], base, "int")
    has_ext = bool(c["exterior"]["families"] or c["exterior"]["names"])
    has_int = bool(c["interior"]["families"] or c["interior"]["names"])
    if c["colors_only"] and ((has_ext and not ext_ok) or (has_int and not int_ok)):
        return None
    price = car["price"]
    base_price = base.get("price_cents") if base else None
    limited = c["max_price_cents"] is not None or c["band_cents"] is not None
    if price is None and limited and c["no_price"] == "exclude":
        return None
    if price is not None and c["max_price_cents"] is not None and price > c["max_price_cents"]:
        return None
    in_band = price is None or c["band_cents"] is None or base_price is None
    if not in_band and abs(price - base_price) > c["band_cents"]:
        return None
    loc = c["location"]
    near = loc["city"] and loc["radius_mi"] and centroid and car.get("lat") is not None
    if near and miles(centroid, (car["lat"], car["lon"])) > loc["radius_mi"]:  # type: ignore[arg-type]
        return None
    musts = [f for f in FEATURES if weights.get(f) == "must"]
    if any(feats.get(f) == "NO" for f in musts):
        return None
    full = all(feats.get(f) == "YES" for f in musts) and not (price is None and limited)
    tier = MATCH if full else POSSIBLE

    earned = possible = 0
    reasons: list[Row] = []
    for f in musts:
        st = feats.get(f, "UNKNOWN")
        reasons.append(
            {"k": "met", "t": f"✓ {SHORT[f]}"}
            if st == "YES"
            else {"k": "unk", "t": f"? {SHORT[f]} not listed"}
        )
    for f in FEATURES:
        if weights.get(f) != "nice":
            continue
        possible += 10
        if feats.get(f) == "YES":
            earned += 10
            reasons.append({"k": "met", "t": f"✓ {SHORT[f]}"})
        else:
            reasons.append({"k": "unk", "t": f"? {SHORT[f]} not listed"})
    if has_ext:
        possible += 15
        earned += 15 if ext_hit == "exact" else 10 if ext_hit == "family" else 0
    if has_int:
        possible += 10
        earned += 10 if int_hit == "exact" else 6 if int_hit == "family" else 0
    if base_price is not None and price is not None:
        possible += 15
        d = abs(price - base_price)
        earned += 15 if d <= 1_000_00 else 10 if d <= 3_000_00 else 5 if d <= 5_000_00 else 0
    if price is None:
        reasons.append(
            {"k": "unk", "t": f"No cash price: {car.get('no_price_why') or 'ask the dealer'}"}
        )
    elif base_price is not None:
        d = price - base_price
        reasons.append(
            {
                "k": "diff",
                "t": "Same price" if d == 0 else ("+" if d > 0 else "\u2212") + fmt_cents(abs(d)),
            }
        )
    if base:
        if car["ext_name"] != base.get("ext_name"):
            reasons.append(
                {"k": "diff", "t": f"{car['ext_name']} instead of {base.get('ext_name')}"}
            )
        if car["int_name"] != base.get("int_name"):
            reasons.append({"k": "diff", "t": f"{car['int_name']} interior"})
    else:
        if has_ext and ext_ok:
            reasons.append({"k": "met", "t": f"✓ {car['ext_name']}"})
        if has_int and int_ok:
            reasons.append({"k": "met", "t": f"✓ {car['int_name']} interior"})
    score = round(100 * earned / possible) if possible else 100
    return {"tier": tier, "score": score, "reasons": reasons}


def rank(results: list[tuple[Row, Row]]) -> list[tuple[Row, Row]]:
    """Match before Possible match, then score, then price, then VIN: fully deterministic."""
    return sorted(
        results,
        key=lambda cr: (
            cr[1]["tier"] != MATCH, -cr[1]["score"],
            cr[0]["price"] is None, cr[0]["price"] or 0, cr[0]["vin"],
        ),
    )  # fmt: skip
