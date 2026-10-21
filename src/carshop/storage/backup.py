"""Backup and verified restore (requirements 8, Recovery)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def backup(db_path: Path, backups_dir: Path) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backups_dir / f"app-{stamp}.db"
    src = sqlite3.connect(str(db_path))
    try:
        src.execute("VACUUM INTO ?", (str(dest),))
    finally:
        src.close()
    return dest


def verify_restore(backup_path: Path) -> dict[str, int]:
    """A restore MUST be opened and read, not merely copied. Returns a few row counts."""
    conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        counts: dict[str, int] = {}
        for table in ("runs", "dealers", "observations", "dealer_runs"):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check failed: {integrity}")
        return counts
    finally:
        conn.close()
