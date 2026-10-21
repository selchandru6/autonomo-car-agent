"""Global search (requirements-web-ui.md section 6): pages, cars, dealers, runs.

Reads committed rows only. The index lives in memory and is rebuilt when the runs table
changes (a publish adds or finishes a run), so a keystroke never scans the tables.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, desc, func, select

from carshop.domain.codes import RunStatus
from carshop.storage import schema as s
from carshop.web import shell

MIN_CHARS = 2
VIN_LEN = 17

SYNONYMS: dict[str, tuple[str, ...]] = {
    "/": ("home", "buy", "wish list", "cars for you"),
    "/shortlist": ("ranked", "compare", "fit"),
    "/inventory": ("inventory", "listings", "vins", "eligible"),
    "/catalog": ("catalog", "workbook", "all cars"),
    "/dealers": ("stores", "dealerships"),
    "/trends": ("prices", "stock", "chart", "history"),
    "/pipeline": ("live", "graph", "events", "running"),
    "/check": ("daily", "overview", "operator", "check"),
    "/health": ("coverage", "errors", "robots", "fetch", "blocked"),
    "/runs": ("digest", "email", "runs", "notifications"),
    "/review": ("pending", "approve", "reject", "queue"),
    "/architecture": ("agents", "architecture", "layers", "rules"),
}

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}  # fmt: skip


@dataclass
class _Index:
    signature: tuple[Any, ...] = ()
    cars: list[dict[str, Any]] = field(default_factory=list)
    dealers: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)


_cache: dict[str, _Index] = {"index": _Index()}
_lock = threading.Lock()


def _signature(conn: Connection) -> tuple[Any, ...]:
    row = conn.execute(
        select(func.count(), func.max(s.runs.c.ended_at), func.max(s.runs.c.started_at))
    ).one()
    dealers = conn.execute(select(func.count()).select_from(s.dealers)).scalar()
    return (*tuple(row), dealers)


def _build(conn: Connection, signature: tuple[Any, ...]) -> _Index:
    covered = (
        select(s.dealer_runs.c.dealer_id, s.dealer_runs.c.run_id, s.dealers.c.name)
        .join(s.runs, s.runs.c.run_id == s.dealer_runs.c.run_id)
        .join(s.dealers, s.dealers.c.dealer_id == s.dealer_runs.c.dealer_id)
        .where(s.runs.c.status == RunStatus.PUBLISHED.value)
        .order_by(desc(s.runs.c.started_at))
    )
    latest_for_dealer: dict[str, str] = {}
    run_dealers: dict[str, list[str]] = {}
    for dealer_id, run_id, name in conn.execute(covered):
        latest_for_dealer.setdefault(dealer_id, run_id)
        run_dealers.setdefault(run_id, []).append(name)
    q = (
        select(
            s.observations.c.dealer_id,
            s.observations.c.vin,
            s.observations.c.stock,
            s.observations.c.run_id,
            s.observations.c.comparison_price_cents,
            s.runs.c.snapshot_date,
            s.dealers.c.name,
            s.dealers.c.city,
        )
        .join(s.runs, s.runs.c.run_id == s.observations.c.run_id)
        .join(s.dealers, s.dealers.c.dealer_id == s.observations.c.dealer_id)
        .where(s.runs.c.status == RunStatus.PUBLISHED.value)
        .where(s.observations.c.rejected.is_(False))
        .order_by(desc(s.runs.c.started_at))
    )
    cars: dict[str, dict[str, Any]] = {}
    for dealer_id, vin, stock, run_id, cents, day, name, city in conn.execute(q):
        if vin in cars:
            continue
        cars[vin] = {
            "vin": vin,
            "stock": (stock or "").upper(),
            "dealer": name,
            "city": city,
            "date": day,
            "current": run_id == latest_for_dealer.get(dealer_id),
            "cents": cents,
        }
    dealers = [
        {"id": r[0], "name": r[1], "city": r[2] or "", "active": bool(r[3])}
        for r in conn.execute(
            select(s.dealers.c.dealer_id, s.dealers.c.name, s.dealers.c.city, s.dealers.c.active)
            .order_by(s.dealers.c.name)
        )
    ]  # fmt: skip
    runs = [
        {"id": r[0], "date": r[1], "trigger": r[2], "status": r[3], "dealers": run_dealers.get(r[0], [])}
        for r in conn.execute(
            select(s.runs.c.run_id, s.runs.c.snapshot_date, s.runs.c.trigger, s.runs.c.status)
            .order_by(desc(s.runs.c.started_at))
        )
    ]  # fmt: skip
    # Statewide runs answer "the run on that day"; partial reruns come after them, newest first.
    runs.sort(key=lambda r: -len(r["dealers"]))
    return _Index(signature=signature, cars=list(cars.values()), dealers=dealers, runs=runs)


def _current(conn: Connection) -> _Index:
    sig = _signature(conn)
    with _lock:
        if _cache["index"].signature != sig:
            _cache["index"] = _build(conn, sig)
        return _cache["index"]


def _money(cents: int | None) -> str:
    return f"${cents / 100:,.0f}" if cents is not None else "no cash price"


def _run_day(q: str) -> str | None:
    """'2026-10-20' or 'oct 20' -> a matcher on snapshot dates ('MM-DD' when no year)."""
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", q):
        return q
    m = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})", q)
    if m and m.group(1) in _MONTHS:
        return f"-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    return None


def pages(q: str) -> list[dict[str, Any]]:
    out = []
    for item in shell.MENU:
        words = (item.label.lower(), *SYNONYMS.get(item.href, ()))
        if not q or any(q in w for w in words):
            out.append({"label": item.label, "sublabel": item.group, "href": item.href})
    return out


def search(conn: Connection, raw: str, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
    q = " ".join(raw.lower().split())
    result: dict[str, list[dict[str, Any]]] = {"pages": [], "cars": [], "dealers": [], "runs": []}
    if len(q) < MIN_CHARS:
        result["pages"] = pages("")[:limit] if not q else []
        return result
    idx = _current(conn)
    result["pages"] = pages(q)[:limit]

    key = re.sub(r"[\s-]", "", raw).upper()
    if len(key) == VIN_LEN:
        cars = [c for c in idx.cars if c["vin"] == key]
    else:
        by_tail = [c for c in idx.cars if 6 <= len(key) <= 8 and c["vin"].endswith(key)]
        by_stock = [c for c in idx.cars if c["stock"] and c["stock"] == key]
        cars = by_tail + [c for c in by_stock if c not in by_tail]
    cars.sort(key=lambda c: not c["current"])
    result["cars"] = [
        {
            "label": c["vin"],
            "sublabel": f"{c['dealer']} · {_money(c['cents'])}"
            + ("" if c["current"] else f" · last seen {c['date']}"),
            "href": f"/vin/{c['vin']}",
        }
        for c in cars[:limit]
    ]

    dealers = [d for d in idx.dealers if q in d["name"].lower() or q in d["city"].lower()]
    dealers.sort(key=lambda d: (not d["active"], d["name"]))
    result["dealers"] = [
        {
            "label": d["name"],
            "sublabel": d["city"] + ("" if d["active"] else " · no longer on the locator"),
            "href": f"/dealers/{d['id']}",
        }
        for d in dealers[:limit]
    ]

    day = _run_day(q)
    runs = [
        r
        for r in idx.runs
        if r["id"].lower().startswith(q) or (day is not None and day in str(r["date"]))
    ]
    result["runs"] = [
        {
            "label": f"{r['date']} · {str(r['trigger']).replace('_', ' ')}"
            + (f" · {r['dealers'][0]}" if len(r["dealers"]) == 1 else ""),
            "sublabel": f"{r['status']} · "
            + (f"{len(r['dealers'])} dealers · " if len(r["dealers"]) > 1 else "")
            + r["id"][-6:],
            "href": f"/pipeline?run_id={r['id']}",
            "alt": {"label": "Runs & digest", "href": "/runs"},
        }
        for r in runs[:limit]
    ]
    return result
