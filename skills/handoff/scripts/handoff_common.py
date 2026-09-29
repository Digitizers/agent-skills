#!/usr/bin/env python3
"""Helpers shared by the handoff guards.

Kept in one module so the two guards cannot drift on what "unattended"
means — a disagreement there shows up as a headless run being told to stop
and ask a human to compact, which is the one thing it cannot do.
"""

import os
import tempfile


def attended() -> bool:
    """True when a human is present to answer.

    `CLAUDE_CODE_SESSION_ATTENDED` is 1 in an interactive session and 0 in a
    `claude -p` run (measured: the headless child overwrites the value it
    inherits, so it describes the session and not the parent). A missing
    variable is treated as attended — an unnecessary nudge is cheap, a
    swallowed handoff is not. `HANDOFF_UNATTENDED=1` forces the other way for
    anything the variable does not cover.
    """
    if os.environ.get("HANDOFF_UNATTENDED") == "1":
        return False
    value = os.environ.get("CLAUDE_CODE_SESSION_ATTENDED")
    if value is None:
        return True
    return value.strip() != "0"


def env_float(name: str, default: float) -> float:
    """Read a numeric setting, falling back to the documented default.

    These values come from a hand-edited `settings.json` and are read on
    EVERY user prompt, so `HANDOFF_THRESHOLD_PCT=seventy` must degrade to the
    default, not raise ValueError and kill the hook. Same policy the guards
    already apply to CONTEXT_WINDOW_BY_MODEL and `updated_at`.

    Unusable means unparseable, non-finite, OR non-positive: see the two
    comments in the body. `env_positive_int` below already applies the same
    rule to the integer settings.
    """
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        value = float(raw.strip())
    except (AttributeError, TypeError, ValueError):
        return float(default)
    # `inf` and `nan` parse fine and then silently DISARM the setting: a
    # threshold of inf never fires, and every comparison against nan is False.
    # That is a worse outcome than the typo it came from, so they fall back
    # to the documented default like any other unusable value.
    if value != value or value in (float("inf"), float("-inf")):
        return float(default)
    # Same class of hole at the other end. Every caller is a PERCENTAGE
    # threshold or an AGE IN SECONDS, and none of them has a meaningful
    # non-positive value: `QUOTA_STALE_SECONDS=0` makes every state instantly
    # stale and silently switches the quota alerts off, and a threshold of 0
    # or less fires on every single prompt. Both are the setting disarmed or
    # jammed on, which is exactly what this function exists to prevent, so
    # they fall back to the documented default too.
    if value <= 0:
        return float(default)
    return value


def env_positive_int(name: str):
    """A positive integer setting, or None when unset, unparseable or <= 0.

    None means "the operator did not state this", which is exactly what the
    callers need: a `CONTEXT_WINDOW_TOKENS=0` is not a 0-token window (that
    divides by zero two hundred lines later), it is an unusable value that
    must fall back to the documented default and NOT count as configured.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value > 0 else None


def sanitize_id(session_id) -> str:
    """A session id reduced to characters that are safe in a filename.

    Returns "" when there is no usable id at all — the callers treat that as
    "this payload does not identify a session", which is a different thing
    from an id made of odd characters.
    """
    if not isinstance(session_id, str) or not session_id:
        return ""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)


def marker_path(session_id: str, name: str) -> str:
    """One marker per session per purpose: a fired warning must never
    disarm a later, louder one."""
    safe = sanitize_id(session_id)
    return os.path.join(tempfile.gettempdir(), f"handoff-{name}-{safe}")


def quota_state_path(session_id) -> str:
    """Where THIS session's quota snapshot lives, or "" for nowhere.

    Codex r7 on #45: every session used to share one file, so concurrent
    sessions — a subscription one beside an API-key or another account's —
    overwrote and deleted each other's snapshots. A high-usage account could
    stop an unrelated low-usage session, and since the round-1 fix an
    API-key payload with no limits DELETED the state a subscription session
    had just written. The file is per session now.

    HANDOFF_QUOTA_STATE still overrides with one explicit path, exactly as
    before: the tests and the install docs use it, and an operator who names
    a path means that path.

    With no override and no session id the answer is "" — nowhere. The
    caller skips rather than falling back to a shared file, because the
    shared file IS the defect; a payload that does not say which session it
    belongs to cannot be recorded without reintroducing it.
    """
    override = os.environ.get("HANDOFF_QUOTA_STATE")
    if override:
        return override
    safe = sanitize_id(session_id)
    if not safe:
        return ""
    return os.path.join(
        os.path.expanduser("~"), ".claude", "handoff", f"quota-{safe}.json"
    )
