#!/usr/bin/env bash
# context-guard.sh — Claude Code hook: nudge the agent to run the handoff
# skill once the context window crosses a usage threshold.
#
# Works as a UserPromptSubmit and/or PostToolUse hook. Reads the hook input
# JSON on stdin, measures token usage from the session transcript, and when
# usage >= threshold injects additionalContext telling the agent to invoke
# the handoff skill. Fires once per session (marker file).
#
# The hook JSON stays on stdin all the way into Python — it can carry a large
# prompt or tool_response, and expanding it into an argv argument would hit
# the OS per-argument limit ("Argument list too long") exactly on the large
# context-growing operations this hook exists to catch. The Python code is
# fed via process substitution so stdin remains the hook payload.
#
# Env:
#   HANDOFF_THRESHOLD_PCT   default 70
#   CONTEXT_WINDOW_TOKENS   default 200000
set -euo pipefail

exec python3 <(cat <<'PY'
import json, os, sys, tempfile

inp = json.load(sys.stdin)
transcript = inp.get("transcript_path") or ""
session_id = inp.get("session_id") or "unknown"
event = inp.get("hook_event_name") or "UserPromptSubmit"

threshold = float(os.environ.get("HANDOFF_THRESHOLD_PCT", "70"))
window = int(os.environ.get("CONTEXT_WINDOW_TOKENS", "200000"))

marker = os.path.join(tempfile.gettempdir(), f"handoff-guard-{session_id}")
if os.path.exists(marker) or not transcript or not os.path.exists(transcript):
    sys.exit(0)

tokens = 0
# Latest assistant message carries cumulative prompt size in its usage block.
with open(transcript, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = (rec.get("message") or {}).get("usage")
        if usage:
            tokens = (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
            )

pct = tokens * 100.0 / window
if pct < threshold:
    sys.exit(0)

open(marker, "w").close()
msg = (
    f"Context window is at ~{pct:.0f}% of {window} tokens ({tokens} used), "
    f"past the {threshold:.0f}% handoff threshold. Invoke the handoff "
    "skill NOW to write a handoff document before context is compacted, "
    "then continue the current task."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": msg,
    }
}))
PY
)
