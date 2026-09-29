#!/usr/bin/env bash
# Regression tests for quota-guard.sh.
set -euo pipefail
unset QUOTA_WARN_PCT QUOTA_ACT_PCT QUOTA_WEEKLY_ACT_PCT QUOTA_STALE_SECONDS HANDOFF_UNATTENDED
GUARD="$(cd "$(dirname "$0")" && pwd)/quota-guard.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; rm -f "${TMPDIR:-/tmp}"/handoff-quota-*-qg-test*' EXIT
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

# 11. Both windows over their act thresholds at once: the weekly limit has the
#     higher bar and wins — the message must name the weekly limit, not the
#     5-hour window, and report the weekly figure (95), not the 5-hour one (85).
state "$WORK/both.json" 85 95 0
OUT="$(run qg-test-both "$WORK/both.json")"
echo "$OUT" | grep -q "STOP" || fail "both-over-threshold did not stop"
echo "$OUT" | grep -q "weekly limit" || fail "both-over-threshold did not name the weekly limit"
echo "$OUT" | grep -q "~95%" || fail "both-over-threshold did not report the weekly percentage"
echo "$OUT" | grep -q "5-hour window" && fail "both-over-threshold named the 5-hour window instead of the weekly limit"
echo "PASS both windows over threshold names the weekly limit"

# 12. A non-object JSON payload (valid JSON, wrong shape) must not crash the
#     hook (Fix round 1, Finding 1): a list, a number, or a string on stdin
#     makes .get() raise on a bare dict-shaped read. Every failure path here
#     must exit 0 and print nothing.
STATUS=0
OUT="$(printf '[1,2,3]' | HANDOFF_QUOTA_STATE="$WORK/act.json" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD")" || STATUS=$?
[ "$STATUS" -eq 0 ] || fail "non-object JSON payload exited non-zero"
[ -z "$OUT" ] || fail "non-object JSON payload produced output"
echo "PASS non-object JSON payload is silent"

# 13. C2: QUOTA_WARN_PCT=high is a hand-edited settings.json value; it used to
#     raise ValueError and exit 1 on EVERY prompt. It must fall back to the
#     documented 70, so 72% still warns and the hook still exits 0.
state "$WORK/badwarn.json" 72 5 0
STATUS=0
OUT="$(printf '{"session_id":"qg-test-badwarn","hook_event_name":"UserPromptSubmit"}' \
  | QUOTA_WARN_PCT=high HANDOFF_QUOTA_STATE="$WORK/badwarn.json" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD")" || STATUS=$?
[ "$STATUS" -eq 0 ] || fail "QUOTA_WARN_PCT=high exited $STATUS"
echo "$OUT" | grep -q "additionalContext" || fail "QUOTA_WARN_PCT=high did not fall back to the default 70"
echo "PASS non-numeric QUOTA_WARN_PCT falls back to the default"

# 14. C2: the same for the act, weekly and staleness settings — all four
#     scalars fall back together and the act still fires at 85%.
state "$WORK/badact.json" 85 5 0
STATUS=0
OUT="$(printf '{"session_id":"qg-test-badact","hook_event_name":"UserPromptSubmit"}' \
  | QUOTA_ACT_PCT=eighty QUOTA_WEEKLY_ACT_PCT=most QUOTA_STALE_SECONDS=soon \
    HANDOFF_QUOTA_STATE="$WORK/badact.json" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD")" || STATUS=$?
[ "$STATUS" -eq 0 ] || fail "non-numeric quota thresholds exited $STATUS"
echo "$OUT" | grep -q "STOP" || fail "non-numeric quota thresholds did not fall back to the defaults"
echo "PASS non-numeric QUOTA_ACT_PCT / WEEKLY_ACT_PCT / STALE_SECONDS fall back"

# 15. Minor: a session id containing "/" must not make the marker write raise
#     FileNotFoundError. marker_path sanitises it; this pins that it still does.
state "$WORK/slash.json" 85 5 0
STATUS=0
OUT="$(printf '{"session_id":"qg-test/slashed","hook_event_name":"UserPromptSubmit"}' \
  | HANDOFF_QUOTA_STATE="$WORK/slash.json" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD")" || STATUS=$?
[ "$STATUS" -eq 0 ] || fail "a session id containing / exited $STATUS"
echo "$OUT" | grep -q "STOP" || fail "a session id containing / suppressed the act"
echo "PASS a session id containing / is sanitised into the marker name"

# 16. Codex round 3, P2 — the once-per-session marker was keyed by LEVEL only
#     (`quota-act`), so a session that crossed the 5-hour act threshold went
#     on to swallow the WEEKLY alert: the guard returned at the marker check
#     and the user never heard about the weekly window or its reset time,
#     which is the one that decides between waiting an hour and stopping for
#     the week. The marker is now keyed by the window that fired, so the two
#     alerts are independent. Same session id throughout, on purpose.
state "$WORK/seq5h.json" 85 5 0
OUT="$(run qg-test-seq "$WORK/seq5h.json")"
echo "$OUT" | grep -q "5-hour window" || fail "the 5-hour act did not fire first: $OUT"
# ...it is still once-per-session for that window:
[ -z "$(run qg-test-seq "$WORK/seq5h.json")" ] || fail "the 5-hour act fired twice in one session"
# ...and now the weekly limit crosses its own, higher bar in the SAME session.
state "$WORK/seqweek.json" 85 95 0
OUT="$(run qg-test-seq "$WORK/seqweek.json")"
[ -n "$OUT" ] || fail "REGRESSION (P2): the 5-hour marker swallowed the weekly alert"
echo "$OUT" | grep -q "weekly limit" || fail "the weekly alert does not name the weekly limit: $OUT"
echo "$OUT" | grep -q "~95%" || fail "the weekly alert does not report the weekly percentage: $OUT"
echo "$OUT" | grep -q "It resets at" || fail "the weekly alert does not give the reset time: $OUT"
# ...and the weekly one is itself once-per-session.
[ -z "$(run qg-test-seq "$WORK/seqweek.json")" ] || fail "the weekly act fired twice in one session"
echo "PASS the 5-hour and weekly act alerts fire independently in one session"

# 17. Codex round 5, P2 — the marker had no RESET CYCLE in it, so a session
#     resumed after the quota window reset was suppressed forever: the bridge
#     writes fresh high usage with a NEW resets_at and the guard returned at
#     the marker left by the previous cycle. Each cycle must be able to warn
#     once. Same session id throughout, on purpose.
cycle_state() { # $1=file $2=five pct $3=seven pct $4=five resets_at $5=seven resets_at
  python3 - "$1" "$2" "$3" "$4" "$5" <<'PY'
import json, sys, time
path = sys.argv[1]
json.dump({"five_hour": {"used_percentage": float(sys.argv[2]), "resets_at": int(sys.argv[4])},
           "seven_day": {"used_percentage": float(sys.argv[3]), "resets_at": int(sys.argv[5])},
           "updated_at": int(time.time())}, open(path, "w"))
PY
}
cycle_state "$WORK/cyc1.json" 85 5 1790000000 1790500000
OUT="$(run qg-test-cycle "$WORK/cyc1.json")"
echo "$OUT" | grep -q "STOP" || fail "the first cycle's act did not fire"
# Same cycle, same session: silent, exactly as before.
[ -z "$(run qg-test-cycle "$WORK/cyc1.json")" ] || fail "the act fired twice within one reset cycle"
# The window has RESET and refilled: a new resets_at, high usage again.
cycle_state "$WORK/cyc2.json" 85 5 1790018000 1790500000
OUT="$(run qg-test-cycle "$WORK/cyc2.json")"
[ -n "$OUT" ] || fail "REGRESSION (P2): the previous cycle's marker suppressed the new one forever"
echo "$OUT" | grep -q "STOP" || fail "the new cycle's act did not stop: $OUT"
# ...and the new cycle is itself once-per-cycle.
[ -z "$(run qg-test-cycle "$WORK/cyc2.json")" ] || fail "the act fired twice within the new reset cycle"
echo "PASS each reset cycle warns once, and a new cycle is not suppressed by the old marker"

# 18. ...and the weekly window carries its own cycle key, so a new WEEKLY
#     reset is not suppressed by the weekly marker from the cycle before.
cycle_state "$WORK/wcyc1.json" 10 95 1790000000 1790500000
OUT="$(run qg-test-wcycle "$WORK/wcyc1.json")"
echo "$OUT" | grep -q "weekly limit" || fail "the first weekly cycle did not fire"
[ -z "$(run qg-test-wcycle "$WORK/wcyc1.json")" ] || fail "the weekly act fired twice within one cycle"
cycle_state "$WORK/wcyc2.json" 10 95 1790000000 1791104800
OUT="$(run qg-test-wcycle "$WORK/wcyc2.json")"
[ -n "$OUT" ] || fail "REGRESSION (P2): a new weekly cycle was suppressed by the old weekly marker"
echo "$OUT" | grep -q "weekly limit" || fail "the new weekly cycle does not name the weekly limit: $OUT"
echo "PASS a new weekly reset cycle warns again"

echo "ALL PASS"
