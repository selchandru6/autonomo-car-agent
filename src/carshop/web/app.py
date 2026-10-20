"""FastAPI screen. Read-only for a single operator in Phase 1 (requirements 2, 11).

Reads committed rows only. Every number links to the observation rows behind it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import Connection, Engine, select, update

from carshop import __version__
from carshop.config.loaders import load_preferences
from carshop.config.settings import Settings, get_settings
from carshop.domain.pricing import fmt_cents
from carshop.export.export import export_run
from carshop.storage import migrate, repos
from carshop.storage import schema as s
from carshop.storage.db import make_engine, read_conn, write_tx
from carshop.util import git_sha, iso, utcnow
from carshop.web import views

_HERE = Path(__file__).parent


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

    def _ctx(
        request: Request, c: Connection, run: dict[str, Any] | None, **extra: Any
    ) -> dict[str, Any]:
        runs = repos.list_runs(c, 30)
        coverage = repos.coverage(c)
        pending = repos.pending_review_count(c, str(run["run_id"])) if run else 0
        return {
            "request": request, "run": run, "runs": runs, "coverage_summary": views.summarize_coverage(coverage),
            "pending": pending, "settings": st, "model_enabled": st.model_enabled, **extra,
        }  # fmt: skip

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, run_id: str | None = None) -> Response:
        with conn() as c:
            run = _latest(c, run_id)
            if run is None:
                return render("empty.html", _ctx(request, c, None, state="no_runs"))
            rid = str(run["run_id"])
            units = views.load_units(c, rid, prefs)
            drs = repos.dealer_runs_for_run(c, rid)
            pe = repos.price_events_for_run(c, rid)
            lc = repos.lifecycle_for_run(c, rid)
            trends = views.trend_series(c, 90)
            kpis = {
                "eligible": len(units),
                "priced": sum(1 for u in units if u.row["comparison_price_cents"] is not None),
                "reported": sum(1 for d in drs if d["status"] == "REPORTED"),
                "attempted": sum(1 for d in drs if d["status"] != "NO_SOURCE"),
                "registry": len(drs),
                "added": sum(1 for e in lc if e["reason"] == "added"),
                "missing": sum(1 for e in lc if e["to_state"] == "MISSING_UNVERIFIED"),
                "price_changes": len(pe),
                "dap_yes": sum(1 for u in units if u.feat("DAP") == "YES"),
                "m_sport": sum(1 for u in units if u.row["m_sport"]),
                "in_transit": sum(1 for u in units if u.row["in_transit"]),
            }
            floors = []
            for d in drs:
                du = [u for u in units if u.row["dealer_id"] == d["dealer_id"]]
                priced = [
                    int(u.row["comparison_price_cents"])
                    for u in du
                    if u.row["comparison_price_cents"] is not None
                ]
                floors.append(
                    {
                        "dealer": d,
                        "units": len(du),
                        "floor": min(priced) if priced else None,
                        "floor_vin": next(
                            (
                                u.vin
                                for u in du
                                if u.row["comparison_price_cents"]
                                == (min(priced) if priced else None)
                            ),
                            None,
                        ),
                        "as_of": max(
                            (str(u.row["fetched_at"]) for u in du), default=d["finished_at"]
                        ),
                    }
                )
            all_dealers_failed = kpis["attempted"] > 0 and kpis["reported"] == 0
            return render(
                "overview.html",
                _ctx(request, c, run, units=units, kpis=kpis, dealer_runs=drs, price_events=pe, lifecycle=lc, floors=floors,
                     band=views.band_series(units), trends=trends, all_failed=all_dealers_failed,
                     top=sorted(units, key=lambda u: (-u.fit_score, -u.confidence_score))[:5]),
            )  # fmt: skip

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
    def health(request: Request) -> Response:
        with conn() as c:
            run = repos.latest_published_run(c)
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
                    locator_snapshot=repos.latest_registry_snapshot(c),
                    grid=grid,
                    attempts=attempts,
                    exclusions=excl,
                    by_code=sorted(by_code.items(), key=lambda x: -x[1]),
                ),
            )

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request) -> Response:
        with conn() as c:
            run = repos.latest_published_run(c)
            items = repos.open_review_items(c)
            audits = [
                dict(r._mapping)
                for r in c.execute(
                    select(s.audit_events).order_by(s.audit_events.c.audit_id.desc()).limit(50)
                )
            ]
            return render("review.html", _ctx(request, c, run, items=items, audits=audits))

    @app.post("/review/{item_id}")
    def decide(
        item_id: int,
        decision: str = Form(...),
        operator_id: str = Form("operator"),
        reason: str = Form(""),
    ) -> Response:
        """Operator decision. The original extraction stays; the decision is audited (F22)."""
        if decision not in ("VALIDATED", "REJECTED"):
            raise HTTPException(400, "decision must be VALIDATED or REJECTED")
        now = iso(utcnow())
        with write_tx(eng) as c:
            item = (
                c.execute(select(s.review_items).where(s.review_items.c.item_id == item_id))
                .mappings()
                .first()
            )
            if item is None:
                raise HTTPException(404)
            c.execute(
                update(s.review_items)
                .where(s.review_items.c.item_id == item_id)
                .values(
                    status="DECIDED", decision=decision, operator_id=operator_id, decided_at=now
                )
            )
            if item["vin"]:
                c.execute(
                    update(s.inventory_staging)
                    .where(
                        s.inventory_staging.c.run_id == item["run_id"],
                        s.inventory_staging.c.dealer_id == item["dealer_id"],
                        s.inventory_staging.c.vin == item["vin"],
                    )
                    .values(status=decision, reason=reason or item["issue"])
                )
                if decision == "REJECTED":
                    c.execute(
                        update(s.observations)
                        .where(
                            s.observations.c.run_id == item["run_id"],
                            s.observations.c.dealer_id == item["dealer_id"],
                            s.observations.c.vin == item["vin"],
                        )
                        .values(rejected=True)
                    )
            c.execute(
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
        return RedirectResponse("/review", status_code=303)

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
