# AI car shopping — saved cars, saved searches, and daily matches

**Product:** AI car shopping (Texas X5 Finder)  
**Revision:** 2.2 (addendum to [`requirements.md`](requirements.md) v1.3, [`requirements-mas.md`](requirements-mas.md) v2.0, and [`requirements-web-ui.md`](requirements-web-ui.md) v2.1)  
**Status:** Implementation baseline  
**Date:** 2026-10-20  
**Implementer:** Cursor · **Reviewer:** Claude · **Approver:** owner

**Amends:**
- `requirements-mas.md` §3 and §4.1: adds a tenth agent, **A10 Matcher**, to the parent graph.
- `requirements-web-ui.md` §3.1 and §8: the Shortlist page becomes **My shortlist** with four tabs, and Save controls appear on car rows and cards.
- `requirements-web-ui.md` §9 and the read-only rule from the 2026-10-20 code review: the app gains a small set of write endpoints, limited to the owner's own saved cars and saved searches (section 9).
- `requirements.md` §7.10: the digest gains a "New for your saved searches" section.

Every other rule still holds. In particular: unknown is never a match, a disappearance is never a sale, a dealer that was not read creates no "missing" event, and the screens read committed, validated rows only.

Words **MUST**, **MUST NOT**, and **SHOULD** are normative.

## 1. Why

The owner found a car they like and wants to:

1. **Save it** with everything we know about it, and keep watching its price and whether it is still listed.
2. **Use it as a base** to describe "cars like this one".
3. **Have the system keep looking.** After every daily collection, it should find cars that are new and similar, and tell the owner.

Worked example, used throughout this file and in the acceptance tests:

| Field | Base car 5UX23EU0XT9462524 (BMW of Arlington, 2026-10-20 run) |
| --- | --- |
| Paint | Skyscraper Grey Metallic (grey family) |
| Interior | Silverstone Sensafin (light family) |
| Cash price / MSRP | $87,949 / $87,125 |
| M Sport | Yes |
| Features with `YES` | Driving Assistance Professional, M Sport, Panoramic roof, Climate Comfort |
| Features `UNKNOWN` | Massage seats, Sky Lounge, Executive package, Air suspension |

## 2. Goals

| ID | Goal |
| --- | --- |
| S1 | Save any car to **My shortlist** in one click from All inventory, Eligible cars, Find a car, and the car page. |
| S2 | A saved car keeps a **snapshot of everything known at save time** (features with their evidence status, paint, interior, prices, dealer, M Sport, availability, package text) and shows what changed since. |
| S3 | Turn a saved car, or the Find a car wish list, into a **saved search** with editable criteria. |
| S4 | A **Matcher agent** runs after every published run and records which cars match each saved search, with a score and the reasons. |
| S5 | The owner sees **"New today"** matches in the app and in the daily digest. A car is announced as new once per search. |
| S6 | Everything is explainable: every match shows which criteria it met, which are unknown, and how it differs from the base car. |

## 3. Terms

| Term | Meaning |
| --- | --- |
| Saved car | A VIN the owner saved. One row per VIN. |
| Base car | A saved car used to prefill a saved search. A search MAY have no base car (for example, one saved from the wish list). |
| Saved search | A named set of criteria (section 5) that the Matcher runs after each published run. |
| Match | A car in the latest published data for its dealer that passes every hard limit and has `YES` for every Must feature. |
| Possible match | A car that passes every hard limit, has no Must feature with `NO`, and has at least one Must feature `UNKNOWN`. Same idea as "Might fit" in `buyer-ui-spec.md`. |
| New | The first time a VIN appears as a Match or Possible match for that search, in any run after the search was created. |
| Now a full match | A VIN that was a Possible match for a search and becomes a Match, for example after a sticker confirms Driving Assistance Professional. |

## 4. Saved cars

### 4.1 Saving

- A **☆ Save** control appears on:
  - every row of All inventory (`/catalog`) and Eligible cars (`/inventory`);
  - every car card on Find a car (`/`), next to Compare;
  - the car page (`/vin/{vin}`) header;
  - car results in the search palette (secondary action).
- One click saves the car and the control turns into **★ Saved**. A second click asks for confirmation inside the page ("Remove from shortlist?") and then removes it (soft delete).
- An optional note (up to 280 characters) can be added on the car page.
- A car can be saved only if it has at least one committed observation. Saving never triggers a fetch.

### 4.2 Snapshot at save time

When a car is saved, store a JSON snapshot of its latest committed observation:

- VIN, dealer id and name, stock, listing URL, run id, and snapshot date;
- year, model, trim, AG code;
- exterior raw string and code, interior raw string and code;
- odometer, availability, in-transit flag, M Sport flag;
- MSRP, advertised price, comparison price, and the price components;
- for each of the eight prioritized features (`requirements.md` §7.7): the status (`YES`, `NO`, `UNKNOWN`), source kind, and quote;
- the catalog package text, when the car is in `catalog_vehicles`.

The snapshot never changes after it is written. It is what "since you saved it" compares against.

### 4.3 Tracking

For each saved car, after every published run that read its dealer, the shortlist shows:

| Field | Rule |
| --- | --- |
| Status | **Listed** at its dealer; **Missing, unverified** (`MISSING_UNVERIFIED`); **Back again** (`RELISTED`); **Also listed at another dealer** (`TRANSFER_SUSPECTED`). Never "Sold" unless `SOLD_CONFIRMED` evidence exists (F14). |
| Price now | Latest comparison price, or its reason when NULL ("Call for price", "Payment only", …). |
| Change since saved | Same-VIN, same price kind only (F18). Shown in dollars and as ▲ / ▼. "Price appeared" or "Price withdrawn" when one side is NULL (§7.6). |
| Lowest seen | Lowest comparison price for this VIN across all published runs. |
| Feature changes | Any feature whose status changed since the snapshot ("Driving Assistance Professional: not listed → ✓"). |
| Last read | Date of the latest run that read this car's dealer. If the dealer was not read, show "Dealer not read on <date>" and keep the previous values. A dealer that was not read never moves the car to Missing. |

Data comes from existing tables (`observations`, `vehicle_features`, `price_components`, `price_events`, `listing_lifecycle`). No new collection.

## 5. Saved searches

### 5.1 Criteria

One schema, shared with the Find a car wish list, so a wish list can be saved as a search and a search can be opened as a wish list. Store it as versioned JSON (`criteria_version: 1`).

| Criterion | Values | Kind |
| --- | --- | --- |
| Base car | VIN of a saved car, or none | Reference for "difference" and price closeness |
| Features | For each of the eight prioritized features: **Must**, **Nice**, or **Ignore** | Must: rule in section 5.3. Nice: adds to the score. |
| M Sport | Must, Nice, Ignore, or **Exclude** | Exclude is a hard limit: a car with M Sport is not a match. |
| Exterior | List of paint families and/or exact paint names | Preference; a hard limit when "only these colors" is on |
| Interior | List of interior families and/or exact interior names | Preference; a hard limit when "only these colors" is on |
| Only these colors | true / false | Turns both color lists into hard limits |
| Maximum price | dollars, or none | Hard limit on comparison price |
| Price band around base | ± dollars, or none (default ±$3,000 when a base car is set) | Hard limit when set; also feeds the score |
| Cars without a cash price | Include as Possible match / Exclude | Default include. A car with no cash price is never a Match when a price limit is set; it can be a Possible match with its reason. |
| Location | "Anywhere in Texas" or a city plus radius (50, 100, 200 miles) | Hard limit, great-circle miles from the dealer's locator coordinates, as Find a car does |
| On the way to the dealer | Include / Exclude | Hard limit on `in_transit` |
| Alerts | Digest on / off | Section 7 |

### 5.2 Creating a search from a base car

On the car page, and on a saved car in My shortlist, **Find cars like this** opens a form prefilled from the snapshot:

| Criterion | Prefill |
| --- | --- |
| Features `YES` on the base car | **Must** for Driving Assistance Professional, **Nice** for every other `YES` feature |
| Features `UNKNOWN` or `NO` on the base car | **Ignore** |
| M Sport | Same as the base car: **Nice** if it has M Sport, else **Ignore** |
| Exterior | The base car's paint family |
| Interior | The base car's interior family |
| Only these colors | off |
| Price band | ±$3,000 around the base car's comparison price; none if the base car has no cash price |
| Location | Anywhere in Texas |
| On the way to the dealer | Include |
| Name | "Like <paint> · <interior> · <dealer city>", editable |

The owner edits any field and saves. The form shows, before saving, how many cars in the latest data would be a Match and a Possible match, using the same matcher code as section 6 (a dry run that writes nothing).

For the worked example the prefilled search is: DAP **Must**; M Sport, Panoramic roof, Climate Comfort **Nice**; others **Ignore**; grey exterior; light interior; $84,949 to $90,949; anywhere in Texas.

### 5.3 Matching rules

For each car in the latest validated, non-rejected observations per dealer (section 6.2):

1. **Hard limits.** If any hard limit fails, the car is not a match. Record nothing.
2. **Must features.**
   - Any Must feature with `NO`: not a match.
   - All Must features `YES`: **Match**.
   - Otherwise (some Must `UNKNOWN`, none `NO`): **Possible match**.
   - `UNKNOWN` never satisfies a Must. A Possible match is shown as such and never counted as a Match.
3. **Score**, 0–100, for ordering within a tier. Score = 100 × earned ÷ possible, over the criteria that apply to this search:

| Criterion | Earned |
| --- | --- |
| Each Nice feature | 10 if `YES`, else 0 (`UNKNOWN` earns 0) |
| M Sport when Nice | 10 if it matches |
| Exterior | 15 for the exact paint, 10 for the same family, 0 otherwise |
| Interior | 10 for the exact interior, 6 for the same family, 0 otherwise |
| Price closeness to base (only with a base car and both prices present) | 15 within $1,000, 10 within $3,000, 5 within $5,000, else 0 |

4. **Reasons.** Store, per match, a list of short reason chips: met ("✓ Driving Assist Pro"), unknown ("? Massage not listed"), and differences from the base car ("+$276", "Dravit Grey instead of Skyscraper Grey", "Cognac interior").

The base car itself is never a match for its own search.

### 5.4 Color families (reference data)

Add a versioned reference table, next to the existing color codes (`reference/`), mapping each paint and interior to a family. Initial values:

| Family | Members |
| --- | --- |
| Grey | Skyscraper Grey Metallic (C4W), Brooklyn Grey Metallic (C4P), Dravit Grey Metallic (C36), Frozen Pure Grey Metallic (C5A) |
| White | Alpine White (300), Mineral White Metallic (A96) |
| Black | Carbon Black Metallic (416), Black Sapphire Metallic (475) |
| Blue | Tanzanite Blue II Metallic (C3Z), Blue Ridge Mountain Metallic (C35) |
| Green | Manhattan Green Metallic (C3D) |
| Interior: Light | Silverstone Sensafin (KPA9), Ivory White Extended Merino (VAEW) |
| Interior: Brown | Coffee Sensafin (KPHF), Cognac Sensafin (KPRI), Coffee Extended Merino (VAHF), Tartufo Extended Merino (VATQ) |
| Interior: Black | Black Sensafin (KPSW), Black Extended Merino (VASW) |

A raw string with no mapped code (`COLOR_UNMAPPED`) belongs to no family. It can only match by exact raw string.

### 5.5 Managing searches

- On the Saved searches tab: rename, edit criteria, pause / resume, turn digest alerts on or off, delete (soft), and **Open in Find a car** (loads the criteria into the wish list).
- On Find a car: **Save this wish list as a search** below the wish list.
- Limits: 20 active saved searches and 200 saved cars (baseline). The UI says so when a limit is reached.

## 6. A10 Matcher agent

### 6.1 Definition

Add to `requirements-mas.md` §3:

| # | Agent | Stage on screen | Mode | Graph | Responsibility | Tools | Writes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A10 | Matcher | Match | deterministic | parent graph node | After the run is published, evaluate every active saved search against the latest data, record matches with score and reasons, mark which are new, and record changes for saved cars. | `shortlist.match` (new, pure functions), `storage.repos` | `matches` (counts per search only, never car rows) |

The Matcher uses **no model**. The two-model-agent cap in `requirements.md` §4 is unchanged.

### 6.2 Where it runs

Parent graph (`requirements-mas.md` §4.1), between publish and notify:

```
… → comparator → supervisor_publish → matcher → notifier → END
```

- It runs on every published run, including single-dealer reruns and catch-ups. A rerun can only add or update matches for the dealers it read.
- It reads committed rows only. The candidate set is, **per dealer**, that dealer's observations from its most recent published run in which it was `REPORTED` or `PARTIAL`, with staging `VALIDATED` and not rejected. So a dealer that was not read today keeps its previous cars as candidates, but those cars are never announced as New because of that run.
- For a `PARTIAL` dealer, cars that were read can be matched. A car absent from a `PARTIAL` read is not removed from matches.
- It runs in its own transaction. If it fails, the run stays `PUBLISHED`. The digest says "Saved-search matches could not be computed today", an operator alert is logged, and `carshop match --run-id <id>` can recompute later.
- Idempotent: re-running the Matcher for the same run (replay, retry, or the CLI) produces the same rows and no new "New" flags (`requirements.md` §13 Replay gate).

### 6.3 New, gone, and changes

| Event | Rule |
| --- | --- |
| New | VIN is a Match or Possible match for the search and has no earlier `search_matches` row for that search. It is announced once. |
| Now a full match | VIN was a Possible match on its previous evaluation and is a Match now. |
| No longer matches | VIN was a match and now fails a hard limit or a Must feature. Shown on the search, not alerted. |
| Not listed today | VIN's dealer was `REPORTED` today and the VIN is absent. Follows lifecycle rules (`MISSING_UNVERIFIED`). Never "sold". |
| Price change on a matched car | Same VIN, same price kind, at least $1 (F18). Shown, not alerted, unless the car is also a saved car. |
| Saved car price change, missing, back again | Alerted in the digest (section 7), with the same F7 and F14 rules. |

**Creating a search never floods.** When a search is created or its criteria change, the Matcher evaluates it immediately against the latest data (outside the daily run) and records those matches as the **baseline**, not as New. Only cars that appear in later runs are New.

## 7. Where the owner sees it

### 7.1 My shortlist page

`/shortlist` becomes **My shortlist** (menu label, Shop group). Four tabs, each with its own URL token (`?tab=`):

| Tab | Content |
| --- | --- |
| **New today** (default when it is non-empty) | New matches and "now a full match" events from the latest published run, grouped by saved search. Each row: car, dealer, price, tier (Match / Possible match), score, reason chips, differences from the base car, ☆ Save, Compare. |
| **Saved cars** | Saved cars with the tracking fields in section 4.3, sorted by most recent change. Each has "Find cars like this", Compare, and Remove. |
| **Saved searches** | Each search: name, base car, criteria summary, Match / Possible match counts, new today, last evaluated, and the controls in 5.5. Opening a search lists all its current matches by tier and score. |
| **Ranked for you** | The current Shortlist content (preference fit and evidence confidence), unchanged. |

Rules:

- Shop vocabulary from `buyer-ui-spec.md`. No internal codes outside "Technical details".
- The menu item shows a badge with the number of New matches in the latest published run, when greater than zero.
- Compare works across tabs, up to four cars, as today.
- Empty states: "No saved cars yet. Use ☆ Save on any car." and "No saved searches yet. Open a car and choose Find cars like this."

### 7.2 Elsewhere

- **Find a car:** cars that are New for any saved search show a small "New for <search name>" tag. Saved cars show ★.
- **Car page:** ★ Saved state, saved note, "since you saved it" changes, and the searches this car matches.
- **Live pipeline** (`requirements-web-ui.md` §8.1): the Matcher appears as a node after publish, with events `start`, `progress` (one per search), and `end` with counts `{searches, matches, possible, new}`.

### 7.3 Digest

Add a section after the existing deltas, only when there is something to say:

```
New for your saved searches
  Like Skyscraper Grey · light interior (2 new)
    ✓ 5UX23EU01T9541662 · BMW of Austin · $88,225 · Dravit Grey / Cognac · +$276 vs your base car
    ? 5UX23EU02T9552461 · BMW of Wichita Falls · $87,900 · Driving Assist Pro not listed
Your saved cars
    5UX23EU0XT9462524 · BMW of Arlington · $87,949 → $86,949 (▼ $1,000)
```

- Each line links to the car page.
- Deduplication key per line: `match:{search_id}:{vin}` and `watch:{vin}:{event}:{run_id}` in `notification_events` (F21). A retry or replay never sends a line twice.
- Searches with digest alerts off are left out.
- Until recipients exist (`requirements.md` §16 item 1), the section is written into the digest file like the rest of the digest.

## 8. Data model

New tables in the business database (Alembic migration):

| Table | Key | Columns |
| --- | --- | --- |
| `saved_cars` | `saved_car_id`; unique active `vin` | `vin`, `saved_at`, `note`, `snapshot_json`, `snapshot_observation_id`, `removed_at` |
| `saved_searches` | `search_id` | `name`, `base_vin` (nullable, references a saved car), `criteria_json`, `criteria_version`, `created_at`, `updated_at`, `paused`, `alerts`, `removed_at`, `baseline_at` |
| `search_matches` | unique `(search_id, run_id, vin)` | `dealer_id`, `observation_id`, `tier` (`MATCH`, `POSSIBLE`), `score`, `reasons_json`, `is_new`, `became_full_match`, `evaluated_at`, `baseline` (true for rows written at create/edit time) |
| `color_families` | `(kind, code_or_raw)` | `kind` (`exterior`, `interior`), `family`, `reference_version` |

- Indexes on `search_matches(search_id, vin)`, `search_matches(run_id)`, `saved_cars(vin)`.
- Saved car tracking (section 4.3) is computed from existing tables at read time. It needs no table of its own.
- Deleting a search or a saved car is a soft delete. Its match history stays for the record.
- These rows are the owner's personal lists, not audit evidence about cars. They are not written to `audit_events`, but every row has created and updated times.

## 9. Write access

This is the first web write path since the review page moved to the CLI. Scope it narrowly:

- Allowed writes, and only these: create, update, and soft-delete rows in `saved_cars` and `saved_searches`, and trigger the baseline evaluation of one search. No write endpoint touches observations, staging, review items, or runs.
- Endpoints (JSON, `POST`/`PATCH`/`DELETE`): `/api/shortlist/cars`, `/api/shortlist/cars/{vin}`, `/api/shortlist/searches`, `/api/shortlist/searches/{id}`, and `POST /api/shortlist/searches/preview` (dry run, writes nothing).
- Every write requires a same-origin request (`Origin` / `Sec-Fetch-Site` check) and a CSRF token rendered into the page.
- Writes are enabled only when the app is bound to a loopback address (the default `web_host`). On any other bind address the endpoints return 403 and the Save controls are hidden, until authentication is added (`requirements.md` §2: shared access requires authentication).
- Each write is one short transaction through the existing single-writer path, so it cannot collide with a running pipeline write beyond the busy timeout. A write that times out returns a clear error ("The daily run is saving data. Try again in a moment.") and the UI keeps the user's input.
- CLI parity, for scripting and recovery: `carshop shortlist list|add VIN|remove VIN`, `carshop search list|show ID|pause ID|resume ID`, `carshop match --run-id ID`.

## 10. Non-functional

| Area | Requirement |
| --- | --- |
| Speed | The Matcher adds at most 10 seconds to a run with 20 searches and 300 candidate cars. The preview endpoint answers within 1 second. |
| Determinism | The same data and criteria always give the same tiers, scores, reasons, and New flags. |
| Explainability | Every Match and Possible match shows its reasons. No score is shown without the chips that produced it. |
| Privacy | Saved lists stay in the local database. Nothing is sent anywhere except the digest. |
| Export | My shortlist adds CSV export of saved cars (with tracking fields) and of a search's current matches. |

## 11. Acceptance checks (the reviewer will verify these)

Fixtures use the worked example in section 1 and the similar cars from the 2026-10-20 data.

1. ☆ Save on All inventory saves 5UX23EU0XT9462524. The row, its Find a car card, and its car page all show ★ Saved. The snapshot holds all eight feature statuses, paint, interior, MSRP $87,125, and cash price $87,949.
2. "Find cars like this" on that car prefills exactly the search in section 5.2. The preview counts equal the Match and Possible match counts written after saving.
3. With that search: 5UX23EU01T9541662 (Dravit Grey / Cognac, DAP `YES`, $88,225) is a **Match** with reasons including "+$276". 5UX23EU02T9552461 (DAP not listed) is a **Possible match**, never a Match. 5UX23EU0XT9439258 ($92,869) is excluded by the ±$3,000 band. The base car is not in its own results.
4. Creating the search writes baseline rows only. The next run's digest lists only VINs that appear after creation. A fixture run that adds one grey, light-interior, DAP `YES` car within the band shows exactly one New line, and a replay of that run sends nothing more.
5. A fixture where a sticker turns DAP from `UNKNOWN` to `YES` on a Possible match produces one "Now a full match" event.
6. A dealer that is `NOT_REPORTED` today: its cars produce no New, no "Not listed today", and no saved-car "Missing". A `REPORTED` dealer where the saved VIN is absent shows "Missing, unverified", never "Sold".
7. A saved car's price drop of $1,000 shows on Saved cars and in the digest as "▼ $1,000". A NULL to value change shows "Price appeared", not a drop.
8. A pending-review car never appears as a Match, Possible match, or New.
9. If the Matcher raises, the run is still `PUBLISHED`, the digest carries the "could not be computed" line, and `carshop match --run-id` produces the same rows a normal run would.
10. Write endpoints reject a cross-origin request, a missing CSRF token, and any request when `web_host` is not loopback. No write endpoint can change observations, staging, review items, or runs (test by route inventory).
11. The Matcher node appears in the compiled graph between `supervisor_publish` and `notifier`, is listed in the agent catalog as deterministic, and passes the one-writer test (it writes only `matches`).
12. My shortlist has the four tabs with working `?tab=` links. Shop vocabulary rules pass (`tests/test_buyer_vocabulary.py` extended to the new tabs). The menu badge equals the New count.
13. "Save this wish list as a search" on Find a car, then "Open in Find a car", round-trips the same criteria.
14. All existing tests and gates pass.

## 12. Delivery steps

| Step | Deliver | Exit |
| --- | --- | --- |
| 1. Data and saving | Migration for the four tables, color-family reference data, write endpoints with the guards in section 9, ☆ Save controls, Saved cars tab with tracking, CLI `shortlist`. | Checks 1, 6 (saved-car part), 7, 8, 10. |
| 2. Searches | Criteria schema, "Find cars like this" form with preview, Saved searches tab, wish-list round trip, CLI `search`. | Checks 2, 3, 13. |
| 3. Matcher agent | `shortlist.match` pure functions, A10 node in the graph and catalog, baseline-on-create, New and full-match events, `carshop match`. | Checks 4, 5, 9, 11. |
| 4. Showing it | New today tab, menu badge, Find a car tags, car page panel, digest section, Live pipeline events for the Matcher (after `requirements-mas.md` events exist). | Checks 12, 14. |

Steps 1 and 2 do not depend on the remaining LangGraph work. Step 3 needs only the graph from `requirements-mas.md` Step 1, which is already merged.

## 13. Out of scope

- Contacting dealers, holding a car, or any purchase step (`requirements.md` §3).
- Model-based similarity or "AI picks". The Matcher is rules only; a model step would need a change to `requirements.md` §4.
- Price prediction or "best time to buy".
- Multiple users or shared lists (needs authentication first).
- Push notifications, SMS, or browser notifications. The digest is the only channel.

## 14. Change log

| Version | Date | Change |
| --- | --- | --- |
| 2.2 | 2026-10-20 | Saved cars with a full snapshot and tracking, saved searches built from a base car or the wish list, a deterministic A10 Matcher agent that runs after every published run and announces new similar cars once, My shortlist with four tabs, a digest section, and a narrow, guarded write path for the owner's lists. |
