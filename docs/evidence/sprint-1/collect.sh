#!/usr/bin/env bash
# Regenerates the Sprint 1 before/after evidence in this directory.
#
#   ./docs/evidence/sprint-1/collect.sh            (from the repository root, venv active)
#
# For each workstream it runs the workstream's tests against
#   BEFORE: the code at the workstream's parent commit (a throwaway `git worktree`),
#           with the current tests overlaid -- so a test that passes only because of the
#           fix shows up as a failure here;
#   AFTER : the current checkout.
# Then it runs the reproduction scripts in repro/ against both and keeps their output.
set -u
REPO="$(git rev-parse --show-toplevel)"
OUT="$REPO/docs/evidence/sprint-1"
PY="${PYTHON:-python}"
AFTER_SHA="$(git rev-parse --short HEAD)"

# name | parent commit of the fix | test files | NEW modules the tests import (copied in so the
# import succeeds and the BEHAVIOUR is what fails -- they are not used by the old code)
WORKSTREAMS=(
  "p0_1|45c0937|backend/tests/test_p0_1_campaign_lifecycle.py|"
  "p0_2|d9002cf|backend/tests/test_p0_2_unified_campaigns.py|"
  "p0_3|b9eedb5|backend/tests/test_p0_3_send_throttles.py backend/tests/test_p0_3_circuit_breaker.py|backend/app/services/circuit_breaker.py"
  "p0_4|38d3b87|backend/tests/test_p0_4_no_silent_truncation.py|backend/app/mcp/response.py"
  "p0_5|60c0f4f|backend/tests/test_p0_5_verification_gate.py|"
  "c10|abeb293|backend/tests/test_c10_secrets.py|"
)
ONLY="${ONLY:-}"   # e.g. ONLY="p0_3 p0_4" re-runs just those
want() { [ -z "$ONLY" ] || [[ " $ONLY " == *" $1 "* ]]; }

worktree() {  # name parent [extra files] -> a checkout of `parent` with today's tests overlaid
  local path="/tmp/sprint1_before_$1"
  rm -rf "$path"; git worktree prune
  git worktree add -f --detach "$path" "$2" > /dev/null 2>&1
  (cd "$REPO" && git archive HEAD backend/tests pyproject.toml ${3:-}) | tar -x -C "$path"
  echo "$path"
}

pytest_run() {  # dir files...
  local dir="$1"; shift
  (cd "$dir" && timeout 900 "$PY" -m pytest "$@" -p no:cacheprovider --no-header -rf --tb=no \
     --continue-on-collection-errors 2>&1 \
     | grep -E "^(FAILED|ERROR) |passed|failed|error" | sed -E 's/ - .*//' | sort -u)
}

for entry in "${WORKSTREAMS[@]}"; do
  IFS='|' read -r name parent files extra <<< "$entry"
  want "$name" || continue
  wt="$(worktree "$name" "$parent" "$extra")"
  {
    echo "# $name -- tests: $files"
    echo "# BEFORE = code at $parent (parent of the fix) with today's tests;  AFTER = $AFTER_SHA"
    echo
    echo "## BEFORE the fix"
    pytest_run "$wt" $files
    echo
    echo "## AFTER the fix"
    pytest_run "$REPO" $files
  } > "$OUT/tests_$name.txt"
  git worktree remove --force "$wt" > /dev/null 2>&1
  echo "wrote tests_$name.txt: $(grep -hE 'passed|failed' "$OUT/tests_$name.txt" | tr '\n' ' ')"
done

# Reproduction scripts: same script, old code vs new code.
declare -A REPRO_PARENT=( [p0_3]=b9eedb5 [p0_4]=38d3b87 [p0_5]=60c0f4f [c10]=abeb293 )
for name in p0_3 p0_4 p0_5 c10; do
  want "$name" || continue
  wt="$(worktree "repro_$name" "${REPRO_PARENT[$name]}")"
  {
    echo "# repro_$name.py"
    echo "## BEFORE (code at ${REPRO_PARENT[$name]})"
    REPO_ROOT="$wt" "$PY" "$OUT/repro/repro_$name.py" 2>&1 | grep -v -E "slowapi|INSECURE|Warning|^  warn"
    echo
    echo "## AFTER ($AFTER_SHA)"
    REPO_ROOT="$REPO" "$PY" "$OUT/repro/repro_$name.py" 2>&1 | grep -v -E "slowapi|INSECURE|Warning|^  warn"
  } > "$OUT/repro_$name.txt"
  git worktree remove --force "$wt" > /dev/null 2>&1
  echo "wrote repro_$name.txt"
done
