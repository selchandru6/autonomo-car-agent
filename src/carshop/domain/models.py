"""Domain records exchanged between pipeline steps. Pure data, no I/O."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from carshop.domain.codes import (
    DealerStatus,
    ExclusionCode,
    ExtractionMethod,
    Feature,
    Flag,
    Platform,
    PriceKind,
    ReasonCode,
    SourceKind,
    Sufficiency,
    TermsStatus,
    Tri,
)


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Dealer(Frozen):
    """One registry row (requirements 7.1, v1.3).

    Identity and hostname come from the BMW USA locator snapshot; start URL, path prefixes and
    platform come from discovery (F25) unless an operator override is set. `terms_status` is
    recorded for audit and does not gate fetching (owner blanket approval, 2026-10-19).
    """

    dealer_id: str
    center_number: str | None
    name: str
    city: str
    state: str = "TX"
    hostname: str  # may be "" when the locator lists no website (then NO_SOURCE)
    start_url: str | None
    path_prefixes: tuple[str, ...]
    platform: Platform
    terms_status: TermsStatus = TermsStatus.OWNER_ACCEPTED_RISK
    sticker_hosts: tuple[str, ...] = ()
    active: bool = True
    notes: str | None = None
    # v1.3 provenance
    locator_url: str | None = None  # website exactly as the locator printed it
    discovery_reason: str | None = None  # DiscoveryReason value
    discovered_at: str | None = None  # ISO time of the discovery that set start_url
    source: str = "locator"  # locator | override | seed
    discovery_fail_streak: int = 0  # consecutive refreshes whose homepage could not be reached

    @property
    def fetchable(self) -> bool:
        """Every active registry dealer with a start URL is attempted; no approval gate."""
        return self.active and bool(self.start_url) and bool(self.hostname)


class EvidenceQuote(Frozen):
    quote: str
    source_kind: SourceKind
    url: str
    page_hash: str


class FeatureEvidence(Frozen):
    feature: Feature
    status: Tri
    sufficiency: Sufficiency
    evidence: EvidenceQuote | None = None


class RawListing(BaseModel):
    """Adapter output: whatever the page said, not yet classified."""

    model_config = ConfigDict(extra="forbid")

    url: str
    page_hash: str
    source_kind: SourceKind
    vin: str | None = None
    stock: str | None = None
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    condition_text: str | None = None
    availability_text: str | None = None
    exterior_raw: str | None = None
    interior_raw: str | None = None
    odometer_miles: int | None = None
    price_pairs: list[tuple[str, str]] = Field(default_factory=list)
    package_text: str | None = None
    full_text: str = ""
    sticker_url: str | None = None
    detail_url: str | None = None
    quotes: dict[str, str] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    image_url: str | None = None


class PriceComponentRecord(Frozen):
    kind: PriceKind
    cents: int | None
    label: str
    eligibility_text: str | None = None
    quote: str | None = None


class Observation(Frozen):
    vin: str
    dealer_id: str
    source_url: str
    detail_url: str | None
    page_hash: str
    fetched_at: datetime
    stock: str | None
    year: int
    model: str
    trim: str
    ag_code: str
    exterior_raw: str | None
    exterior_code: str | None
    interior_raw: str | None
    interior_code: str | None
    odometer_miles: int | None
    availability: str
    in_transit: bool
    msrp_cents: int | None
    advertised_price_cents: int | None
    comparison_price_cents: int | None
    prices: tuple[PriceComponentRecord, ...]
    features: tuple[FeatureEvidence, ...]
    dap_status: Tri
    m_sport: bool
    flags: frozenset[Flag]
    extraction_method: ExtractionMethod
    adapter_version: str
    image_url: str | None = None
    alternate_urls: tuple[str, ...] = ()
    sticker_url: str | None = None
    price_basis: str = "unknown"


class Exclusion(Frozen):
    vin: str | None
    url: str
    dealer_id: str
    code: ExclusionCode
    quote: str


class ClassifiedListing(Frozen):
    observation: Observation | None
    exclusion: Exclusion | None
    needs_review: str | None = None
    parse_failed: bool = False


class FetchAttempt(Frozen):
    url: str
    final_url: str | None
    http_status: int | None
    attempt: int
    latency_ms: int
    robots_decision: str
    content_hash: str | None
    storage_uri: str | None
    error_code: ReasonCode | None
    method: str = "GET"
    renderer: str = "http"


class DealerResult(Frozen):
    dealer_id: str
    status: DealerStatus
    reason: ReasonCode | None
    pages_expected: int
    pages_read: int
    pages_failed: int
    raw_count: int
    eligible_count: int
    duration_ms: int
    cost_usd: float
    anomaly: bool
    observations: tuple[Observation, ...]
    exclusions: tuple[Exclusion, ...]
    attempts: tuple[FetchAttempt, ...]
    review_reasons: tuple[tuple[str, str], ...] = ()  # (vin, issue)
    robots_hash: str | None = None
    adapter_version: str | None = None
