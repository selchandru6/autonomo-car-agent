# AI car shopping — requirements

**Product:** AI car shopping  
**Subject:** New 2026 BMW X5 xDrive40i listings at every Texas BMW dealer  
**Status:** Implementation baseline  
**Date:** 2026-10-19  
**Supersedes:** the original agentic MAS design, revision 1.1, and revision 1.2 of the same date

This file is the single requirements baseline. It merges both design documents and applies the architecture review. Where those sources disagreed, this file states the rule to build.

Words **MUST**, **MUST NOT**, and **SHOULD** are normative. A value marked **baseline** is the rule to implement. The owner can change a baseline value in configuration without a schema change. Section 16 lists the few inputs this file cannot invent.

The goal is every in-scope car at every Texas BMW franchise dealer, stored in the database. No Texas BMW dealer is excluded by an approval step. The registry is every Texas dealer on the BMW USA dealer locator, and each one is attempted on every run.

Coverage is still reported honestly. A dealer whose site could not be read is shown as not read, not as having zero cars. The system claims statewide completeness for a run only when every Texas dealer on the registry is `REPORTED`.

## 1. Purpose

The system finds new model-year 2026 BMW X5 xDrive40i vehicles advertised by every Texas BMW franchise dealer, keeps an auditable history of each listing, and shows price and availability on one screen plus a dated email digest.

A buyer can see where advertised prices sit, which dealer’s floor moved, how stock changed, which VINs appeared or left the listing, and which in-scope cars best match saved preferences. Preference fit and evidence confidence are separate.

Out of scope for the product: contacting a dealer, submitting a lead, reserving, financing, or buying. There is no price forecast and no claim that a delisted VIN was sold.

## 2. Roles

| Role | Responsibility |
| --- | --- |
| Owner | Approves scope and phase gates. The owner has given blanket approval for every Texas BMW franchise dealer (section 7.1), so there is no per-dealer approval step. |
| Operator | Runs the schedule, labels the golden set, and clears the review queue. |
| Buyer | Reads the digest and the screen. |

Phase 1 is a single operator and a read-only screen. Shared access, if added later, requires authentication. Values in the registry come from the locator and from discovery (section 7.1). A manual override of a dealer hostname, path prefix, or sticker host is a privileged edit, stored with operator id and time.

## 3. Scope

### In scope

- New model-year 2026 BMW X5 xDrive40i, including cars that carry the M Sport package (option **ZMP**).
- In-transit new units of that car, flagged `IN_TRANSIT`.
- Every Texas BMW franchise dealer on the BMW USA dealer locator, and their vehicle detail pages.
- Official window stickers linked from those pages when the sticker host is on that dealer’s allowlist.
- A dealer registry, daily collection, append-only history, exception review, a trends screen, a buyer shortlist, CSV and XLSX export, and a dated digest.
- Third-party pages only as manual leads. A fact used in storage MUST be corroborated on a registry dealer’s own page or an allowlisted sticker.

### Out of scope

- sDrive40i, xDrive50e and other PHEVs, M60i, X5 M, certified pre-owned, used, demonstrators over the mileage cut, sale-pending units, and model year 2027 or later.
- Excluding an xDrive40i because it has the M Sport package. ZMP is a flag, not an exclusion. Option line **337** is a content line of ZMP, not a separate model.
- Oklahoma, Arkansas, or any other out-of-state inventory, until the owner adds it as a scope change.
- Automated contact, chat, “check availability,” schedule, lead forms, and purchase flows.
- Bot evasion: CAPTCHA solving, residential proxies, and TLS fingerprint impersonation.
- Treating a monthly payment, a conditional rebate, or MSRP as the advertised cash price.

### Mileage and condition

| Condition | Rule |
| --- | --- |
| New | Odometer is NULL or at most **500 miles** (baseline). |
| In transit | In scope, flagged `IN_TRANSIT`. |
| Demo or loaner | Odometer above 500 miles. Excluded with `DEMO_MILEAGE`. |
| Sale pending | Excluded with `SALE_PENDING`. |
| Used or CPO | Excluded with `USED_OR_CPO`. |

## 4. Locked decisions

These were open or contradictory in the source designs. This baseline closes them.

| Topic | Rule |
| --- | --- |
| M Sport | In scope. Store `PKG_M_SPORT` when ZMP or the phrase “M Sport Package” is evidenced. The screen can filter it. Ingestion does not drop it. |
| Dealer scope | Every Texas BMW franchise dealer on the BMW USA locator. The owner gave blanket approval on 2026-10-19, so no dealer waits on a per-dealer terms decision. Robots and the no-evasion rules still apply to every site. |
| Coverage claim | The denominator is every active Texas dealer on the registry. A dealer with no discoverable public inventory page is `NO_SOURCE`, not an invisible success. |
| Failed read versus true zero | `NOT_REPORTED` writes no unit rows. A rendered explicit empty inventory is `REPORTED` with `unit_count` 0. |
| Silent zero | A dealer that had units and now shows a rendered zero with no HTTP error is `REPORTED`, count 0, plus `SCRAPE_ANOMALY_SUSPECTED`. |
| Comparison price | Unconditional advertised cash price, integer cents, else NULL. Never substitute MSRP. |
| Implausible price | Below **$55,000** or above **$120,000** (baseline band) is `PRICE_IMPLAUSIBLE` and is not a trend point. |
| DAP unknown | A finished value. It does not pause the run and does not drop the car. |
| DAP = NO | Allowed only from a parsed sticker option list that lacks ZDY, 5AU, and the exact package phrase. |
| Human review | Non-blocking. Clean dealers publish at the cutoff. |
| Raw HTML | Claim-check on disk. Graph state holds pointers and hashes. |
| Schedule | One run per America/Chicago civil date, 06:00. Digest by 07:30. Store timestamps in UTC. |
| Agents | Two model-using steps at most: ordering in the supervisor, and ambiguous package text in the extractor. Every other step is deterministic. |
| Anti-bot bypass | Forbidden. A block is `NOT_REPORTED`. |
| Buyer “when to buy” | Not a forecast. The shortlist ranks current listings. A dealer floor is not a same-VIN markdown. |

## 5. Success criteria

1. Every active Texas registry dealer on a scheduled run ends `REPORTED`, `PARTIAL`, `NOT_REPORTED`, or `NO_SOURCE`, with a machine-readable reason when the result is not a clean report.
2. An eligible VIN is stored once per dealer per run. The same VIN at two dealers is two observations.
3. A failed or partial read MUST NOT create a removal, a sale, or a zero-unit inventory point.
4. Every price and every YES/NO feature used on the screen or in the digest has source evidence.
5. The screen, the export, and the digest reconcile to the same published run and show stale or missing dealers.
6. One dealer’s failure MUST NOT block storage or the digest for the other dealers.

## 6. Functional requirements

| ID | Requirement | Acceptance check |
| --- | --- | --- |
| F1 | Build the dealer registry automatically from a BMW USA dealer-locator snapshot for Texas, and refresh it monthly. Every Texas dealer on the locator is added, with no approval step. | Each active dealer has a stable id, BMW center number, official hostname from the locator, inventory start URL, path prefix, platform, robots status, and last-verified date. A dealer that appears on the locator is added on the next refresh. A dealer that leaves the locator is marked inactive, and its history is kept. |
| F2 | Fetch inventory and detail pages for every registry dealer, and allowlisted stickers. Follow only that dealer’s path prefix plus named sticker hosts. | Each expected page is fetched or given an incomplete reason. Pagination, load-more, and JSON strategies stop on their own condition or at 30 pages. Blocked access is not bypassed. |
| F3 | Extract VIN, stock, model, trim, year, condition, availability, exterior color, interior upholstery, odometer, packages, price components, source URL, and fetch time. | A missing field is NULL or `UNKNOWN`. Page-absent values are not inferred. VIN year and powertrain checks in F4 are allowed and store their method. |
| F4 | Classify each listing with the rules in section 7. Keep M Sport xDrive40i units eligible. | Every exclusion has a reason code and an evidence quote in `exclusions`. In-scope ZMP units stay in observations with `PKG_M_SPORT`. |
| F5 | Classify Driving Assistance Professional as `YES`, `NO`, or `UNKNOWN` using the sticker rules in section 7. | `YES` and `NO` cite vehicle-specific evidence. A highlights list that merely omits the package yields `UNKNOWN`. Standard 5AS is not DAP. |
| F6 | Record one run per scheduled civil date, and a dealer result for every registry dealer. | A failed dealer is never stored as zero units. A true empty inventory is `REPORTED` with count 0. `PARTIAL` does not generate removals. |
| F7 | Compare each dealer with its last `REPORTED` run, not with the previous calendar run and not with a one-dealer re-run of other dealers. | Added, missing, and price events match section 7. A disappearance is `MISSING_UNVERIFIED`, never a sale. |
| F8 | Show the views in section 11 from committed rows only. | A `NOT_REPORTED` or `PARTIAL` day is a gap in that dealer’s stock and floor series, not a zero. |
| F9 | Send one dated digest per published run to the named recipient list, plus an operator alert on read failures. | The body hash matches the stored run. Delivery is idempotent on `(run_id, recipient)`. A failed send retries without a new crawl. |
| F10 | Re-run one dealer or one snapshot date on demand. | The new run has its own `run_id`, `trigger`, and `rerun_of_run_id`. Prior rows stay. A re-run does not email unless the operator passes notify. A dealer already in a `RUNNING` run is rejected. |
| F11 | Publish a coverage report for the registry. | The report lists every Texas dealer with last success, reason, verification date, and the ratio of `REPORTED` dealers to active registry dealers. `PARTIAL` is not counted as reported. |
| F12 | Enrich a unit from its detail page and, when linked and allowlisted, its window sticker. | Conflicts are stored on both sides. The sticker does not silently overwrite the listing. |
| F13 | Deduplicate URLs that share a VIN at the same dealer in the same run. | One observation per `(run_id, dealer_id, vin)`. Alternate URLs are kept. Cross-dealer appearances stay distinct and raise `TRANSFER_SUSPECTED`. |
| F14 | Track listing lifecycle without inferring a sale. | States are `ACTIVE`, `MISSING_UNVERIFIED`, `DELISTED`, `SOLD_CONFIRMED`, `TRANSFERRED`, `RELISTED`, and `UNKNOWN`. `SOLD_CONFIRMED` and `TRANSFERRED` require explicit evidence. |
| F15 | Store price components separately. | MSRP, unconditional advertised price, conditional incentives, fees, and add-ons are distinct. Ranking uses `comparison_price` only. |
| F16 | Store feature evidence per prioritized package. | Each feature row has status, source kind, URL, quote, fetch time, and sufficiency (`VERIFIED`, `INSUFFICIENT`, `CONFLICT`). |
| F17 | Keep buyer preferences in configuration. | Hard filters are year, model, powertrain, and region. Colors, interior, DAP, massage, roof, M Sport, and budget change ranking without a parser change. |
| F18 | Separate same-VIN history from cross-VIN comparison. | A price delta requires the same VIN and the same price kind. A lower dealer floor caused by a different VIN is labeled turnover. |
| F19 | Apply completeness gates before a dealer is `REPORTED`. | Missing pages, an unfinished page strategy, or a count anomaly yields `PARTIAL` or `NOT_REPORTED` with a reason. |
| F20 | Build a shortlist with preference fit separate from evidence confidence. | Each row shows VIN, dealer, comparison price or why it is NULL, feature match, unknowns, and last verification time. Unknown is not a positive match. |
| F21 | Make notifications idempotent. | The same business event and channel share one deduplication key. A retry does not send a second copy. |
| F22 | Store human corrections as new versions. | The original extraction remains. The correction records operator id, time, old value, new value, reason, and evidence. Charts read the latest non-rejected version. |
| F23 | Export the latest eligible inventory and a selected history to CSV and XLSX. | The export uses the same scope, price labels, timestamps, and coverage warnings as the screen and the digest. |
| F24 | Enforce the registry host allowlist, robots, and network safety on every fetch. | A host that is not a registry dealer or allowlisted sticker host, a robots disallow, or an unsafe redirect fails closed with a reason. No CAPTCHA, proxy, or TLS-impersonation path exists. |
| F25 | Discover each dealer’s inventory start URL and platform automatically. | From the locator hostname, the fetcher reads the dealer’s public homepage and sitemap (GET only, same host) to find the new-inventory page filtered to X5 where the site allows it, and detects the platform. An operator override takes precedence. A dealer with no discoverable inventory page is `NO_SOURCE` with a reason, and it stays in the denominator. |

## 7. Business rules

### 7.1 Registry

The system of record for “which dealers exist” is a monthly snapshot of the BMW USA dealer locator for Texas. The registry is every Texas dealer in that snapshot. There is no approval subset: every registry dealer is in scope and attempted every run. The blog and other third-party dealer lists are leads only. They do not add dealers, because a Texas BMW franchise dealer is on the locator by definition.

A dealer row MUST include center number, name, city, hostname, start URL, path prefix, website platform, terms status, robots status, and sticker-host allowlist. The hostname comes from the locator. The start URL, path prefix, and platform come from discovery (F25) unless an operator override is set. Group sites such as Sewell and Park Place MAY share a registrable domain. Domain uniqueness MUST NOT be the dealer key. The path prefix MUST keep the fetcher off sister brands and used-car paths. For a group site, the start URL MUST be filtered to that store, so one store’s cars are not stored under another store.

Platform is one of `dealer_com`, `dealer_inspire`, `dealeron`, `cdk`, `other`, or `unknown`. DealerOn and CDK are different values. A new dealer on a known platform is configuration. A dealer on an `other` or `unknown` platform is still attempted with the generic order in section 7.4 (JSON-LD, then the page’s own inventory JSON, then one browser render). If that fails, it is `NOT_REPORTED` / `LAYOUT_UNKNOWN`, and it is a candidate for a new adapter. A new platform adapter is a code change plus golden-set pages.

Terms status is `owner_accepted_risk` for every registry dealer, under the owner’s blanket decision of 2026-10-19. It is recorded for audit and does not gate fetching. A dealer is skipped only for the reasons in section 7.9.

### 7.2 Identity

| Concept | Rule |
| --- | --- |
| Vehicle | VIN, after the checks below. |
| Dealer appearance | One VIN may be listed by more than one dealer. Keep each appearance. |
| Listing | Dealer id plus normalized URL. Tracking query parameters are stripped before storage. |
| Observation | Immutable capture of one dealer and VIN in one run. |
| Run | `run_id` is the execution. `snapshot_date` is the America/Chicago civil date. A re-run is a new `run_id` with `rerun_of_run_id` set. |

VIN rules:

- 17 characters from the VIN alphabet, excluding I, O, and Q, and a valid check digit. Failure excludes the unit with `VIN_CHECKSUM_FAIL`.
- Model year is VIN position 10. For this baseline, **T** means 2026. A page year that disagrees is `CLASSIFICATION_CONFLICT` and is not in scope.
- Powertrain is VIN positions 4–7, the BMW AG code, and MUST agree with an exact quote on the listing:

| AG code | Vehicle | Result |
| --- | --- | --- |
| `23EU` | X5 xDrive40i | In scope when the rest of the rules pass |
| `13EU` | X5 sDrive40i | `NOT_XDRIVE40I` |
| `43EU` | X5 xDrive50e | `POWERTRAIN_OTHER` |
| `33EU` | X5 M60i | `POWERTRAIN_OTHER` |

Any other AG code is out of scope with the matching reason. A decoder MAY corroborate. It MUST NOT invent xDrive40i when the listing quote is missing. These codes are versioned reference data from the BMW of North America X5 (G05 LCI) ordering guide, model year 2026, effective 13 January 2025. Confirm them on one official 2026 window sticker before treating the table as frozen.

### 7.3 Collection outcomes

| Outcome | When | Stored result |
| --- | --- | --- |
| `REPORTED` | Required pages were read and parsed, including a rendered page that explicitly has no matching vehicles. | Observations for in-scope units. Count may be 0. |
| `PARTIAL` | At least one required page was unread or at least one listing failed parsing, and at least one page succeeded. | Parsed in-scope units are kept. Excluded from removal deltas. |
| `NOT_REPORTED` | Timeout, block, captcha, unrendered shell after the browser attempt, unknown layout, robots disallow, or a host outside the registry allowlist. | No observation rows. Reason code required. Stock and floor series leave a gap. |
| `NO_SOURCE` | Locator dealer with no discoverable public inventory URL (F25). | No inventory fetch. Visible in coverage, and counted in the denominator. |

A drop from a positive eligible count to a rendered zero, with no HTTP error, MUST raise `SCRAPE_ANOMALY_SUSPECTED` and still be a legal zero.

Reason codes: `TIMEOUT`, `HTTP_403`, `HTTP_404`, `HTTP_429`, `CAPTCHA`, `UNRENDERED_SHELL`, `LAYOUT_UNKNOWN`, `PAGINATION_INCOMPLETE`, `PARSER_FAILED`, `DETAILS_FAILED`, `BUDGET`, `SSRF_BLOCKED`, `OFF_DOMAIN`, `ROBOTS_DISALLOWED`, `SOURCE_NOT_APPROVED`, `COUNT_ANOMALY`.

Exclusion codes: `NOT_NEW`, `NOT_MY2026`, `NOT_X5`, `NOT_XDRIVE40I`, `MODEL_X5M`, `POWERTRAIN_OTHER`, `USED_OR_CPO`, `DEMO_MILEAGE`, `SALE_PENDING`, `OUT_OF_PATH`, `OUT_OF_REGION`, `VIN_CHECKSUM_FAIL`.

In-scope flags: `DAP_YES`, `DAP_NO`, `DAP_UNKNOWN`, `PRICE_MISSING`, `PRICE_IMPLAUSIBLE`, `PRICE_IS_PAYMENT`, `CALL_FOR_PRICE`, `CONDITIONAL_ONLY`, `CLASSIFICATION_CONFLICT`, `IN_TRANSIT`, `PKG_M_SPORT`, `TRANSFER_SUSPECTED`, `COLOR_UNMAPPED`.

### 7.4 Fetch order and politeness

For each dealer, try sources in this order:

1. JSON-LD (`schema.org/Car` or `Vehicle`) already present in the public page.
2. The inventory JSON request that the public page itself makes, recorded in that dealer’s adapter after it is confirmed.
3. One browser render of the public page.

An HTTP shell MUST escalate to the browser once before `UNRENDERED_SHELL`. A rendered explicit empty result is `REPORTED` with count 0. Undocumented private widget paths are adapter configuration, not a core contract, and they still obey robots.

Re-fetch `robots.txt` every run and store its hash. If `Disallow` covers the inventory path, status is `NOT_REPORTED` / `ROBOTS_DISALLOWED` and nothing else is fetched.

Methods are GET and HEAD only. The fetcher MUST NOT submit forms or activate chat, schedule, or check-availability controls.

Retries apply only to HTTP 429, HTTP 503, and timeouts, at most twice, honoring `Retry-After`, with backoff of 30 seconds then 90 seconds. HTTP 403, 404 on the start URL, and captcha are not retried.

After every redirect, the final URL MUST match the dealer allowlist in punycode, use `https`, contain no userinfo, and MUST NOT resolve to a loopback, link-local, or private address.

Concurrency is at most one request in flight per host, a 5–10 second pause between requests to the same host (baseline), and a global cap of **4** dealers in parallel (baseline). Dealers on a shared group domain share that host’s limit. Per-dealer branch timeout is **10 minutes** (baseline). A timeout keeps the pages already read and makes the dealer `PARTIAL` / `TIMEOUT`.

### 7.5 Page artifacts

Raw HTML MUST NOT be stored in LangGraph state or in trace payloads. Gzip the body to:

`/data/snapshots/{run_id}/{dealer_id}_{page}.html.gz`

Persist only `page_id`, `dealer_id`, URL, SHA-256, storage URI, fetch time, and HTTP status in state and in `fetch_attempts`. Retain snapshot files **14 days** (baseline). Do not store images. Drop embedded chat text. Traces store hashes, token counts, latency, and redacted field names, not cookies or response bodies.

### 7.6 Pricing

Store money as integer USD cents. NULL means the value was not shown. Zero MUST NOT stand in for unknown.

| Field | Meaning |
| --- | --- |
| `msrp_cents` | Sticker MSRP when shown. |
| `advertised_price_cents` | Unconditional cash price the dealer is advertising for that VIN. |
| `comparison_price_cents` | Equal to `advertised_price_cents` when that value is inside the sanity band. Otherwise NULL. |
| Conditional incentive | Name, stated eligibility, and amount. Never subtracted into `comparison_price`. |
| Payment | Monthly or due-at-signing figure. Flag `PRICE_IS_PAYMENT`. Never a comparison price. |
| Fees and add-ons | Stored separately. Not assumed in or out of the advertised price unless the page says so. |

A same-VIN price change requires both comparison prices non-NULL and a difference of at least $1. NULL to a value is “price appeared.” A value to NULL is “price withdrawn.” Those are not drops or increases.

The screen MUST NOT present a scraped out-the-door quote. Taxes, title, and dealer fees vary.

### 7.7 Options, colors, and DAP

Prioritized features, each with its own evidence row: Driving Assistance Professional, M Sport package (ZMP), massage seats, panoramic roof, Panoramic Sky Lounge, Executive package, Climate Comfort, and air suspension. Do not infer one package from another.

DAP evidence, in order:

| Result | Required evidence |
| --- | --- |
| `YES` | Option **ZDY** or feature **5AU**, or the exact phrase “Driving Assistance Professional” or “Driving Assistant Professional,” on the listing or on a parsed allowlisted sticker. |
| `NO` | A parsed sticker option list that contains neither code and neither phrase. |
| `UNKNOWN` | No such evidence, an unreadable PDF, or a sticker host that is not allowlisted. |

**5AS Driving Assistant is standard equipment on this model year and is removed when ZDY is ordered.** 5AS, Active Cruise Control, and Traffic Jam Assistant MUST NOT be read as DAP.

Color and upholstery store the raw quoted string and, when it matches, the ordering-guide code. An unmatched string is kept and flagged `COLOR_UNMAPPED`. It does not drop the unit. Initial exterior codes to load as reference data: 300 Alpine White, 416 Carbon Black Metallic, 475 Black Sapphire Metallic, A96 Mineral White Metallic, C35 Blue Ridge Mountain Metallic, C36 Dravit Grey Metallic, C3D Manhattan Green Metallic, C3Z Tanzanite Blue II Metallic, C4P Brooklyn Grey Metallic, C4W Skyscraper Grey Metallic, C5A Frozen Pure Grey Metallic. Initial upholstery codes: KPA9 Silverstone Sensafin, KPHF Coffee Sensafin, KPRI Cognac Sensafin, KPSW Black Sensafin, VAEW Ivory White Extended Merino, VAHF Coffee Extended Merino, VASW Black Extended Merino, VATQ Tartufo Extended Merino. Confirm the list on the same official sticker used to confirm the option codes.

### 7.8 Lifecycle and deltas

Deltas use that dealer’s last `REPORTED` run as the baseline. Store `baseline_run_id` on each event.

| Event | Rule |
| --- | --- |
| Added | VIN is in today’s `REPORTED` run and absent from the baseline. |
| Missing | VIN was in the baseline, is absent today, and today is `REPORTED`. State becomes `MISSING_UNVERIFIED`. This is not a sale. |
| Price change | Same VIN, both comparison prices present, difference at least $1. |
| Relisted | A VIN in `MISSING_UNVERIFIED` appears again on a later `REPORTED` run. |
| Sold | `SOLD_CONFIRMED` only with explicit evidence. The collector MUST NOT assign this from absence. |
| Transferred | `TRANSFERRED` only with explicit evidence. Two simultaneous dealer appearances are `TRANSFER_SUSPECTED`, not a completed transfer. |

`PARTIAL` and `NOT_REPORTED` MUST NOT emit missing events. Alert at most once when a VIN first becomes `MISSING_UNVERIFIED`.

### 7.9 Review, models, and digest cutoff

`inventory_staging.status` is `VALIDATED`, `PENDING_REVIEW`, or `REJECTED`.

`PENDING_REVIEW` is only for: a layout change, `NOT_REPORTED` after a dealer that was previously `REPORTED`, a price outside the sanity band, `CLASSIFICATION_CONFLICT`, or a VIN check failure. Unknown DAP, unknown color, and ordinary exclusions do not enter the queue. Exclusions still go to the `exclusions` table.

Validated rows commit as each dealer finishes. At the cutoff, comparison and the digest use `VALIDATED` rows only and include the pending count. Clearing the queue writes a supplemental delta into the next digest. It does not send a second email unless the operator asks.

The model is called only for ambiguous package text that deterministic rules did not resolve. Output MUST be schema-valid JSON and every quote MUST be an exact substring of the stored page bytes. A price or VIN the page does not contain is rejected. Page text is data, not instructions. Decoding SHOULD be temperature 0.

If model spend on a run reaches **$0.15** (baseline, inside the $5 monthly model budget), stop new model calls, finish deterministically, mark remaining ambiguous fields `UNKNOWN`, alert, and still publish. If the model is unavailable, do the same and set `model_status` to `down`. Do not fail the dealer for a model outage.

Every active registry dealer is attempted every scheduled run. Order MAY put unhealthy dealers first. Skipping is allowed only for `ROBOTS_DISALLOWED`, `NO_SOURCE`, or `BUDGET`. `SOURCE_NOT_APPROVED` applies only to a URL whose host is neither a registry dealer nor an allowlisted sticker host. It never skips a registry dealer.

### 7.10 Schedule and delivery

- The clock for “daily” is America/Chicago.
- Cron is 06:00. One scheduled run per civil date. A missed run becomes one catch-up labeled `late`. A second scheduled run the same date MUST NOT start.
- Digest subject includes the Chicago date and `run_id`.
- Digest body includes eligible count, added, missing-unverified, price changes, `NOT_REPORTED` dealers, `PARTIAL` dealers, and pending-review count.
- If no `notification_events` row exists by 07:30 America/Chicago, an alert fires from outside the crawler container.
- Secrets are the model API key and the SMTP credential only. There are no dealer-site passwords and no proxy credentials.

## 8. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Reliability | Every dealer ends in a terminal status and a partial run is still published. Target: at least **90%** of active Texas registry dealers `REPORTED` (baseline). That target is not permission to hide the rest. |
| Accuracy | No observation rows from a failed read. No unsupported `SOLD_CONFIRMED`. No unsupported DAP `YES`. Release gates in section 13 are must-pass. |
| Freshness | Wall clock **60 minutes** for every Texas dealer (baseline). The digest deadline in section 7.10 still holds. The screen shows data age per dealer. |
| Compliance | Registry dealer sites and allowlisted sticker hosts only. Honor robots. Identify the fetcher honestly in the HTTP and browser user agents. Do not bypass blocks, sign-in, or rate limits. |
| Network | Allowlist after redirects. Reject private and link-local targets. Sticker hosts are explicit. |
| Security | Secrets outside the repo. Traces and snapshots are redacted as in section 7.5. Operator id is stored instead of a scraped personal profile. |
| Audit | Every comparison price and every YES/NO feature links to a URL, a fetch time, a page hash, and a quote. Observations store git SHA and adapter version. |
| Cost | Model calls under $5 per month, with a $0.15 per-run breaker. Track browser time, storage, email, and backups separately. |
| Maintainability | Known-platform dealers are configuration. New platforms are code. Parsers and option-code tables are versioned. |
| Observability | One trace per run, one span per dealer and node, with latency, tokens, cost, and page hash. |
| Concurrency | Bounds in section 7.4. SQLite has a single writer. Retries and resumes are idempotent at the database, not only in the graph. |
| Scale | 50 dealers and 3 years of structured history. Snapshot bytes are capped by the 14-day retention. Move structured data to Postgres when a second writer is required. |
| Recovery | WAL mode. Backup with the SQLite backup API or `VACUUM INTO` after commit. Application data, LangGraph checkpoints, and traces are separate files. Restore MUST be opened and read, not merely copied. Baseline RPO is the last published run. |
| Checkpoint resume | If the git SHA at resume differs from the SHA that wrote the checkpoint, abandon the checkpoint and start a fresh run. Prune checkpoints older than the last published run, except open review items. |

## 9. Architecture

> **Superseded by [`requirements-mas.md`](requirements-mas.md) v2.0.** The run is a nine-agent LangGraph system with a live Pipeline screen. The table below is kept for history. Model use is still limited as in section 4.

Logical responsibilities stay. They are not eight language-model agents.

| Step | Implementation | Output |
| --- | --- | --- |
| Supervisor | Deterministic scheduler. Model use is limited to ordering among dealers that will all be attempted. | `run_id`, frozen config version, dealer task list. |
| Registry | Deterministic locator import and inventory-URL discovery (F25). No human approval step. | Registry rows and coverage gaps. |
| Fetcher | HTTP, then JSON, then one browser render. Platform detection is a function, not an agent. | `fetch_attempts` and artifact pointers. |
| Extractor | Deterministic parse first. Model only for leftover ambiguous package text. | Structured fields plus evidence quotes. |
| Validator | Rules engine. | Eligibility, feature status, staging status. |
| Storage | Transaction per dealer. | Committed observations or an explicit non-report. |
| Comparator | Deterministic queries against the last good run. | Deltas and lifecycle events. |
| Notifier | Deterministic render and send queue. | `notification_events`. |
| Review | Separate queue. It does not sit on the critical path of the digest. | Audited corrections. |

Phase 1 MAY be a plain scheduled pipeline with the same tables. LangGraph is allowed and is not required. If it is used, these invariants hold:

1. State holds run metadata, dealer ids, and artifact pointers. It does not hold raw HTML.
2. Parallel dealer branches merge with a reducer keyed by dealer id. They MUST NOT append to one shared list.
3. Fetcher writes `NOT_REPORTED`. Validator writes classification and staging status. One writer per field.
4. Graph replay is not a substitute for database idempotency.
5. Checkpoint storage is a different SQLite file from the business database.

## 10. Data model

SQLite in WAL mode. Foreign keys on. Money in integer cents. NULL for unknown. Timestamps in UTC.

| Table | Key | Purpose |
| --- | --- | --- |
| `runs` | `run_id` | `snapshot_date`, trigger (`cron`, `manual_dealer`, `manual_date`, `catchup`), status (`RUNNING`, `PUBLISHED`, `FAILED`), start and end, config version, parser version, model version, git SHA, `rerun_of_run_id`, model spend. |
| `dealers` | `dealer_id` | Center number, name, city, hostname, start URL, path prefix, platform, terms status, robots status, sticker allowlist, active flag, verified time. Domain is not unique. |
| `dealer_runs` | `(run_id, dealer_id)` | Status, reason code, pages expected, pages read, pages failed, raw count, eligible count, duration, cost, baseline run id, anomaly flag. |
| `units` | `vin` | Vehicle identity, model, year, AG code, first seen, last seen. Current dealer is not a column on this row. |
| `dealer_listings` | `listing_id` | Dealer, VIN, normalized URL, alternate URLs, first seen, last seen. |
| `observations` | `observation_id`; unique `(run_id, dealer_id, vin, version)` | Immutable listing capture: stock, colors, odometer, availability, prices, DAP status, M Sport flag, extraction method (`parser`, `llm`, `human`), adapter version, git SHA, page hash, source URL, fetch time. A human correction inserts a new version. |
| `exclusions` | `exclusion_id` | Run, dealer, VIN or URL, reason code, evidence quote. Not used by charts. |
| `fetch_attempts` | `fetch_attempt_id` | URL, final URL, HTTP status, attempt, latency, robots decision, content hash, storage URI, error code. |
| `vehicle_features` | `feature_evidence_id` | Observation, feature, YES/NO/UNKNOWN, source kind, quote, sufficiency, conflict flag. |
| `price_components` | `price_component_id` | Observation, kind, cents, currency, eligibility text, start and end if shown. |
| `listing_lifecycle` | `lifecycle_event_id` | VIN, dealer, from state, to state, time, reason, evidence, baseline run. |
| `inventory_staging` | `(run_id, dealer_id, vin)` | `VALIDATED`, `PENDING_REVIEW`, or `REJECTED`, plus the reason. |
| `review_items` | `item_id` | Run, dealer, VIN, issue, status, operator id, decision, decided time. |
| `audit_events` | `audit_id` | Old value, new value, operator id, time, reason, evidence. |
| `notification_events` | unique `(run_id, recipient, event_key)` | Channel, content hash, attempts, provider status, last error. |
| `schema_migrations` | `version` | Applied migration and time. |

Indexes MUST cover `(dealer_id, run_id)`, `(vin, fetched_at)`, and the notification deduplication key.

A unique key of `(vin, run_id)` is forbidden. It cannot represent two dealers in one run.

## 11. Screen and digest

The screen reads committed rows only. Phase 1 is read-only for a single operator. Implementation MAY be Streamlit in Phase 1. A shared multi-user screen is a later decision and needs authentication.

| View | Question | Display |
| --- | --- | --- |
| Price band | Where do advertised cash prices sit in the latest comparable snapshot? | One point per eligible VIN with a non-NULL comparison price, grouped by dealer, labeled with as-of time. |
| Floor by dealer | How did each dealer’s lowest comparison price move? | One line per dealer, one point per `REPORTED` run. Gap otherwise. Annotate when the floor VIN changed. |
| Stock | How many eligible units were listed? | One line per dealer. Gap on `PARTIAL` and `NOT_REPORTED`. No stacked area. The statewide total counts only dealers `REPORTED` that day and says so. |
| Price change log | Which VINs changed comparison price? | Same-VIN pairs against `baseline_run_id`, with signed dollars and links to both observations. |
| Data health | Which dealers failed or are stale? | Status per dealer per day, last success, page counts, reason. |
| Shortlist | Which in-scope cars fit the saved preferences? | Ranked rows with preference fit and evidence confidence as separate scores, plus a side-by-side feature comparison. |

Filters: dealer, city, exterior, interior, M Sport flag, DAP, in-transit versus on-lot, price present versus call-for-price, massage, roof, Executive package, evidence sufficiency, date range.

A missing feature renders as Unknown, not as No. Every number links to the observation rows behind it. Source links and allowlisted sticker links are shown.

Empty states MUST be explicit: no runs yet, all dealers failed, no in-scope units, and a dealer last reported more than one Chicago day ago.

There is no “when to buy” prediction.

## 12. Buyer preferences

Hard constraints, not preferences: new, model year 2026, xDrive40i, Texas, and the mileage cut in section 3.

| Preference | Baseline effect |
| --- | --- |
| Driving Assistance Professional | High priority. `UNKNOWN` stays visible and is not treated as a match. It does not remove the car. |
| Exterior | Grey shades preferred. Other colors remain listed. |
| Interior | Non-black preferred. Black remains listed. |
| Massage seats | Optional positive. Absence does not remove the car. |
| Panoramic roof and Sky Lounge | Preferred, and stored as separate features. |
| Executive, Climate Comfort, air suspension | Optional, stored separately. |
| M Sport | Included and flagged. The buyer can hide ZMP cars in the filter. |
| Maximum price | Unset until the owner names a number and a price basis. Until then the sanity band only rejects impossible figures. It is not a buyer budget. |

The owner MAY later turn grey exterior, non-black interior, or DAP into hard filters. That change is configuration.

## 13. Evaluation

The operator labels fixtures. The owner spot-checks them. Refresh a dealer’s fixtures when its adapter changes, and review the set at least monthly.

Starter set: at least 30 pages, with at least one dealer from each platform in the registry, stratified so that each of these exists at least once:

- Mixed X3 and X5 inventory, including the Arlington combined inventory page
- Page 2 or a later page of a listing
- HTTP shell that requires the browser
- Captcha or HTTP 403
- Rendered true zero
- Call-for-price
- Payment-only price
- In-transit unit
- xDrive40i with ZMP
- M60i near-miss
- X5 M or sDrive40i near-miss
- Duplicate VIN at one dealer
- Same VIN at two dealers
- Window sticker with ZDY
- Window sticker or listing that shows 5AS and not ZDY
- Conditional rebate shown as if it were the cash price

Offline replay MUST NOT hit live sites. A separate, rate-limited live smoke MAY check robots and blocks and MUST NOT be the accuracy gate.

| Gate | Bar |
| --- | --- |
| VIN | Exact match, including check digit, on every golden page that shows a VIN. |
| Eligibility | Exact on curated in-scope and out-of-scope cases, including ZMP in scope and 5AS not equal to DAP. |
| Comparison price | Exact cents or NULL. No wrong number. Conditional rebates and payments are not the comparison price. |
| DAP | Zero unsupported `YES`. `NO` only on qualifying sticker cases. |
| Dealer status | Exact on failure fixtures, including shell-then-browser and true zero. |
| Duplicates | One observation per dealer, VIN, and run. |
| Deltas | No removal from a failed or partial dealer. No sale from a disappearance. |
| Replay | Reprocessing a run does not duplicate observations or emails. |
| Digest | Screen, export, and digest match one published run and the same coverage warnings. |
| Restore | A backup opens and returns a known row. |

Field-level agreement of 98 percent applies only to the remaining visible fields, after a written normalizer for case and whitespace. It is not a substitute for the gates above. Prompts MUST NOT be accepted solely on pages they were tuned on.

Additional drills: layout change, rate limit 429, redirect off the allowlist, prompt injection in listing text, retry after a commit, corrupt backup, and a model outage.

## 14. Delivery phases

| Phase | Deliver | Exit |
| --- | --- | --- |
| 0. Registry | Texas locator snapshot imported, plus start-URL and platform discovery (F25) for every dealer. | Every Texas locator dealer has a registry row with either a start URL or a `NO_SOURCE` reason. No per-dealer owner input is required. |
| 1. Monitor (statewide) | Every Texas registry dealer, daily run, claim-check snapshots, status model, price rules, staging, idempotent digest, external heartbeat, starter golden set, CSV export, coverage report. | Two weeks of runs reconcile to the operator’s manual checks on a sample of dealers from each platform. A failed dealer never appears as zero. Must-pass gates hold on the starter set. Wall clock within 60 minutes. |
| 2. Coverage and enrichment | Adapters for platforms that ended `LAYOUT_UNKNOWN`, plus detail and sticker enrichment. | At least 90% of active Texas registry dealers `REPORTED`. Every remaining gap is named with a reason. |
| 3. Screen | The six views, filters, exports, source links, empty states. | Owner checks example comparisons, price bases, gaps, and the shortlist against one published run. |
| 4. Hardening | Restore drill, retention enforcement, budget alerts, fixture expansion. LangGraph is no longer optional: see [`requirements-mas.md`](requirements-mas.md) section 11. | Failure drills pass, a restore reads, and a run that trips the model breaker still publishes. |

Phase 1 already includes the review queue, the golden-set starter, and read-error handling. Those are not deferred to Phase 4.

## 15. Risks

| Risk | Mitigation |
| --- | --- |
| Dealer markup changes | Versioned adapters, fixture replay, `LAYOUT_UNKNOWN` or `PARTIAL` instead of a silent empty parse. |
| Access is blocked or forbidden | Record the reason, do not evade, ask the owner about a permitted feed. |
| The last X5 sells | `REPORTED` count 0 plus `SCRAPE_ANOMALY_SUSPECTED`, so a real zero can be confirmed. |
| DAP is missing on the search page | Sticker parse when allowlisted; otherwise `UNKNOWN`. |
| A low price is a rebate or a payment | Separate components. Comparison price stays NULL. |
| The same VIN is listed twice | Deduplicate inside a dealer. Flag, do not merge, across dealers. |
| Review load | Unknown options do not enter the queue. One dealer cannot hold the digest. |
| SQLite lock or a huge checkpoint | Single writer, separate checkpoint file, claim-check for HTML. |
| Model follows page text | Substring quotes, schema checks, spend breaker. |
| A statewide chart looks complete | Denominator, gaps, and as-of time are part of the view. |
| Cron does not run | External heartbeat at 07:30 America/Chicago. |

Deployment shape: one container, a scheduler, a mounted SQLite volume, a separate checkpoint file if a graph checkpointer is used, an artifact directory, and the platform secret store. Host name and email vendor are chosen in Phase 0.

## 16. Still needed from the owner

This file does not invent these. Collection does not wait on any of them: the dealer list comes from the locator, and the owner gave blanket approval for every Texas dealer on 2026-10-19.

1. Digest recipient addresses. Until they exist, the digest is written to a file and not emailed.
2. Confirmation, on one official 2026 X5 window sticker, of the AG codes, ZDY, 5AU, 5AS, and ZMP before those codes are treated as frozen. Until then, the table in section 7.2 is the working set and its version is stored on every observation.
3. Optional: an operator override for any dealer whose inventory URL discovery fails or picks the wrong page, for example a store on a group site.
4. Optional: a buyer maximum price and whether grey exterior, non-black interior, or DAP should become hard filters. Until then section 12 applies.
5. Optional: hosting account and SMTP provider. Local single-container cron is the baseline if none is named.

## 17. Sources

Design references, opened for the original design on 2026-10-19. The dealer list itself comes from the BMW USA dealer locator for Texas (section 7.1). A page below is a vehicle-data source only through its dealer’s registry row.

- [Sewell locations directory](https://www.sewell.com/locations/) — lead for Plano and Grapevine BMW locations.
- [DFW BMW buyer guide](https://www.autowebexpo.com/blog/2025/october/30/a-smart-buyers-guide-to-dfw-bmw-dealers.htm) — non-official lead only. Not a data source.
- [BMW of Arlington new X3 and X5 inventory](https://www.bmwofarlington.com/new-vehicles/new-x3-and-x5-inventory/) — pagination example and a required mixed-inventory fixture.
- [Park Place X5 listing](https://www.parkplace-bmw.com/inventory/new-2026-bmw-x5-xdrive40i-awd-suv-5ux23eu0xt9529123/) — extraction example. VIN positions 4–7 are `23EU`.

Option and color codes are taken from the BMW of North America X5 (G05 LCI) ordering guide, model year 2026, effective 13 January 2025, as circulated with that guide’s retail pricing pages. Confirm on a sticker per section 16.

## 18. Change log

| Version | Date | Change |
| --- | --- | --- |
| 1.0 | 2026-10-19 | Original design. Goals, F1–F10, six tables, eight agent responsibilities, five views, four phases. |
| 1.1 | 2026-10-19 | Review consolidation. F11–F24, lifecycle, price components, evidence rules, non-blocking review, safer checkpoint and schema, shortlist view. Several decisions left open. |
| 1.2 | 2026-10-19 | Merges 1.0 and 1.1 and locks the review: M Sport in scope, true zero versus failed read, comparison price, claim-check snapshots, staging, sticker DAP codes, VIN AG codes, no bot evasion, split time targets, and safety controls in Phase 1. |
| 1.3 | 2026-10-19 | This baseline. Statewide dealer scope: every Texas BMW dealer on the BMW USA locator is in the registry and attempted every run, under the owner’s blanket approval, with no per-dealer terms gate. Adds F25 (automatic inventory-URL and platform discovery). Coverage denominator, politeness limits, time targets, phases, and owner inputs are updated to match. Vehicle scope is unchanged (new 2026 X5 xDrive40i). Robots, honest identification, and the no-evasion rules are unchanged. |
| 2.0 | 2026-10-20 | Architecture addendum in [`requirements-mas.md`](requirements-mas.md): nine-agent LangGraph MAS, pipeline events, live Pipeline screen, seven-layer map. Replaces section 9. Every other section is unchanged. |
| 2.1 | 2026-10-20 | Screen addendum in [`requirements-web-ui.md`](requirements-web-ui.md): every screen in one app shell with a left menu, global search, Dealers, How it works, and the live Pipeline page. Section 11 rules apply on every page. |
| 2.2 | 2026-10-20 | Shortlist addendum in [`requirements-shortlist.md`](requirements-shortlist.md): saved cars with tracking, saved searches from a base car, a deterministic A10 Matcher agent that finds new similar cars after every run, and a digest section. |
