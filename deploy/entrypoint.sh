#!/bin/sh
# Container entrypoint. Modes:
#   serve      web screen (default)
#   run        one collection run now (args forwarded, e.g. --dealer bmw-austin)
#   cron       foreground scheduler: 06:00 America/Chicago daily run (+ heartbeat 07:30)
#   <other>    forwarded to the carshop CLI
set -eu

carshop migrate-db >/dev/null
# The registry (locator snapshot + discovery) is refreshed by the run itself when it is
# missing or older than CARSHOP_REGISTRY_REFRESH_DAYS; `carshop registry refresh` forces it.

case "${1:-serve}" in
  serve)
    exec carshop serve --host "${CARSHOP_WEB_HOST:-0.0.0.0}" --port "${CARSHOP_WEB_PORT:-8000}"
    ;;
  run)
    shift
    # A manual re-run emails only when the operator passes --notify (F10).
    exec carshop run "$@"
    ;;
  cron)
    # supercronic-free approach: a tiny loop that fires at the configured local time once per day.
    exec python - <<'PY'
import os, subprocess, time
from datetime import datetime
from zoneinfo import ZoneInfo

tz = ZoneInfo(os.environ.get("CARSHOP_SCHEDULE_TZ", "America/Chicago"))
hour = int(os.environ.get("CARSHOP_SCHEDULE_HOUR", "6"))
hb_h, hb_m = (int(x) for x in os.environ.get("CARSHOP_DIGEST_DEADLINE", "07:30").split(":"))
fired_run = fired_hb = fired_catchup = None
while True:
    now = datetime.now(tz)
    today = now.date()
    if now.hour == hour and fired_run != today:
        fired_run = today
        subprocess.call(["carshop", "run", "--trigger", "cron", "--notify"])
    # Missed 06:00 (container started late): one catch-up, which no-ops if cron already ran.
    if now.hour >= hour and fired_run != today and fired_catchup != today:
        fired_catchup = today
        subprocess.call(["carshop", "run", "--trigger", "catchup", "--notify"])
    if (now.hour, now.minute) >= (hb_h, hb_m) and fired_hb != today:
        fired_hb = today
        subprocess.call(["sh", "/app/deploy/heartbeat/check.sh"])
    time.sleep(30)
PY
    ;;
  *)
    exec carshop "$@"
    ;;
esac
