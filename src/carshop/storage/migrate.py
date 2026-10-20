"""Run Alembic programmatically against the configured database."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

_ROOT = Path(__file__).resolve().parents[3]


def _config(engine: Engine) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = _config(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"version_table": "schema_migrations"})
        return ctx.get_current_revision()


def head_revision(engine: Engine) -> str | None:
    script = ScriptDirectory.from_config(_config(engine))
    return script.get_current_head()


def is_current(engine: Engine) -> bool:
    return current_revision(engine) == head_revision(engine)
