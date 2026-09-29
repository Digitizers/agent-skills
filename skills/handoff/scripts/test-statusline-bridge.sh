#!/usr/bin/env bash
# Regression tests for statusline-bridge.sh.
set -euo pipefail
unset HANDOFF_STATUSLINE_INNER
BRIDGE="$(cd "$(dirname "$0")" && pwd)/statusline-bridge.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

PAYLOAD='{"model":{"display_name":"Opus"},"rate_limits":{"five_hour":{"used_percentage":42.5,"resets_at":1790000000},"seven_day":{"used_percentage":10,"resets_at":1790500000}}}'

# 1. Writes the state file
echo "$PAYLOAD" | HANDOFF_QUOTA_STATE="$WORK/quota.json" bash "$BRIDGE" >/dev/null
python3 - "$WORK/quota.json" <<'PY' || fail "state file wrong"
import json, sys, time
d = json.load(open(sys.argv[1]))
assert d["five_hour"]["used_percentage"] == 42.5, d
assert d["seven_day"]["resets_at"] == 1790500000, d
assert abs(d["updated_at"] - time.time()) < 60, d
PY
echo "PASS writes state"

# 2. Passes the payload through to the user's own statusline, unchanged
cat > "$WORK/inner.sh" <<'EOF'
#!/usr/bin/env bash
cat > "$INNER_SEEN"
echo "MY STATUSLINE"
EOF
chmod +x "$WORK/inner.sh"
OUT="$(echo "$PAYLOAD" | INNER_SEEN="$WORK/seen.json" HANDOFF_QUOTA_STATE="$WORK/q2.json" \
  HANDOFF_STATUSLINE_INNER="$WORK/inner.sh" bash "$BRIDGE")"
[ "$OUT" = "MY STATUSLINE" ] || fail "inner statusline output not passed through: $OUT"
diff <(python3 -c "import json,sys;print(json.dumps(json.load(open('$WORK/seen.json')),sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps(json.loads('''$PAYLOAD'''),sort_keys=True))") \
     >/dev/null || fail "inner statusline received a modified payload"
echo "PASS passes through unchanged"

# 3. No rate_limits in the payload (API-key account, or before the first
#    response): write NO state file rather than one that reads as 0%.
echo '{"model":{"display_name":"Opus"}}' | HANDOFF_QUOTA_STATE="$WORK/q3.json" bash "$BRIDGE" >/dev/null
[ ! -f "$WORK/q3.json" ] || fail "wrote state for a payload with no rate limits"
echo "PASS no rate_limits writes nothing"

# 4. Malformed payload: exit 0, no crash, no state.
echo 'not json' | HANDOFF_QUOTA_STATE="$WORK/q4.json" bash "$BRIDGE" >/dev/null || fail "non-zero exit on malformed input"
[ ! -f "$WORK/q4.json" ] || fail "wrote state from malformed input"
echo "PASS malformed payload is silent"

# 5. A failing inner statusline must not take the bridge down with it.
cat > "$WORK/bad.sh" <<'EOF'
#!/usr/bin/env bash
exit 3
EOF
chmod +x "$WORK/bad.sh"
echo "$PAYLOAD" | HANDOFF_QUOTA_STATE="$WORK/q5.json" HANDOFF_STATUSLINE_INNER="$WORK/bad.sh" \
  bash "$BRIDGE" >/dev/null || fail "inner failure propagated"
[ -f "$WORK/q5.json" ] || fail "inner failure lost the state write"
echo "PASS inner failure isolated"
# 6. Codex P1 — STALE STATE MUST BE INVALIDATED. A payload with no rate_limits
#    (an API-key account, or the first render after switching accounts) used to
#    return without touching the existing state file. quota-guard.py treats
#    anything written in the last 15 minutes as current, so a high-usage
#    snapshot from the account we just left kept stopping the fresh session —
#    breaking the very switch-to-another-account flow this feature exists for.
GUARD="$(cd "$(dirname "$0")" && pwd)/quota-guard.py"
python3 - "$WORK/q6.json" <<'PY'
import json, sys, time
json.dump({"five_hour": {"used_percentage": 97.0, "resets_at": 1790000000},
           "updated_at": int(time.time())}, open(sys.argv[1], "w"))
PY
# Precondition: that state is fresh and high enough that the guard speaks up.
# TMPDIR scopes the guard's once-per-session marker to this run's temp
# directory, so a second run of this suite is not silenced by the first.
PRE="$(echo '{"session_id":"stale-pre","hook_event_name":"UserPromptSubmit"}' \
  | TMPDIR="$WORK" CLAUDE_CODE_SESSION_ATTENDED=1 HANDOFF_QUOTA_STATE="$WORK/q6.json" python3 "$GUARD")"
[ -n "$PRE" ] || fail "test setup: the guard is already silent on the pre-existing state"

echo '{"model":{"display_name":"Opus"}}' | HANDOFF_QUOTA_STATE="$WORK/q6.json" bash "$BRIDGE" >/dev/null
[ ! -f "$WORK/q6.json" ] || fail "REGRESSION: stale quota state survived a payload with no rate_limits"
POST="$(echo '{"session_id":"stale-post","hook_event_name":"UserPromptSubmit"}' \
  | TMPDIR="$WORK" CLAUDE_CODE_SESSION_ATTENDED=1 HANDOFF_QUOTA_STATE="$WORK/q6.json" python3 "$GUARD")"
[ -z "$POST" ] || fail "the guard still fires from invalidated state: $POST"
echo "PASS no rate_limits invalidates stale state and the guard falls silent"

# 7. ...and that path still passes the payload through to the user's own
#    statusline: invalidating state must not cost them their statusline.
python3 - "$WORK/q7.json" <<'PY'
import json, sys, time
json.dump({"five_hour": {"used_percentage": 97.0}, "updated_at": int(time.time())},
          open(sys.argv[1], "w"))
PY
NOLIMITS='{"model":{"display_name":"Opus"}}'
OUT="$(printf '%s' "$NOLIMITS" | INNER_SEEN="$WORK/seen7.json" HANDOFF_QUOTA_STATE="$WORK/q7.json" \
  HANDOFF_STATUSLINE_INNER="$WORK/inner.sh" bash "$BRIDGE")"
[ "$OUT" = "MY STATUSLINE" ] || fail "inner statusline output lost on the no-rate-limits path: $OUT"
[ ! -f "$WORK/q7.json" ] || fail "stale state survived on the pass-through path"
diff <(python3 -c "import json,sys;print(json.dumps(json.load(open('$WORK/seen7.json')),sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps(json.loads('''$NOLIMITS'''),sort_keys=True))") \
     >/dev/null || fail "inner statusline received a modified payload on the no-rate-limits path"
echo "PASS no rate_limits still passes the payload through unchanged"

# 8. A malformed payload must NOT destroy good state: a garbled statusline
#    render is not evidence that the quota window ended.
python3 - "$WORK/q8.json" <<'PY'
import json, sys, time
json.dump({"five_hour": {"used_percentage": 97.0}, "updated_at": int(time.time())},
          open(sys.argv[1], "w"))
PY
echo 'not json' | HANDOFF_QUOTA_STATE="$WORK/q8.json" bash "$BRIDGE" >/dev/null || fail "non-zero exit on malformed input"
[ -f "$WORK/q8.json" ] || fail "malformed input destroyed a good state file"
echo "PASS malformed payload leaves existing state alone"

echo "ALL PASS"
