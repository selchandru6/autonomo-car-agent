"""Retention jobs (requirements 7.5, 8): 14-day snapshots, checkpoint pruning."""

from __future__ import annotations

import shutil
import time
from pathlib import Path


def prune_snapshots(snapshots_dir: Path, retention_days: int) -> list[str]:
    """Remove per-run snapshot directories older than retention. Returns removed names."""
    if not snapshots_dir.exists():
        return []
    cutoff = time.time() - retention_days * 86400
    removed: list[str] = []
    for run_dir in snapshots_dir.iterdir():
        if run_dir.is_dir() and run_dir.stat().st_mtime < cutoff:
            shutil.rmtree(run_dir, ignore_errors=True)
            removed.append(run_dir.name)
    return removed


def prune_backups(backups_dir: Path, keep: int = 14) -> list[str]:
    if not backups_dir.exists():
        return []
    files = sorted(backups_dir.glob("app-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for f in files[keep:]:
        f.unlink(missing_ok=True)
        removed.append(f.name)
    return removed
