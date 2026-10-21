"""Saved searches: storage, candidates, preview, and the baseline evaluation (§5, §6.2, §8).

Reads committed rows only. Writes `saved_searches` and `search_matches`, nothing else.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, delete, desc, func, or_, select, update

from carshop.domain.codes import DealerStatus, RunStatus
from carshop.domain.display import (
    canon_exterior,
    canon_interior,
    cash_cents,
    index_features,
    price_gap_reason,
)
from carshop.domain.pricing import fmt_cents
from carshop.reference.data import exterior_family, interior_family
from carshop.shortlist import match as m
from carshop.shortlist import saved as sl
from carshop.storage import repos
from carshop.storage import schema as s

Row = dict[str, Any]
MAX_SEARCHES = 20
_READ = (DealerStatus.REPORTED.value, DealerStatus.PARTIAL.value)


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


# ---------------------------------------------------------------- candidates (§6.2)


class Geo:
    """Dealer coordinates and city centroids, computed the way Find a car does."""

    def __init__(self, conn: Connection, coords: dict[str, tuple[float, float]]) -> None:
        self.by_dealer: dict[str, tuple[float, float]] = {}
        cities: dict[str, list[tuple[float, float]]] = {}
        for d in repos.list_dealers(conn):
            latlon = coords.get(str(d["center_number"] or ""))
            if not d["active"] or latlon is None:
                continue
            self.by_dealer[str(d["dealer_id"])] = latlon
            cities.setdefault(str(d["city"]), []).append(latlon)
        self.centroids = {
            city: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            for city, pts in cities.items()
        }

    def centroid(self, criteria: Row) -> tuple[float, float] | None:
        city = criteria["location"]["city"]
        return self.centroids.get(city) if city else None


def latest_reads(conn: Connection, *, upto_started_at: str | None = None) -> dict[str, str]:
    """dealer_id -> run_id of that dealer's most recent published REPORTED or PARTIAL read."""
    dr, r = s.dealer_runs, s.runs
    q = (
        select(dr.c.dealer_id, dr.c.run_id)
        .join(r, r.c.run_id == dr.c.run_id)
        .where(r.c.status == RunStatus.PUBLISHED.value, dr.c.status.in_(_READ))
        .order_by(r.c.started_at)
    )
    if upto_started_at is not None:
        q = q.where(r.c.started_at <= upto_started_at)
    out: dict[str, str] = {}
    for dealer_id, run_id in conn.execute(q):
        out[str(dealer_id)] = str(run_id)
    return out


def car_record(row: Row, features: dict[str, Row], geo: Geo | None) -> Row:
    """One observation in the shape the matcher reads."""
    ext_name, _ = canon_exterior(row["exterior_raw"])
    int_name, _ = canon_interior(row["interior_raw"])
    feats = {f: str(features[f]["status"]) if f in features else "UNKNOWN" for f in m.FEATURES}
    if row["m_sport"]:
        feats["M_SPORT"] = "YES"
    price = cash_cents(row["comparison_price_cents"], row.get("price_basis"))
    latlon = geo.by_dealer.get(str(row["dealer_id"])) if geo else None
    return {
        "vin": str(row["vin"]), "dealer_id": str(row["dealer_id"]), "dealer_name": row.get("dealer_name"),
        "city": row.get("city"), "observation_id": int(row["observation_id"]), "run_id": str(row["run_id"]),
        "price": price, "msrp": row["msrp_cents"],
        "no_price_why": None if price is not None else price_gap_reason(set(row["flags"]), row["msrp_cents"]),
        "ext_name": ext_name, "ext_code": row["exterior_code"], "ext_family": exterior_family(row["exterior_code"]),
        "int_name": int_name, "int_code": row["interior_code"], "int_family": interior_family(row["interior_code"]),
        "in_transit": bool(row["in_transit"]), "features": feats,
        "lat": latlon[0] if latlon else None, "lon": latlon[1] if latlon else None,
    }  # fmt: skip


def candidates(
    conn: Connection, geo: Geo | None, *, upto_started_at: str | None = None
) -> list[Row]:
    """Per dealer: its latest read's validated, non-rejected cars. A dealer not read today keeps
    its previous cars (they are never New because of today's run)."""
    reads = latest_reads(conn, upto_started_at=upto_started_at)
    by_run: dict[str, set[str]] = {}
    for dealer_id, run_id in reads.items():
        by_run.setdefault(run_id, set()).add(dealer_id)
    rows: list[Row] = []
    for run_id, dealers in sorted(by_run.items()):
        rows += [
            r for r in repos.observations_for_run(conn, run_id) if str(r["dealer_id"]) in dealers
        ]
    feats = repos.features_for_observations(conn, [int(r["observation_id"]) for r in rows])
    cars = [
        car_record(r, index_features(feats.get(int(r["observation_id"]), [])), geo) for r in rows
    ]
    return sorted(cars, key=lambda c: (c["vin"], c["dealer_id"]))


def evaluate_all(criteria: Row, cars: list[Row], geo: Geo | None) -> list[tuple[Row, Row]]:
    centroid = geo.centroid(criteria) if geo else None
    out = []
    for car in cars:
        res = m.evaluate(criteria, car, centroid)
        if res is not None:
            out.append((car, res))
    return m.rank(out)


def preview(conn: Connection, criteria: Row, geo: Geo | None) -> Row:
    """Dry run for the form. Writes nothing."""
    c = m.normalize(criteria)
    results = evaluate_all(c, candidates(conn, geo), geo)
    return {
        "match": sum(1 for _c, r in results if r["tier"] == m.MATCH),
        "possible": sum(1 for _c, r in results if r["tier"] == m.POSSIBLE),
        "top": [_result_row(car, res) for car, res in results[:5]],
    }


def _result_row(car: Row, res: Row) -> Row:
    return {
        "vin": car["vin"], "dealer_id": car["dealer_id"], "dealer_name": car["dealer_name"],
        "city": car["city"], "price": car["price"], "no_price_why": car["no_price_why"],
        "ext_name": car["ext_name"], "int_name": car["int_name"], **res,
    }  # fmt: skip


# ---------------------------------------------------------------- storage


def _load(row: Any) -> Row:
    out = dict(row)
    out["criteria"] = json.loads(out.pop("criteria_json"))
    return out


def get_search(conn: Connection, search_id: int) -> Row | None:
    row = conn.execute(
        select(s.saved_searches).where(
            s.saved_searches.c.search_id == search_id, s.saved_searches.c.removed_at.is_(None)
        )
    ).mappings().first()  # fmt: skip
    return _load(row) if row else None


def active_searches(conn: Connection) -> list[Row]:
    q = select(s.saved_searches).where(s.saved_searches.c.removed_at.is_(None))
    return [_load(r) for r in conn.execute(q.order_by(s.saved_searches.c.search_id)).mappings()]


def _baseline_run(conn: Connection) -> Row | None:
    return repos.latest_published_run(conn)


def write_evaluation(
    conn: Connection, search: Row, run_id: str, results: list[tuple[Row, Row]], *,
    baseline: bool, now: datetime | None = None,
) -> list[Row]:  # fmt: skip
    """Replace this search's rows for this run. New = no earlier row for this search (§6.3)."""
    sm = s.search_matches
    sid = int(search["search_id"])
    conn.execute(delete(s.search_matches).where(sm.c.search_id == sid, sm.c.run_id == run_id))
    earlier: dict[str, str] = {}
    prev_run = conn.execute(
        select(sm.c.run_id).where(sm.c.search_id == sid).order_by(desc(sm.c.evaluated_at)).limit(1)
    ).scalar()
    seen = {
        str(v)
        for v in conn.execute(select(sm.c.vin).where(sm.c.search_id == sid).distinct()).scalars()
    }
    if prev_run is not None:
        for vin, tier in conn.execute(
            select(sm.c.vin, sm.c.tier).where(sm.c.search_id == sid, sm.c.run_id == prev_run)
        ):
            earlier[str(vin)] = str(tier)
    ts = _now(now)
    written: list[Row] = []
    for car, res in results:
        row = {
            "search_id": sid, "run_id": run_id, "vin": car["vin"], "dealer_id": car["dealer_id"],
            "observation_id": car["observation_id"], "tier": res["tier"], "score": res["score"],
            "reasons_json": json.dumps(res["reasons"], ensure_ascii=False),
            "is_new": not baseline and car["vin"] not in seen and str(car["run_id"]) == run_id,
            "became_full_match": not baseline
            and earlier.get(car["vin"]) == m.POSSIBLE
            and res["tier"] == m.MATCH,
            "evaluated_at": ts, "baseline": baseline,
        }  # fmt: skip
        conn.execute(s.search_matches.insert().values(**row))
        written.append(row)
    return written


def run_baseline(
    conn: Connection, search: Row, geo: Geo | None, *, now: datetime | None = None
) -> Row:
    """Evaluate one search against the latest data and record it as baseline, never as New."""
    run = _baseline_run(conn)
    ts = _now(now)
    rows: list[Row] = []
    if run is not None:
        results = evaluate_all(search["criteria"], candidates(conn, geo), geo)
        rows = write_evaluation(conn, search, str(run["run_id"]), results, baseline=True, now=now)
    conn.execute(
        update(s.saved_searches)
        .where(s.saved_searches.c.search_id == search["search_id"])
        .values(baseline_at=ts)
    )
    return {
        "match": sum(1 for r in rows if r["tier"] == m.MATCH),
        "possible": sum(1 for r in rows if r["tier"] == m.POSSIBLE),
    }


def _ensure_base_saved(conn: Connection, base_vin: str | None, now: datetime | None) -> None:
    if base_vin and sl.get_saved(conn, base_vin) is None:
        sl.save_car(conn, base_vin, now=now)


def create_search(
    conn: Connection, name: str, criteria: Row, geo: Geo | None, *,
    alerts: bool = True, now: datetime | None = None,
) -> tuple[Row, Row]:  # fmt: skip
    """Create, then run the baseline in the same short transaction. Returns (search, counts)."""
    clean = m.normalize(criteria)
    title = " ".join((name or "").split())[:120]
    if not title:
        raise sl.ShortlistError(400, "Give the search a name.")
    count = (
        conn.execute(
            select(func.count())
            .select_from(s.saved_searches)
            .where(s.saved_searches.c.removed_at.is_(None))
        ).scalar()
        or 0
    )
    if count >= MAX_SEARCHES:
        raise sl.ShortlistError(
            409, f"You have {MAX_SEARCHES} saved searches. Delete one to add another."
        )
    base_vin = clean["base"]["vin"] if clean["base"] else None
    _ensure_base_saved(conn, base_vin, now)
    ts = _now(now)
    inserted = conn.execute(
        s.saved_searches.insert().values(
            name=title, base_vin=base_vin, criteria_json=json.dumps(clean, sort_keys=True),
            criteria_version=m.CRITERIA_VERSION, created_at=ts, updated_at=ts,
            paused=False, alerts=alerts, removed_at=None, baseline_at=None,
        )
    )  # fmt: skip
    assert inserted.inserted_primary_key is not None
    search = get_search(conn, int(inserted.inserted_primary_key[0]))
    assert search is not None
    counts = run_baseline(conn, search, geo, now=now)
    return search, counts


def update_search(
    conn: Connection, search_id: int, geo: Geo | None, *, name: str | None = None,
    criteria: Row | None = None, paused: bool | None = None, alerts: bool | None = None,
    now: datetime | None = None,
) -> tuple[Row, Row | None]:  # fmt: skip
    search = get_search(conn, search_id)
    if search is None:
        raise sl.ShortlistError(404, "That search is not on your shortlist.")
    values: Row = {"updated_at": _now(now)}
    if name is not None:
        title = " ".join(name.split())[:120]
        if not title:
            raise sl.ShortlistError(400, "Give the search a name.")
        values["name"] = title
    changed = False
    if criteria is not None:
        clean = m.normalize(criteria)
        if clean != search["criteria"]:
            changed = True
            values["criteria_json"] = json.dumps(clean, sort_keys=True)
            values["base_vin"] = clean["base"]["vin"] if clean["base"] else None
            _ensure_base_saved(conn, values["base_vin"], now)
    if paused is not None:
        values["paused"] = paused
    if alerts is not None:
        values["alerts"] = alerts
    conn.execute(
        update(s.saved_searches).where(s.saved_searches.c.search_id == search_id).values(**values)
    )
    fresh = get_search(conn, search_id)
    assert fresh is not None
    counts = run_baseline(conn, fresh, geo, now=now) if changed else None
    return fresh, counts


def remove_search(conn: Connection, search_id: int, *, now: datetime | None = None) -> bool:
    ts = _now(now)
    res = conn.execute(
        update(s.saved_searches)
        .where(s.saved_searches.c.search_id == search_id, s.saved_searches.c.removed_at.is_(None))
        .values(removed_at=ts, updated_at=ts)
    )
    return bool(res.rowcount)


# ---------------------------------------------------------------- reads for the screen


def current_matches(conn: Connection, search_id: int) -> tuple[str | None, list[Row]]:
    """Rows from this search's latest evaluation, Match first, then score."""
    sm = s.search_matches
    last = conn.execute(
        select(sm.c.run_id, sm.c.evaluated_at)
        .where(sm.c.search_id == search_id)
        .order_by(desc(sm.c.evaluated_at))
        .limit(1)
    ).first()
    if last is None:
        return None, []
    q = (
        select(sm, s.observations.c.comparison_price_cents, s.observations.c.price_basis,
               s.observations.c.exterior_raw, s.observations.c.interior_raw,
               s.dealers.c.name.label("dealer_name"), s.dealers.c.city)
        .join(s.observations, s.observations.c.observation_id == sm.c.observation_id)
        .join(s.dealers, s.dealers.c.dealer_id == sm.c.dealer_id)
        .where(sm.c.search_id == search_id, sm.c.run_id == last[0])
    )  # fmt: skip
    rows = []
    for r in conn.execute(q).mappings():
        row = dict(r)
        row["reasons"] = json.loads(row.pop("reasons_json"))
        row["price"] = cash_cents(row["comparison_price_cents"], row["price_basis"])
        row["ext_name"] = canon_exterior(row["exterior_raw"])[0]
        row["int_name"] = canon_interior(row["interior_raw"])[0]
        rows.append(row)
    rows.sort(
        key=lambda r: (
            r["tier"] != m.MATCH,
            -r["score"],
            r["price"] is None,
            r["price"] or 0,
            r["vin"],
        )
    )
    return str(last[1]), rows


def overview(conn: Connection) -> list[Row]:
    """Saved searches tab: each search with counts and last evaluated time."""
    latest = repos.latest_published_run(conn)
    out = []
    for srch in active_searches(conn):
        evaluated, rows = current_matches(conn, int(srch["search_id"]))
        new_today = 0
        if latest is not None:
            new_today = conn.execute(
                select(func.count()).select_from(s.search_matches).where(
                    and_(s.search_matches.c.search_id == srch["search_id"],
                         s.search_matches.c.run_id == latest["run_id"], s.search_matches.c.is_new.is_(True))
                )
            ).scalar() or 0  # fmt: skip
        out.append(
            {
                **srch,
                "summary": m.summary(srch["criteria"]),
                "match": sum(1 for r in rows if r["tier"] == m.MATCH),
                "possible": sum(1 for r in rows if r["tier"] == m.POSSIBLE),
                "new_today": int(new_today),
                "evaluated_at": evaluated,
            }
        )
    return out


def wish_search_name(today: str) -> str:
    return f"My wish list · {today}"


MATCHER_FAILED = "matcher_failed"
EMPTY_COUNTS: Row = {"searches": 0, "match": 0, "possible": 0, "new": 0, "error": None}


def _listed_vins(conn: Connection, run_id: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for o in repos.observations_for_run(conn, run_id, validated_only=False):
        out.setdefault(str(o["dealer_id"]), set()).add(str(o["vin"]))
    return out


def _keep_partial(
    conn: Connection,
    search: Row,
    results: list[tuple[Row, Row]],
    statuses: dict[str, str],
    listed: dict[str, set[str]],
) -> list[tuple[Row, Row]]:
    """A car absent from a PARTIAL read is not removed from matches (§6.2)."""
    partial = {d for d, st in statuses.items() if st == DealerStatus.PARTIAL.value}
    if not partial:
        return results
    have = {(c["vin"], c["dealer_id"]) for c, _r in results}
    _when, prev = current_matches(conn, int(search["search_id"]))
    extra: list[tuple[Row, Row]] = []
    for row in prev:
        did, vin = str(row["dealer_id"]), str(row["vin"])
        if did not in partial or (vin, did) in have or vin in listed.get(did, set()):
            continue
        car = {
            "vin": vin, "dealer_id": did, "dealer_name": row["dealer_name"], "city": row.get("city"),
            "observation_id": int(row["observation_id"]), "run_id": str(row["run_id"]),
            "price": row.get("price"), "ext_name": row.get("ext_name"), "int_name": row.get("int_name"),
        }  # fmt: skip
        extra.append((car, {"tier": row["tier"], "score": row["score"], "reasons": row["reasons"]}))
    return results + extra


def evaluate_run(
    conn: Connection, run_id: str, geo: Geo | None, *, now: datetime | None = None
) -> Row:
    """Evaluate every active (not paused) search against this published run. Own transaction."""
    run = repos.get_run(conn, run_id)
    if run is None:
        return dict(EMPTY_COUNTS)
    statuses = {
        str(d["dealer_id"]): str(d["status"]) for d in repos.dealer_runs_for_run(conn, run_id)
    }
    listed = _listed_vins(conn, run_id)
    cars = candidates(conn, geo, upto_started_at=str(run["started_at"]))
    counts = dict(EMPTY_COUNTS)
    for search in active_searches(conn):
        if search["paused"]:
            continue
        counts["searches"] += 1
        results = _keep_partial(
            conn, search, evaluate_all(search["criteria"], cars, geo), statuses, listed
        )
        written = write_evaluation(conn, search, run_id, results, baseline=False, now=now)
        counts["match"] += sum(1 for r in written if r["tier"] == m.MATCH)
        counts["possible"] += sum(1 for r in written if r["tier"] == m.POSSIBLE)
        counts["new"] += sum(1 for r in written if r["is_new"])
    clear_matcher_failed(conn, run_id)
    return counts


def mark_matcher_failed(conn: Connection, run_id: str, error: str) -> None:
    repos.record_notification(
        conn,
        run_id=run_id,
        recipient="operator",
        event_key=MATCHER_FAILED,
        channel="log",
        content_hash="",
        provider_status="error",
        error=error[:300],
        sent=True,
    )


def clear_matcher_failed(conn: Connection, run_id: str) -> None:
    conn.execute(
        delete(s.notification_events).where(
            s.notification_events.c.run_id == run_id,
            s.notification_events.c.event_key == MATCHER_FAILED,
        )
    )


def matcher_failed(conn: Connection, run_id: str) -> bool:
    return repos.notification_exists(conn, run_id, "operator", MATCHER_FAILED)


def new_count(conn: Connection, run_id: str | None) -> int:
    if not run_id:
        return 0
    n = conn.execute(
        select(func.count())
        .select_from(s.search_matches)
        .where(s.search_matches.c.run_id == run_id, s.search_matches.c.is_new.is_(True))
    ).scalar()
    return int(n or 0)


def new_events(conn: Connection, run_id: str | None) -> list[Row]:
    """New matches and now-a-full-match rows, grouped by search, for the New today tab."""
    if not run_id:
        return []
    sm = s.search_matches
    q = (
        select(
            sm, s.saved_searches.c.name,
            s.observations.c.comparison_price_cents, s.observations.c.price_basis,
            s.observations.c.exterior_raw, s.observations.c.interior_raw,
            s.dealers.c.name.label("dealer_name"), s.dealers.c.city,
        )
        .join(s.saved_searches, s.saved_searches.c.search_id == sm.c.search_id)
        .join(s.observations, s.observations.c.observation_id == sm.c.observation_id)
        .join(s.dealers, s.dealers.c.dealer_id == sm.c.dealer_id)
        .where(sm.c.run_id == run_id, s.saved_searches.c.removed_at.is_(None))
        .where(or_(sm.c.is_new.is_(True), sm.c.became_full_match.is_(True)))
    )  # fmt: skip
    grouped: dict[int, Row] = {}
    for r in conn.execute(q).mappings():
        row = dict(r)
        row["reasons"] = json.loads(row.pop("reasons_json"))
        row["price"] = cash_cents(row["comparison_price_cents"], row["price_basis"])
        row["ext_name"] = canon_exterior(row["exterior_raw"])[0]
        row["int_name"] = canon_interior(row["interior_raw"])[0]
        sid = int(row["search_id"])
        grouped.setdefault(sid, {"search_id": sid, "name": row["name"], "rows": []})["rows"].append(
            row
        )
    out = list(grouped.values())
    for g in out:
        g["rows"].sort(key=lambda r: (r["tier"] != m.MATCH, -r["score"], r["vin"]))
    out.sort(key=lambda g: g["name"])
    return out


def new_tags(conn: Connection, run_id: str | None) -> dict[str, list[str]]:
    """VIN -> search names that announced it as New in this run (Find a car tags)."""
    out: dict[str, list[str]] = {}
    for g in new_events(conn, run_id):
        for row in g["rows"]:
            if row["is_new"]:
                out.setdefault(str(row["vin"]), []).append(str(g["name"]))
    return out


def matches_for_vin(conn: Connection, vin: str) -> list[Row]:
    """Current searches this VIN matches, for the car page."""
    out = []
    for srch in active_searches(conn):
        _when, rows = current_matches(conn, int(srch["search_id"]))
        hit = next((r for r in rows if r["vin"] == vin), None)
        if hit is not None:
            out.append({
                **srch, "tier": hit["tier"], "score": hit["score"], "reasons": hit["reasons"],
                "is_new": bool(hit["is_new"]),
            })  # fmt: skip
    return out


def digest_search_lines(conn: Connection, run_id: str) -> list[str]:
    """New-match lines for the digest. Empty when nothing is new or the Matcher failed."""
    if matcher_failed(conn, run_id):
        return []
    lines: list[str] = []
    for g in new_events(conn, run_id):
        srch = get_search(conn, int(g["search_id"]))
        if srch is None or srch["paused"] or not srch["alerts"]:
            continue
        news = [r for r in g["rows"] if r["is_new"]]
        if not news:
            continue
        lines.append(f"  {g['name']} ({len(news)} new)")
        for r in news:
            mark = "✓" if r["tier"] == m.MATCH else "?"
            price = fmt_cents(int(r["price"])) if r["price"] is not None else "no cash price"
            diffs = [x["t"] for x in r["reasons"] if x["k"] == "diff"]
            extra = (" · " + " · ".join(diffs)) if diffs else ""
            lines.append(
                f"    {mark} {r['vin']} · {r['dealer_name']} · {price} · "
                f"{r['ext_name']} / {r['int_name']}{extra}"
            )
    return lines


def digest_line_keys(conn: Connection, run_id: str) -> list[str]:
    """Dedup keys for this run's digest lines (F21 / §7.3)."""
    keys = [f"match:{g['search_id']}:{r['vin']}" for g in new_events(conn, run_id) for r in g["rows"] if r["is_new"]]
    from carshop.shortlist.saved import digest_watch_keys

    keys += digest_watch_keys(conn, run_id)
    return keys
