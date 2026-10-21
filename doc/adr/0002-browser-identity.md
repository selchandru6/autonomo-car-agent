# ADR 0002 — Browser identity for the render fallback

Status: accepted · Date: 2026-10-19 · Amended 2026-10-20 (review H5)

## Context

Most Texas BMW dealer sites sit behind a bot-management edge: Dealer Inspire stores behind
Cloudflare, Dealer.com stores behind Akamai. Probing on 2026-10-19/20 showed:

- Plain HTTP (`httpx`, `curl`) returns 403 regardless of the `User-Agent` string; the block is
  made at the TLS/HTTP fingerprint level, before any header is read.
- Playwright's headless Chromium with its default UA (`HeadlessChrome/…`) also returns 403.
- The same Chromium build with the token `Headless` removed from its UA string, i.e. the
  string the non-headless build of that exact version sends, renders every page normally.
- Appending the product token `carshop/0.1` to that UA is accepted by both edges.
- Appending `carshop/0.1 (+https://github.com/...)` — the URL-in-parentheses form that
  classic crawlers use — is rejected by Akamai with "Access Denied" (8 of 27 Texas dealers).

Requirements v1.3 §4 (Compliance) says: *Honor robots. Identify the fetcher honestly in the
HTTP and browser user agents. Do not bypass blocks, sign-in, or rate limits.* §7.4 forbids
CAPTCHA solving, proxies, TLS impersonation, and anything but GET/HEAD.

## Decision

`fetch.browser.BrowserRenderer` launches the Chromium that Playwright installed. The default
mode is `browser_ua_mode="identified"`:

- **User-Agent**: `Mozilla/5.0 (<real OS>) … HeadlessChrome/<major>.0.0.0 Safari/537.36 carshop/0.1`.
  The OS token comes from `platform.system()` (and `platform.mac_ver()` on macOS). The Chrome
  major version comes from the running browser. `HeadlessChrome` stays in the string.
- **X-Carshop**: the full HTTP identity, `carshop/0.1 (+https://github.com/selchandru6/autonomo-car-agent)`.
- **From**: the operator's contact address when `CARSHOP_CONTACT_EMAIL` is set (RFC 9110 §10.1.2).

The HTTP client (`fetch.http`) identifies as `carshop/0.1 (+https://github.com/…)` and is what
`robots.txt` is evaluated against (`safety.robots`, matched by product token).

`chrome` mode is removed. A UA with no product token and no `From` / `X-Carshop` headers was
an impersonation path, which §8 and F24 forbid. The only other mode is `native`, which sends
Playwright's own UA unchanged and is not the default.

The 2026-10-19 probe found that Akamai returns 403 to a `HeadlessChrome` token. Keeping the
token is the compliance choice in review H5. A dealer that blocks it is `NOT_REPORTED`, not
silently fetched under a desktop Chrome identity.

The renderer still:

- fetches and honours `robots.txt` every run;
- blocks image/font/media requests to reduce load on the dealer;
- renders each page at most once per run and pauses 5–10 s between requests to one host;
- stops at the first CAPTCHA or interstitial and records `CAPTCHA`;
- never clicks through a challenge, never submits a form, never uses a proxy.

## Why this is honest and not evasion

Every request names the fetcher (`carshop/0.1`) in the User-Agent and carries the project URL
in a header. The UA also says `HeadlessChrome` and names the machine's real operating system.
We do not impersonate a different browser, drop the headless token, spoof TLS, rotate
addresses, or defeat any challenge. If a dealer blocks that, the dealer is `NOT_REPORTED` /
`HTTP_403` and the owner is told.

We chose the token-only UA form rather than the `(+url)` form because the latter is treated by
Akamai as a generic-crawler signature and would silently turn eight dealers into gaps for a
formatting reason rather than a policy one. The project URL is still sent on every request, in
`X-Carshop`. The owner can revert to the `(+url)` UA form by setting
`CARSHOP_BROWSER_PRODUCT_TOKEN="carshop/0.1 (+https://…)"` and accepting those gaps.

## Consequences

- A 27-dealer statewide run takes roughly 15–30 minutes with four dealers in parallel,
  dominated by politeness pauses.
- `terms_status` is `owner_accepted_risk` for every registry dealer under the owner's blanket
  decision of 2026-10-19 (requirements v1.3 §7.1); it is recorded for audit, not as a gate.
- A Chromium update changes the UA automatically; no code change is needed.
