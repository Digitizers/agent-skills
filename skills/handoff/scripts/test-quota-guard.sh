#!/usr/bin/env bash
# Regression tests for quota-guard.sh.
set -euo pipefail
unset QUOTA_WARN_PCT QUOTA_ACT_PCT QUOTA_WEEKLY_ACT_PCT QUOTA_STALE_SECONDS HANDOFF_UNATTENDED
GUARD="$(cd "$(dirname "$0")" && pwd)/quota-guard.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; rm -f "${TMPDIR:-/tmp}"/handoff-quota-*-qg-test-*' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

state() { # $1=file $2=five_hour pct $3=seven_day pct $4=age seconds
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json, sys, time
path, five, seven, age = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
now = int(time.time())
json.dump({"five_hour": {"used_percentage": five, "resets_at": now + 3600},
           "seven_day": {"used_percentage": seven, "resets_at": now + 86400},
           "updated_at": now - age}, open(path, "w"))
PY
}
run() { # $1=session id, $2=state file
  printf '{"session_id":"%s","hook_event_name":"UserPromptSubmit"}' "$1" \
    | HANDOFF_QUOTA_STATE="$2" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD" \
    || fail "guard exited non-zero"
}

# 1. Below the warn threshold -> silent
state "$WORK/low.json" 20 5 0
[ -z "$(run qg-test-low "$WORK/low.json")" ] || fail "fired below warn"
echo "PASS below warn silent"

# 2. Warn band -> a warning that does NOT stop the session
state "$WORK/warn.json" 72 5 0
OUT="$(run qg-test-warn "$WORK/warn.json")"
echo "$OUT" | grep -q "additionalContext" || fail "no warning in the warn band"
echo "$OUT" | grep -q "STOP" && fail "warn band told the session to stop"
echo "PASS warn band warns only"

# 3. Act band on the 5-hour window -> stop and hand off
state "$WORK/act.json" 85 5 0
OUT="$(run qg-test-act "$WORK/act.json")"
echo "$OUT" | grep -q "STOP" || fail "act band did not stop"
echo "$OUT" | grep -q "quota" || fail "act band did not name the mode"
echo "PASS act band stops"

# 4. The weekly limit has its own, higher bar: 85% weekly is not yet an act.
state "$WORK/week-low.json" 10 85 0
OUT="$(run qg-test-week-low "$WORK/week-low.json")"
echo "$OUT" | grep -q "STOP" && fail "weekly stopped below its threshold"
echo "PASS weekly below threshold"

# 5. ...and 95% weekly is.
state "$WORK/week.json" 10 95 0
OUT="$(run qg-test-week "$WORK/week.json")"
echo "$OUT" | grep -q "STOP" || fail "weekly act did not stop"
echo "PASS weekly act stops"

# 6. Stale state is ignored (Review Focus 1): a file from a previous window
#    must not stop a session whose quota has since reset.
state "$WORK/stale.json" 95 5 3600
[ -z "$(run qg-test-stale "$WORK/stale.json")" ] || fail "acted on stale state"
echo "PASS stale state ignored"

# 7. Missing state file -> silent, exit 0
[ -z "$(run qg-test-missing "$WORK/nope.json")" ] || fail "fired with no state file"
echo "PASS missing state silent"

# 8. Corrupt state file -> silent, exit 0
echo '{"five_hour": ' > "$WORK/bad.json"
[ -z "$(run qg-test-bad "$WORK/bad.json")" ] || fail "fired on corrupt state"
echo "PASS corrupt state silent"

# 9. Once per session per band: the same act does not fire twice.
state "$WORK/once.json" 85 5 0
run qg-test-once "$WORK/once.json" >/dev/null
[ -z "$(run qg-test-once "$WORK/once.json")" ] || fail "act fired twice"
echo "PASS act fires once"

# 10. Unattended run is never told to stop.
state "$WORK/head.json" 85 5 0
OUT="$(printf '{"session_id":"qg-test-head","hook_event_name":"UserPromptSubmit"}' \
  | HANDOFF_QUOTA_STATE="$WORK/head.json" CLAUDE_CODE_SESSION_ATTENDED=0 bash "$GUARD")"
echo "$OUT" | grep -q "STOP" && fail "headless run told to stop"
echo "PASS headless run does not stop"
echo "ALL PASS"
