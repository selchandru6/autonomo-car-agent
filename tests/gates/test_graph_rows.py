"""requirements-mas.md §11 Step 2 exit: the golden fixtures produce the same rows as the plain
pipeline did. `graph_rows.json` was recorded from the plain `run_dealer` path before the
dealer branch was split; set REGEN_GRAPH_ROWS=1 to re-record after an intended change."""

from __future__ import annotations

import json
import os

from carshop.domain.codes import RunStatus
from tests.conftest import GOLDEN
from tests.gates.rows import dump_rows
from tests.gates.test_gates import (
    DC,
    DC_PAGE,
    DI,
    DI_MULTI,
    NOSRC,
    T1,
    T2,
    T3,
    dc_routes,
    di_routes,
    go,
    registry,
)

ROWS = GOLDEN / "graph_rows.json"


def test_golden_fixtures_produce_the_plain_pipeline_rows(env) -> None:  # type: ignore[no-untyped-def]
    st, eng, fake = env
    reg = registry(DI, DC, NOSRC)
    run_ids = []

    fake.routes = {**di_routes(), **dc_routes()}
    run_ids.append(go(env, reg, now=T1).run_id)

    fake.routes = {
        **di_routes(DI_MULTI),
        **dc_routes(),
        "https://www.dc-test.com/new-inventory/": (200, DC_PAGE.replace("$78,400", "$77,900")),
    }
    run_ids.append(go(env, reg, now=T2).run_id)

    fake.routes = {
        **di_routes(vdp=None),
        "https://www.dc-test.com/robots.txt": (404, None),
        "https://www.dc-test.com/new-inventory/": (403, None),
    }
    rep = go(env, reg, now=T3)
    run_ids.append(rep.run_id)
    assert rep.status is RunStatus.PUBLISHED

    got = dump_rows(eng, run_ids, str(st.data_dir))
    if os.environ.get("REGEN_GRAPH_ROWS"):
        ROWS.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    want = json.loads(ROWS.read_text())
    assert got.keys() == want.keys()
    for table in want:
        assert got[table] == want[table], table
