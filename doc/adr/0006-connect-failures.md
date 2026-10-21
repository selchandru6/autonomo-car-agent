# ADR 0006 — Connection and TLS failures are not timeouts

Status: accepted · Date: 2026-10-20

## Context

Review G1. `HttpFetcher` mapped every `httpx.HTTPError` to `ReasonCode.TIMEOUT`. A refused
connection and a failed TLS handshake were then retried with the 30 s / 90 s backoff that
§7.4 reserves for 429, 503, and real timeouts. College Station's dead locator host cost
about two minutes per URL and was recorded as a timeout.

## Decision

`ReasonCode` gains two values, which extends the §7.3 vocabulary:

- `CONNECT_FAILED` — `httpx.ConnectError` that is not a TLS failure. Not retried.
- `TLS_FAILED` — the exception chain contains `ssl.SSLError`, or the message says
  certificate, TLS, or SSL. Not retried.

`TimeoutException` stays `TIMEOUT` and stays retryable. The exception text stays in
`detail`. Discovery stores the class as `homepage_connect_failed`, `homepage_tls_failed`,
or `homepage_timeout`.

## Consequences

A dead host fails in one attempt. The coverage screen can tell a stale locator website
from a timeout.
