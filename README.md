# AI car shopping — 2026 BMW X5 xDrive40i, Texas

A daily, deterministic inventory monitor for one owner. It reads every Texas BMW franchise
dealer on the BMW USA dealer locator once a day, classifies every listing against hard rules, stores every observation with
its evidence in SQLite, computes same-VIN deltas against each dealer's last good read, sends a
digest, and shows everything on a local web screen.

It is a measurement instrument, not a forecaster. The requirements in
[`doc/requirements.md`](doc/requirements.md) are the contract; the code follows them section by
section. Invariants that matter most:

- Unknown is never No. A missing price is `NULL`, never `0`, and MSRP is never substituted.
- A dealer that was not read is a gap (`PARTIAL` / `NOT_REPORTED`), never zero inventory.
- A VIN that disappears is `MISSING_UNVERIFIED`, never a sale.
- Every `YES`/`NO` on a feature carries an exact quote, URL, fetch time and page hash.
- GET/HEAD only, robots re-read every run, no CAPTCHA solving, no proxies, no TLS tricks.
- The dealer list is the locator's, not ours: every Texas dealer is on the registry and
  attempted every run; coverage is reported against all of them (requirements v1.3).

## Layout

```
src/carshop/
  config/      settings (env, CARSHOP_*), dealer registry + preferences (YAML)
  domain/      pure rules: codes, VIN, pricing, features (DAP), classify, lifecycle
  reference/   versioned AG codes, colour and upholstery tables
  safety/      URL allowlist, SSRF guard, robots evaluation
  fetch/       httpx client, Playwright renderer, claim-check artifacts, host politeness
  adapters/    Dealer Inspire, Dealer.com, DealerOn, DealerFire parsers, generic JSON-LD + detection
  extract/     optional LLM package resolver (off without a key)
  pipeline/    run supervisor, per-dealer branch, comparator, digest
  storage/     SQLAlchemy Core schema, Alembic migrations, repos, backup, retention
  export/      CSV / XLSX
  web/         FastAPI + Jinja screen (Tailwind, Alpine, ECharts via CDN)
  cli.py       `carshop` command
tests/         unit, golden fixtures (offline HTML), §13 gate tests
deploy/        Dockerfile entrypoint, crontab, heartbeat
doc/           requirements, ADRs, runbooks
```

## Quick start (local)

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --all-extras
uv run playwright install chromium        # browser fallback for Cloudflare-fronted dealers
cp .env.example .env                      # optional; everything has a default
uv run carshop migrate-db
uv run carshop registry refresh           # locator snapshot + inventory-URL discovery, ~4 min
uv run carshop run --notify               # every Texas dealer, ~15-30 min with the browser
uv run carshop serve                      # http://127.0.0.1:8000
```

Useful commands:

```sh
uv run carshop run --dealer bmw-austin           # one dealer
uv run carshop run --trigger catchup --date 2026-10-18
uv run carshop status                            # latest run as JSON
uv run carshop coverage                          # per-dealer last status vs. the registry
uv run carshop registry show                     # the generated registry
uv run carshop registry refresh --only-missing   # re-discover only dealers without a URL
uv run carshop export --kind xlsx                # or csv
uv run carshop backup && uv run carshop prune
uv run carshop digest <run_id>                   # print the digest text
```

Without `CARSHOP_MODEL_API_KEY` the run records `model_status=down` and ambiguous package
text stays `UNKNOWN`. Nothing else changes. Set the key later and the single LLM step turns on
with a $0.15 per-run breaker.

## The screen

| Page | What it shows |
| --- | --- |
| Overview | KPIs, price band scatter per dealer, floors, stock and floor trends, top preference fits, dealer health |
| Inventory | Every eligible VIN in the published run with facet filters (dealer, colour, DAP, M Sport, transit, price shown) and sort |
| VIN | One car: prices by kind, feature evidence with quotes, same-VIN price history, lifecycle, every observation |
| Shortlist | Preference fit and evidence confidence as separate scores; side-by-side compare up to 4 |
| Trends | Floor and stock per dealer over time with gaps where a dealer was not read; annotations for floor-VIN turnover |
| Health | Registry coverage, 30-day status grid, exclusions by code, every fetch attempt |
| Review | PENDING_REVIEW items with validate/reject, full audit trail |
| Runs | Run history, notification log, digest text |

The screen reads committed rows only and serves the latest `PUBLISHED` run by default; any
earlier run is selectable. Front-end libraries load from public CDNs; for an air-gapped host
vendor them into `src/carshop/web/static/`.

## Registry (requirements 7.1, F1, F25)

`carshop registry refresh` builds `data/registry/dealers.generated.yaml`:

1. **Locator snapshot** — the BMW USA dealer locator's own results request for Texas (GET,
   robots checked, issued from the locator page in Chromium because the BMW edge rejects
   non-browser TLS). Dealers are de-duplicated by center number. The raw bodies are kept.
2. **Discovery** — for each dealer host: homepage → platform → robots → the cleanest verified
   new-X5 inventory URL (homepage link, platform convention, sitemap, unfiltered new page).
   One cross-host redirect is followed as a rename and recorded.
3. **Compose** — stable ids, `active: false` for dealers that left the locator, and operator
   overrides from `src/carshop/config/registry/overrides.yaml` (privileged: `operator` and
   `set_at` are required and audited).

A run refreshes automatically when the snapshot is older than 30 days. A dealer with no
discoverable inventory URL is `NO_SOURCE` with a reason (`no_hostname`,
`homepage_unreachable`, …), shown on the Health page and counted in the coverage denominator.
See [ADR 0003](doc/adr/0003-registry-from-locator.md).

## Dealer access

Most dealer sites sit behind Cloudflare or Akamai and return 403 to plain HTTP clients. The
pipeline tries HTTP first and then renders once with a real Chromium through Playwright,
identified as that Chrome build **plus the product token `carshop/0.1`**, with the project URL
in an `X-Carshop` header and `From:` when `CARSHOP_CONTACT_EMAIL` is set (see
[ADR 0002](doc/adr/0002-browser-identity.md)). No CAPTCHA is ever solved; a challenge page
is recorded as `CAPTCHA` and the dealer is `NOT_REPORTED` for the day.

Politeness (v1.3 baselines): one request in flight per host, a 5–10 s pause between requests
to one host, four dealers in parallel, a 10-minute branch timeout per dealer (timeout keeps
the pages already read → `PARTIAL` / `TIMEOUT`), 60-minute wall-clock target per run.

## Development

```sh
uv run ruff check src tests && uv run ruff format src tests
uv run mypy
uv run pytest                      # network is blocked by pytest-socket
uv run pytest -m gate              # §13 release gates only
uv run pre-commit install
```

Golden fixtures live in `tests/golden/pages`; expected classifications in
`tests/golden/expected`. Change a parser, re-run, and inspect the diff before updating the
expected JSON.

## Deployment

`Dockerfile` builds a non-root image with Chromium. `compose.yaml` runs the web screen and a
scheduler that fires the 06:00 America/Chicago run and the 07:30 heartbeat. For a plain VM use
`deploy/crontab`. Runbooks are in `doc/runbooks/`.

Secrets: only `CARSHOP_MODEL_API_KEY` and `CARSHOP_SMTP_URL`, via environment. Nothing in
the repo or the database.
