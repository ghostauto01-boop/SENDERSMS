#!/usr/bin/env bash
# Keep a free Render service (and any other sleeping host) awake.
#
#   APP_BASE_URL=https://your-service.onrender.com scripts/keep_alive.sh
#
# Run it from cron (or any always-on box) every 10 minutes:
#
#   */10 * * * * APP_BASE_URL=https://your-service.onrender.com /path/to/scripts/keep_alive.sh >> /var/log/sendify-keepalive.log 2>&1
#
# Only /api/v1/health is pinged. That endpoint is a pure liveness probe: it
# touches neither the database nor the poller, so warming the web instance does
# not stop Neon's compute from suspending (which is what keeps the free tier in
# budget). Never point this at a data route.
#
# No jq, no curl features beyond --retry: this has to work on a bare box.
set -u

BASE="${APP_BASE_URL:-}${APP_BASE_URL:+/}"
BASE="${BASE%/}"
if [ -z "$BASE" ]; then
  echo "$(date -u +%FT%TZ) APP_BASE_URL is not set — nothing to do."
  exit 0
fi

URL="$BASE/api/v1/health"
# --retry covers a cold boot: the first attempt may time out, the second lands.
code=$(curl -s -o /dev/null -w '%{http_code}' \
        --retry 3 --retry-delay 10 --retry-all-errors --max-time 60 "$URL" || echo 000)

if [ "$code" = "200" ]; then
  echo "$(date -u +%FT%TZ) $URL → $code (awake)"
  exit 0
fi
echo "$(date -u +%FT%TZ) $URL → $code (still waking)"
exit 1
