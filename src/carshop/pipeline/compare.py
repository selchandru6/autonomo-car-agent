"""Comparator: deltas against each dealer's last REPORTED run (requirements 7.8, F7, F18)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection

from carshop.domain.codes import DealerStatus, Flag, LifecycleState
from carshop.domain.lifecycle import Snapshot, compute_delta, next_state
from carshop.domain.models import DealerResult
from carshop.storage import repos


@dataclass(slots=True)
class CompareSummary:
    added: int = 0
    missing: int = 0
    relisted: int = 0
    price_changes: int = 0
    transfer_suspected: list[str] = field(default_factory=list)


def compare_dealer(
    conn: Connection, run_id: str, result: DealerResult, baseline_run_id: str | None
) -> CompareSummary:
    """Write lifecycle and price events for one dealer. Only a REPORTED dealer emits anything."""
    summary = CompareSummary()
    if result.status is not DealerStatus.REPORTED:
        return summary  # PARTIAL / NOT_REPORTED / NO_SOURCE never produce missing events
    pending = {vin for vin, _issue in result.review_reasons if vin}
    today = Snapshot(
        run_id=run_id,
        prices={
            o.vin: o.comparison_price_cents for o in result.observations if o.vin not in pending
        },
    )
    baseline = (
        Snapshot(
            run_id=baseline_run_id,
            prices=repos.reported_prices(conn, baseline_run_id, result.dealer_id),
        )
        if baseline_run_id
        else None
    )
    prior_missing = repos.missing_unverified_vins(conn, result.dealer_id)
    delta = compute_delta(
        today, baseline, missing_unverified=prior_missing, held=pending, status=result.status
    )

    for vin in delta.added:
        cur = repos.current_lifecycle_state(conn, result.dealer_id, vin)
        nxt = next_state(cur, seen_today=True, reported=True)
        if nxt is not None:
            repos.record_lifecycle(
                conn,
                vin=vin,
                dealer_id=result.dealer_id,
                run_id=run_id,
                from_state=cur,
                to_state=nxt,
                reason="added",
                baseline_run_id=baseline_run_id,
            )
        summary.added += 1
    for vin in delta.relisted:
        cur = repos.current_lifecycle_state(conn, result.dealer_id, vin)
        repos.record_lifecycle(
            conn,
            vin=vin,
            dealer_id=result.dealer_id,
            run_id=run_id,
            from_state=cur,
            to_state=LifecycleState.RELISTED,
            reason="relisted",
            baseline_run_id=baseline_run_id,
        )
        summary.relisted += 1
    for vin in delta.missing:
        cur = repos.current_lifecycle_state(conn, result.dealer_id, vin)
        nxt = next_state(cur, seen_today=False, reported=True)
        if nxt is not None:  # alert at most once per VIN
            repos.record_lifecycle(
                conn,
                vin=vin,
                dealer_id=result.dealer_id,
                run_id=run_id,
                from_state=cur,
                to_state=nxt,
                reason="absent from REPORTED run; not a sale",
                baseline_run_id=baseline_run_id,
            )
            summary.missing += 1
    # VINs present in both runs that are still RELISTED settle back to ACTIVE.
    if baseline is not None:
        for vin in today.prices:
            if vin in baseline.prices:
                cur = repos.current_lifecycle_state(conn, result.dealer_id, vin)
                nxt = next_state(cur, seen_today=True, reported=True)
                if nxt is not None:
                    repos.record_lifecycle(
                        conn,
                        vin=vin,
                        dealer_id=result.dealer_id,
                        run_id=run_id,
                        from_state=cur,
                        to_state=nxt,
                        reason="seen",
                        baseline_run_id=baseline_run_id,
                    )
    for pc in delta.price_changes:
        assert baseline_run_id is not None
        repos.record_price_event(
            conn,
            run_id=run_id,
            baseline_run_id=baseline_run_id,
            dealer_id=result.dealer_id,
            vin=pc.vin,
            event=pc.event.value,
            before=pc.before_cents,
            after=pc.after_cents,
            delta=pc.delta_cents,
        )
        summary.price_changes += 1
    return summary


def flag_cross_dealer(conn: Connection, run_id: str) -> list[str]:
    """F13: the same VIN at two dealers stays two observations and raises TRANSFER_SUSPECTED."""
    dupes = repos.dedup_cross_dealer(conn, run_id)
    for vin in dupes:
        repos.add_flag_to_observations(conn, run_id, vin, Flag.TRANSFER_SUSPECTED.value)
    return sorted(dupes)
