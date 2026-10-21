"""Money handling (requirements 7.6). Integer cents, NULL for unknown, zero never stands in."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from carshop.domain.codes import Flag, PriceBasis, PriceEvent, PriceKind

_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
_PAYMENT_RE = re.compile(
    r"(/\s*mo\b|per\s+month|monthly|due at signing|lease|\bbi-?weekly\b)", re.I
)
_CALL_RE = re.compile(
    r"(call\s+for\s+price|contact\s+(us|dealer)\s+for\s+pric|please\s+call|inquire)", re.I
)
_CONDITIONAL_RE = re.compile(
    r"(loyalty|conquest|military|college|grad|first\s+responder|usaa|with\s+approved\s+credit|"
    r"financ(e|ing)\s+(bonus|credit|offer)|qualif(y|ied|ying)|eligib)",
    re.I,
)


def parse_cents(text: str) -> int | None:
    """Parse the first dollar amount into integer cents. None when absent."""
    m = _MONEY_RE.search(text)
    if not m:
        return None
    dollars = int(m.group(1).replace(",", ""))
    cents = int(m.group(2)) if m.group(2) else 0
    value = dollars * 100 + cents
    return value if value > 0 else None


def fmt_cents(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:,.0f}"


@dataclass(slots=True)
class PriceComponent:
    kind: PriceKind
    cents: int | None
    label: str
    eligibility_text: str | None = None
    quote: str | None = None


@dataclass(slots=True)
class PriceSet:
    components: list[PriceComponent] = field(default_factory=list)
    flags: set[Flag] = field(default_factory=set)
    price_basis: PriceBasis = PriceBasis.UNKNOWN

    @property
    def msrp_cents(self) -> int | None:
        return next((c.cents for c in self.components if c.kind is PriceKind.MSRP), None)

    @property
    def advertised_price_cents(self) -> int | None:
        return next((c.cents for c in self.components if c.kind is PriceKind.ADVERTISED), None)

    def comparison_price_cents(self, band_min: int, band_max: int) -> int | None:
        """Vehicle-only cash price (ADR 0005). Fee-inclusive totals are reduced when the page itemizes the fees."""
        adv = next((c for c in self.components if c.kind is PriceKind.ADVERTISED), None)
        if adv is None or adv.cents is None:
            self.price_basis = PriceBasis.UNKNOWN
            return None
        vehicle, basis = _vehicle_amount(adv, self)
        self.price_basis = basis
        if vehicle is None:
            return None
        if vehicle < band_min or vehicle > band_max:
            self.flags.add(Flag.PRICE_IMPLAUSIBLE)
            return None
        return vehicle


_ADVERTISED_LABELS = (
    "sale price", "selling price", "our price", "dealer price", "internet price", "your price",
    "price", "e-price", "eprice", "advertised price", "transparent price", "bert ogden price",
    "cardenas price",
)  # fmt: skip
_MSRP_LABELS = ("msrp", "retail price", "sticker price", "suggested retail")
_FEE_LABELS = (
    "doc fee",
    "documentary fee",
    "dealer fee",
    "destination",
    "freight",
    "processing",
    "service fee",
)
# Common dealer add-ons. `package` counts only next to the word "dealer" (a store package),
# so a factory name such as "M Sport Package" stays unclassified (review N1).
_ADDON_PARTS = ("tint", "accessor", "protection", "appearance")
_CONDITIONAL_LABEL = re.compile(
    r"\b(conditional|final|after|net of)\b|with (?:all )?(?:offers|incentives|rebates)",
    re.I,
)
_TOTAL_LABEL = re.compile(r"total price|with accessor", re.I)
_STATED_CASH = re.compile(r"transparent price|no hidden fee", re.I)


def normalize_label(label: str) -> str:
    """Split concatenated dealer labels (`PriceNo Hidden Fees`) and drop parentheticals."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", label)
    spaced = re.sub(r"\s+", " ", spaced)
    return re.sub(r"\(.*?\)", "", spaced).strip().lower().rstrip(":").strip()


def classify_price_label(label: str, value: str = "") -> PriceKind | None:
    """Map a price-stack label (and its value) to a component kind. Unknown labels return None."""
    key = normalize_label(label)
    blob = f"{key} {value}".strip()
    if _PAYMENT_RE.search(blob) or re.search(r"finance for|lease for", blob, re.I):
        return PriceKind.PAYMENT
    if any(k in key for k in _MSRP_LABELS):
        return PriceKind.MSRP
    if any(k in key for k in _FEE_LABELS):
        return PriceKind.FEE
    if any(part in key for part in _ADDON_PARTS) or ("package" in key and "dealer" in key):
        return PriceKind.ADDON
    if (
        _CONDITIONAL_LABEL.search(key)
        or _CONDITIONAL_RE.search(key)
        or "rebate" in key
        or "incentive" in key
        or "savings" in key
        or "discount" in key
    ):
        return PriceKind.CONDITIONAL_INCENTIVE
    if _is_advertised(key):
        return PriceKind.ADVERTISED
    return None


def _is_advertised(key: str) -> bool:
    for label in _ADVERTISED_LABELS:
        if key == label or key.endswith(" " + label) or key.startswith(label + " "):
            return True
        if label != "price" and key.startswith(label):
            return True
    return False


def _vehicle_amount(adv: PriceComponent, ps: PriceSet) -> tuple[int | None, PriceBasis]:
    """Subtract itemized fees and add-ons when they are inside the advertised figure."""
    assert adv.cents is not None
    label = normalize_label(adv.label)
    extras = [
        c.cents
        for c in ps.components
        if c.kind in (PriceKind.FEE, PriceKind.ADDON, PriceKind.UNCLASSIFIED)
        and c.cents is not None
        and c.cents > 0
    ]
    extra = sum(extras)
    msrp = ps.msrp_cents
    included = 0
    if (msrp is not None and extra and abs(adv.cents - (msrp + extra)) <= 100) or (
        _TOTAL_LABEL.search(label) and extra
    ):
        included = extra
    if _TOTAL_LABEL.search(label) and included == 0 and not _STATED_CASH.search(label):
        return None, PriceBasis.INCLUDES_FEES
    if _STATED_CASH.search(label) or included or not _TOTAL_LABEL.search(label):
        return adv.cents - included, PriceBasis.VEHICLE_ONLY
    return None, PriceBasis.INCLUDES_FEES


def build_price_set(pairs: list[tuple[str, str]]) -> PriceSet:
    """Build a PriceSet from (label, value_text) pairs in page order.

    Unknown labels stay UNCLASSIFIED (never a silent add-on, never the comparison price).
    A payment in the value (`$899/mo`, `Or Finance For`) is never the advertised price.
    """
    ps = PriceSet()
    for label, value in pairs:
        cents = parse_cents(value)
        kind = classify_price_label(label, value)
        if kind is None:
            if _CALL_RE.search(value) or _CALL_RE.search(label):
                ps.flags.add(Flag.CALL_FOR_PRICE)
                continue
            if cents is None:
                continue
            kind = PriceKind.UNCLASSIFIED
            ps.flags.add(Flag.PRICE_LABEL_UNKNOWN)
        if kind is PriceKind.PAYMENT:
            ps.flags.add(Flag.PRICE_IS_PAYMENT)
        if kind is PriceKind.ADVERTISED and ps.advertised_price_cents is not None:
            kind = PriceKind.CONDITIONAL_INCENTIVE
        if kind is PriceKind.ADVERTISED and _CALL_RE.search(value):
            ps.flags.add(Flag.CALL_FOR_PRICE)
            continue
        ps.components.append(
            PriceComponent(
                kind=kind,
                cents=cents,
                label=label.strip(),
                quote=f"{label.strip()} {value.strip()}".strip(),
            )
        )
    if ps.advertised_price_cents is None:
        if any(c.kind is PriceKind.CONDITIONAL_INCENTIVE for c in ps.components):
            ps.flags.add(Flag.CONDITIONAL_ONLY)
        if Flag.CALL_FOR_PRICE not in ps.flags and Flag.PRICE_IS_PAYMENT not in ps.flags:
            ps.flags.add(Flag.PRICE_MISSING)
    return ps


def price_event(before: int | None, after: int | None) -> tuple[PriceEvent, int] | None:
    """Same-VIN comparison-price event. Both present and |delta| >= $1 is a change."""
    if before is None and after is None:
        return None
    if before is None:
        return (PriceEvent.APPEARED, 0)
    if after is None:
        return (PriceEvent.WITHDRAWN, 0)
    delta = after - before
    if abs(delta) < 100:
        return None
    return (PriceEvent.DROP if delta < 0 else PriceEvent.INCREASE, delta)
