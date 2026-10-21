"""FastAPI screen for a single operator (requirements 2, 11).

Reads committed rows only. Every number links to the observation rows behind it. The only
writes are the owner's saved cars and saved searches (requirements-shortlist.md §9), behind
`_write_guard`.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from carshop import __version__
from carshop.catalog.features import FEATURES
from carshop.config.loaders import load_locator_coords, load_preferences
from carshop.config.settings import Settings, get_settings
from carshop.domain.pricing import fmt_cents
from carshop.export.export import export_run
from carshop.shortlist import match as sm
from carshop.shortlist import saved as sl
from carshop.shortlist import searches as ss
from carshop.storage import migrate, repos
from carshop.storage import schema as s
from carshop.storage.db import make_engine, read_conn, write_tx
from carshop.util import git_sha
from carshop.web import search, shell, views

_HERE = Path(__file__).parent
SHORTLIST_TABS = ("new", "saved", "searches", "ranked")


def is_loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class SaveCar(BaseModel):
    vin: str
    note: str | None = None


class CarNote(BaseModel):
    note: str | None = None


class SearchIn(BaseModel):
    """Criteria, or a Find a car wish list (same schema, §5.1)."""

    name: str | None = None
    criteria: dict[str, Any] | None = None
    wish: dict[str, Any] | None = None
    alerts: bool | None = None
    paused: bool | None = None

    def to_criteria(self) -> dict[str, Any] | None:
        if self.criteria is not None:
            return self.criteria
        return sm.criteria_from_wish(self.wish) if self.wish is not None else None


# Menu pages whose full build lands in a later delivery step (requirements-web-ui.md §12).
# They render inside the shell so no menu item is a dead link.
PLANNED: dict[str, dict[str, Any]] = {
    "pipeline": {
        "title": "Live pipeline",
        "step": "step 5",
        "lead": "Watch each daily run move through the agents: collect, extract, validate, enrich, store, compare, notify.",
        "bullets": [
            "Run bar with elapsed time, dealers finished, and model spend against the breaker.",
            "The agent graph, with hand-offs animated from real pipeline events only.",
            "A stage funnel, one row per dealer branch, and the event log.",
        ],
        "links": [("Runs & digest", "/runs"), ("Data health & coverage", "/health")],
    },
    "architecture": {
        "title": "How it works",
        "step": "step 5",
        "lead": "The system explained from the code itself: agents, layers, and the rules that never bend.",
        "bullets": [
            "Unknown is never No. A missing price stays empty, never guessed.",
            "A dealer we could not read is a gap, not zero. A car that disappears is not a sale.",
            "Every yes or no about a package quotes the dealer's page. No bot evasion.",
        ],
        "links": [("Today's check", "/check")],
    },
}


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    st = settings or get_settings()
    st.ensure_dirs()
    eng = engine or make_engine(st.db_path)
    if not migrate.is_current(eng):
        migrate.upgrade(eng)
    prefs = load_preferences()

    app = FastAPI(title="AI car shopping", version=__version__, docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    templates.env.filters["money"] = lambda c: fmt_cents(int(c)) if c is not None else "—"
    templates.env.filters["age"] = lambda ts: views.age_label(ts, st.schedule_tz)
    templates.env.filters["buyer_result"] = lambda code: views.BUYER_RESULT.get(
        str(code or ""), "Not checked yet"
    )
    # JSON for inline <script>: not HTML-escaped (Markup), but with the sequences that could
    # close the script tag or form HTML comments escaped, so page data can never break out.
    templates.env.filters["tojson_safe"] = lambda v: Markup(  # noqa: S704 - escaped below
        json.dumps(v, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    # Saving is a write: only on a loopback bind, same origin, with this process's token (§9).
    writes = is_loopback(st.web_host)
    csrf = secrets.token_urlsafe(32)
    templates.env.globals.update(
        version=__version__, git_sha=git_sha(), tz=st.schedule_tz,
        shortlist_writes=writes, csrf_token=csrf if writes else "",
    )  # fmt: skip

    def render(name: str, ctx: dict[str, Any]) -> Response:
        return templates.TemplateResponse(ctx["request"], name, ctx)

    @contextmanager
    def conn() -> Iterator[Connection]:
        with read_conn(eng) as c:
            yield c

    def _latest(c: Connection, run_id: str | None) -> dict[str, Any] | None:
        if run_id:
            return repos.get_run(c, run_id)
        return repos.latest_published_run(c)

    def _age(ts: str | None) -> str:
        return views.age_label(ts, st.schedule_tz)

    def _ctx(
        request: Request, c: Connection, run: dict[str, Any] | None, **extra: Any
    ) -> dict[str, Any]:
        runs = repos.list_runs(c, 30)
        coverage = repos.coverage(c)
        path = request.url.path
        status = shell.status(c, run, model_enabled=st.model_enabled, age=_age)
        item = shell.item_for(path)
        frame = {
            "menu": shell.menu(path, request.query_params.get("run_id"), status),
            "status": status,
            "item": item,
            "crumb": shell.crumb(path, request.headers.get("referer")),
            "run_scoped": bool(item and item.run_scoped),
            "exports": bool(item and item.exports),
        }
        return {
            "request": request, "run": run, "runs": runs, "coverage_summary": views.summarize_coverage(coverage),
            "pending": status["pending"], "settings": st, "model_enabled": st.model_enabled, "shell": frame,
            "saved_vins": sl.saved_vins(c), **extra,
        }  # fmt: skip

    def _write_guard(request: Request) -> None:
        if not writes:
            raise HTTPException(
                403, "Saving works only when the app runs on this computer (localhost)."
            )
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        host = request.headers.get("host", "")
        if origin is not None:
            if urlsplit(origin).netloc != host:
                raise HTTPException(403, "Cross-origin request refused.")
        elif site is None or site != "same-origin":
            raise HTTPException(403, "Cross-origin request refused.")
        token = request.headers.get("x-csrf-token", "")
        if not token or not hmac.compare_digest(token, csrf):
            raise HTTPException(403, "Missing or wrong page token. Reload the page and try again.")

    def _write(fn: Callable[[Connection], dict[str, Any]]) -> JSONResponse:
        """One short transaction through the single-writer path."""
        try:
            with write_tx(eng) as c:
                return JSONResponse(fn(c))
        except sl.ShortlistError as e:
            return JSONResponse({"error": e.message}, status_code=e.status)
        except sm.CriteriaError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                return JSONResponse({"error": sl.BUSY}, status_code=503)
            raise

    def _geo(c: Connection) -> ss.Geo:
        return ss.Geo(c, load_locator_coords(st.registry_dir))

    def _search_form(
        request: Request, c: Connection, name: str, criteria: dict[str, Any],
        search: dict[str, Any] | None = None, alerts: bool = True,
    ) -> Response:  # fmt: skip
        geo = _geo(c)
        cars = ss.candidates(c, geo)
        ext = sorted({str(x["ext_name"]) for x in cars} | set(criteria["exterior"]["names"]))
        inner = sorted({str(x["int_name"]) for x in cars} | set(criteria["interior"]["names"]))
        return render(
            "search_form.html",
            _ctx(request, c, None, search=search, name=name, criteria=criteria, alerts=alerts,
                 cities=sorted(geo.centroids), ext_names=ext, int_names=inner,
                 features=[(f, sm.SHORT[f]) for f in sm.FEATURES], radii=sm.RADII,
                 ext_families=sm.EXTERIOR_FAMILY_NAMES, int_families=sm.INTERIOR_FAMILY_NAMES),
        )  # fmt: skip

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def finder(request: Request, view: str | None = None, search: int | None = None) -> Response:
        if view == "check":
            return RedirectResponse(url="/check", status_code=301)
        with conn() as c:
            payload = views.buyer_payload(c, st.registry_dir)
            latest = repos.latest_published_run(c)
            payload["newFor"] = ss.new_tags(c, str(latest["run_id"]) if latest else None)
            opened = ss.get_search(c, search) if search is not None else None
            if opened is not None:
                payload["openSearch"] = {
                    "id": opened["search_id"], "name": opened["name"],
                    "wish": sm.wish_from_criteria(opened["criteria"]),
                    "band": opened["criteria"]["band_cents"] is not None,
                }  # fmt: skip
            return render("finder.html", _ctx(request, c, None, payload=payload))

    @app.get("/operator")
    def operator_home() -> RedirectResponse:
        """Old overview address."""
        return RedirectResponse(url="/check", status_code=301)

    @app.get("/check", response_class=HTMLResponse)
    def check(request: Request, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
            payload = views.buyer_payload(c, st.registry_dir)
            dealers: list[dict[str, Any]] = []
            price_events: list[dict[str, Any]] = []
            if run is not None:
                rid = str(run["run_id"])
                dealers = views.check_dealers(c, rid, prefs)
                price_events = repos.price_events_for_run(c, rid)
            all_failed = bool(dealers) and not any(
                d["status"] in ("REPORTED", "PARTIAL") for d in dealers
            )
            return render(
                "check.html",
                _ctx(request, c, run, payload=payload, dealers=dealers,
                     price_events=price_events, all_failed=all_failed),
            )  # fmt: skip

    def _planned(request: Request, page: str, **extra: Any) -> Response:
        with conn() as c:
            return render("planned.html", _ctx(request, c, None, page=PLANNED[page], **extra))

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline_page(request: Request) -> Response:
        return _planned(request, "pipeline")

    @app.get("/architecture", response_class=HTMLResponse)
    def architecture_page(request: Request) -> Response:
        return _planned(request, "architecture")

    @app.get("/dealers", response_class=HTMLResponse)
    def dealers_page(request: Request) -> Response:
        with conn() as c:
            directory = views.dealer_directory(c)
            return render("dealers.html", _ctx(request, c, None, directory=directory))

    @app.get("/dealers/{dealer_id}", response_class=HTMLResponse)
    def dealer_page(request: Request, dealer_id: str, run_id: str | None = None) -> Response:
        with conn() as c:
            detail = views.dealer_detail(c, dealer_id, prefs, run_id)
            if detail is None:
                raise HTTPException(404, "That dealer is not in the registry.")
            return render("dealer.html", _ctx(request, c, None, d=detail))

    @app.get("/inventory", response_class=HTMLResponse)
    def inventory(request: Request, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
            if run is None:
                return render("empty.html", _ctx(request, c, None, state="no_runs"))
            rid = str(run["run_id"])
            units = views.load_units(c, rid, prefs)
            q = dict(request.query_params)
            filtered = views.apply_filters(units, q)
            sort = q.get("sort", "price")
            keyf = {
                "price": lambda u: (
                    u.row["comparison_price_cents"] is None,
                    u.row["comparison_price_cents"] or 0,
                ),
                "fit": lambda u: (-u.fit_score, -u.confidence_score),
                "dealer": lambda u: (u.row["dealer_name"], u.row["comparison_price_cents"] or 0),
                "msrp": lambda u: (u.row["msrp_cents"] is None, u.row["msrp_cents"] or 0),
            }.get(sort, lambda _u: 0)
            filtered.sort(key=keyf)
            drs = repos.dealer_runs_for_run(c, rid)
            return render(
                "inventory.html",
                _ctx(
                    request,
                    c,
                    run,
                    units=filtered,
                    total=len(units),
                    facets=views.facets(units),
                    q=q,
                    dealer_runs=drs,
                ),
            )

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog(request: Request) -> Response:
        with conn() as c:
            rows = repos.catalog_vehicles(c)
            q = dict(request.query_params)
            filtered = views.filter_catalog(rows, q)
            moved = [r for r in rows if r["prior_dealer"]]
            counts: dict[str, dict[str, int]] = {f.code: {"YES": 0, "NO": 0} for f in FEATURES}
            for row in rows:
                for code, status in row["features"].items():
                    if code in counts and status in counts[code]:
                        counts[code][status] += 1
            return render(
                "catalog.html",
                _ctx(
                    request,
                    c,
                    None,
                    units=filtered,
                    total=len(rows),
                    facets=views.catalog_facets(rows),
                    features=FEATURES,
                    feature_counts=counts,
                    q=q,
                    moved=moved,
                    savable=sl.savable_vins(c, [str(u["vin"]) for u in filtered]),
                ),
            )

    @app.get("/vin/{vin}", response_class=HTMLResponse)
    def vin_page(request: Request, vin: str) -> Response:
        with conn() as c:
            history = repos.observation_history(c, vin)
            if not history:
                raise HTTPException(404, "VIN not observed")
            ids = [int(h["observation_id"]) for h in history]
            feats = repos.features_for_observations(c, ids)
            prices = repos.prices_for_observations(c, ids)
            latest = history[-1]
            run = repos.get_run(c, str(latest["run_id"]))
            lifecycle = [
                dict(r._mapping)
                for r in c.execute(
                    select(s.listing_lifecycle)
                    .where(s.listing_lifecycle.c.vin == vin)
                    .order_by(s.listing_lifecycle.c.lifecycle_event_id)
                )
            ]
            pe = [
                dict(r._mapping)
                for r in c.execute(
                    select(s.price_events)
                    .where(s.price_events.c.vin == vin)
                    .order_by(s.price_events.c.price_event_id)
                )
            ]
            saved = sl.get_saved(c, vin)
            tracked = sl.track(c, saved) if saved else None
            return render(
                "vin.html",
                _ctx(request, c, run, vin=vin, history=history, saved=saved, tracked=tracked,
                     matching=ss.matches_for_vin(c, vin),
                     savable=sl.latest_validated(c, vin) is not None, latest=latest, features=feats.get(int(latest["observation_id"]), []),
                     prices=prices.get(int(latest["observation_id"]), []), lifecycle=lifecycle, price_events=pe,
                     series=[{"date": h["snapshot_date"], "dealer": h["dealer_name"], "price": (h["comparison_price_cents"] or 0) / 100 if h["comparison_price_cents"] else None, "msrp": (h["msrp_cents"] or 0) / 100 if h["msrp_cents"] else None} for h in history]),
            )  # fmt: skip

    @app.get("/shortlist", response_class=HTMLResponse)
    def shortlist(request: Request, run_id: str | None = None, tab: str | None = None) -> Response:
        with conn() as c:
            tracked = sl.track_all(c)
            searches = ss.overview(c)
            run = _latest(c, run_id)
            events = ss.new_events(c, str(run["run_id"]) if run else None)
            new_n = sum(len(g["rows"]) for g in events)
            if tab not in SHORTLIST_TABS:
                tab = "new" if events else "saved" if tracked else "searches" if searches else "ranked"
            if tab in ("new", "saved", "searches"):
                return render(
                    "shortlist.html",
                    _ctx(request, c, run, tab=tab, tracked=tracked, max_saved=sl.MAX_SAVED_CARS,
                         searches=searches, max_searches=ss.MAX_SEARCHES, new_events=events, new_n=new_n),
                )  # fmt: skip
            if run is None:
                return render("empty.html", _ctx(request, c, None, state="no_runs"))
            units = views.load_units(c, str(run["run_id"]), prefs)
            q = dict(request.query_params)
            units = views.apply_filters(units, q)
            units.sort(
                key=lambda u: (
                    -u.fit_score,
                    -u.confidence_score,
                    u.row["comparison_price_cents"] or 10**12,
                )
            )
            compare_vins = [v for v in q.get("compare", "").split(",") if v]
            compare = [u for u in units if u.vin in compare_vins][:4]
            return render(
                "shortlist.html",
                _ctx(
                    request,
                    c,
                    run,
                    units=units,
                    prefs=prefs,
                    q=q,
                    compare=compare,
                    compare_vins=compare_vins,
                    tab=tab,
                    tracked=tracked,
                    searches=searches,
                    new_events=events,
                    new_n=new_n,
                    max_saved=sl.MAX_SAVED_CARS,
                    max_searches=ss.MAX_SEARCHES,
                ),
            )

    @app.get("/shortlist/searches/new", response_class=HTMLResponse)
    def search_new(request: Request, base: str | None = None) -> Response:
        with conn() as c:
            if not base:
                return _search_form(request, c, "", sm.default_criteria())
            vin = base.strip().upper()
            saved = sl.get_saved(c, vin)
            obs = sl.latest_validated(c, vin)
            if obs is None:
                raise HTTPException(404, "That car has not been read in a daily check yet.")
            snap = saved["snapshot"] if saved else sl.build_snapshot(c, obs)
            name, criteria = sm.prefill_from_snapshot(snap, obs.get("city"))
            return _search_form(request, c, name, criteria)

    @app.get("/shortlist/searches/{search_id}/edit", response_class=HTMLResponse)
    def search_edit(request: Request, search_id: int) -> Response:
        with conn() as c:
            found = ss.get_search(c, search_id)
            if found is None:
                raise HTTPException(404, "That search is not on your shortlist.")
            return _search_form(
                request,
                c,
                found["name"],
                found["criteria"],
                search=found,
                alerts=bool(found["alerts"]),
            )

    @app.get("/shortlist/searches/{search_id}", response_class=HTMLResponse)
    def search_page_detail(request: Request, search_id: int) -> Response:
        with conn() as c:
            found = ss.get_search(c, search_id)
            if found is None:
                raise HTTPException(404, "That search is not on your shortlist.")
            evaluated, rows = ss.current_matches(c, search_id)
            return render(
                "search_detail.html",
                _ctx(request, c, None, search=found, summary=sm.summary(found["criteria"]),
                     evaluated_at=evaluated, matches=rows),
            )  # fmt: skip

    @app.get("/trends", response_class=HTMLResponse)
    def trends(request: Request, days: int = 90, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
            if run is None:
                return render("empty.html", _ctx(request, c, None, state="no_runs"))
            rid = str(run["run_id"])
            units = views.load_units(c, rid, prefs)
            pe = repos.price_events_for_run(c, rid)
            # Floor-VIN change annotations: compare consecutive REPORTED runs per dealer.
            t = views.trend_series(c, days)
            notes = []
            for d in t["dealers"]:
                prev = None
                for i, v in enumerate(t["floor_vin"][d]):
                    if v and prev and v != prev:
                        notes.append(
                            {
                                "dealer": d,
                                "date": t["dates"][i],
                                "from": prev,
                                "to": v,
                                "label": "floor VIN changed (turnover, not a markdown)",
                            }
                        )
                    if v:
                        prev = v
            return render(
                "trends.html",
                _ctx(
                    request,
                    c,
                    run,
                    band=views.band_series(units),
                    trends=t,
                    price_events=pe,
                    notes=notes,
                    days=days,
                ),
            )

    @app.get("/health", response_class=HTMLResponse)
    def health(request: Request, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
            cov = repos.coverage(c)
            grid = views.health_grid(c, 30)
            attempts = []
            excl: list[dict[str, Any]] = []
            if run:
                rid = str(run["run_id"])
                attempts = [
                    dict(r._mapping)
                    for r in c.execute(
                        select(s.fetch_attempts)
                        .where(s.fetch_attempts.c.run_id == rid)
                        .order_by(s.fetch_attempts.c.fetch_attempt_id)
                    )
                ]
                excl = repos.exclusions_for_run(c, rid)
            by_code: dict[str, int] = {}
            for e in excl:
                by_code[str(e["reason_code"])] = by_code.get(str(e["reason_code"]), 0) + 1
            return render(
                "health.html",
                _ctx(
                    request,
                    c,
                    run,
                    coverage=cov,
                    unreachable=views.locator_unreachable(cov),
                    locator_snapshot=repos.latest_registry_snapshot(c),
                    grid=grid,
                    attempts=attempts,
                    exclusions=excl,
                    by_code=sorted(by_code.items(), key=lambda x: -x[1]),
                ),
            )

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
            items = repos.open_review_items(c)
            audits = [
                dict(r._mapping)
                for r in c.execute(
                    select(s.audit_events).order_by(s.audit_events.c.audit_id.desc()).limit(50)
                )
            ]
            return render("review.html", _ctx(request, c, run, items=items, audits=audits))

    @app.post("/review/{item_id}")
    def decide(item_id: int) -> Response:
        """Phase 1 screen is read-only. Decisions go through `carshop review` (F22)."""
        raise HTTPException(
            403,
            f"review item {item_id} is not decided here; use `carshop review approve|reject|correct`",
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> Response:
        from carshop.pipeline.notify import build_digest

        with conn() as c:
            run = repos.latest_published_run(c)
            all_runs = repos.list_runs(c, 100)
            digest = build_digest(c, str(run["run_id"])) if run else None
            notes = [
                dict(r._mapping)
                for r in c.execute(
                    select(s.notification_events)
                    .order_by(s.notification_events.c.notification_id.desc())
                    .limit(30)
                )
            ]
            return render(
                "runs.html",
                _ctx(request, c, run, all_runs=all_runs, digest=digest, notifications=notes),
            )

    # ------------------------------------------------------------------ exports and API
    @app.get("/export.{kind}")
    def export(kind: str, run_id: str | None = None) -> Response:
        if kind not in ("csv", "xlsx"):
            raise HTTPException(404)
        with conn() as c:
            run = _latest(c, run_id)
            if run is None:
                raise HTTPException(404, "no published run")
            data = export_run(c, str(run["run_id"]), kind)
        media = (
            "text/csv"
            if kind == "csv"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        return Response(
            content=data,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="x5-xdrive40i-{run["snapshot_date"]}.{kind}"'
            },
        )

    @app.get("/api/trends")
    def api_trends(days: int = 90) -> JSONResponse:
        with conn() as c:
            return JSONResponse(views.trend_series(c, days))

    @app.get("/api/search")
    def api_search(q: str = "", limit: int = 6) -> JSONResponse:
        with conn() as c:
            return JSONResponse(search.search(c, q, max(1, min(limit, 50))))

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, q: str = "") -> Response:
        with conn() as c:
            results = search.search(c, q, 50)
            return render("search.html", _ctx(request, c, None, q=q, results=results))

    # ------------------------------------------------------------------ the owner's saved cars
    @app.post("/api/shortlist/cars", dependencies=[Depends(_write_guard)])
    def api_save_car(body: SaveCar) -> JSONResponse:
        def go(c: Connection) -> dict[str, Any]:
            row, created = sl.save_car(c, body.vin, note=body.note)
            return {
                "vin": row["vin"],
                "saved": True,
                "created": created,
                "count": len(sl.saved_vins(c)),
            }

        return _write(go)

    @app.patch("/api/shortlist/cars/{vin}", dependencies=[Depends(_write_guard)])
    def api_note_car(vin: str, body: CarNote) -> JSONResponse:
        def go(c: Connection) -> dict[str, Any]:
            row = sl.set_note(c, vin, body.note)
            return {"vin": row["vin"], "saved": True, "note": row["note"]}

        return _write(go)

    @app.delete("/api/shortlist/cars/{vin}", dependencies=[Depends(_write_guard)])
    def api_remove_car(vin: str) -> JSONResponse:
        def go(c: Connection) -> dict[str, Any]:
            removed = sl.remove_car(c, vin)
            return {
                "vin": vin.strip().upper(),
                "saved": False,
                "removed": removed,
                "count": len(sl.saved_vins(c)),
            }

        return _write(go)

    # ------------------------------------------------------------------ the owner's saved searches
    @app.post("/api/shortlist/searches/preview", dependencies=[Depends(_write_guard)])
    def api_search_preview(body: SearchIn) -> JSONResponse:
        """Dry run with the same matcher the daily run uses. Writes nothing."""
        try:
            criteria = body.to_criteria() or sm.default_criteria()
            with conn() as c:
                return JSONResponse(ss.preview(c, criteria, _geo(c)))
        except sm.CriteriaError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/shortlist/searches", dependencies=[Depends(_write_guard)])
    def api_create_search(body: SearchIn) -> JSONResponse:
        def go(c: Connection) -> dict[str, Any]:
            criteria = body.to_criteria() or sm.default_criteria()
            now = datetime.now(ZoneInfo(st.schedule_tz))
            today = f"{now.strftime('%b')} {now.day}"
            name = body.name or (ss.wish_search_name(today) if body.wish is not None else "")
            found, counts = ss.create_search(
                c, name, criteria, _geo(c), alerts=True if body.alerts is None else body.alerts
            )
            sid = int(found["search_id"])
            return {
                "search_id": sid,
                "name": found["name"],
                "url": f"/shortlist/searches/{sid}",
                **counts,
            }

        return _write(go)

    @app.patch("/api/shortlist/searches/{search_id}", dependencies=[Depends(_write_guard)])
    def api_update_search(search_id: int, body: SearchIn) -> JSONResponse:
        def go(c: Connection) -> dict[str, Any]:
            found, counts = ss.update_search(
                c, search_id, _geo(c), name=body.name, criteria=body.to_criteria(),
                paused=body.paused, alerts=body.alerts,
            )  # fmt: skip
            return {
                "search_id": search_id, "name": found["name"], "paused": bool(found["paused"]),
                "alerts": bool(found["alerts"]), "url": f"/shortlist/searches/{search_id}",
                "rebaselined": counts is not None, **(counts or {}),
            }  # fmt: skip

        return _write(go)

    @app.delete("/api/shortlist/searches/{search_id}", dependencies=[Depends(_write_guard)])
    def api_remove_search(search_id: int) -> JSONResponse:
        def go(c: Connection) -> dict[str, Any]:
            if not ss.remove_search(c, search_id):
                raise sl.ShortlistError(404, "That search is not on your shortlist.")
            return {"search_id": search_id, "removed": True}

        return _write(go)

    @app.get("/api/status")
    def api_status() -> JSONResponse:
        """Sidebar run card. Polled every 30 s, every 5 s while a run is RUNNING."""
        with conn() as c:
            return JSONResponse(shell.status(c, None, model_enabled=st.model_enabled, age=_age))

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        path = request.url.path
        if exc.status_code != 404 or path.startswith(("/api", "/static", "/export")):
            return await http_exception_handler(request, exc)
        with conn() as c:
            ctx = _ctx(request, c, None, detail=exc.detail)
        return templates.TemplateResponse(request, "not_found.html", ctx, status_code=404)

    @app.get("/api/health")
    def api_health() -> JSONResponse:
        with conn() as c:
            run = repos.latest_published_run(c)
            return JSONResponse(
                {
                    "ok": True,
                    "version": __version__,
                    "git_sha": git_sha(),
                    "latest_run": run and {k: str(v) for k, v in run.items()},
                }
            )

    return app
