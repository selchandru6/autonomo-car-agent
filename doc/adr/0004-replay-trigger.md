# ADR 0004 — `Trigger.REPLAY`

Status: accepted · Date: 2026-10-20

## Context

Requirements v1.3 lists the run triggers an operator can start: cron, a manual date, a manual
dealer, and catch-up. The code also has `Trigger.REPLAY`. Review L2 asked for this to be
documented rather than removed. The replay gate in §13 says reprocessing a run does not
duplicate observations or send another email.

## Decision

`replay` reprocesses one existing run (`rerun_of` is that run's id) through the same publish
path as a live run. It is not a schedule. `carshop run --trigger replay` leaves `--notify` off,
and `execute_run` forces `notify=False` for this trigger even if a caller passes it. A replay
therefore never sends the digest.

Storing the same dealer result again does not insert a second observation, fetch attempt,
exclusion, or review item (review M1). Charts keep reading the latest non-rejected observation
version.

## Consequences

- A replay cannot be mistaken for the morning run: `scheduled_run_exists` counts only `cron`
  and `catchup`.
- An operator who wants a digest after a correction uses `carshop digest`, not replay.
