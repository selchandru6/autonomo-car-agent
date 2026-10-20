"""View models for the screen (requirements 11, 12). Reads committed rows only."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection

from carshop.config.loaders import Preferences
from carshop.domain.codes import Feature, Tri
from carshop.domain.pricing import fmt_cents
from carshop.reference.data import BLACK_INTERIOR_CODES, GREY_EXTERIOR_CODES
from carshop.storage import repos

Row = dict[str, Any]

_PRIORITIZED = [f.value for f in Feature]


def _age(fetched_at: str | None, tz: str) -> str:
    if not fetched_at:
        return "—"
    dt = datetime.fromisoformat(fetched_at)
    delta = datetime.now(UTC) - dt
    hours = delta.total_seconds() / 3600
    local = dt.astimezone(ZoneInfo(tz)).strftime("%b %d %H:%M")
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)} min ago ({local})"
    if hours < 48:
        return f"{hours:.0f} h ago ({local})"
    return f"{hours / 24:.0f} d ago ({local})"


@dataclass(slots=True)
class Unit:
    row: Row
    features: dict[str, Row] = field(default_factory=dict)
    prices: list[Row] = field(default_factory=list)
    fit_score: int = 0
    confidence_score: int = 0
    fit_reasons: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    @property
    def vin(self) -> str:
        return str(self.row["vin"])

    def feat(self, name: str) -> str:
        f = self.features.get(name)
        return str(f["status"]) if f else Tri.UNKNOWN.value

    @property
    def price_label(self) -> str:
        c = self.row["comparison_price_cents"]
        if c is not None:
            return fmt_cents(int(c))
        flags = self.row["flags"]
        if "PRICE_IMPLAUSIBLE" in flags:
            return "implausible"
        if "CALL_FOR_PRICE" in flags:
            return "call for price"
        if "PRICE_IS_PAYMENT" in flags:
            return "payment only"
        if "CONDITIONAL_ONLY" in flags:
            return "conditional only"
        return "not shown"


def load_units(conn: Connection, run_id: str, prefs: Preferences) -> list[Unit]:
    rows = repos.observations_for_run(conn, run_id, validated_only=True)
    ids = [int(r["observation_id"]) for r in rows]
    feats = repos.features_for_observations(conn, ids)
    prices = repos.prices_for_observations(conn, ids)
    units: list[Unit] = []
    for r in rows:
        u = Unit(
            row=r,
            features={f["feature"]: f for f in feats.get(int(r["observation_id"]), [])},
            prices=prices.get(int(r["observation_id"]), []),
        )
        score(u, prefs)
        units.append(u)
    return units


def score(u: Unit, prefs: Preferences) -> None:
    """Preference fit and evidence confidence are separate (F20). UNKNOWN never matches."""
    w = prefs.weights
    total = (
        w.dap
        + w.exterior_grey
        + w.interior_non_black
        + w.massage
        + w.panoramic_roof
        + w.sky_lounge
        + w.executive
        + w.climate_comfort
        + w.air_suspension
    )
    got = 0
    reasons: list[str] = []
    unknowns: list[str] = []
    if u.feat("DAP") == "YES":
        got += w.dap
        reasons.append("Driving Assistance Professional")
    elif u.feat("DAP") == "UNKNOWN":
        unknowns.append("DAP")
    ext = u.row["exterior_code"]
    if ext in GREY_EXTERIOR_CODES:
        got += w.exterior_grey
        reasons.append("grey exterior")
    elif ext is None:
        unknowns.append("exterior code")
    interior = u.row["interior_code"]
    if interior is not None and interior not in BLACK_INTERIOR_CODES:
        got += w.interior_non_black
        reasons.append("non-black interior")
    elif interior is None:
        unknowns.append("interior code")
    for feat, weight, label in (
        ("MASSAGE", w.massage, "massage seats"), ("PANORAMIC_ROOF", w.panoramic_roof, "panoramic roof"),
        ("SKY_LOUNGE", w.sky_lounge, "Sky Lounge"), ("EXECUTIVE", w.executive, "Executive package"),
        ("CLIMATE_COMFORT", w.climate_comfort, "Climate Comfort"), ("AIR_SUSPENSION", w.air_suspension, "air suspension"),
    ):  # fmt: skip
        st = u.feat(feat)
        if st == "YES":
            got += weight
            reasons.append(label)
        elif st == "UNKNOWN":
            unknowns.append(label)
    u.fit_score = round(100 * got / total) if total else 0
    u.fit_reasons = reasons
    u.unknowns = unknowns
    known_feats = sum(1 for f in _PRIORITIZED if u.feat(f) != "UNKNOWN")
    conf = 60 * known_feats / len(_PRIORITIZED)
    conf += 25 if u.row["comparison_price_cents"] is not None else 0
    conf += 8 if u.row["interior_code"] else 0
    conf += 7 if u.row["exterior_code"] else 0
    if any(f.get("conflict") for f in u.features.values()):
        conf -= 15
    u.confidence_score = max(0, min(100, round(conf)))


def apply_filters(units: list[Unit], q: dict[str, str]) -> list[Unit]:
    def want(key: str) -> str | None:
        v = q.get(key, "").strip()
        return v or None

    out = units
    if d := want("dealer"):
        out = [u for u in out if u.row["dealer_id"] == d]
    if c := want("city"):
        out = [u for u in out if u.row["city"] == c]
    if e := want("exterior"):
        out = [u for u in out if (u.row["exterior_code"] or "?") == e]
    if i := want("interior"):
        out = [u for u in out if (u.row["interior_code"] or "?") == i]
    if m := want("msport"):
        out = [u for u in out if bool(u.row["m_sport"]) == (m == "yes")]
    if dap := want("dap"):
        out = [u for u in out if u.feat("DAP") == dap]
    if t := want("transit"):
        out = [u for u in out if bool(u.row["in_transit"]) == (t == "transit")]
    if p := want("price"):
        out = [u for u in out if (u.row["comparison_price_cents"] is not None) == (p == "present")]
    for key, feat in (
        ("massage", "MASSAGE"),
        ("roof", "PANORAMIC_ROOF"),
        ("exec", "EXECUTIVE"),
        ("sky", "SKY_LOUNGE"),
    ):
        if v := want(key):
            out = [u for u in out if u.feat(feat) == v]
    if s := want("sufficiency"):
        out = [u for u in out if u.confidence_score >= int(s)]
    if want("hide_unknown_dap") == "1":
        out = [u for u in out if u.feat("DAP") != "UNKNOWN"]
    return out


def facets(units: list[Unit]) -> dict[str, list[tuple[str, str, int]]]:
    f: dict[str, dict[tuple[str, str], int]] = defaultdict(lambda: defaultdict(int))
    for u in units:
        f["dealer"][(str(u.row["dealer_id"]), str(u.row["dealer_name"]))] += 1
        f["city"][(str(u.row["city"]), str(u.row["city"]))] += 1
        f["exterior"][
            (str(u.row["exterior_code"] or "?"), str(u.row["exterior_raw"] or "unmapped"))
        ] += 1
        f["interior"][
            (str(u.row["interior_code"] or "?"), str(u.row["interior_raw"] or "unknown"))
        ] += 1
    return {
        k: sorted(((a, b, n) for (a, b), n in v.items()), key=lambda x: -x[2]) for k, v in f.items()
    }


def band_series(units: list[Unit]) -> list[dict[str, Any]]:
    by: dict[str, list[list[Any]]] = defaultdict(list)
    for u in units:
        if u.row["comparison_price_cents"] is not None:
            by[str(u.row["dealer_name"])].append(
                [
                    str(u.row["dealer_name"]),
                    int(u.row["comparison_price_cents"]) / 100,
                    u.vin,
                    u.row["exterior_raw"],
                    u.feat("DAP"),
                    bool(u.row["m_sport"]),
                ]
            )
    return [{"name": k, "data": v} for k, v in sorted(by.items())]


def trend_series(conn: Connection, days: int = 90) -> dict[str, Any]:
    rows = repos.series_by_dealer(conn, days)
    dates = sorted({str(r["snapshot_date"]) for r in rows})
    dealers = sorted({str(r["dealer_name"]) for r in rows})
    floor: dict[str, list[Any]] = {d: [None] * len(dates) for d in dealers}
    stock: dict[str, list[Any]] = {d: [None] * len(dates) for d in dealers}
    floor_vin: dict[str, list[Any]] = {d: [None] * len(dates) for d in dealers}
    status: dict[str, list[Any]] = {d: [None] * len(dates) for d in dealers}
    statewide = [0] * len(dates)
    reported_count = [0] * len(dates)
    for r in rows:
        i = dates.index(str(r["snapshot_date"]))
        d = str(r["dealer_name"])
        status[d][i] = {
            "status": r["status"],
            "reason": r["reason_code"],
            "anomaly": bool(r["anomaly"]),
        }
        if r["status"] == "REPORTED":
            stock[d][i] = int(r["eligible_count"])
            floor[d][i] = (int(r["floor_cents"]) / 100) if r["floor_cents"] is not None else None
            floor_vin[d][i] = r["floor_vin"]
            statewide[i] += int(r["eligible_count"])
            reported_count[i] += 1
    return {
        "dates": dates,
        "dealers": dealers,
        "floor": floor,
        "stock": stock,
        "floor_vin": floor_vin,
        "status": status,
        "statewide": statewide,
        "reported_count": reported_count,
    }


def health_grid(conn: Connection, days: int = 30) -> dict[str, Any]:
    rows = repos.series_by_dealer(conn, days)
    dates = sorted({str(r["snapshot_date"]) for r in rows})
    grid: dict[str, dict[str, Row]] = defaultdict(dict)
    for r in rows:
        grid[str(r["dealer_name"])][str(r["snapshot_date"])] = r
    return {"dates": dates, "grid": dict(grid)}


def summarize_coverage(rows: list[Row]) -> dict[str, int]:
    """Coverage denominator = every active Texas registry dealer (v1.3 §4). NO_SOURCE dealers
    (no discoverable inventory URL) are counted in the denominator, never hidden."""
    active = [r for r in rows if r["active"]]
    return {
        "registry": len(rows),
        "active": len(active),
        "active_approved": len(active),  # kept for templates written against v1.2
        "reported": sum(1 for r in active if r["last_status"] == "REPORTED"),
        "no_source": sum(1 for r in active if not r["start_url"]),
    }


def age_label(fetched_at: str | None, tz: str) -> str:
    return _age(fetched_at, tz)
