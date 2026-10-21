"""App shell: the left menu, page titles, breadcrumbs, and the sidebar run status.

One definition of the menu (requirements-web-ui.md section 3.1) feeds the template, the
status endpoint, and the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from sqlalchemy import Connection

from carshop.domain.codes import DealerStatus, RunStatus
from carshop.storage import repos


@dataclass(frozen=True)
class Item:
    group: str
    label: str
    href: str
    icon: str
    audience: str
    run_scoped: bool = False
    exports: bool = False


GROUPS: tuple[str, ...] = ("Shop", "Market", "Pipeline", "System")

MENU: tuple[Item, ...] = (
    Item("Shop", "Find a car", "/", "i-car", "buyer"),
    Item("Shop", "My shortlist", "/shortlist", "i-star", "buyer", run_scoped=True, exports=True),
    Item("Market", "Eligible cars", "/inventory", "i-list", "buyer", run_scoped=True, exports=True),
    Item("Market", "All inventory", "/catalog", "i-grid", "buyer", exports=True),
    Item("Market", "Dealers", "/dealers", "i-store", "buyer"),
    Item("Market", "Price & stock trends", "/trends", "i-trend", "buyer", run_scoped=True),
    Item("Pipeline", "Live pipeline", "/pipeline", "i-activity", "operator"),
    Item("Pipeline", "Today's check", "/check", "i-check", "operator", run_scoped=True),
    Item("Pipeline", "Data health & coverage", "/health", "i-health", "operator", run_scoped=True),
    Item("Pipeline", "Runs & digest", "/runs", "i-runs", "operator", exports=True),
    Item("Pipeline", "Review queue", "/review", "i-inbox", "operator", run_scoped=True),
    Item("System", "How it works", "/architecture", "i-layers", "both"),
)

_BY_HREF = {item.href: item for item in MENU}

# Detail pages are not in the menu. Their breadcrumb parent is the page the user came
# from when that page is in the menu, else this default.
_DETAIL_PARENT: tuple[tuple[str, str], ...] = (("/vin/", "/inventory"), ("/dealers/", "/dealers"))


def item_for(path: str) -> Item | None:
    return _BY_HREF.get(path)


def _with_run(href: str, run_id: str | None) -> str:
    return f"{href}?{urlencode({'run_id': run_id})}" if run_id else href


def menu(path: str, picked_run_id: str | None, status: dict[str, Any]) -> list[dict[str, Any]]:
    """Menu groups for the template. A picked run carries over to the other run-scoped pages."""
    out: list[dict[str, Any]] = []
    for group in GROUPS:
        entries = []
        for item in (i for i in MENU if i.group == group):
            badge: dict[str, Any] | None = None
            if item.href == "/review" and status["pending"]:
                n = status["pending"]
                badge = {"n": n, "tone": "warn", "label": f"{n} pending review"}
            elif item.href == "/health" and status["not_reported"]:
                n = status["not_reported"]
                badge = {"n": n, "tone": "bad", "label": f"{n} dealers not read in this run"}
            entries.append(
                {
                    "label": item.label,
                    "href": _with_run(item.href, picked_run_id if item.run_scoped else None),
                    "icon": item.icon,
                    "current": item.href == path,
                    "badge": badge,
                    "live": item.href == "/pipeline",
                }
            )
        out.append({"label": group, "entries": entries})
    return out


def crumb(path: str, referer: str | None) -> dict[str, str] | None:
    """Breadcrumb parent for detail pages; None on menu pages."""
    for prefix, default in _DETAIL_PARENT:
        if path.startswith(prefix):
            came_from = urlsplit(referer).path if referer else ""
            parent = _BY_HREF.get(came_from) or _BY_HREF[default]
            return {"label": parent.label, "href": parent.href}
    return None


def status(
    conn: Connection,
    shown_run: dict[str, Any] | None,
    *,
    model_enabled: bool,
    age: Any,
) -> dict[str, Any]:
    """Sidebar run card and menu badges. JSON-safe; also served by GET /api/status."""
    latest_any = repos.list_runs(conn, 1)
    current = latest_any[0] if latest_any else None
    shown = shown_run or repos.latest_published_run(conn)
    not_reported = pending = 0
    if shown:
        sid = str(shown["run_id"])
        not_reported = sum(
            1
            for d in repos.dealer_runs_for_run(conn, sid)
            if d["status"] != DealerStatus.REPORTED.value
        )
        pending = repos.pending_review_count(conn, sid)
    rows = repos.coverage(conn)
    active = [r for r in rows if r["active"]]
    run: dict[str, Any] | None = None
    if current:
        stamp = current["ended_at"] or current["started_at"]
        run = {
            "id": str(current["run_id"]),
            "date": str(current["snapshot_date"]),
            "trigger": str(current["trigger"]),
            "status": str(current["status"]),
            "as_of": age(stamp),
        }
    return {
        "run": run,
        "running": bool(current and current["status"] == RunStatus.RUNNING.value),
        "coverage": {
            "reported": sum(1 for r in active if r["last_status"] == DealerStatus.REPORTED.value),
            "active": len(active),
            "no_source": sum(1 for r in active if not r["start_url"]),
        },
        "model": "on" if model_enabled else "off, rules only",
        "pending": pending,
        "not_reported": not_reported,
    }
