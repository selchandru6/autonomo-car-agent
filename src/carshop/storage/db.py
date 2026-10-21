"""Engine factory. WAL, foreign keys on, busy timeout, single writer (requirements 8, 10)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event

_WRITE_LOCK = threading.RLock()


def make_engine(path: Path | str) -> Engine:
    p = Path(path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite://" if str(p) == ":memory:" else f"sqlite:///{p}"
    engine = create_engine(url, future=True, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: sqlite3.Connection, _record: object) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


@contextmanager
def write_tx(engine: Engine) -> Iterator[Connection]:
    """One writer at a time within this process; one transaction per caller (per dealer)."""
    with _WRITE_LOCK, engine.begin() as conn:
        yield conn


@contextmanager
def read_conn(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as conn:
        yield conn
