# handoff — reference

## Auto-triggering the handoff at a context-usage threshold

Skills are loaded by the model when their description matches the task — a
skill cannot observe the context window on its own, and Claude Code has no
built-in "context reached N%" event. The `PreCompact` hook is the closest
native signal, but it fires only when compaction actually starts, which is
later than you want a handoff written.

The supported way to get "run handoff at 70%" is a **hook** that measures
usage from the session transcript and injects a reminder the model then acts
on:

1. Hook script ([scripts/context-guard.sh](scripts/context-guard.sh)) runs on
   `UserPromptSubmit` (and optionally `PostToolUse` for long autonomous
   turns).
2. It reads the hook input JSON (`transcript_path`, `session_id`), scans the
   transcript JSONL for the **latest assistant message's `usage` block**, and
   sums `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
   — that sum is the current prompt size.
3. At `>= HANDOFF_THRESHOLD_PCT` (default 70) of `CONTEXT_WINDOW_TOKENS`
   (default 200000) it emits `additionalContext` instructing the agent to
   invoke the handoff skill, and drops a per-session marker file so it fires
   **once per session**.

The chain is: hook (deterministic measurement) → injected instruction →
model invokes the skill. The final hop is still model-performed, but an
explicit injected instruction naming the skill is a reliable trigger.

### Installation

Add to `~/.claude/settings.json` (user-wide) or `.claude/settings.json`
(per-project):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/agent-skills/skills/handoff/scripts/context-guard.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/agent-skills/skills/handoff/scripts/context-guard.sh"
          }
        ]
      }
    ]
  }
}
```

`UserPromptSubmit` alone is the low-noise option (checks once per user
message). Add the `PostToolUse` entry as well if sessions run long autonomous
turns where context can cross the threshold between user messages.

Tune via env (e.g. in the same settings file's `env` block):

```json
{ "env": { "HANDOFF_THRESHOLD_PCT": "70", "CONTEXT_WINDOW_TOKENS": "200000" } }
```

### Caveats

- The threshold is measured against a fixed window size; set
  `CONTEXT_WINDOW_TOKENS` to match the model in use (200k default; 1M-context
  models need the larger value or the hook fires far too early).
- Auto-compaction may summarize the conversation before any user prompt if a
  single turn overshoots — the `PostToolUse` variant closes most of that gap.
- The marker file lives in the OS temp dir and is keyed by session id;
  deleting it re-arms the hook for the same session.
