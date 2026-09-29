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

# `$(cat)` strips EVERY trailing newline, so an inner statusline written as
# `IFS= read -r payload` saw EOF with no delimiter, failed, and rendered
# nothing — the one thing this wrapper promises not to do. The `printf X`
# sentinel preserves the payload exactly, terminating newline included.
payload="$(cat; printf X)"
payload="${payload%X}"
# The state path is derived from the payload's OWN session id, inside the
# python below, through handoff_common.quota_state_path — the same function
# quota-guard.py reads it with, so the two cannot drift. HANDOFF_QUOTA_STATE
# still overrides it with one explicit path.
HANDOFF_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

HANDOFF_SCRIPT_DIR="$HANDOFF_SCRIPT_DIR" python3 - "$payload" <<'PY' || true
import json, os, sys, tempfile, time

sys.path.insert(0, os.environ.get("HANDOFF_SCRIPT_DIR", ""))
try:
    from handoff_common import quota_state_path
except ImportError:
    # No state beats a broken statusline: the wrapper's first duty is to
    # hand the payload on unchanged, and the shell below still does that.
    sys.exit(0)

try:
    data = json.loads(sys.argv[1])
except (ValueError, IndexError):
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)
# One file per SESSION. Concurrent sessions used to share one, so a
# high-usage account's snapshot stopped an unrelated low-usage session, and
# an API-key render deleted what a subscription session had just written.
# No session id and no override means nowhere to write: skip, rather than
# fall back to the shared file that was the defect.
path = quota_state_path(data.get("session_id"))
if not path:
    sys.exit(0)
limits = data.get("rate_limits")
# Absent for API-key accounts and before the first response. Writing an empty
# object here would read downstream as "0% used", which is a lie with the
# dangerous sign: it would silence the guard instead of standing down.
#
# Leaving an OLD state file in place is the opposite lie, and just as bad:
# quota-guard.py treats anything written in the last 15 minutes as current,
# so a high-usage snapshot from the account we just switched away from would
# stop the fresh session — breaking the switch-to-another-account flow this
# whole feature exists to support. Absent limits mean "no quota information",
# so remove the state rather than write or keep one.
# ...and it clears only THIS session's file, never another session's.
if not isinstance(limits, dict) or not limits:
    try:
        os.remove(path)
    except OSError:
        pass
    sys.exit(0)

out = {k: v for k, v in limits.items() if isinstance(v, dict)}
out["updated_at"] = int(time.time())
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
# Atomic: the guard may read this file at any moment, and a half-written
# JSON file is the failure mode that would make the guard crash a session.
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
with os.fdopen(fd, "w") as f:
    json.dump(out, f)
os.replace(tmp, path)

# Per-session files would otherwise accumulate forever in ~/.claude/handoff,
# which the OS never purges. Not a daemon and not a schedule: one listdir on
# a write we were making anyway, dropping snapshots older than a day. The
# guard already ignores anything older than 15 minutes, and a live session's
# bridge rewrites its file on every statusline render, so a day-old file
# cannot belong to one. Skipped entirely when HANDOFF_QUOTA_STATE names an
# explicit path — an operator's directory is not ours to tidy.
if not os.environ.get("HANDOFF_QUOTA_STATE"):
    try:
        directory = os.path.dirname(path)
        cutoff = time.time() - 86400
        for name in os.listdir(directory):
            if not (name.startswith("quota-") and name.endswith(".json")):
                continue
            stale = os.path.join(directory, name)
            try:
                if os.path.getmtime(stale) < cutoff:
                    os.remove(stale)
            except OSError:
                pass
    except OSError:
        pass
PY

inner="${HANDOFF_STATUSLINE_INNER:-}"
if [ -n "$inner" ]; then
  printf '%s' "$payload" | eval "$inner" || true
fi
exit 0
