"""View models for the screen (requirements 11, 12). Reads committed rows only."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select

from carshop.config.loaders import Preferences
from carshop.domain.codes import Feature, Tri
from carshop.domain.pricing import fmt_cents
from carshop.reference.data import BLACK_INTERIOR_CODES, EXTERIOR_COLORS, GREY_EXTERIOR_CODES
from carshop.storage import repos
from carshop.storage import schema as s

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


def _index_features(rows: list[Row]) -> dict[str, Row]:
    """A conflict keeps both stored rows, and the screen must not treat either as a match."""
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["feature"]), []).append(row)
    out: dict[str, Row] = {}
    for name, items in grouped.items():
        if any(item["sufficiency"] == "CONFLICT" for item in items):
            merged = dict(items[0])
            merged["status"] = "UNKNOWN"
            merged["sufficiency"] = "CONFLICT"
            out[name] = merged
        else:
            out[name] = items[-1]
    return out


def load_units(conn: Connection, run_id: str, prefs: Preferences) -> list[Unit]:
    rows = repos.observations_for_run(conn, run_id, validated_only=True)
    ids = [int(r["observation_id"]) for r in rows]
    feats = repos.features_for_observations(conn, ids)
    prices = repos.prices_for_observations(conn, ids)
    units: list[Unit] = []
    for r in rows:
        u = Unit(
            row=r,
            features=_index_features(feats.get(int(r["observation_id"]), [])),
            prices=prices.get(int(r["observation_id"]), []),
        )
        score(u, prefs)
        units.append(u)
    return units


def check_dealers(conn: Connection, run_id: str, prefs: Preferences) -> list[Row]:
    """One row per dealer in a run: its result, pages, raw and eligible counts, and floor."""
    floors: dict[str, tuple[int, str]] = {}
    units: dict[str, int] = defaultdict(int)
    for u in load_units(conn, run_id, prefs):
        dealer = str(u.row["dealer_id"])
        units[dealer] += 1
        cents = u.row["comparison_price_cents"]
        if cents is not None and (dealer not in floors or int(cents) < floors[dealer][0]):
            floors[dealer] = (int(cents), u.vin)
    out: list[Row] = []
    for d in repos.dealer_runs_for_run(conn, run_id):
        floor = floors.get(str(d["dealer_id"]))
        out.append(
            {
                **d,
                "units": units.get(str(d["dealer_id"]), 0),
                "floor_cents": floor[0] if floor else None,
                "floor_vin": floor[1] if floor else None,
            }
        )
    order = {"NOT_REPORTED": 0, "PARTIAL": 1, "NO_SOURCE": 2, "REPORTED": 3}
    out.sort(key=lambda r: (order.get(str(r["status"]), 9), str(r["name"])))
    return out


BUYER_RESULT = {
    "REPORTED": "Checked",
    "PARTIAL": "Partly checked",
    "NOT_REPORTED": "Couldn't read the website",
    "NO_SOURCE": "Website not on file yet",
}
_READ = ("REPORTED", "PARTIAL")


def _latest_per_dealer(series: list[Row]) -> dict[str, Row]:
    """series_by_dealer is oldest first, so the last row per dealer is its newest published result."""
    return {str(r["dealer_id"]): r for r in series}


def dealer_directory(conn: Connection) -> dict[str, list[Row]]:
    """Every registry dealer with its newest published result in buyer words (web-ui 8.3)."""
    latest = _latest_per_dealer(repos.series_by_dealer(conn, 3650))
    active: list[Row] = []
    inactive: list[Row] = []
    for d in repos.coverage(conn):
        r = latest.get(str(d["dealer_id"]))
        status = str(r["status"]) if r else None
        read = status in _READ
        row = {
            "dealer_id": d["dealer_id"],
            "name": d["name"],
            "city": d["city"],
            "status": status,
            "result": BUYER_RESULT.get(status or "", "Not checked yet"),
            "cars": int(r["validated_count"] or 0) if r and read else None,
            "floor_cents": r["floor_cents"] if r and read else None,
            "floor_vin": r["floor_vin"] if r and read else None,
            "last_success": d["last_success_date"],
            "run_id": r["run_id"] if r else None,
            "date": r["snapshot_date"] if r else None,
        }
        (active if d["active"] else inactive).append(row)
    active.sort(key=lambda x: str(x["name"]).lower())
    inactive.sort(key=lambda x: str(x["name"]).lower())
    return {"active": active, "inactive": inactive}


def dealer_detail(
    conn: Connection, dealer_id: str, prefs: Preferences, run_id: str | None = None
) -> Row | None:
    dealer = (
        conn.execute(select(s.dealers).where(s.dealers.c.dealer_id == dealer_id)).mappings().first()
    )
    if dealer is None:
        return None
    series = repos.series_by_dealer(conn, 3650)
    mine = [r for r in series if r["dealer_id"] == dealer_id]
    latest = mine[-1] if mine else None
    shown = run_id or (str(latest["run_id"]) if latest else None)

    window = repos.series_by_dealer(conn, 30)
    dates = sorted({str(r["snapshot_date"]) for r in window})
    by_date = {str(r["snapshot_date"]): r for r in window if r["dealer_id"] == dealer_id}
    strip = [
        {"date": day, "status": by_date[day]["status"] if day in by_date else None,
         "reason": by_date[day]["reason_code"] if day in by_date else None,
         "run_id": by_date[day]["run_id"] if day in by_date else None,
         "cars": by_date[day]["validated_count"] if day in by_date else None}
        for day in dates
    ]  # fmt: skip

    per_day = {str(r["snapshot_date"]): r for r in mine}
    chart_dates = sorted(per_day)
    chart = {
        "dates": chart_dates,
        "floor": [
            int(per_day[d]["floor_cents"]) / 100
            if per_day[d]["status"] in _READ and per_day[d]["floor_cents"] is not None
            else None
            for d in chart_dates
        ],
        "stock": [
            int(per_day[d]["validated_count"] or 0) if per_day[d]["status"] in _READ else None
            for d in chart_dates
        ],
    }

    units: list[Unit] = []
    if latest is not None and latest["status"] in _READ:
        units = [
            u
            for u in load_units(conn, str(latest["run_id"]), prefs)
            if u.row["dealer_id"] == dealer_id
        ]
        units.sort(
            key=lambda u: (
                u.row["comparison_price_cents"] is None,
                u.row["comparison_price_cents"] or 0,
            )
        )

    attempts: list[Row] = []
    robots: Row | None = None
    if shown:
        attempts = [
            dict(r)
            for r in conn.execute(
                select(s.fetch_attempts)
                .where(
                    s.fetch_attempts.c.run_id == shown, s.fetch_attempts.c.dealer_id == dealer_id
                )
                .order_by(s.fetch_attempts.c.fetched_at)
            ).mappings()
        ]
        rob = (
            conn.execute(
                select(s.robots_snapshots)
                .where(
                    s.robots_snapshots.c.run_id == shown,
                    s.robots_snapshots.c.dealer_id == dealer_id,
                )
                .order_by(s.robots_snapshots.c.fetched_at.desc())
            )
            .mappings()
            .first()
        )
        robots = dict(rob) if rob else None

    success = [r for r in mine if r["status"] == "REPORTED"]
    return {
        "dealer": dict(dealer),
        "path_prefixes": json.loads(dealer["path_prefixes"] or "[]"),
        "latest": latest,
        "result": BUYER_RESULT.get(str(latest["status"]), "Not checked yet")
        if latest
        else "Not checked yet",
        "last_success": success[-1]["snapshot_date"] if success else None,
        "units": units,
        "strip": strip,
        "chart": chart,
        "shown_run": shown,
        "attempts": attempts,
        "robots": robots,
    }


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
            stock[d][i] = int(r["validated_count"])
            floor[d][i] = (int(r["floor_cents"]) / 100) if r["floor_cents"] is not None else None
            floor_vin[d][i] = r["floor_vin"]
            statewide[i] += int(r["validated_count"])
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


_UNREACHABLE = frozenset(
    {
        "homepage_connect_failed",
        "homepage_tls_failed",
        "homepage_timeout",
        "homepage_unreachable",
    }
)


def locator_unreachable(rows: list[Row]) -> dict[str, Any] | None:
    """Coverage banner when a locator host has failed on consecutive refreshes (review G2)."""
    hits = [
        r
        for r in rows
        if r.get("active")
        and int(r.get("discovery_fail_streak") or 0) >= 1
        and r.get("discovery_reason") in _UNREACHABLE
    ]
    if not hits:
        return None
    n = max(int(r["discovery_fail_streak"]) for r in hits)
    return {
        "n": n,
        "names": ", ".join(str(r["name"]) for r in hits),
        "message": (
            f"locator website unreachable for {n} consecutive refreshes"
            " — operator override needed (§16.3)"
        ),
    }


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


def filter_catalog(rows: list[Row], q: dict[str, str]) -> list[Row]:
    """Filter the one-row-per-VIN catalog. Feature values are YES, NO, or absent (not shown)."""

    def want(key: str) -> str | None:
        value = q.get(key, "").strip()
        return value or None

    out = rows
    if dealer := want("dealer"):
        out = [r for r in out if r["dealer_name"] == dealer]
    if city := want("city"):
        out = [r for r in out if (r["city"] or "") == city]
    if trim := want("trim"):
        out = [r for r in out if r["trim_code"] == trim]
    if exterior := want("exterior"):
        out = [r for r in out if (r["exterior"] or "") == exterior]
    if interior := want("interior"):
        out = [r for r in out if (r["interior"] or "") == interior]
    if price := want("price"):
        out = [r for r in out if (r["advertised_cents"] is not None) == (price == "present")]
    for key, value in q.items():
        if not key.startswith("f_") or not value:
            continue
        code = key[2:]
        if value == "ABSENT":
            out = [r for r in out if code not in r["features"]]
        else:
            out = [r for r in out if r["features"].get(code) == value]
    out = list(out)
    sort = want("sort") or "dealer"
    if sort == "price":
        out.sort(key=lambda r: (r["advertised_cents"] is None, r["advertised_cents"] or 0))
    elif sort == "msrp":
        out.sort(key=lambda r: (r["msrp_cents"] is None, r["msrp_cents"] or 0))
    else:
        out.sort(key=lambda r: (r["dealer_name"], r["trim_code"], r["vin"]))
    return out


def catalog_facets(rows: list[Row]) -> dict[str, list[tuple[str, str, int]]]:
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fields = (
        ("dealer", "dealer_name"),
        ("city", "city"),
        ("trim", "trim_code"),
        ("exterior", "exterior"),
        ("interior", "interior"),
    )
    for row in rows:
        for facet, column in fields:
            value = str(row[column] or "")
            if value:
                buckets[facet][value] += 1
    return {
        facet: sorted(
            ((value, value, n) for value, n in counts.items()), key=lambda item: (-item[2], item[1])
        )
        for facet, counts in buckets.items()
    }


_BUYER_FEATURES = (
    "DAP",
    "M_SPORT",
    "MASSAGE",
    "PANORAMIC_ROOF",
    "SKY_LOUNGE",
    "EXECUTIVE",
    "CLIMATE_COMFORT",
    "AIR_SUSPENSION",
)
_EXT_NAMES = [
    re.sub(r"\s+metallic$", "", name, flags=re.I) for name in EXTERIOR_COLORS.values()
] + ["Grigio Telesto"]
_INT_COLORS = ("ivory white", "silverstone", "cognac", "coffee", "tartufo", "black")
# Buyer-facing tokens. The page must not contain pipeline words such as REPORTED.
_STATUS_WORD = {
    "REPORTED": "checked",
    "PARTIAL": "partial",
    "NO_SOURCE": "missing",
    "NOT_REPORTED": "unread",
}
_STATUS_REASON = {
    "partial": "Partly checked",
    "missing": "Website not on file yet",
    "unread": "Couldn't read the website",
}


def canon_exterior(raw: str | None) -> tuple[str, str]:
    """Drop Metallic, spell Grey, and use the official color name when it is contained."""
    if not raw:
        return "Not shown", "Other"
    cleaned = re.sub(r"\bmetallic\b", "", raw, flags=re.I)
    cleaned = cleaned.replace("Gray", "Grey").replace("gray", "grey")
    cleaned = " ".join(cleaned.split())
    name = cleaned or "Not shown"
    low = cleaned.lower()
    for known in _EXT_NAMES:
        if known.lower() in low:
            name = known
            break
    family = "Other"
    for fam in ("grey", "white", "black", "blue", "green"):
        if fam in name.lower():
            family = fam.capitalize()
            break
    return name, family


def canon_interior(raw: str | None) -> tuple[str, str | None]:
    if not raw:
        return "Not shown", None
    low = raw.lower()
    color = next((name for name in _INT_COLORS if name in low), None)
    material = "Merino leather" if "merino" in low else "Sensafin" if "sensafin" in low else None
    return (color.title() if color else " ".join(raw.split()), material)


def price_gap_reason(flags: set[str], msrp_cents: int | None) -> str:
    if "PRICE_IS_PAYMENT" in flags:
        return "Lease/finance payment only"
    if "CONDITIONAL_ONLY" in flags:
        return "Price only after conditional rebates"
    if "CALL_FOR_PRICE" in flags:
        return "Call for price"
    if msrp_cents:
        return "Only MSRP shown"
    return "No cash price shown"


def _cash_cents(comparison: int | None, basis: str | None) -> int | None:
    """A cash price is the stored vehicle-only figure. Anything else is not a price."""
    if comparison is None:
        return None
    if basis in (None, "", "vehicle_only"):
        return int(comparison)
    return None


def _locator_coords(directory: Path) -> dict[str, tuple[float, float]]:
    files = sorted(directory.glob("locator-*Z.json"))
    if not files:
        return {}
    try:
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, tuple[float, float]] = {}
    for dealer in payload.get("dealers") or []:
        lat, lon = dealer.get("latitude"), dealer.get("longitude")
        if lat is None or lon is None:
            continue
        out[str(dealer.get("center_id"))] = (float(lat), float(lon))
    return out


def buyer_payload(conn: Connection, registry_dir: Path) -> dict[str, Any]:
    """The buyer page's one payload: the latest statewide run, with later re-checks
    updating whether each dealer was read. Cars stay the statewide listings."""
    run = repos.latest_published_run(conn)
    if run is None:
        return {
            "asOf": None,
            "days": [],
            "dealers": [],
            "cars": [],
            "flow": _empty_flow(),
        }
    run_id = str(run["run_id"])
    started = str(run["started_at"])
    coords = _locator_coords(registry_dir)
    dealer_rows = repos.list_dealers(conn)
    status: dict[str, dict[str, Any]] = {}
    later = conn.execute(
        select(
            s.dealer_runs.c.dealer_id,
            s.dealer_runs.c.status,
            s.dealer_runs.c.eligible_count,
            s.dealer_runs.c.run_id,
            s.runs.c.started_at,
        )
        .join(s.runs, s.runs.c.run_id == s.dealer_runs.c.run_id)
        .where(s.runs.c.status == "PUBLISHED", s.runs.c.started_at >= started)
        .order_by(s.runs.c.started_at)
    )
    later_rows = list(later)
    counts = repos.validated_counts(conn, sorted({str(r[3]) for r in later_rows}))
    for dealer_id, dealer_status, _eligible, later_run_id, _when in later_rows:
        status[str(dealer_id)] = {
            "status": str(dealer_status),
            "cars": counts.get((str(later_run_id), str(dealer_id)), 0),
            "run_id": str(later_run_id),
        }
    dealers: list[dict[str, Any]] = []
    for row in dealer_rows:
        if not row["active"]:
            continue
        found = status.get(str(row["dealer_id"]), {"status": "NOT_REPORTED", "cars": 0})
        word = _STATUS_WORD.get(str(found["status"]), "unread")
        latlon = coords.get(str(row["center_number"] or ""))
        dealers.append(
            {
                "id": row["dealer_id"],
                "name": row["name"],
                "city": row["city"],
                "lat": latlon[0] if latlon else None,
                "lon": latlon[1] if latlon else None,
                "status": word,
                "reason": _STATUS_REASON.get(word),
                "cars": int(found["cars"]),
            }
        )
    observations = list(repos.observations_for_run(conn, run_id, validated_only=True))
    # A later single-dealer check replaces that dealer's cars only. The rest stay
    # on the statewide list, so one store's re-read cannot wipe the page.
    for dealer_id, info in status.items():
        later_run = str(info.get("run_id") or "")
        if not later_run or later_run == run_id or info.get("status") != "REPORTED":
            continue
        fresh = [
            row
            for row in repos.observations_for_run(conn, later_run, validated_only=True)
            if str(row["dealer_id"]) == dealer_id
        ]
        observations = [row for row in observations if str(row["dealer_id"]) != dealer_id]
        observations.extend(fresh)
    vins = [str(row["vin"]) for row in observations]
    first_seen: dict[str, str] = {}
    if vins:
        for vin, seen in conn.execute(
            select(s.units.c.vin, s.units.c.first_seen_at).where(s.units.c.vin.in_(vins))
        ):
            first_seen[str(vin)] = str(seen or "")[:10]
    feat_rows = repos.features_for_observations(
        conn, [int(row["observation_id"]) for row in observations]
    )
    history: dict[str, dict[str, int | None]] = defaultdict(dict)
    if vins:
        hist_rows = conn.execute(
            select(
                s.observations.c.vin,
                s.observations.c.comparison_price_cents,
                s.observations.c.price_basis,
                s.runs.c.snapshot_date,
                s.runs.c.started_at,
            )
            .join(s.runs, s.runs.c.run_id == s.observations.c.run_id)
            .where(
                s.runs.c.status == "PUBLISHED",
                s.observations.c.rejected.is_(False),
                s.observations.c.vin.in_(vins),
            )
            .order_by(s.runs.c.snapshot_date, s.runs.c.started_at)
        )
        for vin, cents, basis, day, _when in hist_rows:
            history[str(vin)][str(day)] = _cash_cents(cents, basis)
    days = [
        str(day)
        for (day,) in conn.execute(
            select(s.runs.c.snapshot_date)
            .where(s.runs.c.status == "PUBLISHED")
            .distinct()
            .order_by(s.runs.c.snapshot_date)
        )
    ]
    cars: list[dict[str, Any]] = []
    for row in observations:
        flags = set(row["flags"])
        msrp = row["msrp_cents"]
        price = _cash_cents(row["comparison_price_cents"], row.get("price_basis"))
        ext, family = canon_exterior(row["exterior_raw"])
        interior, material = canon_interior(row["interior_raw"])
        indexed = {
            str(item["feature"]): str(item["status"])
            for item in feat_rows.get(int(row["observation_id"]), [])
        }
        vin = str(row["vin"])
        series = history.get(vin, {})
        cars.append(
            {
                "vin": vin,
                "dealer": row["dealer_id"],
                "price": price,
                "msrp": int(msrp) if msrp is not None else None,
                "noPriceWhy": None if price is not None else price_gap_reason(flags, msrp),
                "ext": ext,
                "extFamily": family,
                "int": interior,
                "intMat": material,
                "inTransit": bool(row["in_transit"]),
                "url": row["detail_url"] or row["source_url"],
                "firstSeen": first_seen.get(vin) or str(row["fetched_at"])[:10],
                "feats": {
                    name: "yes" if indexed.get(name) == "YES" else "no" for name in _BUYER_FEATURES
                },
                "history": [[day, series[day]] for day in sorted(series)],
            }
        )
    snapshot = date.fromisoformat(str(run["snapshot_date"]))
    lifecycle = repos.lifecycle_for_run(conn, run_id)
    return {
        "asOf": str(run["snapshot_date"]),
        "days": days,
        "dealers": dealers,
        "cars": cars,
        "flow": {
            "checked": sum(1 for dealer in dealers if dealer["status"] == "checked"),
            "dealers": len(dealers),
            "kept": len(cars),
            "priced": sum(1 for car in cars if car["price"] is not None),
            "waiting": repos.pending_review_count(conn, run_id),
            "added": sum(1 for event in lifecycle if event["reason"] == "added"),
            "missing": sum(1 for event in lifecycle if event["to_state"] == "MISSING_UNVERIFIED"),
            "priceChanges": len(repos.price_events_for_run(conn, run_id)),
            "when": "6:00 AM Central",
            "asOfLabel": f"{snapshot.strftime('%B')} {snapshot.day}, {snapshot.year}",
        },
    }


def _empty_flow() -> dict[str, Any]:
    return {
        "checked": 0,
        "dealers": 0,
        "kept": 0,
        "priced": 0,
        "waiting": 0,
        "added": 0,
        "missing": 0,
        "priceChanges": 0,
        "when": "6:00 AM Central",
        "asOfLabel": None,
    }
