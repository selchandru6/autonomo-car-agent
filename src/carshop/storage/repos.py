"""Repositories. One write transaction per dealer result; reads are plain selects.

All functions take a Connection so callers own transaction boundaries.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, and_, delete, desc, func, or_, select, text, update

from carshop.domain.codes import (
    DealerStatus,
    LifecycleState,
    ReasonCode,
    RunStatus,
    StagingStatus,
    Tri,
    Trigger,
)
from carshop.domain.models import Dealer, DealerResult, Observation
from carshop.storage import schema as s
from carshop.util import PARSER_VERSION, git_sha, iso, normalize_url, utcnow

Row = dict[str, Any]


def _rows(result: Any) -> list[Row]:
    return [dict(r._mapping) for r in result]


# ----------------------------------------------------------------------------- registry
def upsert_dealers(conn: Connection, dealers: Iterable[Dealer], operator_id: str = "system") -> int:
    n = 0
    now = iso(utcnow())
    for d in dealers:
        if d.source == "locator" and not (d.center_number or "").strip():
            raise ValueError(f"locator dealer {d.dealer_id} has no center number")
        existing = (
            conn.execute(select(s.dealers).where(s.dealers.c.dealer_id == d.dealer_id))
            .mappings()
            .first()
        )
        values = {
            "dealer_id": d.dealer_id,
            "center_number": d.center_number,
            "name": d.name,
            "city": d.city,
            "state": d.state,
            "hostname": d.hostname,
            "start_url": d.start_url,
            "path_prefixes": json.dumps(list(d.path_prefixes)),
            "platform": d.platform.value,
            "terms_status": d.terms_status.value,
            "sticker_hosts": json.dumps(list(d.sticker_hosts)),
            "active": d.active,
            "notes": d.notes,
            "locator_url": d.locator_url,
            "discovery_reason": d.discovery_reason,
            "discovered_at": d.discovered_at,
            "source": d.source,
            "discovery_fail_streak": d.discovery_fail_streak,
        }
        if existing is None:
            conn.execute(s.dealers.insert().values(verified_at=now, **values))
            conn.execute(s.audit_events.insert().values(entity="dealer", entity_id=d.dealer_id, field=None, old_value=None,
                                                        new_value=json.dumps(values), operator_id=operator_id, event_at=now, reason="registry apply"))  # fmt: skip
            n += 1
            continue
        changed = {k: v for k, v in values.items() if existing.get(k) != v and k != "dealer_id"}
        if changed:
            conn.execute(
                s.dealers.update()
                .where(s.dealers.c.dealer_id == d.dealer_id)
                .values(verified_at=now, **changed)
            )
            for k, v in changed.items():
                conn.execute(s.audit_events.insert().values(entity="dealer", entity_id=d.dealer_id, field=k, old_value=str(existing.get(k)),
                                                            new_value=str(v), operator_id=operator_id, event_at=now, reason="registry apply"))  # fmt: skip
            n += 1
    return n


_CENTERLESS_CHILDREN = (
    s.dealer_runs,
    s.dealer_listings,
    s.exclusions,
    s.fetch_attempts,
    s.review_items,
    s.inventory_staging,
    s.listing_lifecycle,
    s.price_events,
    s.robots_snapshots,
)


def retire_centerless_dealers(conn: Connection) -> list[str]:
    """Drop locator rows that have no center number and no observations (review G3).

    A row that already has observations stays, inactive, with source `seed` and a note naming
    the center that replaced it. Locator identity is the center number (§7.1).
    """
    rows = (
        conn.execute(
            select(s.dealers).where(
                or_(s.dealers.c.center_number.is_(None), s.dealers.c.center_number == "")
            )
        )
        .mappings()
        .all()
    )
    changed: list[str] = []
    for row in rows:
        did = str(row["dealer_id"])
        obs = int(
            conn.execute(
                select(func.count())
                .select_from(s.observations)
                .where(s.observations.c.dealer_id == did)
            ).scalar()
            or 0
        )
        if obs:
            center = _superseding_center(conn, str(row["city"] or ""), str(row["name"] or ""), did)
            note = (
                f"superseded by center {center}"
                if center
                else "superseded; locator rows require a center number"
            )
            conn.execute(
                update(s.dealers)
                .where(s.dealers.c.dealer_id == did)
                .values(active=False, source="seed", notes=note)
            )
        else:
            for table in _CENTERLESS_CHILDREN:
                conn.execute(delete(table).where(table.c.dealer_id == did))
            conn.execute(delete(s.dealers).where(s.dealers.c.dealer_id == did))
        changed.append(did)
    return changed


def _brand_tokens(name: str) -> set[str]:
    stop = {"bmw", "of", "the", "sewell", "and"}
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in stop and len(t) > 2}


def _superseding_center(conn: Connection, city: str, name: str, dealer_id: str) -> str | None:
    candidates = conn.execute(
        select(s.dealers.c.center_number, s.dealers.c.name).where(
            s.dealers.c.center_number.is_not(None),
            s.dealers.c.center_number != "",
            s.dealers.c.dealer_id != dealer_id,
            func.lower(s.dealers.c.city) == city.strip().lower(),
        )
    ).all()
    if not candidates:
        return None
    tokens = _brand_tokens(name)
    for center, other in candidates:
        if tokens & _brand_tokens(str(other)):
            return str(center)
    return str(candidates[0][0])


def list_dealers(conn: Connection) -> list[Row]:
    return _rows(conn.execute(select(s.dealers).order_by(s.dealers.c.city)))


def deactivate_dealers_not_in(
    conn: Connection, keep_ids: Iterable[str], operator_id: str = "system"
) -> list[str]:
    """Mark database dealer rows that are no longer on the registry inactive (history kept)."""
    keep = set(keep_ids)
    now = iso(utcnow())
    gone: list[str] = []
    for r in conn.execute(select(s.dealers.c.dealer_id).where(s.dealers.c.active.is_(True))):
        if r[0] in keep:
            continue
        gone.append(str(r[0]))
        conn.execute(
            update(s.dealers)
            .where(s.dealers.c.dealer_id == r[0])
            .values(active=False, verified_at=now)
        )
        conn.execute(s.audit_events.insert().values(entity="dealer", entity_id=r[0], field="active", old_value="True",
                                                    new_value="False", operator_id=operator_id, event_at=now,
                                                    reason="not on the generated registry (left the locator)"))  # fmt: skip
    return gone


def record_registry_snapshot(conn: Connection, *, fetched_at: str, sha256: str, storage_uri: str | None,
                             dealer_count: int, total_seen: int, registry_version: str) -> None:  # fmt: skip
    conn.execute(
        s.registry_snapshots.insert().values(
            fetched_at=fetched_at,
            sha256=sha256,
            storage_uri=storage_uri,
            dealer_count=dealer_count,
            total_seen=total_seen,
            registry_version=registry_version,
            applied_at=iso(utcnow()),
        )
    )


def latest_registry_snapshot(conn: Connection) -> Row | None:
    r = (
        conn.execute(
            select(s.registry_snapshots).order_by(desc(s.registry_snapshots.c.snapshot_id)).limit(1)
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


# ----------------------------------------------------------------------------- runs
def create_run(
    conn: Connection, *, run_id: str, snapshot_date: str, trigger: str, config_version: str,
    model_status: str, rerun_of: str | None = None, label: str | None = None,
    started_at: str | None = None,
) -> None:  # fmt: skip
    conn.execute(
        s.runs.insert().values(
            run_id=run_id, snapshot_date=snapshot_date, trigger=trigger, status=RunStatus.RUNNING.value,
            started_at=started_at or iso(utcnow()), config_version=config_version, parser_version=PARSER_VERSION,
            model_version=None, model_status=model_status, git_sha=git_sha(), rerun_of_run_id=rerun_of, label=label,
        )
    )  # fmt: skip


def finish_run(conn: Connection, run_id: str, status: RunStatus, model_spend_usd: float) -> None:
    conn.execute(
        update(s.runs)
        .where(s.runs.c.run_id == run_id)
        .values(status=status.value, ended_at=iso(utcnow()), model_spend_usd=model_spend_usd)
    )


def get_run(conn: Connection, run_id: str) -> Row | None:
    r = conn.execute(select(s.runs).where(s.runs.c.run_id == run_id)).mappings().first()
    return dict(r) if r else None


def reclaim_stale_runs(conn: Connection, *, now: datetime, max_age_s: float) -> list[str]:
    """Mark RUNNING runs older than `max_age_s` as FAILED (process crashed or was killed).

    Without this a dead process would block its dealers forever (F10). Audited.
    """
    cutoff = iso(now - timedelta(seconds=max_age_s))
    stale = [
        str(r[0])
        for r in conn.execute(
            select(s.runs.c.run_id)
            .where(s.runs.c.status == RunStatus.RUNNING.value)
            .where(s.runs.c.started_at < cutoff)
        )
    ]
    for run_id in stale:
        conn.execute(
            update(s.runs)
            .where(s.runs.c.run_id == run_id)
            .values(status=RunStatus.FAILED.value, ended_at=iso(now), label="reclaimed_stale")
        )
        conn.execute(
            s.audit_events.insert().values(
                entity="run", entity_id=run_id, field="status", old_value=RunStatus.RUNNING.value,
                new_value=RunStatus.FAILED.value, operator_id="system", event_at=iso(now),
                reason=f"RUNNING for more than {int(max_age_s)}s; process presumed dead", evidence=None,
            )
        )  # fmt: skip
        conn.execute(
            update(s.dealer_runs)
            .where(s.dealer_runs.c.run_id == run_id, s.dealer_runs.c.status == "RUNNING")
            .values(
                status=DealerStatus.NOT_REPORTED.value,
                reason_code=ReasonCode.TIMEOUT.value,
                finished_at=iso(now),
            )
        )
    return stale


def claim_dealer_run(conn: Connection, run_id: str, dealer_id: str) -> None:
    """Mark a dealer in progress before any page is fetched (F10)."""
    conn.execute(
        s.dealer_runs.insert().values(
            run_id=run_id,
            dealer_id=dealer_id,
            status="RUNNING",
            reason_code=None,
            pages_expected=0,
            pages_read=0,
            pages_failed=0,
            raw_count=0,
            eligible_count=0,
            duration_ms=0,
            cost_usd=0.0,
            baseline_run_id=None,
            anomaly=False,
            robots_hash=None,
            adapter_version=None,
            finished_at=None,
        )
    )


def mark_dealer_failed(conn: Connection, run_id: str, dealer_id: str, detail: str) -> None:
    conn.execute(
        update(s.dealer_runs)
        .where(s.dealer_runs.c.run_id == run_id, s.dealer_runs.c.dealer_id == dealer_id)
        .values(
            status=DealerStatus.NOT_REPORTED.value,
            reason_code=ReasonCode.PARSER_FAILED.value,
            finished_at=iso(utcnow()),
        )
    )
    conn.execute(
        s.audit_events.insert().values(
            entity="dealer_run",
            entity_id=f"{run_id}:{dealer_id}",
            field="status",
            old_value="RUNNING",
            new_value=DealerStatus.NOT_REPORTED.value,
            operator_id="system",
            event_at=iso(utcnow()),
            reason=detail[:500],
            evidence=None,
        )
    )


def running_run_for_dealer(conn: Connection, dealer_id: str) -> str | None:
    """F10: a dealer with a RUNNING dealer_runs row is rejected."""
    q = (
        select(s.dealer_runs.c.run_id)
        .select_from(s.dealer_runs.join(s.runs, s.runs.c.run_id == s.dealer_runs.c.run_id))
        .where(s.dealer_runs.c.dealer_id == dealer_id)
        .where(s.dealer_runs.c.status == "RUNNING")
        .where(s.runs.c.status == RunStatus.RUNNING.value)
    )
    r = conn.execute(q).first()
    return r[0] if r else None


def scheduled_run_exists(conn: Connection, snapshot_date: str) -> bool:
    q = (
        select(func.count())
        .select_from(s.runs)
        .where(s.runs.c.snapshot_date == snapshot_date)
        .where(s.runs.c.trigger.in_(["cron", "catchup"]))
    )
    return bool(conn.execute(q).scalar())


def latest_published_run(conn: Connection, *, full_registry: bool = True) -> Row | None:
    """Latest PUBLISHED run. By default a run that covered the whole registry; a
    dealer-scoped manual run would otherwise make the statewide screen look empty."""
    q = select(s.runs).where(s.runs.c.status == RunStatus.PUBLISHED.value)
    if full_registry:
        q = q.where(s.runs.c.trigger != Trigger.MANUAL_DEALER.value)
    r = conn.execute(q.order_by(desc(s.runs.c.started_at)).limit(1)).mappings().first()
    if r is None and full_registry:
        return latest_published_run(conn, full_registry=False)
    return dict(r) if r else None


def list_runs(conn: Connection, limit: int = 60) -> list[Row]:
    return _rows(conn.execute(select(s.runs).order_by(desc(s.runs.c.started_at)).limit(limit)))


# ----------------------------------------------------------------------------- baseline
def last_reported_run(
    conn: Connection, dealer_id: str, *, before_run_id: str | None = None
) -> str | None:
    q = (
        select(s.dealer_runs.c.run_id)
        .select_from(s.dealer_runs.join(s.runs, s.runs.c.run_id == s.dealer_runs.c.run_id))
        .where(s.dealer_runs.c.dealer_id == dealer_id)
        .where(s.dealer_runs.c.status == DealerStatus.REPORTED.value)
        .where(s.runs.c.status == RunStatus.PUBLISHED.value)
        .order_by(desc(s.runs.c.started_at))
    )
    if before_run_id:
        q = q.where(s.dealer_runs.c.run_id != before_run_id)
    r = conn.execute(q.limit(1)).first()
    return r[0] if r else None


def reported_prices(conn: Connection, run_id: str, dealer_id: str) -> dict[str, int | None]:
    """vin -> comparison_price for the latest non-rejected version in that run."""
    q = (
        select(
            s.observations.c.vin, s.observations.c.comparison_price_cents, s.observations.c.version
        )
        .where(s.observations.c.run_id == run_id)
        .where(s.observations.c.dealer_id == dealer_id)
        .where(s.observations.c.rejected.is_(False))
        .order_by(s.observations.c.vin, desc(s.observations.c.version))
    )
    out: dict[str, int | None] = {}
    for vin, price, _v in conn.execute(q):
        out.setdefault(vin, price)
    return out


def current_lifecycle_state(conn: Connection, dealer_id: str, vin: str) -> LifecycleState | None:
    r = conn.execute(
        select(s.listing_lifecycle.c.to_state)
        .where(s.listing_lifecycle.c.dealer_id == dealer_id, s.listing_lifecycle.c.vin == vin)
        .order_by(desc(s.listing_lifecycle.c.lifecycle_event_id)).limit(1)
    ).first()  # fmt: skip
    return LifecycleState(r[0]) if r else None


def missing_unverified_vins(conn: Connection, dealer_id: str) -> set[str]:
    sub = (
        select(s.listing_lifecycle.c.vin, func.max(s.listing_lifecycle.c.lifecycle_event_id).label("mx"))
        .where(s.listing_lifecycle.c.dealer_id == dealer_id)
        .group_by(s.listing_lifecycle.c.vin).subquery()
    )  # fmt: skip
    q = (
        select(s.listing_lifecycle.c.vin)
        .join(
            sub,
            and_(
                s.listing_lifecycle.c.vin == sub.c.vin,
                s.listing_lifecycle.c.lifecycle_event_id == sub.c.mx,
            ),
        )
        .where(s.listing_lifecycle.c.to_state == LifecycleState.MISSING_UNVERIFIED.value)
    )
    return {r[0] for r in conn.execute(q)}


def _review_exists(conn: Connection, run_id: str, dealer_id: str, vin: str, issue: str) -> bool:
    return (
        conn.execute(
            select(s.review_items.c.item_id).where(
                s.review_items.c.run_id == run_id,
                s.review_items.c.dealer_id == dealer_id,
                s.review_items.c.issue == issue,
                func.coalesce(s.review_items.c.vin, "") == (vin or ""),
            )
        ).first()
        is not None
    )


# ----------------------------------------------------------------------------- dealer result
def store_dealer_result(
    conn: Connection, run_id: str, result: DealerResult, *, baseline_run_id: str | None
) -> dict[str, int]:
    """Persist one dealer's result atomically. Idempotent on (run_id, dealer_id, vin, version)."""
    now = iso(utcnow())
    sha = git_sha()
    inserted = 0
    for a in result.attempts:
        already = conn.execute(
            select(s.fetch_attempts.c.fetch_attempt_id).where(
                s.fetch_attempts.c.run_id == run_id,
                s.fetch_attempts.c.dealer_id == result.dealer_id,
                s.fetch_attempts.c.url == a.url,
                s.fetch_attempts.c.attempt == a.attempt,
                s.fetch_attempts.c.renderer == a.renderer,
            )
        ).first()
        if already:
            continue
        conn.execute(
            s.fetch_attempts.insert().values(
                run_id=run_id, dealer_id=result.dealer_id, url=a.url, final_url=a.final_url, method=a.method,
                renderer=a.renderer, http_status=a.http_status, attempt=a.attempt, latency_ms=a.latency_ms,
                robots_decision=a.robots_decision, content_hash=a.content_hash, storage_uri=a.storage_uri,
                error_code=a.error_code.value if a.error_code else None, fetched_at=now,
            )
        )  # fmt: skip
    for e in result.exclusions:
        already_ex = conn.execute(
            select(s.exclusions.c.exclusion_id).where(
                s.exclusions.c.run_id == run_id,
                s.exclusions.c.dealer_id == result.dealer_id,
                s.exclusions.c.url == e.url,
                s.exclusions.c.reason_code == e.code.value,
                func.coalesce(s.exclusions.c.vin, "") == (e.vin or ""),
            )
        ).first()
        if already_ex:
            continue
        conn.execute(
            s.exclusions.insert().values(
                run_id=run_id,
                dealer_id=result.dealer_id,
                vin=e.vin,
                url=e.url,
                reason_code=e.code.value,
                evidence_quote=e.quote,
            )
        )

    review_vins = dict(result.review_reasons)
    for obs in result.observations:
        exists = conn.execute(
            select(s.observations.c.observation_id).where(
                s.observations.c.run_id == run_id, s.observations.c.dealer_id == obs.dealer_id,
                s.observations.c.vin == obs.vin, s.observations.c.version == 1,
            )
        ).first()  # fmt: skip
        if exists:
            continue
        _upsert_unit_and_listing(conn, obs, now)
        inserted_pk = conn.execute(
            s.observations.insert().values(
                run_id=run_id, dealer_id=obs.dealer_id, vin=obs.vin, version=1, stock=obs.stock, year=obs.year,
                model=obs.model, trim=obs.trim, exterior_raw=obs.exterior_raw, exterior_code=obs.exterior_code,
                interior_raw=obs.interior_raw, interior_code=obs.interior_code, odometer_miles=obs.odometer_miles,
                availability=obs.availability, in_transit=obs.in_transit, msrp_cents=obs.msrp_cents,
                advertised_price_cents=obs.advertised_price_cents, comparison_price_cents=obs.comparison_price_cents,
                price_basis=obs.price_basis,
                dap_status=obs.dap_status.value, m_sport=obs.m_sport, flags=json.dumps(sorted(f.value for f in obs.flags)),
                extraction_method=obs.extraction_method.value, adapter_version=obs.adapter_version, git_sha=sha,
                page_hash=obs.page_hash, source_url=obs.source_url, detail_url=obs.detail_url, sticker_url=obs.sticker_url,
                image_url=obs.image_url, fetched_at=iso(obs.fetched_at),
            )
        ).inserted_primary_key  # fmt: skip
        assert inserted_pk is not None
        obs_id = int(inserted_pk[0])
        for f in obs.features:
            conn.execute(
                s.vehicle_features.insert().values(
                    observation_id=obs_id, feature=f.feature.value, status=f.status.value,
                    source_kind=f.evidence.source_kind.value if f.evidence else None,
                    url=f.evidence.url if f.evidence else None, quote=f.evidence.quote if f.evidence else None,
                    page_hash=f.evidence.page_hash if f.evidence else None, sufficiency=f.sufficiency.value,
                    conflict=f.sufficiency.value == "CONFLICT",
                )
            )  # fmt: skip
        for p in obs.prices:
            conn.execute(
                s.price_components.insert().values(
                    observation_id=obs_id,
                    kind=p.kind.value,
                    cents=p.cents,
                    label=p.label,
                    eligibility_text=p.eligibility_text,
                    quote=p.quote,
                )
            )
        status = StagingStatus.PENDING_REVIEW if obs.vin in review_vins else StagingStatus.VALIDATED
        conn.execute(
            text(
                "INSERT OR REPLACE INTO inventory_staging (run_id, dealer_id, vin, status, reason) VALUES (:r,:d,:v,:s,:reason)"
            ),
            {
                "r": run_id,
                "d": obs.dealer_id,
                "v": obs.vin,
                "s": status.value,
                "reason": review_vins.get(obs.vin),
            },
        )
        if obs.vin in review_vins and not _review_exists(
            conn, run_id, obs.dealer_id, obs.vin, review_vins[obs.vin]
        ):
            conn.execute(
                s.review_items.insert().values(
                    run_id=run_id,
                    dealer_id=obs.dealer_id,
                    vin=obs.vin,
                    issue=review_vins[obs.vin],
                    status="OPEN",
                    created_at=now,
                )
            )
        inserted += 1
    for vin, issue in result.review_reasons:
        if vin not in {o.vin for o in result.observations} and not _review_exists(
            conn, run_id, result.dealer_id, vin, issue
        ):
            conn.execute(
                s.review_items.insert().values(
                    run_id=run_id,
                    dealer_id=result.dealer_id,
                    vin=vin or None,
                    issue=issue,
                    status="OPEN",
                    created_at=now,
                )
            )

    conn.execute(
        text(
            "INSERT OR REPLACE INTO dealer_runs (run_id, dealer_id, status, reason_code, pages_expected, pages_read, pages_failed,"
            " raw_count, eligible_count, duration_ms, cost_usd, baseline_run_id, anomaly, robots_hash, adapter_version, finished_at)"
            " VALUES (:run_id,:dealer_id,:status,:reason,:pe,:pr,:pf,:raw,:elig,:dur,:cost,:baseline,:anomaly,:robots,:adapter,:fin)"
        ),
        {
            "run_id": run_id, "dealer_id": result.dealer_id, "status": result.status.value,
            "reason": result.reason.value if result.reason else None, "pe": result.pages_expected, "pr": result.pages_read,
            "pf": result.pages_failed, "raw": result.raw_count, "elig": result.eligible_count, "dur": result.duration_ms,
            "cost": result.cost_usd, "baseline": baseline_run_id, "anomaly": result.anomaly, "robots": result.robots_hash,
            "adapter": result.adapter_version, "fin": now,
        },
    )  # fmt: skip
    return {
        "observations": inserted,
        "exclusions": len(result.exclusions),
        "attempts": len(result.attempts),
    }


def _upsert_unit_and_listing(conn: Connection, obs: Observation, now: str) -> None:
    unit = conn.execute(select(s.units.c.vin).where(s.units.c.vin == obs.vin)).first()
    if unit is None:
        conn.execute(
            s.units.insert().values(
                vin=obs.vin,
                model=obs.model,
                year=obs.year,
                ag_code=obs.ag_code,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    else:
        conn.execute(update(s.units).where(s.units.c.vin == obs.vin).values(last_seen_at=now))
    norm = normalize_url(obs.detail_url or obs.source_url)
    listing = (
        conn.execute(
            select(s.dealer_listings).where(
                s.dealer_listings.c.dealer_id == obs.dealer_id, s.dealer_listings.c.vin == obs.vin
            )
        )
        .mappings()
        .first()
    )
    if listing is None:
        conn.execute(
            s.dealer_listings.insert().values(
                dealer_id=obs.dealer_id,
                vin=obs.vin,
                normalized_url=norm,
                alternate_urls=json.dumps(list(obs.alternate_urls)),
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    else:
        alts = set(json.loads(listing["alternate_urls"])) | set(obs.alternate_urls)
        if norm != listing["normalized_url"]:
            alts.add(listing["normalized_url"])
        conn.execute(
            update(s.dealer_listings)
            .where(s.dealer_listings.c.listing_id == listing["listing_id"])
            .values(last_seen_at=now, normalized_url=norm, alternate_urls=json.dumps(sorted(alts)))
        )


def record_robots(
    conn: Connection, run_id: str, dealer_id: str, hostname: str, sha: str | None, decision: str
) -> None:
    conn.execute(
        s.robots_snapshots.insert().values(
            run_id=run_id,
            dealer_id=dealer_id,
            hostname=hostname,
            sha256=sha,
            decision=decision,
            fetched_at=iso(utcnow()),
        )
    )
    conn.execute(
        update(s.dealers).where(s.dealers.c.dealer_id == dealer_id).values(robots_status=decision)
    )


# ----------------------------------------------------------------------------- deltas
def record_lifecycle(conn: Connection, *, vin: str, dealer_id: str, run_id: str, from_state: LifecycleState | None,
                     to_state: LifecycleState, reason: str, baseline_run_id: str | None, evidence: str | None = None) -> None:  # fmt: skip
    conn.execute(
        s.listing_lifecycle.insert().values(
            vin=vin, dealer_id=dealer_id, run_id=run_id, from_state=from_state.value if from_state else None,
            to_state=to_state.value, event_at=iso(utcnow()), reason=reason, evidence=evidence, baseline_run_id=baseline_run_id,
        )
    )  # fmt: skip


def record_price_event(conn: Connection, *, run_id: str, baseline_run_id: str, dealer_id: str, vin: str, event: str,
                       before: int | None, after: int | None, delta: int) -> None:  # fmt: skip
    conn.execute(
        s.price_events.insert().values(
            run_id=run_id,
            baseline_run_id=baseline_run_id,
            dealer_id=dealer_id,
            vin=vin,
            event=event,
            before_cents=before,
            after_cents=after,
            delta_cents=delta,
        )
    )


# ----------------------------------------------------------------------------- notifications
def notification_exists(conn: Connection, run_id: str, recipient: str, event_key: str) -> bool:
    r = conn.execute(select(s.notification_events.c.notification_id).where(
        s.notification_events.c.run_id == run_id, s.notification_events.c.recipient == recipient, s.notification_events.c.event_key == event_key)).first()  # fmt: skip
    return r is not None


def record_notification(conn: Connection, *, run_id: str, recipient: str, event_key: str, channel: str,
                        content_hash: str, provider_status: str, error: str | None, sent: bool) -> None:  # fmt: skip
    now = iso(utcnow())
    conn.execute(
        text(
            "INSERT INTO notification_events (run_id, recipient, event_key, channel, content_hash, attempts, provider_status, last_error, created_at, sent_at)"
            " VALUES (:run_id,:recipient,:key,:channel,:hash,1,:status,:err,:now,:sent)"
            " ON CONFLICT(run_id, recipient, event_key) DO UPDATE SET attempts=attempts+1, provider_status=excluded.provider_status,"
            " last_error=excluded.last_error, sent_at=COALESCE(notification_events.sent_at, excluded.sent_at)"
        ),
        {"run_id": run_id, "recipient": recipient, "key": event_key, "channel": channel, "hash": content_hash,
         "status": provider_status, "err": error, "now": now, "sent": now if sent else None},
    )  # fmt: skip


# ----------------------------------------------------------------------------- reads for UI / digest
def dealer_runs_for_run(conn: Connection, run_id: str) -> list[Row]:
    q = (
        select(s.dealer_runs, s.dealers.c.name, s.dealers.c.city, s.dealers.c.platform)
        .join(s.dealers, s.dealers.c.dealer_id == s.dealer_runs.c.dealer_id)
        .where(s.dealer_runs.c.run_id == run_id)
        .order_by(s.dealers.c.city)
    )
    return _rows(conn.execute(q))


def observations_for_run(
    conn: Connection, run_id: str, *, validated_only: bool = True
) -> list[Row]:
    q = (
        select(
            s.observations,
            s.dealers.c.name.label("dealer_name"),
            s.dealers.c.city,
            s.inventory_staging.c.status.label("staging_status"),
        )
        .join(s.dealers, s.dealers.c.dealer_id == s.observations.c.dealer_id)
        .join(
            s.inventory_staging,
            and_(
                s.inventory_staging.c.run_id == s.observations.c.run_id,
                s.inventory_staging.c.dealer_id == s.observations.c.dealer_id,
                s.inventory_staging.c.vin == s.observations.c.vin,
            ),
            isouter=True,
        )
        .where(s.observations.c.run_id == run_id, s.observations.c.rejected.is_(False))
        .order_by(s.observations.c.dealer_id, s.observations.c.comparison_price_cents.nulls_last())
    )
    rows = _rows(conn.execute(q))
    latest: dict[tuple[str, str], Row] = {}
    for r in rows:
        key = (r["dealer_id"], r["vin"])
        if key not in latest or r["version"] > latest[key]["version"]:
            latest[key] = r
    out = list(latest.values())
    if validated_only:
        out = [r for r in out if r["staging_status"] == StagingStatus.VALIDATED.value]
    ids = [int(r["observation_id"]) for r in out]
    extra: dict[int, set[str]] = {}
    if ids:
        for oid, flag in conn.execute(
            select(s.observation_flags.c.observation_id, s.observation_flags.c.flag).where(
                s.observation_flags.c.observation_id.in_(ids)
            )
        ):
            extra.setdefault(int(oid), set()).add(str(flag))
    for r in out:
        flags = set(json.loads(r["flags"]))
        flags.update(extra.get(int(r["observation_id"]), set()))
        r["flags"] = sorted(flags)
    return out


def features_for_observations(
    conn: Connection, observation_ids: Sequence[int]
) -> dict[int, list[Row]]:
    if not observation_ids:
        return {}
    rows = _rows(
        conn.execute(
            select(s.vehicle_features).where(
                s.vehicle_features.c.observation_id.in_(list(observation_ids))
            )
        )
    )
    out: dict[int, list[Row]] = {}
    for r in rows:
        out.setdefault(r["observation_id"], []).append(r)
    return out


def prices_for_observations(
    conn: Connection, observation_ids: Sequence[int]
) -> dict[int, list[Row]]:
    if not observation_ids:
        return {}
    rows = _rows(
        conn.execute(
            select(s.price_components).where(
                s.price_components.c.observation_id.in_(list(observation_ids))
            )
        )
    )
    out: dict[int, list[Row]] = {}
    for r in rows:
        out.setdefault(r["observation_id"], []).append(r)
    return out


def observation_history(conn: Connection, vin: str) -> list[Row]:
    q = (
        select(s.observations, s.runs.c.snapshot_date, s.dealers.c.name.label("dealer_name"))
        .join(s.runs, s.runs.c.run_id == s.observations.c.run_id)
        .join(s.dealers, s.dealers.c.dealer_id == s.observations.c.dealer_id)
        .where(s.observations.c.vin == vin).order_by(s.observations.c.fetched_at)
    )  # fmt: skip
    rows = _rows(conn.execute(q))
    for r in rows:
        r["flags"] = json.loads(r["flags"])
    return rows


def series_by_dealer(conn: Connection, days: int = 90) -> list[Row]:
    """Per dealer per published run: status, eligible count, floor comparison price, floor VIN."""
    q = text(
        """
        SELECT r.run_id, r.snapshot_date, r.started_at, dr.dealer_id, d.name AS dealer_name, d.city, dr.status, dr.reason_code,
               dr.eligible_count, dr.anomaly,
               (SELECT MIN(o.comparison_price_cents) FROM observations o
                 JOIN inventory_staging st ON st.run_id=o.run_id AND st.dealer_id=o.dealer_id AND st.vin=o.vin
                 WHERE o.run_id=dr.run_id AND o.dealer_id=dr.dealer_id AND o.rejected=0 AND st.status='VALIDATED') AS floor_cents,
               (SELECT o.vin FROM observations o
                 JOIN inventory_staging st ON st.run_id=o.run_id AND st.dealer_id=o.dealer_id AND st.vin=o.vin
                 WHERE o.run_id=dr.run_id AND o.dealer_id=dr.dealer_id AND o.rejected=0 AND st.status='VALIDATED'
                   AND o.comparison_price_cents IS NOT NULL ORDER BY o.comparison_price_cents LIMIT 1) AS floor_vin
        FROM dealer_runs dr JOIN runs r ON r.run_id=dr.run_id JOIN dealers d ON d.dealer_id=dr.dealer_id
        WHERE r.status='PUBLISHED' AND r.started_at >= datetime('now', :window)
        ORDER BY r.started_at, d.city
        """
    )
    return _rows(conn.execute(q, {"window": f"-{days} days"}))


def price_events_for_run(conn: Connection, run_id: str) -> list[Row]:
    q = (
        select(s.price_events, s.dealers.c.name.label("dealer_name"))
        .join(s.dealers, s.dealers.c.dealer_id == s.price_events.c.dealer_id)
        .where(s.price_events.c.run_id == run_id)
        .order_by(s.price_events.c.delta_cents)
    )
    return _rows(conn.execute(q))


def lifecycle_for_run(conn: Connection, run_id: str) -> list[Row]:
    q = (
        select(s.listing_lifecycle, s.dealers.c.name.label("dealer_name"))
        .join(s.dealers, s.dealers.c.dealer_id == s.listing_lifecycle.c.dealer_id)
        .where(s.listing_lifecycle.c.run_id == run_id)
    )
    return _rows(conn.execute(q))


def exclusions_for_run(conn: Connection, run_id: str) -> list[Row]:
    q = (
        select(s.exclusions, s.dealers.c.name.label("dealer_name"))
        .join(s.dealers, s.dealers.c.dealer_id == s.exclusions.c.dealer_id)
        .where(s.exclusions.c.run_id == run_id)
        .order_by(s.exclusions.c.reason_code)
    )
    return _rows(conn.execute(q))


def open_review_items(conn: Connection) -> list[Row]:
    q = (
        select(s.review_items, s.dealers.c.name.label("dealer_name"))
        .join(s.dealers, s.dealers.c.dealer_id == s.review_items.c.dealer_id)
        .where(s.review_items.c.status == "OPEN")
        .order_by(desc(s.review_items.c.created_at))
    )
    return _rows(conn.execute(q))


def pending_review_count(conn: Connection, run_id: str) -> int:
    return int(
        conn.execute(
            select(func.count())
            .select_from(s.inventory_staging)
            .where(
                s.inventory_staging.c.run_id == run_id,
                s.inventory_staging.c.status == StagingStatus.PENDING_REVIEW.value,
            )
        ).scalar()
        or 0
    )


def coverage(conn: Connection) -> list[Row]:
    q = text(
        """
        SELECT d.*,
               (SELECT r.snapshot_date FROM dealer_runs dr JOIN runs r ON r.run_id=dr.run_id
                 WHERE dr.dealer_id=d.dealer_id AND dr.status='REPORTED' AND r.status='PUBLISHED' ORDER BY r.started_at DESC LIMIT 1) AS last_success_date,
               (SELECT dr.status FROM dealer_runs dr JOIN runs r ON r.run_id=dr.run_id
                 WHERE dr.dealer_id=d.dealer_id ORDER BY r.started_at DESC LIMIT 1) AS last_status,
               (SELECT dr.reason_code FROM dealer_runs dr JOIN runs r ON r.run_id=dr.run_id
                 WHERE dr.dealer_id=d.dealer_id ORDER BY r.started_at DESC LIMIT 1) AS last_reason,
               (SELECT dr.finished_at FROM dealer_runs dr JOIN runs r ON r.run_id=dr.run_id
                 WHERE dr.dealer_id=d.dealer_id ORDER BY r.started_at DESC LIMIT 1) AS last_attempt_at
        FROM dealers d ORDER BY d.city
        """
    )
    return _rows(conn.execute(q))


def dedup_cross_dealer(conn: Connection, run_id: str) -> dict[str, list[str]]:
    """VINs appearing at more than one dealer in a run -> dealer ids."""
    q = (
        select(s.observations.c.vin, s.observations.c.dealer_id)
        .where(s.observations.c.run_id == run_id, s.observations.c.rejected.is_(False))
        .distinct()
    )
    seen: dict[str, list[str]] = {}
    for vin, dealer in conn.execute(q):
        seen.setdefault(vin, []).append(dealer)
    return {v: ds for v, ds in seen.items() if len(ds) > 1}


def add_flag_to_observations(conn: Connection, run_id: str, vin: str, flag: str) -> None:
    """Record a flag beside the observation. The observation row itself stays immutable."""
    rows = conn.execute(
        select(s.observations.c.observation_id).where(
            s.observations.c.run_id == run_id, s.observations.c.vin == vin
        )
    ).all()
    now = iso(utcnow())
    for (oid,) in rows:
        exists = conn.execute(
            select(s.observation_flags.c.flag).where(
                s.observation_flags.c.observation_id == oid, s.observation_flags.c.flag == flag
            )
        ).first()
        if exists:
            continue
        conn.execute(
            s.observation_flags.insert().values(observation_id=oid, flag=flag, created_at=now)
        )


def replace_catalog(conn: Connection, vehicles: Sequence[Any], imported_at: str) -> int:
    """Replace the workbook catalog. `vin` is the primary key, so a VIN cannot be stored twice."""
    from carshop.catalog.workbook import CatalogVehicle

    conn.execute(delete(s.catalog_features))
    conn.execute(delete(s.catalog_vehicles))
    rows = [v for v in vehicles if isinstance(v, CatalogVehicle)]
    if rows:
        conn.execute(
            s.catalog_vehicles.insert(),
            [
                {
                    "vin": v.vin,
                    "dealer_name": v.dealer_name,
                    "city": v.city,
                    "phone": v.phone,
                    "year": v.year,
                    "model": v.model,
                    "trim": v.trim,
                    "trim_code": v.trim_code,
                    "stock": v.stock,
                    "exterior": v.exterior,
                    "interior": v.interior,
                    "mileage": v.mileage,
                    "msrp_cents": v.msrp_cents,
                    "advertised_cents": v.advertised_cents,
                    "listing_url": v.listing_url,
                    "as_of": v.as_of,
                    "prior_dealer": v.prior_dealer,
                    "packages_text": v.packages_text,
                    "imported_at": imported_at,
                }
                for v in rows
            ],
        )
        feat_rows = [
            {"vin": v.vin, "feature": code, "status": status, "quote": None}
            for v in rows
            for code, status in sorted(v.features.items())
        ]
        if feat_rows:
            conn.execute(s.catalog_features.insert(), feat_rows)
    return len(rows)


def catalog_vehicles(conn: Connection) -> list[Row]:
    rows = _rows(
        conn.execute(
            select(s.catalog_vehicles).order_by(
                s.catalog_vehicles.c.dealer_name, s.catalog_vehicles.c.vin
            )
        )
    )
    feats: dict[str, dict[str, str]] = {}
    for vin, feature, status in conn.execute(
        select(s.catalog_features.c.vin, s.catalog_features.c.feature, s.catalog_features.c.status)
    ):
        feats.setdefault(vin, {})[feature] = status
    for row in rows:
        row["features"] = feats.get(row["vin"], {})
    return rows


def catalog_transferred(conn: Connection) -> list[Row]:
    return _rows(
        conn.execute(
            select(s.catalog_vehicles).where(s.catalog_vehicles.c.prior_dealer.is_not(None))
        )
    )


def dap_status_of(row: Row) -> Tri:
    return Tri(row["dap_status"])


def parse_dt(s_: str) -> datetime:
    return datetime.fromisoformat(s_)
