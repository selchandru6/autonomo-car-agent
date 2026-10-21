"""SQLAlchemy Core schema (requirements section 10).

Money is integer cents. NULL means unknown. Timestamps are UTC ISO-8601 strings in SQLite.
`(vin, run_id)` is deliberately NOT unique: one VIN may appear at two dealers in one run.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

naming = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=naming)

runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("snapshot_date", String, nullable=False),  # America/Chicago civil date
    Column("trigger", String, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("ended_at", String),
    Column("config_version", String, nullable=False),
    Column("parser_version", String, nullable=False),
    Column("model_version", String),
    Column("model_status", String, nullable=False, default="down"),
    Column("git_sha", String, nullable=False),
    Column("rerun_of_run_id", String, ForeignKey("runs.run_id")),
    Column("model_spend_usd", Float, nullable=False, default=0.0),
    Column("label", String),  # e.g. "late" for a catch-up
    Index("ix_runs_snapshot_date", "snapshot_date"),
)

dealers = Table(
    "dealers",
    metadata,
    Column("dealer_id", String, primary_key=True),
    Column("center_number", String),
    Column("name", String, nullable=False),
    Column("city", String, nullable=False),
    Column("state", String, nullable=False),
    Column("hostname", String, nullable=False),  # NOT unique by design (7.1)
    Column("start_url", String),
    Column("path_prefixes", Text, nullable=False),  # JSON list
    Column("platform", String, nullable=False),
    Column("terms_status", String, nullable=False),
    Column("robots_status", String),
    Column("sticker_hosts", Text, nullable=False),  # JSON list
    Column("active", Boolean, nullable=False, default=True),
    Column("verified_at", String),
    Column("notes", Text),
    # v1.3 provenance (7.1, F25)
    Column("locator_url", String),
    Column("discovery_reason", String),
    Column("discovered_at", String),
    Column("source", String, nullable=False, server_default="locator"),
    Column("discovery_fail_streak", Integer, nullable=False, server_default="0"),
)

# One row per locator snapshot that was turned into a registry (F1: monthly refresh).
registry_snapshots = Table(
    "registry_snapshots",
    metadata,
    Column("snapshot_id", Integer, primary_key=True, autoincrement=True),
    Column("fetched_at", String, nullable=False),
    Column("sha256", String, nullable=False),
    Column("storage_uri", String),
    Column("dealer_count", Integer, nullable=False),
    Column("total_seen", Integer, nullable=False),
    Column("registry_version", String, nullable=False),
    Column("applied_at", String, nullable=False),
)

dealer_runs = Table(
    "dealer_runs",
    metadata,
    Column("run_id", String, ForeignKey("runs.run_id"), primary_key=True),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), primary_key=True),
    Column("status", String, nullable=False),
    Column("reason_code", String),
    Column("pages_expected", Integer, nullable=False, default=0),
    Column("pages_read", Integer, nullable=False, default=0),
    Column("pages_failed", Integer, nullable=False, default=0),
    Column("raw_count", Integer, nullable=False, default=0),
    Column("eligible_count", Integer, nullable=False, default=0),
    Column("duration_ms", Integer, nullable=False, default=0),
    Column("cost_usd", Float, nullable=False, default=0.0),
    Column("baseline_run_id", String, ForeignKey("runs.run_id")),
    Column("anomaly", Boolean, nullable=False, default=False),
    Column("robots_hash", String),
    Column("adapter_version", String),
    Column("finished_at", String),
    Index("ix_dealer_runs_dealer_run", "dealer_id", "run_id"),
)

units = Table(
    "units",
    metadata,
    Column("vin", String, primary_key=True),
    Column("model", String, nullable=False),
    Column("year", Integer, nullable=False),
    Column("ag_code", String, nullable=False),
    Column("first_seen_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
)

dealer_listings = Table(
    "dealer_listings",
    metadata,
    Column("listing_id", Integer, primary_key=True, autoincrement=True),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("vin", String, ForeignKey("units.vin"), nullable=False),
    Column("normalized_url", String, nullable=False),
    Column("alternate_urls", Text, nullable=False, default="[]"),
    Column("first_seen_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    UniqueConstraint("dealer_id", "vin", name="uq_dealer_listings_dealer_vin"),
)

observations = Table(
    "observations",
    metadata,
    Column("observation_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("vin", String, ForeignKey("units.vin"), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("stock", String),
    Column("year", Integer, nullable=False),
    Column("model", String, nullable=False),
    Column("trim", String, nullable=False),
    Column("exterior_raw", String),
    Column("exterior_code", String),
    Column("interior_raw", String),
    Column("interior_code", String),
    Column("odometer_miles", Integer),
    Column("availability", String, nullable=False),
    Column("in_transit", Boolean, nullable=False, default=False),
    Column("msrp_cents", Integer),
    Column("advertised_price_cents", Integer),
    Column("comparison_price_cents", Integer),
    Column("price_basis", String),
    Column("dap_status", String, nullable=False),
    Column("m_sport", Boolean, nullable=False, default=False),
    Column("flags", Text, nullable=False, default="[]"),
    Column("extraction_method", String, nullable=False),
    Column("adapter_version", String, nullable=False),
    Column("git_sha", String, nullable=False),
    Column("page_hash", String, nullable=False),
    Column("source_url", String, nullable=False),
    Column("detail_url", String),
    Column("sticker_url", String),
    Column("image_url", String),
    Column("fetched_at", String, nullable=False),
    Column("rejected", Boolean, nullable=False, default=False),
    UniqueConstraint(
        "run_id", "dealer_id", "vin", "version", name="uq_observations_run_dealer_vin_version"
    ),
    Index("ix_observations_dealer_run", "dealer_id", "run_id"),
    Index("ix_observations_vin_fetched", "vin", "fetched_at"),
)

observation_flags = Table(
    "observation_flags",
    metadata,
    Column("observation_id", Integer, ForeignKey("observations.observation_id"), primary_key=True),
    Column("flag", String, primary_key=True),
    Column("created_at", String, nullable=False),
)

exclusions = Table(
    "exclusions",
    metadata,
    Column("exclusion_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("vin", String),
    Column("url", String, nullable=False),
    Column("reason_code", String, nullable=False),
    Column("evidence_quote", Text, nullable=False),
    Index("ix_exclusions_dealer_run", "dealer_id", "run_id"),
)

fetch_attempts = Table(
    "fetch_attempts",
    metadata,
    Column("fetch_attempt_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("url", String, nullable=False),
    Column("final_url", String),
    Column("method", String, nullable=False),
    Column("renderer", String, nullable=False),
    Column("http_status", Integer),
    Column("attempt", Integer, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("robots_decision", String, nullable=False),
    Column("content_hash", String),
    Column("storage_uri", String),
    Column("error_code", String),
    Column("fetched_at", String, nullable=False),
    Index("ix_fetch_attempts_dealer_run", "dealer_id", "run_id"),
)

vehicle_features = Table(
    "vehicle_features",
    metadata,
    Column("feature_evidence_id", Integer, primary_key=True, autoincrement=True),
    Column("observation_id", Integer, ForeignKey("observations.observation_id"), nullable=False),
    Column("feature", String, nullable=False),
    Column("status", String, nullable=False),
    Column("source_kind", String),
    Column("url", String),
    Column("quote", Text),
    Column("page_hash", String),
    Column("sufficiency", String, nullable=False),
    Column("conflict", Boolean, nullable=False, default=False),
    Index("ix_vehicle_features_obs", "observation_id"),
)

price_components = Table(
    "price_components",
    metadata,
    Column("price_component_id", Integer, primary_key=True, autoincrement=True),
    Column("observation_id", Integer, ForeignKey("observations.observation_id"), nullable=False),
    Column("kind", String, nullable=False),
    Column("cents", Integer),
    Column("currency", String, nullable=False, default="USD"),
    Column("label", String, nullable=False),
    Column("eligibility_text", Text),
    Column("quote", Text),
    Column("valid_from", String),
    Column("valid_to", String),
    Index("ix_price_components_obs", "observation_id"),
)

listing_lifecycle = Table(
    "listing_lifecycle",
    metadata,
    Column("lifecycle_event_id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String, ForeignKey("units.vin"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("from_state", String),
    Column("to_state", String, nullable=False),
    Column("event_at", String, nullable=False),
    Column("reason", String, nullable=False),
    Column("evidence", Text),
    Column("baseline_run_id", String, ForeignKey("runs.run_id")),
    Index("ix_listing_lifecycle_dealer_vin", "dealer_id", "vin"),
)

price_events = Table(
    "price_events",
    metadata,
    Column("price_event_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("baseline_run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("vin", String, ForeignKey("units.vin"), nullable=False),
    Column("event", String, nullable=False),
    Column("before_cents", Integer),
    Column("after_cents", Integer),
    Column("delta_cents", Integer, nullable=False),
    Index("ix_price_events_run", "run_id"),
)

inventory_staging = Table(
    "inventory_staging",
    metadata,
    Column("run_id", String, ForeignKey("runs.run_id"), primary_key=True),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), primary_key=True),
    Column("vin", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("reason", String),
)

review_items = Table(
    "review_items",
    metadata,
    Column("item_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("vin", String),
    Column("issue", String, nullable=False),
    Column("status", String, nullable=False, default="OPEN"),
    Column("operator_id", String),
    Column("decision", String),
    Column("decided_at", String),
    Column("created_at", String, nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("audit_id", Integer, primary_key=True, autoincrement=True),
    Column("entity", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("field", String),
    Column("old_value", Text),
    Column("new_value", Text),
    Column("operator_id", String, nullable=False),
    Column("event_at", String, nullable=False),
    Column("reason", Text),
    Column("evidence", Text),
)

notification_events = Table(
    "notification_events",
    metadata,
    Column("notification_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("recipient", String, nullable=False),
    Column("event_key", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("provider_status", String),
    Column("last_error", Text),
    Column("created_at", String, nullable=False),
    Column("sent_at", String),
    UniqueConstraint("run_id", "recipient", "event_key", name="uq_notification_events_dedup"),
)

catalog_vehicles = Table(
    "catalog_vehicles",
    metadata,
    Column("vin", String, primary_key=True),
    Column("dealer_name", String, nullable=False),
    Column("city", String),
    Column("phone", String),
    Column("year", Integer),
    Column("model", String),
    Column("trim", String),
    Column("trim_code", String, nullable=False),
    Column("stock", String),
    Column("exterior", String),
    Column("interior", String),
    Column("mileage", Integer),
    Column("msrp_cents", Integer),
    Column("advertised_cents", Integer),
    Column("listing_url", String),
    Column("as_of", String, nullable=False),
    Column("prior_dealer", String),
    Column("packages_text", Text),
    Column("imported_at", String, nullable=False),
    Index("ix_catalog_vehicles_dealer", "dealer_name"),
    Index("ix_catalog_vehicles_trim", "trim_code"),
)

catalog_features = Table(
    "catalog_features",
    metadata,
    Column("vin", String, ForeignKey("catalog_vehicles.vin"), primary_key=True),
    Column("feature", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("quote", Text),
)

robots_snapshots = Table(
    "robots_snapshots",
    metadata,
    Column("robots_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("dealer_id", String, ForeignKey("dealers.dealer_id"), nullable=False),
    Column("hostname", String, nullable=False),
    Column("sha256", String),
    Column("decision", String, nullable=False),
    Column("fetched_at", String, nullable=False),
)
