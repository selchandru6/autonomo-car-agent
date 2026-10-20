"""Money handling (requirements 7.6). Integer cents, NULL for unknown, zero never stands in."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from carshop.domain.codes import Flag, PriceEvent, PriceKind

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

    @property
    def msrp_cents(self) -> int | None:
        return next((c.cents for c in self.components if c.kind is PriceKind.MSRP), None)

    @property
    def advertised_price_cents(self) -> int | None:
        return next((c.cents for c in self.components if c.kind is PriceKind.ADVERTISED), None)

    def comparison_price_cents(self, band_min: int, band_max: int) -> int | None:
        adv = self.advertised_price_cents
        if adv is None:
            return None
        if adv < band_min or adv > band_max:
            self.flags.add(Flag.PRICE_IMPLAUSIBLE)
            return None
        return adv


_ADVERTISED_LABELS = (
    "sale price", "selling price", "our price", "dealer price", "internet price", "your price",
    "price", "total price", "e-price", "eprice", "net price", "advertised price",
)  # fmt: skip
_MSRP_LABELS = ("msrp", "retail price", "sticker price", "suggested retail")
_FEE_LABELS = ("doc fee", "documentary fee", "dealer fee", "destination", "freight", "processing")


def classify_price_label(label: str) -> PriceKind | None:
    """Map a price-stack label to a component kind. Unknown labels return None."""
    key = re.sub(r"\(.*?\)", "", label).strip().lower().rstrip(":").strip()
    if _PAYMENT_RE.search(key):
        return PriceKind.PAYMENT
    if any(k in key for k in _MSRP_LABELS):
        return PriceKind.MSRP
    if any(k in key for k in _FEE_LABELS):
        return PriceKind.FEE
    if (
        _CONDITIONAL_RE.search(key)
        or "rebate" in key
        or "incentive" in key
        or "savings" in key
        or "discount" in key
    ):
        return PriceKind.CONDITIONAL_INCENTIVE
    if any(k == key or key.endswith(k) for k in _ADVERTISED_LABELS):
        return PriceKind.ADVERTISED
    return None


def build_price_set(pairs: list[tuple[str, str]]) -> PriceSet:
    """Build a PriceSet from (label, value_text) pairs in page order.

    Pairs with labels that are neither MSRP, fee, payment, incentive, nor advertised become
    ADDON (e.g. "Window Tint $599"). Payments never become the advertised price.
    """
    ps = PriceSet()
    for label, value in pairs:
        cents = parse_cents(value)
        kind = classify_price_label(label)
        if kind is None:
            if _CALL_RE.search(value) or _CALL_RE.search(label):
                ps.flags.add(Flag.CALL_FOR_PRICE)
                continue
            if cents is None:
                continue
            kind = PriceKind.ADDON
        if kind is PriceKind.PAYMENT:
            ps.flags.add(Flag.PRICE_IS_PAYMENT)
        if kind is PriceKind.ADVERTISED and ps.advertised_price_cents is not None:
            # Keep the first advertised figure; later ones (e.g. "with incentives") are conditional.
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
