"""Versioned reference data (requirements 7.2 and 7.7).

Source: BMW of North America X5 (G05 LCI) ordering guide, MY2026, effective 13 Jan 2025.
Section 16 item 3: confirm on one official 2026 window sticker before freezing.
"""

from __future__ import annotations

from carshop.domain.codes import ExclusionCode

REFERENCE_VERSION = "2026-og-v1"

# AG code -> (vehicle, exclusion or None when in scope)
AG_CODES: dict[str, tuple[str, ExclusionCode | None]] = {
    "23EU": ("X5 xDrive40i", None),
    "13EU": ("X5 sDrive40i", ExclusionCode.NOT_XDRIVE40I),
    "43EU": ("X5 xDrive50e", ExclusionCode.POWERTRAIN_OTHER),
    "33EU": ("X5 M60i", ExclusionCode.POWERTRAIN_OTHER),
}

EXTERIOR_COLORS: dict[str, str] = {
    "300": "Alpine White",
    "416": "Carbon Black Metallic",
    "475": "Black Sapphire Metallic",
    "A96": "Mineral White Metallic",
    "C35": "Blue Ridge Mountain Metallic",
    "C36": "Dravit Grey Metallic",
    "C3D": "Manhattan Green Metallic",
    "C3Z": "Tanzanite Blue II Metallic",
    "C4P": "Brooklyn Grey Metallic",
    "C4W": "Skyscraper Grey Metallic",
    "C5A": "Frozen Pure Grey Metallic",
}

UPHOLSTERY: dict[str, str] = {
    "KPA9": "Silverstone Sensafin",
    "KPHF": "Coffee Sensafin",
    "KPRI": "Cognac Sensafin",
    "KPSW": "Black Sensafin",
    "VAEW": "Ivory White Extended Merino",
    "VAHF": "Coffee Extended Merino",
    "VASW": "Black Extended Merino",
    "VATQ": "Tartufo Extended Merino",
}

GREY_EXTERIOR_CODES = frozenset({"C36", "C4P", "C4W", "C5A"})
BLACK_INTERIOR_CODES = frozenset({"KPSW", "VASW"})

# Color families for saved searches (requirements-shortlist.md §5.4). Keyed by code; a raw
# string with no mapped code belongs to no family. The migration seeds `color_families` from this.
COLOR_FAMILY_VERSION = "families-v1"
EXTERIOR_FAMILIES: dict[str, str] = {
    "C4W": "Grey", "C4P": "Grey", "C36": "Grey", "C5A": "Grey",
    "300": "White", "A96": "White",
    "416": "Black", "475": "Black",
    "C3Z": "Blue", "C35": "Blue",
    "C3D": "Green",
}  # fmt: skip
INTERIOR_FAMILIES: dict[str, str] = {
    "KPA9": "Light", "VAEW": "Light",
    "KPHF": "Brown", "KPRI": "Brown", "VAHF": "Brown", "VATQ": "Brown",
    "KPSW": "Black", "VASW": "Black",
}  # fmt: skip


def exterior_family(code: str | None) -> str | None:
    return EXTERIOR_FAMILIES.get(code) if code else None


def interior_family(code: str | None) -> str | None:
    return INTERIOR_FAMILIES.get(code) if code else None


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("gray", "grey").replace("metallic", "").split())


def match_exterior(raw: str | None) -> str | None:
    """Exact name after normalization, or a code token. Prefixes like "Black" or "M" do not match."""
    if not raw:
        return None
    n = _norm(raw)
    token = raw.strip().upper()
    if token in EXTERIOR_COLORS:
        return token
    for code, name in EXTERIOR_COLORS.items():
        official = _norm(name)
        if n == official or official in n:
            return code
    return None


def match_upholstery(raw: str | None) -> str | None:
    if not raw:
        return None
    n = _norm(raw)
    token = raw.strip().upper()
    if token in UPHOLSTERY:
        return token
    for code, name in UPHOLSTERY.items():
        if _norm(name) == n or _norm(name) in n:
            return code
    # Only names that are unambiguous in the ordering guide. "Black" and "Coffee" are both
    # Sensafin and Merino, so they stay unmapped.
    colour_only = {
        "cognac": "KPRI",
        "silverstone": "KPA9",
        "ivory white": "VAEW",
        "tartufo": "VATQ",
    }
    return colour_only.get(n)
