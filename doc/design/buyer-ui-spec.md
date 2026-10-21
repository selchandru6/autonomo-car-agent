# Buyer screen: implementation spec

> **Updated by [`../requirements-web-ui.md`](../requirements-web-ui.md) (2026-10-20).** The page now lives inside the single app shell as "Find a car", "Today's check" moves to `/check`, `/operator` redirects there, and the brand tokens and theme toggle apply to every page. Everything else in this spec still governs the page's contents.

**Author:** Claude (reviewer and designer) · **Implementer:** Cursor · **Approved by:** owner, 2026-10-20. The owner approved the mockup design and asked for two additions: [Lowest and highest priced cars](#lowest-and-highest-priced-cars-owner-request) and a [Light / dark mode toggle](#light--dark-mode-toggle-owner-request). Both are in scope and must ship with the page.
**Mockup (owner-reviewed):** https://claude.ai/artifact/FAnoxnVSdEePU7kuDt8BSw. Open it and click through it before you start.
**Reference files in this folder:**
- [buyer-mockup.template.html](buyer-mockup.template.html): the mockup's markup, CSS and JS. `__DATA__` is replaced by the export.
- [buyer-mockup-export.py](buyer-mockup-export.py): the read-only export that fed it. Its queries show exactly which fields the screen needs.

**Spec links:** §1 Purpose, §11 Screen, §12 Buyer preferences, F17, F20, F18.

## Why

The owner is a car buyer. Today's Overview shows pipeline internals: `REPORTED`, `NO_SOURCE`, `UNKNOWN`, run ids, “eligible units”, and a scatter labeled “comparison price”. A buyer can't tell from it which car to buy, how many cars are available, or whether prices are moving.

The owner asked for:
- a screen that answers buyer questions in plain language;
- a **wish list** (budget, location, colors, must-have packages) that narrows and ranks the cars;
- no unexplained or invented numbers.

## Scope

1. **New default page `/`: “Texas X5 Finder”.** Build it as in the mockup.
2. **Move the current technical Overview to `/operator`.** Keep it working, along with `/health`, `/runs`, `/review` and `/trends`. Link it from a small “Operator view” link in the footer. Nothing in the pipeline changes.
3. **No new data.** Everything on the buyer page comes from existing tables. If a value doesn't exist, the page says so in words.

## Rules that apply everywhere on the buyer page

| Rule | Detail |
| --- | --- |
| Plain words, no internal codes | Never show `REPORTED`, `PARTIAL`, `NOT_REPORTED`, `NO_SOURCE`, `UNKNOWN`, `VALIDATED`, reason codes, run ids, observation ids, “eligible”, or “comparison price”. Use the vocabulary table below. |
| No invented values | Never fill a gap with a guess, an average, or a placeholder number. A missing price shows its reason, and a package the page doesn't mention shows “Not listed”. The price-change trend uses same-VIN pairs only (F18), and shows “Steady”/“needs more days” when there is no change. |
| One data source | The page reads the latest **published statewide** run (`latest_published_run(full_registry=True)`), plus any later single-dealer re-runs for dealer status. It shows that run's date as “checked <date>”. |
| Price shown | `comparison_price_cents` with `price_basis = vehicle_only`: the vehicle's cash price before tax, title and dealer fees. Always show it next to the MSRP as “$X above/below MSRP $Y” or “At MSRP $Y”. |
| Unknown is never yes | A must-have whose feature status isn't `YES` puts the car in “Might fit”, never in “Fit”. A nice-to-have that isn't `YES` scores 0. |

### Vocabulary

| Internal | Buyer page says |
| --- | --- |
| feature `YES` | ✓ Driving Assist Pro, etc. (the package's short name) |
| feature `UNKNOWN` | “? Driving Assist Pro”; tooltip: “The dealer's page doesn't mention it. Ask the dealer.” |
| `comparison_price_cents` NULL + `PRICE_IS_PAYMENT` | “Lease/finance payment only” |
| … + `CONDITIONAL_ONLY` | “Price only after conditional rebates” |
| … + `CALL_FOR_PRICE` | “Call for price” |
| … with MSRP only | “Only MSRP shown” + “MSRP $X · ask the dealer” |
| `in_transit` | “On the way to the dealer” (otherwise “On the lot”) |
| `units.first_seen_at` | “first seen Oct 19”. It means when *we* first saw the car, not when the dealer listed it. |
| dealer `REPORTED` | listed under “Dealers we checked”, with its car count |
| dealer `NO_SOURCE` | “Website not on file yet” |
| dealer `NOT_REPORTED` | “Couldn't read the website” |
| dealer `PARTIAL` | “Partly checked” |
| dealer platform/adapter | never shown |

## Page layout (follow the mockup)

**Left rail (stacks on top under 900px):** My wish list.

**Header:** page name, “checked <date>”, and the **light/dark toggle** at the right.

**Right column**, top to bottom:

1. **Answer strip:** four numbers.
2. **Lowest and highest priced cars:** two full car cards, always shown. *New; see below.*
3. **Where prices sit today:** dot strip.
4. **Cars for you:** ranked cards.
5. **Price changes.**
6. **Dealers we checked:** collapsed by default.

**Compare tray:** fixed at the bottom while 1–3 cars are ticked.

### 1. Wish list (left rail)

| Control | Values | Effect |
| --- | --- | --- |
| Budget | slider $74k–$94k in $500 steps. The top stop means “No limit”. | Hard limit. A car over budget is hidden and counted in “N cars hidden by your limits: … over budget”. A car with no cash price is kept, and “Within budget” counts as not known. |
| Location | “Anywhere in Texas” or “Near <dealer city>” (one entry per dealer city, positioned at the mean of that city's dealer coordinates from the locator snapshot). Then radius chips: 50, 100, 200 mi, Any. | Hard limit when a radius is set (great-circle miles to the dealer). Picking a city defaults the radius to 200 mi. |
| Packages | For each of the 8 prioritized features (§7.7): **Must / Nice / Skip**. Show the count of cars where that package is listed. | Must: only `YES` is a fit; otherwise the car goes to “Might fit”. Nice: `YES` counts as one wish met. |
| Exterior colors | chips built from the colors present in the run, each with a swatch and count | Preference: one wish, met if the car's color is selected. |
| Interior colors | same, from interior color | Same as exterior. |
| Only show these colors | checkbox | Turns both color preferences into hard limits. |
| Include cars on the way | checkbox (default on) | Hard limit when off. |
| Reset | link | Restores the defaults below. |

**Defaults** come from `config/preferences.yaml` (§12): DAP = Must, panoramic roof = Nice, massage = Nice, grey exteriors (Skyscraper, Brooklyn, Dravit, Frozen Pure Grey), and non-black interiors. Budget: no limit, because `max_price_cents` is null. Location: anywhere.

**Persistence:** the browser's `localStorage` (key `x5finder.wish.v1`), wrapped in try/catch. The web app stays read-only for Phase 1 (review finding M3). A later CLI command `carshop prefs export` can write the browser wish list back to `preferences.yaml`; that is out of scope here.

**Color names:** normalize the raw strings before building chips. Drop “Metallic”, change “Gray” to “Grey”, and map to the reference names (`reference/data.py`). Interior chips are the color word (Black, Cognac, Coffee, Silverstone, Tartufo, Ivory White); the material (Sensafin or Merino leather) is shown on the card. See `canon_ext` / `canon_int` in the export script. Swatch hex values are approximate and for recognition only. Keep them in one constant.

### 2. Answer strip

| Tile | Value | Caption |
| --- | --- | --- |
| Matches | count of cars in “Fit” | “cars fit your wish list · N more might” |
| Lowest | lowest price among the matches, or the lowest in Texas if there are no priced matches | Say which of the two it is. |
| Typical | median price of all priced cars | “most sell for $Q1–$Q3” (25th–75th percentile) |
| Trend | “▼ Going down” / “▲ Going up” / “● Steady” | “N price changes across M cars since <first day>”, or “needs two days of checks” |

The trend rule: take VINs priced on at least two published days. A change means the first and last price differ by at least $1 (§7.6). The direction is the majority of drops vs rises. **Never** compare medians across days: the dealer set differs from day to day.

### Lowest and highest priced cars (owner request)

The owner wants to **always** see the full details of the cheapest and the most expensive car on the market, whatever the wish list says.

- **Placement:** directly under the answer strip, a panel titled **“Price range today”** with two cards side by side (stacked under 600px): **“Lowest price in Texas”** and **“Highest price in Texas”**.
- **Which cars:**
  - the car with the minimum and the car with the maximum `comparison_price_cents` among *all* cars in the run;
  - only cars with a cash price count, because a car without a price can't be lowest or highest;
  - the wish list, budget, distance and color limits **do not** apply to this panel.
  - On a tie, take the earlier `first_seen_at`, then the lower VIN, so the choice is stable.
- **Card contents:** the same full card as in “Cars for you”:
  - the price and the difference from MSRP;
  - the dealer, city, and distance when a city is chosen;
  - exterior and interior with swatches;
  - every **listed** package as ✓ (all 8, not only the wished-for ones), plus “? name” for wished-for packages that aren't listed;
  - on the lot / on the way, and first seen;
  - Compare checkbox and **View at dealer ↗**.
- **Wish-list status:** each card also shows one line saying how it relates to the wish list:
  - “Fits your wish list · meets X of Y wishes”;
  - “Might fit: not listed: <must-haves>”;
  - or “Outside your limits: <reasons>”, for example “over budget” or “too far”.

  The buyer sees both extremes and knows at once whether either is a candidate.
- **Spread line:** under the two cards, one line: “**$X difference** between the lowest and highest price · N cars have a cash price.”
- **Edge cases:**
  - if only one car has a price, show one card titled “Only car with a cash price”;
  - if none do, show: “No dealer is showing a cash price right now.”
- **Data:** this uses the same payload as the rest of the page. No new queries.

On the 2026-10-20 run, the lowest and highest priced cars must match the ends of the “Where prices sit today” strip.

### 3. Where prices sit today

A horizontal dot strip, one dot per priced car, in $500 bins, stacked. Fill the dots that fit the wish list with the accent color, and the others neutral. Draw ticks every $2,500 from the floor to the ceiling of the data. When a budget is set, add a dashed vertical line labeled “Your budget”. The tooltip shows the price, colors and dealer. ECharts (already used) or plain SVG are both fine; it must be drawn to scale.

### 4. Cars for you

- **Two sections:** “Fit your wish list” and “Might fit: the dealer doesn't list a must-have”. The second has the help line: “These cars may still have the package. Dealer pages often leave it out, so ask the dealer before ruling them out.”
- **Then one line:** “N cars hidden by your limits: 3 over budget, 5 too far …”
- **Sort:** Best match (wishes met, then lower price), Lowest price, or Nearest. Show the first 6 per section and a “Show all N” button.
- **Card contents:**
  - the price, or its reason in words;
  - the price vs MSRP;
  - the badge “Meets X of Y wishes” or “Not listed: <must-haves>”;
  - the dealer, city, and distance when a city is chosen;
  - exterior and interior with swatches;
  - package tags: ✓ listed, or dashed “? name” for wished-for but not listed;
  - on the lot / on the way;
  - first seen;
  - a **Compare** checkbox (max 3);
  - **View at dealer ↗**, linking to `detail_url`, else `source_url`.
- **Best match:** the first card under “Best match” sort gets an accent border.
- **Empty state:** “No cars fit right now. Try a higher budget, a wider distance, or turn a must-have into a nice-to-have.”

### 5. Price changes

List same-VIN price changes, newest first: the new price, ▼/▲ and the dollar amount, “since <date>”, the dealer and the color. When there are none, say: “None of those N cars changed price. A clearer trend appears after about a week of daily checks.” The data is `price_events` (§7.8), or recompute it from the observation history as the export does.

### 6. Dealers we checked

Two lists. Dealers that were read, with their car counts, and dealers that weren't, with the plain reason from the vocabulary table. The summary line reads “19 of 27 Texas dealers checked”.

### Compare tray

A table with up to 3 cars side by side: price, MSRP, dealer, exterior, interior, the 8 packages (“✓ Listed” / “Not listed”), availability and VIN. Hide / Clear buttons.

## ApexMoonAI brand theme (owner request)

The buyer page must match the owner's site, https://www.apexmoonai.com. These values come from that site's own CSS (`:root` block); use them as the page's tokens. The site is **dark-first**, so dark is the brand look and light is the alternative for the toggle.

| Token | Dark (brand, default) | Light (toggle) | Use |
| --- | --- | --- | --- |
| `--bg` | `#020617` (site `--bg-void`) | `#f4f7fb` | page background |
| `--surface` | `#0a0f1a` (site `--bg-surface`) | `#ffffff` | panels |
| `--sunk` | `#0f172a` (site `--bg-elevated`) | `#eef3f9` | cards, inputs |
| `--ink` | `#F9FAFB` (site `--text-primary`) | `#0b1220` | text |
| `--muted` | `#9ca3af` (site `--text-secondary`) | `#475569` | secondary text |
| `--line` | `rgba(255,255,255,.08)` (site `--border-subtle`) | `#dbe3ee` | borders |
| `--accent` | `#22D3EE` (site `--accent-cyan`) | `#0e7490` (darker cyan for AA contrast on white) | matches, links, focus |
| `--accent-2` | `#0EA5E9` (site `--accent-cyan-dark`) | `#0369a1` | gradients |
| `--gold` | `#FBBF24` (site `--accent-gold`) | `#b45309` | budget line, “Must”, highest-price tag |
| `--good` | `#34d399` | `#047857` | ✓ listed, “Fits” badge |

- **Fonts (Google Fonts, same as the site):** **Syne** for headings, **DM Sans** for body text, numbers and prices, and **JetBrains Mono** for small uppercase labels, the VIN and chart ticks.
- **Typography rules (owner-reported alignment bug, 2026-10-20):**
  - **Syne is for headings only** (page title and section titles), at weight 700, never 800, with letter-spacing 0. Syne is very wide; in prices and summary numbers it made “$74,250 / $83,237 / Steady” overlap.
  - **Every price and number** (summary tiles, card prices, budget, spread) uses DM Sans 700, `font-variant-numeric: tabular-nums`, letter-spacing −0.02em. Card prices are 28px and summary numbers 34px.
  - **No gradient text on numbers.** The cyan→gold gradient turned “0” into an oval; the match count is plain accent cyan.
  - **The summary strip** fits 4 tiles in one row at desktop width: `repeat(auto-fit, minmax(min(100%, 175px), 1fr))`, each tile `min-width: 0`, numbers `white-space: nowrap`.
  - **Card status line** (“Meets X of Y wishes”, “Not listed: …”, “Outside your limits: …”) goes on its **own line under the price**, left-aligned, as a small 6px-radius label. It never sits beside the price in a pill.
  - **The “Lowest/Highest price in Texas” tags** are sized to their text (`width: max-content`), never stretched to the column.
  - **Count-up animation never runs on first load.** The first render shows the real values at once, and counting runs only when the wish list changes, from the old value to the new one. A mid-animation frame must never show a number that isn't real.
- **Background:** the site's two soft radial glows, cyan top-left and gold top-right, at about 10% / 7% opacity, fixed.
- **Signature details from the site:**
  - the cyan→gold gradient on the headline number (“cars fit your wish list”);
  - a pulsing green status dot next to “checked <date>”;
  - small mono uppercase eyebrow labels;
  - 12px card radius, 6–8px control radius;
  - a cyan glow on the best-match card and on hover.
- **Easing:** `--ease: cubic-bezier(0.16, 1, 0.3, 1)` (the site's `--ease-out-expo`) and `--spring: cubic-bezier(0.34, 1.56, 0.64, 1)` (the site's `--ease-spring`).
- **Footer:** “An ApexMoonAI project”.

The updated mockup (same link) shows all of this. [buyer-mockup.template.html](buyer-mockup.template.html) is the reference CSS and JS.

## Favicon and app mark (owner request)

- **File:** [favicon.svg](favicon.svg), a 64×64 SVG of a **generic SUV side silhouette** (X5-like proportions: tall body, short window band, long roof, long hood). The body is brand cyan on void navy, with a gold “moon” and a gold headlight.
- **Use it as:**
  - the favicon: `<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">`;
  - a 180×180 PNG export for `apple-touch-icon`;
  - the 40px app mark beside the page title.
- **Don't use the BMW logo or name in the icon.** The BMW roundel and wordmark are BMW AG trademarks. Using them as this site's icon would make an unofficial tool look like an official BMW product, which is the impersonation risk the spec avoids. The SUV silhouette says “X5” without that. Dealer and model names stay in the page text, where they describe the cars.

## Animations and actions (owner request)

Motion follows the site's own vocabulary: a fade-up reveal with expo easing, cards that lift on hover, the pulsing status dot, and spring presses. Every animation:
- runs **once** or on a user action;
- never hides content while waiting (nothing waits at `opacity: 0` for a scroll observer; elements start visible or near-visible and settle in);
- is disabled under `prefers-reduced-motion: reduce`.

| Moment | Animation | Spec |
| --- | --- | --- |
| Page load | Panels rise in, staggered | `fadeUp` from `opacity .35; translateY(18px)` to rest, 800ms `--ease`, 90ms stagger (header, rail, summary, range, chart, cars, changes, dealers) |
| Status | Live dot pulses | site `pulse-dot`: scale 1→.8, opacity 1→.5, 2s loop |
| Summary numbers | Count up to the new value when the wish list changes, **never on first load** | 550ms ease-out quart, from the previous value |
| Price strip, first draw | Dots pop in | scale 0→1, 550ms `--spring`, 7ms stagger per dot |
| Price strip, wish change | Dots re-color in place (cyan ↔ neutral); no redraw | `fill` transition 400ms |
| Car list, wish/sort change | **FLIP re-rank:** cars that stay glide to their new position; newly matching cars fade up | Web Animations API, 520ms `--ease`; new cards 420ms with 35ms stagger |
| Card hover (pointer devices) | Lift 4px, cyan border, soft shadow | site `.pillar:hover` |
| Best match | Cyan ring + glow | static |
| Chips / Must-Nice-Skip | Press scale .94 with spring return | 250ms `--spring` |
| “View at dealer ↗” | Arrow nudges up-right on hover | 300ms `--spring` |
| Theme toggle | Sun/moon icon rotates and swaps; page colors cross-fade | 500ms spring on the icon; 350ms color transition applied **only during the switch** (class on `<html>` for 450ms) |
| Wish list change | “✓ Saved” fades in beside “on this device” | 1.4s |
| Compare | Tray slides up from the bottom; toast “Added to compare” | 450ms `--ease` |
| Reset / theme switch | Small toast (“Wish list reset”, “Dark mode”, “Following your system setting”) | 1.8s, `role="status"` |
| “Dealers we checked” | Chevron rotates on open | 300ms |

**Performance:** animate only `transform` and `opacity` (and SVG `fill`). Use no animation libraries; plain CSS plus the Web Animations API is enough.

## Light / dark mode toggle (owner request)

- **Default:** with no saved choice, follow the operating system. Brand dark is what users with a dark OS setting see.
- **Control:** a round button in the page header, top right, on the buyer page **and** on every operator page (`base.html`).
  - It shows a sun icon when the dark theme is on (meaning “switch to light”) and a moon when the light theme is on.
  - It carries visible text or an `aria-label`: “Switch to light mode” / “Switch to dark mode”.
  - It is reachable by keyboard and has a visible focus ring.
- **Behavior:**
  - **First visit:** follow the operating system (`prefers-color-scheme`), with no attribute set.
  - **A click** sets `data-theme="light"` or `data-theme="dark"` on `<html>` and saves the choice in `localStorage` (key `x5finder.theme`, wrapped in try/catch).
  - **Later visits** apply the saved choice.
  - **Back to system:** a small “Use system setting” link next to the toggle, or a third state on the button (System → Light → Dark), clears the saved choice. Either is fine, but the user must be able to return to following the OS.
- **No flash:** apply the saved theme **before first paint**, with a tiny inline script in `<head>` that reads `localStorage` and sets `data-theme`. It must run before the stylesheet renders, so a dark-mode user never sees a white flash on reload.
- **Tokens:** every color comes from CSS custom properties. The light values sit on `:root`, and the dark values are defined twice: under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { … } }` and under `:root[data-theme="dark"] { … }`, each with `color-scheme: dark`. The mockup template already has this token structure; reuse it.
  - No component may use a literal color. That includes chart axis text, tooltip backgrounds, swatch borders and the compare tray.
  - ECharts and SVG charts must read their colors from the tokens and **re-render on toggle**. Listen for the click and for `matchMedia('(prefers-color-scheme: dark)')` changes.
- **Operator pages:** these currently use hard-coded Tailwind slate classes (dark only). Move them to the same tokens, so the toggle works there too. If that is too large for this change, at minimum the buyer page must fully support both themes, and the operator pages get the toggle in a follow-up. Note which option you chose in the PR.
- **Contrast:** body text and price numbers must meet WCAG AA (4.5:1) in both themes. Check the good/warn badges and the dashed “not listed” tags in dark mode especially.

## Implementation notes

- **Route:** `GET /` renders `templates/finder.html`. The data comes from a new pure function `views.buyer_payload(conn)` that returns the same shape as the mockup's `D` object (`asOf`, `runId`, `days`, `dealers[]`, `cars[]` with `history`). Use the export script's queries as the reference, reading the stored `comparison_price_cents` and `price_basis` instead of recomputing.
- **Rows:** only cars with staging `VALIDATED` and latest non-rejected version (§7.9, F22) appear.
- **Client side:** ship the payload as JSON in the page, and do the wish-list filtering in the browser, as the mockup does. At about 100–300 cars the payload is roughly 100 KB, which is fine. There are no new endpoints, and the app stays read-only.
- **Styling:** the mockup's own tokens (light and dark). Keep Tailwind for the operator pages if you prefer, but the buyer page must not reuse their slate “internal dashboard” styling.
- **Accessibility:** every control has a label; the segmented Must/Nice/Skip buttons use `aria-pressed`; focus is visible; the layout works at 400px width.

## Acceptance checks (the reviewer will verify these)

1. `/` shows no internal code or id anywhere (grep the rendered HTML for `REPORTED|NO_SOURCE|UNKNOWN|VALIDATED|run_id`). The only exception is the operator link.
2. With the defaults on the 2026-10-20 statewide run, “Fit” = cars whose DAP is `YES` (23 in that run) and “Might fit” = the rest (81).
3. Setting the budget to $80,000 hides exactly the priced cars above $80,000 and reports the count. Unpriced cars stay, with their reason.
4. Choosing “Near Houston, within 50 mi” shows only Houston, Katy and The Woodlands dealers' cars, with distances.
5. With a must-have set, no card in “Fit” lacks that package as ✓.
6. The trend tile reads “● Steady” and “no price changes across 42 cars since Oct 19” on the 2026-10-20 data. A fixture with one same-VIN drop of $1,000 shows “▼ Going down” and the change card.
7. The dealers list shows 27 dealers, split into checked and not checked, using only the plain reasons.
8. The wish list survives a reload, and Reset restores the defaults.
9. The operator Overview still works at `/operator`.
10. **Lowest/highest panel:**
    - It always shows the minimum- and maximum-priced cars in the run, even when the wish list hides them from “Cars for you”. Example: set the budget below the highest price; the highest card stays, labeled “Outside your limits: over budget”.
    - Its prices equal the two ends of the dot strip.
    - Every listed package appears on both cards.
    - The spread line equals highest minus lowest.
11. **Theme toggle:**
    - The toggle switches the buyer page between light and dark, and the choice survives a reload with no white flash.
    - With no saved choice, the page follows the OS setting.
    - “Use system setting” restores that behavior.
    - Charts re-color on toggle, and no element keeps a light-only or dark-only literal color (check the chart axes, tooltips and compare tray in both themes).
12. Both themes pass WCAG AA contrast for body text, prices and badges.
13. **Brand:**
    - The page uses the token values in the brand table and the Syne / DM Sans / JetBrains Mono fonts.
    - Side by side with apexmoonai.com in dark mode, the background, cyan and gold match.
14. **Favicon:** the browser tab and the header show the SUV mark from `doc/design/favicon.svg`. No BMW logo appears anywhere in the app's own branding.
15. **No overlapping or squeezed text:** at 1280px, 900px and 400px wide, in both themes:
    - no two text elements overlap;
    - the four summary tiles sit on one row at 1280px;
    - every card shows its price, then its status line, then the dealer, left-aligned.

    Take screenshots at those three widths and attach them to the PR.
16. **Motion:**
    - Each row of the animation table works.
    - With the OS “reduce motion” setting on, nothing animates, and every element is fully visible on load.
    - No content stays hidden until scrolled into view.
