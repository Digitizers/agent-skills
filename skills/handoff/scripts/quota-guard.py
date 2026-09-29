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
from handoff_common import (  # noqa: E402
    attended,
    env_float,
    marker_path,
    quota_state_path,
)


# A reset timestamp outside this range cannot be real: 2000-01-01 to
# 2100-01-01. Values beyond it are corruption, not information.
EPOCH_FLOOR = 946684800
EPOCH_CEILING = 4102444800


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

    # This session's own snapshot — never another session's. The same
    # function the bridge writes through, so the two cannot drift. "" means
    # the payload identifies no session and there is no override: nothing to
    # read, and silence is this guard's default for everything unknown.
    path = quota_state_path(inp.get("session_id"))
    if not path:
        return
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

    used = seven if window == "weekly limit" else five
    resets = (state.get("seven_day") if window == "weekly limit"
              else state.get("five_hour")) or {}
    resets_at = resets.get("resets_at")

    # Codex r5 on #45: the marker needs the RESET CYCLE in it, or a session
    # resumed after the window reset is suppressed forever — the bridge
    # supplies fresh high usage with a NEW resets_at and the guard returns at
    # the marker from the cycle before. Keying by the cycle keeps the
    # once-per-cycle property and lets the next cycle warn once of its own.
    # A state file with no usable resets_at falls into one shared bucket,
    # which is the old behaviour for exactly that case.
    if isinstance(resets_at, (int, float)) and resets_at == resets_at:
        try:
            cycle_key = f"c{int(resets_at)}"
        except (OverflowError, ValueError):
            cycle_key = "cnone"
    else:
        cycle_key = "cnone"

    marker = marker_path(session_id, f"quota-{level}-{window_key}-{cycle_key}")
    if os.path.exists(marker):
        return

    # The bridge stores whatever numeric fields the payload carried, without
    # validating them, so `resets_at` may be 1e300 or deeply negative — and
    # time.localtime() raises OverflowError, OSError or ValueError on those,
    # which platform deciding which. That would make the hook exit nonzero on
    # EVERY prompt: exactly the failure the never-raise rule exists to stop.
    # The range check is what makes the outcome the SAME everywhere: macOS
    # renders localtime(-99999999999999) as a date in the year 830 instead of
    # raising, and "It resets at 16:34 on 24 Feb" for a window that resets in
    # an hour is worse than no sentence. A reset time that cannot be true is
    # worth dropping the sentence for, never the warning.
    when = ""
    if (isinstance(resets_at, (int, float))
            and not isinstance(resets_at, bool)
            and resets_at == resets_at                 # not NaN
            and EPOCH_FLOOR <= resets_at <= EPOCH_CEILING):
        try:
            when = (" It resets at "
                    + time.strftime('%H:%M on %d %b', time.localtime(resets_at))
                    + ".")
        except (OverflowError, OSError, ValueError):
            # Belt and braces: the range check above already rejects the
            # values that raise, but which ones raise is platform-specific
            # and this hook runs on every prompt.
            when = ""

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
