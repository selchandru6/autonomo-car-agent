"""LangGraph checkpointer in its own file (requirements-mas.md §5 invariant 5).

Business data stays in the app database; checkpoints go to `data/checkpoints.sqlite`
with `thread_id = run_id`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from carshop.config.settings import Settings


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
