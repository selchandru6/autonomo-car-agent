"""CSV / XLSX export of a published run (F23). Same scope, labels and warnings as the screen."""

from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from sqlalchemy import Connection

from carshop.domain.pricing import fmt_cents
from carshop.storage import repos

COLUMNS = [
    "run_id", "snapshot_date", "dealer", "city", "vin", "stock", "year", "model", "trim", "exterior", "exterior_code",
    "interior", "interior_code", "odometer_miles", "availability", "msrp", "advertised_price", "comparison_price",
    "comparison_price_note", "dap", "m_sport", "flags", "staging", "source_url", "detail_url", "fetched_at_utc", "page_hash",
]  # fmt: skip


def _rows(conn: Connection, run_id: str) -> tuple[list[dict[str, object]], list[str]]:
    run = repos.get_run(conn, run_id) or {}
    obs = repos.observations_for_run(conn, run_id, validated_only=True)
    drs = repos.dealer_runs_for_run(conn, run_id)
    warnings = [
        f"{d['name']}: {d['status']} ({d['reason_code'] or '—'}) — gap, not zero"
        for d in drs
        if d["status"] != "REPORTED"
    ]
    rows: list[dict[str, object]] = []
    for o in obs:
        note = (
            ""
            if o["comparison_price_cents"] is not None
            else (
                "PRICE_IMPLAUSIBLE"
                if "PRICE_IMPLAUSIBLE" in o["flags"]
                else "no unconditional cash price shown"
            )
        )
        rows.append(
            {
                "run_id": run_id, "snapshot_date": run.get("snapshot_date"), "dealer": o["dealer_name"], "city": o["city"],
                "vin": o["vin"], "stock": o["stock"], "year": o["year"], "model": o["model"], "trim": o["trim"],
                "exterior": o["exterior_raw"], "exterior_code": o["exterior_code"], "interior": o["interior_raw"],
                "interior_code": o["interior_code"], "odometer_miles": o["odometer_miles"], "availability": o["availability"],
                "msrp": fmt_cents(o["msrp_cents"]) if o["msrp_cents"] else "", "advertised_price": fmt_cents(o["advertised_price_cents"]) if o["advertised_price_cents"] else "",
                "comparison_price": fmt_cents(o["comparison_price_cents"]) if o["comparison_price_cents"] else "", "comparison_price_note": note,
                "dap": o["dap_status"], "m_sport": "YES" if o["m_sport"] else "NO", "flags": " ".join(o["flags"]), "staging": o["staging_status"],
                "source_url": o["source_url"], "detail_url": o["detail_url"], "fetched_at_utc": o["fetched_at"], "page_hash": o["page_hash"],
            }
        )  # fmt: skip
    return rows, warnings


def export_run(conn: Connection, run_id: str, kind: str) -> bytes:
    rows, warnings = _rows(conn, run_id)
    if kind == "csv":
        buf = io.StringIO()
        for w in warnings:
            buf.write(f"# WARNING {w}\n")
        buf.write(
            f"# Prices are advertised cash prices excluding tax, title and dealer fees unless itemised. Not a forecast. run={run_id}\n"
        )
        wr = csv.DictWriter(buf, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerows(rows)
        return buf.getvalue().encode("utf-8")
    if kind == "xlsx":
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "inventory"
        ws.append(COLUMNS)
        for r in rows:
            ws.append([r[c] for c in COLUMNS])
        ws2 = wb.create_sheet("coverage_warnings")
        ws2.append(["warning"])
        for w in warnings:
            ws2.append([w])
        ws2.append(
            [
                "Prices are advertised cash prices excluding tax, title and dealer fees unless itemised. Not a forecast."
            ]
        )
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    raise ValueError(f"unknown export kind {kind}")
