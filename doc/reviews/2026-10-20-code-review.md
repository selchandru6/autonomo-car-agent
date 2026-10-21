# Code review: carshop @ 8a20b6b (2026-10-20)

**Reviewer:** Claude (reviewer/discriminator role; Cursor is the implementer)
**Spec:** [doc/requirements.md](../requirements.md) v1.3. Section numbers (§) and requirement IDs (F1–F25) refer to it.
**Inputs reviewed:**
- every file under `src/carshop/`, `deploy/`, `.env.example`;
- the live database `data/app.db`, run `20261020T052908Z-manual_date-a78f54`, the statewide run of 27 dealers;
- the test suite: 86 passed, mypy clean, ruff 1 warning.

## How to use this document

1. Work the findings in the order given in [Work order](#work-order). Each finding has a stable ID (C1, H3, M7…). Put the ID in the commit message and the test name, for example `test_C2_page_cap_is_partial`.
2. Every finding gives the location, the requirement it breaks, a reproduction, the expected behavior, a suggested fix, and the test to add. Items marked ✔ were reproduced by running the code or querying the live DB, not just read.
3. **Do not edit `doc/requirements.md` to make a finding go away.** If you think a finding is wrong, write the reason under that finding's heading in this file, and the owner decides.
4. One finding (C4) needs an **owner decision** before you implement it. Build the recommended default only after the owner confirms.
5. Add a regression test for every ✔ finding. Put pure-function cases in `tests/unit/`, and pipeline/DB cases in `tests/gates/` with the `gate` marker.

Run the reproductions from the repo root with:

```bash
PYTHONPATH=src .venv/bin/python -B -c '<snippet>'
```

## Status of the first review (2026-10-19)

That review raised 20 findings. Of those, 3 are resolved, 1 can be closed by documenting it, and **16 are still open** (15 unfixed, 1 partially fixed). The open ones are carried into this document under new IDs:

| Old # | Topic | Status | New ID |
| --- | --- | --- | --- |
| 1 | Dealers marked `owner_accepted_risk` without the owner | Resolved by spec v1.3 (blanket approval) | — |
| 2 | Browser UA hides automation | **Partial**: `identified` default added; fake OS and `chrome` mode remain | H5 |
| 3 | “Conditional Price” / “Final Price” become comparison price | **Open** | C5 |
| 4 | `model="X5 xDrive40i"` → `NOT_X5` | **Open** (adapters mask it today) | H6 |
| 5 | Year rules inverted | **Open** | H7 |
| 6 | `CLASSIFICATION_CONFLICT` excluded instead of reviewed | **Open** | H8 |
| 7 | Payment with a generic label becomes the advertised price | **Open** | H1 |
| 8 | Unknown price label → `ADDON` | **Open**, now seen in live data | H1 |
| 9 | Sticker absence → NO for every feature | **Open** | H9 |
| 10 | Feature merge drops the sticker evidence | **Open** | M5 |
| 11 | Color/upholstery codes guessed by prefix | **Open** | M6 |
| 12 | “Demo” ≤ 500 miles excluded | **Open** | M7 |
| 13 | Comma-separated env lists crash `Settings` | **Open, worse**: `.env.example` as shipped crashes | C1 |
| 14 | DAP YES from upsell text and lowercase `zdy` | **Open** | H10 |
| 15 | Missing VIN labeled `VIN_CHECKSUM_FAIL` | **Open** | M8 |
| 16 | `compute_delta` claims an assertion it doesn't make | **Open** | M9 |
| 17 | Any availability text → `ON_LOT` | **Open** | M10 |
| 18 | `Dealer` missing `robots_status` / `verified_at` | Resolved (columns in `dealers` table) | — |
| 19 | `Trigger.REPLAY` not in spec | Accept; document it in an ADR | L2 |
| 20 | mypy/ruff failures | Resolved; one new ruff warning | L1 |

## Live run summary (for context)

- The locator returned 27 Texas dealers: **17 `REPORTED`**, 2 `NOT_REPORTED`, and 8 `NO_SOURCE`. That is 63% of the denominator, against the §8 target of 90%.
- **104 in-scope X5 xDrive40i** were stored, priced $74,475–$93,774. DAP status: 23 YES, 81 UNKNOWN, 0 NO.
- **Verified correct:**
  - every DAP YES quotes the car's own detail-page package list;
  - no `23EU` VIN with year `T` was wrongly excluded;
  - all 5 `DEMO_MILEAGE` exclusions are real (629–9,546 miles).
- 7 of the 8 `NO_SOURCE` dealers have **no website in the locator**: South Austin, Beaumont, Lubbock, McKinney, Plano, Clear Lake, and Wichita Falls. This is operator work (overrides), not a code bug. See L3.

---

## Critical

### C1 ✔ Copying `.env.example` to `.env` makes the app crash on startup
- **Where:** [settings.py:38](../../src/carshop/config/settings.py#L38) (`digest_recipients`) and the validator at line 94 (`digest_recipients`, `locator_seed_zips`).
- **Requirement:** the README setup steps; §7.10.
- **Repro:**
  ```python
  # with .env.example copied unchanged to /tmp/env.test
  from carshop.config.settings import Settings
  Settings(_env_file="/tmp/env.test")
  # -> SettingsError: error parsing value for field "digest_recipients" from source "DotEnvSettingsSource"
  ```
  Even the empty `CARSHOP_DIGEST_RECIPIENTS=` fails, and `CARSHOP_DIGEST_RECIPIENTS=a@x.com,b@y.com` fails too. pydantic-settings JSON-decodes `list[...]` fields before any `mode="before"` validator runs.
- **Expected:** an empty value gives `[]`, and a comma list gives a list. The same applies to `CARSHOP_LOCATOR_SEED_ZIPS=76801,79901,78520`.
- **Fix:** annotate both fields with `Annotated[list[str], NoDecode]` (pydantic-settings ≥ 2.7, which is installed at 2.16) and keep the split validator. Treat an empty string as `[]`.
- **Test:** load `Settings(_env_file=<copy of .env.example>)` in a unit test, so the shipped template can never break again.

### C2 ✔ Hitting the 30-page cap is reported as a complete read
- **Where:** [dealer.py:402-403](../../src/carshop/pipeline/dealer.py#L402-L403) (HTTP path) and [dealer.py:464](../../src/carshop/pipeline/dealer.py#L464) (browser path, which has no cap check at all).
- **Requirement:** F2 (stop at 30 pages), F19 (an unfinished page strategy yields `PARTIAL`), §5.3.
- **Evidence:** live DB, `bmw-permian-basin`: `pages_expected=1`, `pages_read=30`, status `REPORTED`. At the cap the code sets `pages_expected = cap`, which equals `pages_read`, so the `pages_read < pages_expected` gate never fires.
- **Expected:** if a next link still exists after the cap-th page, the dealer is `PARTIAL / PAGINATION_INCOMPLETE`. The browser path does the same.
- **Fix:** track a `truncated` flag on `_Acc`. Set it when the loop exits with `nxt` still non-empty, in both paths. In the completeness gates, `if acc.truncated: return finish(PARTIAL, PAGINATION_INCOMPLETE)`.
- **Test:** a fake fetcher that always returns a page with a new `next` URL. Assert 30 requests and status `PARTIAL`.

### C3 ✔ The same page was fetched 30 times; the loop guard only compares URLs
- **Where:** [dealer.py:360](../../src/carshop/pipeline/dealer.py#L360) and [dealer.py:464](../../src/carshop/pipeline/dealer.py#L464) (`nxt not in visited`).
- **Requirement:** F2 (the page strategy stops on its own condition), §7.4 politeness.
- **Evidence:** Permian Basin: 30 GETs, but only **9 distinct VINs** across 270 exclusion rows. The next link contained a literal `&amp;`, so the server ignored the page parameter and returned page 1 every time. The `&amp;` unescape is fixed in `generic_jsonld.py:171`, but nothing detects repeated *content*.
- **Expected:** stop when a page's content repeats. Treat it as the end of pagination if the page adds no new VINs, or as `PAGINATION_INCOMPLETE` if the site claimed more pages.
- **Fix:** keep a set of page `sha256` values (already computed by `ArtifactStore.put`) and a set of VINs seen. Break when the hash repeats or a page adds zero new VINs.
- **Test:** a fake site whose `?page=N` always returns identical HTML. Assert at most 2 requests.

### C4 ✔ Comparison prices are not like-for-like across dealers (**owner decision needed**)
- **Where:** [pricing.py](../../src/carshop/domain/pricing.py) (`comparison_price_cents`) and the per-adapter price extraction.
- **Requirement:** §7.6 (fees and add-ons stored separately; comparison is the unconditional advertised cash price), §11 (floor and price band compare across dealers).
- **Evidence (live DB):**

  | Dealer | Label stored as ADVERTISED | Price vs MSRP |
  | --- | --- | --- |
  | Arlington | Sale Price | MSRP + $599 tint + $225 doc fee |
  | Autobahn | Total Price | MSRP + $499 accessories + $750 service fee + $225 doc fee |
  | El Paso, Tyler, Houston Midtown, Dallas, Pharr, Austin | Sale/Internet/Selling/Our/“Bert Ogden” Price | MSRP + $225 doc fee |
  | Grapevine | (none; only “MSRP with Accessories”) | comparison NULL for all 8 cars |

  So the floor-by-dealer view and the shortlist rank partly on fees, with skews up to about $1,500.
- **Owner decision:** choose one basis.
  - **(a) Recommended: vehicle-only.** Subtract fees and add-ons that the page itemizes as included in the advertised figure. Store `price_basis` (`vehicle_only` | `includes_fees` | `unknown`). Compare only on `vehicle_only`, and show the others with a label.
  - **(b) Fees-included.** Compare all-in prices, and require each adapter to add itemized fees where the page excludes them.
- **Until decided:** add the `price_basis` column and fill it, but don't change `comparison_price_cents`.
- **Test:** golden fixtures for Autobahn-style “Total Price” and Arlington-style “Sale Price incl. doc fee” assert the stored `price_basis`.

### C5 ✔ A conditional or after-rebate price becomes the comparison price (was #3)
- **Where:** [pricing.py:88](../../src/carshop/domain/pricing.py#L88). `key.endswith("price")` classifies any label ending in “price” as ADVERTISED.
- **Requirement:** §7.6, and the §13 gate “Conditional rebates and payments are not the comparison price”.
- **Repro:**
  ```python
  from carshop.domain import pricing
  p = pricing.build_price_set([("MSRP","$80,000"),("Conditional Price","$77,250")])
  p.comparison_price_cents(5_500_000, 12_000_000)   # -> 7725000  (wrong; expected None + CONDITIONAL_ONLY)
  p = pricing.build_price_set([("MSRP","$80,000"),("Final Price","$74,995")])
  p.comparison_price_cents(5_500_000, 12_000_000)   # -> 7499500  (“Final Price” is usually after rebates)
  ```
- **Fix:** check conditional terms before advertised: add `conditional`, `after`, `with (all )?(offers|incentives|rebates)`, `final`, and `net of` to the conditional matcher. Replace the generic `endswith("price")` with an explicit allowlist of advertised labels per adapter, or at least an exact-match list.
- **Test:** a parametrized unit test over these labels, plus the live labels in C4 and H1.

---

## High

### H1 ✔ Price labels are misclassified: payments and unknown labels (was #7, #8)
- **Where:** [pricing.py:77-90](../../src/carshop/domain/pricing.py#L77-L90) (only the label is inspected) and [pricing.py:107-109](../../src/carshop/domain/pricing.py#L107-L109) (unknown label → `ADDON`).
- **Requirement:** §7.6 (a payment is flagged `PRICE_IS_PAYMENT` and never becomes the comparison price; components are distinct).
- **Repro:**
  ```python
  from carshop.domain import pricing
  pricing.build_price_set([("Price","$899/mo")]).components            # ADVERTISED 89900, no PRICE_IS_PAYMENT
  pricing.build_price_set([("Internet Special","$74,995")]).components # ADDON 7499500, flagged PRICE_MISSING
  ```
- **Live evidence:**
  - `"Transparent PriceNo Hidden Fees"` at $77,820–$84,840 (Momentum, West Houston) was stored as **ADDON**. It is the dealer's real price.
  - `"Or Finance For"` at $2,136–$2,190 (Tyler) was stored as **ADDON**. It is a payment.
- **Fix:**
  1. Classify the value text as well as the label: `/mo`, `per month`, `finance for`, `lease for`, `due at signing` → PAYMENT.
  2. Treat an unknown label with a dollar figure as `UNCLASSIFIED`. Add a new `PriceKind` and flag `PRICE_LABEL_UNKNOWN` instead of silently choosing ADDON, and count these in `dealer_runs` so a layout change is visible.
  3. Add `transparent price` to the advertised list. Labels arrive concatenated (`PriceNo Hidden Fees`), so normalize whitespace at element boundaries in the adapter.
- **Test:** a unit test per label above. Also a golden page per adapter asserting no `ADDON` over $5,000.

### H2 robots.txt is checked only for the start URL, and a 5xx is treated as “allow”
- **Where:** [dealer.py:121-124](../../src/carshop/pipeline/dealer.py#L121-L124) (`evaluate(..., dealer.start_url, ...)` once) and [robots.py](../../src/carshop/safety/robots.py) (`http_status >= 400` → allow-all).
- **Requirement:** F24 (robots enforced on **every** fetch) and §7.4.
- **Problem:**
  - Pagination URLs, detail pages (`/viewdetails/...`, `/new-vehicles/...vin`) and browser navigations are never checked against the robots rules.
  - A robots.txt 5xx or network failure becomes allow-all. RFC 9309 §2.3.1.4 says an unreachable robots.txt (server or network error) means **complete disallow**, so the code comment misstates the RFC. A 4xx means allow-all, which is correct.
- **Fix:**
  - Parse robots once per dealer branch and pass the decision object in `BranchDeps`/`_Acc`. Check it before every `http.get` and every `browser.render` in `_fetch_listing_pages` and `_enrich`.
  - A disallowed detail URL skips enrichment for that car and records `robots_decision`. A disallowed pagination URL makes the dealer `PARTIAL`.
  - Treat 5xx and timeout as disallow, so the dealer is `NOT_REPORTED / ROBOTS_DISALLOWED`. The detail field should say “robots unreachable”.
- **Test:** robots disallows `/viewdetails/` and allows the listing. Assert the observations are stored without enrichment and the attempts show `robots_decision` = disallowed. Robots returns 503: assert `NOT_REPORTED`.

### H3 One dealer's storage exception aborts the run and leaves it `RUNNING`
- **Where:** [run.py:179-181](../../src/carshop/pipeline/run.py#L179-L181) (`asyncio.gather(...)` without `return_exceptions`). There is no `except` that marks the run `FAILED`.
- **Requirement:** §5.6 (one dealer's failure MUST NOT block the others), F6.
- **Problem:** `branch()` catches crashes in `run_dealer`, but not in `store_dealer_result`, `record_robots` or `compare_dealer`. Any exception there propagates out of `gather`, other branches are orphaned, and the run stays `RUNNING`. That blocks re-runs until `reclaim_stale_runs` releases it, about 85 minutes later.
- **Fix:**
  - Wrap the storage block in `branch()`. On error, roll back that dealer's transaction, then write a minimal `dealer_runs` row (`NOT_REPORTED`, reason `PARSER_FAILED`, detail “store failed: …”) in a fresh transaction.
  - Use `gather(..., return_exceptions=True)` as a backstop.
  - Wrap the whole run in `try/except` that calls `finish_run(..., FAILED)` before re-raising.
- **Test:** monkeypatch `repos.store_dealer_result` to raise for one dealer. Assert the other dealers are stored, that dealer is `NOT_REPORTED`, and the run is `PUBLISHED`.

### H4 The “dealer already in a RUNNING run” check is wrong
- **Where:** [repos.py:193-204](../../src/carshop/storage/repos.py#L193-L204) (`running_run_for_dealer`).
- **Requirement:** F10.
- **Problem:** `dealer_runs` rows are written only when a dealer **finishes**.
  - While a run has no finished dealers, the outer join's `dealer_id IS NULL` branch blocks **every** dealer.
  - Once one dealer finishes, dealers still in flight have no row, so a duplicate re-run of them is **allowed**.
- **Fix:** at admission, insert a `dealer_runs` row with `status='RUNNING'` for each target dealer. Allow `RUNNING` in the status column. Update that row when the dealer finishes, and make `reclaim_stale_runs` mark leftover `RUNNING` rows `NOT_REPORTED / TIMEOUT`. The check then becomes a plain `status='RUNNING' AND dealer_id=?`.
- **Test:** start a run of dealers A and B, and finish A only. A re-run of B is rejected, and a re-run of C is accepted.

### H5 The browser user-agent fakes the OS and still offers a no-identification mode (was #2, partial)
- **Where:** [browser.py:70-86](../../src/carshop/fetch/browser.py#L70-L86) and [settings.py:17](../../src/carshop/config/settings.py#L17) (`UaMode = Literal["identified", "chrome", "native"]`).
- **Requirement:** §8 Compliance (“identify the fetcher honestly in the HTTP and browser user agents”), F24 (“no … impersonation path exists”), §3 (no bot evasion).
- **Problem:**
  - Every mode except `native` hard-codes `Macintosh; Intel Mac OS X 10_15_7` and drops `Headless`, whatever the real platform is (Linux in Docker).
  - `chrome` mode sends that UA with no product token and no `From`/`X-Carshop` headers, which is a pure impersonation path.
- **Fix:**
  - Remove `chrome` from `UaMode`.
  - In `identified` mode, build the UA from the real one: take Playwright's default (`await browser.new_page()` → `navigator.userAgent`, or `browser.version` plus `platform.system()`), keep the `HeadlessChrome` token, and append the product token.
  - Update ADR 0002 to match.
- **Test:** assert the UA contains `HeadlessChrome` and `carshop/`, and that `Settings(browser_ua_mode="chrome")` fails validation.

### H6 ✔ Model text other than exactly `X5` is excluded as `NOT_X5` (was #4)
- **Where:** [classify.py](../../src/carshop/domain/classify.py), step 3 (`re.fullmatch(r"X5", model)`).
- **Requirement:** F4.
- **Repro:**
  ```python
  from datetime import datetime, UTC
  from carshop.domain.classify import classify, ClassifyContext
  from carshop.domain.models import RawListing
  from carshop.domain.codes import SourceKind
  ctx = ClassifyContext("d", datetime.now(UTC), "a1", 2026, "23EU", 500, 5_500_000, 12_000_000)
  base = dict(url="https://x/new-vehicles/a", page_hash="h", source_kind=SourceKind.LISTING,
              vin="5UX23EU0XT9529123", year=2026, trim="xDrive40i",
              full_text="2026 BMW X5 xDrive40i", price_pairs=[("Sale Price","$78,000")])
  classify(RawListing(model="X5 xDrive40i", **base), ctx).exclusion.code   # NOT_X5 (wrong)
  classify(RawListing(model="X5 Sports Activity Vehicle", **base), ctx).exclusion.code  # NOT_X5 (wrong)
  ```
  This doesn't happen in the live run only because the current adapters normalize `model` to `"X5"`. A new adapter, or a site change, would silently drop every car.
- **Live side effect:** 8 xDrive50e cars (`"X5 PHEV"`) were recorded as `NOT_X5` instead of `POWERTRAIN_OTHER`. See M11.
- **Fix:** test for X5 with `\bX5\b` on model + trim + title. Let the AG code decide the powertrain (step 4). Use `NOT_X5` only when no X5 token exists anywhere.
- **Test:** the repro above returns in-scope. `model="X5 PHEV"` with AG `43EU` returns `POWERTRAIN_OTHER`.

### H7 ✔ The year rules are reversed (was #5)
- **Where:** [classify.py](../../src/carshop/domain/classify.py), step 3 (the `raw.year != ctx.model_year` and `info.year != ctx.model_year` branches).
- **Requirement:** §7.2: “VIN position 10 … T means 2026. A page year that disagrees is `CLASSIFICATION_CONFLICT`.”
- **Repro:** use `RawListing(year=2025, ...)` with VIN year `T`. You get `NOT_MY2026`, but expected is `CLASSIFICATION_CONFLICT`. Conversely, a VIN with year ≠ `T` and no page year gives `CLASSIFICATION_CONFLICT`, where expected is `NOT_MY2026`.
- **Fix:** the VIN decides scope. If the VIN year is not 2026, exclude as `NOT_MY2026`. If the VIN year is 2026 and the page year is present and different, it's a conflict (see H8).
- **Test:** both cases above.

### H8 ✔ `CLASSIFICATION_CONFLICT` and VIN failures skip the review queue (was #6)
- **Where:** [classify.py](../../src/carshop/domain/classify.py) (conflicts return `_excl(...)`), [codes.py](../../src/carshop/domain/codes.py) (`ExclusionCode.CLASSIFICATION_CONFLICT`), and [dealer.py](../../src/carshop/pipeline/dealer.py) (review only for `VIN_CHECKSUM_FAIL` / `CLASSIFICATION_CONFLICT` exclusions).
- **Requirement:** §7.3 lists `CLASSIFICATION_CONFLICT` as an **in-scope flag**. §7.9 sends it to `PENDING_REVIEW`, so it must not be published as `VALIDATED` and must not be silently dropped.
- **Problem:** a conflicted car becomes an exclusion row. Charts never see it, which is right, but it also never becomes an observation that an operator can approve. Approving it in `/review` changes `inventory_staging` for a row that doesn't exist.
- **Fix:**
  - On conflict (page year ≠ VIN year, or AG `23EU` with no or contradicting xDrive40i quote), build the observation with flag `CLASSIFICATION_CONFLICT` and `needs_review="CLASSIFICATION_CONFLICT"`. Staging then becomes `PENDING_REVIEW`.
  - Remove `CLASSIFICATION_CONFLICT` from `ExclusionCode`, which also brings it in line with §7.3.
  - A VIN checksum failure stays an exclusion, but it still creates a `review_items` row (§7.9). That already happens; keep it.
- **Test:** a page year conflict produces one observation with staging `PENDING_REVIEW` and one open review item. Approving it makes the car appear in `/inventory`.

### H9 ✔ A sticker that doesn't mention a feature sets that feature to NO (was #9)
- **Where:** [features.py](../../src/carshop/domain/features.py), `evaluate_features` (the `elif is_parsed_sticker_options and source_kind is STICKER` branch).
- **Requirement:** §7.7 (“Do not infer one package from another”). The only absence-means-NO rule in the spec is for DAP.
- **Repro:**
  ```python
  from carshop.domain.features import evaluate_features
  from carshop.domain.codes import SourceKind
  stk = evaluate_features("Options: 5AS Driving Assistant; 4FM", source_kind=SourceKind.STICKER,
                          url="s", page_hash="h", is_parsed_sticker_options=True)
  [(f.feature.value, f.status.value) for f in stk]   # M_SPORT NO, MASSAGE NO, PANORAMIC_ROOF NO, ...
  ```
- **Fix:** for every feature except DAP, sticker absence gives `UNKNOWN / INSUFFICIENT`. Allow NO only when the sticker's option list is parsed **and** an owner-confirmed option code for that feature exists in reference data (none are confirmed yet; see §16.2).
- **Test:** the repro gives NO for DAP only.

### H10 ✔ DAP YES from upsell text and lowercase codes (was #14)
- **Where:** [features.py](../../src/carshop/domain/features.py) (`_DAP_YES` compiled with `re.I`) and [classify.py](../../src/carshop/domain/classify.py) (`feature_text` = `package_text` + whole `full_text`).
- **Requirement:** §7.7 (vehicle-specific evidence), and the §13 gate “zero unsupported YES”.
- **Repro:**
  ```python
  evaluate_features("Optional: add Driving Assistance Professional Package for $1,700",
                    source_kind=SourceKind.LISTING, url="u", page_hash="h")[0].status   # YES (wrong)
  evaluate_features("Option codes: zdy", source_kind=SourceKind.LISTING, url="u", page_hash="h")[0].status  # YES
  ```
  Every live YES was legitimate, but nothing prevents false ones.
- **Fix:**
  - Match option codes case-sensitively (`\bZDY\b`, `\b5AU\b`, `\bZMP\b`).
  - Keep the phrases case-insensitive.
  - Evaluate DAP only against the car's own option/package list (`package_text`, detail-page “Packages/Options” blocks), not `full_text`.
  - Reject matches whose window contains upsell cues (`add`, `available`, `optional`, `upgrade to`, `for $`).
- **Test:** both repros give UNKNOWN. The five live quote shapes (Arlington, Austin, Corpus Christi, Grapevine, Pharr) still give YES. Add them as golden cases.

---

## Medium

### M1 Re-processing a run duplicates rows
- **Where:** [repos.py:305-325](../../src/carshop/storage/repos.py#L305-L325) and the `review_items` inserts later in `store_dealer_result`.
- **Requirement:** §13 Replay gate (“Reprocessing a run does not duplicate observations or emails”), §8 Concurrency (idempotent at the database).
- **Problem:** observations are skipped when they already exist, but `fetch_attempts`, `exclusions` and `review_items` are inserted every time.
- **Fix:** add unique indexes on `exclusions(run_id, dealer_id, coalesce(vin,''), url, reason_code)` and `review_items(run_id, dealer_id, coalesce(vin,''), issue)`. Use `INSERT … ON CONFLICT DO NOTHING`. Skip `fetch_attempts` on replay (they record history), or key them on `(run_id, dealer_id, url, attempt, renderer)`. Ship it as a new Alembic migration.
- **Test:** call `store_dealer_result` twice with the same result. All row counts are unchanged.

### M2 Stored observations are edited after insert
- **Where:** [repos.py:748](../../src/carshop/storage/repos.py#L748) (`add_flag_to_observations` UPDATEs `observations.flags`).
- **Requirement:** §7.2 (“Observation: immutable capture”), F22.
- **Fix:** record `TRANSFER_SUSPECTED` as a row in `listing_lifecycle` (or a small `observation_flags` table keyed by `observation_id`). The screen and digest join it in, and `observations` stays insert-only.
- **Test:** after `flag_cross_dealer`, the observation row's `flags` column is unchanged, and the flag is visible through the query the screen uses.

### M3 Review endpoint: no auth or CSRF, spoofable operator, no corrections
- **Where:** [app.py:338](../../src/carshop/web/app.py#L338) (`POST /review/{item_id}`).
- **Requirement:** §2 (Phase 1 read-only screen; shared access needs authentication), F22 (corrections recorded as new versions with operator id, old and new value, reason, evidence).
- **Problems:**
  - Any page open in the user's browser can POST to `127.0.0.1:8000/review/N`, which is CSRF.
  - `operator_id` comes from the form, so the audit trail can be spoofed.
  - Only VALIDATED/REJECTED exist. There is no field correction that inserts observation `version + 1` with `extraction_method="human"`.
  - `src/carshop/review/` is empty.
- **Fix:**
  - Move decisions to the CLI (`carshop review list|approve|reject|correct`), with the operator taken from `CARSHOP_OPERATOR_ID` or the OS user. Make the web review page read-only.
  - If web writes stay, add a CSRF token and a configured operator identity.
  - Implement `correct` per F22.
- **Test:** CLI `correct` creates version 2. Charts read v2, and v1 is untouched.

### M4 Comparison includes rows still pending review
- **Where:** [compare.py](../../src/carshop/pipeline/compare.py) (`today` built from all `result.observations`).
- **Requirement:** §7.9 (“At the cutoff, comparison and the digest use `VALIDATED` rows only”).
- **Fix:** build `today` from observations whose staging is `VALIDATED`. Treat pending VINs as present when computing *missing*, so a pending car doesn't fire a false MISSING.
- **Test:** a car with `PRICE_IMPLAUSIBLE` produces no price event and no missing event.

### M5 ✔ Feature merge drops one side of a conflict (was #10)
- **Where:** [features.py](../../src/carshop/domain/features.py) (`merge_feature_sets`).
- **Requirement:** F12 (“Conflicts are stored on both sides. The sticker does not silently overwrite the listing.”), F16.
- **Repro:** merging listing DAP YES with sticker DAP NO gives one row, `YES / CONFLICT`, with only the listing evidence URL.
- **Fix:** return both evidence rows, or one row with `status=UNKNOWN`, `sufficiency=CONFLICT` and both quotes. A conflicted DAP must not count as a shortlist match.
- **Test:** the repro yields two stored evidence rows (listing and sticker), and the shortlist treats DAP as not matched.

### M6 ✔ Color and upholstery codes are guessed (was #11)
- **Where:** [data.py:59](../../src/carshop/reference/data.py#L59) (prefix match) and [data.py:73](../../src/carshop/reference/data.py#L73) (`colour_only` map).
- **Requirement:** §7.7 (store the code “when it matches”; otherwise keep the raw text and flag `COLOR_UNMAPPED`).
- **Repro:**
  ```python
  from carshop.reference import data as ref
  [ref.match_exterior(x) for x in ["Black","M","Blue"]]    # ['475','A96','C35']  (all guesses)
  [ref.match_upholstery(x) for x in ["Coffee","Black"]]     # ['KPHF','KPSW']      (Merino also exists)
  ```
- **Fix:**
  - Match exterior names exactly after normalization (case, grey/gray, “Metallic” optional), or match a code token in the text.
  - Use no prefix matching and no `n.startswith(name)` either way round, unless `n` contains the full name.
  - Drop the `colour_only` map, or keep only entries that are unambiguous in the reference table (`cognac`, `silverstone`, `tartufo`, `ivory white`).
- **Test:** the repro gives `None` for all five. Full names still map.

### M7 ✔ A “Demo” under 500 miles is excluded (was #12)
- **Where:** [classify.py](../../src/carshop/domain/classify.py) (`if _DEMO_RE.search(cond): return _excl(..., DEMO_MILEAGE, ...)`).
- **Requirement:** §3 mileage table. New means an odometer that's NULL or ≤ 500. Demo or loaner means **odometer above 500**.
- **Fix:** decide a demo by mileage only. A demo label with ≤ 500 miles stays in scope. You may add an informational flag, but don't exclude.
- **Test:** `condition_text="Demo", odometer_miles=120` is in scope, and `odometer_miles=501` gives `DEMO_MILEAGE`.

### M8 A missing VIN is labeled `VIN_CHECKSUM_FAIL` (was #15)
- **Where:** [classify.py:76](../../src/carshop/domain/classify.py#L76).
- **Requirement:** §7.3. A listing that failed parsing makes the dealer `PARTIAL / PARSER_FAILED`. `VIN_CHECKSUM_FAIL` is for a VIN that is present but invalid.
- **Fix:** return a parse-failure signal. The branch counts it and marks the dealer `PARTIAL / PARSER_FAILED`, unless the card is clearly not a vehicle (promo tile). Don't write `VIN_CHECKSUM_FAIL`.
- **Test:** a page with one card missing its VIN gives dealer `PARTIAL`, and there's no `VIN_CHECKSUM_FAIL` row.

### M9 `compute_delta` doesn't enforce the REPORTED-only rule its docstring claims (was #16)
- **Where:** [lifecycle.py](../../src/carshop/domain/lifecycle.py) (docstring: “asserted here”).
- **Requirement:** §5.3, §7.8.
- **Fix:** add a `status: DealerStatus` parameter and `raise ValueError` unless it is `REPORTED`.
- **Test:** calling it with `PARTIAL` raises.

### M10 Any availability text marks a car as on the lot (was #17)
- **Where:** [classify.py:142](../../src/carshop/domain/classify.py#L142).
- **Fix:** set `ON_LOT` only on explicit phrases (`in stock`, `on lot`, `available now`). Anything else is `UNKNOWN`.
- **Test:** “Call for availability” gives `UNKNOWN`.

### M11 ✔ xDrive50e cars are excluded with the wrong reason
- **Evidence:** 8 live exclusions such as `5UX43EU03T9215953`, “New 2026 BMW X5 PHEV PHEV xDrive50e”, recorded as `NOT_X5`.
- **Requirement:** §7.2 (`43EU` → `POWERTRAIN_OTHER`).
- **Fix:** fixed by H6.

### M12 Docker re-runs always send the digest, and missed runs aren't caught up
- **Where:** [deploy/entrypoint.sh](../../deploy/entrypoint.sh) (`run)` → `carshop run --notify "$@"`) and [deploy/crontab](../../deploy/crontab).
- **Requirement:** F10 (a re-run does not email unless the operator passes notify), §7.10 (a missed run becomes one catch-up labeled `late`).
- **Fix:**
  - Pass `--notify` only on the scheduled path.
  - On scheduler start and at 07:00, if no scheduled run exists for today's Chicago date and it's past 06:00, start one run with `--trigger catchup`.

---

## Low

### L1 ruff W291
Trailing whitespace at [migrations/versions/20261019_44a0d1b83d4c_baseline_schema.py:4](../../migrations/versions/20261019_44a0d1b83d4c_baseline_schema.py#L4).

### L2 `Trigger.REPLAY` is not in the spec
Document it in an ADR (what it does, and that it never sends email), or remove it.

### L3 Operator overrides for the 7 dealers the locator lists without a website
**This is operator work, not code.** Add `overrides.yaml` entries for these center numbers:

| Center | Dealer |
| --- | --- |
| 44235 | South Austin |
| 53089 | Beaumont |
| 50857 | Lubbock |
| 52938 | McKinney |
| 50923 | Plano |
| 51029 | Clear Lake |
| 54314 | Wichita Falls |

Fix BMW of Temple (`LAYOUT_UNKNOWN`) and Cardenas (`OFF_DOMAIN`) as well. Together they would raise coverage from 63% toward the 90% target. Plano and McKinney may belong to group sites; per §7.1, their start URL must be filtered to the store.

---

## Work order

1. **C1, C2, C3.** These are small, mechanical fixes with high impact.
2. **C5, H1.** Price classification; one test file covers both.
3. **H2, H3, H4.** Safety and run integrity.
4. **H6, H7, H8, M7, M8, M10, M11.** Classifier pass.
5. **H9, H10, M5, M6.** Feature and color evidence.
6. **M1, M2, M4.** Idempotency and immutability.
7. **H5, M3, M12, L1, L2.**
8. **C4.** After the owner chooses the price basis.

When a batch is done, re-run the live statewide run and attach the per-dealer `dealer_runs` table and the price-label table, so the reviewer can re-verify C2, C3, C4 and H1 from data.

## What is good (keep it)

- **Dealer list:** locator import with a stored snapshot and hash.
- **Discovery:** automatic inventory-URL discovery (F25), and adapters switch when the page reveals its real platform.
- **Safe fetching:** redirects are validated hop by hop against the allowlist and SSRF rules, and retries are limited to 429, 503 and timeouts.
- **Failed reads:** they never write zero stock. Deltas run only for `REPORTED` dealers, and a silent zero is flagged.
- **Detail pages:** a demo hidden in the detail page is caught (El Paso, 9,546 miles).
- **Storage and operations:** per-dealer transactions, a verified backup after every run, the 07:30 external heartbeat, ADRs, and runbooks.
- **Evidence:** every live DAP YES traces to a vehicle-specific quote.
