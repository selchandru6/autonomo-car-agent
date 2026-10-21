"""Read the October 2026 Texas X5 workbooks into one vehicle per VIN.

The same VIN is never stored twice. A later snapshot replaces price, mileage, and dealer.
When the dealer name actually changes (not just "BMW of Dallas" vs "BMW of Dallas, the car
you like"), the earlier dealer is kept on `prior_dealer`. Two VINs in these files did move,
from Sewell BMW of Plano to BMW of Grapevine, between October 6 and October 13.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from carshop.catalog.features import BY_CODE, FEATURES, THREE_PACKAGES

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_NEG_RE = re.compile(r"\b(no|not|without|missing|lacks)\b", re.I)

# File name -> snapshot date. Unknown files sort as the earliest.
_DATES: dict[str, str] = {
    "Texas_2026_BMW_X5_Inventory.xlsx": "2026-10-06",
    "Texas_2026_BMW_X5_Inventory_Oct11.xlsx": "2026-10-11",
    "Texas_2026_X5_40i_Gray_Light_DAP_Massage.xlsx": "2026-10-11",
    "Texas_2026_BMW_X5_Oct13_Deal_Analysis.xlsx": "2026-10-13",
    "DFW_2026_BMW_X5_Silverstone_DAP_Oct15.xlsx": "2026-10-15",
}

_PACKAGE_COLS = (
    "Packages",
    "Packages (Dealer.com feed only)",
    "Features and packages",
)
_LISTING_COLS = ("Listing", "Listing URL")


@dataclass(slots=True)
class CatalogVehicle:
    vin: str
    dealer_name: str
    city: str | None = None
    phone: str | None = None
    year: int | None = None
    model: str | None = None
    trim: str | None = None
    trim_code: str = "Other"
    stock: str | None = None
    exterior: str | None = None
    interior: str | None = None
    mileage: int | None = None
    msrp_cents: int | None = None
    advertised_cents: int | None = None
    listing_url: str | None = None
    as_of: str = ""
    prior_dealer: str | None = None
    packages_text: str | None = None
    features: dict[str, str] = field(default_factory=dict)


def canonical_dealer(name: str) -> str:
    """Drop a trailing city or a parenthetical so one store is one name."""
    text = re.sub(r"\s+", " ", name).strip()
    text = re.sub(r"\s*\([^)]*\)", "", text).strip()
    if "," in text:
        left, right = (part.strip() for part in text.split(",", 1))
        if left and "bmw" not in right.lower() and " of " not in f" {right.lower()} ":
            return left
    return text


def trim_code(trim: str) -> str:
    t = trim.lower()
    for code in ("sdrive40i", "xdrive40i", "xdrive50e", "m60i"):
        if code in t.replace(" ", ""):
            return {
                "sdrive40i": "sDrive40i",
                "xdrive40i": "xDrive40i",
                "xdrive50e": "xDrive50e",
                "m60i": "M60i",
            }[code]
    if "competition" in t or re.search(r"\bx5\s*m\b", t):
        return "X5 M"
    return "Other"


def import_workbooks(paths: Sequence[Path]) -> list[CatalogVehicle]:
    """Merge every VIN row in these workbooks. One vehicle per VIN; latest dealer wins."""
    partials: list[_Partial] = []
    for path in paths:
        partials.extend(_read_book(path))
    partials.sort(key=lambda p: (p.as_of, p.weight))
    merged: dict[str, CatalogVehicle] = {}
    feat_rank: dict[tuple[str, str], tuple[str, int, int]] = {}
    for part in partials:
        cur = merged.get(part.vin)
        if cur is None:
            merged[part.vin] = part.to_vehicle()
        else:
            _overlay(cur, part)
        for code, (status, explicit) in part.features.items():
            rank = (part.as_of, part.weight, 1 if explicit else 0)
            prev = feat_rank.get((part.vin, code))
            if prev is None or rank >= prev:
                feat_rank[(part.vin, code)] = rank
                merged[part.vin].features[code] = status
    return sorted(merged.values(), key=lambda v: (v.dealer_name, v.trim_code, v.vin))


@dataclass(slots=True)
class _Partial:
    vin: str
    as_of: str
    weight: int
    dealer: str
    city: str | None = None
    phone: str | None = None
    year: int | None = None
    model: str | None = None
    trim: str | None = None
    stock: str | None = None
    exterior: str | None = None
    interior: str | None = None
    mileage: int | None = None
    msrp_cents: int | None = None
    advertised_cents: int | None = None
    listing_url: str | None = None
    packages_text: str | None = None
    features: dict[str, tuple[str, bool]] = field(default_factory=dict)

    def to_vehicle(self) -> CatalogVehicle:
        return CatalogVehicle(
            vin=self.vin,
            dealer_name=self.dealer,
            city=self.city,
            phone=self.phone,
            year=self.year,
            model=self.model,
            trim=self.trim,
            trim_code=trim_code(self.trim or ""),
            stock=self.stock,
            exterior=self.exterior,
            interior=self.interior,
            mileage=self.mileage,
            msrp_cents=self.msrp_cents,
            advertised_cents=self.advertised_cents,
            listing_url=self.listing_url,
            as_of=self.as_of,
            packages_text=self.packages_text,
        )


def _overlay(cur: CatalogVehicle, part: _Partial) -> None:
    # Compare sheets sometimes name the wrong store. Only an inventory or curated
    # sheet (weight >= 4) may move a VIN, and only when the snapshot date is later.
    if part.weight >= 4 and part.dealer and part.dealer != cur.dealer_name:
        if part.as_of > cur.as_of:
            cur.prior_dealer = cur.dealer_name
        cur.dealer_name = part.dealer
    if part.weight >= 4:
        cur.as_of = part.as_of
    for attr, value in (
        ("city", part.city),
        ("phone", part.phone),
        ("year", part.year),
        ("model", part.model),
        ("trim", part.trim),
        ("stock", part.stock),
        ("exterior", part.exterior),
        ("interior", part.interior),
        ("mileage", part.mileage),
        ("msrp_cents", part.msrp_cents),
        ("advertised_cents", part.advertised_cents),
        ("listing_url", part.listing_url),
        ("packages_text", part.packages_text),
    ):
        if value not in (None, ""):
            setattr(cur, attr, value)
    if part.trim:
        cur.trim_code = trim_code(part.trim)


def _read_book(path: Path) -> list[_Partial]:
    as_of = _DATES.get(path.name, "2026-10-01")
    wb = load_workbook(path, read_only=True, data_only=True)
    out: list[_Partial] = []
    try:
        for ws in wb.worksheets:
            weight = _sheet_weight(ws.title)
            if weight == 0:
                continue
            rows = list(ws.iter_rows(values_only=True))
            header_at = _header_index(rows)
            if header_at is None:
                continue
            header = [str(c).strip() if c else "" for c in rows[header_at]]
            if "VIN" not in header or "Dealer" not in header:
                continue
            for raw in rows[header_at + 1 :]:
                if not raw:
                    continue
                row = {header[i]: raw[i] for i in range(min(len(header), len(raw))) if header[i]}
                vin = str(row.get("VIN") or "").strip().upper()
                if not VIN_RE.match(vin):
                    continue
                dealer = canonical_dealer(str(row.get("Dealer") or ""))
                if not dealer:
                    continue
                packages = _first(row, _PACKAGE_COLS)
                part = _Partial(
                    vin=vin,
                    as_of=as_of,
                    weight=weight,
                    dealer=dealer,
                    city=_opt(row, "City"),
                    phone=_opt(row, "Phone"),
                    year=_int(row.get("Year")),
                    model=_opt(row, "Model"),
                    trim=_opt(row, "Trim"),
                    stock=_opt(row, "Stock"),
                    exterior=_opt(row, "Exterior"),
                    interior=_opt(row, "Interior"),
                    mileage=_int(row.get("Miles") if "Miles" in row else row.get("Mileage")),
                    msrp_cents=_cents(row.get("MSRP")),
                    advertised_cents=_cents(
                        row.get("Advertised")
                        if "Advertised" in row
                        else row.get("Advertised price")
                    ),
                    listing_url=_first(row, _LISTING_COLS),
                    packages_text=packages,
                )
                _features_from_text(part, packages or "")
                _features_from_flags(part, row)
                out.append(part)
    finally:
        wb.close()
    return out


def _sheet_weight(title: str) -> int:
    t = title.lower()
    if t in {"notes", "summary", "counts", "deal"}:
        return 0
    if "compare" in t:
        return 1
    if "inventory" in t:
        return 4
    return 5


def _header_index(rows: Iterable[tuple[Any, ...] | list[Any]]) -> int | None:
    for i, row in enumerate(rows):
        if row and any(str(c).strip() == "VIN" for c in row if c is not None):
            return i
    return None


def _opt(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        found = _opt(row, key)
        if found:
            return found
    return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value or not re.fullmatch(r"-?\d+(\.0+)?", value):
            return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _cents(value: Any) -> int | None:
    """Whole dollars in the sheet become integer cents. Ignore sub-$1000 cells (deal inputs)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value * 100 if value >= 1000 else None
    if isinstance(value, float):
        return round(value * 100) if value >= 1000 else None
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return round(number * 100) if number >= 1000 else None
    n = _int(value)
    return n * 100 if n is not None and n >= 1000 else None


def _features_from_text(part: _Partial, text: str) -> None:
    if not text:
        return
    for feat in FEATURES:
        hit = _mention(text, feat.pattern)
        if hit is None:
            continue
        part.features[feat.code] = ("YES" if hit else "NO", False)


def _features_from_flags(part: _Partial, row: dict[str, Any]) -> None:
    for feat in FEATURES:
        for header in feat.explicit_headers:
            flag = _yes_no(row.get(header))
            if flag is not None:
                part.features[feat.code] = (flag, True)
    three = _yes_no(row.get("Has the three packages"))
    if three == "YES":
        for code in THREE_PACKAGES:
            part.features[code] = ("YES", True)


def _yes_no(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "y", "true"}:
        return "YES"
    if text in {"no", "n", "false"}:
        return "NO"
    return None


def _mention(text: str, pattern: re.Pattern[str]) -> bool | None:
    yes = False
    no = False
    for match in pattern.finditer(text):
        window = text[max(0, match.start() - 24) : match.start()]
        if _NEG_RE.search(window):
            no = True
        else:
            yes = True
    if yes:
        return True
    if no:
        return False
    return None


def feature_label(code: str) -> str:
    feat = BY_CODE.get(code)
    return feat.label if feat else code.replace("_", " ").title()
