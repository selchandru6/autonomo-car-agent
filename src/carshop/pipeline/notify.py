"""Notifier: deterministic digest render and idempotent delivery (F9, F21, 7.10).

Without an SMTP URL the digest is written to /data/digests and recorded as channel "file".
Delivery is idempotent on (run_id, recipient, event_key); a retry never sends twice.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import structlog
from sqlalchemy import Connection, Engine

from carshop.config.settings import Settings
from carshop.domain.pricing import fmt_cents
from carshop.storage import repos
from carshop.storage.db import write_tx
from carshop.util import sha256_text

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Digest:
    subject: str
    body: str
    content_hash: str


def build_digest(conn: Connection, run_id: str) -> Digest:
    run = repos.get_run(conn, run_id)
    assert run is not None
    drs = repos.dealer_runs_for_run(conn, run_id)
    obs = repos.observations_for_run(conn, run_id, validated_only=True)
    pe = repos.price_events_for_run(conn, run_id)
    lc = repos.lifecycle_for_run(conn, run_id)
    pending = repos.pending_review_count(conn, run_id)
    added = [e for e in lc if e["to_state"] == "ACTIVE" and e["reason"] == "added"]
    missing = [e for e in lc if e["to_state"] == "MISSING_UNVERIFIED"]
    not_reported = [d for d in drs if d["status"] == "NOT_REPORTED"]
    partial = [d for d in drs if d["status"] == "PARTIAL"]
    reported = [d for d in drs if d["status"] == "REPORTED"]
    no_source = [d for d in drs if d["status"] == "NO_SOURCE"]
    # v1.3: the denominator is every active Texas registry dealer in the run, NO_SOURCE included.
    pct = (100 * len(reported) // len(drs)) if drs else 0
    complete = len(reported) == len(drs) and bool(drs)

    lines = [
        f"AI car shopping digest — {run['snapshot_date']} (America/Chicago) — run {run_id}",
        "",
        f"Eligible new 2026 BMW X5 xDrive40i units (validated): {len(obs)}",
        f"Texas dealers REPORTED: {len(reported)} of {len(drs)} on the registry ({pct}%; target 90%)"
        + ("  — statewide complete" if complete else "  — NOT statewide complete"),
        f"Added VINs: {len(added)}    Missing (unverified, not a sale): {len(missing)}    Price changes: {len(pe)}",
        f"Pending review: {pending}",
        "",
    ]
    if no_source:
        lines.append(
            "NO SOURCE (no discoverable public inventory URL; counted in the denominator):"
        )
        lines += [f"  - {d['name']}" for d in no_source]
        lines.append("")
    if not_reported:
        lines.append("NOT REPORTED (gap, not zero):")
        lines += [f"  - {d['name']}: {d['reason_code']}" for d in not_reported]
        lines.append("")
    if partial:
        lines.append("PARTIAL (kept units, no removals):")
        lines += [f"  - {d['name']}: {d['reason_code']}" for d in partial]
        lines.append("")
    if pe:
        lines.append("Same-VIN comparison price changes:")
        for e in pe:
            lines.append(
                f"  - {e['dealer_name']} {e['vin']}: {e['event']} {fmt_cents(e['before_cents'])} -> {fmt_cents(e['after_cents'])} ({e['delta_cents'] / 100:+,.0f})"
            )
        lines.append("")
    from carshop.shortlist.saved import digest_lines
    from carshop.shortlist.searches import digest_search_lines, matcher_failed

    if matcher_failed(conn, run_id):
        lines += ["Saved-search matches could not be computed today", ""]
    else:
        fresh = digest_search_lines(conn, run_id)
        if fresh:
            lines += ["New for your saved searches", *fresh, ""]
    watched = digest_lines(conn, run_id)
    if watched:
        lines += ["Your saved cars", *watched, ""]
    lines.append(
        "Floor comparison price by dealer (cash advertised, excl. tax/title; MSRP never substituted):"
    )
    by_dealer: dict[str, list[dict[str, object]]] = {}
    for o in obs:
        by_dealer.setdefault(str(o["dealer_name"]), []).append(o)
    for d in drs:
        rows = by_dealer.get(str(d["name"]), [])
        priced = [
            int(str(r["comparison_price_cents"]))
            for r in rows
            if r["comparison_price_cents"] is not None
        ]
        if d["status"] == "REPORTED":
            floor = fmt_cents(min(priced)) if priced else "no cash price shown"
            lines.append(f"  - {d['name']}: {len(rows)} units, floor {floor}")
        else:
            lines.append(f"  - {d['name']}: {d['status']} ({d['reason_code'] or '—'})")
    lines += [
        "",
        "Nothing here is a forecast. A missing VIN is MISSING_UNVERIFIED, never a sale.",
        "",
    ]
    body = "\n".join(lines)
    subject = f"[carshop] {run['snapshot_date']} X5 xDrive40i digest — {len(obs)} eligible, {len(reported)}/{len(drs)} dealers — {run_id}"
    return Digest(subject=subject, body=body, content_hash=sha256_text(body))


def _send_smtp(smtp_url: str, sender: str, recipient: str, digest: Digest) -> None:
    parts = urlsplit(smtp_url)
    host, port = parts.hostname or "localhost", parts.port or 587
    q = parse_qs(parts.query)
    starttls = q.get("starttls", ["true"])[0].lower() != "false"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = digest.subject, sender, recipient
    msg.set_content(digest.body)
    if parts.scheme == "smtps":
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
            if parts.username:
                s.login(parts.username, parts.password or "")
            s.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=30) as s:
        if starttls:
            s.starttls(context=ssl.create_default_context())
        if parts.username:
            s.login(parts.username, parts.password or "")
        s.send_message(msg)


def deliver_digest(
    engine: Engine, settings: Settings, run_id: str, *, event_key: str = "digest"
) -> dict[str, str]:
    """Idempotent delivery per recipient. Returns recipient -> status."""
    with write_tx(engine) as conn:
        digest = build_digest(conn, run_id)
    results: dict[str, str] = {}
    recipients = settings.digest_recipients or ["file://digest"]
    smtp = settings.smtp_url.get_secret_value() if settings.smtp_url else None
    for rcpt in recipients:
        with write_tx(engine) as conn:
            if repos.notification_exists(conn, run_id, rcpt, event_key) and _already_sent(
                conn, run_id, rcpt, event_key
            ):
                results[rcpt] = "already-sent"
                continue
            if smtp and not rcpt.startswith("file://"):
                try:
                    _send_smtp(smtp, settings.digest_from, rcpt, digest)
                    repos.record_notification(
                        conn,
                        run_id=run_id,
                        recipient=rcpt,
                        event_key=event_key,
                        channel="smtp",
                        content_hash=digest.content_hash,
                        provider_status="sent",
                        error=None,
                        sent=True,
                    )
                    results[rcpt] = "sent"
                except (OSError, smtplib.SMTPException) as e:
                    repos.record_notification(
                        conn,
                        run_id=run_id,
                        recipient=rcpt,
                        event_key=event_key,
                        channel="smtp",
                        content_hash=digest.content_hash,
                        provider_status="error",
                        error=str(e)[:300],
                        sent=False,
                    )
                    results[rcpt] = f"error: {e}"
            else:
                path = _write_file(settings.digests_dir, run_id, digest)
                repos.record_notification(
                    conn,
                    run_id=run_id,
                    recipient=rcpt,
                    event_key=event_key,
                    channel="file",
                    content_hash=digest.content_hash,
                    provider_status=str(path),
                    error=None,
                    sent=True,
                )
                results[rcpt] = f"file:{path}"
            if results.get(rcpt) in ("sent",) or str(results.get(rcpt, "")).startswith("file:"):
                _record_line_keys(conn, run_id, rcpt, digest.content_hash)
    return results


def _record_line_keys(conn: Connection, run_id: str, rcpt: str, content_hash: str) -> None:
    from carshop.shortlist.searches import digest_line_keys

    for key in digest_line_keys(conn, run_id):
        repos.record_notification(
            conn,
            run_id=run_id,
            recipient=rcpt,
            event_key=key,
            channel="digest-line",
            content_hash=content_hash,
            provider_status="sent",
            error=None,
            sent=True,
        )


def _already_sent(conn: Connection, run_id: str, rcpt: str, key: str) -> bool:
    from sqlalchemy import select

    from carshop.storage import schema as s

    r = conn.execute(
        select(s.notification_events.c.sent_at).where(
            s.notification_events.c.run_id == run_id,
            s.notification_events.c.recipient == rcpt,
            s.notification_events.c.event_key == key,
        )
    ).first()
    return bool(r and r[0])


def _write_file(digests_dir: Path, run_id: str, digest: Digest) -> Path:
    digests_dir.mkdir(parents=True, exist_ok=True)
    path = digests_dir / f"{run_id}.txt"
    path.write_text(f"Subject: {digest.subject}\n\n{digest.body}", encoding="utf-8")
    return path
