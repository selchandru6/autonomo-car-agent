"""Run state (requirements-mas.md §4.3).

`RunState` is the parent graph's state: primitives only, so every checkpoint is JSON-friendly
and holds no page text (§5 invariant 1). `DealerState` lives only while one dealer branch
runs; that subgraph is never checkpointed, and it is discarded when the branch returns its
`DealerOutcome`. Live objects (engine, fetchers, browser) travel in `RunContext`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict

from carshop.adapters.base import Adapter, PageParse
from carshop.domain.codes import ReasonCode
from carshop.domain.models import Exclusion, FeatureEvidence, FetchAttempt, Observation, RawListing
from carshop.graph.reducers import merge_by_dealer_id

Phase = Literal["admit", "plan", "collect", "compare", "publish", "done", "failed"]


class RunMeta(TypedDict):
    """Everything needed to rebuild the run's context on resume."""

    run_id: str
    snapshot_date: str
    trigger: str
    started_at: str
    git_sha: str
    dealer_ids: list[str] | None
    notify: bool
    rerun_of: str | None
    label: str | None
    use_browser: bool


class DealerTask(TypedDict):
    """The `Send` payload for one dealer branch."""

    dealer_id: str
    prior_eligible_count: int | None
    baseline_run_id: str | None


class CompareInput(TypedDict):
    """What the Comparator needs from one stored dealer: validated prices and held VINs."""

    status: str
    prices: dict[str, int | None]
    held: list[str]
    baseline_run_id: str | None


class DealerOutcome(TypedDict):
    """What one dealer branch hands back. Counts, codes, and VIN-keyed prices only.

    `status` / `reason` are what the Collector or Validator decided. When `stored` is false
    the Recorder could not commit them and wrote an explicit non-report instead.
    """

    status: str
    reason: str | None
    pages_read: int
    raw_count: int
    eligible_count: int
    duration_ms: int
    model_calls: int
    stored: bool
    store_error: str | None
    compare_input: CompareInput | None


class CompareCounts(TypedDict):
    added: int
    missing: int
    relisted: int
    price_changes: int


class Deltas(TypedDict):
    by_dealer: dict[str, CompareCounts]
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
    # Recorder (one key per dealer, merged across parallel branches)
    outcomes: Annotated[dict[str, DealerOutcome], merge_by_dealer_id]
    # Comparator
    deltas: Deltas
    # Notifier
    notifications: Notifications


# --- dealer subgraph ----------------------------------------------------------------------


@dataclass(slots=True)
class FetchRecord:
    """The Collector's account of one dealer's fetches. Pages are pointers and parses only."""

    adapter: Adapter | None
    started: float
    deadline: float
    attempts: list[FetchAttempt] = field(default_factory=list)
    pages_expected: int = 0
    pages_read: int = 0
    pages_failed: int = 0
    truncated: bool = False
    used_browser: bool = False
    robots_status: int | None = None
    robots_body: str | None = None
    robots_hash: str | None = None
    reason: ReasonCode | None = None


@dataclass(slots=True)
class Screening:
    """Validator bookkeeping from the listing pass."""

    parse_failures: int = 0
    alternates: dict[str, set[str]] = field(default_factory=dict)


@dataclass(slots=True)
class EnrichedUnit:
    vin: str
    observation: Observation
    detailed: bool  # the detail page was read and merged


@dataclass(slots=True)
class Enrichment:
    units: list[EnrichedUnit] = field(default_factory=list)
    attempts: list[FetchAttempt] = field(default_factory=list)
    details_failed: int = 0
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class AmbiguousText:
    """Package text the deterministic rules left UNKNOWN on a detail page."""

    text: str
    unknown: tuple[str, ...]
    url: str
    page_hash: str


@dataclass(frozen=True, slots=True)
class DealerVerdict:
    status: str
    reason: str | None
    anomaly: bool
    duration_ms: int


class DealerState(TypedDict, total=False):
    task: DealerTask
    # Collector
    fetch: FetchRecord
    pages: list[PageParse]
    fetch_status: DealerVerdict | None
    fetch_reason: str | None
    # Extractor
    listings: list[RawListing]
    model_usage: dict[str, int]
    resolutions: dict[str, list[FeatureEvidence]]
    # Validator
    eligible: list[Observation]
    exclusions: list[Exclusion]
    staging: list[tuple[str, str]]
    screening: Screening
    dealer_status: DealerVerdict
    # Enricher
    enrichment: Enrichment
    ambiguous_text: dict[str, AmbiguousText]
    # Recorder
    stored: bool
    outcome: DealerOutcome
