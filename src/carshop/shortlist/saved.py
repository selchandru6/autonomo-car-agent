"""Saved cars: snapshot at save time and tracking after each published run (§4).

Everything reads committed rows from published runs, staging VALIDATED and not rejected, so a
car held for review can be neither saved nor tracked from its held observation (check 8).
Writes touch `saved_cars` only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, desc, func, select, update

from carshop.domain.codes import (
    DealerStatus,
    Feature,
    Flag,
    LifecycleState,
    RunStatus,
    StagingStatus,
)
from carshop.domain.display import index_features as _index_features
from carshop.domain.display import price_gap_reason
from carshop.domain.pricing import fmt_cents
from carshop.storage import repos
from carshop.storage import schema as s

Row = dict[str, Any]

BUSY = "The daily run is saving data. Try again in a moment."
MAX_SAVED_CARS = 200
NOTE_MAX = 280
SNAPSHOT_VERSION = 1

FEATURE_LABELS: dict[str, str] = {
    Feature.DAP: "Driving Assistance Professional",
    Feature.M_SPORT: "M Sport package",
    Feature.MASSAGE: "Massage seats",
    Feature.PANORAMIC_ROOF: "Panoramic roof",
    Feature.SKY_LOUNGE: "Sky Lounge LED roof",
    Feature.EXECUTIVE: "Executive package",
    Feature.CLIMATE_COMFORT: "Climate Comfort package",
    Feature.AIR_SUSPENSION: "Air suspension",
}
STATUS_WORDS = {"YES": "✓", "NO": "not on this car", "UNKNOWN": "not listed"}
_READ = (DealerStatus.REPORTED.value, DealerStatus.PARTIAL.value)


class ShortlistError(Exception):
    """A refused write, with the HTTP status and the sentence the owner sees."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def _norm_vin(vin: str) -> str:
    v = vin.strip().upper()
    if len(v) != 17 or not v.isalnum():
        raise ShortlistError(400, "That is not a 17-character VIN.")
    return v


# ---------------------------------------------------------------- reads


def validated_history(conn: Connection, vin: str) -> list[Row]:
    """Every validated, non-rejected observation of this VIN in a published run, oldest first.

    One row per (run, dealer): the latest non-rejected version.
    """
    o, st, r = s.observations, s.inventory_staging, s.runs
    q = (
        select(
            o, r.c.snapshot_date, r.c.started_at.label("run_started_at"),
            s.dealers.c.name.label("dealer_name"), s.dealers.c.city,
        )
        .join(r, r.c.run_id == o.c.run_id)
        .join(s.dealers, s.dealers.c.dealer_id == o.c.dealer_id)
        .join(st, and_(st.c.run_id == o.c.run_id, st.c.dealer_id == o.c.dealer_id, st.c.vin == o.c.vin))
        .where(o.c.vin == vin, o.c.rejected.is_(False))
        .where(st.c.status == StagingStatus.VALIDATED.value, r.c.status == RunStatus.PUBLISHED.value)
        .order_by(r.c.started_at, o.c.version)
    )  # fmt: skip
    latest: dict[tuple[str, str], Row] = {}
    for m in conn.execute(q).mappings():
        row = dict(m)
        latest[(row["run_id"], row["dealer_id"])] = row
    rows = list(latest.values())
    extra: dict[int, set[str]] = {}
    ids = [int(x["observation_id"]) for x in rows]
    if ids:
        flags_q = select(s.observation_flags.c.observation_id, s.observation_flags.c.flag)
        for oid, flag in conn.execute(flags_q.where(s.observation_flags.c.observation_id.in_(ids))):
            extra.setdefault(int(oid), set()).add(str(flag))
    for x in rows:
        x["flags"] = sorted(
            set(json.loads(x["flags"] or "[]")) | extra.get(int(x["observation_id"]), set())
        )
    return rows


def savable_vins(conn: Connection, vins: list[str]) -> set[str]:
    """Of these VINs, the ones with a validated observation in a published run."""
    if not vins:
        return set()
    o, st, r = s.observations, s.inventory_staging, s.runs
    q = (
        select(o.c.vin).distinct()
        .join(r, r.c.run_id == o.c.run_id)
        .join(st, and_(st.c.run_id == o.c.run_id, st.c.dealer_id == o.c.dealer_id, st.c.vin == o.c.vin))
        .where(o.c.vin.in_(vins), o.c.rejected.is_(False))
        .where(st.c.status == StagingStatus.VALIDATED.value, r.c.status == RunStatus.PUBLISHED.value)
    )  # fmt: skip
    return {str(v) for v in conn.execute(q).scalars()}


def latest_validated(conn: Connection, vin: str) -> Row | None:
    hist = validated_history(conn, vin)
    return hist[-1] if hist else None


def _features(conn: Connection, observation_id: int) -> dict[str, Row]:
    rows = repos.features_for_observations(conn, [observation_id]).get(observation_id, [])
    return _index_features(rows)


def build_snapshot(conn: Connection, obs: Row) -> Row:
    """§4.2. Written once at save time and never changed."""
    oid = int(obs["observation_id"])
    feats = _features(conn, oid)
    prices = repos.prices_for_observations(conn, [oid]).get(oid, [])
    packages = conn.execute(
        select(s.catalog_vehicles.c.packages_text).where(s.catalog_vehicles.c.vin == obs["vin"])
    ).scalar()
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "observation_id": oid,
        "vin": obs["vin"],
        "dealer_id": obs["dealer_id"],
        "dealer_name": obs["dealer_name"],
        "stock": obs["stock"],
        "listing_url": obs["detail_url"] or obs["source_url"],
        "run_id": obs["run_id"],
        "snapshot_date": obs["snapshot_date"],
        "year": obs["year"],
        "model": obs["model"],
        "trim": obs["trim"],
        "ag_code": str(obs["vin"])[3:7],
        "exterior_raw": obs["exterior_raw"],
        "exterior_code": obs["exterior_code"],
        "interior_raw": obs["interior_raw"],
        "interior_code": obs["interior_code"],
        "odometer_miles": obs["odometer_miles"],
        "availability": obs["availability"],
        "in_transit": bool(obs["in_transit"]),
        "m_sport": bool(obs["m_sport"]),
        "msrp_cents": obs["msrp_cents"],
        "advertised_price_cents": obs["advertised_price_cents"],
        "comparison_price_cents": obs["comparison_price_cents"],
        "price_basis": obs["price_basis"],
        "flags": sorted(obs["flags"]),
        "price_components": [
            {k: p[k] for k in ("kind", "cents", "label", "eligibility_text", "quote", "valid_from", "valid_to")}
            for p in prices
        ],
        "features": {
            f.value: {
                "status": str(feats[f.value]["status"]) if f.value in feats else "UNKNOWN",
                "source_kind": feats[f.value]["source_kind"] if f.value in feats else None,
                "quote": feats[f.value]["quote"] if f.value in feats else None,
            }
            for f in Feature
        },
        "catalog_packages": packages,
    }  # fmt: skip


def _active(conn: Connection, vin: str | None = None) -> list[Row]:
    q = select(s.saved_cars).where(s.saved_cars.c.removed_at.is_(None))
    if vin is not None:
        q = q.where(s.saved_cars.c.vin == vin)
    out = []
    for m in conn.execute(q.order_by(desc(s.saved_cars.c.saved_at))).mappings():
        row = dict(m)
        row["snapshot"] = json.loads(row.pop("snapshot_json"))
        out.append(row)
    return out


def active_saved(conn: Connection) -> list[Row]:
    return _active(conn)


def get_saved(conn: Connection, vin: str) -> Row | None:
    rows = _active(conn, vin.strip().upper())
    return rows[0] if rows else None


def saved_vins(conn: Connection) -> set[str]:
    q = select(s.saved_cars.c.vin).where(s.saved_cars.c.removed_at.is_(None))
    return {str(v) for v in conn.execute(q).scalars()}


# ---------------------------------------------------------------- writes (saved_cars only)


def save_car(
    conn: Connection, vin: str, *, note: str | None = None, now: datetime | None = None
) -> tuple[Row, bool]:
    """Save a car from its latest committed observation. Returns (row, created). Never fetches."""
    v = _norm_vin(vin)
    existing = get_saved(conn, v)
    if existing is not None:
        return existing, False
    obs = latest_validated(conn, v)
    if obs is None:
        held = conn.execute(
            select(func.count()).select_from(s.observations).where(s.observations.c.vin == v)
        ).scalar()
        if held:
            raise ShortlistError(
                409, "This car is waiting for a review check. It can be saved once that is done."
            )
        raise ShortlistError(
            404, "This car has not been read in a daily check yet, so it cannot be saved."
        )
    count = (
        conn.execute(
            select(func.count())
            .select_from(s.saved_cars)
            .where(s.saved_cars.c.removed_at.is_(None))
        ).scalar()
        or 0
    )
    if count >= MAX_SAVED_CARS:
        raise ShortlistError(
            409, f"Your shortlist holds {MAX_SAVED_CARS} cars. Remove one to save another."
        )
    ts = _now(now)
    conn.execute(
        s.saved_cars.insert().values(
            vin=v, saved_at=ts, updated_at=ts, note=_clean_note(note),
            snapshot_json=json.dumps(build_snapshot(conn, obs), sort_keys=True, default=str),
            snapshot_observation_id=int(obs["observation_id"]), removed_at=None,
        )
    )  # fmt: skip
    row = get_saved(conn, v)
    assert row is not None
    return row, True


def remove_car(conn: Connection, vin: str, *, now: datetime | None = None) -> bool:
    """Soft delete. The row stays for the record."""
    v = _norm_vin(vin)
    ts = _now(now)
    res = conn.execute(
        update(s.saved_cars)
        .where(s.saved_cars.c.vin == v, s.saved_cars.c.removed_at.is_(None))
        .values(removed_at=ts, updated_at=ts)
    )
    return bool(res.rowcount)


def _clean_note(note: str | None) -> str | None:
    if note is None:
        return None
    n = note.strip()
    if len(n) > NOTE_MAX:
        raise ShortlistError(400, f"A note can be at most {NOTE_MAX} characters.")
    return n or None


def set_note(conn: Connection, vin: str, note: str | None, *, now: datetime | None = None) -> Row:
    v = _norm_vin(vin)
    clean = _clean_note(note)
    res = conn.execute(
        update(s.saved_cars)
        .where(s.saved_cars.c.vin == v, s.saved_cars.c.removed_at.is_(None))
        .values(note=clean, updated_at=_now(now))
    )
    if not res.rowcount:
        raise ShortlistError(404, "This car is not on your shortlist.")
    row = get_saved(conn, v)
    assert row is not None
    return row


# ---------------------------------------------------------------- tracking (§4.3)


def price_change(
    before: int | None, before_basis: str | None, after: int | None, after_basis: str | None
) -> Row:
    """Same VIN, same price kind only (F18). NULL on one side is appeared/withdrawn, not a move."""
    if before is None and after is None:
        return {"label": "No cash price yet", "direction": None, "delta_cents": None}
    if before is None:
        return {"label": "Price appeared", "direction": None, "delta_cents": None}
    if after is None:
        return {"label": "Price withdrawn", "direction": None, "delta_cents": None}
    if (before_basis or "") != (after_basis or ""):
        return {"label": "Price shown a different way", "direction": None, "delta_cents": None}
    delta = int(after) - int(before)
    if delta == 0:
        return {"label": "No change", "direction": None, "delta_cents": 0}
    arrow = "▼" if delta < 0 else "▲"
    return {
        "label": f"{arrow} {fmt_cents(abs(delta))}",
        "direction": "down" if delta < 0 else "up",
        "delta_cents": delta,
    }


def _dealer_reads(conn: Connection, dealer_id: str) -> list[Row]:
    dr, r = s.dealer_runs, s.runs
    q = (
        select(dr.c.run_id, dr.c.status, r.c.snapshot_date)
        .join(r, r.c.run_id == dr.c.run_id)
        .where(dr.c.dealer_id == dealer_id, r.c.status == RunStatus.PUBLISHED.value)
        .order_by(desc(r.c.started_at))
    )
    return [dict(m) for m in conn.execute(q).mappings()]


def _last_lifecycle(conn: Connection, dealer_id: str, vin: str) -> Row | None:
    lc = s.listing_lifecycle
    m = conn.execute(
        select(lc.c.to_state, lc.c.run_id)
        .where(lc.c.dealer_id == dealer_id, lc.c.vin == vin)
        .order_by(desc(lc.c.lifecycle_event_id)).limit(1)
    ).mappings().first()  # fmt: skip
    return dict(m) if m else None


def _status(conn: Connection, latest: Row, last_read: Row | None) -> str:
    lc = _last_lifecycle(conn, str(latest["dealer_id"]), str(latest["vin"]))
    state = lc["to_state"] if lc else None
    if state == LifecycleState.SOLD_CONFIRMED.value:
        return "Sold (confirmed by the dealer)"
    if state == LifecycleState.MISSING_UNVERIFIED.value:
        return "Missing, unverified"
    present = last_read is not None and latest["run_id"] == last_read["run_id"]
    if not present:
        if last_read is None:
            return "Listed"
        held = conn.execute(
            select(s.inventory_staging.c.status).where(
                s.inventory_staging.c.run_id == last_read["run_id"],
                s.inventory_staging.c.dealer_id == latest["dealer_id"],
                s.inventory_staging.c.vin == latest["vin"],
            )
        ).scalar()
        if held is not None:
            return "Waiting for a review check"
        if last_read["status"] == DealerStatus.PARTIAL.value:
            return "Not seen on the last partial read"
        return "Listed"
    if Flag.TRANSFER_SUSPECTED.value in latest["flags"]:
        return "Also listed at another dealer"
    if lc and state == LifecycleState.RELISTED.value and lc["run_id"] == latest["run_id"]:
        return "Back again"
    return "Listed"


def track(conn: Connection, saved: Row) -> Row:
    """The tracking fields for one saved car, computed from existing tables at read time."""
    snap = saved["snapshot"]
    hist = validated_history(conn, str(saved["vin"]))
    latest = hist[-1] if hist else None
    out: Row = {
        "vin": saved["vin"], "saved_at": saved["saved_at"], "note": saved["note"], "snapshot": snap,
        "dealer_id": snap["dealer_id"], "dealer_name": snap["dealer_name"],
        "status": "Listed", "price_now_cents": snap["comparison_price_cents"], "price_now": None,
        "change": price_change(snap["comparison_price_cents"], snap["price_basis"], snap["comparison_price_cents"], snap["price_basis"]),
        "lowest_cents": None, "feature_changes": [], "last_read": None, "not_read_on": None,
        "moved_from": None, "changed": False,
    }  # fmt: skip
    if latest is None:
        out["status"] = "No longer shown after a review check"
        out["changed"] = True
        return out
    reads = _dealer_reads(conn, str(latest["dealer_id"]))
    last_read = next((r for r in reads if r["status"] in _READ), None)
    if reads and reads[0]["status"] not in _READ:
        out["not_read_on"] = reads[0]["snapshot_date"]
    out["last_read"] = last_read["snapshot_date"] if last_read else None
    out["status"] = _status(conn, latest, last_read)
    out["dealer_id"], out["dealer_name"] = latest["dealer_id"], latest["dealer_name"]
    if latest["dealer_id"] != snap["dealer_id"]:
        out["moved_from"] = snap["dealer_name"]
    now_cents = latest["comparison_price_cents"]
    out["price_now_cents"] = now_cents
    out["price_now"] = (
        fmt_cents(int(now_cents)) if now_cents is not None
        else price_gap_reason(set(latest["flags"]), latest["msrp_cents"])
    )  # fmt: skip
    out["change"] = price_change(
        snap["comparison_price_cents"], snap["price_basis"], now_cents, latest["price_basis"]
    )
    priced = [
        int(h["comparison_price_cents"]) for h in hist if h["comparison_price_cents"] is not None
    ]
    out["lowest_cents"] = min(priced) if priced else None
    feats = _features(conn, int(latest["observation_id"]))
    for f in Feature:
        was = snap["features"].get(f.value, {}).get("status", "UNKNOWN")
        now = str(feats[f.value]["status"]) if f.value in feats else "UNKNOWN"
        if was != now:
            out["feature_changes"].append(
                f"{FEATURE_LABELS[f]}: {STATUS_WORDS.get(was, was)} → {STATUS_WORDS.get(now, now)}"
            )
    out["changed"] = bool(
        out["status"] != "Listed" or out["change"]["delta_cents"] or out["feature_changes"]
        or out["change"]["label"] in ("Price appeared", "Price withdrawn") or out["moved_from"]
    )  # fmt: skip
    return out


def track_all(conn: Connection) -> list[Row]:
    """Saved cars with tracking, most recent change first, then most recently saved."""
    rows = [track(conn, sc) for sc in active_saved(conn)]
    rows.sort(key=lambda t: str(t["saved_at"]), reverse=True)
    rows.sort(key=lambda t: not t["changed"])
    return rows


# ---------------------------------------------------------------- digest (§7.3, saved-car part)


def digest_lines(conn: Connection, run_id: str) -> list[str]:
    """Saved-car price changes, missing, and back again in this run. Empty when nothing happened."""
    saved = {str(sc["vin"]): sc for sc in active_saved(conn)}
    if not saved:
        return []
    lines: list[str] = []
    for e in repos.price_events_for_run(conn, run_id):
        if e["vin"] in saved:
            ch = price_change(e["before_cents"], None, e["after_cents"], None)
            lines.append(
                f"    {e['vin']} · {e['dealer_name']} · {fmt_cents(e['before_cents'])} → "
                f"{fmt_cents(e['after_cents'])} ({ch['label']})"
            )
    words = {
        LifecycleState.MISSING_UNVERIFIED.value: "Missing, unverified (not a sale)",
        LifecycleState.RELISTED.value: "Back again",
    }
    for e in repos.lifecycle_for_run(conn, run_id):
        if e["vin"] in saved and e["to_state"] in words:
            lines.append(f"    {e['vin']} · {e['dealer_name']} · {words[e['to_state']]}")
    return lines


def digest_watch_keys(conn: Connection, run_id: str) -> list[str]:
    """`watch:{vin}:{event}:{run_id}` keys for the saved-car digest lines (§7.3)."""
    saved = {str(sc["vin"]) for sc in active_saved(conn)}
    keys: list[str] = []
    for e in repos.price_events_for_run(conn, run_id):
        if e["vin"] in saved:
            keys.append(f"watch:{e['vin']}:price:{run_id}")
    for e in repos.lifecycle_for_run(conn, run_id):
        if e["vin"] in saved and e["to_state"] in (
            LifecycleState.MISSING_UNVERIFIED.value,
            LifecycleState.RELISTED.value,
        ):
            keys.append(f"watch:{e['vin']}:{e['to_state']}:{run_id}")
    return keys
