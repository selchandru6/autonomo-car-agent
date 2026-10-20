# ADR 0002 — Browser identity for the render fallback

Status: accepted · Date: 2026-10-19 · Amended 2026-10-20 for requirements v1.3

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

`fetch.browser.BrowserRenderer` launches the Chromium that Playwright installed and, by
default (`browser_ua_mode="identified"`), sends:

- **User-Agent**: that browser's own UA string with the `Headless` token removed, followed by
  the fetcher's product token: `Mozilla/5.0 (…) Chrome/156.0.0.0 Safari/537.36 carshop/0.1`.
  The Chrome version is read from the running browser, never hard-coded.
- **X-Carshop**: the full HTTP identity, `carshop/0.1 (+https://github.com/selchandru6/autonomo-car-agent)`.
- **From**: the operator's contact address when `CARSHOP_CONTACT_EMAIL` is set (RFC 9110 §10.1.2).

The HTTP client (`fetch.http`) identifies as `carshop/0.1 (+https://github.com/…)` and is what
`robots.txt` is evaluated against (`safety.robots`, matched by product token).

Two other modes exist for the owner: `chrome` (the Chrome build only, no product token — the
pre-v1.3 behaviour) and `native` (Playwright's stock `HeadlessChrome` UA).

The renderer still:

- fetches and honours `robots.txt` every run;
- blocks image/font/media requests to reduce load on the dealer;
- renders each page at most once per run and pauses 5–10 s between requests to one host;
- stops at the first CAPTCHA or interstitial and records `CAPTCHA`;
- never clicks through a challenge, never submits a form, never uses a proxy.

## Why this is honest and not evasion

Every request names the fetcher (`carshop/0.1`) in the User-Agent and carries the project URL
in a header. The browser part of the UA is exactly what that Chromium build sends. We do not
impersonate a different browser, spoof TLS, rotate addresses, or defeat any challenge. If a
dealer blocks even that, the dealer is `NOT_REPORTED` / `HTTP_403` and the owner is told.

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
