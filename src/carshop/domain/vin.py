"""VIN validation and decoding (requirements 7.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

_TRANSLIT: dict[str, int] = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}  # fmt: skip
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

# Model-year letter codes for position 10 (ISO 3779 / NHTSA). 2026 = T.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def check_digit(vin: str) -> str:
    total = sum(_TRANSLIT[ch] * w for ch, w in zip(vin, _WEIGHTS, strict=True))
    rem = total % 11
    return "X" if rem == 10 else str(rem)


def is_valid(vin: str) -> bool:
    vin = vin.strip().upper()
    return bool(VIN_RE.match(vin)) and check_digit(vin) == vin[8]


def model_year(vin: str, *, base_2010_cycle: bool = True) -> int | None:
    """Decode position 10. BMW uses letters/digits in the 30-year cycle starting 2010 ('A')."""
    code = vin[9].upper()
    if code not in _YEAR_CODES:
        return None
    idx = _YEAR_CODES.index(code)
    return 2010 + idx if base_2010_cycle else 1980 + idx


def ag_code(vin: str) -> str:
    """BMW AG model code: positions 4-7 (1-based)."""
    return vin[3:7].upper()


@dataclass(frozen=True, slots=True)
class VinInfo:
    vin: str
    valid: bool
    year: int | None
    ag_code: str


def decode(vin: str) -> VinInfo:
    v = vin.strip().upper()
    ok = is_valid(v)
    return VinInfo(
        vin=v, valid=ok, year=model_year(v) if ok else None, ag_code=ag_code(v) if ok else ""
    )
