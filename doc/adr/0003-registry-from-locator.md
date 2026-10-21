# ADR 0003 — Registry from the BMW USA dealer locator plus inventory-URL discovery

Status: accepted · Date: 2026-10-20 · Requirements v1.3 (F1, F25, §7.1)

## Context

v1.2 kept a hand-written `dealers.yaml` of six approved dealers. v1.3 makes the registry
*every Texas BMW franchise dealer on the BMW USA dealer locator*, attempted every run under
the owner's blanket approval, with the inventory start URL and platform discovered
automatically. Only the locator decides who exists; discovery decides where to read.

Probing `https://www.bmwusa.com/dealer-locator.html` on 2026-10-20:

- The page's own results come from one GET:
  `/bin/dealerLocatorServlet?getdealerdetailsByRadius/{zip}/{radius}?includeSatelliteDealers=true`.
- `robots.txt` on bmwusa.com disallows `/api` and a few pages; `/bin/dealerLocatorServlet` is
  allowed. We re-check robots on every refresh.
- The response lists dealers with `centerID`, name, address, `dealerURL` (sometimes empty,
  sometimes a bare host), coordinates and phone. A 500-mile radius from Brownwood (76801)
  covers Texas; two extra seeds (El Paso, Brownsville) guard the corners. Results are
  de-duplicated by `centerID`. 27 Texas new-vehicle dealers were returned; 7 list no website.
- The BMW USA edge (Akamai) rejects non-browser TLS clients, so the GET is issued from inside
  the locator page in the same Chromium context the pipeline already uses — the page's own
  XHR path (§7.4 step 2).

## Decision

### Snapshot (F1)

`registry.locator.fetch_snapshot` writes `data/registry/locator-<stamp>.json` (parsed) and
`.raw.json` (verbatim bodies, for audit) and records a row in `registry_snapshots`. A snapshot
older than `CARSHOP_REGISTRY_REFRESH_DAYS` (30) is stale; `execute_run` refreshes before the
first dealer is touched, and `carshop registry refresh` does it on demand.

### Discovery (F25)

`registry.discovery.Discoverer.discover(hostname)` reads only that host (GET; HTTP first,
one browser render if the HTTP body is a shell or blocked):

1. Homepage. One cross-host redirect is followed and recorded as a rename
   (`advantagebmwhouston.com → bmwofhoustonmidtown.com`). 403/CAPTCHA → `homepage_blocked`;
   TLS/DNS/timeout → `homepage_unreachable`.
2. Platform from the markup (`adapters.detect`), with the inventory page as the final authority.
3. robots.txt for the host; candidates it disallows are skipped.
4. Candidates, cleanest first, each verified live before acceptance:
   - a homepage link that is *the* new-X5 page (`/new-vehicles/x5/`, `?model=X5`);
   - the platform's conventional URL;
   - a homepage X5 link with benign filters (make, year, type);
   - the sitemap (one level of nesting);
   - an unfiltered new-inventory page (`unfiltered_new`, the 30-page cap still applies).
   Links that narrow X5 to one variant (`x5-xdrive50e`, a trim filter, "under 1,000 miles") or
   widen it to other models (`X5 + X3`, `X5 PHEV + 330e`) are rejected: the first refresh chose
   several of those before this rule existed.
5. Verification: the platform adapter parses cards or an explicit empty state; for platforms
   without an adapter, the page must be a real document that talks about X5 inventory.

Nothing verifiable → `NO_SOURCE` with a `DiscoveryReason`, still in the denominator.

### Composition and overrides

`registry.build.compose` writes `data/registry/dealers.generated.yaml`:

- `dealer_id` is a slug of the name (`BMW of Tyler → bmw-tyler`) and is **stable**: a center
  number already in the previous generated registry keeps its id, so history attaches.
- An operator override (`config/registry/overrides.yaml`, keyed by center number) wins for the
  fields it sets and must carry `operator` and `set_at`; both are written to the row's notes
  and to `audit_events` (privileged edit, §3).
- A dealer that leaves the locator is kept with `active: false`; database rows not on the
  registry are deactivated with an audit event.
- `terms_status` is `owner_accepted_risk` for every row and does not gate fetching.

### Platforms without an adapter

`adapters.generic_jsonld` reads schema.org `Vehicle`/`Car` blocks (confirmed on the Sewell
group platform, bmwpb.com). A page with no JSON-LD vehicles and no explicit empty state is
`layout_ok=False` → `NOT_REPORTED` / `LAYOUT_UNKNOWN`: the honest signal that a dedicated
adapter (code plus golden pages) is needed. The first statewide run (2026-10-20) produced
exactly one such signal, BMW of Temple, and `adapters.dealeron` was written from its snapshot
the same day (cards as `data-*` attributes, JSON-LD `ItemList` for VINs the lazy grid has
not mounted, `?pt=N` paging). Cardenas BMW (DealerFire) followed the same day:
`adapters.dealerfire` reads the `a[data-vin]` CTA attributes and the `.price_name` /
`.price_value` rows.

The fetched inventory page is the authority on the platform. When the registry row says
`unknown`/`other` but the page's markup matches a dedicated adapter (Cardenas: homepage had no
DealerFire marker, the SRP did), the pipeline switches adapters for that branch and records the
adapter version it actually used.

### Verification of a candidate start URL

A candidate counts only when the platform adapter parses cards and at least half of them are
X5s. The first registry accepted `?superModel=X5` links on five Dealer.com stores; the sites
ignore that parameter and showed their whole new lot, so the daily run read 13-16 pages where
2-3 were needed. The convention `?model=X5` is tried when the link fails the X5-share check.

## Consequences

- First statewide registry (2026-10-20): 27 Texas dealers; 20 with a verified start URL;
  7 `NO_SOURCE` (`no_hostname` ×6 — the locator prints no website for BMW of South Austin,
  Beaumont, Lubbock, McKinney, Plano, Clear Lake, Wichita Falls; `homepage_unreachable` ×1 —
  bmwofbrazosvalley.com fails the TLS handshake for Chromium and httpx alike).
- The coverage ceiling without owner overrides is therefore 20/27 = 74%, below the 90%
  target. Every gap is named; the fix is an override per dealer, not a code change.
- A refresh costs about four minutes (politeness pauses dominate) and touches each dealer
  host three to five times.
