#!/usr/bin/env python3
"""idle-handoff — write a handoff while the session is idle, before the
prompt cache expires (#54).

A hook cannot run on idle: every hook event needs activity. What can run on
idle is a job scheduled INSIDE the session (Claude Code's CronCreate): it
fires only while the REPL is idle, and its turn reads the conversation from
the prompt cache like any other turn. Measured on 2026-10-27: a cron-fired
turn read 168,319 tokens from cache and wrote 39, while a
`claude --resume <id> --fork-session -p` child of the same session read
26,691 and rewrote 147,154 — the fork misses the cache, so it is not used.

So this script has two halves:

* Hook mode (no arguments; stdin is the hook payload).
  - `SessionStart` with source startup/resume: a new process holds no cron
    jobs, so the state is marked unarmed.
  - `UserPromptSubmit` from the user: records the prompt time. When the
    session holds no job, it tells the agent to create one (CronCreate,
    recurring, twice an hour) and to record its id. Prompts the job itself
    enqueues start with CHECK_TAG and are ignored, so they never count as
    user activity.

* Command mode, run by the agent from the job's prompt:
  - `check <session>`  prints one verdict line: WAIT, WRITE or STOP.
  - `armed <session> <job-id>`  records the job.
  - `done <session>`  records that the handoff was written; the job is then
    deleted by the agent and re-created on the next user prompt.

Two checks an hour keep the cache warm by themselves (each is a turn that
reads it), so a handoff written between IDLE_HANDOFF_MINUTES and
IDLE_HANDOFF_MINUTES + 30 minutes of idle is always a cache read.

Settings (environment, all optional):
  IDLE_HANDOFF=0                 switch the whole thing off
  IDLE_HANDOFF_MINUTES=45        user idle time before a handoff is written
  IDLE_HANDOFF_MIN_TOKENS=100000 smaller contexts are not worth a handoff
  IDLE_HANDOFF_STATE_DIR         where state lives (default ~/.claude/handoff)

Silence is the default for every failure in hook mode: this runs on every
prompt and may not be the thing that breaks the session.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handoff_common import (  # noqa: E402
    attended,
    env_float,
    env_positive_int,
    sanitize_id,
)

CHECK_TAG = "[idle-handoff-check]"
CRON = "13,43 * * * *"
SCRIPT = os.path.abspath(__file__)


def state_path(session_id) -> str:
    safe = sanitize_id(session_id)
    if not safe:
        return ""
    base = os.environ.get("IDLE_HANDOFF_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "handoff"
    )
    return os.path.join(base, f"idle-{safe}.json")


def load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def context_tokens(transcript: str) -> int:
    """Tokens the latest assistant call carried, or 0 when unknown."""
    latest = 0
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("subtype") == "compact_boundary":
                    latest = 0
                    continue
                msg = rec.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if rec.get("type") == "assistant" and isinstance(usage, dict):
                    try:
                        latest = (
                            int(usage.get("input_tokens", 0))
                            + int(usage.get("cache_read_input_tokens", 0))
                            + int(usage.get("cache_creation_input_tokens", 0))
                        )
                    except (TypeError, ValueError):
                        pass
    except OSError:
        return 0
    return latest


def arm_instructions(session_id: str) -> str:
    prompt = (
        f"{CHECK_TAG} Run: python3 {SCRIPT} check {session_id} "
        "and follow its one-line verdict exactly. Do nothing else in this turn."
    )
    return (
        "Idle handoff is not armed in this session. Before your reply, call "
        "CronCreate once with "
        f"cron \"{CRON}\", recurring true, and this exact prompt: "
        f"{json.dumps(prompt, ensure_ascii=False)} . Then run "
        f"`python3 {SCRIPT} armed {session_id} <job id>` with the id it "
        "returns. Do not mention this to the user unless it fails; then "
        "carry on with the user's request."
    )


def hook() -> None:
    if os.environ.get("IDLE_HANDOFF") == "0" or not attended():
        return
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(inp, dict):
        return
    session_id = inp.get("session_id")
    path = state_path(session_id)
    if not path:
        return
    event = inp.get("hook_event_name") or ""
    state = load(path)
    try:
        if event == "SessionStart":
            if inp.get("source") in ("startup", "resume", "clear"):
                state.pop("job", None)
                save(path, state)
            return
        if event != "UserPromptSubmit":
            return
        prompt = inp.get("prompt")
        if isinstance(prompt, str) and prompt.lstrip().startswith(CHECK_TAG):
            return
        state["last_prompt_at"] = time.time()
        if inp.get("transcript_path"):
            state["transcript"] = inp["transcript_path"]
        save(path, state)
    except OSError:
        return
    if state.get("job"):
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": arm_instructions(session_id),
        }
    }))


def verdict(session_id: str) -> str:
    path = state_path(session_id)
    state = load(path) if path else {}
    job = state.get("job") or "<the idle-handoff job id>"

    def stop() -> str:
        # The job goes, so the next user prompt must re-arm: forget it here
        # rather than trusting the agent to report the deletion.
        if path and "job" in state:
            state.pop("job", None)
            try:
                save(path, state)
            except OSError:
                pass
        return (
            f"STOP: call CronDelete with id {job}, then end the turn without "
            "any other output."
        )

    if os.environ.get("IDLE_HANDOFF") == "0":
        return stop()
    last = state.get("last_prompt_at")
    if not isinstance(last, (int, float)):
        return "WAIT: end the turn now without any output."
    if isinstance(state.get("handed_off_at"), (int, float)) and (
        state["handed_off_at"] >= last
    ):
        return stop()
    idle_min = (time.time() - last) / 60.0
    if idle_min < env_float("IDLE_HANDOFF_MINUTES", 45.0):
        return "WAIT: end the turn now without any output."
    floor = env_positive_int("IDLE_HANDOFF_MIN_TOKENS") or 100_000
    if context_tokens(state.get("transcript") or "") < floor:
        # Too small to be worth a handoff, and nothing will change until
        # the user is back. Stop the checks so they do not keep the cache
        # warm all night; the next prompt re-arms.
        return stop()
    return (
        f"WRITE: the user has been idle {int(idle_min)} minutes. Invoke the "
        "handoff skill in compaction mode now: write the handoff to its "
        "durable path and run its gate until GATE: PASS. Do not message "
        "other sessions or change anything else. Then run "
        f"`python3 {SCRIPT} done {session_id}`, call CronDelete with id "
        f"{job}, and end the turn with one line giving the handoff path."
    )


def command(argv) -> int:
    if len(argv) < 2 or argv[0] not in ("check", "armed", "done"):
        print(f"usage: {os.path.basename(SCRIPT)} check|armed|done <session> [job]",
              file=sys.stderr)
        return 2
    action, session_id = argv[0], argv[1]
    path = state_path(session_id)
    if not path:
        print("bad session id", file=sys.stderr)
        return 2
    if action == "check":
        print(verdict(session_id))
        return 0
    state = load(path)
    if action == "armed":
        if len(argv) < 3 or not argv[2].strip():
            print("armed needs the job id", file=sys.stderr)
            return 2
        state["job"] = argv[2].strip()
        state.pop("handed_off_at", None)
    else:
        state["handed_off_at"] = time.time()
        state.pop("job", None)
    save(path, state)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(command(sys.argv[1:]))
    hook()
