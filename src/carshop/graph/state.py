"""Parent run state (requirements-mas.md §4.3).

Primitives only, so every checkpoint is JSON-friendly and holds no page text (§5 invariant 1).
Live objects (engine, fetchers, browser) travel in `RunContext`, never in state.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from carshop.graph.reducers import add_usage, merge_by_dealer_id

Phase = Literal["admit", "plan", "collect", "compare", "publish", "done", "failed"]


class RunMeta(TypedDict):
    run_id: str
    snapshot_date: str
    trigger: str
    git_sha: str


class DealerTask(TypedDict):
    """The `Send` payload for one dealer branch."""

    dealer_id: str
    prior_eligible_count: int | None
    baseline_run_id: str | None


class CompareCounts(TypedDict):
    added: int
    missing: int
    relisted: int
    price_changes: int
    transfer_suspected: list[str]


class DealerOutcome(TypedDict):
    """What one dealer branch hands back. Counts and codes only, never listings."""

    status: str
    reason: str | None
    pages_read: int
    raw_count: int
    eligible_count: int
    duration_ms: int
    cost_usd: float
    stored: bool
    compare: CompareCounts | None


class Deltas(TypedDict):
    transfer_suspected: list[str]


class Published(TypedDict):
    status: str
    model_status: str
    model_spend_usd: float
    wall_clock_s: int


class Notifications(TypedDict):
    sent: dict[str, str]
    backup_path: str | None


class RunState(TypedDict, total=False):
    # Supervisor
    run: RunMeta
    dealer_queue: list[DealerTask]
    phase: Phase
    published: Published
    # Registry
    registry_stale: bool
    registry_version: str
    coverage_gaps: list[str]
    # Dealer branch (Collector .. Recorder)
    outcomes: Annotated[dict[str, DealerOutcome], merge_by_dealer_id]
    model_usage: Annotated[dict[str, float], add_usage]
    # Comparator
    deltas: Deltas
    # Notifier
    notifications: Notifications
