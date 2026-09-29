#!/usr/bin/env bash
# Regression tests for handoff_common.py.
set -euo pipefail
unset HANDOFF_UNATTENDED CLAUDE_CODE_SESSION_ATTENDED HF_T HF_W
DIR="$(cd "$(dirname "$0")" && pwd)"
fail() { echo "FAIL: $1" >&2; exit 1; }

# 1. ATTENDED=1 -> attended
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.attended())")"
[ "$OUT" = "True" ] || fail "attended session read as unattended"
echo "PASS attended session"

# 2. ATTENDED=0 (a claude -p run) -> unattended
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=0 python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.attended())")"
[ "$OUT" = "False" ] || fail "headless run read as attended"
echo "PASS headless run"

# 3. The variable missing entirely -> assume attended (the safe default:
#    a warning nobody reads costs nothing; silence on a real session costs
#    the handoff).
OUT="$(python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.attended())")"
[ "$OUT" = "True" ] || fail "missing variable should default to attended"
echo "PASS missing variable defaults to attended"

# 4. HANDOFF_UNATTENDED=1 overrides an attended session
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 HANDOFF_UNATTENDED=1 python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.attended())")"
[ "$OUT" = "False" ] || fail "manual override ignored"
echo "PASS manual override"

# 5. Marker paths are session-scoped and name-scoped
OUT="$(python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h
a, b = h.marker_path('s1', 'warn'), h.marker_path('s1', 'stop')
c = h.marker_path('s2', 'warn')
print(a != b and a != c and 's1' in a and 'warn' in a)")"
[ "$OUT" = "True" ] || fail "marker paths collide"
echo "PASS marker paths distinct"

# 6. env_float falls back on an unparseable value, and on the two that PARSE
#    but disarm the setting: `inf` (a threshold that never fires) and `nan`
#    (every comparison against it is False).
OUT="$(HF_T=seventy python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.env_float('HF_T', 70.0))")"
[ "$OUT" = "70.0" ] || fail "env_float did not fall back on an unparseable value: $OUT"
for bad in inf -inf Infinity nan NaN; do
  OUT="$(HF_T="$bad" python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.env_float('HF_T', 70.0))")"
  [ "$OUT" = "70.0" ] || fail "env_float accepted the non-finite value $bad: $OUT"
done
OUT="$(HF_T=42.5 python3 -c "
import sys; sys.path.insert(0, '$DIR')
import handoff_common as h; print(h.env_float('HF_T', 70.0))")"
[ "$OUT" = "42.5" ] || fail "env_float dropped a good value: $OUT"
echo "PASS env_float rejects unparseable and non-finite values"

# 7. env_positive_int: None for unset, unparseable, zero and negative — None
#    is what tells the caller "the operator did not state this".
OUT="$(python3 -c "
import sys, os; sys.path.insert(0, '$DIR')
import handoff_common as h
print([h.env_positive_int('HF_W')] + [
    (os.environ.__setitem__('HF_W', v), h.env_positive_int('HF_W'))[1]
    for v in ('one-million', '0', '-5', '1000000')])")"
[ "$OUT" = "[None, None, None, None, 1000000]" ] || fail "env_positive_int: $OUT"
echo "PASS env_positive_int rejects unparseable, zero and negative"

echo "ALL PASS"
