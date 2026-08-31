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
   sums `input_tokens + cache_read_input_tokens + cache_creation_input_tokens
   + output_tokens`. Because that block only counts context sent into the
   *previous* model call, it also adds a conservative floor estimate for
   the transcript tail recorded after it and for the current hook payload
   (prompt / tool_response): **UTF-8 bytes ÷ 2**. A floor, not an average —
   ASCII counts 2 chars/token, Hebrew 1:1, CJK ~0.67, emoji ~2 per code
   point — so English prose counts ~2x high and the nudge can only fire
   early, never late. Any residual undercount (tokenizer byte-fallback on
   pathological input) self-heals: the next hook event reads a usage block
   that already prices this content exactly.
3. A `system` / `compact_boundary` record resets the running total: an
   auto-compact leaves the pre-compact conversation in the transcript file
   but not in the window, and pricing it after a restart is what made the
   hook report ">100%" on the first prompt of a resumed session (#35).
4. The window size is evidence-led. `CONTEXT_WINDOW_TOKENS` is treated as a
   floor: the largest context any single call in the transcript actually
   carried proves the window is at least that big, so a 1M-context session
   left on the 200k default is measured against 1M instead of being read as
   300% full. Evidence is scoped to **this session and this model**: a
   transcript outlives the settings it was written under (a resume can change
   the model, or the same model's window mode — a 1M and a 200k session log
   the same model id), and evidence that outlives its window is the same lie
   with the sign flipped, the guard going quiet at the real ceiling. With no
   evidence from this session, the configured window stands. The reported percentage is also capped at 100 — the byte-floor
   estimate deliberately over-counts, and an impossible figure is a lie even
   when the nudge itself is warranted.
5. At `>= HANDOFF_THRESHOLD_PCT` (default 70) of `CONTEXT_WINDOW_TOKENS`
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

- `CONTEXT_WINDOW_TOKENS` is a floor, not a fact — the hook widens it to the
  smallest known tier (200k / 500k / 1M) that fits the largest context the
  transcript proves was sent. Setting it correctly is still worth doing: the
  evidence only arrives once a call has actually carried that much context,
  so early in a 1M session the default can still fire early.
- Auto-compaction may summarize the conversation before any user prompt if a
  single turn overshoots — the `PostToolUse` variant closes most of that gap.
  After a compaction the count restarts from the boundary, so the hook can
  legitimately fire a second time later in a long session (the marker is per
  session, so in practice it fires once).
- The marker file lives in the OS temp dir and is keyed by session id;
  deleting it re-arms the hook for the same session.
