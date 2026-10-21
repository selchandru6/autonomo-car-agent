"""LangGraph checkpointer in its own file (requirements-mas.md §5 invariants 5 and 6).

Business data stays in the app database; checkpoints go to `data/checkpoints.sqlite` with
`thread_id = run_id`. Threads older than the last published run are pruned after each publish.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy import Engine, select

from carshop.config.settings import Settings
from carshop.storage import repos
from carshop.storage import schema as s
from carshop.storage.db import read_conn

log = structlog.get_logger(__name__)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncSqliteSaver]:
    settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
        yield saver


def thread_config(run_id: str, *, max_concurrency: int) -> RunnableConfig:
    return {
        "configurable": {"thread_id": run_id},
        "max_concurrency": max(1, max_concurrency),
    }


async def thread_ids(saver: AsyncSqliteSaver) -> list[str]:
    await saver.setup()
    async with saver.conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cur:
        return [str(row[0]) for row in await cur.fetchall()]


async def prune_checkpoints(saver: AsyncSqliteSaver, engine: Engine) -> list[str]:
    """Drops threads of runs that started before the last published run, and threads whose
    run never got a row (rejected at admission). Returns the pruned run ids."""
    with read_conn(engine) as conn:
        latest = repos.latest_published_run(conn, full_registry=False)
        if latest is None:
            return []
        started = {
            str(r[0]): str(r[1]) for r in conn.execute(select(s.runs.c.run_id, s.runs.c.started_at))
        }
    cutoff = str(latest["started_at"])
    pruned = [t for t in await thread_ids(saver) if t not in started or started[t] < cutoff]
    for thread in pruned:
        await saver.adelete_thread(thread)
    if pruned:
        log.info("pruned checkpoints", threads=len(pruned))
    return pruned
