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


def marker_path(session_id: str, name: str) -> str:
    """One marker per session per purpose: a fired warning must never
    disarm a later, louder one."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return os.path.join(tempfile.gettempdir(), f"handoff-{name}-{safe}")
