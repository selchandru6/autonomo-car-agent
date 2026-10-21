"""Buyer-facing display rules shared by the screen, the shortlist, and the digest. Pure."""

from __future__ import annotations

import re
from typing import Any

from carshop.reference.data import EXTERIOR_COLORS

Row = dict[str, Any]

_EXT_NAMES = [
    re.sub(r"\s+metallic$", "", name, flags=re.I) for name in EXTERIOR_COLORS.values()
] + ["Grigio Telesto"]
_INT_COLORS = ("ivory white", "silverstone", "cognac", "coffee", "tartufo", "black")


def index_features(rows: list[Row]) -> dict[str, Row]:
    """A conflict keeps both stored rows, and the screen must not treat either as a match."""
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["feature"]), []).append(row)
    out: dict[str, Row] = {}
    for name, items in grouped.items():
        if any(item["sufficiency"] == "CONFLICT" for item in items):
            merged = dict(items[0])
            merged["status"] = "UNKNOWN"
            merged["sufficiency"] = "CONFLICT"
            out[name] = merged
        else:
            out[name] = items[-1]
    return out


def canon_exterior(raw: str | None) -> tuple[str, str]:
    """Drop Metallic, spell Grey, and use the official color name when it is contained."""
    if not raw:
        return "Not shown", "Other"
    cleaned = re.sub(r"\bmetallic\b", "", raw, flags=re.I)
    cleaned = cleaned.replace("Gray", "Grey").replace("gray", "grey")
    cleaned = " ".join(cleaned.split())
    name = cleaned or "Not shown"
    low = cleaned.lower()
    for known in _EXT_NAMES:
        if known.lower() in low:
            name = known
            break
    family = "Other"
    for fam in ("grey", "white", "black", "blue", "green"):
        if fam in name.lower():
            family = fam.capitalize()
            break
    return name, family


def canon_interior(raw: str | None) -> tuple[str, str | None]:
    if not raw:
        return "Not shown", None
    low = raw.lower()
    color = next((name for name in _INT_COLORS if name in low), None)
    material = "Merino leather" if "merino" in low else "Sensafin" if "sensafin" in low else None
    return (color.title() if color else " ".join(raw.split()), material)


def price_gap_reason(flags: set[str], msrp_cents: int | None) -> str:
    if "PRICE_IS_PAYMENT" in flags:
        return "Lease/finance payment only"
    if "CONDITIONAL_ONLY" in flags:
        return "Price only after conditional rebates"
    if "CALL_FOR_PRICE" in flags:
        return "Call for price"
    if msrp_cents:
        return "Only MSRP shown"
    return "No cash price shown"


def cash_cents(comparison: int | None, basis: str | None) -> int | None:
    """A cash price is the stored vehicle-only figure. Anything else is not a price."""
    if comparison is None:
        return None
    if basis in (None, "", "vehicle_only"):
        return int(comparison)
    return None
