"""A9 Notifier: digest, then backup, both after the run is committed PUBLISHED (F9, F21).

A backup failure is logged and never fails the run. A digest failure fails the run, as before.
"""

from __future__ import annotations

import structlog
from langgraph.runtime import Runtime

from carshop.graph.context import RunContext
from carshop.graph.state import RunState
from carshop.storage.backup import backup, verify_restore
from carshop.storage.retention import prune_backups, prune_snapshots

log = structlog.get_logger(__name__)


async def notifier(state: RunState, runtime: Runtime[RunContext]) -> RunState:
    ctx = runtime.context
    settings = ctx.settings
    sent: dict[str, str] = {}
    if ctx.notify:
        sent = ctx.deliver_digest(ctx.engine, settings, ctx.run_id)
    backup_path: str | None = None
    try:
        path = backup(settings.db_path, settings.backups_dir)
        verify_restore(path)
        backup_path = str(path)
        prune_backups(settings.backups_dir)
        prune_snapshots(settings.snapshots_dir, settings.snapshot_retention_days)
    except Exception:
        log.exception("backup failed")
    return {"notifications": {"sent": sent, "backup_path": backup_path}}
