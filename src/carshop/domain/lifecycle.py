"""Deltas and lifecycle (requirements 7.8). Pure functions over two observation sets."""

from __future__ import annotations

from dataclasses import dataclass, field

from carshop.domain.codes import LifecycleState, PriceEvent
from carshop.domain.pricing import price_event


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Minimal view of one dealer's REPORTED run: vin -> comparison_price_cents."""

    run_id: str
    prices: dict[str, int | None]


@dataclass(frozen=True, slots=True)
class PriceChange:
    vin: str
    event: PriceEvent
    before_cents: int | None
    after_cents: int | None
    delta_cents: int


@dataclass(slots=True)
class Delta:
    baseline_run_id: str | None
    added: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    relisted: list[str] = field(default_factory=list)
    price_changes: list[PriceChange] = field(default_factory=list)


def compute_delta(
    today: Snapshot, baseline: Snapshot | None, *, missing_unverified: set[str] | None = None
) -> Delta:
    """Compare a REPORTED run with the dealer's last REPORTED run.

    The caller MUST only invoke this when today's dealer status is REPORTED. PARTIAL and
    NOT_REPORTED never emit missing events (enforced in the pipeline, asserted here).
    """
    if baseline is None:
        return Delta(baseline_run_id=None, added=sorted(today.prices))
    delta = Delta(baseline_run_id=baseline.run_id)
    prior_missing = missing_unverified or set()
    for vin in sorted(today.prices):
        if vin not in baseline.prices:
            if vin in prior_missing:
                delta.relisted.append(vin)
            else:
                delta.added.append(vin)
        else:
            ev = price_event(baseline.prices[vin], today.prices[vin])
            if ev is not None:
                delta.price_changes.append(
                    PriceChange(
                        vin=vin,
                        event=ev[0],
                        before_cents=baseline.prices[vin],
                        after_cents=today.prices[vin],
                        delta_cents=ev[1],
                    )
                )
    for vin in sorted(baseline.prices):
        if vin not in today.prices:
            delta.missing.append(vin)
    return delta


def next_state(
    current: LifecycleState | None, *, seen_today: bool, reported: bool
) -> LifecycleState | None:
    """State transition. Returns None when nothing changes."""
    if not reported:
        return None  # a failed or partial read changes nothing
    if seen_today:
        if current in (None, LifecycleState.UNKNOWN):
            return LifecycleState.ACTIVE
        if current is LifecycleState.MISSING_UNVERIFIED:
            return LifecycleState.RELISTED
        if current is LifecycleState.RELISTED:
            return LifecycleState.ACTIVE
        return None
    if current in (LifecycleState.ACTIVE, LifecycleState.RELISTED):
        return LifecycleState.MISSING_UNVERIFIED
    return None
