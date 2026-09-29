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

# 9. Codex round 6, P2 — `$(cat)` STRIPS THE TRAILING NEWLINE, so an existing
#    statusline written the ordinary way — `IFS= read -r payload` — saw EOF
#    with no delimiter, failed, and rendered nothing. The wrapper's whole
#    promise is that the user's statusline looks exactly as before.
cat > "$WORK/reader.sh" <<'EOF'
#!/usr/bin/env bash
IFS= read -r line || { echo "READ FAILED" ; exit 1; }
printf '%s' "$line" > "$READER_SEEN"
echo "READ OK"
EOF
chmod +x "$WORK/reader.sh"
OUT="$(printf '%s\n' "$PAYLOAD" | READER_SEEN="$WORK/read.json" HANDOFF_QUOTA_STATE="$WORK/q9.json" \
  HANDOFF_STATUSLINE_INNER="$WORK/reader.sh" bash "$BRIDGE")" \
  || fail "the bridge failed with a read -r inner statusline"
[ "$OUT" = "READ OK" ] || fail "REGRESSION (P2): a read -r inner statusline got no line-terminated payload: $OUT"
diff <(python3 -c "import json,sys;print(json.dumps(json.load(open('$WORK/read.json')),sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps(json.loads('''$PAYLOAD'''),sort_keys=True))") \
     >/dev/null || fail "the read -r inner statusline received a modified payload"
echo "PASS an inner statusline using read -r gets a newline-terminated payload"

# 10. ...and the bytes are passed through EXACTLY, terminating newline
#     included — not "the same JSON", the same bytes.
cat > "$WORK/raw.sh" <<'EOF'
#!/usr/bin/env bash
cat > "$RAW_SEEN"
EOF
chmod +x "$WORK/raw.sh"
printf '%s\n' "$PAYLOAD" > "$WORK/raw-in.json"
RAW_SEEN="$WORK/raw-out.json" HANDOFF_QUOTA_STATE="$WORK/q10.json" \
  HANDOFF_STATUSLINE_INNER="$WORK/raw.sh" bash "$BRIDGE" < "$WORK/raw-in.json" >/dev/null
cmp "$WORK/raw-in.json" "$WORK/raw-out.json" || fail "the inner statusline did not receive byte-identical input"
echo "PASS the payload reaches the inner statusline byte for byte"

# 11. ...and a payload with NO trailing newline stays without one: preserving
#     the bytes means preserving their absence too.
printf '%s' "$PAYLOAD" > "$WORK/raw-in2.json"
RAW_SEEN="$WORK/raw-out2.json" HANDOFF_QUOTA_STATE="$WORK/q11.json" \
  HANDOFF_STATUSLINE_INNER="$WORK/raw.sh" bash "$BRIDGE" < "$WORK/raw-in2.json" >/dev/null
cmp "$WORK/raw-in2.json" "$WORK/raw-out2.json" || fail "a payload with no trailing newline was altered"
[ -f "$WORK/q11.json" ] || fail "a payload with no trailing newline lost the state write"
echo "PASS a payload with no trailing newline is passed through unchanged"

# 12. Codex round 7, P1 — ONE STATE FILE FOR EVERY SESSION. Concurrent
#     sessions (a subscription one beside an API-key or another account's)
#     overwrote and deleted each other's snapshots: a high-usage account
#     could stop an unrelated low-usage session, and since the round-1 fix an
#     API-key render DELETED what a subscription session had just written.
#     The file is per session now. HOME is redirected so these cases use the
#     real default path scheme (~/.claude/handoff/quota-<session>.json)
#     rather than the HANDOFF_QUOTA_STATE override the cases above use.
unset HANDOFF_QUOTA_STATE
HOMEDIR="$WORK/home"
mkdir -p "$HOMEDIR"
sess_payload() { # $1=session id, $2=five_hour pct
  printf '{"session_id":"%s","model":{"display_name":"Opus"},"rate_limits":{"five_hour":{"used_percentage":%s,"resets_at":1790000000},"seven_day":{"used_percentage":5,"resets_at":1790500000}}}' "$1" "$2"
}
sess_payload session-alpha 95 | HOME="$HOMEDIR" bash "$BRIDGE" >/dev/null
sess_payload session-beta 10 | HOME="$HOMEDIR" bash "$BRIDGE" >/dev/null
[ -f "$HOMEDIR/.claude/handoff/quota-session-alpha.json" ] || fail "no per-session state file for session-alpha"
[ -f "$HOMEDIR/.claude/handoff/quota-session-beta.json" ] || fail "no per-session state file for session-beta"
python3 - "$HOMEDIR/.claude/handoff/quota-session-alpha.json" "$HOMEDIR/.claude/handoff/quota-session-beta.json" <<'PY' || fail "the two sessions share a snapshot"
import json, sys
a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
assert a["five_hour"]["used_percentage"] == 95, a
assert b["five_hour"]["used_percentage"] == 10, b
PY
echo "PASS each session gets its own state file"

# 13. ...and the high-usage session does not stop the other one: the guard
#     reads the file for ITS own session id.
GUARD2="$(cd "$(dirname "$0")" && pwd)/quota-guard.sh"
OUT="$(printf '{"session_id":"session-beta","hook_event_name":"UserPromptSubmit"}' \
  | HOME="$HOMEDIR" TMPDIR="$WORK" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD2")" \
  || fail "the guard exited non-zero for session-beta"
[ -z "$OUT" ] || fail "REGRESSION (P1): a different session's high usage stopped session-beta: $OUT"
OUT="$(printf '{"session_id":"session-alpha","hook_event_name":"UserPromptSubmit"}' \
  | HOME="$HOMEDIR" TMPDIR="$WORK" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD2")"
echo "$OUT" | grep -q "STOP" || fail "the high-usage session's own alarm did not fire: $OUT"
echo "PASS one session's usage does not stop another"

# 14. ...and an API-key payload with no rate_limits clears only ITS OWN
#     session's state. This is the case the round-1 deletion fix broke when
#     it met a shared file.
printf '{"session_id":"session-beta","model":{"display_name":"Opus"}}' | HOME="$HOMEDIR" bash "$BRIDGE" >/dev/null
[ ! -f "$HOMEDIR/.claude/handoff/quota-session-beta.json" ] || fail "the no-limits payload did not clear its own session's state"
[ -f "$HOMEDIR/.claude/handoff/quota-session-alpha.json" ] || fail "REGRESSION (P1): an API-key session deleted another session's snapshot"
echo "PASS a no-limits payload clears only its own session's state"

# 15. ...and a payload with NO session id writes nothing at all: falling back
#     to a shared file is the defect, so there is nothing to fall back to.
before="$(ls "$HOMEDIR/.claude/handoff" | sort)"
printf '{"model":{"display_name":"Opus"},"rate_limits":{"five_hour":{"used_percentage":99,"resets_at":1790000000}}}' \
  | HOME="$HOMEDIR" bash "$BRIDGE" >/dev/null
after="$(ls "$HOMEDIR/.claude/handoff" | sort)"
[ "$before" = "$after" ] || fail "a payload with no session id wrote a state file: $after"
echo "PASS a payload with no session id writes nothing"

# 16. ...and HANDOFF_QUOTA_STATE still overrides with one explicit path, for
#     a payload that carries a session id and for one that does not.
echo "$PAYLOAD" | HOME="$HOMEDIR" HANDOFF_QUOTA_STATE="$WORK/override.json" bash "$BRIDGE" >/dev/null
[ -f "$WORK/override.json" ] || fail "the override was ignored for a payload with no session id"
sess_payload session-gamma 42 | HOME="$HOMEDIR" HANDOFF_QUOTA_STATE="$WORK/override2.json" bash "$BRIDGE" >/dev/null
[ -f "$WORK/override2.json" ] || fail "the override was ignored for a payload with a session id"
[ ! -f "$HOMEDIR/.claude/handoff/quota-session-gamma.json" ] || fail "the override did not stop the per-session write"
echo "PASS HANDOFF_QUOTA_STATE still overrides with one explicit path"

echo "ALL PASS"
