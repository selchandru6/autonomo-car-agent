"""Small shared helpers: time, hashing, build identity, URL normalisation."""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from carshop import __version__

PARSER_VERSION = "parser-2026.10.1"

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid",
    "msclkid", "mc_cid", "mc_eid", "_ga", "ref", "referrer", "source",
}  # fmt: skip


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def civil_date(dt: datetime, tz: str) -> date:
    return dt.astimezone(ZoneInfo(tz)).date()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


@lru_cache(maxsize=1)
def git_sha() -> str:
    env = os.environ.get("CARSHOP_GIT_SHA")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
            capture_output=True, text=True, timeout=5, check=False, cwd=Path(__file__).resolve().parent,
        )  # fmt: skip
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return f"unknown-{__version__}"


def normalize_url(url: str) -> str:
    """Strip tracking params and fragments, lowercase host, keep meaningful query."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    host = parts.hostname or ""
    netloc = host if parts.port in (None, 80, 443) else f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, urlencode(query), ""))


def new_run_id(now: datetime, trigger: str) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{trigger}-{sha256_text(str(now.timestamp()))[:6]}"
