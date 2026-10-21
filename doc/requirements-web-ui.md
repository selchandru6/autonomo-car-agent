# AI car shopping — unified web UI

**Product:** AI car shopping (app name on screen: **Texas X5 Finder**)  
**Revision:** 2.1 (screen addendum to [`requirements.md`](requirements.md) v1.3 and [`requirements-mas.md`](requirements-mas.md) v2.0)  
**Status:** Implementation baseline  
**Date:** 2026-10-20  
**Implementer:** Cursor · **Reviewer:** Claude · **Approver:** owner  
**Prototype:** https://claude.ai/artifact/A9ucSRqVE7EdMTzmqooNC2 (source: [`design/pipeline-live-mockup.html`](design/pipeline-live-mockup.html)). It shows the app shell, the left menu, and a working Pipeline page. Open it and click through the menu before you start.

**Supersedes:**
- `requirements-mas.md` section 7 (the Pipeline screen now lives in section 8.1 of this file);
- in [`design/buyer-ui-spec.md`](design/buyer-ui-spec.md): Scope item 2 (operator Overview at `/operator`, footer "Operator view" link) and the "Light / dark mode toggle" note that operator pages may defer tokens to a follow-up;
- the two separate page shells (`templates/finder.html` as a standalone document and `templates/base.html` for everything else).

This file is the single screen requirement. It does not change what the pipeline collects or how it classifies anything. `requirements.md` §11 (views, filters, empty states, "every number links", no forecast) still holds on every page. `buyer-ui-spec.md` still governs the contents of the Find a car page, except where this file says otherwise.

Words **MUST**, **MUST NOT**, and **SHOULD** are normative.

## 1. Goals

| ID | Goal |
| --- | --- |
| U1 | **One app.** Every screen, old and new, lives in one shell with a persistent left menu. No page is a standalone document with its own header and theme. |
| U2 | **Easy to find.** Every screen is reachable in one click from the menu. Any VIN, stock number, dealer, city, or run is reachable in one search from any page. |
| U3 | **One look.** One token set (the ApexMoonAI brand from `buyer-ui-spec.md`) in light and dark, on every page including the operator pages and charts. |
| U4 | **See the pipeline.** The animated Pipeline page (section 8.1) is a first-class menu item, and the sidebar shows when a run is live. |
| U5 | **Nothing lost.** Every existing page keeps its content and its URL, or redirects to its new URL. |

## 2. Today (what this replaces)

| Route | Template | Shell | Problem to fix |
| --- | --- | --- | --- |
| `/` | `finder.html` | standalone, brand theme, own theme toggle | No app menu. Contains a second view (`?view=check`) that is really a separate page. |
| `/?view=check` | `finder.html` | standalone | Linked from the old menu as "Today's check". Should be its own route. |
| `/operator` | redirect | — | Redirects to `/?view=check`. |
| `/inventory`, `/catalog`, `/vin/{vin}`, `/shortlist`, `/trends`, `/health`, `/review`, `/runs` | `*.html` extending `base.html` | slate sidebar, Tailwind slate classes, dark-first literals | Different look from `/`. Light theme is patched with `!important` overrides in `app.css`. Menu icons are unicode glyphs. |
| — | `overview.html` | — | No route renders it. Orphan. |
| `/pipeline`, `/architecture`, dealer pages, search | — | — | Do not exist yet. |

## 3. Information architecture

### 3.1 Left menu

Four groups, in this order. Labels are what the user sees. Icons are inline SVG from one sprite (section 5.4), never emoji or unicode glyphs.

| Group | Item | Route | Audience | Badge / live state |
| --- | --- | --- | --- | --- |
| **Shop** | Find a car | `/` | buyer | — |
| | Shortlist | `/shortlist` | buyer | — |
| **Market** | Eligible cars | `/inventory` | buyer | — |
| | All inventory | `/catalog` | buyer | — |
| | Dealers | `/dealers` | buyer | — |
| | Price & stock trends | `/trends` | buyer | — |
| **Pipeline** | Live pipeline | `/pipeline` | operator | pulsing dot and "Live" while a run is `RUNNING` |
| | Today's check | `/check` | operator | — |
| | Data health & coverage | `/health` | operator | count of dealers not `REPORTED` in the shown run, when > 0 |
| | Runs & digest | `/runs` | operator | — |
| | Review queue | `/review` | operator | pending review count, when > 0 |
| **System** | How it works | `/architecture` | both | — |

Not in the menu, reached by links and search:

| Page | Route | Reached from |
| --- | --- | --- |
| Car detail | `/vin/{vin}` | any car card or row, search |
| Dealer detail | `/dealers/{dealer_id}` | Dealers, Health, car detail, search |
| Pipeline replay of an older run | `/pipeline?run_id=…` | Runs, the Pipeline run picker |
| Search results | `/search?q=…` | search box (no-JavaScript fallback and "See all results") |
| API docs | `/api/docs` | sidebar footer link |

### 3.2 Vocabulary by group

- **Shop** and **Market** pages follow the buyer vocabulary table in `buyer-ui-spec.md`: no internal status codes, reason codes, run ids, or observation ids in visible text. Where an operator needs the code, put it inside a closed "Technical details" disclosure at the bottom of the page.
- **Pipeline** and **System** pages are operator pages. They show codes (`REPORTED`, `NOT_REPORTED / HTTP_403`, run ids) as today.

### 3.3 Routes and redirects

| Old | New | Code |
| --- | --- | --- |
| `/?view=check` | `/check` | 301 |
| `/operator` | `/check` | 301 |
| every other existing route | unchanged | — |

Existing query parameters (`run_id`, filters, `sort`) keep working on every route.

## 4. App shell

One base template, `templates/base.html`. **Every** page template, including `finder.html`, MUST `{% extends "base.html" %}`. There is one `<head>`, one theme script, one stylesheet entry, one menu.

### 4.1 Layout

```
┌──────────────┬──────────────────────────────────────────────────────────────┐
│ [mark] Texas │  Page title · breadcrumb      [ Search  ⌘K ]  Run ▾  CSV XLSX ☾ │
│ X5 Finder    ├──────────────────────────────────────────────────────────────┤
│              │                                                              │
│ SHOP         │                                                              │
│  Find a car  │                     page content                             │
│  Shortlist   │                     (max width 1440px)                       │
│ MARKET       │                                                              │
│  …           │                                                              │
│ PIPELINE     │                                                              │
│  Live ● Live │                                                              │
│  …           │                                                              │
│ SYSTEM       │                                                              │
│  How it works│                                                              │
│ ──────────── │                                                              │
│ Run status   │                                                              │
│ v0.1 · sha   │  footer disclaimer                                           │
└──────────────┴──────────────────────────────────────────────────────────────┘
```

| Width | Sidebar |
| --- | --- |
| ≥ 1280px | Expanded, 248px, labels and badges visible. |
| 1024–1279px | Collapsed icon rail, 64px. Labels show as tooltips and on keyboard focus. |
| < 1024px | Hidden. A menu button in the top bar opens it as a drawer over the page with a scrim. Esc, the scrim, or choosing an item closes it. |

- A collapse button at the bottom of the sidebar toggles expanded and collapsed at any width ≥ 1024px. The choice is saved in `localStorage` key `x5finder.sidebar` (try/catch; a missing value falls back to the width rule).
- On `/` the sidebar starts **collapsed** below 1440px, because Find a car has its own wish-list column. The user's saved choice wins.
- The current item has `aria-current="page"` and a visible active style (accent bar and tinted background), not color alone.
- Group headings are small mono uppercase labels, not links.

### 4.2 Sidebar header and footer

- **Header:** the 40px SUV app mark (`static/favicon.svg`), "Texas X5 Finder", and a second line "2026 X5 xDrive40i · Texas". The whole block links to `/`.
- **Footer, run status card:**
  - latest run date, trigger, and status chip (`RUNNING`, `PUBLISHED`, `FAILED`);
  - "as of" age (the existing `age` filter);
  - coverage "R / A dealers read" and "N no source" (existing `coverage_summary`);
  - model status ("on" / "off, rules only");
  - while a run is `RUNNING`, the card shows a pulsing dot and "Watch live" linking to `/pipeline`.
- **Footer, small print:** `v{{ version }} · {{ git_sha }}`, a link to API docs, and "An ApexMoonAI project".
- In the collapsed rail the run status card shrinks to a single status dot with a tooltip.

The run status card is fed by a small endpoint `GET /api/status` (wraps the existing `pipeline.run.status_summary`) polled every 30 seconds, and every 5 seconds while a run is `RUNNING`. It never blocks page render; the first paint uses server-rendered values.

### 4.3 Top bar

Sticky at `top: 0`, on every page.

| Element | Rule |
| --- | --- |
| Menu button | Visible below 1024px only. `aria-expanded`, `aria-controls`. |
| Page title | `{% block heading %}`. On detail pages, a breadcrumb: "Eligible cars / 5UX23EU0XT9529123". The parent is the page the user came from when it is in the menu, else the menu parent defined per page in section 8. |
| Search | Section 6. Shows the shortcut hint "⌘K" on macOS and "Ctrl K" elsewhere. Collapses to an icon button below 640px. |
| Run picker | Only on run-scoped pages (section 7.1). Lists published runs, as the current picker does. |
| Export | CSV and XLSX links, only on Eligible cars, All inventory, Shortlist, and Runs. They carry the shown `run_id`. |
| Theme toggle | One round button (sun / moon) plus "Use system setting", exactly as `buyer-ui-spec.md` "Light / dark mode toggle". This replaces the separate toggles on `/` and in today's `base.html`. |

### 4.4 Footer

The existing disclaimer from `base.html` ("Advertised cash prices exclude tax…Unknown is not No.") on every page, in muted small text.

## 5. Design system

### 5.1 Tokens

- One token set in `static/app.css`: the brand table in `buyer-ui-spec.md` ("ApexMoonAI brand theme"), light values on `:root`, dark values under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }` and again under `:root[data-theme="dark"]`, each with `color-scheme: dark`.
- Add the tokens the operator pages need, in both themes:

| Token | Use |
| --- | --- |
| `--ok-bg`, `--ok-fg` | `REPORTED`, `PUBLISHED`, ✓ |
| `--warn-bg`, `--warn-fg` | `PARTIAL`, pending review |
| `--bad-bg`, `--bad-fg` | `NOT_REPORTED`, `FAILED`, errors, filtered-out counts |
| `--none-bg`, `--none-fg` | `NO_SOURCE`, queued, unknown |
| `--model`, `--model-soft` | model-assisted agent badge, model spend meter |
| `--sidebar-bg` | sidebar surface |

- **No literal colors in templates or inline styles.** Remove the Tailwind `slate-*`, `sky-*`, `emerald-*`, `amber-*` color classes from every template, and delete the `html[data-theme="light"] … !important` override block in `app.css`. Tailwind MAY stay for spacing and layout utilities only. If kept, pin the Play CDN to an exact version.
- Status chips are one component (`.chip` plus a status modifier) used on every page, replacing `.badge .b-*`.

### 5.2 Type

Syne (headings only, 700), DM Sans (body, prices, numbers, tabular figures), JetBrains Mono (eyebrow labels, VINs, run ids, reason codes, chart ticks), with the typography rules in `buyer-ui-spec.md`. These apply to every page, not only `/`.

### 5.3 Charts

One `mkChart` helper in `base.html` reads colors from the CSS tokens at render time and re-renders every chart on theme change (`x5finder-theme` event and the `prefers-color-scheme` media query). No hard-coded hex values in chart options.

### 5.4 Icons

One SVG sprite at `static/icons.svg` with `<symbol>`s for the menu items, search, theme, collapse, external link, and status. 24px grid, 1.75px stroke, `currentColor`. Lucide (ISC license) shapes are acceptable; vendor the needed symbols, do not load a remote icon font.

### 5.5 Motion

The `buyer-ui-spec.md` "Animations and actions" rules apply everywhere: animate only `transform`, `opacity`, and SVG `fill`; nothing waits hidden for a scroll observer; everything is off under `prefers-reduced-motion: reduce`. Shell-specific motion:

| Moment | Animation |
| --- | --- |
| Sidebar collapse / expand | width 240ms `--ease`; labels fade |
| Drawer open (mobile) | slide in from the left 280ms `--ease`; scrim fades |
| Live pipeline menu item | the site `pulse-dot` while a run is `RUNNING` |
| Command palette open | scale .98 → 1 and fade, 160ms |

## 6. Search

### 6.1 Command palette

- Opens from the top-bar search box, **⌘K / Ctrl K**, or **/** when focus is not in a text field.
- Modal dialog (`role="dialog"`, `aria-modal`), focus trapped, Esc closes and returns focus.
- Results grouped, at most 6 per group, in this order:

| Group | Matches | Opens |
| --- | --- | --- |
| Pages | menu labels and synonyms ("coverage" → Data health, "digest" → Runs, "agents" → How it works) | the page |
| Cars | full VIN, or the last 6–8 VIN characters, or stock number | `/vin/{vin}` |
| Dealers | name or city, case-insensitive substring | `/dealers/{dealer_id}` |
| Runs | run id prefix or snapshot date (`2026-10-20`, `oct 20`) | `/pipeline?run_id=…` for operator context, with a secondary "Runs & digest" action |

- Arrow keys move, Enter opens, ⌘/Ctrl Enter opens in a new tab. "See all results" opens `/search?q=…`.
- With an empty query the palette lists Pages and the last 5 items the user opened (`localStorage` key `x5finder.recent`, try/catch).

### 6.2 API

`GET /api/search?q=&limit=6` returns `{ "pages": [...], "cars": [...], "dealers": [...], "runs": [...] }`. Each item has `label`, `sublabel`, `href`. Rules:

- Minimum 2 characters, except a full 17-character VIN.
- Reads committed rows only. Cars come from the latest published run first, then any earlier observation of that VIN (labelled "last seen <date>").
- Read-only, parameterized queries, no full-table scans on each keystroke: index or an in-memory cache rebuilt on each publish.
- The client debounces input by 150ms and cancels stale requests.

`GET /search?q=` renders the same groups as a full page for no-JavaScript use.

## 7. Cross-page rules

### 7.1 Run scope

- Run-scoped pages (Eligible cars, All inventory where applicable, Shortlist, Trends, Health, Review, Today's check) read `?run_id=`; without it they use the latest published run, as today.
- When the user has picked a run, menu links to other run-scoped pages carry the same `run_id`. Non-run-scoped pages (Find a car, Dealers, How it works) drop it.
- Every run-scoped page shows the run date and "as of" age in the top bar.
- Find a car keeps its own rule from `buyer-ui-spec.md` (latest published statewide run).

### 7.2 Links between pages

| From | Link to |
| --- | --- |
| Any car card or row | `/vin/{vin}` |
| Any dealer name | `/dealers/{dealer_id}` |
| Any dealer status chip (operator pages) | that dealer's Pipeline lane for that run: `/pipeline?run_id=…&dealer=…` |
| Funnel numbers on Pipeline | the rows behind them once committed (Eligible cars filtered to the run, Health for exclusions) |
| Find a car "1 · Collect / 2 · Check / 4 · Update" flow steps | `/pipeline` and `/check` |
| Runs table row | `/pipeline?run_id=…` (replay) and the digest |

### 7.3 Empty and error states

Keep the explicit empty states from `requirements.md` §11 on every page (no runs yet, all dealers failed, no in-scope units, stale dealer). The shell renders around them; an empty state never hides the menu. A 404 (unknown VIN, dealer, or run) renders inside the shell with a search box.

## 8. Pages

Existing pages keep their content. "Move into shell" means: extend `base.html`, replace literal colors and slate classes with tokens and shared components, and keep every number, filter, and link working.

| Page | Route | Change |
| --- | --- | --- |
| Find a car | `/` | Move into shell. Remove its own `<head>`, header theme controls, and the "Cars for you / Today's check" switcher (Today's check moves to `/check`). Keep the wish-list column, all panels, compare tray, and animations from `buyer-ui-spec.md`. Its scoped CSS moves to `static/finder.css` and uses the shared tokens. The page heading is "Find a car". |
| Shortlist | `/shortlist` | Move into shell. |
| Eligible cars | `/inventory` | Move into shell. Dealer names link to dealer detail. |
| All inventory | `/catalog` | Move into shell. |
| Car detail | `/vin/{vin}` | Move into shell. Breadcrumb. Each dealer appearance links to dealer detail. |
| Price & stock trends | `/trends` | Move into shell. Charts use token colors and re-render on theme change. Gaps stay gaps. |
| Today's check | `/check` | **New route** for the content of today's `/?view=check`. Move into shell. Its "dealer" rows link to dealer detail. |
| Data health & coverage | `/health` | Move into shell. Status cells link to the Pipeline lane for that dealer and run. |
| Runs & digest | `/runs` | Move into shell. Each run row gets a "Replay" link to `/pipeline?run_id=…`. |
| Review queue | `/review` | Move into shell. The validate / reject form stays the only write action in the app. |
| Live pipeline | `/pipeline` | **New.** Section 8.1. |
| How it works | `/architecture` | **New.** Section 8.2. |
| Dealers | `/dealers` | **New.** Section 8.3. |
| Dealer detail | `/dealers/{dealer_id}` | **New.** Section 8.3. |
| Search results | `/search` | **New.** Section 6. |
| `overview.html` | — | Delete. Before deleting, move any panel it has that `/check` lacks into `/check`. |

### 8.1 Live pipeline (`/pipeline`)

Data comes from the pipeline events and APIs in `requirements-mas.md` section 6 (`/api/pipeline/runs`, `/events`, `/stream` over SSE, `/snapshot`). Until the LangGraph event sink exists, the page MUST work from a fixture event file (`tests/fixtures/pipeline_events.jsonl`) served by the same endpoints in a dev setting, so the screen can be built and tested before the graph.

**Layout, top to bottom:**

1. **Run bar.** Run picker (latest first), run status chip, elapsed time against the 60-minute target with a meter, dealers finished "N / total", model spend against the $0.15 breaker with a meter, mode **Live / Replay**, replay speed (1×, 4×, 16×, 64×), Play / Pause, Restart.
2. **Agent graph.** The nine agents from `requirements-mas.md` §3 as nodes in pipeline order: Supervisor, Collector, Extractor, Validator (validate · filter), Enricher, Validator (completeness gate), Recorder, Comparator, Notifier. The Registry sits above the Supervisor with a dashed "only when snapshot > 30 days" edge. An **Excluded** box sits below Validator. A dashed frame labelled "Dealer subgraph · one branch per dealer · max 4 in parallel · 10 min timeout" surrounds Collector → Recorder. Alternative edges are dashed: Collector → Recorder ("not read / no source"), Validator → completeness gate ("0 eligible"), Notifier → Supervisor ("publish"). Each node shows name, stage, a `RULES` or `MODEL` badge, and "N running · M done". Hovering or focusing a node shows its tools.
3. **Stage funnel.** Nine counters for the run so far: Dealers attempted → Pages collected → Listings extracted → Valid VIN → Filtered out → Eligible → Enriched → Stored (validated) → Pending review.
4. **Two columns** (stack below 1000px):
   - **Dealer branches.** One row per active registry dealer: name, city · platform, six stage cells (Collect, Extract, Validate, Enrich, Gate, Store), and a result (status chip plus reason code or "N eligible"). Running rows sort to the top; queued next; finished last, newest first. A `?dealer=` parameter scrolls to and highlights that row.
   - **Layers** (the seven layers from `requirements-mas.md` §8 plus cross-cutting), highlighted while an agent that uses them is active, with a link "How it works →" to `/architecture`.
   - **Event log.** Newest first, `seq`, time, agent, dealer, message. Filter by agent and by dealer. Error events have a tinted row.

**Animation:**

| Moment | What moves |
| --- | --- |
| Node `start` | Node fills with the accent tint; border pulses until `end`. |
| `handoff` | A dot travels along the edge from source to target, labelled with the record count it carries ("41", "3 pg", "9 cars"). About 650ms plus path length at 1×. |
| Filter | At Validator, the excluded share travels down into the Excluded box (red dot), whose counter increases. |
| Not read | A dealer that ends `NOT_REPORTED` or `NO_SOURCE` sends an amber dot along the dashed Collector → Recorder edge. |
| `error` | The node flashes the bad color once; the dealer row shows the reason. |
| Funnel change | The number briefly takes the accent color. It shows the real value at every frame (no count-up through invented values). |
| Publish | The run chip changes to `PUBLISHED`; the funnel settles. |

Rules:

- Animation is driven only by real events. No invented progress or interpolated counts. A stage is active only between its `start` and `end` events.
- At most 60 dots in flight; extra hand-offs only update counters.
- `prefers-reduced-motion`: no dots, no pulses; state changes still show as color and text.
- The graph scrolls horizontally inside its own container below 1000px; the page body never scrolls sideways.
- Opening a finished run starts in Replay mode at its final frame. Opening the page while a run is `RUNNING` selects it in Live mode.
- Stream lost: banner "Live updates paused, reconnecting", then SSE resume from the last `seq`.
- Run `FAILED`: the failing agent stays highlighted and the error event is pinned to the top of the log.
- No control starts, stops, or changes a run.

The prototype implements this layout and animation against simulated events. Match its behavior; take colors and type from section 5, not from the prototype's palette where they differ.

### 8.2 How it works (`/architecture`)

A static explanation of the system, generated from code (`GET /api/architecture`, `requirements-mas.md` §6.3), not hand-written HTML that can drift.

1. **The pipeline in one picture.** The same agent graph as the Pipeline page, static, with each node linking to its agent card below.
2. **Agents.** One card per agent: name, stage, mode (`rules` / `model`), responsibility, tools (module names), fields it owns. A "Watch it run" link to `/pipeline` with the log filtered to that agent.
3. **Layers.** The seven layers as stacked bands, top (Delivery) to bottom (Sources), each listing its modules, with the allowed-dependency arrows. Cross-cutting concerns shown as a vertical band.
4. **Rules that never bend.** The invariants from `README.md`: unknown is never No; a missing price is NULL; a dealer not read is a gap; a missing VIN is not a sale; every YES/NO has a quote; no bot evasion. Plain sentences, each linking to its requirement section.

### 8.3 Dealers (`/dealers`, `/dealers/{dealer_id}`)

From existing tables only (`dealers`, `dealer_runs`, `observations`, `fetch_attempts`, `robots_snapshots`). No new data.

**`/dealers`:** every registry dealer in one table, sortable and filterable by city and by last result: name, city, cars in the latest published run, lowest price, last successful read, and the last result in buyer words ("Checked", "Partly checked", "Couldn't read the website", "Website not on file yet"). Inactive dealers are listed under a closed "No longer on the BMW locator" section.

**`/dealers/{dealer_id}`:**
- Header: name, city, link to the dealer's own inventory page (external, `rel="noopener"`), last successful read.
- Cars at this dealer in the latest published run, as cards or rows linking to car detail.
- 30-day result strip (one cell per run date, gaps shown as gaps), and the dealer's floor price and stock count over time (same chart rules as Trends).
- "Technical details" disclosure (closed by default): platform, start URL, path prefix, robots status and hash, last fetch attempts for the shown run (URL, HTTP status, latency, reason code), and a link to this dealer's lane on `/pipeline`.

## 9. Technical constraints

- Server-rendered FastAPI + Jinja pages, Alpine for interaction, ECharts for charts, inline SVG for the pipeline graph. No SPA framework. Menu items are ordinary links.
- The app stays read-only except the existing review decision POST.
- New endpoints: `/api/status`, `/api/search`, `/search`, `/check`, `/dealers`, `/dealers/{id}`, `/pipeline`, `/architecture`, plus the pipeline and architecture APIs from `requirements-mas.md` §6.3.
- Front-end libraries load from pinned CDN versions, as today. For an air-gapped host they can be vendored into `static/`.
- Page weight: the shell (HTML, CSS, icons, theme script) adds at most 40 KB gzipped to any page.

## 10. Accessibility and responsive

- Landmarks: `<nav aria-label="Main">` for the menu, `<main>`, `<header>`. A "Skip to content" link is the first focusable element.
- Every menu item, palette result, and control is keyboard reachable with a visible focus ring.
- Badges have text equivalents (`aria-label="3 pending review"`). Live state is announced politely (`aria-live="polite"`) when a run starts or publishes, not on every event.
- WCAG AA contrast (4.5:1 text, 3:1 large text and UI) in both themes, including chips, badges, and chart labels.
- Every page works at 400px, 900px, and 1280px wide with no horizontal page scroll. Wide tables and the pipeline graph scroll inside their own containers.

## 11. Acceptance checks (the reviewer will verify these)

1. Every template except `_macros.html` extends `base.html`. `grep -L 'extends "base.html"' src/carshop/web/templates/*.html` lists only `base.html` and `_macros.html`.
2. Every route in section 3 renders inside the shell with the menu, top bar, and footer. `overview.html` no longer exists.
3. `/?view=check` and `/operator` return 301 to `/check`, and `/check` shows the former "Today's check" content.
4. The menu matches section 3.1: four groups, twelve items, in order, with `aria-current` on the current page. The review badge equals the pending count; the health badge equals the number of non-`REPORTED` dealers in the shown run.
5. While a run is `RUNNING` (fixture), the Live pipeline item and the sidebar run card show the pulsing live state, and "Watch live" opens `/pipeline` in Live mode. After publish, both return to normal within one polling interval.
6. Sidebar: expanded at 1280px, icon rail at 1100px, drawer at 800px. The collapse choice survives reload. On `/` at 1366px it starts collapsed unless the user chose otherwise.
7. Search: ⌘K / Ctrl K and `/` open the palette. Typing the last 6 characters of a VIN in the fixture run finds that car; "houston" finds the Houston-area dealers; "2026-10-20" finds that run; "coverage" finds Data health. Enter opens the result. `/search?q=houston` works with JavaScript disabled.
8. No template or inline style contains a hex, `rgb(`, or Tailwind color class (`grep -E '#[0-9a-fA-F]{3,6}\b|rgb\(|(text|bg|border)-(slate|sky|emerald|amber|rose|indigo)-' src/carshop/web/templates`), apart from `favicon.svg`. The `!important` light-theme override block is gone from `app.css`.
9. Theme: one toggle in the top bar switches every page, survives reload with no flash, and "Use system setting" restores OS-following. Every chart re-colors on toggle.
10. Shop and Market pages pass the `buyer-ui-spec.md` acceptance check 1 grep for internal codes outside "Technical details" disclosures.
11. Pipeline: replaying the fixture event file shows each agent active in order, every dealer ending in the status stored for that run, and the final funnel totals equal to the published run's totals (pages, listings, eligible, excluded, stored, pending). With reduced motion on, no dots or pulses appear and all values still update. Killing and restoring the SSE connection resumes without gaps or duplicate log rows.
12. How it works: adding a module to a layer's package or an agent to the agent registry appears on `/architecture` without editing a template.
13. Dealers: `/dealers` lists all 27 registry dealers with buyer-word results. A dealer detail page shows its cars, its 30-day strip with gaps, and its technical details closed by default.
14. Every number on every page still links to the rows behind it (`requirements.md` F8). Spot-check five numbers across five pages.
15. Screenshots of `/`, `/pipeline`, `/health`, `/dealers/{id}`, and the open search palette at 400px, 900px, and 1280px, in both themes, attached to the PR. No overlapping or clipped text.
16. Existing tests pass. New tests cover: redirects, menu render and `aria-current`, `/api/search` grouping and limits, `/api/status`, dealer pages, and the token grep in check 8.

## 12. Delivery steps

| Step | Deliver | Exit |
| --- | --- | --- |
| 1. Shell and tokens | New `base.html` (menu, top bar, footer, theme, sidebar states), `app.css` tokens and shared components, icon sprite, `/api/status`. Move all operator pages into it. | Checks 1, 2, 4, 6, 8, 9 pass for the operator pages. |
| 2. Find a car and Today's check | `finder.html` extends `base.html`; `/check` route; redirects; `overview.html` removed. | Checks 3, 10 pass; `buyer-ui-spec.md` checks still pass. |
| 3. Search | Palette, `/api/search`, `/search`. | Check 7 passes. |
| 4. Dealers | `/dealers`, `/dealers/{id}`, cross-links. | Check 13 passes. |
| 5. Pipeline and How it works | `/pipeline` on the fixture event file, then on live events once `requirements-mas.md` step 3 lands; `/architecture`. | Checks 5, 11, 12 pass. |
| 6. Polish | Accessibility pass, screenshots, page-weight check. | Checks 14, 15, 16 pass. |

Steps 1–4 do not depend on the LangGraph work and can ship first.

## 13. Change log

| Version | Date | Change |
| --- | --- | --- |
| 2.1 | 2026-10-20 | One app shell with a four-group left menu for every screen, global search, Dealers and How it works pages, the Pipeline page moved here from `requirements-mas.md` §7, and one brand token set on every page. |
