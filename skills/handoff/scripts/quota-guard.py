#!/usr/bin/env python3
"""quota-guard — Claude Code hook: hand off before the subscription window
runs out.

Hook payloads carry no rate-limit data (measured), so the numbers come from
the state file that statusline-bridge.sh writes. No bridge, no state file, no
alarm — quota mode still works when the user asks for it by hand.

Silence is the default for every failure: a missing file, a corrupt file, a
stale file, an unreadable field. This runs on every prompt.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handoff_common import attended, env_float, marker_path  # noqa: E402


def pct(limits: dict, key: str):
    entry = limits.get(key)
    if not isinstance(entry, dict):
        return None
    value = entry.get("used_percentage")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(inp, dict):
        return
    session_id = inp.get("session_id") or "unknown"
    event = inp.get("hook_event_name") or "UserPromptSubmit"

    path = os.environ.get("HANDOFF_QUOTA_STATE") or os.path.join(
        os.path.expanduser("~"), ".claude", "handoff", "quota.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(state, dict):
        return

    # A state file from an earlier window would stop a session whose quota has
    # already reset. The bridge rewrites it on every statusline render, so
    # anything older than the stale window means the bridge is not running.
    # Parsed defensively: these are hand-edited settings.json values read on
    # every prompt, so `QUOTA_WARN_PCT=high` must fall back to the documented
    # default, not raise ValueError and break the session.
    stale_after = env_float("QUOTA_STALE_SECONDS", 900.0)
    try:
        age = time.time() - float(state.get("updated_at", 0))
    except (TypeError, ValueError):
        return
    if age > stale_after:
        return

    five = pct(state, "five_hour")
    seven = pct(state, "seven_day")
    warn_pct = env_float("QUOTA_WARN_PCT", 70.0)
    act_pct = env_float("QUOTA_ACT_PCT", 80.0)
    weekly_pct = env_float("QUOTA_WEEKLY_ACT_PCT", 93.0)

    if (five is not None and five >= act_pct) or (
        seven is not None and seven >= weekly_pct
    ):
        level = "act"
    elif five is not None and five >= warn_pct:
        level = "warn"
    else:
        return

    # The window is chosen BEFORE the marker, because the marker is keyed by
    # it. A `quota-act` marker that did not say which window fired meant the
    # 5-hour alert silenced the weekly one for the rest of the session — and
    # the weekly limit, with its reset time, is the one that decides between
    # waiting an hour and stopping for the week.
    window = "weekly limit" if level == "act" and (
        seven is not None and seven >= weekly_pct
    ) else "5-hour window"
    window_key = "weekly" if window == "weekly limit" else "5h"

    marker = marker_path(session_id, f"quota-{level}-{window_key}")
    if os.path.exists(marker):
        return

    used = seven if window == "weekly limit" else five
    resets = (state.get("seven_day") if window == "weekly limit"
              else state.get("five_hour")) or {}
    resets_at = resets.get("resets_at")
    when = ""
    if isinstance(resets_at, (int, float)):
        when = f" It resets at {time.strftime('%H:%M on %d %b', time.localtime(resets_at))}."

    if level == "warn":
        msg = (
            f"The {window} is at ~{used:.0f}%. Do not start long work without a "
            "save point: if the next step is substantial, invoke the handoff "
            f"skill first and keep working from a written state.{when}"
        )
    elif attended():
        msg = (
            f"The {window} is at ~{used:.0f}% and the quota is about to run out. "
            "Finish ONLY the action already in progress, then STOP: invoke the "
            "handoff skill in quota mode, which writes the document plus a "
            "paste-ready PROMPT.txt for a fresh session or another account."
            f"{when} Do not begin new work."
        )
    else:
        msg = (
            f"The {window} is at ~{used:.0f}%. This run is unattended, so it will "
            "not be stopped: invoke the handoff skill in quota mode now so the "
            f"state survives if the quota runs out mid-task.{when}"
        )

    try:
        open(marker, "w").close()
    except OSError:
        # A marker that cannot be written costs a repeated nudge; a traceback
        # on every prompt costs the session.
        pass
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": msg}
    }))


if __name__ == "__main__":
    main()
