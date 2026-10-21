"""Feature vocabulary for the workbook catalog.

A feature is YES when a sheet names it, NO only when a sheet says so in a yes/no column,
and otherwise not stored (the screen calls that "not shown"). An empty package cell is not
evidence that the car lacks the feature.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class FeatureDef(NamedTuple):
    code: str
    label: str
    pattern: re.Pattern[str]
    explicit_headers: tuple[str, ...] = ()


def _p(expr: str) -> re.Pattern[str]:
    return re.compile(expr, re.I)


FEATURES: tuple[FeatureDef, ...] = (
    FeatureDef(
        "DAP",
        "Driving Assistance Professional",
        _p(r"driving assistance professional"),
        ("Driving Assistance Professional",),
    ),
    FeatureDef("MASSAGE", "Front massage seats", _p(r"massage"), ("Front massage seats",)),
    FeatureDef("PARKING", "Parking Assistance", _p(r"parking assistance")),
    FeatureDef("PREMIUM", "Premium package", _p(r"premium package")),
    FeatureDef("CLIMATE", "Climate Comfort", _p(r"climate comfort")),
    FeatureDef("M_SPORT_PRO", "M Sport Professional", _p(r"m sport professional")),
    FeatureDef("M_SPORT", "M Sport package", _p(r"m sport(?! professional)")),
    FeatureDef("EXECUTIVE", "Executive package", _p(r"executive package")),
    FeatureDef("SPORT_SEATS", "Sport seats", _p(r"sport seats")),
    FeatureDef("HEATED_SEATS", "Heated seats", _p(r"heated seats")),
    FeatureDef("VENTILATED", "Ventilated seats", _p(r"ventilated")),
    FeatureDef("PANORAMIC", "Panoramic roof / sunroof", _p(r"panoramic|sunroof|moonroof")),
    FeatureDef("SKY_LOUNGE", "Sky Lounge roof", _p(r"sky lounge")),
    FeatureDef("PREMIUM_SOUND", "Premium sound", _p(r"harman kardon|premium sound|bowers")),
    FeatureDef(
        "AIR_SUSPENSION", "Air suspension", _p(r"air suspension|adaptive (?:m )?suspension|2-axle")
    ),
    FeatureDef("HEAD_UP", "Head-up display", _p(r"head-?up display")),
    FeatureDef("HEATED_WHEEL", "Heated steering wheel", _p(r"heated steering")),
    FeatureDef("REMOTE_START", "Remote start", _p(r"remote start")),
    FeatureDef("NAVIGATION", "Navigation", _p(r"navigation")),
    FeatureDef("BLIND_SPOT", "Blind spot monitor", _p(r"blind spot")),
    FeatureDef("WHEELS_22", '22" wheels', _p(r'22\s*(?:"|inch|\')')),
    FeatureDef("WHEELS_21", '21" wheels', _p(r'21\s*(?:"|inch|\')')),
)

BY_CODE: dict[str, FeatureDef] = {f.code: f for f in FEATURES}

# "Has the three packages" on the Oct 13 similar-cars sheet means the Dallas reference
# car's three: Driving Assistance Professional, Premium, and Parking Assistance.
THREE_PACKAGES = ("DAP", "PREMIUM", "PARKING")
