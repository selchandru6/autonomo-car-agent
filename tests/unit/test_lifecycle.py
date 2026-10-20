from carshop.domain.codes import LifecycleState, PriceEvent
from carshop.domain.lifecycle import Snapshot, compute_delta, next_state


def test_first_run_everything_is_added() -> None:
    d = compute_delta(Snapshot("r1", {"A": 100, "B": None}), None)
    assert d.added == ["A", "B"] and d.missing == [] and d.baseline_run_id is None


def test_delta_added_missing_price_change() -> None:
    base = Snapshot("r1", {"A": 79_100_00, "B": 80_000_00, "C": None})
    today = Snapshot("r2", {"A": 78_100_00, "C": 81_000_00, "D": 90_000_00})
    d = compute_delta(today, base)
    assert d.added == ["D"]
    assert d.missing == ["B"]
    events = {p.vin: p.event for p in d.price_changes}
    assert events == {"A": PriceEvent.DROP, "C": PriceEvent.APPEARED}


def test_relisted_when_previously_missing() -> None:
    d = compute_delta(Snapshot("r3", {"B": 1}), Snapshot("r2", {}), missing_unverified={"B"})
    assert d.relisted == ["B"] and d.added == []


def test_state_machine() -> None:
    assert next_state(None, seen_today=True, reported=True) is LifecycleState.ACTIVE
    assert (
        next_state(LifecycleState.ACTIVE, seen_today=False, reported=True)
        is LifecycleState.MISSING_UNVERIFIED
    )
    assert (
        next_state(LifecycleState.MISSING_UNVERIFIED, seen_today=True, reported=True)
        is LifecycleState.RELISTED
    )
    assert (
        next_state(LifecycleState.RELISTED, seen_today=True, reported=True) is LifecycleState.ACTIVE
    )
    # A failed or partial read changes nothing, ever.
    assert next_state(LifecycleState.ACTIVE, seen_today=False, reported=False) is None
    assert next_state(LifecycleState.MISSING_UNVERIFIED, seen_today=False, reported=True) is None
