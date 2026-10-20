# autonomo-car-agent

An auditable daily monitor for new **2026 BMW X5 xDrive40i** listings at approved Texas BMW franchise dealers.

Each day it collects listings from the dealers on an approved registry. It keeps an append-only history of every listing and shows price and availability on one screen, plus a dated email digest. A buyer can see:

- where advertised cash prices sit;
- which dealer's lowest price moved;
- how stock changed;
- which VINs appeared or dropped off;
- which cars best match saved preferences, such as grey exterior, a non-black interior, and the Driving Assistance Professional package.

It does **not** contact dealers, submit leads, forecast prices, or claim that a delisted car was sold. It never claims to have found every car in Texas. It reports every approved dealer, including the ones it could not read.

> **Status: early scaffold.** The package layout and tooling are in place, but the pipeline is not implemented yet. The commands in [Usage](#usage) are planned, not working. The full specification is [doc/requirements.md](doc/requirements.md).

## How it works

```
registry ─► fetcher ─► extractor ─► validator ─► storage ─► comparator ─► notifier
 (approved   (robots,    (VIN, price,   (scope rules,  (SQLite,     (deltas vs the    (digest +
  dealers)    allowlist)  options)       flags)         per dealer)  last good run)    alerts)
```

- **One run per Chicago calendar date at 06:00**, with the digest by 07:30. Timestamps are stored in UTC.
- **Every dealer ends each run in a final status:**
  - `REPORTED`: read successfully. This includes a page that genuinely lists zero matching cars.
  - `PARTIAL`: some pages were read and some failed.
  - `NOT_REPORTED`: the read failed. No vehicle rows are written, so a failure never shows up as zero stock.
  - `NO_SOURCE`: the dealer has no approved public inventory page.
- **Changes are measured against that dealer's last fully read run.** A car that disappears is marked `MISSING_UNVERIFIED`, never sold.
- **Prices are stored in whole cents, and NULL means unknown.** Dealers are compared on the unconditional advertised cash price only, never on MSRP, a monthly payment, or a conditional rebate.
- **Features need evidence.** Driving Assistance Professional counts as present only with option `ZDY` or `5AU`, or the exact package name. The standard `5AS` Driving Assistant does not count. M Sport (`ZMP`) cars stay in scope and are flagged.
- **Fetching is polite and blocks are respected.** Requests are GET and HEAD only, robots.txt is checked on every run, and redirects must stay on the dealer's allowlist. There is no CAPTCHA solving, no proxies, and no evasion of bot blocking: a block is recorded as `NOT_REPORTED`.

## Tech stack

Python 3.12 (managed with [uv](https://docs.astral.sh/uv/)) · SQLite in WAL mode · httpx and Playwright for fetching · selectolax for parsing · SQLAlchemy and Alembic · Typer CLI · FastAPI for the screen · optional LangGraph. Development tools: pytest, ruff, mypy and pre-commit.

## Setup

```bash
git clone https://github.com/selchandru6/autonomo-car-agent.git
cd autonomo-car-agent
uv sync                      # creates .venv with Python 3.12 and all dependencies
cp .env.example .env         # then edit; see below
```

`.env` holds the only two secrets the system uses. Both are optional:

| Variable | If left empty |
| --- | --- |
| `CARSHOP_MODEL_API_KEY` | Runs fully deterministic; ambiguous package text is stored as `UNKNOWN`. |
| `CARSHOP_SMTP_URL` | The digest is written to `data/digests/` instead of being emailed. |

Runtime data lives in `./data` and is git-ignored: the SQLite database, gzipped page snapshots, backups, and digests.

## Usage

*Planned, not yet implemented.* The CLI entry point is `carshop` (see `pyproject.toml`).

```bash
uv run carshop --help
```

## Development

```bash
uv run pytest                # tests; network access is blocked by default
uv run pytest -m gate        # section 13 must-pass release gates
uv run ruff check . && uv run mypy
```

Tests must never hit live dealer sites. Tests marked `live` are a separate, rate-limited smoke check, never the accuracy gate.

## Before the first live run

As [section 16](doc/requirements.md#16-still-needed-from-the-owner) of the requirements says, no dealer site is fetched until the owner provides:

1. The Phase 1 dealer rows: legal name, BMW center number, city, hostname, inventory URL, path prefix, website platform, and a terms decision.
2. The digest recipient addresses.
3. Confirmation of the option and powertrain codes (`23EU`, `ZDY`, `5AU`, `5AS`, `ZMP`) on one official 2026 X5 window sticker.

## Project layout

```
doc/requirements.md   the requirements baseline (v1.2)
src/carshop/          adapters, config, domain, export, extract, fetch, observability,
                      pipeline, reference, review, safety, storage, web
pyproject.toml        dependencies and ruff/mypy/pytest configuration
.env.example          environment template
```
