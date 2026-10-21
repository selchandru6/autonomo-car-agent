#!/bin/sh
# Dead-man's switch (requirements 7.10): runs OUTSIDE the crawler process at the digest deadline.
# If no PUBLISHED run exists for today's America/Chicago civil date, say so loudly.
# Exit 0 = healthy, 2 = no digest today. Wire the exit code to whatever pages you (cron MAILTO,
# healthchecks.io ping, etc.). This script never touches dealer sites.
set -u
DATA_DIR="${CARSHOP_DATA_DIR:-./data}"
DB="${DATA_DIR}/carshop.db"
TZ_NAME="${CARSHOP_SCHEDULE_TZ:-America/Chicago}"
TODAY="$(TZ="$TZ_NAME" date +%F)"

if [ ! -f "$DB" ]; then
  echo "HEARTBEAT FAIL: database $DB not found" >&2
  exit 2
fi

COUNT="$(sqlite3 "$DB" "SELECT COUNT(*) FROM runs WHERE status='PUBLISHED' AND snapshot_date='${TODAY}';" 2>/dev/null || echo 0)"
if [ "${COUNT:-0}" -ge 1 ]; then
  echo "HEARTBEAT OK: ${COUNT} published run(s) for ${TODAY}"
  if [ -n "${CARSHOP_HEARTBEAT_URL:-}" ]; then curl -fsS -m 10 "$CARSHOP_HEARTBEAT_URL" >/dev/null 2>&1 || true; fi
  exit 0
fi

RUNNING="$(sqlite3 "$DB" "SELECT COUNT(*) FROM runs WHERE status='RUNNING' AND snapshot_date='${TODAY}';" 2>/dev/null || echo 0)"
echo "HEARTBEAT FAIL: no PUBLISHED run for ${TODAY} by the digest deadline (RUNNING=${RUNNING}). No digest means something is wrong." >&2
if [ -n "${CARSHOP_HEARTBEAT_URL:-}" ]; then curl -fsS -m 10 "${CARSHOP_HEARTBEAT_URL}/fail" >/dev/null 2>&1 || true; fi
exit 2
