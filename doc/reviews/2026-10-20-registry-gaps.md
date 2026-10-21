# Registry gaps: the 8 `NO_SOURCE` dealers (2026-10-20)

**Reviewer:** Claude (reviewer role; Cursor implements)
**Companion to:** [2026-10-20-code-review.md](2026-10-20-code-review.md) (finding L3)
**Spec:** [doc/requirements.md](../requirements.md) v1.3, §7.1, F1, F25, §16.3

## Summary

The statewide run `20261020T052908Z-manual_date-a78f54` read 17 of 27 Texas dealers. Of the other 10:

- **Temple and Cardenas are already fixed.** Later single-dealer runs (`…cf2ecd`, `…5fe7ba`) report both as `REPORTED`. They will count on the next full run.
- **8 dealers are `NO_SOURCE`:**
  - 7 because BMW's locator lists no website for them;
  - 1 (College Station) because the website the locator lists is dead.

I identified the official website, platform, and X5 inventory URL for all 8. They are below as **proposed** `overrides.yaml` entries, together with the risks Cursor must check before trusting each one.

**Expected result:** if all 8 overrides work, coverage goes from 17/27 (63%) to up to **27/27**. Two sites (South Austin and College Station) returned HTTP 403 to my fetcher, so they may end `NOT_REPORTED / HTTP_403`. That is a legitimate outcome under §3 (no bot evasion), not a bug.

## How identity was confirmed

Each dealer was matched to BMW's locator record through **center number + locator phone number + address**, not by name alone. The locator's own name for a store can differ from its public brand: “BMW of Plano” is Sewell BMW of Plano, and “BMW of Clear Lake” is Advantage BMW Clear Lake. Evidence is listed per dealer.

## Instructions for Cursor

1. **Do not change code for this document's section A.** Section A is data: copy the YAML in [A.9](#a9-proposed-overridesyaml-copy-after-checks) into `src/carshop/config/registry/overrides.yaml` only after you have done that dealer's “Check before use” items.
2. For each dealer, run a single-dealer run (`carshop run --dealer <id>`) and record the resulting status, `pages_read`, `raw_count`, and `eligible_count`. If the check fails, leave that entry out and write the reason under the dealer's heading.
3. Section B lists **code findings** discovered during this analysis. Fix them like any other review finding (IDs G1–G4), with tests.
4. Then run one full statewide run and attach the per-dealer `dealer_runs` table for re-review.

---

## A. Per-dealer findings

### A.1 Plano: center 50923 → Sewell BMW of Plano
- **Evidence:** the locator phone 214-778-2600 matches Sewell BMW of Plano, 6800 Dallas Pkwy, Plano TX 75024. That is the former Classic BMW address; Classic BMW is closed. `www.bmwofplano.com/hours-and-directions/` names “Sewell BMW of Plano”, with the same address and phone.
- **Website:** `https://www.bmwofplano.com`. This is a **store-specific** domain, not the shared `sewell.com` group site, so the §7.1 group-site risk is smaller.
- **Platform:** `dealer_inspire` (assets on `dealerinspire.com`).
- **Start URL:** `https://www.bmwofplano.com/new-vehicles/x5/`
- **Check before use:**
  - every listed car must be at the Plano store, with no “available at Sewell Grapevine” sister-store units;
  - if sister-store units appear, add the site's store filter to the start URL.
- **Duplicates:** the registry also holds inactive seed rows `sewell-bmw-plano` and `sewell-bmw-grapevine` (host `www.sewell.com`). Remove them (see G3) so Plano can never be counted twice. `bmw-grapevine` (`www.bmwofgrapevine.com`) is already `REPORTED` and is the Grapevine store.

### A.2 McKinney: center 52938 → BMW of McKinney (Hendrick)
- **Evidence:** name and address match, 2601 N Central Expy, McKinney TX 75071. The site lists 945-234-4984; the locator phone 945-234-6400 is in the same exchange.
- **Website:** `https://www.bmwofmckinney.com`
- **Platform:** `dealer_inspire`
- **Start URL:** `https://www.bmwofmckinney.com/new-vehicles/x5/`
- **Check before use:** none beyond the standard single-dealer run.

### A.3 South Austin: center 44235 → BMW of South Austin (Hendrick)
- **Evidence:** the locator phone 512-438-7500 matches, at 5501 S I-35 Frontage Rd, Austin TX 78744. It is a Hendrick store, listed on hendrickcars.com.
- **Website:** `https://www.bmwofsouthaustin.com`
- **Platform:** `dealer_com` (`/new-inventory/index.htm`, `/dealership/staff.htm`, `/contact.htm` URL patterns).
- **Start URL:** `https://www.bmwofsouthaustin.com/new-inventory/index.htm?model=X5`. This is the same pattern as the working Dallas and San Antonio rows.
- **Check before use:** the homepage returned **HTTP 403** to my fetcher. If the carshop fetcher also gets 403 after the one allowed browser attempt, the correct result is `NOT_REPORTED / HTTP_403`. **Do not** add workarounds (§3, F24). Keep the override anyway, so the dealer is attempted and the block is visible in coverage.

### A.4 Clear Lake (Webster): center 51029 → Advantage BMW Clear Lake
- **Evidence:** the locator phone 281-557-7000 matches Advantage BMW Clear Lake, 15943 Gulf Fwy, Webster TX 77598. The locator's own scheduling key is `webKey=advantbmwclrlake`.
- **Website:** `https://www.bmwofclearlake.com`
- **Platform:** `dealer_inspire`
- **Start URL:** `https://www.bmwofclearlake.com/new-vehicles/x5/`
- **Check before use:** my fetch of that URL showed **all models (265 vehicles, 3 pages)**, not just X5. The X5 filter may be applied client-side. With the adapter's browser path, confirm the result is X5-only, or at least that all 3 pages are read and the dealer ends `REPORTED`, not `PARTIAL`. Excluding non-X5 cars is fine. Missing pages is not.

### A.5 Beaumont: center 53089 → BMW of Beaumont (Doggett Auto Group)
- **Evidence:** the locator phone 409-840-3585 matches, at 1855 I-10 S, Beaumont TX 77701. The site currently shows 409-515-7289.
- **Website:** `https://www.bmwofbeaumont.com`
- **Platform:** **RideMotive** (assets on `app.ridemotive.com`). There is **no dedicated adapter**, so use `other` and the generic order in §7.4 (JSON-LD, then the page's inventory JSON, then a browser render).
- **Start URL (needs confirmation):** `https://www.bmwofbeaumont.com/cars/new-inventory`. I could not find a URL-level X5 filter: the model filter appears to be JavaScript-only, and there is no JSON-LD in the server HTML.
- **Path prefixes:** `["/cars/", "/inventory/"]`. VDPs use both `/inventory/New-2026-BMW-X5-xDrive40i-<VIN>` and `/inventory/new-2026-bmw-x5-xdrive40i-...-<vin>/`.
- **Check before use:** this is the dealer most likely to end `LAYOUT_UNKNOWN`. If the generic adapter can't read it, record that result. Adding a RideMotive adapter is a new-platform code change with golden pages (§7.1), so propose it separately.
- **Note for C4 (price basis):** Beaumont's VDP shows “MSRP $83,700 + doc fee $225 + ceramic tint $595 = total price $84,520”. That is another fees-included price.

### A.6 Lubbock: center 50857 → BMW of Lubbock
- **Evidence:** the locator phone 806-763-8041 is the service line of BMW of Lubbock, 1120 19th St, Lubbock TX 79401 (sales 806-224-2753). The locator's CDK scheduling link is consistent.
- **Website:** `https://www.bmwoflubbock.com`
- **Platform:** `dealer_com` (footer: “Website by Dealer.com”).
- **Start URL:** `https://www.bmwoflubbock.com/new-inventory/index.htm?model=X5`
- **⚠ Trap:** the homepage's own X5 link is `/new-inventory/index.htm?year=2027&make=BMW&model=X5&gvBodyStyle=SUV`. **Do not copy it.** `year=2027` would hide every 2026 car, and the dealer would report a false 0. Discovery (F25) must also never adopt a year-filtered link (see G4).

### A.7 Wichita Falls: center 54314 → BMW of Wichita Falls (Grubbs)
- **Evidence:** name and address match, 315 Central East Fwy, Wichita Falls TX 76301. The Grubbs Family of Dealerships acquired it in June 2025. The locator phone 940-766-0293 is the shared Grubbs (Cadillac) line at the same address; the site lists 940-687-8376.
- **Website:** `https://www.bmwwichitafalls.com`. Use this, not the group site `grubbswichitafalls.com`.
- **Platform:** `dealeron` (footer: “Copyright © 2026 by DealerOn”).
- **Start URL:** `https://www.bmwwichitafalls.com/searchnew.aspx?Make=BMW&Model=X5`
- **⚠ Trap:** the homepage's X5 link is `/searchnew.aspx?Make=BMW&ModelAndTrim=X5:sDrive40i`, which is filtered to **sDrive40i**, the out-of-scope trim. Copying it would make every in-scope car invisible. Discovery must not adopt trim-filtered links either (see G4).
- **Check before use:** the inventory is JavaScript-rendered (skeleton loaders in the HTTP HTML), so expect the browser path. Temple, also on DealerOn, needed adapter fixes; confirm Wichita Falls ends `REPORTED`.

### A.8 College Station: center 43380 → BMW of College Station (locator website is dead)
- **Evidence:** the locator lists `https://www.bmwofbrazosvalley.com`, which fails the TLS handshake on `www` and refuses connections on the bare domain (`54.243.57.127:443`). The store now operates `bmwofcollegestation.com`. Search results there list new 2026 X5 xDrive40i and xDrive50e units. The locator phone is 979-314-2000.
- **Website:** `https://www.bmwofcollegestation.com`. Set `hostname` in the override, because the locator's host is wrong.
- **Platform:** probably `dealerfire` (Dealer eProcess-style `/search/new-bmw-x5-college-station-tx/?…&tp=new` URLs). **Confirm from markup** before setting it, or leave it `unknown` and let `_readapt` switch.
- **Start URL:** `https://www.bmwofcollegestation.com/search/new-bmw-x5-college-station-tx/?cy=77845&md=38&tp=new`
- **Path prefixes:** `["/search/", "/inventory/", "/new-"]`. Confirm the VDP path on first fetch.
- **Check before use:** the listing returned **HTTP 403** to my fetcher. Same rule as South Austin: if blocked, record `NOT_REPORTED / HTTP_403` and do not work around it.

### A.9 Proposed `overrides.yaml` (copy after checks)

`operator` and `set_at` are required by `Override` in `registry/build.py`. The owner approved this analysis on 2026-10-20; record the operator as `owner`.

```yaml
overrides:
  - center_number: "50923"   # Sewell BMW of Plano (locator name "BMW of Plano"; no website in locator)
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwofplano.com
    start_url: https://www.bmwofplano.com/new-vehicles/x5/
    path_prefixes: ["/new-vehicles/", "/inventory/"]
    platform: dealer_inspire
    notes: "Sewell store-specific site; verify no sister-store units (review A.1)"

  - center_number: "52938"   # BMW of McKinney (Hendrick)
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwofmckinney.com
    start_url: https://www.bmwofmckinney.com/new-vehicles/x5/
    path_prefixes: ["/new-vehicles/", "/inventory/"]
    platform: dealer_inspire

  - center_number: "44235"   # BMW of South Austin (Hendrick)
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwofsouthaustin.com
    start_url: https://www.bmwofsouthaustin.com/new-inventory/index.htm?model=X5
    path_prefixes: ["/new-inventory/", "/new/"]
    platform: dealer_com
    notes: "403 to reviewer's fetcher; a block is NOT_REPORTED/HTTP_403, never bypassed (review A.3)"

  - center_number: "51029"   # Advantage BMW Clear Lake (locator name "BMW of Clear Lake")
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwofclearlake.com
    start_url: https://www.bmwofclearlake.com/new-vehicles/x5/
    path_prefixes: ["/new-vehicles/", "/inventory/"]
    platform: dealer_inspire
    notes: "X5 filter may be client-side; confirm all pages read (review A.4)"

  - center_number: "53089"   # BMW of Beaumont (Doggett) - RideMotive, no dedicated adapter
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwofbeaumont.com
    start_url: https://www.bmwofbeaumont.com/cars/new-inventory
    path_prefixes: ["/cars/", "/inventory/"]
    platform: other
    notes: "RideMotive; X5 filter is JS-only; LAYOUT_UNKNOWN likely until an adapter exists (review A.5)"

  - center_number: "50857"   # BMW of Lubbock
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwoflubbock.com
    start_url: https://www.bmwoflubbock.com/new-inventory/index.htm?model=X5
    path_prefixes: ["/new-inventory/", "/new/"]
    platform: dealer_com
    notes: "Do NOT use the homepage X5 link: it is year=2027-filtered (review A.6)"

  - center_number: "54314"   # BMW of Wichita Falls (Grubbs)
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwwichitafalls.com
    start_url: https://www.bmwwichitafalls.com/searchnew.aspx?Make=BMW&Model=X5
    path_prefixes: ["/searchnew.aspx", "/new-", "/inventory/"]
    platform: dealeron
    notes: "Do NOT use the homepage X5 link: it is sDrive40i-filtered (review A.7)"

  - center_number: "43380"   # BMW of College Station - locator host bmwofbrazosvalley.com is dead
    operator: owner
    set_at: "2026-10-20T00:00:00+00:00"
    hostname: www.bmwofcollegestation.com
    start_url: https://www.bmwofcollegestation.com/search/new-bmw-x5-college-station-tx/?cy=77845&md=38&tp=new
    path_prefixes: ["/search/", "/inventory/", "/new-"]
    platform: unknown
    notes: "Platform likely dealerfire - confirm; 403 to reviewer's fetcher (review A.8)"
```

---

## B. Code findings from this analysis

### G1 Connection and TLS failures are recorded as `TIMEOUT` and retried with 30 s/90 s backoff
- **Where:** [http.py:99](../../src/carshop/fetch/http.py#L99): `except httpx.HTTPError` → `ReasonCode.TIMEOUT`, `"transport: …"`.
- **Evidence:** College Station's discovery recorded `homepage status=None error=TIMEOUT`. The real causes were a TLS handshake failure and a connection refused. Because `TIMEOUT` is retryable, each dead host costs about 2 minutes of sleeps per URL.
- **Requirement:** §7.4 (retries apply only to 429, 503 and timeouts), §7.3 (machine-readable reason).
- **Fix:** catch `httpx.TimeoutException` first, which is already done. Map `httpx.ConnectError` and SSL errors to a non-retryable reason. Either add `CONNECT_FAILED` to `ReasonCode`, with a one-line note in an ADR because it extends the §7.3 vocabulary, or reuse `TIMEOUT` but without retry. Keep the exception text in `detail`.
- **Test:** a respx mock raising `httpx.ConnectError` returns after 1 attempt with no sleep and the new reason code.

### G2 Discovery can't tell “no website” from “dead website”
- **Where:** [registry/discovery.py](../../src/carshop/registry/discovery.py) (the `homepage_unreachable` path).
- **Problem:** a locator host that refuses connections or fails TLS on every run (College Station) looks the same as a temporary outage. The operator never learns that the locator data is stale.
- **Fix:**
  - Record the failure class from G1 in `discovery_reason` (`homepage_connect_failed`, `homepage_tls_failed`, `homepage_timeout`).
  - On the coverage screen (F11), show “locator website unreachable for N consecutive refreshes — operator override needed (§16.3)”.
- **Test:** discovery against a mocked `ConnectError` stores `homepage_connect_failed`.

### G3 Inactive Sewell seed rows linger in `dealers`
- **Where:** `dealers` rows `sewell-bmw-plano` and `sewell-bmw-grapevine` (`active=0`, host `www.sewell.com`, **no `center_number`**, 0 observations), left over from the v1.2 hand-made registry. They are labeled `source='locator'`, which is wrong: they never came from the locator.
- **Problem:**
  - Once Plano gets an override (A.1), the same store exists twice: the stale row and the locator row `bmw-plano` (center 50923). If anything reactivates the stale row, Plano would be fetched and counted twice.
  - A `locator` row without a center number also breaks the rule that locator rows are keyed by center (§7.1, F1).
- **Fix:**
  - An Alembic data migration deletes `dealers` rows with `center_number IS NULL` and no observations.
  - If a row has observations, keep it inactive with `source='seed'` and `notes = "superseded by center <n>"`.
  - Make `upsert_dealers` refuse a `source='locator'` row without a center number.
- **Test:** after `refresh_registry`, every `source='locator'` row has a center number, and no inactive row duplicates a locator row's city and brand.

### G4 Discovery must reject year- or trim-filtered inventory links
- **Where:** [registry/discovery.py](../../src/carshop/registry/discovery.py) (candidate selection from homepage links).
- **Evidence:** two live homepages link “X5” to filtered pages:
  - Lubbock: `?year=2027&...&model=X5`, which hides every 2026 car;
  - Wichita Falls: `?ModelAndTrim=X5:sDrive40i`, which hides every xDrive40i.
  If discovery adopts either one, the dealer reports a believable but **false zero** every day. No error appears, and the §4 silent-zero rule doesn't fire if the dealer never had a count.
- **Fix:** when scoring candidates, strip or reject query parameters that filter on year or trim: `year`, `Year`, `ModelAndTrim` with a trim, `trim`, `gvTrim`, and `cy` when it is a year. Keep model-only filters. Prefer a candidate whose listing parses to at least one X5 of any year.
- **Test:** golden homepage fixtures containing those two links produce start URLs without the year or trim filter.

---

## C. After this lands, expected coverage

| Dealer | Expected status | Note |
| --- | --- | --- |
| Temple, Cardenas | `REPORTED` | already fixed |
| Plano, McKinney, Clear Lake | `REPORTED` | DealerInspire; adapter exists |
| Lubbock, South Austin | `REPORTED` or `NOT_REPORTED / HTTP_403` | Dealer.com; South Austin blocked the reviewer |
| Wichita Falls | `REPORTED` | DealerOn; browser path |
| College Station | `REPORTED` or `NOT_REPORTED / HTTP_403` | blocked the reviewer |
| Beaumont | likely `NOT_REPORTED / LAYOUT_UNKNOWN` | RideMotive needs an adapter |

**Realistic outcome:** 24–27 of 27 dealers `REPORTED` (89–100%), up from 17. That meets or approaches the §8 target of 90%.

## Sources

- [Sewell BMW of Plano (sewell.com)](https://www.sewell.com/locations/bmw-of-plano/) · [bmwofplano.com hours and directions](https://www.bmwofplano.com/hours-and-directions/) · [Classic BMW closed, same address (Yelp)](https://www.yelp.com/biz/classic-bmw-plano-3)
- [BMW of McKinney: contact](https://www.bmwofmckinney.com/contact-us/) · [about](https://www.bmwofmckinney.com/about-us/)
- [BMW of South Austin](https://www.bmwofsouthaustin.com/) · [Hendrick listing](https://www.hendrickcars.com/texas/austin/bmw-of-south-austin.htm)
- [BMW of Clear Lake](https://www.bmwofclearlake.com/) · [X5 page](https://www.bmwofclearlake.com/new-vehicles/x5/) · [Advantage BMW-MINI Clear Lake (Carfax)](https://www.carfax.com/Reviews-Advantage-BMW-MINI-Clear-Lake-Webster-TX_TIZ27HH001)
- [BMW of Beaumont](https://www.bmwofbeaumont.com/) · [example 2026 X5 xDrive40i VDP](https://bmwofbeaumont.com/inventory/New-2026-BMW-X5-xDrive40i-5UX23EU06T9369161) · [Doggett Auto Group](https://www.doggettautogroup.com/)
- [BMW of Lubbock](https://www.bmwoflubbock.com/) · [Carfax](https://www.carfax.com/Reviews-BMW-of-Lubbock-Lubbock-TX_WTLEQ6VWVM)
- [BMW of Wichita Falls](https://www.bmwwichitafalls.com/why-buy) · [Grubbs acquisition (News Channel 6)](https://www.newschannel6now.com/2025/06/27/grubbs-family-dealerships-acquires-bmw-wichita-falls/) · [Grubbs locations](https://www.grubbswichitafalls.com/locations.html)
- [BMW of College Station: new X5 search](https://www.bmwofcollegestation.com/search/new-bmw-x5-college-station-tx/?cy=18045&md=38&tp=new) · [CarEdge](https://caredge.com/dealer/bmw-of-college-station--tx-college-station)
