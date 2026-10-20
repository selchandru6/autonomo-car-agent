# Runbooks

Short, operator-facing procedures. Each is written to be followed at 07:35 with coffee.

## 1. No digest arrived by 07:30

1. `uv run carshop status` — is there a run for today? What status?
   - No run: the scheduler did not fire. Check cron / the `scheduler` container logs. Run
     `uv run carshop run --trigger catchup --notify` to produce today's data late (it is
     labelled `late` in the digest).
   - `RUNNING` for more than ~1 h: the process died. The next run reclaims it automatically
     (marked `FAILED`, label `reclaimed_stale`, audited). You can start the catch-up now.
   - `FAILED`: read the log (`data/logs` or container stdout), fix, then catch-up.
2. If the run is `PUBLISHED` but no email arrived: `uv run carshop digest <run_id>` prints the
   text; check `notification_events` on the Runs page for the provider error. With no
   `CARSHOP_SMTP_URL` the digest is a file in `data/digests/`.

## 2. A dealer is NOT_REPORTED

Open the Health page and read the reason code.

| Code | Meaning | Action |
| --- | --- | --- |
| `HTTP_403` / `CAPTCHA` | The site blocked us, including the browser render | Nothing today. If it persists 3 days, open the dealer page manually; if it needs a challenge, mark the dealer `terms_status: disallowed` or wait. |
| `ROBOTS_DISALLOWED` | robots.txt now forbids the path | Respect it. Look for a different allowed start URL; otherwise `NO_SOURCE`. |
| `UNRENDERED_SHELL` / `LAYOUT_UNKNOWN` | The page changed shape | Fetch the stored snapshot from `data/snapshots/<run>/`, update the adapter, add a golden fixture, re-run that dealer. |
| `PAGINATION_INCOMPLETE` (PARTIAL) | Page 1 read, later pages failed | Check the browser is installed (`uv run playwright install chromium`); re-run the dealer. |
| `OFF_DOMAIN` / `SSRF_BLOCKED` | A redirect left the allowlist | Inspect `fetch_attempts.final_url`. Do not widen the allowlist without reading where it went. |
| `COUNT_ANOMALY` | A positive count dropped to a rendered zero | Open the site by hand. If genuinely empty, nothing to do; the next REPORTED run resolves it. |

PARTIAL and NOT_REPORTED days never emit "missing" events, so there is nothing to undo.

## 3. A dealer is NO_SOURCE (fix with an override)

Dealers are never added or removed by hand: the registry is the BMW USA locator for Texas.
`NO_SOURCE` means discovery (F25) found no public inventory URL. The Health page shows why:

| Discovery reason | Meaning | Action |
| --- | --- | --- |
| `no_hostname` | The locator prints no website for this center | Find the dealer's official site yourself and add an override with `hostname` (and usually `start_url`). |
| `homepage_unreachable` | TLS/DNS/timeout on the homepage | Try the site in a browser. If it works for you, add an override with the start URL; the run will still verify robots and the allowlist. |
| `homepage_blocked` | 403 / CAPTCHA on the homepage | Nothing today. If it persists, the dealer stays a named gap. |
| `no_inventory_link` | Homepage and sitemap had no verifiable new-X5 page | Open the site, find the new-X5 inventory page, add an override with `start_url` (and `platform`). |
| `robots_disallowed` | robots.txt forbids every candidate | Respect it. The dealer is a named gap. |

Override procedure (privileged edit, requirements §3):

1. Edit `src/carshop/config/registry/overrides.yaml`. Key by `center_number` (from
   `carshop registry show`). Set only what discovery got wrong: `hostname`, `start_url`,
   `path_prefixes`, `platform`, `sticker_hosts`. `operator` and `set_at` are required.
2. For a group site (Sewell, Park Place) the start URL **must** be filtered to that store so
   one store's cars are not stored under another.
3. `uv run carshop registry refresh --only-missing` (keeps verified dealers, applies the
   override, audits it), then `uv run carshop run --dealer <id>`.
4. If the platform has no adapter (`LAYOUT_UNKNOWN` after the run), write one in
   `carshop/adapters/`, register it in `detect.py`, and add a golden fixture.

## 3b. Monthly registry refresh

The run refreshes the registry itself when the snapshot is older than 30 days. To force it:
`uv run carshop registry refresh`. Read the summary: `added` (new on the locator), `left the
locator (now inactive)`, and `NO_SOURCE` with reasons. History for inactive dealers is kept.

## 4. Parser change

1. Reproduce offline: copy the stored snapshot into `tests/golden/pages/` (strip anything
   personal; the artifact store already scrubs chat widgets).
2. Change the adapter; run `uv run pytest tests/golden`. Review the diff of the expected
   JSON by hand before updating it — a silent change in exclusions is the bug you are looking for.
3. Bump the adapter `version` string. Observations record it, so old rows stay attributable.

## 5. Restore from backup

```sh
uv run carshop backup                     # take one more, just in case
ls data/backups/                          # pick a *.db file
cp data/backups/<file>.db data/carshop.db
uv run carshop migrate-db                 # no-op if current
uv run carshop status
```

Backups are `VACUUM INTO` copies verified with `PRAGMA integrity_check` and row counts at
creation time.

## 6. Turn the LLM step on or off

Set or unset `CARSHOP_MODEL_API_KEY`. Off: `model_status=down`, ambiguous features
`UNKNOWN`. On: at most $0.15 per run, $5 per month; the breaker trips to `down` on overrun and
the run continues deterministically. Every model-derived `YES` still needs an exact quote found
in the page text, or it is discarded.

## 7. Rotate secrets

Only two exist: `CARSHOP_MODEL_API_KEY`, `CARSHOP_SMTP_URL`. Change them in the environment
(or `.env`) and restart `serve` / the scheduler. Nothing is stored in the database or logs;
`SecretStr` redacts them in any dump.
