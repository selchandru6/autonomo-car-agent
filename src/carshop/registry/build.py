"""Compose the generated registry: locator snapshot + discovery + operator overrides.

Output is `data/registry/dealers.generated.yaml`, the file `load_registry()` reads. Rules:

* Every Texas dealer on the snapshot gets a row. Nothing is approved or rejected by hand.
* `dealer_id` is stable across refreshes: a center number already in the previous generated
  registry keeps its id (history in the database hangs off it).
* An operator override (keyed by center number) wins over discovery for whatever fields it
  sets, and is a privileged edit: it carries operator id and time into the row's notes and
  into `audit_events` when applied.
* A dealer that left the locator is kept with `active: false`; its history is untouched.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from carshop.config.loaders import Registry, load_registry
from carshop.config.settings import Settings
from carshop.domain.codes import DiscoveryReason, Platform, TermsStatus
from carshop.domain.models import Dealer
from carshop.fetch.browser import BrowserRenderer
from carshop.fetch.http import HttpFetcher
from carshop.fetch.politeness import HostGate
from carshop.registry.discovery import Discoverer, Discovery, prefixes_for
from carshop.registry.locator import LocatorDealer, LocatorSnapshot, fetch_snapshot
from carshop.storage import repos
from carshop.storage.db import write_tx
from carshop.util import iso, utcnow

log = structlog.get_logger(__name__)


class Override(BaseModel):
    """Operator override for one dealer (requirements 7.1: privileged edit with id and time)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    center_number: str
    operator: str
    set_at: str
    dealer_id: str | None = None
    hostname: str | None = None
    start_url: str | None = None
    path_prefixes: tuple[str, ...] | None = None
    platform: Platform | None = None
    sticker_hosts: tuple[str, ...] | None = None
    active: bool | None = None
    notes: str | None = None


def load_overrides(path: Path | None = None) -> dict[str, Override]:
    if path is not None:
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
    else:
        raw = (
            resources.files("carshop.config").joinpath("registry/overrides.yaml").read_text("utf-8")
        )
    data = yaml.safe_load(raw) or {}
    entries = data.get("overrides") or []
    out: dict[str, Override] = {}
    for e in entries:
        o = Override.model_validate(e)
        if o.center_number in out:
            raise ValueError(f"duplicate override for center {o.center_number}")
        out[o.center_number] = o
    return out


def slug_for(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\bbmw of the\b", "bmw", s)
    s = re.sub(r"\bbmw of\b", "bmw", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "dealer"


@dataclass(slots=True)
class BuildResult:
    registry: Registry
    path: Path
    added: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    no_source: dict[str, str] = field(default_factory=dict)  # dealer_id -> reason


def compose(
    snapshot: LocatorSnapshot,
    discoveries: dict[str, Discovery],
    overrides: dict[str, Override],
    previous: Registry | None,
    now: datetime,
) -> tuple[dict[str, Any], BuildResult]:
    """Pure: build the registry document. `discoveries` is keyed by center id."""
    prev_by_center: dict[str, Dealer] = {}
    prev_by_id: dict[str, Dealer] = {}
    if previous is not None:
        for d in previous.dealers:
            prev_by_id[d.dealer_id] = d
            if d.center_number:
                prev_by_center[d.center_number] = d
    used_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    res_added: list[str] = []
    res_no_source: dict[str, str] = {}
    seen_centers: set[str] = set()

    for ld in snapshot.dealers:
        seen_centers.add(ld.center_id)
        o = overrides.get(ld.center_id)
        prev = prev_by_center.get(ld.center_id)
        dealer_id = (o.dealer_id if o and o.dealer_id else None) or (
            prev.dealer_id if prev else None
        )
        if not dealer_id or dealer_id in used_ids:
            base = slug_for(ld.name)
            dealer_id = (
                base
                if base not in used_ids and base not in prev_by_id
                else f"{base}-{ld.center_id}"
            )
        used_ids.add(dealer_id)
        disc = discoveries.get(ld.center_id)
        # No fresh discovery for this center (e.g. --only-missing): the previous verified row
        # is carried forward unchanged.
        carry = prev if disc is None and prev is not None else None
        hostname = (
            (o.hostname if o and o.hostname else None)
            or (disc.hostname if disc else None)
            or (carry.hostname if carry else None)
            or ld.hostname
            or ""
        )
        start_url = (
            (o.start_url if o and o.start_url else None)
            or (disc.start_url if disc else None)
            or (carry.start_url if carry else None)
        )
        platform = (
            (o.platform if o and o.platform else None)
            or (disc.platform if disc else None)
            or (carry.platform if carry else None)
            or Platform.UNKNOWN
        )
        if o and o.path_prefixes:
            prefixes: tuple[str, ...] = o.path_prefixes
        elif disc and disc.path_prefixes:
            prefixes = disc.path_prefixes
        elif carry and carry.path_prefixes:
            prefixes = carry.path_prefixes
        elif start_url:
            prefixes = prefixes_for(platform, start_url)
        else:
            prefixes = ()
        reason: str
        discovered_at: str | None
        if o and o.start_url:
            reason = DiscoveryReason.OVERRIDE.value
            discovered_at = o.set_at
        elif disc:
            reason = disc.reason.value
            discovered_at = iso(now) if disc.start_url else None
        elif prev and prev.start_url:
            reason = prev.discovery_reason or DiscoveryReason.PLATFORM_CONVENTION.value
            discovered_at = prev.discovered_at
        else:
            reason = (
                DiscoveryReason.NO_HOSTNAME.value
                if not hostname
                else DiscoveryReason.NO_INVENTORY_LINK.value
            )
            discovered_at = None
        notes: list[str] = []
        if disc and disc.redirected_from:
            notes.append(f"locator host {disc.redirected_from} redirected to {hostname}")
        if disc and not disc.start_url:
            notes.append(f"discovery: {disc.detail}")
        if o:
            notes.append(
                f"override by {o.operator} at {o.set_at}" + (f": {o.notes}" if o.notes else "")
            )
        if not start_url:
            res_no_source[dealer_id] = reason
        if prev is None:
            res_added.append(dealer_id)
        streak = _fail_streak(reason, prev, fresh=disc is not None and disc.start_url is None)
        rows.append(
            {
                "dealer_id": dealer_id,
                "center_number": ld.center_id,
                "name": ld.name,
                "city": ld.city,
                "state": ld.state,
                "hostname": hostname,
                "start_url": start_url,
                "path_prefixes": list(prefixes),
                "platform": platform.value,
                "terms_status": TermsStatus.OWNER_ACCEPTED_RISK.value,
                "sticker_hosts": list(o.sticker_hosts) if o and o.sticker_hosts else [],
                "active": o.active if o and o.active is not None else True,
                "notes": "; ".join(notes) or None,
                "locator_url": ld.website,
                "discovery_reason": reason,
                "discovered_at": discovered_at,
                "source": "override" if o and (o.start_url or o.hostname) else "locator",
                "discovery_fail_streak": streak,
            }
        )

    deactivated: list[str] = []
    if previous is not None:
        for d in previous.dealers:
            if d.center_number in seen_centers:
                continue
            row = d.model_dump(mode="json")
            row["path_prefixes"] = list(d.path_prefixes)
            row["sticker_hosts"] = list(d.sticker_hosts)
            if d.active:
                row["notes"] = "; ".join(
                    filter(None, [d.notes, f"left the locator on {now.date().isoformat()}"])
                )
                deactivated.append(d.dealer_id)
            row["active"] = False
            rows.append(row)

    doc: dict[str, Any] = {
        "version": f"{now.date().isoformat()}.{snapshot.sha256[:6]}",
        "generated_at": iso(now),
        "locator_snapshot": {
            "fetched_at": snapshot.fetched_at,
            "sha256": snapshot.sha256,
            "storage_uri": snapshot.storage_uri,
            "dealer_count": len(snapshot.dealers),
            "total_seen": snapshot.total_seen,
            "source_urls": snapshot.source_urls,
        },
        "dealers": rows,
    }
    result = BuildResult(registry=_registry_from_doc(doc), path=Path(), added=res_added,
                         deactivated=deactivated, no_source=res_no_source)  # fmt: skip
    return doc, result


_HOMEPAGE_FAILS = frozenset(
    {
        DiscoveryReason.HOMEPAGE_CONNECT_FAILED.value,
        DiscoveryReason.HOMEPAGE_TLS_FAILED.value,
        DiscoveryReason.HOMEPAGE_TIMEOUT.value,
        DiscoveryReason.HOMEPAGE_UNREACHABLE.value,
    }
)


def _fail_streak(reason: str, prev: Dealer | None, *, fresh: bool) -> int:
    """Consecutive refreshes whose homepage could not be reached. A carried-forward row does
    not increment: only a discovery that failed again this refresh does."""
    if reason not in _HOMEPAGE_FAILS:
        return 0
    if not fresh:
        return prev.discovery_fail_streak if prev is not None else 0
    if prev is not None and prev.discovery_reason in _HOMEPAGE_FAILS:
        return prev.discovery_fail_streak + 1
    return 1


def _registry_from_doc(doc: dict[str, Any]) -> Registry:
    import hashlib

    raw = yaml.safe_dump(doc, sort_keys=False)
    return Registry(
        version=str(doc["version"]),
        dealers=tuple(Dealer.model_validate(d) for d in doc["dealers"]),
        config_hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
    )


def write_registry(doc: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# GENERATED by `carshop registry refresh` from the BMW USA dealer locator (Texas) plus\n"
        "# inventory-URL discovery (F25). Do not edit: put operator overrides in overrides.yaml.\n"
    )
    path.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), "utf-8")


async def refresh_registry(
    settings: Settings,
    engine: Engine | None,
    *,
    only_missing: bool = False,
    snapshot: LocatorSnapshot | None = None,
    operator_id: str = "system",
) -> BuildResult:
    """Fetch (or reuse) a locator snapshot, discover inventory URLs, write the registry, and
    apply it to the database with audit events."""
    settings.ensure_dirs()
    now = utcnow()
    previous = load_registry(settings.registry_path) if settings.registry_path.exists() else None
    overrides = load_overrides(settings.registry_overrides_path)
    http = HttpFetcher(user_agent=settings.http_user_agent, timeout_s=settings.http_timeout_s,
                       backoff_s=settings.retry_backoff_s)  # fmt: skip
    gate = HostGate(settings.per_host_min_pause_s, settings.per_host_max_pause_s)
    discoveries: dict[str, Discovery] = {}
    try:
        async with BrowserRenderer(settings) as browser:
            snap = snapshot or await fetch_snapshot(settings, browser)
            disc = Discoverer(settings, http, browser, gate)
            sem = asyncio.Semaphore(settings.discovery_concurrency)

            prev_by_center = {d.center_number: d for d in (previous.dealers if previous else ())}

            async def one(ld: LocatorDealer) -> None:
                o = overrides.get(ld.center_id)
                if o and o.start_url:
                    return  # override wins; never re-discovered
                prev = prev_by_center.get(ld.center_id)
                if only_missing and prev and prev.start_url:
                    discoveries[ld.center_id] = Discovery(
                        prev.hostname, prev.start_url, prev.path_prefixes, prev.platform,
                        DiscoveryReason(prev.discovery_reason or "platform_convention"), "kept",
                    )  # fmt: skip
                    return
                host = (o.hostname if o and o.hostname else None) or ld.hostname
                async with sem:
                    d = await disc.discover(host, slug_for(ld.name))
                discoveries[ld.center_id] = d
                log.info("discovery", dealer=ld.name, host=d.hostname, start_url=d.start_url,
                         platform=d.platform.value, reason=d.reason.value)  # fmt: skip

            await asyncio.gather(*(one(ld) for ld in snap.dealers))
    finally:
        await http.aclose()

    doc, result = compose(snap, discoveries, overrides, previous, now)
    write_registry(doc, settings.registry_path)
    result.path = settings.registry_path
    result.registry = load_registry(settings.registry_path)
    if engine is not None:
        with write_tx(engine) as conn:
            repos.upsert_dealers(conn, result.registry.dealers, operator_id=operator_id)
            repos.retire_centerless_dealers(conn)
            gone = repos.deactivate_dealers_not_in(
                conn, (d.dealer_id for d in result.registry.dealers), operator_id=operator_id
            )
            result.deactivated.extend(g for g in gone if g not in result.deactivated)
            repos.record_registry_snapshot(
                conn,
                fetched_at=snap.fetched_at,
                sha256=snap.sha256,
                storage_uri=snap.storage_uri,
                dealer_count=len(snap.dealers),
                total_seen=snap.total_seen,
                registry_version=result.registry.version,
            )
    log.info(
        "registry refreshed",
        version=result.registry.version,
        dealers=len(result.registry.dealers),
        added=result.added,
        deactivated=result.deactivated,
        no_source=result.no_source,
    )
    return result


def registry_is_stale(settings: Settings, now: datetime | None = None) -> bool:
    """True when there is no generated registry or its snapshot is older than the refresh window."""
    if not settings.registry_path.exists():
        return True
    doc = yaml.safe_load(settings.registry_path.read_text("utf-8")) or {}
    fetched = ((doc.get("locator_snapshot") or {}).get("fetched_at")) or doc.get("generated_at")
    if not fetched:
        return True
    from carshop.util import parse_iso

    age = ((now or utcnow()) - parse_iso(str(fetched))).total_seconds() / 86400.0
    return age > settings.registry_refresh_days
