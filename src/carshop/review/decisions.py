"""F22: approve, reject, or correct. The original observation row is never updated."""

from __future__ import annotations

from sqlalchemy import Connection, desc, select, update

from carshop.domain.codes import ExtractionMethod, StagingStatus
from carshop.storage import schema as s
from carshop.util import iso, utcnow

CORRECTABLE = frozenset(
    {
        "comparison_price_cents",
        "advertised_price_cents",
        "odometer_miles",
        "exterior_raw",
        "interior_raw",
        "dap_status",
        "availability",
    }
)
_INT_FIELDS = frozenset({"comparison_price_cents", "advertised_price_cents", "odometer_miles"})


def approve(conn: Connection, item_id: int, *, operator_id: str, reason: str) -> None:
    _decide(
        conn,
        item_id,
        decision=StagingStatus.VALIDATED.value,
        operator_id=operator_id,
        reason=reason,
    )


def reject(conn: Connection, item_id: int, *, operator_id: str, reason: str) -> None:
    _decide(
        conn, item_id, decision=StagingStatus.REJECTED.value, operator_id=operator_id, reason=reason
    )


def _decide(
    conn: Connection, item_id: int, *, decision: str, operator_id: str, reason: str
) -> None:
    item = (
        conn.execute(select(s.review_items).where(s.review_items.c.item_id == item_id))
        .mappings()
        .first()
    )
    if item is None:
        raise LookupError(f"review item {item_id} not found")
    now = iso(utcnow())
    conn.execute(
        update(s.review_items)
        .where(s.review_items.c.item_id == item_id)
        .values(status="DECIDED", decision=decision, operator_id=operator_id, decided_at=now)
    )
    if item["vin"]:
        conn.execute(
            update(s.inventory_staging)
            .where(
                s.inventory_staging.c.run_id == item["run_id"],
                s.inventory_staging.c.dealer_id == item["dealer_id"],
                s.inventory_staging.c.vin == item["vin"],
            )
            .values(status=decision, reason=reason or item["issue"])
        )
        if decision == StagingStatus.REJECTED.value:
            conn.execute(
                update(s.observations)
                .where(
                    s.observations.c.run_id == item["run_id"],
                    s.observations.c.dealer_id == item["dealer_id"],
                    s.observations.c.vin == item["vin"],
                )
                .values(rejected=True)
            )
    conn.execute(
        s.audit_events.insert().values(
            entity="review_item",
            entity_id=str(item_id),
            field="status",
            old_value="OPEN",
            new_value=decision,
            operator_id=operator_id,
            event_at=now,
            reason=reason or item["issue"],
            evidence=None,
        )
    )


def correct(
    conn: Connection,
    *,
    run_id: str,
    dealer_id: str,
    vin: str,
    field: str,
    new_value: str,
    operator_id: str,
    reason: str,
    evidence: str | None,
) -> int:
    """Insert observation version+1. Charts read the latest non-rejected version; v1 stays."""
    if field not in CORRECTABLE:
        raise ValueError(f"{field} cannot be corrected")
    row = (
        conn.execute(
            select(s.observations)
            .where(
                s.observations.c.run_id == run_id,
                s.observations.c.dealer_id == dealer_id,
                s.observations.c.vin == vin,
                s.observations.c.rejected.is_(False),
            )
            .order_by(desc(s.observations.c.version))
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError(f"no observation for {dealer_id} {vin} in {run_id}")
    old = row[field]
    coerced: object
    if field in _INT_FIELDS:
        coerced = None if new_value.strip().lower() in {"", "null", "none"} else int(new_value)
    else:
        coerced = new_value
    data = dict(row)
    old_id = int(data.pop("observation_id"))
    data["version"] = int(data["version"]) + 1
    data[field] = coerced
    data["extraction_method"] = ExtractionMethod.HUMAN.value
    inserted = conn.execute(s.observations.insert().values(**data)).inserted_primary_key
    assert inserted is not None
    new_id = int(inserted[0])
    for feat in conn.execute(
        select(s.vehicle_features).where(s.vehicle_features.c.observation_id == old_id)
    ).mappings():
        feat_row = dict(feat)
        feat_row.pop("feature_evidence_id", None)
        feat_row["observation_id"] = new_id
        conn.execute(s.vehicle_features.insert().values(**feat_row))
    for price in conn.execute(
        select(s.price_components).where(s.price_components.c.observation_id == old_id)
    ).mappings():
        price_row = dict(price)
        price_row.pop("price_component_id", None)
        price_row["observation_id"] = new_id
        conn.execute(s.price_components.insert().values(**price_row))
    now = iso(utcnow())
    conn.execute(
        s.audit_events.insert().values(
            entity="observation",
            entity_id=f"{run_id}:{dealer_id}:{vin}",
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if coerced is None else str(coerced),
            operator_id=operator_id,
            event_at=now,
            reason=reason,
            evidence=evidence,
        )
    )
    return new_id
