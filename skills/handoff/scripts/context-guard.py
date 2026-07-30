#!/usr/bin/env python3
"""context-guard — Claude Code hook: nudge the agent to run the handoff
skill once the context window crosses a usage threshold.

Works as a UserPromptSubmit and/or PostToolUse hook. Reads the hook input
JSON on stdin, measures token usage from the session transcript, and when
usage >= threshold prints hookSpecificOutput.additionalContext telling the
agent to invoke the handoff skill. Fires once per session (marker file).

The hook JSON is read from stdin, never argv: a large prompt or
tool_response would exceed the OS per-argument limit ("Argument list too
long") exactly on the large context-growing operations this hook exists to
catch.

Env:
  HANDOFF_THRESHOLD_PCT   default 70
  CONTEXT_WINDOW_TOKENS   default 200000
"""
import json
import os
import sys
import tempfile


def main() -> None:
    inp = json.load(sys.stdin)
    transcript = inp.get("transcript_path") or ""
    session_id = inp.get("session_id") or "unknown"
    event = inp.get("hook_event_name") or "UserPromptSubmit"

    threshold = float(os.environ.get("HANDOFF_THRESHOLD_PCT", "70"))
    window = int(os.environ.get("CONTEXT_WINDOW_TOKENS", "200000"))

    marker = os.path.join(tempfile.gettempdir(), f"handoff-guard-{session_id}")
    if os.path.exists(marker) or not transcript or not os.path.exists(transcript):
        return

    # The latest assistant usage block counts only the context sent INTO that
    # model call — it excludes that call's own output_tokens and anything
    # appended to the transcript since (new user prompt, tool results). A
    # large tail can cross the threshold silently, so estimate it at
    # ~4 chars/token and add the current hook payload the same way. Double
    # counting between tail and payload only makes the nudge fire earlier,
    # never later — the safe direction for a handoff reminder.
    tokens = 0
    tail_chars = 0
    with open(transcript, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                tail_chars += len(line)
                continue
            usage = (rec.get("message") or {}).get("usage")
            if usage:
                tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("output_tokens", 0)
                )
                tail_chars = 0
            else:
                tail_chars += len(line)

    payload_chars = len(inp.get("prompt") or "")
    tool_response = inp.get("tool_response")
    if tool_response is not None:
        payload_chars += len(json.dumps(tool_response, ensure_ascii=False))

    tokens += tail_chars // 4 + payload_chars // 4

    pct = tokens * 100.0 / window
    if pct < threshold:
        return

    open(marker, "w").close()
    msg = (
        f"Context window is at ~{pct:.0f}% of {window} tokens (~{tokens} "
        f"used, estimated), past the {threshold:.0f}% handoff threshold. "
        "Invoke the handoff skill NOW to write a handoff document before "
        "context is compacted, then continue the current task."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": msg,
        }
    }))


if __name__ == "__main__":
    main()
