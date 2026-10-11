#!/usr/bin/env bash
# Boots the real app (SQLite) next to a fake Brevo and drives it with the repo's own
# end-to-end tools, so the results come from real HTTP/JSON-RPC, not unit tests.
#
#   PYTHON=/path/to/venv/bin/python ./docs/evidence/sprint-1/integration.sh
#
# Output: simulator_gate_on.txt, simulator_gate_off.txt, mcp_smoke.txt, smoke_all_endpoints.txt
set -u
REPO="$(git rev-parse --show-toplevel)"; OUT="$REPO/docs/evidence/sprint-1"; PY="${PYTHON:-python}"
DB=/tmp/sprint1_sim.db
KEYS=(SECRET_KEY=sim-secret-key-not-for-production
      CREDENTIAL_ENCRYPTION_KEY=JXfG0R5AHCO_Ib0mVG-1NxBwUJzZKrM4LBS9hCVvKmg=)   # the helpers' defaults

up() {  # url -> wait until it answers
  for _ in $(seq 1 60); do
    "$PY" - "$1" <<'PYEOF' && return 0
import sys, urllib.request
try: urllib.request.urlopen(sys.argv[1], timeout=2)
except Exception as exc:
    sys.exit(0 if getattr(exc, "code", None) else 1)
PYEOF
    sleep 0.5
  done
  echo "server did not start: $1" >&2; return 1
}
stop_app() { pkill -f "[u]vicorn app.main:app --port 8000" 2> /dev/null; sleep 1; }
start_app() {  # extra env assignments...
  stop_app; rm -f "$DB"
  (cd "$REPO/backend" && env DATABASE_URL="sqlite+aiosqlite:///$DB" APP_ENV=development "${KEYS[@]}" \
      BREVO_API_BASE=http://127.0.0.1:8799/v3 PUBLIC_BASE_URL=http://127.0.0.1:8000 "$@" \
      "$PY" -m uvicorn app.main:app --port 8000 > /tmp/sprint1_app.log 2>&1 &)
  up http://127.0.0.1:8000/api/v1/health
}
cleanup() { stop_app; pkill -f "[u]vicorn tools.fake_brevo:app" 2> /dev/null; rm -f "$DB"; }
trap cleanup EXIT

rm -f /tmp/fake_brevo_log.jsonl
(cd "$REPO" && "$PY" -m uvicorn tools.fake_brevo:app --port 8799 > /tmp/sprint1_brevo.log 2>&1 &)
up http://127.0.0.1:8799/v3/senders || exit 1

echo "== verification gate ON (the default): the simulator checks the gate over real HTTP"
start_app || exit 1
(cd "$REPO" && env DATABASE_URL="sqlite+aiosqlite:///$DB" "${KEYS[@]}" "$PY" tools/simulate_email_flow.py) > "$OUT/simulator_gate_on.txt" 2>&1
echo "   PASS $(grep -c '^\[PASS\]' "$OUT/simulator_gate_on.txt")  FAIL $(grep -c '^\[FAIL\]' "$OUT/simulator_gate_on.txt")"

echo "== gate OFF (a closed test environment): the whole email flow, then MCP, then every GET"
start_app EMAIL_REQUIRE_VERIFIED=false || exit 1
# The simulator's helper subprocesses (run_campaign_now.py, run_followup_now.py) open the DATABASE
# themselves, so they need the same DATABASE_URL, keys and gate setting as the server.
SIM_ENV=(DATABASE_URL="sqlite+aiosqlite:///$DB" "${KEYS[@]}")
(cd "$REPO" && env EMAIL_REQUIRE_VERIFIED=false "${SIM_ENV[@]}" "$PY" tools/simulate_email_flow.py) > "$OUT/simulator_gate_off.txt" 2>&1
echo "   simulator  PASS $(grep -c '^\[PASS\]' "$OUT/simulator_gate_off.txt")  FAIL $(grep -c '^\[FAIL\]' "$OUT/simulator_gate_off.txt")"
(cd "$REPO" && env ADMIN_PASSWORD=12345678 "$PY" tools/mcp_smoke.py) > "$OUT/mcp_smoke.txt" 2>&1
echo "   mcp_smoke  $(grep -E 'checks passed' "$OUT/mcp_smoke.txt")"
(cd "$REPO" && env ADMIN_PASSWORD=12345678 "$PY" tools/smoke_all_endpoints.py) > "$OUT/smoke_all_endpoints.txt" 2>&1
echo "   GET sweep  $(grep -E 'checked|RESULT' "$OUT/smoke_all_endpoints.txt" | tr '\n' ' ')"
