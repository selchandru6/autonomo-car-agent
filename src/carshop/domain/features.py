"""Prioritized feature evidence (requirements 7.7). Each feature has its own row.

Nothing is inferred from another package. 5AS / Active Cruise Control / Traffic Jam
Assistant never read as DAP. A sticker option list that lacks the codes and phrases is the
only path to DAP NO.
"""

from __future__ import annotations

import re

from carshop.domain.codes import Feature, SourceKind, Sufficiency, Tri
from carshop.domain.models import EvidenceQuote, FeatureEvidence

# Exact phrases / codes, each compiled for case-insensitive search. Quotes are extracted
# as exact substrings of the page text so they satisfy the substring rule in 7.9.
_DAP_PHRASE = re.compile(r"Driving Assistan(?:ce|t) Professional(?: Package)?", re.I)
_DAP_CODE = re.compile(r"\b(?:ZDY|5AU)\b")  # case-sensitive; lowercase "zdy" is not the option code
_DAP_NEG_ONLY = re.compile(
    r"(\b5AS\b|Driving Assistant(?! Professional)|Active Cruise Control|Traffic Jam Assistant)",
    re.I,
)
_UPSELL = re.compile(r"\b(add|available|optional|upgrade to|for \$)\b", re.I)

_FEATURE_PATTERNS: dict[Feature, re.Pattern[str]] = {
    Feature.M_SPORT: re.compile(r"M Sport Package(?: \(337\))?", re.I),
    Feature.MASSAGE: re.compile(
        r"(Massag(?:e|ing) (?:Function|Seats?)|Multi-?function Seats? with Massage|\bMassage\b)",
        re.I,
    ),
    Feature.PANORAMIC_ROOF: re.compile(
        r"(Panoramic (?:Moon|Sun)?roof|Panoramic Roof|Panoramic Glass Roof)", re.I
    ),
    Feature.SKY_LOUNGE: re.compile(r"(Panoramic Sky Lounge(?: LED Roof)?|Sky Lounge)", re.I),
    Feature.EXECUTIVE: re.compile(r"(Executive Package)", re.I),
    Feature.CLIMATE_COMFORT: re.compile(r"(Climate Comfort Package)", re.I),
    Feature.AIR_SUSPENSION: re.compile(r"((?:2-Axle |Adaptive )?Air Suspension)", re.I),
}


def _quote(text: str, m: re.Match[str], pad: int = 40) -> str:
    start = max(0, m.start() - pad)
    end = min(len(text), m.end() + pad)
    return text[start:end]


def _upsell(text: str, match: re.Match[str]) -> bool:
    window = text[max(0, match.start() - 60) : match.end() + 24]
    return _UPSELL.search(window) is not None


def _first_dap(text: str) -> re.Match[str] | None:
    for pattern in (_DAP_CODE, _DAP_PHRASE):
        for match in pattern.finditer(text):
            if not _upsell(text, match):
                return match
    return None


def evaluate_dap(
    text: str, *, source_kind: SourceKind, url: str, page_hash: str, is_parsed_sticker_options: bool
) -> FeatureEvidence:
    m = _first_dap(text)
    if m:
        return FeatureEvidence(
            feature=Feature.DAP,
            status=Tri.YES,
            sufficiency=Sufficiency.VERIFIED,
            evidence=EvidenceQuote(
                quote=_quote(text, m), source_kind=source_kind, url=url, page_hash=page_hash
            ),
        )
    if is_parsed_sticker_options and source_kind is SourceKind.STICKER:
        neg = _DAP_NEG_ONLY.search(text)
        q = _quote(text, neg) if neg else text[:80]
        return FeatureEvidence(
            feature=Feature.DAP,
            status=Tri.NO,
            sufficiency=Sufficiency.VERIFIED,
            evidence=EvidenceQuote(quote=q, source_kind=source_kind, url=url, page_hash=page_hash),
        )
    return FeatureEvidence(
        feature=Feature.DAP, status=Tri.UNKNOWN, sufficiency=Sufficiency.INSUFFICIENT
    )


def evaluate_features(
    text: str,
    *,
    source_kind: SourceKind,
    url: str,
    page_hash: str,
    is_parsed_sticker_options: bool = False,
    dap_text: str | None = None,
) -> list[FeatureEvidence]:
    """`dap_text` is the car's own package list. DAP is not read from the rest of the page."""
    out: list[FeatureEvidence] = [
        evaluate_dap(
            text if dap_text is None else dap_text,
            source_kind=source_kind,
            url=url,
            page_hash=page_hash,
            is_parsed_sticker_options=is_parsed_sticker_options,
        )
    ]
    for feat, pat in _FEATURE_PATTERNS.items():
        m = pat.search(text)
        if m is None and feat is Feature.M_SPORT:
            code = re.search(r"\bZMP\b", text)
            m = code
        if m:
            out.append(
                FeatureEvidence(
                    feature=feat,
                    status=Tri.YES,
                    sufficiency=Sufficiency.VERIFIED,
                    evidence=EvidenceQuote(
                        quote=_quote(text, m), source_kind=source_kind, url=url, page_hash=page_hash
                    ),
                )
            )
        else:
            # Absence is NO only for DAP, and only from a parsed sticker option list.
            out.append(
                FeatureEvidence(
                    feature=feat, status=Tri.UNKNOWN, sufficiency=Sufficiency.INSUFFICIENT
                )
            )
    return out


def resolved_dap(features: list[FeatureEvidence]) -> Tri:
    """A YES/NO conflict is not a shortlist match."""
    rows = [f for f in features if f.feature is Feature.DAP]
    if any(f.sufficiency is Sufficiency.CONFLICT for f in rows):
        return Tri.UNKNOWN
    return next((f.status for f in rows), Tri.UNKNOWN)


def merge_feature_sets(
    primary: list[FeatureEvidence], secondary: list[FeatureEvidence]
) -> list[FeatureEvidence]:
    """YES beats UNKNOWN. YES vs NO keeps both evidence rows and marks each CONFLICT."""
    by_feat: dict[Feature, list[FeatureEvidence]] = {}
    for row in (*primary, *secondary):
        by_feat.setdefault(row.feature, []).append(row)
    out: list[FeatureEvidence] = []
    for feat, rows in by_feat.items():
        known = [r for r in rows if r.status is not Tri.UNKNOWN]
        statuses = {r.status for r in known}
        if len(statuses) > 1:
            for row in known:
                out.append(
                    FeatureEvidence(
                        feature=feat,
                        status=row.status,
                        sufficiency=Sufficiency.CONFLICT,
                        evidence=row.evidence,
                    )
                )
        elif known:
            out.append(known[0])
        else:
            out.append(rows[0])
    return out
