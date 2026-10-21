"""FastAPI screen. Read-only for a single operator in Phase 1 (requirements 2, 11).

Reads committed rows only. Every number links to the observation rows behind it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import Connection, Engine, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from carshop import __version__
from carshop.catalog.features import FEATURES
from carshop.config.loaders import load_preferences
from carshop.config.settings import Settings, get_settings
from carshop.domain.pricing import fmt_cents
from carshop.export.export import export_run
from carshop.storage import migrate, repos
from carshop.storage import schema as s
from carshop.storage.db import make_engine, read_conn
from carshop.util import git_sha
from carshop.web import search, shell, views

_HERE = Path(__file__).parent

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
    templates.env.globals.update(version=__version__, git_sha=git_sha(), tz=st.schedule_tz)

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
            "pending": status["pending"], "settings": st, "model_enabled": st.model_enabled, "shell": frame, **extra,
        }  # fmt: skip

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def finder(request: Request, view: str | None = None) -> Response:
        if view == "check":
            return RedirectResponse(url="/check", status_code=301)
        with conn() as c:
            payload = views.buyer_payload(c, st.registry_dir)
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
            return render(
                "vin.html",
                _ctx(request, c, run, vin=vin, history=history, latest=latest, features=feats.get(int(latest["observation_id"]), []),
                     prices=prices.get(int(latest["observation_id"]), []), lifecycle=lifecycle, price_events=pe,
                     series=[{"date": h["snapshot_date"], "dealer": h["dealer_name"], "price": (h["comparison_price_cents"] or 0) / 100 if h["comparison_price_cents"] else None, "msrp": (h["msrp_cents"] or 0) / 100 if h["msrp_cents"] else None} for h in history]),
            )  # fmt: skip

    @app.get("/shortlist", response_class=HTMLResponse)
    def shortlist(request: Request, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
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
                ),
            )

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
