#!/usr/bin/env bash
# statusline-bridge.sh — wrap the user's statusline command so the handoff
# quota guard can see rate limits.
#
# Hook payloads carry no rate-limit data (measured); the statusline command is
# the only place Claude Code passes `rate_limits`. This reads the payload once,
# records the limits, and hands the SAME bytes to whatever statusline command
# the user already had, so their statusline looks exactly as before.
#
# Install: set this as `statusLine.command`, and put the previous command in
# HANDOFF_STATUSLINE_INNER. Removing it restores the previous setting.
set -uo pipefail

payload="$(cat)"
state="${HANDOFF_QUOTA_STATE:-$HOME/.claude/handoff/quota.json}"

STATE_PATH="$state" python3 - "$payload" <<'PY' || true
import json, os, sys, tempfile, time

try:
    data = json.loads(sys.argv[1])
except (ValueError, IndexError):
    sys.exit(0)
limits = data.get("rate_limits") if isinstance(data, dict) else None
# Absent for API-key accounts and before the first response. Writing an empty
# object here would read downstream as "0% used", which is a lie with the
# dangerous sign: it would silence the guard instead of standing down.
if not isinstance(limits, dict) or not limits:
    sys.exit(0)

out = {k: v for k, v in limits.items() if isinstance(v, dict)}
out["updated_at"] = int(time.time())
path = os.environ["STATE_PATH"]
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
# Atomic: the guard may read this file at any moment, and a half-written
# JSON file is the failure mode that would make the guard crash a session.
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
with os.fdopen(fd, "w") as f:
    json.dump(out, f)
os.replace(tmp, path)
PY

inner="${HANDOFF_STATUSLINE_INNER:-}"
if [ -n "$inner" ]; then
  printf '%s' "$payload" | eval "$inner" || true
fi
exit 0
