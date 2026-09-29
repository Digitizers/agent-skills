#!/usr/bin/env bash
# Regression tests for handoff_common.py.
set -euo pipefail
unset HANDOFF_UNATTENDED CLAUDE_CODE_SESSION_ATTENDED
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
echo "ALL PASS"
