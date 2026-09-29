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
echo "ALL PASS"
