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
_DAP_YES = re.compile(r"(Driving Assistan(?:ce|t) Professional(?: Package)?|\bZDY\b|\b5AU\b)", re.I)
_DAP_NEG_ONLY = re.compile(
    r"(\b5AS\b|Driving Assistant(?! Professional)|Active Cruise Control|Traffic Jam Assistant)",
    re.I,
)

_FEATURE_PATTERNS: dict[Feature, re.Pattern[str]] = {
    Feature.M_SPORT: re.compile(r"(M Sport Package(?: \(337\))?|\bZMP\b|M SPORT PACKAGE)", re.I),
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


def evaluate_dap(
    text: str, *, source_kind: SourceKind, url: str, page_hash: str, is_parsed_sticker_options: bool
) -> FeatureEvidence:
    m = _DAP_YES.search(text)
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
) -> list[FeatureEvidence]:
    out: list[FeatureEvidence] = [
        evaluate_dap(
            text,
            source_kind=source_kind,
            url=url,
            page_hash=page_hash,
            is_parsed_sticker_options=is_parsed_sticker_options,
        )
    ]
    for feat, pat in _FEATURE_PATTERNS.items():
        m = pat.search(text)
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
        elif is_parsed_sticker_options and source_kind is SourceKind.STICKER:
            out.append(FeatureEvidence(feature=feat, status=Tri.NO, sufficiency=Sufficiency.VERIFIED,
                                       evidence=EvidenceQuote(quote=text[:80], source_kind=source_kind, url=url, page_hash=page_hash)))  # fmt: skip
        else:
            out.append(
                FeatureEvidence(
                    feature=feat, status=Tri.UNKNOWN, sufficiency=Sufficiency.INSUFFICIENT
                )
            )
    return out


def merge_feature_sets(
    primary: list[FeatureEvidence], secondary: list[FeatureEvidence]
) -> list[FeatureEvidence]:
    """Merge listing and sticker evidence. YES beats UNKNOWN; YES vs NO is a CONFLICT kept on both sides."""
    by_feat = {f.feature: f for f in primary}
    for s in secondary:
        p = by_feat.get(s.feature)
        if p is None or p.status is Tri.UNKNOWN:
            by_feat[s.feature] = s
        elif s.status is not Tri.UNKNOWN and s.status is not p.status:
            by_feat[s.feature] = FeatureEvidence(
                feature=p.feature,
                status=p.status,
                sufficiency=Sufficiency.CONFLICT,
                evidence=p.evidence,
            )
    return list(by_feat.values())
