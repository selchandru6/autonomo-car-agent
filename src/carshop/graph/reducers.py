"""State reducers (requirements-mas.md §5 invariant 2).

Parallel dealer branches merge through these. Nothing appends to a shared list.
"""

from __future__ import annotations

from collections.abc import Mapping


def merge_by_dealer_id[V](
    current: Mapping[str, V] | None, update: Mapping[str, V] | None
) -> dict[str, V]:
    """Keyed merge: each branch writes only its own dealer id, so concurrent writes all survive.

    A later write for the same dealer replaces the earlier one; within one run only that
    dealer's own branch writes its key.
    """
    merged = dict(current or {})
    merged.update(update or {})
    return merged


def add_usage(
    current: Mapping[str, float] | None, update: Mapping[str, float] | None
) -> dict[str, float]:
    """Sums model usage counters across branches."""
    total = dict(current or {})
    for key, value in (update or {}).items():
        total[key] = round(total.get(key, 0.0) + value, 6)
    return total
