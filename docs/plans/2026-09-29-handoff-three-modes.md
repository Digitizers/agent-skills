# Handoff three modes — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `skills/handoff` three modes (compaction, another agent, subscription quota) with three guards that trigger them, a mechanical gate before delivery, and offline tests for all of it.

**Architecture:** The existing `context-guard.py` gains a second threshold; a new `quota-guard.py` reads a state file written by a new statusline bridge (hooks cannot see rate limits — measured); a new `handoff-gate.py` refuses to deliver a document that is incomplete or leaks a secret. `SKILL.md` keeps one document contract and adds a mode table; the per-mode blocks live in `references/modes.md`.

**Tech Stack:** Python 3 (stdlib only — these run as hooks on every prompt), bash for the wrappers and test suites. No dependencies: the repo is public and installs by `git clone`.

**Spec:** `docs/specs/2026-09-29-handoff-three-modes-design.md`

## Global Constraints

- Python **stdlib only**, Python 3.8+ compatible. No third-party imports in any script under `skills/handoff/scripts/`.
- Every guard **must exit 0 and print nothing** on any malformed input. These run on every user prompt; a traceback breaks the session.
- Tests are **offline and hermetic**: no `claude` invocation, no network, no token cost. Each suite unsets the env vars it does not set itself.
- Marker files live in `tempfile.gettempdir()` and are keyed by session id, as today.
- Secrets are **never** written to any artifact — values redacted, names kept.
- Durable output location stays `~/.claude/handoffs/<project-slug>/`; never `/tmp`, `$TMPDIR` or the session scratchpad.
- Unattended = `CLAUDE_CODE_SESSION_ATTENDED != "1"` or `HANDOFF_UNATTENDED=1`. Unattended sessions are never told to stop or to ask for compaction.

## Review Focus

Input classes the spec implies that no task's happy path exercises. Each has a test in the task that owns the code.

1. **A quota state file left over from a previous session** — stale data would stop a session whose window has since reset. Owned by Task 4: entries older than `QUOTA_STALE_SECONDS` (default 900) are ignored.
2. **A statusline payload with no `rate_limits` key** — the field is absent for API-key accounts and before the first response. Owned by Task 3: the bridge passes through and writes no state rather than writing an empty object that reads as 0%.
3. **A handoff whose "next steps" are prose, not a numbered list** — the most common way a handoff fails its reader. Owned by Task 5.
4. **A secret that appears only in `PROMPT.txt`, not in `HANDOFF.md`** — the paste-ready prompt is the file most likely to be pasted into another account. Owned by Task 5: the scan covers every file in the handoff directory.
5. **Both context thresholds crossed in the same hook call** (a single huge tool result jumps from 60% to 85%) — the warn marker must not swallow the stop instruction. Owned by Task 2.

---

### Task 1: Shared helpers — attended detection and marker paths

**Files:**
- Create: `skills/handoff/scripts/handoff_common.py`
- Create: `skills/handoff/scripts/test-handoff-common.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `attended() -> bool`, `marker_path(session_id: str, name: str) -> str`. Tasks 2 and 4 import both.

- [ ] **Step 1: Write the failing test**

```bash
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash skills/handoff/scripts/test-handoff-common.sh`
Expected: FAIL — `ModuleNotFoundError: No module named 'handoff_common'`

- [ ] **Step 3: Write the minimal implementation**

```python
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
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `bash skills/handoff/scripts/test-handoff-common.sh`
Expected: five PASS lines then `ALL PASS`

- [ ] **Step 5: Commit**

```bash
git add skills/handoff/scripts/handoff_common.py skills/handoff/scripts/test-handoff-common.sh
git commit -m "feat(handoff): shared attended-session detection and marker paths"
```

---

### Task 2: Second context threshold — stop and ask to compact

**Files:**
- Modify: `skills/handoff/scripts/context-guard.py` (the threshold read near the top of `main()`, and the marker/message block at the end)
- Modify: `skills/handoff/scripts/test-context-guard.sh` (append cases)

**Interfaces:**
- Consumes: `handoff_common.attended` from Task 1. It does **not** use
  `marker_path`: this script already builds marker names that encode window
  provenance, and replacing that with the simpler helper would drop the
  distinctions four Codex rounds put there. The level is appended to the
  existing name instead.
- Produces: nothing other tasks import. The hook's observable contract: `hookSpecificOutput.additionalContext` containing either the existing write-the-handoff text or, past `HANDOFF_STOP_PCT`, text containing `STOP` and `/compact`.

- [ ] **Step 1: Write the failing tests** (append to `test-context-guard.sh`, before the final summary line, renumbering from the last existing case)

```bash
# 20. Past HANDOFF_STOP_PCT -> the stop instruction, not the write-and-continue one.
mk_transcript "$WORK/stop.jsonl" 170000
printf '{"transcript_path":"%s","session_id":"cg-test-stop","hook_event_name":"UserPromptSubmit"}' "$WORK/stop.jsonl" > "$WORK/in-stop.json"
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 run_guard "$WORK/in-stop.json")"
echo "$OUT" | grep -q "STOP" || fail "no stop instruction past the stop threshold"
echo "$OUT" | grep -q "/compact" || fail "stop instruction does not name /compact"
echo "PASS stop threshold fires"

# 21. One call crossing BOTH thresholds still emits the stop instruction
#     (Review Focus 5): a warn marker must not swallow it.
mk_transcript "$WORK/both.jsonl" 170000
printf '{"transcript_path":"%s","session_id":"cg-test-both","hook_event_name":"UserPromptSubmit"}' "$WORK/both.jsonl" > "$WORK/in-both.json"
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 run_guard "$WORK/in-both.json")"
echo "$OUT" | grep -q "STOP" || fail "jumping both thresholds at once lost the stop"
echo "PASS both thresholds in one call"

# 22. The warn marker does not disarm the stop: same session, warn first.
mk_transcript "$WORK/seq.jsonl" 150000
printf '{"transcript_path":"%s","session_id":"cg-test-seq","hook_event_name":"UserPromptSubmit"}' "$WORK/seq.jsonl" > "$WORK/in-seq.json"
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 run_guard "$WORK/in-seq.json")"
echo "$OUT" | grep -q "additionalContext" || fail "warn did not fire"
mk_transcript "$WORK/seq.jsonl" 170000
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 run_guard "$WORK/in-seq.json")"
echo "$OUT" | grep -q "STOP" || fail "warn marker suppressed the later stop"
echo "PASS warn then stop in one session"

# 23. Each threshold still fires only once.
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=1 run_guard "$WORK/in-seq.json")"
[ -z "$OUT" ] || fail "stop fired twice"
echo "PASS stop fires once"

# 24. Unattended run: warned, never told to stop or to compact.
mk_transcript "$WORK/head.jsonl" 170000
printf '{"transcript_path":"%s","session_id":"cg-test-headless","hook_event_name":"UserPromptSubmit"}' "$WORK/head.jsonl" > "$WORK/in-head.json"
OUT="$(CLAUDE_CODE_SESSION_ATTENDED=0 run_guard "$WORK/in-head.json")"
echo "$OUT" | grep -q "additionalContext" || fail "headless run got no handoff nudge at all"
echo "$OUT" | grep -q "STOP" && fail "headless run told to stop"
echo "$OUT" | grep -q "/compact" && fail "headless run asked for compaction"
echo "PASS headless run writes but does not stop"
```

- [ ] **Step 2: Run to verify they fail**

Run: `bash skills/handoff/scripts/test-context-guard.sh`
Expected: FAIL at case 20 — `no stop instruction past the stop threshold`

- [ ] **Step 3: Implement**

In `context-guard.py`, add to the imports:

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handoff_common import attended  # noqa: E402
```

Read the second threshold beside the first:

```python
    threshold = float(os.environ.get("HANDOFF_THRESHOLD_PCT", "70"))
    stop_pct = float(os.environ.get("HANDOFF_STOP_PCT", "80"))
```

Replace the single-level marker/fire block with a two-level one. `level` is
part of every marker name, so the warn firing cannot disarm the stop:

```python
    level = "stop" if pct >= stop_pct else "warn"
    if pct < threshold:
        return
    suffix = f"{provenance}-{window_key}-{level}"
    window_marker = f"{marker}-{suffix}"
    if not assumed and os.path.exists(window_marker):
        return
    assumed_marker = f"{marker}-assumed-{model_key}-{level}"
    if assumed and os.path.exists(assumed_marker):
        return
```

and choose the closing instruction by level and attendance:

```python
    if level == "stop" and attended():
        msg += (
            "This is past the stop threshold. Finish ONLY the action already "
            "in progress, refresh the handoff document with what changed "
            "since it was written, then STOP and ask the user to run "
            "/compact or open a fresh session. Do not begin new work."
        )
    else:
        msg += (
            "Invoke the handoff skill NOW to write a handoff document before "
            "context is compacted, then continue the current task."
        )
```

- [ ] **Step 4: Run the whole suite**

Run: `bash skills/handoff/scripts/test-context-guard.sh`
Expected: every existing PASS line plus the five new ones. The pre-existing cases must not change behaviour — if one fails, the marker suffix broke an older case, not the new feature.

- [ ] **Step 5: Commit**

```bash
git add skills/handoff/scripts/context-guard.py skills/handoff/scripts/test-context-guard.sh
git commit -m "feat(handoff): second context threshold stops and asks for compaction"
```

---

### Task 3: Statusline bridge

**Files:**
- Create: `skills/handoff/scripts/statusline-bridge.sh`
- Create: `skills/handoff/scripts/test-statusline-bridge.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: a state file at `$HANDOFF_QUOTA_STATE` (default `~/.claude/handoff/quota.json`) shaped
  `{"five_hour": {"used_percentage": <float>, "resets_at": <int>}, "seven_day": {...}, "updated_at": <int epoch>}`.
  Task 4 reads exactly this.

- [ ] **Step 1: Write the failing test**

```bash
#!/usr/bin/env bash
# Regression tests for statusline-bridge.sh.
set -euo pipefail
unset HANDOFF_STATUSLINE_INNER
BRIDGE="$(cd "$(dirname "$0")" && pwd)/statusline-bridge.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

PAYLOAD='{"model":{"display_name":"Opus"},"rate_limits":{"five_hour":{"used_percentage":42.5,"resets_at":1790000000},"seven_day":{"used_percentage":10,"resets_at":1790500000}}}'

# 1. Writes the state file
echo "$PAYLOAD" | HANDOFF_QUOTA_STATE="$WORK/quota.json" bash "$BRIDGE" >/dev/null
python3 - "$WORK/quota.json" <<'PY' || fail "state file wrong"
import json, sys, time
d = json.load(open(sys.argv[1]))
assert d["five_hour"]["used_percentage"] == 42.5, d
assert d["seven_day"]["resets_at"] == 1790500000, d
assert abs(d["updated_at"] - time.time()) < 60, d
PY
echo "PASS writes state"

# 2. Passes the payload through to the user's own statusline, unchanged
cat > "$WORK/inner.sh" <<'EOF'
#!/usr/bin/env bash
cat > "$INNER_SEEN"
echo "MY STATUSLINE"
EOF
chmod +x "$WORK/inner.sh"
OUT="$(echo "$PAYLOAD" | INNER_SEEN="$WORK/seen.json" HANDOFF_QUOTA_STATE="$WORK/q2.json" \
  HANDOFF_STATUSLINE_INNER="$WORK/inner.sh" bash "$BRIDGE")"
[ "$OUT" = "MY STATUSLINE" ] || fail "inner statusline output not passed through: $OUT"
diff <(python3 -c "import json,sys;print(json.dumps(json.load(open('$WORK/seen.json')),sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps(json.loads('''$PAYLOAD'''),sort_keys=True))") \
     >/dev/null || fail "inner statusline received a modified payload"
echo "PASS passes through unchanged"

# 3. No rate_limits in the payload (API-key account, or before the first
#    response): write NO state file rather than one that reads as 0%.
echo '{"model":{"display_name":"Opus"}}' | HANDOFF_QUOTA_STATE="$WORK/q3.json" bash "$BRIDGE" >/dev/null
[ ! -f "$WORK/q3.json" ] || fail "wrote state for a payload with no rate limits"
echo "PASS no rate_limits writes nothing"

# 4. Malformed payload: exit 0, no crash, no state.
echo 'not json' | HANDOFF_QUOTA_STATE="$WORK/q4.json" bash "$BRIDGE" >/dev/null || fail "non-zero exit on malformed input"
[ ! -f "$WORK/q4.json" ] || fail "wrote state from malformed input"
echo "PASS malformed payload is silent"

# 5. A failing inner statusline must not take the bridge down with it.
cat > "$WORK/bad.sh" <<'EOF'
#!/usr/bin/env bash
exit 3
EOF
chmod +x "$WORK/bad.sh"
echo "$PAYLOAD" | HANDOFF_QUOTA_STATE="$WORK/q5.json" HANDOFF_STATUSLINE_INNER="$WORK/bad.sh" \
  bash "$BRIDGE" >/dev/null || fail "inner failure propagated"
[ -f "$WORK/q5.json" ] || fail "inner failure lost the state write"
echo "PASS inner failure isolated"
echo "ALL PASS"
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash skills/handoff/scripts/test-statusline-bridge.sh`
Expected: FAIL — the bridge does not exist.

- [ ] **Step 3: Implement**

```bash
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
```

- [ ] **Step 4: Run the tests**

Run: `bash skills/handoff/scripts/test-statusline-bridge.sh`
Expected: five PASS lines then `ALL PASS`

- [ ] **Step 5: Commit**

```bash
git add skills/handoff/scripts/statusline-bridge.sh skills/handoff/scripts/test-statusline-bridge.sh
git commit -m "feat(handoff): statusline bridge records rate limits and passes through"
```

---

### Task 4: Quota guard

**Files:**
- Create: `skills/handoff/scripts/quota-guard.py`
- Create: `skills/handoff/scripts/quota-guard.sh`
- Create: `skills/handoff/scripts/test-quota-guard.sh`

**Interfaces:**
- Consumes: `handoff_common.attended`, `handoff_common.marker_path` (Task 1); the state file written by Task 3.
- Produces: a hook that prints `hookSpecificOutput.additionalContext`, or nothing.

- [ ] **Step 1: Write the failing test**

```bash
#!/usr/bin/env bash
# Regression tests for quota-guard.sh.
set -euo pipefail
unset QUOTA_WARN_PCT QUOTA_ACT_PCT QUOTA_WEEKLY_ACT_PCT QUOTA_STALE_SECONDS HANDOFF_UNATTENDED
GUARD="$(cd "$(dirname "$0")" && pwd)/quota-guard.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; rm -f "${TMPDIR:-/tmp}"/handoff-quota-qg-test-*' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

state() { # $1=file $2=five_hour pct $3=seven_day pct $4=age seconds
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json, sys, time
path, five, seven, age = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
now = int(time.time())
json.dump({"five_hour": {"used_percentage": five, "resets_at": now + 3600},
           "seven_day": {"used_percentage": seven, "resets_at": now + 86400},
           "updated_at": now - age}, open(path, "w"))
PY
}
run() { # $1=session id, $2=state file
  printf '{"session_id":"%s","hook_event_name":"UserPromptSubmit"}' "$1" \
    | HANDOFF_QUOTA_STATE="$2" CLAUDE_CODE_SESSION_ATTENDED=1 bash "$GUARD" \
    || fail "guard exited non-zero"
}

# 1. Below the warn threshold -> silent
state "$WORK/low.json" 20 5 0
[ -z "$(run qg-test-low "$WORK/low.json")" ] || fail "fired below warn"
echo "PASS below warn silent"

# 2. Warn band -> a warning that does NOT stop the session
state "$WORK/warn.json" 72 5 0
OUT="$(run qg-test-warn "$WORK/warn.json")"
echo "$OUT" | grep -q "additionalContext" || fail "no warning in the warn band"
echo "$OUT" | grep -q "STOP" && fail "warn band told the session to stop"
echo "PASS warn band warns only"

# 3. Act band on the 5-hour window -> stop and hand off
state "$WORK/act.json" 85 5 0
OUT="$(run qg-test-act "$WORK/act.json")"
echo "$OUT" | grep -q "STOP" || fail "act band did not stop"
echo "$OUT" | grep -q "quota" || fail "act band did not name the mode"
echo "PASS act band stops"

# 4. The weekly limit has its own, higher bar: 85% weekly is not yet an act.
state "$WORK/week-low.json" 10 85 0
OUT="$(run qg-test-week-low "$WORK/week-low.json")"
echo "$OUT" | grep -q "STOP" && fail "weekly stopped below its threshold"
echo "PASS weekly below threshold"

# 5. ...and 95% weekly is.
state "$WORK/week.json" 10 95 0
OUT="$(run qg-test-week "$WORK/week.json")"
echo "$OUT" | grep -q "STOP" || fail "weekly act did not stop"
echo "PASS weekly act stops"

# 6. Stale state is ignored (Review Focus 1): a file from a previous window
#    must not stop a session whose quota has since reset.
state "$WORK/stale.json" 95 5 3600
[ -z "$(run qg-test-stale "$WORK/stale.json")" ] || fail "acted on stale state"
echo "PASS stale state ignored"

# 7. Missing state file -> silent, exit 0
[ -z "$(run qg-test-missing "$WORK/nope.json")" ] || fail "fired with no state file"
echo "PASS missing state silent"

# 8. Corrupt state file -> silent, exit 0
echo '{"five_hour": ' > "$WORK/bad.json"
[ -z "$(run qg-test-bad "$WORK/bad.json")" ] || fail "fired on corrupt state"
echo "PASS corrupt state silent"

# 9. Once per session per band: the same act does not fire twice.
state "$WORK/once.json" 85 5 0
run qg-test-once "$WORK/once.json" >/dev/null
[ -z "$(run qg-test-once "$WORK/once.json")" ] || fail "act fired twice"
echo "PASS act fires once"

# 10. Unattended run is never told to stop.
state "$WORK/head.json" 85 5 0
OUT="$(printf '{"session_id":"qg-test-head","hook_event_name":"UserPromptSubmit"}' \
  | HANDOFF_QUOTA_STATE="$WORK/head.json" CLAUDE_CODE_SESSION_ATTENDED=0 bash "$GUARD")"
echo "$OUT" | grep -q "STOP" && fail "headless run told to stop"
echo "PASS headless run does not stop"
echo "ALL PASS"
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash skills/handoff/scripts/test-quota-guard.sh`
Expected: FAIL — the guard does not exist.

- [ ] **Step 3: Implement**

`quota-guard.sh` (same wrapper shape as `context-guard.sh`, for the same reason — one path for hook configs, no heredoc tricks under bash 3.2):

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/quota-guard.py"
```

`quota-guard.py`:

```python
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
from handoff_common import attended, marker_path  # noqa: E402


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
    stale_after = float(os.environ.get("QUOTA_STALE_SECONDS", "900"))
    try:
        age = time.time() - float(state.get("updated_at", 0))
    except (TypeError, ValueError):
        return
    if age > stale_after:
        return

    five = pct(state, "five_hour")
    seven = pct(state, "seven_day")
    warn_pct = float(os.environ.get("QUOTA_WARN_PCT", "70"))
    act_pct = float(os.environ.get("QUOTA_ACT_PCT", "80"))
    weekly_pct = float(os.environ.get("QUOTA_WEEKLY_ACT_PCT", "93"))

    if (five is not None and five >= act_pct) or (
        seven is not None and seven >= weekly_pct
    ):
        level = "act"
    elif five is not None and five >= warn_pct:
        level = "warn"
    else:
        return

    marker = marker_path(session_id, f"quota-{level}")
    if os.path.exists(marker):
        return

    window = "weekly limit" if level == "act" and (
        seven is not None and seven >= weekly_pct
    ) else "5-hour window"
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

    open(marker, "w").close()
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": msg}
    }))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `bash skills/handoff/scripts/test-quota-guard.sh`
Expected: ten PASS lines then `ALL PASS`

- [ ] **Step 5: Commit**

```bash
git add skills/handoff/scripts/quota-guard.py skills/handoff/scripts/quota-guard.sh skills/handoff/scripts/test-quota-guard.sh
git commit -m "feat(handoff): quota guard warns, then stops before the window runs out"
```

---

### Task 5: The delivery gate

**Files:**
- Create: `skills/handoff/scripts/handoff-gate.py`
- Create: `skills/handoff/scripts/test-handoff-gate.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: CLI `python3 handoff-gate.py <path> --mode <compaction|same-workspace|cross-workspace|quota>`; prints `GATE: PASS` and exits 0, or prints one `GATE: FAIL — <reason>` line per problem and exits 1. `<path>` is a `HANDOFF.md` file or a handoff directory; a directory scans every file in it.

- [ ] **Step 1: Write the failing test**

```bash
#!/usr/bin/env bash
# Regression tests for handoff-gate.py.
set -euo pipefail
GATE="$(cd "$(dirname "$0")" && pwd)/handoff-gate.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

mkgood() { # $1=dir
  mkdir -p "$1"
  cat > "$1/HANDOFF.md" <<EOF
# Handoff — demo (2026-09-29)

## Project overview
A demo project.

### Tools
- pytest — \`pytest -q\`

## Details
Conventions live in $WORK/conventions.md

## Suggested skills
- pr-first-workflow — before landing anything

## Current state
Tests pass.

## Tried and rejected
- Patching the caller: the bug is in the callee.

## Open issues
None.

## What to do next
1. Run the suite.
2. Open the PR.
EOF
  echo "conventions" > "$WORK/conventions.md"
}

# 1. A complete document passes
mkgood "$WORK/good"
OUT="$(python3 "$GATE" "$WORK/good" --mode compaction)" || fail "valid handoff rejected: $OUT"
echo "$OUT" | grep -q "GATE: PASS" || fail "no PASS line"
echo "PASS valid handoff"

# 2. An empty section fails
mkgood "$WORK/empty"
python3 - "$WORK/empty/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace("Tests pass.", "")
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/empty" --mode compaction && fail "empty section passed"
echo "PASS empty section fails"

# 3. Prose next steps fail (Review Focus 3)
mkgood "$WORK/prose"
python3 - "$WORK/prose/HANDOFF.md" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read().replace("1. Run the suite.\n2. Open the PR.", "Run the suite and open the PR.")
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/prose" --mode compaction && fail "unnumbered next steps passed"
echo "PASS prose next steps fail"

# 4. A path named in the document that does not exist fails
mkgood "$WORK/badpath"
python3 - "$WORK/badpath/HANDOFF.md" "$WORK" <<'PY'
import sys
p, work = sys.argv[1], sys.argv[2]
s = open(p).read().replace(f"{work}/conventions.md", f"{work}/does-not-exist.md")
open(p, "w").write(s)
PY
python3 "$GATE" "$WORK/badpath" --mode compaction && fail "nonexistent path passed"
echo "PASS missing path fails"

# 5. A secret fails — and is caught in PROMPT.txt, not just HANDOFF.md
#    (Review Focus 4). The token below is a syntactically valid fake.
mkgood "$WORK/secret"
printf 'Continue the work. Use sk-ant-api03-%s\n' "$(python3 -c 'print("A"*95)')" > "$WORK/secret/PROMPT.txt"
OUT="$(python3 "$GATE" "$WORK/secret" --mode quota || true)"
echo "$OUT" | grep -q "PROMPT.txt" || fail "secret in PROMPT.txt not reported: $OUT"
python3 "$GATE" "$WORK/secret" --mode quota && fail "secret passed the gate"
echo "PASS secret in PROMPT.txt fails"

# 6. Mode-specific required block: quota mode needs PROMPT.txt
mkgood "$WORK/noprompt"
python3 "$GATE" "$WORK/noprompt" --mode quota && fail "quota mode passed without PROMPT.txt"
echo "PASS quota mode requires PROMPT.txt"

# 7. A zip is scanned by name list: an .env inside it fails the gate.
mkgood "$WORK/zip"
mkdir -p "$WORK/zipsrc" && echo "KEY=value" > "$WORK/zipsrc/.env"
(cd "$WORK/zipsrc" && zip -q "$WORK/zip/workspace.zip" .env)
python3 "$GATE" "$WORK/zip" --mode compaction && fail "zip containing .env passed"
echo "PASS zip with .env fails"

# 8. Cross-workspace mode needs a setup block
mkgood "$WORK/nosetup"
echo "paste me" > "$WORK/nosetup/PROMPT.txt"
python3 "$GATE" "$WORK/nosetup" --mode cross-workspace && fail "cross-workspace passed without setup"
echo "PASS cross-workspace requires setup"
echo "ALL PASS"
```

The zip is checked by its **entry names only** — never extracted. A handoff
gate that unpacks an archive to inspect it is a gate that can be made to write
anywhere on the disk.

- [ ] **Step 2: Run to verify it fails**

Run: `bash skills/handoff/scripts/test-handoff-gate.sh`
Expected: FAIL — the gate does not exist.

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
"""handoff-gate — refuse to deliver a handoff that would fail its reader.

The skill's rules ("no empty sections", "redact secrets") were instructions to
a model. This is the mechanical check: run it before telling the user the
handoff is ready, and fix whatever it names. A handoff that does not print
GATE: PASS is not delivered.
"""

import argparse
import os
import re
import sys

REQUIRED_SECTIONS = (
    "Project overview",
    "Details",
    "Suggested skills",
    "Current state",
    "Open issues",
    "What to do next",
)

# Per-mode extra requirements: (section heading or filename, human reason).
MODE_SECTIONS = {
    "compaction": (("Tried and rejected", "what compaction destroys first"),),
    "same-workspace": (("Tried and rejected", "what compaction destroys first"),),
    "cross-workspace": (
        ("Tried and rejected", "what compaction destroys first"),
        ("Setup", "the reader is on another machine"),
    ),
    "quota": (("Tried and rejected", "what compaction destroys first"),),
}
MODE_FILES = {
    "cross-workspace": ("PROMPT.txt",),
    "quota": ("PROMPT.txt",),
}

SECRET_PATTERNS = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub fine-grained token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b[a-z+]+://[^/\s:@]+:[^/\s:@]+@"), "connection string with a password"),
    (re.compile(r"(?i)\b(password|secret|token|api[_-]?key)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{12,}"),
     "credential assignment"),
)

# Paths the document mentions. Quoted in backticks or bare, absolute or ~-rooted.
PATH_RX = re.compile(r"`([^`\n]+)`|(?<![\w`])((?:~|/)[\w./~-]{3,})")


def sections(text: str) -> dict:
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("#"):
            current = line.lstrip("#").strip()
            # "Handoff — project (date)" and "Tools" are headings too; keep
            # them all, the required list decides which ones matter.
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def check_paths(text: str, base: str) -> list:
    problems = []
    for quoted, bare in PATH_RX.findall(text):
        candidate = (quoted or bare).strip()
        if not candidate.startswith(("/", "~")):
            continue
        # A command, not a path: `git -C /repo status`.
        if " " in candidate:
            continue
        resolved = os.path.expanduser(candidate)
        if not os.path.exists(resolved):
            problems.append(f"path does not exist: {candidate}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--mode", required=True,
                    choices=sorted(MODE_SECTIONS))
    args = ap.parse_args()

    if os.path.isdir(args.path):
        directory = args.path
        doc = os.path.join(directory, "HANDOFF.md")
    else:
        directory = os.path.dirname(args.path) or "."
        doc = args.path

    problems = []
    if not os.path.exists(doc):
        print(f"GATE: FAIL — no handoff document at {doc}")
        return 1
    text = open(doc, "r", encoding="utf-8", errors="replace").read()
    found = sections(text)

    for name in REQUIRED_SECTIONS:
        match = next((v for k, v in found.items() if k.lower() == name.lower()), None)
        if match is None:
            problems.append(f"missing section: {name}")
        elif not match:
            problems.append(f"empty section: {name}")

    for name, why in MODE_SECTIONS[args.mode]:
        if not any(k.lower() == name.lower() and v for k, v in found.items()):
            problems.append(f"missing section for {args.mode} mode: {name} ({why})")

    for filename in MODE_FILES.get(args.mode, ()):
        if not os.path.exists(os.path.join(directory, filename)):
            problems.append(f"missing file for {args.mode} mode: {filename}")

    steps = next((v for k, v in found.items()
                  if k.lower() == "what to do next"), "")
    if steps and not re.search(r"^\s*\d+[.)]\s+\S", steps, re.M):
        problems.append("What to do next is not a numbered list")

    problems.extend(check_paths(text, directory))

    # The zip is checked by entry NAME, never extracted: a gate that unpacks
    # an archive is a gate that can be made to write outside its directory.
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not name.endswith(".zip"):
            continue
        try:
            import zipfile
            with zipfile.ZipFile(os.path.join(directory, name)) as zf:
                entries = zf.namelist()
        except (OSError, zipfile.BadZipFile):
            problems.append(f"unreadable archive: {name}")
            continue
        for entry in entries:
            base = os.path.basename(entry.rstrip("/"))
            if base in (".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519") or \
                    base.startswith(".env."):
                problems.append(f"{name} contains {entry} — credentials never travel in the zip")

    # Every file in the handoff directory is scanned, not just the document:
    # PROMPT.txt is the file most likely to be pasted into another account.
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else [os.path.basename(doc)]:
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        try:
            body = open(full, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(body):
                problems.append(f"{label} found in {name} — redact the value, keep the name")

    if problems:
        for problem in problems:
            print(f"GATE: FAIL — {problem}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `bash skills/handoff/scripts/test-handoff-gate.sh`
Expected: eight PASS lines then `ALL PASS`

- [ ] **Step 5: Commit**

```bash
git add skills/handoff/scripts/handoff-gate.py skills/handoff/scripts/test-handoff-gate.sh
git commit -m "feat(handoff): mechanical gate refuses an incomplete or leaking handoff"
```

---

### Task 6: The skill — modes, blocks, delivery

**Files:**
- Modify: `skills/handoff/SKILL.md`
- Create: `skills/handoff/references/modes.md`

**Interfaces:**
- Consumes: the gate CLI from Task 5 (exact invocation must match: `python3 <skill>/scripts/handoff-gate.py <dir> --mode <mode>`).
- Produces: the mode names other files cite — `compaction`, `same-workspace`, `cross-workspace`, `quota`.

- [ ] **Step 1: Add the mode table to `SKILL.md`**

After the "Output contract" section, insert:

```markdown
## Modes

The four-part contract above is the whole document in every mode. A mode
**adds** a block; it never replaces the core. Pick the mode from what the
reader will not know:

| Mode | The reader | Adds |
|---|---|---|
| `compaction` | this same agent, minutes from now, memory erased, workspace intact | what is mid-action; the user's constraints and decisions verbatim; **Tried and rejected**; a verification command (branch, sha, `git status`) |
| `same-workspace` | another agent on this machine | the above, plus what is uncommitted and the user's unwritten conventions |
| `cross-workspace` | another agent on another machine | the above, plus a **Setup** section: repository, branch, exact sha, environment variable names, required MCP servers and skills, and one command that must pass first |
| `quota` | a fresh session, possibly another account | the above, plus `PROMPT.txt` and when the window resets |

When the user says "hand this to another agent" without saying where, ask
which of the two workspaces it is — the answer changes half the document.

Details and the `PROMPT.txt` template: [references/modes.md](references/modes.md).
```

- [ ] **Step 2: Add the gate to the Delivery section of `SKILL.md`**

```markdown
- **Gate before delivery.** Run
  `python3 <skill>/scripts/handoff-gate.py <handoff-dir> --mode <mode>` and do
  not tell the user the handoff is ready until it prints `GATE: PASS`. It
  fails on empty sections, unnumbered next steps, a path that does not exist,
  a block missing for the mode, and secrets — in every file of the handoff,
  not only the document. A failure is a list of things to fix, not an opinion.
```

- [ ] **Step 3: Write `references/modes.md`**

```markdown
# handoff — modes

The document contract in `SKILL.md` is the whole document. This file is what
each mode adds, and why.

## Tried and rejected (every mode)

A list of approaches already attempted and the evidence that killed each one.

This is the first thing compaction destroys and the reason a fresh agent
walks into the same wall an hour later. One line per attempt: what was tried,
what happened, and the artifact that proves it (a failing test name, an error
message, a command's output).

Bad: "tried a few caching approaches".
Good: "In-memory cache per worker — rejected: four workers, so a purge on one
leaves three stale; reproduced with `tests/test_purge.py::test_multi_worker`."

## compaction

The reader is this same agent after its memory is gone, on the same machine
with the same tools. Setup instructions would be noise. What it needs:

- **Mid-action state** — the file open, the command half-run, the PR mid-review.
- **Constraints verbatim** — what the user said, in their words. Paraphrase
  loses the constraint that was not obviously a constraint at the time.
- **A verification command** that re-establishes where things stand, e.g.
  `git -C <repo> status --short && git -C <repo> log --oneline -3`.

Delivered as a single file. There is no one to paste a prompt to.

## same-workspace

Another agent, same machine. Add:

- **Uncommitted work** — which files are dirty and why they are not committed.
- **Unwritten conventions** — what the user corrected you on that is not in
  `CLAUDE.md`. This is the knowledge that dies with the session.

Also a single file.

## cross-workspace

Another machine or another checkout. Add a **Setup** section:

- Repository, branch, and the **exact sha** the work is based on.
- Where to put it and any per-machine paths.
- Environment variable **names** (never values) and where they come from.
- Required MCP servers, plugins, and skills.
- One command that must pass before the agent touches anything — the proof
  the setup worked.

Delivered as a directory with `PROMPT.txt`.

## quota

The 5-hour window or the weekly limit is running out. Everything from the
mode that otherwise applies, plus:

- **When the window resets** — from the guard's message. This is what decides
  between waiting and switching accounts.
- **`PROMPT.txt`** — paste-ready, self-contained.

### PROMPT.txt template

```
Continue the work described in HANDOFF.md in this directory.

Read HANDOFF.md first. Do not start working: confirm the state matches what
it describes by running the verification command in it, tell me what you
found, and wait.

Repository: <repo> @ <branch> (<sha>)
Handoff: <absolute path to HANDOFF.md>
Stopped because: the <5-hour window|weekly limit> reached <N>%; it resets at <time>.
Next step when I say go: <step 1 from What to do next>
```

Nothing in `PROMPT.txt` may contain a credential value. The gate enforces it.

## The zip

Only when the user asks. It carries work that is not in git and not
reproducible — scratch files, uncommitted edits in a temporary workspace,
generated artifacts that cost real time. Never `.env`, credentials,
`node_modules`, or anything the repository already holds.
```

- [ ] **Step 4: Verify the skill still passes the repository's own validator**

Run: `python3 tools/portability/validate.py --repo . --visibility public`
Expected: `portability OK: 10 skills (public)`

- [ ] **Step 5: Commit**

```bash
git add skills/handoff/SKILL.md skills/handoff/references/modes.md
git commit -m "feat(handoff): four modes, per-mode blocks, and the delivery gate"
```

---

### Task 7: Installation docs and evals

**Files:**
- Modify: `skills/handoff/REFERENCE.md`
- Modify: `skills/handoff/evals/evals.json`
- Modify: `skills/handoff/evals/triggers.json`

**Interfaces:**
- Consumes: the env var names from Tasks 2 and 4, the script paths from Tasks 3–5, the mode names from Task 6.
- Produces: nothing.

- [ ] **Step 1: Add the second threshold, the bridge and the PreCompact net to `REFERENCE.md`**

Add a section after the existing installation block:

````markdown
### The second threshold

`HANDOFF_STOP_PCT` (default 80) is the point where the agent stops instead of
writing and continuing. Both thresholds are served by the same hook; each
keeps its own marker, so the 70% nudge never disarms the 80% stop.

```json
{ "env": { "HANDOFF_THRESHOLD_PCT": "70", "HANDOFF_STOP_PCT": "80" } }
```

Unattended sessions (`claude -p`, scheduled runs) are warned but never
stopped: there is nobody to run `/compact`, and stopping only kills the task.
Detection is `CLAUDE_CODE_SESSION_ATTENDED` (1 interactive, 0 headless);
`HANDOFF_UNATTENDED=1` forces it.

### Quota detection — the statusline bridge

Hook payloads carry **no** rate-limit data. Measured on Claude Code 2.1.x:
`UserPromptSubmit` delivers `cwd, hook_event_name, permission_mode, prompt,
prompt_id, scratchpad_dir, session_id, transcript_path`, and `Stop` adds
`background_tasks, effort, last_assistant_message, session_crons,
stop_hook_active`. Neither carries `rate_limits`. The statusline command does.

So quota detection is two pieces: a bridge that records what the statusline
receives, and a hook that reads it.

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /path/to/agent-skills/skills/handoff/scripts/statusline-bridge.sh"
  },
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
        "command": "bash /path/to/agent-skills/skills/handoff/scripts/quota-guard.sh" } ] }
    ]
  },
  "env": {
    "HANDOFF_STATUSLINE_INNER": "<your previous statusline command>",
    "QUOTA_WARN_PCT": "70",
    "QUOTA_ACT_PCT": "80",
    "QUOTA_WEEKLY_ACT_PCT": "93"
  }
}
```

The bridge passes the payload through to `HANDOFF_STATUSLINE_INNER` unchanged,
so the statusline looks exactly as it did. Removing the two entries restores
the previous setup; nothing else is touched.

**Ask before installing it.** It edits the user's `settings.json`. Print the
JSON, explain what the bridge does, and let them decide. Without it, quota
mode still works when the user asks for it by name.

`rate_limits` is present only for subscription accounts, and only after the
first response of a session. An API-key account never populates it, and the
guard stays silent rather than reading its absence as 0%.

### The PreCompact net

`PreCompact` fires when compaction starts — too late to be the main trigger,
which is why the thresholds above exist, but exactly right as a backstop for
the case where the 80% stop was ignored:

```json
{
  "hooks": {
    "PreCompact": [
      { "hooks": [ { "type": "command",
        "command": "bash /path/to/agent-skills/skills/handoff/scripts/context-guard.sh" } ] }
    ]
  }
}
```
````

- [ ] **Step 2: Add one eval per mode to `evals/evals.json`**

```json
{
  "id": 4,
  "name": "compaction-mode-tried-and-rejected",
  "prompt": "We're at 75% context. Write the handoff before we get compacted — we've already ruled out two approaches today and I don't want to redo them.",
  "expected_output": "Writes the handoff in compaction mode: the four core parts plus a 'Tried and rejected' section naming each rejected approach with the evidence that killed it, what is mid-action, and a verification command. Runs handoff-gate.py and reports GATE: PASS before calling it ready.",
  "files": [],
  "expectations": [
    "The document contains a 'Tried and rejected' section with at least one attempt and the evidence against it",
    "The document contains a verification command that re-establishes state (git status / branch / sha)",
    "The reply shows the gate was run with --mode compaction and reported GATE: PASS",
    "No setup or clone instructions are included — the reader is on the same machine"
  ]
},
{
  "id": 5,
  "name": "cross-workspace-setup",
  "prompt": "Hand this off to an agent on my other laptop.",
  "expected_output": "Recognises this is cross-workspace, writes a directory containing HANDOFF.md and PROMPT.txt, and includes a Setup section with repository, branch, exact sha, environment variable names (never values), required MCP servers and skills, and one command that must pass before work starts.",
  "files": [],
  "expectations": [
    "A Setup section names the repository, the branch and an exact commit sha",
    "Environment variables appear by name only, with no values",
    "A PROMPT.txt is written alongside HANDOFF.md",
    "The gate is run with --mode cross-workspace and reports GATE: PASS"
  ]
},
{
  "id": 6,
  "name": "quota-mode-paste-ready",
  "prompt": "המכסה שלי כמעט נגמרת, תכין לי handoff שאני יכול להמשיך ממנו בחשבון אחר",
  "expected_output": "Writes the handoff in quota mode, in Hebrew (the chat's language), as a directory with HANDOFF.md and a paste-ready PROMPT.txt; states when the window resets if known; runs the gate with --mode quota and reports GATE: PASS.",
  "files": [],
  "expectations": [
    "The document is written in Hebrew, with code identifiers, commands and paths left as-is",
    "A PROMPT.txt exists and is self-contained enough to paste into a fresh session",
    "No credential values appear in any file",
    "The gate is run with --mode quota and reports GATE: PASS"
  ]
}
```

- [ ] **Step 3: Add trigger phrases to `evals/triggers.json`**

```json
{ "query": "המכסה שלי כמעט נגמרת, תכין handoff להמשך בחשבון אחר", "should_trigger": true },
{ "query": "hand this off to an agent on my other laptop", "should_trigger": true },
{ "query": "we're about to get compacted, save the state first", "should_trigger": true },
{ "query": "how much of my 5-hour window is left?", "should_trigger": false }
```

- [ ] **Step 4: Validate the JSON and the repository contract**

Run:
```bash
python3 -c "import json; json.load(open('skills/handoff/evals/evals.json')); json.load(open('skills/handoff/evals/triggers.json')); print('json ok')"
python3 tools/portability/validate.py --repo . --visibility public
```
Expected: `json ok` and `portability OK: 10 skills (public)`

- [ ] **Step 5: Run every suite once, together**

Run:
```bash
for t in skills/handoff/scripts/test-*.sh; do echo "== $t"; bash "$t" || exit 1; done
```
Expected: `ALL PASS` from each suite.

- [ ] **Step 6: Commit**

```bash
git add skills/handoff/REFERENCE.md skills/handoff/evals
git commit -m "docs(handoff): install the second threshold, the bridge and the PreCompact net"
```
