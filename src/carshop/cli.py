"""Operator CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from sqlalchemy import Engine

from carshop.config.loaders import load_registry
from carshop.config.settings import Settings, get_settings
from carshop.domain.codes import Trigger
from carshop.observability.logging import configure as configure_logging
from carshop.observability.tracing import configure as configure_tracing
from carshop.storage import migrate, repos
from carshop.storage.backup import backup as do_backup
from carshop.storage.backup import verify_restore
from carshop.storage.db import make_engine, read_conn
from carshop.storage.retention import prune_backups, prune_snapshots

app = typer.Typer(
    help="AI car shopping monitor: new 2026 BMW X5 xDrive40i at approved Texas BMW dealers.",
    no_args_is_help=True,
)
console = Console()


def _engine() -> tuple[Settings, Engine]:
    st = get_settings()
    st.ensure_dirs()
    configure_logging(st.log_level, json_output=st.env == "prod")
    configure_tracing(st.otel_exporter_otlp_endpoint)
    eng = make_engine(st.db_path)
    return st, eng


@app.command()
def migrate_db() -> None:
    """Apply database migrations (schema_migrations table)."""
    _, eng = _engine()
    migrate.upgrade(eng)
    rprint(f"[green]schema at[/green] {migrate.current_revision(eng)}")


registry_app = typer.Typer(
    help="Dealer registry: BMW USA locator snapshot (Texas) + inventory-URL discovery (F1, F25).",
    no_args_is_help=True,
)
app.add_typer(registry_app, name="registry")

review_app = typer.Typer(
    help="Review queue. The web screen is read-only (F22).", no_args_is_help=True
)
app.add_typer(review_app, name="review")

shortlist_app = typer.Typer(
    help="Your saved cars (requirements-shortlist.md §9: CLI parity).", no_args_is_help=True
)
app.add_typer(shortlist_app, name="shortlist")


@shortlist_app.command("list")
def shortlist_list() -> None:
    """Saved cars with status, price now, and change since saved."""
    from carshop.shortlist.saved import track_all

    _, eng = _engine()
    with read_conn(eng) as conn:
        rows = track_all(conn)
    if not rows:
        rprint("No saved cars yet.")
        return
    table = Table(
        "vin", "dealer", "status", "price now", "since saved", "lowest", "last read", "note"
    )
    for t in rows:
        last = (
            f"dealer not read on {t['not_read_on']}"
            if t["not_read_on"]
            else str(t["last_read"] or "—")
        )
        lowest = t["lowest_cents"]
        table.add_row(
            str(t["vin"]), str(t["dealer_name"]), str(t["status"]), str(t["price_now"] or "—"),
            str(t["change"]["label"]), f"${lowest / 100:,.0f}" if lowest is not None else "—",
            last, str(t["note"] or ""),
        )  # fmt: skip
    console.print(table)


@shortlist_app.command("add")
def shortlist_add(vin: str, note: str | None = typer.Option(None, "--note")) -> None:
    """Save a car from its latest committed observation. Never fetches."""
    from carshop.shortlist.saved import ShortlistError, save_car
    from carshop.storage.db import write_tx

    _, eng = _engine()
    try:
        with write_tx(eng) as conn:
            row, created = save_car(conn, vin, note=note)
    except ShortlistError as e:
        rprint(f"[red]{e.message}[/red]")
        raise typer.Exit(1) from e
    rprint(f"[green]{'saved' if created else 'already saved'}[/green] {row['vin']}")


@shortlist_app.command("remove")
def shortlist_remove(vin: str) -> None:
    """Remove a car from the shortlist (soft delete)."""
    from carshop.shortlist.saved import ShortlistError, remove_car
    from carshop.storage.db import write_tx

    _, eng = _engine()
    try:
        with write_tx(eng) as conn:
            removed = remove_car(conn, vin)
    except ShortlistError as e:
        rprint(f"[red]{e.message}[/red]")
        raise typer.Exit(1) from e
    rprint(f"[green]removed[/green] {vin.upper()}" if removed else f"{vin.upper()} was not saved")


search_app = typer.Typer(
    help="Your saved searches (requirements-shortlist.md §9: CLI parity).", no_args_is_help=True
)
app.add_typer(search_app, name="search")


@search_app.command("list")
def search_list() -> None:
    """Saved searches with their current Match and Possible match counts."""
    from carshop.shortlist.searches import overview

    _, eng = _engine()
    with read_conn(eng) as conn:
        rows = overview(conn)
    if not rows:
        rprint("No saved searches yet.")
        return
    table = Table("id", "name", "state", "match", "possible", "new today", "checked")
    for r in rows:
        state = ("paused" if r["paused"] else "active") + ("" if r["alerts"] else ", no alerts")
        table.add_row(
            str(r["search_id"]), str(r["name"]), state, str(r["match"]), str(r["possible"]),
            str(r["new_today"]), str(r["evaluated_at"] or "—"),
        )  # fmt: skip
    console.print(table)


@search_app.command("show")
def search_show(search_id: int) -> None:
    """One search: its criteria in plain words and every current match with its reasons."""
    from carshop.shortlist.match import summary
    from carshop.shortlist.searches import current_matches, get_search

    _, eng = _engine()
    with read_conn(eng) as conn:
        found = get_search(conn, search_id)
        if found is None:
            rprint(f"[red]No saved search {search_id}.[/red]")
            raise typer.Exit(1)
        evaluated, rows = current_matches(conn, search_id)
    rprint(f"[bold]{found['name']}[/bold]" + (" (paused)" if found["paused"] else ""))
    rprint("  " + " · ".join(summary(found["criteria"])))
    rprint(f"  checked {evaluated or 'not yet'}")
    table = Table("tier", "score", "vin", "dealer", "price", "reasons")
    for r in rows:
        price = f"${r['price'] / 100:,.0f}" if r["price"] is not None else "—"
        table.add_row(
            "Match" if r["tier"] == "MATCH" else "Possible", str(r["score"]), str(r["vin"]),
            str(r["dealer_name"]), price, ", ".join(x["t"] for x in r["reasons"]),
        )  # fmt: skip
    console.print(table)


def _set_paused(search_id: int, paused: bool) -> None:
    from carshop.shortlist.saved import ShortlistError
    from carshop.shortlist.searches import update_search
    from carshop.storage.db import write_tx

    _, eng = _engine()
    try:
        with write_tx(eng) as conn:
            update_search(conn, search_id, None, paused=paused)
    except ShortlistError as e:
        rprint(f"[red]{e.message}[/red]")
        raise typer.Exit(1) from e
    rprint(f"[green]{'paused' if paused else 'resumed'}[/green] search {search_id}")


@search_app.command("pause")
def search_pause(search_id: int) -> None:
    """Stop checking this search after daily runs (its matches stay)."""
    _set_paused(search_id, True)


@search_app.command("resume")
def search_resume(search_id: int) -> None:
    """Check this search again after daily runs."""
    _set_paused(search_id, False)


@app.command()
def match(
    run_id: str | None = typer.Option(None, "--run-id", help="Defaults to the latest published run"),
) -> None:
    """Recompute saved-search matches for a published run (requirements-shortlist.md §6.2)."""
    from carshop.config.loaders import load_locator_coords
    from carshop.shortlist.searches import Geo, evaluate_run

    st, eng = _engine()
    with read_conn(eng) as conn:
        rid = run_id or (repos.latest_published_run(conn) or {}).get("run_id")
        if not rid:
            rprint("[red]no published run[/red]")
            raise typer.Exit(1)
    from carshop.storage.db import write_tx

    with write_tx(eng) as conn:
        counts = evaluate_run(conn, str(rid), Geo(conn, load_locator_coords(st.registry_dir)))
    rprint(
        f"[green]matched[/green] run {rid}: {counts['searches']} searches, "
        f"{counts['match']} match, {counts['possible']} possible, {counts['new']} new"
    )


def _operator(explicit: str | None) -> str:
    import getpass

    if explicit:
        return explicit
    st = get_settings()
    return st.operator_id or getpass.getuser()


@review_app.command("list")
def review_list() -> None:
    """Open review items."""
    _, eng = _engine()
    with read_conn(eng) as conn:
        items = repos.open_review_items(conn)
    if not items:
        rprint("No open review items.")
        return
    table = Table("id", "dealer", "vin", "issue")
    for item in items:
        table.add_row(
            str(item["item_id"]), str(item["dealer_id"]), str(item["vin"] or ""), str(item["issue"])
        )
    console.print(table)


@review_app.command("approve")
def review_approve(
    item_id: int,
    reason: str = typer.Option("", "--reason"),
    operator: str | None = typer.Option(None, "--operator"),
) -> None:
    from carshop.review.decisions import approve
    from carshop.storage.db import write_tx

    _, eng = _engine()
    with write_tx(eng) as conn:
        approve(conn, item_id, operator_id=_operator(operator), reason=reason)
    rprint(f"[green]validated[/green] item {item_id}")


@review_app.command("reject")
def review_reject(
    item_id: int,
    reason: str = typer.Option("", "--reason"),
    operator: str | None = typer.Option(None, "--operator"),
) -> None:
    from carshop.review.decisions import reject
    from carshop.storage.db import write_tx

    _, eng = _engine()
    with write_tx(eng) as conn:
        reject(conn, item_id, operator_id=_operator(operator), reason=reason)
    rprint(f"[yellow]rejected[/yellow] item {item_id}")


@review_app.command("correct")
def review_correct(
    run_id: str,
    dealer_id: str,
    vin: str,
    field: str,
    value: str,
    reason: str = typer.Option(..., "--reason"),
    evidence: str | None = typer.Option(None, "--evidence"),
    operator: str | None = typer.Option(None, "--operator"),
) -> None:
    """Store a human correction as a new observation version. Version 1 is not edited."""
    from carshop.review.decisions import correct
    from carshop.storage.db import write_tx

    _, eng = _engine()
    with write_tx(eng) as conn:
        new_id = correct(
            conn,
            run_id=run_id,
            dealer_id=dealer_id,
            vin=vin,
            field=field,
            new_value=value,
            operator_id=_operator(operator),
            reason=reason,
            evidence=evidence,
        )
    rprint(f"[green]correction stored[/green] observation {new_id}")


@registry_app.command("refresh")
def registry_refresh(
    only_missing: bool = typer.Option(
        False, help="Re-discover only dealers that have no start URL; keep verified ones"
    ),
    operator: str = typer.Option("cli", help="Operator id recorded in audit_events"),
) -> None:
    """Fetch the locator, discover each dealer's X5 inventory URL, write the registry, apply it."""
    import asyncio

    from carshop.registry.build import refresh_registry

    st, eng = _engine()
    if not migrate.is_current(eng):
        migrate.upgrade(eng)
    res = asyncio.run(refresh_registry(st, eng, only_missing=only_missing, operator_id=operator))
    reg = res.registry
    rprint(f"[green]registry {reg.version}[/green] -> {res.path}")
    rprint(f"  {len(reg.active())} active dealers, {len(reg.dealers) - len(reg.active())} inactive")
    if res.added:
        rprint(f"  added: {res.added}")
    if res.deactivated:
        rprint(f"  left the locator (now inactive): {res.deactivated}")
    if res.no_source:
        rprint("[yellow]  NO_SOURCE (no discoverable inventory URL):[/yellow]")
        for d, r in sorted(res.no_source.items()):
            rprint(f"    {d}: {r}")
    _print_registry(reg)


@registry_app.command("show")
def registry_show() -> None:
    """Print the generated registry."""
    st, _ = _engine()
    _print_registry(load_registry(st.registry_path))


def _print_registry(reg: object) -> None:
    from carshop.config.loaders import Registry

    assert isinstance(reg, Registry)
    t = Table(title=f"Registry {reg.version}  ({len(reg.dealers)} dealers)")
    for col in (
        "id",
        "center",
        "name",
        "city",
        "host",
        "platform",
        "start URL",
        "reason",
        "active",
    ):
        t.add_column(col)
    for d in reg.dealers:
        t.add_row(
            d.dealer_id, d.center_number or "—", d.name, d.city, d.hostname or "—", d.platform.value,
            (d.start_url or "—")[:70], d.discovery_reason or "—", "yes" if d.active else "no",
        )  # fmt: skip
    console.print(t)


@app.command()
def run(
    trigger: str = typer.Option(
        "manual_date", help="cron | manual_date | manual_dealer | catchup | replay"
    ),
    dealer: list[str] = typer.Option(None, "--dealer", help="Restrict to dealer id(s)"),
    date: str | None = typer.Option(None, help="Snapshot date YYYY-MM-DD (America/Chicago)"),
    notify: bool = typer.Option(False, help="Send/write the digest after publishing"),
    rerun_of: str | None = typer.Option(None, help="run_id this run re-does"),
    no_browser: bool = typer.Option(False, help="HTTP only; never render in a browser"),
    resume: str | None = typer.Option(
        None, help="Continue an interrupted RUNNING run from its checkpoint (run_id)"
    ),
) -> None:
    """Execute one collection run end to end and publish it."""
    import asyncio

    from carshop.pipeline.run import RunRejected, resume_run, run_sync

    st, eng = _engine()
    if not migrate.is_current(eng):
        migrate.upgrade(eng)
    trig = Trigger(trigger if not dealer else "manual_dealer")
    try:
        if resume:
            rep = asyncio.run(resume_run(eng, st, resume))
        else:
            rep = run_sync(
                eng,
                st,
                trigger=trig,
                dealer_ids=dealer or None,
                snapshot_date=date,
                notify=notify,
                rerun_of=rerun_of,
                use_browser=not no_browser,
            )
    except RunRejected as e:
        # A 07:00 catch-up that finds the 06:00 run already published is a no-op, not a failure.
        if trig is Trigger.CATCHUP and "already exists" in str(e):
            rprint(f"[yellow]catch-up skipped:[/yellow] {e}")
            raise typer.Exit(0) from e
        rprint(f"[red]rejected:[/red] {e}")
        raise typer.Exit(2) from e
    t = Table(
        title=f"run {rep.run_id}  ({rep.snapshot_date})  status={rep.status.value}  model={rep.model_status}"
    )
    for col in ("dealer", "status", "reason", "eligible", "added", "missing", "price Δ"):
        t.add_column(col)
    for d, status in rep.dealers.items():
        c = rep.compare.get(d)
        reason = rep.reasons.get(d)
        t.add_row(
            d,
            status.value,
            reason.value if reason is not None else "—",
            str(rep.eligible[d]),
            str(c.added if c else 0),
            str(c.missing if c else 0),
            str(c.price_changes if c else 0),
        )
    console.print(t)
    if rep.transfer_suspected:
        rprint(f"[yellow]TRANSFER_SUSPECTED:[/yellow] {rep.transfer_suspected}")
    if rep.notifications:
        rprint(f"notifications: {rep.notifications}")
    if rep.backup_path:
        rprint(f"backup verified: {rep.backup_path}")


@app.command()
def digest(
    run_id: str | None = typer.Option(None, help="Defaults to the latest published run"),
) -> None:
    """Render (and deliver, idempotently) the digest for a run."""
    from carshop.pipeline.notify import build_digest, deliver_digest

    st, eng = _engine()
    with read_conn(eng) as conn:
        rid = run_id or (repos.latest_published_run(conn) or {}).get("run_id")
        if not rid:
            rprint("[red]no published run[/red]")
            raise typer.Exit(1)
        d = build_digest(conn, str(rid))
    rprint(d.subject)
    rprint(d.body)
    rprint(deliver_digest(eng, st, str(rid)))


@app.command()
def coverage() -> None:
    """Coverage report: every registry dealer with last success, status and reason (F11)."""
    _, eng = _engine()
    with read_conn(eng) as conn:
        rows = repos.coverage(conn)
    t = Table(title="Coverage")
    for col in (
        "dealer",
        "city",
        "platform",
        "discovery",
        "last status",
        "reason",
        "last success",
        "last attempt",
    ):
        t.add_column(col)
    active_rows = [r for r in rows if r["active"]]
    reported = sum(1 for r in active_rows if r["last_status"] == "REPORTED")
    for r in rows:
        t.add_row(
            r["name"] + ("" if r["active"] else " (inactive)"),
            r["city"],
            r["platform"],
            r.get("discovery_reason") or "—",
            r["last_status"] or "—",
            r["last_reason"] or "—",
            r["last_success_date"] or "—",
            (r["last_attempt_at"] or "—")[:19],
        )
    console.print(t)
    # The denominator is every active Texas registry dealer (v1.3 §4 Coverage claim).
    rprint(f"REPORTED {reported} / {len(active_rows)} active registry dealers ({len(rows)} rows)")


@app.command()
def backup() -> None:
    """VACUUM INTO a timestamped backup and verify it opens and reads."""
    st, _ = _engine()
    p = do_backup(st.db_path, st.backups_dir)
    rprint({"backup": str(p), "verified": verify_restore(p)})


@app.command()
def prune() -> None:
    """Enforce snapshot retention and backup count."""
    st, _ = _engine()
    rprint(
        {
            "snapshots_removed": prune_snapshots(st.snapshots_dir, st.snapshot_retention_days),
            "backups_removed": prune_backups(st.backups_dir),
        }
    )


@app.command("import-workbooks")
def import_workbooks_cmd(
    paths: list[Path] = typer.Argument(..., help="October X5 workbooks (.xlsx)"),
) -> None:
    """Load every VIN from the workbooks. One row per VIN; a later sheet replaces the dealer."""
    from carshop.catalog.workbook import import_workbooks
    from carshop.util import iso, utcnow

    _, eng = _engine()
    if not migrate.is_current(eng):
        migrate.upgrade(eng)
    vehicles = import_workbooks(paths)
    from carshop.storage.db import write_tx

    with write_tx(eng) as c:
        n = repos.replace_catalog(c, vehicles, iso(utcnow()))
    moved = [v for v in vehicles if v.prior_dealer]
    rprint(f"[green]{n} vehicles[/green], one row per VIN")
    if moved:
        rprint("[yellow]VIN changed dealer between snapshots (latest dealer kept):[/yellow]")
        for v in moved:
            rprint(f"  {v.vin}  {v.prior_dealer} -> {v.dealer_name}  as of {v.as_of}")


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Start the web screen."""
    import os

    import uvicorn

    st, eng = _engine()
    if not migrate.is_current(eng):
        migrate.upgrade(eng)
    if host:
        # The app turns saving off unless it is bound to loopback, so it must see the real bind.
        os.environ["CARSHOP_WEB_HOST"] = host
        st.web_host = host
    uvicorn.run(
        "carshop.web.app:create_app",
        factory=True,
        host=host or st.web_host,
        port=port or st.web_port,
        reload=reload,
        log_level=st.log_level.lower(),
    )


@app.command()
def export(
    kind: str = typer.Argument("csv", help="csv | xlsx"),
    out: Path = typer.Option(Path("export"), help="Output path without extension"),
) -> None:
    """Export the latest published run's eligible inventory (F23)."""
    from carshop.export.export import export_run

    _, eng = _engine()
    with read_conn(eng) as conn:
        latest = repos.latest_published_run(conn)
        if not latest:
            rprint("[red]no published run[/red]")
            raise typer.Exit(1)
        data = export_run(conn, str(latest["run_id"]), kind)
    path = out.with_suffix(f".{kind}")
    path.write_bytes(data)
    rprint(f"wrote {path} ({len(data)} bytes)")


@app.command()
def status() -> None:
    """Latest run and a one-line health summary."""
    _, eng = _engine()
    with read_conn(eng) as conn:
        latest = repos.latest_published_run(conn)
        rows = repos.dealer_runs_for_run(conn, str(latest["run_id"])) if latest else []
    rprint(
        json.dumps(
            {
                "latest": latest,
                "dealers": [
                    {k: r[k] for k in ("dealer_id", "status", "reason_code", "eligible_count")}
                    for r in rows
                ],
            },
            indent=1,
            default=str,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
