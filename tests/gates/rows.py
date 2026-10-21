"""Normalized dump of every run-written table, for row-identity checks across refactors.

Ids, timestamps, durations, and the git SHA are dropped; run ids become RUN0, RUN1, ...;
child rows point at their observation as run:dealer:vin instead of an autoincrement id.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, Integer, select

from carshop.storage import schema as s
from carshop.storage.db import read_conn

SKIP_TABLES = {"dealers", "registry_snapshots", "catalog_vehicles", "catalog_features"}
VOLATILE = {"duration_ms", "latency_ms", "git_sha", "first_seen", "last_seen", "event_at"}


def _autoincrement_pk(table: Any) -> str | None:
    pk = list(table.primary_key.columns)
    if len(pk) == 1 and isinstance(pk[0].type, Integer):
        return str(pk[0].name)
    return None


def dump_rows(eng: Engine, run_ids: list[str], tmp_root: str) -> dict[str, list[str]]:
    names = {rid: f"RUN{i}" for i, rid in enumerate(run_ids)}

    def norm(text: str) -> str:
        for rid, name in names.items():
            text = text.replace(rid, name)
        return text.replace(tmp_root, "TMP")

    out: dict[str, list[str]] = {}
    with read_conn(eng) as conn:
        obs_key = {
            r[
                "observation_id"
            ]: f"{names.get(r['run_id'], r['run_id'])}:{r['dealer_id']}:{r['vin']}"
            for r in conn.execute(select(s.observations)).mappings()
        }
        for table in s.metadata.sorted_tables:
            if table.name in SKIP_TABLES:
                continue
            pk = _autoincrement_pk(table)
            rows = []
            for r in conn.execute(select(table)).mappings():
                row = {
                    k: v
                    for k, v in r.items()
                    if k != pk and k not in VOLATILE and not k.endswith("_at")
                }
                if "observation_id" in row:
                    row["observation_id"] = obs_key.get(row["observation_id"])
                rows.append(norm(json.dumps(row, sort_keys=True, default=str)))
            out[table.name] = sorted(rows)
    return out
